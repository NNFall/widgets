from __future__ import annotations

import hashlib
import ipaddress
import secrets
from datetime import UTC, datetime, timedelta
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

from aiohttp import web
from aiohttp_session import STORAGE_KEY, get_session, new_session
from sqlalchemy import select

from app.auth.oauth import (
    GoogleOAuthProvider,
    OAuthCallback,
    OAuthProvider,
    OAuthTransaction,
    YandexOAuthProvider,
)
from app.auth.service import link_identity_and_claim_draft
from app.auth.session_storage import DatabaseSessionStorage
from app.auth.tokens import issue_token, token_digest
from app.config import AppConfig
from app.db.session import session_scope
from app.saas.models import AnonymousDraft, OAuthState

OAUTH_PROVIDERS_KEY = "oauth_providers"


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
    return providers


async def create_draft(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception as error:  # noqa: BLE001
        raise web.HTTPBadRequest(text=_json_error("invalid JSON"), content_type="application/json") from error
    source_url = str(payload.get("url", "")).strip()
    brief = str(payload.get("brief", "")).strip() or None
    if not _is_public_http_url(source_url):
        return web.json_response({"error": "url must be a public http or https address"}, status=400)
    raw_claim, claim_digest = issue_token()
    draft = AnonymousDraft(
        source_url=source_url,
        brief=brief,
        claim_token_digest=claim_digest,
        expires_at=datetime.now(UTC) + timedelta(hours=2),
    )
    async with session_scope(request.app) as database:
        database.add(draft)
        await database.flush()
        draft_id = str(draft.id)
    session = await get_session(request)
    session["pending_draft_id"] = draft_id
    session["pending_draft_claim"] = raw_claim
    return web.json_response({"id": draft_id, "url": source_url, "brief": brief}, status=201)


async def auth_start(request: web.Request) -> web.StreamResponse:
    provider_name = request.match_info["provider"]
    provider = request.app[OAUTH_PROVIDERS_KEY].get(provider_name)
    if provider is None:
        raise web.HTTPNotFound(text=_json_error("oauth provider is not configured"), content_type="application/json")
    session = await get_session(request)
    requested_draft = request.query.get("draft_id")
    pending_draft = session.get("pending_draft_id")
    draft_id = requested_draft if requested_draft == pending_draft else None

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
        database.add(
            OAuthState(
                state_digest=state_digest,
                provider=provider_name,
                pkce_verifier=verifier,
                nonce=nonce,
                return_path="/studio",
                draft_id=UUID(draft_id) if draft_id else None,
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        )
    raise web.HTTPFound(provider.authorization_url(transaction))


async def auth_callback(request: web.Request) -> web.StreamResponse:
    provider_name = request.match_info["provider"]
    provider = request.app[OAUTH_PROVIDERS_KEY].get(provider_name)
    if provider is None:
        raise web.HTTPNotFound(text=_json_error("oauth provider is not configured"), content_type="application/json")
    raw_state = request.query.get("state", "")
    code = request.query.get("code", "")
    async with session_scope(request.app) as database:
        state = (
            await database.execute(
                select(OAuthState).where(
                    OAuthState.state_digest == token_digest(raw_state),
                    OAuthState.provider == provider_name,
                    OAuthState.consumed_at.is_(None),
                    OAuthState.expires_at > datetime.now(UTC),
                )
            )
        ).scalar_one_or_none()
        if state is None:
            raise web.HTTPBadRequest(text=_json_error("oauth state is invalid or expired"), content_type="application/json")
        transaction = OAuthTransaction(
            state=raw_state,
            code_verifier=state.pkce_verifier,
            code_challenge=_pkce_challenge(state.pkce_verifier),
            nonce=state.nonce or "",
            redirect_uri=f"{request.app['public_base_url']}/api/auth/{provider_name}/callback",
        )
        identity = await provider.exchange(
            transaction, OAuthCallback(code=code, state=raw_state)
        )
        browser_session = await get_session(request)
        draft_id = state.draft_id
        raw_claim = browser_session.get("pending_draft_claim")
        if str(draft_id) != browser_session.get("pending_draft_id"):
            draft_id = None
            raw_claim = None
        user, project = await link_identity_and_claim_draft(
            database,
            identity,
            draft_id=draft_id,
            claim_token_digest=token_digest(raw_claim) if isinstance(raw_claim, str) else None,
        )
        state.consumed_at = datetime.now(UTC)

    old_identity = browser_session.identity
    storage = request[STORAGE_KEY]
    if old_identity and isinstance(storage, DatabaseSessionStorage):
        await storage.revoke(request, str(old_identity))
    authenticated = await new_session(request)
    authenticated["user_id"] = user.id
    authenticated["tenant_id"] = user.tenant_id
    authenticated["email"] = user.email
    location = f"/studio?project={project.id}" if project else "/studio"
    raise web.HTTPFound(location)


async def auth_session(request: web.Request) -> web.Response:
    session = await get_session(request)
    user_id = session.get("user_id")
    return web.json_response(
        {
            "enabled": bool(request.app.get("public_auth_enabled", False)),
            "authenticated": isinstance(user_id, int),
            "user_id": user_id if isinstance(user_id, int) else None,
            "email": session.get("email") if isinstance(user_id, int) else None,
            "pending_draft_id": session.get("pending_draft_id"),
            "providers": sorted(request.app[OAUTH_PROVIDERS_KEY]),
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
) -> None:
    app["public_base_url"] = public_base_url.rstrip("/")
    app["public_auth_enabled"] = public_auth_enabled
    app.router.add_post("/api/drafts", create_draft)
    app.router.add_get("/api/auth/{provider}/start", auth_start)
    app.router.add_get("/api/auth/{provider}/callback", auth_callback)
    app.router.add_get("/api/auth/session", auth_session)
    app.router.add_post("/api/auth/logout", auth_logout)


def _pkce_challenge(verifier: str) -> str:
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _is_public_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        return False
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _json_error(message: str) -> str:
    import json

    return json.dumps({"error": message})
