from __future__ import annotations

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import setup as setup_session
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.routes import setup_auth_routes
from app.auth.session_storage import DatabaseSessionStorage
from app.db.base import Base
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import FunnelEvent, FunnelJourney


async def _client(*, limit: int = 20):
    import app.saas.models  # noqa: F401
    from app.analytics.routes import setup_analytics_routes

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(
        app,
        public_base_url="https://kaigo.space",
        entry_rate_limit_requests=limit,
    )
    setup_analytics_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, factory, client


@pytest.mark.asyncio
async def test_landing_entry_replay_freezes_campaign_and_records_one_event() -> None:
    engine, factory, client = await _client()
    try:
        first = await client.post(
            "/api/analytics/entry",
            json={"campaign": {"utm_source": "telegram", "utm_campaign": "launch"}},
        )
        second = await client.post(
            "/api/analytics/entry",
            json={"campaign": {"utm_source": "google", "utm_campaign": "summer"}},
        )
        assert first.status == second.status == 204
        assert await first.read() == await second.read() == b""
        assert "journey" not in " ".join(first.headers).lower()

        async with factory() as database:
            journeys = list((await database.scalars(select(FunnelJourney))).all())
            events = list((await database.scalars(select(FunnelEvent))).all())
        assert len(journeys) == len(events) == 1
        assert journeys[0].campaign_source == "telegram"
        assert journeys[0].campaign_name == "launch"
        assert events[0].event_key == f"landing_entered:journey:{journeys[0].id}"
        assert events[0].journey_id == journeys[0].id
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_client_funnel_events_are_allowlisted_private_and_idempotent() -> None:
    engine, factory, client = await _client()
    try:
        assert (
            await client.post(
                "/api/analytics/entry",
                json={"campaign": {"utm_source": "yandex", "utm_medium": "cpc"}},
            )
        ).status == 204

        for event_type in (
            "landing_scrolled_end",
            "studio_cta_clicked",
            "studio_entered",
        ):
            first = await client.post(
                "/api/analytics/event",
                json={"event_type": event_type},
            )
            replay = await client.post(
                "/api/analytics/event",
                json={"event_type": event_type},
            )
            assert first.status == replay.status == 204
            assert await first.read() == await replay.read() == b""

        for payload in (
            {"event_type": "published"},
            {"event_type": "landing_scrolled_end", "ip": "203.0.113.42"},
            {
                "event_type": "landing_scrolled_end",
                "metadata": {"email": "private@example.com"},
            },
        ):
            rejected = await client.post("/api/analytics/event", json=payload)
            assert rejected.status == 400

        async with factory() as database:
            journeys = list((await database.scalars(select(FunnelJourney))).all())
            events = list(
                (
                    await database.scalars(
                        select(FunnelEvent).order_by(FunnelEvent.occurred_at)
                    )
                ).all()
            )

        assert len(journeys) == 1
        assert [event.event_type for event in events] == [
            "landing_entered",
            "landing_scrolled_end",
            "studio_cta_clicked",
            "studio_entered",
        ]
        assert {event.journey_id for event in events} == {journeys[0].id}
        assert all(event.campaign_source == "yandex" for event in events)
        assert all(event.campaign_medium == "cpc" for event in events)
        stored = " ".join(
            str(value)
            for event in events
            for value in event.__dict__.values()
            if value is not None
        )
        assert "203.0.113.42" not in stored
        assert "private@example.com" not in stored
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.parametrize("field", ["journey_id", "url", "email", "ip"])
@pytest.mark.asyncio
async def test_landing_entry_rejects_client_identity_fields(field: str) -> None:
    engine, factory, client = await _client()
    try:
        response = await client.post("/api/analytics/entry", json={field: "private"})
        assert response.status == 400
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(FunnelJourney))
                == 0
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_landing_entry_rate_limit_precedes_database_write() -> None:
    engine, factory, client = await _client(limit=1)
    try:
        assert (await client.post("/api/analytics/entry", json={})).status == 204
        rejected = await client.post("/api/analytics/entry", json={})
        assert rejected.status == 429
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(FunnelJourney))
                == 1
            )
            assert (
                await database.scalar(select(func.count()).select_from(FunnelEvent))
                == 1
            )
    finally:
        await client.close()
        await engine.dispose()
