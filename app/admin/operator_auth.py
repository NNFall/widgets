from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Literal

from aiohttp import web
from aiohttp_session import get_session
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.saas.models import UserIdentity
from builder_lab.forensics.config import GenerationForensicsConfig


@dataclass(frozen=True, slots=True)
class OperatorPrincipal:
    user_id: int | None
    auth_method: Literal["oauth", "service_token"]


def _forensics_config(request: web.Request) -> GenerationForensicsConfig:
    app_config = request.app.get("config")
    config = getattr(app_config, "generation_forensics", None)
    if not isinstance(config, GenerationForensicsConfig) or not config.enabled:
        raise web.HTTPNotFound(headers={"Cache-Control": "no-store"})
    return config


async def require_verified_operator(request: web.Request) -> int:
    """Require a public OAuth session and an exact verified-email allowlist match."""

    config = _forensics_config(request)
    session = await get_session(request)
    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        raise web.HTTPUnauthorized(
            text='{"error":{"code":"authentication_required"}}',
            content_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    factory = get_session_factory(request.app)
    async with factory() as database:
        identity_id = await database.scalar(
            select(UserIdentity.id)
            .where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider.in_(("google", "yandex")),
                UserIdentity.email_verified.is_(True),
                func.lower(UserIdentity.email).in_(config.admin_emails),
            )
            .limit(1)
        )
    if identity_id is None:
        raise web.HTTPForbidden(
            text='{"error":{"code":"operator_access_denied"}}',
            content_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    return user_id


def _bearer_token(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    scheme, separator, token = value.partition(" ")
    if (
        scheme.casefold() != "bearer"
        or not separator
        or not token
        or token != token.strip()
        or " " in token
    ):
        return None
    return token


async def require_read_operator(request: web.Request) -> OperatorPrincipal:
    """Require the OAuth operator or the optional read-only service token."""

    _forensics_config(request)
    configured_token = getattr(request.app.get("config"), "operator_read_token", None)
    supplied_token = _bearer_token(request.headers.get("Authorization"))
    if (
        isinstance(configured_token, str)
        and supplied_token is not None
        and secrets.compare_digest(configured_token, supplied_token)
    ):
        return OperatorPrincipal(user_id=None, auth_method="service_token")

    # Keep the OAuth boundary as the fallback and avoid making the service
    # credential a general-purpose admin identity.
    return OperatorPrincipal(
        user_id=await require_verified_operator(request),
        auth_method="oauth",
    )


def operator_log_extra(principal: OperatorPrincipal) -> dict[str, object]:
    extra: dict[str, object] = {"operator_auth_method": principal.auth_method}
    if principal.user_id is not None:
        extra["operator_user_id"] = principal.user_id
    return extra


__all__ = [
    "OperatorPrincipal",
    "operator_log_extra",
    "require_read_operator",
    "require_verified_operator",
]
