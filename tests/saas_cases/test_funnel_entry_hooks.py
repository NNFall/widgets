from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace
from uuid import UUID

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import setup as setup_session
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.routes import OAUTH_PROVIDERS_KEY, setup_auth_routes
from app.analytics.routes import setup_analytics_routes
from app.auth.session_storage import DatabaseSessionStorage
from app.db.base import Base
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import (
    AnonymousDraft,
    AuthSession,
    FunnelEvent,
    FunnelJourney,
    GenerationRun,
    OAuthState,
    Project,
)
from tests.saas_cases.test_auth_routes import FakeProvider
from tests.saas_cases.test_project_routes import _project_app


@pytest.mark.asyncio
async def test_draft_and_oauth_boundaries_emit_only_server_owned_funnel_events() -> (
    None
):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {"google": FakeProvider()}
    setup_session(
        app,
        DatabaseSessionStorage(
            cookie_name="kaigo_session",
            max_age=3600,
            secure=False,
            samesite="Lax",
        ),
    )
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    setup_analytics_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        entry = await client.post(
            "/api/analytics/entry",
            json={"campaign": {"utm_source": "telegram", "utm_campaign": "launch"}},
        )
        assert entry.status == 204
        draft_response = await client.post(
            "/api/drafts",
            json={
                "url": "https://private.example/path",
                "brief": "Private customer brief",
                "email": "must-not-store@example.com",
                "raw_ip": "203.0.113.42",
                "campaign": {
                    "utm_source": "telegram",
                    "utm_medium": "social",
                    "utm_campaign": "launch",
                    "utm_term": "widgets",
                    "utm_content": "hero",
                    "url": "https://must-not-store.example",
                    "brief": "must not store",
                    "email": "campaign-private@example.com",
                    "ip": "203.0.113.43",
                },
            },
        )
        draft = await draft_response.json()
        started = await client.get(
            f"/api/auth/google/start?draft_id={draft['id']}",
            allow_redirects=False,
        )
        raw_state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        completed = await client.get(
            f"/api/auth/google/callback?state={raw_state}&code=valid-code",
            allow_redirects=False,
        )
        assert completed.status == 302

        async with factory() as database:
            stored_draft = await database.get(AnonymousDraft, UUID(draft["id"]))
            state = await database.scalar(select(OAuthState))
            project = await database.scalar(select(Project))
            auth_session = await database.scalar(
                select(AuthSession).where(AuthSession.user_id.is_not(None))
            )
            journeys = list((await database.execute(select(FunnelJourney))).scalars())
            events = list((await database.execute(select(FunnelEvent))).scalars())

        by_type = {event.event_type: event for event in events}
        assert set(by_type) == {
            "landing_entered",
            "composer_submitted",
            "auth_started",
            "auth_completed",
            "authenticated_project",
        }
        assert len(journeys) == 1
        journey = journeys[0]
        assert stored_draft.journey_id == journey.id
        assert state.journey_id == journey.id
        assert project.journey_id == journey.id
        assert auth_session.payload["session"]["funnel_journey_id"] == str(journey.id)
        assert {event.journey_id for event in events} == {journey.id}
        composed = by_type["composer_submitted"]
        started_event = by_type["auth_started"]
        completed_event = by_type["auth_completed"]
        assert composed.event_key == f"composer_submitted:draft:{draft['id']}"
        assert composed.anonymous_draft_id == state.draft_id
        assert composed.campaign_source == "telegram"
        assert composed.campaign_medium is None
        assert composed.campaign_name == "launch"
        assert composed.campaign_term is None
        assert composed.campaign_content is None
        assert started_event.event_key == f"auth_started:oauth_state:{state.id}"
        assert started_event.oauth_state_id == state.id
        assert started_event.anonymous_draft_id == state.draft_id
        assert completed_event.event_key == f"auth_completed:oauth_state:{state.id}"
        assert completed_event.oauth_state_id == state.id
        assert completed_event.anonymous_draft_id == state.draft_id
        assert completed_event.user_id == project.owner_user_id
        assert completed_event.project_id == project.id
        for event in events:
            assert event.campaign_source == "telegram"
            assert event.campaign_name == "launch"
            assert event.campaign_medium is None
        stored = " ".join(
            str(value)
            for event in events
            for value in event.__dict__.values()
            if value is not None
        )
        assert "private.example" not in stored
        assert "Private customer brief" not in stored
        assert "must-not-store@example.com" not in stored
        assert "campaign-private@example.com" not in stored
        assert "must-not-store.example" not in stored
        assert "203.0.113.42" not in stored
        assert "203.0.113.43" not in stored
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_queue_replay_emits_one_run_queued_event(tmp_path) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        headers = {
            "Idempotency-Key": "funnel-run-key",
            "X-CSRF-Token": "test-csrf",
        }
        first = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers=headers,
        )
        replay = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers=headers,
        )
        first_payload = await first.json()
        replay_payload = await replay.json()

        async with factory() as database:
            project = await database.get(Project, project_id)
            run = await database.get(GenerationRun, UUID(first_payload["id"]))
            events = list((await database.execute(select(FunnelEvent))).scalars())
            count = await database.scalar(select(func.count()).select_from(FunnelEvent))

        assert first.status == replay.status == 202
        assert replay_payload["id"] == first_payload["id"]
        assert count == 1
        assert events[0].event_type == "run_queued"
        assert project.journey_id is not None
        assert run.journey_id == project.journey_id
        assert events[0].journey_id == project.journey_id
        assert events[0].event_key == f"run_queued:run:{first_payload['id']}"
        assert events[0].user_id == 10
        assert events[0].project_id == project_id
        assert str(events[0].run_id) == first_payload["id"]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("config_state", ["disabled", "missing"])
async def test_run_queue_respects_fail_closed_funnel_journeys_flag(
    tmp_path,
    config_state: str,
) -> None:
    def configure(app: web.Application, _factory) -> None:
        if config_state == "missing":
            del app["config"]
        else:
            app["config"] = SimpleNamespace(
                funnel_journeys_enabled=False,
                project_versions_enabled=False,
            )

    engine, factory, client, project_id, _ = await _project_app(
        tmp_path,
        configure_app=configure,
    )
    try:
        await client.post("/test/login/10")

        response = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={
                "Idempotency-Key": "funnel-disabled-run-key",
                "X-CSRF-Token": "test-csrf",
            },
        )
        payload = await response.json()

        async with factory() as database:
            project = await database.get(Project, project_id)
            run = await database.get(GenerationRun, UUID(payload["id"]))
            journey_count = await database.scalar(
                select(func.count()).select_from(FunnelJourney)
            )
            events = list((await database.scalars(select(FunnelEvent))).all())

        assert response.status == 202
        assert project.journey_id is None
        assert run.journey_id is None
        assert journey_count == 0
        assert [(event.event_type, event.journey_id) for event in events] == [
            ("run_queued", None)
        ]
    finally:
        await client.close()
        await engine.dispose()
