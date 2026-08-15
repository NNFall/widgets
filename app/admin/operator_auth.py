from __future__ import annotations

from aiohttp import web
from aiohttp_session import get_session
from sqlalchemy import func, select

from app.db.session import get_session_factory
from app.saas.models import UserIdentity
from builder_lab.forensics.config import GenerationForensicsConfig


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


__all__ = ["require_verified_operator"]
