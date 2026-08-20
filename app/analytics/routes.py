from __future__ import annotations

import json
from uuid import UUID

from aiohttp import web
from aiohttp_session import get_session

from app.analytics.service import (
    CLIENT_FUNNEL_EVENT_TYPES,
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


async def _read_payload(request: web.Request) -> dict[str, object]:
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
    if not isinstance(payload, dict):
        raise _bad_request("JSON body must be an object")
    return payload


def _session_journey_id(session) -> UUID | None:
    candidate = session.get(FUNNEL_JOURNEY_SESSION_KEY)
    try:
        return UUID(candidate) if isinstance(candidate, str) else None
    except ValueError:
        return None


async def _check_rate_limit(request: web.Request, session, *, scope: str) -> None:
    await request.app[ENTRY_RATE_LIMITER_KEY].check(
        request,
        account_id=(
            session.get("user_id") if isinstance(session.get("user_id"), int) else None
        ),
        scope=scope,
    )


async def record_landing_entry(request: web.Request) -> web.Response:
    session = await get_session(request)
    await _check_rate_limit(request, session, scope="funnel_entry")
    payload = await _read_payload(request)
    if set(payload) - {"campaign"}:
        raise _bad_request("JSON body must contain only optional campaign")
    campaign = payload.get("campaign")
    if campaign is not None and not isinstance(campaign, dict):
        raise _bad_request("campaign must be an object")

    journey_id = _session_journey_id(session)
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


async def record_client_event(request: web.Request) -> web.Response:
    session = await get_session(request)
    await _check_rate_limit(request, session, scope="funnel_event")
    payload = await _read_payload(request)
    if set(payload) != {"event_type"}:
        raise _bad_request("JSON body must contain only event_type")
    event_type = payload.get("event_type")
    if not isinstance(event_type, str) or event_type not in CLIENT_FUNNEL_EVENT_TYPES:
        raise _bad_request("unsupported client funnel event")

    async with session_scope(request.app) as database:
        result = await ensure_funnel_journey(
            database,
            journey_id=_session_journey_id(session),
            campaign=None,
        )
        if result.created:
            await record_funnel_event(
                database,
                event_type="landing_entered",
                event_key=f"landing_entered:journey:{result.journey.id}",
                journey_id=result.journey.id,
            )
        await record_funnel_event(
            database,
            event_type=event_type,
            event_key=f"{event_type}:journey:{result.journey.id}",
            journey_id=result.journey.id,
        )
        stored_journey_id = str(result.journey.id)
    session[FUNNEL_JOURNEY_SESSION_KEY] = stored_journey_id
    return web.Response(status=204)


def setup_analytics_routes(app: web.Application, *, enabled: bool = True) -> None:
    if enabled:
        app.router.add_post("/api/analytics/entry", record_landing_entry)
        app.router.add_post("/api/analytics/event", record_client_event)


__all__ = ["setup_analytics_routes"]
