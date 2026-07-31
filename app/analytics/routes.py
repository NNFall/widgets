from __future__ import annotations

import json
from uuid import UUID

from aiohttp import web
from aiohttp_session import get_session

from app.analytics.service import (
    FUNNEL_JOURNEY_SESSION_KEY,
    ensure_funnel_journey,
    record_funnel_event,
)
from app.auth.routes import ENTRY_MAX_BODY_BYTES_KEY, ENTRY_RATE_LIMITER_KEY
from app.db.session import session_scope


def _bad_request(message: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(
        text=json.dumps({"error": message}),
        content_type="application/json",
    )


async def record_landing_entry(request: web.Request) -> web.Response:
    session = await get_session(request)
    await request.app[ENTRY_RATE_LIMITER_KEY].check(
        request,
        account_id=(
            session.get("user_id")
            if isinstance(session.get("user_id"), int)
            else None
        ),
        scope="funnel_entry",
    )
    maximum = request.app[ENTRY_MAX_BODY_BYTES_KEY]
    if request.content_length is not None and request.content_length > maximum:
        raise web.HTTPRequestEntityTooLarge(
            max_size=maximum,
            actual_size=request.content_length,
        )
    raw = await request.read()
    if len(raw) > maximum:
        raise web.HTTPRequestEntityTooLarge(max_size=maximum, actual_size=len(raw))
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _bad_request("invalid JSON") from error
    if not isinstance(payload, dict) or set(payload) - {"campaign"}:
        raise _bad_request("JSON body must contain only optional campaign")
    campaign = payload.get("campaign")
    if campaign is not None and not isinstance(campaign, dict):
        raise _bad_request("campaign must be an object")

    candidate = session.get(FUNNEL_JOURNEY_SESSION_KEY)
    try:
        journey_id = UUID(candidate) if isinstance(candidate, str) else None
    except ValueError:
        journey_id = None
    async with session_scope(request.app) as database:
        result = await ensure_funnel_journey(
            database,
            journey_id=journey_id,
            campaign=campaign,
        )
        await record_funnel_event(
            database,
            event_type="landing_entered",
            event_key=f"landing_entered:journey:{result.journey.id}",
            journey_id=result.journey.id,
        )
        stored_journey_id = str(result.journey.id)
    session[FUNNEL_JOURNEY_SESSION_KEY] = stored_journey_id
    return web.Response(status=204)


def setup_analytics_routes(app: web.Application, *, enabled: bool = True) -> None:
    if enabled:
        app.router.add_post("/api/analytics/entry", record_landing_entry)


__all__ = ["setup_analytics_routes"]
