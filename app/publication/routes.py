from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import secrets
from collections import deque
from ipaddress import ip_address, ip_network
from time import monotonic, time as unix_time
from urllib.parse import urlsplit
from uuid import UUID

from aiohttp import web
from sqlalchemy import select
from app.chat import CHAT_SERVICE_KEY, ChatContext, ChatServiceError
from app.db.session import get_session_factory
from app.projects.routes import _chat_reference_context, _require_csrf, _scope, _uuid
from app.publication.service import (
    InvalidAllowedDomain,
    InvalidPublicationArtifact,
    PublicationConflict,
    PublicationIdentityUnverified,
    PublicationNotFound,
    PublicationService,
    PublicationUpgradeRequired,
    ReleaseCorrupt,
)
from app.widgets.loader import render_loader, render_runtime
from app.saas.models import GenerationArtifact, GenerationRun, Project
from builder_lab.persona import assistant_persona_from_artifact_config


PUBLICATION_CHAT_SIGNING_KEY = web.AppKey("publication_chat_signing_key", bytes)
PUBLICATION_CHAT_RATE_LIMITER_KEY = web.AppKey(
    "publication_chat_rate_limiter", object
)
PUBLICATION_CHAT_TRUSTED_PROXIES_KEY = web.AppKey(
    "publication_chat_trusted_proxies", tuple
)
_CAPABILITY_TTL_SECONDS = 300
_MAX_PUBLIC_CHAT_BODY_BYTES = 8_192
_MAX_PUBLIC_CHAT_MESSAGE_CHARS = 1_000
_MAX_PUBLIC_CHAT_ORIGIN_CHARS = 512
_MAX_PUBLIC_CHAT_CAPABILITY_CHARS = 2_048
_MAX_PUBLICATION_BODY_BYTES = 32_768
_MAX_PUBLICATION_ORIGINS = 32
_PUBLIC_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,95}$")
_PUBLIC_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class _PublicChatRateLimiter:
    def __init__(self, *, publication_limit: int, ip_limit: int, window_seconds: int) -> None:
        self._publication_limit = publication_limit
        self._ip_limit = ip_limit
        self._window_seconds = window_seconds
        self._attempts: dict[tuple[str, str], deque[float]] = {}
        self._seen: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def check(
        self,
        *,
        publication_id: str,
        remote_address: str | None,
        request_identity: str,
        message: str,
    ) -> None:
        now = monotonic()
        threshold = now - self._window_seconds
        message_digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
        async with self._lock:
            for key, attempted in tuple(self._attempts.items()):
                while attempted and attempted[0] <= threshold:
                    attempted.popleft()
                if not attempted:
                    self._attempts.pop(key, None)
            for identity, (_digest, recorded_at) in tuple(self._seen.items()):
                if recorded_at <= threshold:
                    self._seen.pop(identity, None)
            seen = self._seen.get(request_identity)
            if seen is not None and seen[0] == message_digest:
                return

            publication_key = ("publication", publication_id)
            ip_key = ("ip", remote_address) if remote_address is not None else None
            if len(self._attempts.get(publication_key, ())) >= self._publication_limit:
                raise ChatServiceError(
                    "chat_publication_rate_limited",
                    "В этом виджете временно слишком много сообщений",
                    status=429,
                    retryable=True,
                )
            if ip_key is not None and len(self._attempts.get(ip_key, ())) >= self._ip_limit:
                raise ChatServiceError(
                    "chat_ip_rate_limited",
                    "С этого адреса временно слишком много сообщений",
                    status=429,
                    retryable=True,
                )
            self._attempts.setdefault(publication_key, deque()).append(now)
            if ip_key is not None:
                self._attempts.setdefault(ip_key, deque()).append(now)
            self._seen[request_identity] = (message_digest, now)


def _error(code: str, *, message: str | None = None) -> dict:
    payload = {"code": code}
    if message:
        payload["message"] = message
    return {"error": payload}


def _chat_error(
    code: str,
    message: str,
    *,
    status: int,
    request_id: str | None = None,
    retryable: bool = False,
) -> web.Response:
    return web.json_response(
        {
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "request_id": request_id,
            }
        },
        status=status,
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _chat_signing_key(app: web.Application) -> bytes:
    return app[PUBLICATION_CHAT_SIGNING_KEY]


def _chat_capability_ttl(app: web.Application) -> int:
    config = app.get("config")
    return int(getattr(config, "publication_chat_capability_ttl_seconds", _CAPABILITY_TTL_SECONDS))


def _visitor_remote_address(request: web.Request) -> str | None:
    try:
        peer = ip_address(str(request.remote or ""))
    except ValueError:
        return None
    trusted_proxies = request.app[PUBLICATION_CHAT_TRUSTED_PROXIES_KEY]
    peer_is_trusted = any(peer in network for network in trusted_proxies)
    if not peer_is_trusted:
        config = request.app.get("config")
        environment = str(getattr(config, "environment", "development")).strip().lower()
        if environment == "production" and not trusted_proxies and (
            peer.is_loopback or peer.is_private
        ):
            return None
        return str(peer)

    forwarded = request.headers.get("X-Forwarded-For", "")
    if not forwarded:
        return None
    try:
        chain = [ip_address(part.strip()) for part in forwarded.split(",") if part.strip()]
    except ValueError:
        return None
    for candidate in reversed(chain):
        if not any(candidate in network for network in trusted_proxies):
            return str(candidate)
    return None


def _mint_chat_capability(
    request: web.Request,
    published,
    host_origin: str,
) -> str:
    payload = {
        "exp": int(unix_time()) + _chat_capability_ttl(request.app),
        "key": published.stable_key,
        "origin": host_origin,
        "release_id": str(published.release_id),
        "revision": published.revision,
    }
    encoded = _base64url(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = _base64url(
        hmac.new(_chat_signing_key(request.app), encoded.encode("ascii"), hashlib.sha256).digest()
    )
    return f"{encoded}.{signature}"


def _verify_chat_capability(
    request: web.Request,
    token: str,
    *,
    published,
    host_origin: str,
) -> None:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected_signature = _base64url(
            hmac.new(
                _chat_signing_key(request.app),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("invalid signature")
        payload = json.loads(_base64url_decode(encoded))
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        expected = {
            "exp",
            "key",
            "origin",
            "release_id",
            "revision",
        }
        expires_at = payload.get("exp")
        if set(payload) != expected or isinstance(expires_at, bool) or not isinstance(expires_at, int):
            raise ValueError("invalid payload")
        if expires_at <= int(unix_time()):
            raise ValueError("expired")
        if (
            payload["key"] != published.stable_key
            or payload["release_id"] != str(published.release_id)
            or payload["revision"] != published.revision
            or payload["origin"] != host_origin
        ):
            raise ValueError("binding mismatch")
    except (UnicodeError, ValueError, json.JSONDecodeError, TypeError) as error:
        raise ChatServiceError(
            "chat_capability_denied",
            "Сессия опубликованного виджета недействительна",
            status=403,
        ) from error


class _DuplicateJsonKey(ValueError):
    pass


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


async def _body(
    request: web.Request,
    *,
    allowed_keys: frozenset[str],
    required_keys: frozenset[str],
) -> dict:
    try:
        content_length = request.content_length
        if content_length is not None and content_length > _MAX_PUBLICATION_BODY_BYTES:
            raise web.HTTPRequestEntityTooLarge(
                max_size=_MAX_PUBLICATION_BODY_BYTES,
                actual_size=content_length,
                text=json.dumps(_error("request_too_large")),
                content_type="application/json",
            )
        raw = bytearray()
        async for chunk in request.content.iter_chunked(8_192):
            actual_size = len(raw) + len(chunk)
            if actual_size > _MAX_PUBLICATION_BODY_BYTES:
                raise web.HTTPRequestEntityTooLarge(
                    max_size=_MAX_PUBLICATION_BODY_BYTES,
                    actual_size=actual_size,
                    text=json.dumps(_error("request_too_large")),
                    content_type="application/json",
                )
            raw.extend(chunk)
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_from_pairs)
    except web.HTTPRequestEntityTooLarge:
        raise
    except (json.JSONDecodeError, UnicodeError, _DuplicateJsonKey) as error:
        raise web.HTTPBadRequest(
            text=json.dumps(_error("invalid_json")), content_type="application/json"
        ) from error
    if (
        not isinstance(payload, dict)
        or not required_keys <= set(payload)
        or not set(payload) <= allowed_keys
    ):
        raise web.HTTPBadRequest(
            text=json.dumps(_error("invalid_body")), content_type="application/json"
        )
    return payload


def _versions_enabled(request: web.Request) -> bool:
    config = request.app.get("config")
    return bool(config is not None and getattr(config, "project_versions_enabled", False))


def _request_uuid(value: object, *, nullable: bool = False) -> UUID | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("UUID value is required")
    return UUID(value)


def _allowed_domains(payload: dict) -> list[str] | None:
    if "allowed_domains" not in payload:
        return None
    values = payload["allowed_domains"]
    if (
        not isinstance(values, list)
        or len(values) > _MAX_PUBLICATION_ORIGINS
        or any(not isinstance(value, str) for value in values)
    ):
        raise web.HTTPBadRequest(
            text=json.dumps(_error("invalid_body")), content_type="application/json"
        )
    return values


def _service(request: web.Request) -> PublicationService:
    config = request.app.get("config")
    allow_insecure = bool(
        config is not None
        and getattr(config, "publication_allow_insecure_origins", False)
        and str(getattr(config, "environment", "production")).strip().lower()
        != "production"
    )
    return PublicationService(
        get_session_factory(request.app), allow_insecure_origins=allow_insecure
    )


def _public_base_url(request: web.Request) -> str:
    config = request.app.get("config")
    configured = getattr(config, "public_base_url", None) if config is not None else None
    if isinstance(configured, str) and configured.strip():
        return configured.strip().rstrip("/")
    environment = str(
        getattr(config, "environment", "development") if config is not None else "development"
    ).strip().lower()
    public_mode = bool(
        config is not None
        and (getattr(config, "public_auth_enabled", False) or environment == "production")
    )
    if public_mode:
        raise web.HTTPServiceUnavailable(
            text=json.dumps(_error("public_base_url_unavailable")),
            content_type="application/json",
        )
    return ""


def _published_payload(published, *, base: str) -> web.Response:
    return web.json_response(
        {
            "publication_id": str(published.publication_id),
            "release_id": str(published.release_id),
            "artifact_id": str(published.artifact_id),
            "project_version_id": (
                str(published.project_version_id)
                if published.project_version_id is not None
                else None
            ),
            "stable_key": published.stable_key,
            "revision": published.revision,
            "allowed_domains": list(published.allowed_domains),
            "checksum": published.checksum,
            "embed_url": f"{base}/embed/{published.stable_key}.js",
            "runtime_url": f"{base}/runtime/{published.stable_key}",
        },
        status=201 if published.created else 200,
    )


def _publication_state_payload(publication, *, base: str) -> web.Response:
    def release_payload(release) -> dict:
        return {
            "release_id": str(release.release_id),
            "artifact_id": str(release.artifact_id),
            "project_version_id": (
                str(release.project_version_id)
                if release.project_version_id is not None
                else None
            ),
            "previous_release_id": (
                str(release.previous_release_id)
                if release.previous_release_id is not None
                else None
            ),
            "revision": release.revision,
            "checksum": release.checksum,
            "created_at": release.created_at.isoformat(),
        }

    return web.json_response(
        {
            "publication": {
                "publication_id": str(publication.publication_id),
                "stable_key": publication.stable_key,
                "state": publication.state,
                "allowed_domains": list(publication.allowed_domains),
                "embed_url": f"{base}/embed/{publication.stable_key}.js",
                "runtime_url": f"{base}/runtime/{publication.stable_key}",
                "active_release": release_payload(publication.active_release),
                "releases": [
                    release_payload(release) for release in publication.releases
                ],
            }
        }
    )


async def get_project_publication(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request, verified=True)
    project_id = _uuid(request.match_info["project_id"])
    try:
        publication = await _service(request).get_project_state(
            project_id,
            actor_user_id=user_id,
            tenant_id=tenant_id,
        )
    except (PublicationNotFound, ReleaseCorrupt):
        raise web.HTTPNotFound(
            text=json.dumps(_error("not_found")), content_type="application/json"
        )
    if publication is None:
        return web.json_response({"publication": None})
    return _publication_state_payload(publication, base=_public_base_url(request))


async def publish_project(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request, verified=True)
    await _require_csrf(request)
    base = _public_base_url(request)
    project_id = _uuid(request.match_info["project_id"])
    try:
        if _versions_enabled(request):
            payload = await _body(
                request,
                allowed_keys=frozenset(
                    {
                        "project_version_id",
                        "expected_active_release_id",
                        "allowed_domains",
                    }
                ),
                required_keys=frozenset(
                    {
                        "project_version_id",
                        "expected_active_release_id",
                    }
                ),
            )
            published = await _service(request).publish_version(
                project_id,
                actor_user_id=user_id,
                tenant_id=tenant_id,
                project_version_id=_request_uuid(payload["project_version_id"]),
                expected_active_release_id=_request_uuid(
                    payload["expected_active_release_id"], nullable=True
                ),
                allowed_domains=_allowed_domains(payload),
            )
        else:
            payload = await _body(
                request,
                allowed_keys=frozenset({"artifact_id", "revision", "allowed_domains"}),
                required_keys=frozenset(),
            )
            artifact_id = payload.get("artifact_id")
            revision = payload.get("revision")
            if artifact_id is None and revision is None:
                raise web.HTTPBadRequest(
                    text=json.dumps(_error("invalid_body")),
                    content_type="application/json",
                )
            parsed_artifact_id = (
                _request_uuid(artifact_id) if artifact_id is not None else None
            )
            if isinstance(revision, bool) or (
                revision is not None and not isinstance(revision, int)
            ):
                raise ValueError("revision must be an integer")
            published = await _service(request).publish(
                project_id,
                actor_user_id=user_id,
                tenant_id=tenant_id,
                artifact_id=parsed_artifact_id,
                revision=revision,
                allowed_domains=_allowed_domains(payload),
            )
    except web.HTTPException:
        raise
    except PublicationConflict:
        return web.json_response(
            _error(
                "publication_conflict",
                message="Publication changed; reload before retrying",
            ),
            status=409,
        )
    except (TypeError, ValueError, InvalidAllowedDomain, InvalidPublicationArtifact) as error:
        status = 400 if isinstance(error, ValueError) else 422
        code = "invalid_body" if status == 400 else "publication_invalid"
        return web.json_response(_error(code, message=str(error)), status=status)
    except PublicationNotFound:
        raise web.HTTPNotFound(text=json.dumps(_error("not_found")), content_type="application/json")
    except PublicationUpgradeRequired:
        return web.json_response(_error("upgrade_required"), status=402)
    except PublicationIdentityUnverified:
        return web.json_response(_error("verified_oauth_required"), status=403)
    return _published_payload(published, base=base)


async def rollback_publication(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request, verified=True)
    await _require_csrf(request)
    base = _public_base_url(request)
    publication_id = _uuid(request.match_info["publication_id"])
    try:
        if _versions_enabled(request):
            payload = await _body(
                request,
                allowed_keys=frozenset(
                    {"target_release_id", "expected_active_release_id"}
                ),
                required_keys=frozenset(
                    {"target_release_id", "expected_active_release_id"}
                ),
            )
            published = await _service(request).rollback(
                publication_id,
                actor_user_id=user_id,
                tenant_id=tenant_id,
                target_release_id=_request_uuid(payload["target_release_id"]),
                expected_active_release_id=_request_uuid(
                    payload["expected_active_release_id"]
                ),
            )
        else:
            payload = await _body(
                request,
                allowed_keys=frozenset({"target_release_id"}),
                required_keys=frozenset({"target_release_id"}),
            )
            published = await _service(request).rollback(
                publication_id,
                actor_user_id=user_id,
                tenant_id=tenant_id,
                target_release_id=_request_uuid(payload["target_release_id"]),
            )
    except web.HTTPException:
        raise
    except ValueError:
        return web.json_response(_error("invalid_release_id"), status=400)
    except PublicationConflict:
        return web.json_response(
            _error(
                "publication_conflict",
                message="Publication changed; reload before retrying",
            ),
            status=409,
        )
    except (PublicationNotFound, ReleaseCorrupt):
        raise web.HTTPNotFound(text=json.dumps(_error("not_found")), content_type="application/json")
    except PublicationUpgradeRequired:
        return web.json_response(_error("upgrade_required"), status=402)
    except PublicationIdentityUnverified:
        return web.json_response(_error("verified_oauth_required"), status=403)
    return _published_payload(published, base=base)


async def embed_loader(request: web.Request) -> web.Response:
    key = request.match_info["stable_key"]
    try:
        await _service(request).resolve(key)
    except (PublicationNotFound, ReleaseCorrupt):
        raise web.HTTPNotFound(text=json.dumps(_error("not_found")), content_type="application/json")
    return web.Response(
        text=render_loader(),
        content_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


async def runtime(request: web.Request) -> web.Response:
    key = request.match_info["stable_key"]
    try:
        published = await _service(request).resolve(key)
    except (PublicationNotFound, ReleaseCorrupt):
        raise web.HTTPNotFound(text=json.dumps(_error("not_found")), content_type="application/json")
    ancestors = " ".join(published.allowed_domains) if published.allowed_domains else "'none'"
    host_origin = _approved_referrer_origin(request, published)
    chat_capability = (
        _mint_chat_capability(request, published, host_origin) if host_origin is not None else None
    )
    csp = (
        "default-src 'none'; style-src 'unsafe-inline' data:; "
        "script-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; "
        "connect-src 'self'; media-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-src 'none'; navigate-to 'none'; "
        f"frame-ancestors {ancestors}"
    )
    return web.Response(
        text=render_runtime(
            published,
            host_origin=host_origin,
            chat_capability=chat_capability,
        ),
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


def _approved_referrer_origin(
    request: web.Request,
    published,
) -> str | None:
    referrer = request.headers.get("Referer", "")
    if not referrer:
        return None
    try:
        parsed = urlsplit(referrer)
        if not parsed.hostname:
            return None
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        origin = f"{parsed.scheme}://{host}"
        if parsed.port is not None:
            origin += f":{parsed.port}"
        normalized = _service(request).normalize_origin(origin)
    except (InvalidAllowedDomain, ValueError):
        return None
    return normalized if normalized in published.allowed_domains else None


async def _public_chat_payload(request: web.Request) -> dict:
    try:
        raw = await request.read()
        if len(raw) > _MAX_PUBLIC_CHAT_BODY_BYTES:
            raise ValueError("chat body is too large")
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError, ValueError) as error:
        raise ChatServiceError(
            "invalid_chat_request",
            "Параметры чата некорректны",
            status=400,
        ) from error
    expected = {
        "request_id",
        "message",
        "revision",
        "session_id",
        "host_origin",
        "capability",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ChatServiceError(
            "invalid_chat_request",
            "Параметры чата некорректны",
            status=400,
        )
    return payload


async def public_runtime_chat(request: web.Request) -> web.Response:
    request_id: str | None = None
    try:
        if request.headers.get("Origin") != "null":
            raise ChatServiceError(
                "chat_origin_denied",
                "Чат доступен только из опубликованного виджета",
                status=403,
            )
        payload = await _public_chat_payload(request)
        supplied_request_id = payload.get("request_id")
        message = payload.get("message")
        revision = payload.get("revision")
        session_id = payload.get("session_id")
        host_origin = payload.get("host_origin")
        capability = payload.get("capability")
        if (
            not isinstance(supplied_request_id, str)
            or _PUBLIC_REQUEST_ID.fullmatch(supplied_request_id) is None
            or not isinstance(message, str)
            or not message.strip()
            or len(message) > _MAX_PUBLIC_CHAT_MESSAGE_CHARS
            or "\x00" in message
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or not isinstance(session_id, str)
            or _PUBLIC_SESSION_ID.fullmatch(session_id) is None
            or not isinstance(host_origin, str)
            or len(host_origin) > _MAX_PUBLIC_CHAT_ORIGIN_CHARS
            or not isinstance(capability, str)
            or not capability
            or len(capability) > _MAX_PUBLIC_CHAT_CAPABILITY_CHARS
        ):
            raise ChatServiceError(
                "invalid_chat_request",
                "Параметры чата некорректны",
                status=400,
            )
        request_id = supplied_request_id
        service = _service(request)
        published = await service.resolve(request.match_info["stable_key"])
        try:
            normalized_origin = service.normalize_origin(host_origin)
        except InvalidAllowedDomain as error:
            raise ChatServiceError(
                "chat_origin_denied",
                "Домен не разрешён для этого виджета",
                status=403,
            ) from error
        if normalized_origin not in published.allowed_domains:
            raise ChatServiceError(
                "chat_origin_denied",
                "Домен не разрешён для этого виджета",
                status=403,
            )
        if revision != published.revision:
            raise ChatServiceError(
                "chat_release_changed",
                "Виджет обновлён. Перезагрузите страницу",
                status=409,
            )
        _verify_chat_capability(
            request,
            capability,
            published=published,
            host_origin=normalized_origin,
        )
        domain_hash = hashlib.sha256(normalized_origin.encode("utf-8")).hexdigest()[:24]
        remote_address = _visitor_remote_address(request)
        await request.app[PUBLICATION_CHAT_RATE_LIMITER_KEY].check(
            publication_id=str(published.publication_id),
            remote_address=remote_address,
            request_identity=(
                f"{published.release_id}:{domain_hash}:{session_id}:{request_id}"
            ),
            message=message,
        )
        factory = get_session_factory(request.app)
        async with factory() as database:
            row = (
                await database.execute(
                    select(GenerationArtifact, GenerationRun, Project)
                    .join(GenerationRun, GenerationArtifact.run_id == GenerationRun.id)
                    .join(Project, GenerationRun.project_id == Project.id)
                    .where(GenerationArtifact.id == published.artifact_id)
                )
            ).one_or_none()
            reference_context = (
                await _chat_reference_context(database, row[1].id)
                if row is not None
                else ""
            )
        if row is None:
            raise ChatServiceError(
                "chat_not_ready",
                "Опубликованный виджет недоступен для чата",
                status=409,
            )
        artifact, run, project = row
        configured = artifact.config.get("artifact") if isinstance(artifact.config, dict) else {}
        art_direction = (
            str(configured.get("art_direction", "")) if isinstance(configured, dict) else ""
        )
        try:
            assistant_persona = assistant_persona_from_artifact_config(
                artifact.config
            )
        except (TypeError, ValueError) as error:
            raise ChatServiceError(
                "chat_not_ready",
                "Опубликованный виджет содержит некорректную конфигурацию чата",
                status=409,
            ) from error
        chat_service = request.app.get(CHAT_SERVICE_KEY)
        if chat_service is None:
            raise ChatServiceError(
                "chat_not_configured",
                "Чат временно не настроен",
                status=503,
                retryable=True,
            )
        remote_hash = hashlib.sha256(
            (
                remote_address
                if remote_address is not None
                else f"session:{session_id}"
            ).encode("utf-8")
        ).hexdigest()[:24]
        reply = await chat_service.reply(
            scope=(
                f"publication:{published.publication_id}:release:{published.release_id}:"
                f"domain:{domain_hash}"
            ),
            session_id=session_id,
            client_id=(
                f"publication:{published.publication_id}:domain:{domain_hash}:"
                f"remote:{remote_hash}"
            ),
            request_id=request_id,
            text=message,
            context=ChatContext(
                source_url=project.source_url,
                brief=project.brief or "",
                art_direction=art_direction,
                reference_context=reference_context,
                assistant_persona=assistant_persona,
            ),
            run_id=run.id,
        )
    except (PublicationNotFound, ReleaseCorrupt):
        return _chat_error(
            "chat_not_found",
            "Опубликованный виджет не найден",
            status=404,
            request_id=request_id,
        )
    except ChatServiceError as error:
        return _chat_error(
            error.code,
            error.public_message,
            status=error.status,
            request_id=request_id,
            retryable=error.retryable,
        )
    return web.json_response(
        {"request_id": reply.request_id, "reply": reply.text},
        headers={"Access-Control-Allow-Origin": "*"},
    )


def setup_publication_routes(app: web.Application) -> None:
    config = app.get("config")
    configured_secret = getattr(config, "publication_chat_signing_secret", None)
    if isinstance(configured_secret, str) and configured_secret.strip():
        signing_key = configured_secret.strip().encode("utf-8")
    else:
        signing_key = secrets.token_bytes(32)
    app[PUBLICATION_CHAT_SIGNING_KEY] = signing_key
    app[PUBLICATION_CHAT_RATE_LIMITER_KEY] = _PublicChatRateLimiter(
        publication_limit=int(
            getattr(config, "publication_chat_key_rate_limit_requests", 120)
        ),
        ip_limit=int(getattr(config, "publication_chat_ip_rate_limit_requests", 60)),
        window_seconds=int(getattr(config, "chat_rate_limit_window_seconds", 60)),
    )
    app[PUBLICATION_CHAT_TRUSTED_PROXIES_KEY] = tuple(
        ip_network(cidr, strict=False)
        for cidr in getattr(config, "publication_chat_trusted_proxy_cidrs", ())
    )
    app.router.add_get(
        "/api/projects/{project_id}/publication", get_project_publication
    )
    app.router.add_post("/api/projects/{project_id}/publish", publish_project)
    app.router.add_post(
        "/api/publications/{publication_id}/rollback", rollback_publication
    )
    app.router.add_get("/embed/{stable_key}.js", embed_loader)
    app.router.add_get("/runtime/{stable_key}", runtime)
    app.router.add_post("/runtime/{stable_key}/chat", public_runtime_chat)


__all__ = ["setup_publication_routes"]
