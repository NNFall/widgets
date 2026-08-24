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


@dataclass(frozen=True, slots=True)
class DeveloperPrincipal:
    """Verified browser identity allowed to debug every SaaS project."""

    user_id: int
    email: str


def _developer_config(request: web.Request) -> GenerationForensicsConfig | None:
    app_config = request.app.get("config")
    config = getattr(app_config, "generation_forensics", None)
    if not isinstance(config, GenerationForensicsConfig):
        return None
    if not config.enabled or not config.admin_emails:
        return None
    return config


async def optional_developer_principal(
    request: web.Request,
) -> DeveloperPrincipal | None:
    """Return the verified OAuth developer identity, if this session has one.

    This helper is intentionally browser-session-only. It never accepts the
    read-only bearer token because that token must not become a mutation or
    cross-tenant project credential.
    """

    config = _developer_config(request)
    if config is None:
        return None
    session = await get_session(request)
    user_id = session.get("user_id")
    if not isinstance(user_id, int):
        return None
    factory = get_session_factory(request.app)
    async with factory() as database:
        identity = await database.scalar(
            select(UserIdentity)
            .where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider.in_(("google", "yandex")),
                UserIdentity.email_verified.is_(True),
                func.lower(UserIdentity.email).in_(config.admin_emails),
            )
            .limit(1)
        )
    if identity is None or not isinstance(identity.email, str):
        return None
    return DeveloperPrincipal(user_id=user_id, email=identity.email)


async def developer_session_snapshot(request: web.Request) -> dict[str, object]:
    principal = await optional_developer_principal(request)
    return {
        "enabled": principal is not None,
        "scope": "all_projects" if principal is not None else None,
    }


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
    "DeveloperPrincipal",
    "OperatorPrincipal",
    "developer_session_snapshot",
    "operator_log_extra",
    "optional_developer_principal",
    "require_read_operator",
    "require_verified_operator",
]
