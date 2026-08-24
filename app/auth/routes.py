from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import secrets
from asyncio import Lock, Semaphore
from collections import deque
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from time import monotonic
from typing import Mapping
from urllib.parse import quote, urlsplit, urlunsplit
from uuid import UUID

from aiohttp import web
from aiohttp_session import STORAGE_KEY, get_session, new_session
from sqlalchemy import delete, or_, select, update

from app.admin.operator_auth import developer_session_snapshot
from app.analytics.service import (
    FUNNEL_JOURNEY_SESSION_KEY,
    ensure_funnel_journey,
    record_funnel_event,
    sanitize_campaign,
)
from app.auth.oauth import (
    GoogleOAuthProvider,
    OAuthCallback,
    OAuthError,
    OAuthProvider,
    OAuthTransaction,
    UnverifiedIdentity,
    VKOAuthProvider,
    YandexOAuthProvider,
)
from app.auth.service import (
    IdentityLinkConflict,
    draft_project_id,
    link_identity_and_claim_draft,
)
from app.auth.session_storage import DatabaseSessionStorage
from app.auth.tokens import issue_token, token_digest
from app.config import AppConfig
from app.db.session import session_scope
from app.saas.models import AnonymousDraft, AuthSession, OAuthState, Project

OAUTH_PROVIDERS_KEY = "oauth_providers"
ENTRY_RATE_LIMITER_KEY = web.AppKey("entry_rate_limiter", object)
ENTRY_TRUSTED_PROXIES_KEY = web.AppKey("entry_trusted_proxies", tuple)
ENTRY_MAX_BODY_BYTES_KEY = web.AppKey("entry_max_body_bytes", int)
OAUTH_CALLBACK_SEMAPHORE_KEY = web.AppKey("oauth_callback_semaphore", Semaphore)
_MAX_SOURCE_URL_CHARS = 2_048
_MAX_BRIEF_CHARS = 4_000
logger = logging.getLogger(__name__)


def _session_journey_id(session) -> UUID | None:
    candidate = session.get(FUNNEL_JOURNEY_SESSION_KEY)
    try:
        return UUID(candidate) if isinstance(candidate, str) else None
    except ValueError:
        return None


def _funnel_journeys_enabled(request: web.Request) -> bool:
    config = request.app.get("config")
    return True if config is None else bool(
        getattr(config, "funnel_journeys_enabled", True)
    )


def _oauth_failure_location(public_code: str) -> str:
    return f"/studio?auth_error={public_code}"


def _provider_callback_public_code(provider_error: str) -> str:
    if provider_error == "access_denied":
        return "access_denied"
    if provider_error in {"server_error", "temporarily_unavailable"}:
        return "provider_unavailable"
    return "oauth_failed"


def _exchange_public_code(error: OAuthError) -> str:
    return "identity_unverified" if isinstance(error, UnverifiedIdentity) else "oauth_failed"


def _clear_oauth_binding(session) -> None:
    session.pop("oauth_state_id", None)
    session.pop("oauth_session_binding", None)


def _log_oauth_failure(
    *, provider: str, public_code: str, failure: str
) -> None:
    # Provider descriptions and exception messages are intentionally omitted:
    # they can contain authorization codes or provider-side diagnostics.
    logger.warning(
        "oauth_callback_failed provider=%s public_code=%s failure=%s",
        provider,
        public_code,
        failure,
    )


class _EntryRateLimiter:
    def __init__(self, *, requests: int, window_seconds: int) -> None:
        self._limit = requests
        self._window_seconds = window_seconds
        self._attempts: dict[tuple[str, str, str], deque[float]] = {}
        self._lock = Lock()

    async def check(
        self,
        request: web.Request,
        *,
        account_id: int | None,
        scope: str,
    ) -> None:
        now = monotonic()
        threshold = now - self._window_seconds
        remote = _entry_remote_address(request)
        agent = request.headers.get("User-Agent", "")[:512]
        device = (
            hashlib.sha256(f"{remote}|{agent}".encode("utf-8")).hexdigest()
            if remote and agent
            else None
        )
        keys = []
        if remote:
            keys.append((scope, "ip", remote))
        if device:
            keys.append((scope, "device", device))
        if account_id is not None:
            keys.append((scope, "account", str(account_id)))
        async with self._lock:
            for key, attempted in tuple(self._attempts.items()):
                while attempted and attempted[0] <= threshold:
                    attempted.popleft()
                if not attempted:
                    self._attempts.pop(key, None)
            if any(len(self._attempts.get(key, ())) >= self._limit for key in keys):
                raise web.HTTPTooManyRequests(
                    text=_json_error("too many entry requests"),
                    content_type="application/json",
                    headers={"Retry-After": str(self._window_seconds)},
                )
            for key in keys:
                self._attempts.setdefault(key, deque()).append(now)


def build_oauth_providers(config: AppConfig) -> Mapping[str, OAuthProvider]:
    providers: dict[str, OAuthProvider] = {}
    if config.google_oauth_client_id and config.google_oauth_client_secret:
        providers["google"] = GoogleOAuthProvider(
            config.google_oauth_client_id, config.google_oauth_client_secret
        )
    if config.yandex_oauth_client_id and config.yandex_oauth_client_secret:
        providers["yandex"] = YandexOAuthProvider(
            config.yandex_oauth_client_id, config.yandex_oauth_client_secret
        )
    if config.vk_oauth_app_id is not None:
        providers["vk"] = VKOAuthProvider(config.vk_oauth_app_id)
    return providers


async def create_draft(request: web.Request) -> web.Response:
    session = await get_session(request)
    await request.app[ENTRY_RATE_LIMITER_KEY].check(
        request,
        account_id=session.get("user_id")
        if isinstance(session.get("user_id"), int)
        else None,
        scope="draft",
    )
    if (
        request.content_length
        and request.content_length > request.app[ENTRY_MAX_BODY_BYTES_KEY]
    ):
        raise web.HTTPRequestEntityTooLarge(
            max_size=request.app[ENTRY_MAX_BODY_BYTES_KEY],
            actual_size=request.content_length,
        )
    try:
        payload = await request.json()
    except Exception as error:  # noqa: BLE001
        raise web.HTTPBadRequest(
            text=_json_error("invalid JSON"), content_type="application/json"
        ) from error
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(
            text=_json_error("JSON body must be an object"),
            content_type="application/json",
        )
    raw_source_url = str(payload.get("url", "")).strip()
    source_url = _canonical_public_https_url(raw_source_url)
    brief = str(payload.get("brief", "")).strip() or None
    campaign = sanitize_campaign(
        payload.get("campaign") if isinstance(payload.get("campaign"), dict) else None
    )
    if len(raw_source_url) > _MAX_SOURCE_URL_CHARS or (
        brief is not None and len(brief) > _MAX_BRIEF_CHARS
    ):
        return web.json_response({"error": "draft fields are too long"}, status=400)
    if source_url is None:
        return web.json_response(
            {"error": "url must be a canonical public https address"}, status=400
        )
    raw_claim, claim_digest = issue_token()
    draft = AnonymousDraft(
        source_url=source_url,
        brief=brief,
        campaign=campaign,
        claim_token_digest=claim_digest,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    async with session_scope(request.app) as database:
        await _cleanup_expired_auth_records(database, datetime.now(UTC))
        journey_id = None
        if _funnel_journeys_enabled(request):
            journey_result = await ensure_funnel_journey(
                database,
                journey_id=_session_journey_id(session),
                campaign=campaign,
            )
            journey_id = journey_result.journey.id
            draft.journey_id = journey_id
        database.add(draft)
        await database.flush()
        await record_funnel_event(
            database,
            event_type="composer_submitted",
            event_key=f"composer_submitted:draft:{draft.id}",
            journey_id=journey_id,
            anonymous_draft_id=draft.id,
            campaign=campaign,
        )
        draft_id = str(draft.id)
        stored_journey_id = str(journey_id) if journey_id is not None else None
    if stored_journey_id is not None:
        session[FUNNEL_JOURNEY_SESSION_KEY] = stored_journey_id
    session["pending_draft_id"] = draft_id
    session["pending_draft_claim"] = raw_claim
    return web.json_response(
        {"id": draft_id, "url": source_url, "brief": brief}, status=201
    )


async def claim_draft(request: web.Request) -> web.Response:
    session = await get_session(request)
    user_id = session.get("user_id")
    tenant_id = session.get("tenant_id")
    if not isinstance(user_id, int) or not isinstance(tenant_id, int):
        raise web.HTTPUnauthorized(
            text=_json_error("authentication required"),
            content_type="application/json",
        )
    expected_csrf = session.get("csrf_token")
    supplied_csrf = request.headers.get("X-CSRF-Token")
    if (
        not isinstance(expected_csrf, str)
        or not supplied_csrf
        or not secrets.compare_digest(expected_csrf, supplied_csrf)
    ):
        raise web.HTTPForbidden(
            text=_json_error("csrf failed"), content_type="application/json"
        )
    try:
        draft_id = UUID(request.match_info["draft_id"])
    except (TypeError, ValueError) as error:
        raise web.HTTPNotFound(
            text=_json_error("draft not found"), content_type="application/json"
        ) from error
    raw_claim = session.get("pending_draft_claim")
    if (
        session.get("pending_draft_id") != str(draft_id)
        or not isinstance(raw_claim, str)
    ):
        raise web.HTTPNotFound(
            text=_json_error("draft not found"), content_type="application/json"
        )

    project_id = draft_project_id(draft_id)
    created = False
    async with session_scope(request.app) as database:
        draft = await database.scalar(
            select(AnonymousDraft)
            .where(
                AnonymousDraft.id == draft_id,
                AnonymousDraft.expires_at > datetime.now(UTC),
            )
            .with_for_update()
        )
        if draft is None or not secrets.compare_digest(
            draft.claim_token_digest, token_digest(raw_claim)
        ):
            raise web.HTTPNotFound(
                text=_json_error("draft not found"), content_type="application/json"
            )
        project = await database.get(Project, project_id)
        if draft.claimed_at is not None:
            if (
                draft.claimed_by_user_id != user_id
                or project is None
                or project.owner_user_id != user_id
                or project.tenant_id != tenant_id
            ):
                raise web.HTTPConflict(
                    text=_json_error("draft already claimed"),
                    content_type="application/json",
                )
        else:
            if project is not None:
                raise web.HTTPConflict(
                    text=_json_error("draft project conflict"),
                    content_type="application/json",
                )
            project = Project(
                id=project_id,
                tenant_id=tenant_id,
                owner_user_id=user_id,
                journey_id=draft.journey_id,
                source_url=draft.source_url,
                brief=draft.brief,
                status="draft",
            )
            database.add(project)
            draft.claimed_by_user_id = user_id
            draft.claimed_at = datetime.now(UTC)
            await database.flush()
            created = True
        if project.journey_id is None and draft.journey_id is not None:
            project.journey_id = draft.journey_id
        if project.journey_id is not None:
            await record_funnel_event(
                database,
                event_type="authenticated_project",
                event_key=f"authenticated_project:project:{project.id}",
                journey_id=project.journey_id,
                user_id=user_id,
                project_id=project.id,
            )
        payload = {
            "project": {
                "id": str(project.id),
                "source_url": project.source_url,
                "brief": project.brief,
            }
        }
    return web.json_response(payload, status=201 if created else 200)


async def auth_start(request: web.Request) -> web.StreamResponse:
    provider_name = request.match_info["provider"]
    provider = request.app[OAUTH_PROVIDERS_KEY].get(provider_name)
    if provider is None:
        raise web.HTTPNotFound(
            text=_json_error("oauth provider is not configured"),
            content_type="application/json",
        )
    session = await get_session(request)
    await request.app[ENTRY_RATE_LIMITER_KEY].check(
        request,
        account_id=session.get("user_id")
        if isinstance(session.get("user_id"), int)
        else None,
        scope="oauth_start",
    )
    transaction = await _create_oauth_transaction(
        request, provider_name=provider_name, session=session
    )
    raise web.HTTPFound(provider.authorization_url(transaction))


async def vk_auth_bootstrap(request: web.Request) -> web.Response:
    provider = request.app[OAUTH_PROVIDERS_KEY].get("vk")
    app_id = getattr(provider, "app_id", None)
    if provider is None or isinstance(app_id, bool) or not isinstance(app_id, int):
        raise web.HTTPNotFound(
            text=_json_error("oauth provider is not configured"),
            content_type="application/json",
        )
    session = await get_session(request)
    await request.app[ENTRY_RATE_LIMITER_KEY].check(
        request,
        account_id=session.get("user_id")
        if isinstance(session.get("user_id"), int)
        else None,
        scope="oauth_start",
    )
    transaction = await _create_oauth_transaction(
        request, provider_name="vk", session=session
    )
    return web.json_response(
        {
            "app_id": app_id,
            "redirect_uri": transaction.redirect_uri,
            "state": transaction.state,
            "code_verifier": transaction.code_verifier,
        },
        headers={"Cache-Control": "no-store"},
    )


async def _create_oauth_transaction(
    request: web.Request, *, provider_name: str, session
) -> OAuthTransaction:
    requested_draft = request.query.get("draft_id")
    pending_draft = session.get("pending_draft_id")
    draft_id = None
    draft_candidate = None
    if requested_draft and requested_draft == pending_draft:
        try:
            draft_candidate = UUID(requested_draft)
        except (TypeError, ValueError):
            draft_candidate = None
    browser_binding = secrets.token_urlsafe(32)
    session["oauth_session_binding"] = browser_binding

    raw_state, state_digest = issue_token()
    verifier = secrets.token_urlsafe(48)
    challenge = _pkce_challenge(verifier)
    nonce = secrets.token_urlsafe(32)
    public_base_url = request.app["public_base_url"]
    redirect_uri = f"{public_base_url}/api/auth/{provider_name}/callback"
    transaction = OAuthTransaction(
        state=raw_state,
        code_verifier=verifier,
        code_challenge=challenge,
        nonce=nonce,
        redirect_uri=redirect_uri,
    )
    async with session_scope(request.app) as database:
        await _cleanup_expired_auth_records(database, datetime.now(UTC))
        draft = (
            await database.get(AnonymousDraft, draft_candidate)
            if draft_candidate is not None
            else None
        )
        if requested_draft == pending_draft and draft is None:
            session.pop("pending_draft_id", None)
            session.pop("pending_draft_claim", None)
        draft_id = draft.id if draft is not None else None
        journey_id = None
        if _funnel_journeys_enabled(request):
            journey_result = await ensure_funnel_journey(
                database,
                journey_id=(
                    draft.journey_id
                    if draft is not None
                    else _session_journey_id(session)
                ),
                campaign=draft.campaign if draft is not None else None,
            )
            journey_id = journey_result.journey.id
        oauth_state = OAuthState(
            state_digest=state_digest,
            session_binding_digest=token_digest(browser_binding),
            provider=provider_name,
            pkce_verifier=verifier,
            nonce=nonce,
            return_path="/studio",
            draft_id=draft_id,
            journey_id=journey_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        database.add(oauth_state)
        await database.flush()
        session["oauth_state_id"] = str(oauth_state.id)
        await record_funnel_event(
            database,
            event_type="auth_started",
            event_key=f"auth_started:oauth_state:{oauth_state.id}",
            journey_id=oauth_state.journey_id,
            anonymous_draft_id=oauth_state.draft_id,
            oauth_state_id=oauth_state.id,
            campaign=draft.campaign if draft is not None else None,
        )
    return transaction


async def auth_callback(request: web.Request) -> web.StreamResponse:
    provider_name = request.match_info["provider"]
    provider = request.app[OAUTH_PROVIDERS_KEY].get(provider_name)
    if provider is None:
        raise web.HTTPNotFound(
            text=_json_error("oauth provider is not configured"),
            content_type="application/json",
        )
    raw_state = request.query.get("state", "")
    code = request.query.get("code", "")
    device_id = request.query.get("device_id")
    browser_session = await get_session(request)
    await request.app[ENTRY_RATE_LIMITER_KEY].check(
        request,
        account_id=browser_session.get("user_id")
        if isinstance(browser_session.get("user_id"), int)
        else None,
        scope="oauth_callback",
    )
    browser_binding = browser_session.get("oauth_session_binding")
    browser_state_id = browser_session.get("oauth_state_id")
    binding_digest = (
        token_digest(browser_binding) if isinstance(browser_binding, str) else ""
    )
    now = datetime.now(UTC)
    valid_state_filter = (
        OAuthState.state_digest == token_digest(raw_state),
        OAuthState.provider == provider_name,
        OAuthState.consumed_at.is_(None),
        OAuthState.expires_at > now,
    )
    try:
        bound_state_id = UUID(browser_state_id) if isinstance(browser_state_id, str) else None
    except ValueError:
        bound_state_id = None
    async with session_scope(request.app) as database:
        claimed = None
        if bound_state_id is not None and binding_digest:
            claimed = (
                await database.execute(
                    update(OAuthState)
                    .where(
                        *valid_state_filter,
                        OAuthState.id == bound_state_id,
                        OAuthState.session_binding_digest == binding_digest,
                    )
                    .values(consumed_at=now)
                    .returning(
                        OAuthState.id,
                        OAuthState.pkce_verifier,
                        OAuthState.nonce,
                        OAuthState.return_path,
                        OAuthState.draft_id,
                        OAuthState.journey_id,
                    )
                )
            ).mappings().one_or_none()
        if claimed is None:
            belongs_to_other_browser = await database.scalar(
                select(OAuthState.id).where(*valid_state_filter)
            )
            message = (
                "oauth state does not belong to this browser session"
                if belongs_to_other_browser is not None
                else "oauth state is invalid or expired"
            )
            raise web.HTTPBadRequest(
                text=_json_error(message),
                content_type="application/json",
            )

    _clear_oauth_binding(browser_session)
    provider_callback_error = request.query.get("error", "")
    if provider_callback_error:
        public_code = _provider_callback_public_code(provider_callback_error)
        _log_oauth_failure(
            provider=provider_name,
            public_code=public_code,
            failure="provider_callback",
        )
        raise web.HTTPFound(_oauth_failure_location(public_code))

    transaction = OAuthTransaction(
        state=raw_state,
        code_verifier=claimed["pkce_verifier"],
        code_challenge=_pkce_challenge(claimed["pkce_verifier"]),
        nonce=claimed["nonce"] or "",
        redirect_uri=f"{request.app['public_base_url']}/api/auth/{provider_name}/callback",
    )
    try:
        async with request.app[OAUTH_CALLBACK_SEMAPHORE_KEY]:
            identity = await provider.exchange(
                transaction,
                OAuthCallback(code=code, state=raw_state, device_id=device_id),
            )
    except OAuthError as error:
        public_code = _exchange_public_code(error)
        _log_oauth_failure(
            provider=provider_name,
            public_code=public_code,
            failure=type(error).__name__,
        )
        raise web.HTTPFound(_oauth_failure_location(public_code)) from None
    except web.HTTPException:
        raise
    except Exception as error:
        # Network, provider JSON, and verifier failures must not become a raw
        # HTTP 500 or leak provider diagnostics to the browser. Cancellation
        # and process-level errors inherit BaseException and are not swallowed.
        public_code = "provider_unavailable"
        _log_oauth_failure(
            provider=provider_name,
            public_code=public_code,
            failure=type(error).__name__,
        )
        raise web.HTTPFound(_oauth_failure_location(public_code)) from None

    async with session_scope(request.app) as database:
        claimed_state_id = claimed["id"]
        draft_id = claimed["draft_id"]
        draft = await database.get(AnonymousDraft, draft_id) if draft_id else None
        raw_claim = browser_session.get("pending_draft_claim")
        if str(draft_id) != browser_session.get("pending_draft_id"):
            draft_id = None
            raw_claim = None
        try:
            user, project = await link_identity_and_claim_draft(
                database,
                identity,
                draft_id=draft_id,
                claim_token_digest=token_digest(raw_claim)
                if isinstance(raw_claim, str)
                else None,
            )
        except IdentityLinkConflict:
            _log_oauth_failure(
                provider=provider_name,
                public_code="account_link_required",
                failure="IdentityLinkConflict",
            )
            raise web.HTTPFound(
                _oauth_failure_location("account_link_required")
            ) from None
        await record_funnel_event(
            database,
            event_type="auth_completed",
            event_key=f"auth_completed:oauth_state:{claimed_state_id}",
            journey_id=claimed["journey_id"],
            anonymous_draft_id=claimed["draft_id"],
            oauth_state_id=claimed_state_id,
            user_id=user.id,
            project_id=project.id if project is not None else None,
            campaign=draft.campaign if draft is not None else None,
        )
        if project is not None and project.journey_id is not None:
            await record_funnel_event(
                database,
                event_type="authenticated_project",
                event_key=f"authenticated_project:project:{project.id}",
                journey_id=project.journey_id,
                user_id=user.id,
                project_id=project.id,
            )

    old_identity = browser_session.identity
    storage = request[STORAGE_KEY]
    if old_identity and isinstance(storage, DatabaseSessionStorage):
        await _clear_stored_oauth_binding(request, str(old_identity))
        await storage.revoke(request, str(old_identity))
    authenticated = await new_session(request)
    authenticated["user_id"] = user.id
    authenticated["tenant_id"] = user.tenant_id
    authenticated["email"] = user.email
    authenticated["csrf_token"] = secrets.token_urlsafe(32)
    if claimed["journey_id"] is not None:
        authenticated[FUNNEL_JOURNEY_SESSION_KEY] = str(claimed["journey_id"])
    location = (
        f"/studio?project={project.id}&autostart=1"
        if project
        else "/studio"
    )
    raise web.HTTPFound(location)


async def auth_session(request: web.Request) -> web.Response:
    session = await get_session(request)
    user_id = session.get("user_id")
    csrf_token = session.get("csrf_token")
    if isinstance(user_id, int) and not isinstance(csrf_token, str):
        csrf_token = secrets.token_urlsafe(32)
        session["csrf_token"] = csrf_token
    return web.json_response(
        {
            "enabled": bool(request.app.get("public_auth_enabled", False)),
            "authenticated": isinstance(user_id, int),
            "user_id": user_id if isinstance(user_id, int) else None,
            "email": session.get("email") if isinstance(user_id, int) else None,
            "csrf_token": csrf_token if isinstance(user_id, int) else None,
            "pending_draft_id": session.get("pending_draft_id"),
            "providers": sorted(request.app[OAUTH_PROVIDERS_KEY]),
            "developer": await developer_session_snapshot(request),
        }
    )


async def auth_logout(request: web.Request) -> web.Response:
    session = await get_session(request)
    session.invalidate()
    return web.json_response({"authenticated": False})


def setup_auth_routes(
    app: web.Application,
    *,
    public_base_url: str,
    public_auth_enabled: bool = True,
    entry_rate_limit_requests: int = 20,
    entry_rate_limit_window_seconds: int = 60,
    entry_max_body_bytes: int = 16_384,
    trusted_proxy_cidrs: tuple[str, ...] = (),
    oauth_callback_concurrency: int | None = None,
) -> None:
    app["public_base_url"] = public_base_url.rstrip("/")
    app["public_auth_enabled"] = public_auth_enabled
    app[ENTRY_RATE_LIMITER_KEY] = _EntryRateLimiter(
        requests=entry_rate_limit_requests,
        window_seconds=entry_rate_limit_window_seconds,
    )
    app[ENTRY_MAX_BODY_BYTES_KEY] = entry_max_body_bytes
    config = app.get("config")
    configured_entry_proxies = getattr(
        config, "entry_trusted_proxy_cidrs", trusted_proxy_cidrs
    )
    app[ENTRY_TRUSTED_PROXIES_KEY] = tuple(
        ip_network(value, strict=False) for value in configured_entry_proxies
    )
    configured_callback_concurrency = oauth_callback_concurrency
    if configured_callback_concurrency is None:
        configured_callback_concurrency = int(
            getattr(config, "oauth_callback_concurrency", 4)
        )
    app[OAUTH_CALLBACK_SEMAPHORE_KEY] = Semaphore(configured_callback_concurrency)
    app.router.add_post("/api/drafts", create_draft)
    app.router.add_post("/api/drafts/{draft_id}/claim", claim_draft)
    app.router.add_post("/api/auth/vk/bootstrap", vk_auth_bootstrap)
    app.router.add_get("/api/auth/{provider}/start", auth_start)
    app.router.add_get("/api/auth/{provider}/callback", auth_callback)
    app.router.add_get("/api/auth/session", auth_session)
    app.router.add_post("/api/auth/logout", auth_logout)


def _pkce_challenge(verifier: str) -> str:
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def _cleanup_expired_auth_records(database, now: datetime) -> None:
    await database.execute(delete(OAuthState).where(OAuthState.expires_at <= now))
    await database.execute(
        delete(AnonymousDraft).where(AnonymousDraft.expires_at <= now)
    )
    await database.execute(
        delete(AuthSession).where(
            or_(
                AuthSession.expires_at <= now,
                AuthSession.revoked_at <= now - timedelta(days=1),
            )
        )
    )


async def _clear_stored_oauth_binding(request: web.Request, raw_session: str) -> None:
    async with session_scope(request.app) as database:
        record = await database.scalar(
            select(AuthSession).where(
                AuthSession.token_digest == token_digest(raw_session)
            )
        )
        if record is None:
            return
        payload = dict(record.payload)
        session_payload = payload.get("session")
        if not isinstance(session_payload, dict):
            return
        sanitized = dict(session_payload)
        sanitized.pop("oauth_state_id", None)
        sanitized.pop("oauth_session_binding", None)
        payload["session"] = sanitized
        record.payload = payload


def _entry_remote_address(request: web.Request) -> str | None:
    try:
        peer = ip_address(str(request.remote or ""))
    except ValueError:
        return None
    trusted = request.app[ENTRY_TRUSTED_PROXIES_KEY]
    if not any(peer in network for network in trusted):
        return str(peer)
    forwarded = request.headers.get("X-Forwarded-For", "")
    try:
        chain = [
            ip_address(part.strip()) for part in forwarded.split(",") if part.strip()
        ]
    except ValueError:
        return str(peer)
    for candidate in reversed(chain):
        if not any(candidate in network for network in trusted):
            return str(candidate)
    return str(peer)


_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PRIVATE_DNS_SUFFIXES = (".internal", ".localhost", ".local", ".lan", ".home")


def _canonical_public_https_url(value: str) -> str | None:
    raw = value.strip()
    if "?" in raw or "#" in raw:
        return None
    try:
        parsed = urlsplit(raw)
        port = parsed.port
        hostname = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return None
    if (
        "." not in hostname
        or hostname == "localhost"
        or hostname.endswith(_PRIVATE_DNS_SUFFIXES)
        or any(not _DNS_LABEL.fullmatch(label) for label in hostname.split("."))
    ):
        return None
    path = quote(parsed.path or "/", safe="/%:@-._~!$&'()*+,;=")
    return urlunsplit(("https", hostname, path, "", ""))


def _is_public_http_url(value: str) -> bool:
    return _canonical_public_https_url(value) is not None


def _json_error(message: str) -> str:
    import json

    return json.dumps({"error": message})
