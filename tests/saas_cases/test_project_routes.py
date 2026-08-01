from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from uuid import UUID

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.models.contracts import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderCapabilities,
)
from app.models.router import (
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
    SqlModelCallAudit,
)
from app.chat import CHAT_SERVICE_KEY, RoutedChatService
from app.projects.routes import setup_project_routes
from app.projects.serializers import serialize_event
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationForensicManifest,
    GenerationRun,
    FunnelEvent,
    FunnelJourney,
    ModelCall,
    Project,
    TrialEntitlement,
    UsageLedger,
    UserIdentity,
)
from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.models import BuilderRequest, EngineName
from builder_lab.preview import PREVIEW_CSP
from tests.builder_lab_cases.test_validation import artifact

POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


def test_registry_v1_event_reprojects_stored_public_candidate_fail_closed() -> None:
    event = GenerationEvent(
        id=9001,
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        sequence=1,
        event_type="stage.started",
        public_message="Started",
        payload={"status": "raw", "stage": "foundation"},
        registry_version=1,
        public_payload={
            "status": "stored",
            "stage": "foundation",
            "diagnostic": "never-public",
            "token": "never-public-token",
        },
    )

    serialized = serialize_event(event)

    assert serialized["payload"] == {
        "status": "stored",
        "stage": "foundation",
    }
    assert "never-public" not in json.dumps(serialized)


def test_owner_event_serializer_never_exposes_screenshot_checksum() -> None:
    checksum = "a" * 64
    event = GenerationEvent(
        id=9002,
        run_id=UUID("00000000-0000-0000-0000-000000000001"),
        sequence=2,
        event_type="screenshot.captured",
        public_message="Screenshot captured",
        payload={},
        registry_version=1,
        public_payload={
            "status": "completed",
            "stage": "motion_polish",
            "revision": 5,
            "output_refs": [checksum, "desktop-open-initial"],
        },
    )

    serialized = serialize_event(event)

    assert serialized["payload"]["output_refs"] == ["desktop-open-initial"]
    assert checksum not in json.dumps(serialized)


async def _wait_until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0.01)


async def _project_app(
    tmp_path,
    *,
    configure_app=None,
    generation_forensics: GenerationForensicsConfig | None = None,
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add_all(
            [
                Tenant(id=1, name="Alpha", slug="alpha"),
                Tenant(id=2, name="Beta", slug="beta"),
                User(id=10, tenant_id=1, email="owner@example.com"),
                User(id=11, tenant_id=1, email="other@example.com"),
                User(id=20, tenant_id=2, email="beta@example.com"),
            ]
        )
        await database.flush()
        database.add_all(
            [
                UserIdentity(
                    user_id=10,
                    provider="google",
                    provider_subject="owner",
                    email="owner@example.com",
                    email_verified=True,
                ),
                UserIdentity(
                    user_id=11,
                    provider="google",
                    provider_subject="other",
                    email="other@example.com",
                    email_verified=True,
                ),
            ]
        )
        owner = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://example.com/",
            brief="Build a sales assistant",
        )
        foreign = Project(
            tenant_id=1,
            owner_user_id=11,
            source_url="https://foreign.example.com/",
        )
        database.add_all([owner, foreign])
        await database.flush()
        owner_id, foreign_id = owner.id, foreign.id

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(
        funnel_journeys_enabled=True,
        project_versions_enabled=False,
    )
    app["project_sse_poll_seconds"] = 0.01
    setup_session(app, SimpleCookieStorage(cookie_name="kaigo_test"))

    async def login(request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        session = await get_session(request)
        session["user_id"] = user_id
        session["tenant_id"] = 1 if user_id in {10, 11} else 2
        session["email"] = f"user-{user_id}@example.com"
        session["csrf_token"] = "test-csrf"
        return web.json_response({"csrf_token": "test-csrf"})

    app.router.add_post("/test/login/{user_id}", login)
    setup_project_routes(app, generation_forensics=generation_forensics)
    if configure_app is not None:
        configure_app(app, factory)
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, factory, client, owner_id, foreign_id


@pytest.mark.asyncio
async def test_create_project_requires_owner_session_and_csrf_and_always_creates_a_new_draft(
    tmp_path,
) -> None:
    engine, factory, client, _project_id, _ = await _project_app(tmp_path)
    payload = {
        "url": "https://fresh.example.com/services",
        "campaign": {
            "utm_source": "telegram",
            "utm_campaign": "launch",
            "url": "https://must-not-store.example",
            "brief": "must not store",
            "email": "campaign-private@example.com",
            "ip": "203.0.113.43",
        },
        "brief": "Спокойный консультант по услугам",
    }
    try:
        unauthenticated = await client.post(
            "/api/projects",
            json=payload,
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert unauthenticated.status == 401

        await client.post("/test/login/10")
        without_csrf = await client.post("/api/projects", json=payload)
        assert without_csrf.status == 403

        first = await client.post(
            "/api/projects",
            json=payload,
            headers={"X-CSRF-Token": "test-csrf"},
        )
        second = await client.post(
            "/api/projects",
            json=payload,
            headers={"X-CSRF-Token": "test-csrf"},
        )

        assert first.status == second.status == 201
        first_payload, second_payload = await first.json(), await second.json()
        assert first_payload["id"] != second_payload["id"]
        assert first_payload["source_url"] == payload["url"]
        assert first_payload["brief"] == payload["brief"]
        assert first_payload["owner_user_id"] == 10
        assert first_payload["tenant_id"] == 1
        assert first_payload["status"] == "draft"

        async with factory() as database:
            created = list(
                (
                    await database.execute(
                        select(Project).where(Project.source_url == payload["url"])
                    )
                ).scalars()
            )
            funnel_events = list(
                (
                    await database.execute(
                        select(FunnelEvent).order_by(FunnelEvent.occurred_at)
                    )
                ).scalars()
            )
            journeys = list((await database.scalars(select(FunnelJourney))).all())
        assert len(created) == 2
        assert {(project.owner_user_id, project.tenant_id) for project in created} == {
            (10, 1)
        }
        assert len(journeys) == 1
        assert {project.journey_id for project in created} == {journeys[0].id}
        assert len(funnel_events) == 4
        assert all(event.campaign_source == "telegram" for event in funnel_events)
        assert all(event.campaign_name == "launch" for event in funnel_events)
        assert {
            (
                event.event_type,
                event.event_key,
                event.user_id,
                event.project_id,
            )
            for event in funnel_events
        } == {
            (
                event_type,
                f"{event_type}:project:{project_id}",
                10,
                project_id,
            )
            for project_id in {created_project.id for created_project in created}
            for event_type in {"composer_submitted", "authenticated_project"}
        }
        stored_funnel_data = " ".join(
            str(value)
            for event in funnel_events
            for value in event.__dict__.values()
            if value is not None
        )
        assert "fresh.example.com" not in stored_funnel_data
        assert payload["brief"] not in stored_funnel_data
        assert "owner@example.com" not in stored_funnel_data
        assert "127.0.0.1" not in stored_funnel_data
        assert "must-not-store.example" not in stored_funnel_data
        assert "campaign-private@example.com" not in stored_funnel_data
        assert "203.0.113.43" not in stored_funnel_data
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_project_does_not_write_journey_when_rollout_is_disabled(
    tmp_path,
) -> None:
    def configure(app: web.Application, _factory) -> None:
        app["config"] = SimpleNamespace(funnel_journeys_enabled=False)

    engine, factory, client, _project_id, _ = await _project_app(
        tmp_path,
        configure_app=configure,
    )
    try:
        await client.post("/test/login/10")
        response = await client.post(
            "/api/projects",
            json={"url": "https://disabled.example.com/", "brief": "disabled"},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert response.status == 201
        project_id = UUID((await response.json())["id"])
        async with factory() as database:
            project = await database.get(Project, project_id)
            journeys = list((await database.scalars(select(FunnelJourney))).all())
            events = list((await database.scalars(select(FunnelEvent))).all())
        assert project is not None and project.journey_id is None
        assert journeys == []
        assert [(event.event_type, event.journey_id) for event in events] == [
            ("composer_submitted", None)
        ]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [{}, {"url": "https://example.com"}, {"url": "not-a-url", "brief": ""}]
)
async def test_create_project_requires_explicit_valid_url_and_brief(
    tmp_path, body
) -> None:
    engine, _factory, client, _project_id, _ = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        response = await client.post(
            "/api/projects",
            json=body,
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert response.status == 400
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_owner_can_update_canonical_draft_fields_only_before_first_run(
    tmp_path,
) -> None:
    engine, factory, client, project_id, foreign_id = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        payload = {
            "url": "https://EXAMPLE.COM:443/services",
            "brief": "  Новый brief  ",
        }
        without_csrf = await client.patch(
            f"/api/projects/{project_id}", json=payload
        )
        assert without_csrf.status == 403
        foreign = await client.patch(
            f"/api/projects/{foreign_id}",
            json=payload,
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert foreign.status == 404

        updated = await client.patch(
            f"/api/projects/{project_id}",
            json=payload,
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert updated.status == 200
        updated_payload = await updated.json()
        assert updated_payload["source_url"] == "https://example.com/services"
        assert updated_payload["brief"] == "Новый brief"

        async with factory() as database, database.begin():
            database.add(
                GenerationRun(
                    project_id=project_id,
                    mode="express",
                    state="failed",
                    progress=0,
                    next_event_sequence=1,
                    idempotency_key="historical-run",
                )
            )

        locked = await client.patch(
            f"/api/projects/{project_id}",
            json={"url": "https://other.example.com/", "brief": "Поздно"},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert locked.status == 409
        async with factory() as database:
            stored = await database.get(Project, project_id)
        assert stored is not None
        assert stored.source_url == "https://example.com/services"
        assert stored.brief == "Новый brief"
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_run_is_202_idempotent_and_never_calls_engine_inline(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)

    class InlineEngineMustNotRun:
        async def run(self, *_args, **_kwargs):
            raise AssertionError(
                "HTTP project route must never execute the engine inline"
            )

    client.server.app["builder_engine"] = InlineEngineMustNotRun()
    try:
        unauthenticated = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={"Idempotency-Key": "first-run", "X-CSRF-Token": "test-csrf"},
        )
        assert unauthenticated.status == 401

        await client.post("/test/login/10")
        without_csrf = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={"Idempotency-Key": "first-run"},
        )
        assert without_csrf.status == 403

        first = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={"Idempotency-Key": "first-run", "X-CSRF-Token": "test-csrf"},
        )
        second = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={"Idempotency-Key": "first-run", "X-CSRF-Token": "test-csrf"},
        )
        assert first.status == second.status == 202
        first_payload, second_payload = await first.json(), await second.json()
        assert first_payload["status"] == "queued"
        assert second_payload["id"] == first_payload["id"]

        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(GenerationRun))
                == 1
            )
            run = (await database.execute(select(GenerationRun))).scalar_one()
            event = (await database.execute(select(GenerationEvent))).scalar_one()
            project = await database.get(Project, project_id)
            entitlement = (
                await database.execute(
                    select(TrialEntitlement).where(TrialEntitlement.user_id == 10)
                )
            ).scalar_one()
            assert event.event_type == "run.created"
            assert event.registry_version == 1
            assert event.public_payload == {"status": "queued"}
            assert event.forensic_ref is None
            assert event.run_id == run.id
            assert project.active_run_id == run.id
            assert entitlement.state == "reserved"
            assert (
                await database.scalar(
                    select(func.count())
                    .select_from(UsageLedger)
                    .where(UsageLedger.entry_type == "trial.reserve")
                )
                == 2
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_enabled_enqueue_creates_only_pending_forensic_sql_manifest(
    tmp_path,
) -> None:
    root = tmp_path / "private-generation-forensics"
    config = GenerationForensicsConfig(
        enabled=True,
        root=root,
        ttl_hours=120,
        max_bytes=32 * 1024 * 1024,
    )
    engine, factory, client, project_id, _ = await _project_app(
        tmp_path,
        generation_forensics=config,
    )
    try:
        await client.post("/test/login/10")
        response = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={
                "Idempotency-Key": "forensic-pending",
                "X-CSRF-Token": "test-csrf",
            },
        )
        assert response.status == 202
        run_id = UUID((await response.json())["id"])

        async with factory() as database:
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )
        assert manifest is not None
        assert manifest.state == "pending"
        assert manifest.storage_key == f"runs/{run_id.hex[:2]}/{run_id}"
        assert manifest.expires_at is None
        assert not root.exists()
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_owner_can_request_project_run_cancellation_idempotently_with_csrf(
    tmp_path,
) -> None:
    engine, factory, client, project_id, foreign_id = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        created = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={
                "Idempotency-Key": "cancel-source",
                "X-CSRF-Token": "test-csrf",
            },
        )
        assert created.status == 202
        run_id = UUID((await created.json())["id"])
        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_id)
            assert run is not None
            run.state = "running"

            foreign_run = GenerationRun(
                project_id=foreign_id,
                mode="express",
                state="running",
                next_event_sequence=1,
                idempotency_key="foreign-cancel",
            )
            database.add(foreign_run)
            await database.flush()
            foreign = await database.get(Project, foreign_id)
            assert foreign is not None
            foreign.active_run_id = foreign_run.id
            foreign_run_id = foreign_run.id

        without_csrf = await client.post(f"/api/runs/{run_id}/cancel", json={})
        assert without_csrf.status == 403
        foreign_response = await client.post(
            f"/api/runs/{foreign_run_id}/cancel",
            json={},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert foreign_response.status == 404

        first = await client.post(
            f"/api/runs/{run_id}/cancel",
            json={},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        second = await client.post(
            f"/api/runs/{run_id}/cancel",
            json={},
            headers={"X-CSRF-Token": "test-csrf"},
        )

        assert first.status == second.status == 202
        assert await first.json() == {
            "run_id": str(run_id),
            "cancel_requested": True,
            "status": "running",
        }
        assert await second.json() == await first.json()
        async with factory() as database:
            events = list(
                (
                    await database.execute(
                        select(GenerationEvent).where(
                            GenerationEvent.run_id == run_id,
                            GenerationEvent.event_type == "run.cancel_requested",
                        )
                    )
                ).scalars()
            )
        assert len(events) == 1
        assert events[0].public_message == "Запрошена отмена генерации"
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_compensated_project_run_is_idempotent_and_reserves_trial_once(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        created = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={
                "Idempotency-Key": "retry-source",
                "X-CSRF-Token": "test-csrf",
            },
        )
        assert created.status == 202
        source_run_id = UUID((await created.json())["id"])
        async with factory() as database, database.begin():
            source = await database.get(GenerationRun, source_run_id)
            project = await database.get(Project, project_id)
            assert source is not None and project is not None
            source.state = "failed"
            source.failure_category = "provider"
            source.error_code = "provider_unavailable"
            project.status = "failed"

        headers = {
            "Idempotency-Key": "retry-after-compensation",
            "X-CSRF-Token": "test-csrf",
        }
        first = await client.post(
            f"/api/runs/{source_run_id}/retry", json={}, headers=headers
        )
        second = await client.post(
            f"/api/runs/{source_run_id}/retry", json={}, headers=headers
        )

        assert first.status == second.status == 202
        first_payload, second_payload = await first.json(), await second.json()
        assert first_payload["id"] == second_payload["id"]
        assert first_payload["status"] == "queued"
        assert first_payload["id"] != str(source_run_id)
        async with factory() as database:
            runs = list(
                (
                    await database.execute(
                        select(GenerationRun).order_by(GenerationRun.created_at)
                    )
                ).scalars()
            )
            source = await database.get(GenerationRun, source_run_id)
            project = await database.get(Project, project_id)
            entitlement = await database.scalar(
                select(TrialEntitlement).where(TrialEntitlement.user_id == 10)
            )
            reserve_entries = await database.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.entry_type == "trial.reserve")
            )
        assert len(runs) == 2
        assert source is not None and source.trial_settlement == "compensated"
        assert project is not None and str(project.active_run_id) == first_payload["id"]
        assert entitlement is not None and entitlement.state == "reserved"
        assert reserve_entries == 4
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_rejects_reusing_source_run_idempotency_key(tmp_path) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        created = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={
                "Idempotency-Key": "source-key-must-not-be-reused",
                "X-CSRF-Token": "test-csrf",
            },
        )
        source_run_id = UUID((await created.json())["id"])
        async with factory() as database, database.begin():
            source = await database.get(GenerationRun, source_run_id)
            project = await database.get(Project, project_id)
            assert source is not None and project is not None
            source.state = "failed"
            source.failure_category = "provider"
            project.status = "failed"

        response = await client.post(
            f"/api/runs/{source_run_id}/retry",
            json={},
            headers={
                "Idempotency-Key": "source-key-must-not-be-reused",
                "X-CSRF-Token": "test-csrf",
            },
        )

        assert response.status == 409
        assert (await response.json())["error"]["code"] == "idempotency_key_conflict"
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(GenerationRun))
                == 1
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_rejects_consumed_trial_and_different_key_after_replacement(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        created = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={
                "Idempotency-Key": "consumed-source",
                "X-CSRF-Token": "test-csrf",
            },
        )
        source_run_id = UUID((await created.json())["id"])
        async with factory() as database, database.begin():
            source = await database.get(GenerationRun, source_run_id)
            project = await database.get(Project, project_id)
            assert source is not None and project is not None
            source.state = "failed"
            source.failure_category = "user"
            source.error_code = "invalid_request"
            project.status = "failed"

        consumed = await client.post(
            f"/api/runs/{source_run_id}/retry",
            json={},
            headers={
                "Idempotency-Key": "retry-consumed",
                "X-CSRF-Token": "test-csrf",
            },
        )
        assert consumed.status == 409
        assert (await consumed.json())["error"]["code"] == "trial_consumed"
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(GenerationRun))
                == 1
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_retry_with_one_key_creates_one_replacement_and_reservation(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        created = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={
                "Idempotency-Key": "concurrent-source",
                "X-CSRF-Token": "test-csrf",
            },
        )
        source_run_id = UUID((await created.json())["id"])
        async with factory() as database, database.begin():
            source = await database.get(GenerationRun, source_run_id)
            project = await database.get(Project, project_id)
            assert source is not None and project is not None
            source.state = "failed"
            source.failure_category = "provider"
            project.status = "failed"

        headers = {
            "Idempotency-Key": "one-concurrent-retry",
            "X-CSRF-Token": "test-csrf",
        }
        responses = await asyncio.gather(
            client.post(f"/api/runs/{source_run_id}/retry", json={}, headers=headers),
            client.post(f"/api/runs/{source_run_id}/retry", json={}, headers=headers),
        )

        assert [response.status for response in responses] == [202, 202]
        payloads = [await response.json() for response in responses]
        assert payloads[0]["id"] == payloads[1]["id"]
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(GenerationRun))
                == 2
            )
            assert (
                await database.scalar(
                    select(func.count())
                    .select_from(UsageLedger)
                    .where(UsageLedger.entry_type == "trial.reserve")
                )
                == 4
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [[], "express", None, 7])
async def test_create_run_rejects_non_object_json_with_structured_400(
    tmp_path,
    body,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        await client.post("/test/login/10")
        response = await client.post(
            f"/api/projects/{project_id}/runs",
            data=json.dumps(body),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "invalid-body",
                "X-CSRF-Token": "test-csrf",
            },
        )

        assert response.status == 400
        assert (await response.json()) == {"error": {"code": "invalid_body"}}
        async with factory() as database:
            assert (
                await database.scalar(select(func.count()).select_from(GenerationRun))
                == 0
            )
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_run_and_preview_reads_are_owner_scoped_and_restore_state(
    tmp_path,
) -> None:
    engine, factory, client, project_id, foreign_id = await _project_app(tmp_path)
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="running",
                progress=35,
                current_stage="foundation",
                next_event_sequence=3,
                idempotency_key="restore-run",
            )
            foreign_run = GenerationRun(
                project_id=foreign_id,
                mode="express",
                state="queued",
                idempotency_key="foreign-run",
            )
            database.add_all([run, foreign_run])
            await database.flush()
            database.add_all(
                [
                    GenerationEvent(
                        id=101,
                        run_id=run.id,
                        sequence=1,
                        event_type="run.created",
                        public_message="Queued",
                        payload={"status": "queued"},
                    ),
                    GenerationEvent(
                        id=102,
                        run_id=run.id,
                        sequence=2,
                        event_type="artifact.draft_staged",
                        public_message="Draft",
                        payload={
                            "artifact": _artifact_payload(1),
                            "diagnostic": "private stack trace",
                            "prompt": "private prompt",
                            "request": {"secret": "private"},
                            "status": "needs_repair",
                            "revision": 1,
                        },
                        forensic_ref="events/00000002-artifact.draft_staged.json",
                    ),
                ]
            )
            project = await database.get(Project, project_id)
            project.active_run_id = run.id
            project.status = "generating"
            run_id, foreign_run_id = run.id, foreign_run.id

        await client.post("/test/login/10")
        listed = await client.get("/api/projects")
        assert listed.status == 200
        assert [item["id"] for item in (await listed.json())["projects"]] == [
            str(project_id)
        ]

        detail = await client.get(f"/api/projects/{project_id}")
        assert detail.status == 200
        assert (await detail.json())["active_run"]["id"] == str(run_id)

        snapshot = await client.get(f"/api/runs/{run_id}")
        payload = await snapshot.json()
        assert snapshot.status == 200
        assert payload["status"] == "running"
        assert [event["sequence"] for event in payload["events"]] == [1, 2]
        assert payload["events"][1]["payload"] == {
            "status": "needs_repair",
            "revision": 1,
        }
        assert payload["preview"]["revision"] == 1
        assert payload["preview"]["source"] == "restorable_draft"
        owner_json = json.dumps(payload)
        for private_value in (
            "forensic_ref",
            "events/00000002-artifact.draft_staged.json",
            "private prompt",
            "private stack trace",
            "/admin/generation-runs",
        ):
            assert private_value not in owner_json

        preview = await client.get(f"/api/runs/{run_id}/preview")
        assert preview.status == 200
        assert (await preview.json())["artifact"]["revision"] == 1

        assert (await client.get(f"/api/projects/{foreign_id}")).status == 404
        assert (await client.get(f"/api/runs/{foreign_run_id}")).status == 404
        assert (await client.get(f"/api/runs/{foreign_run_id}/preview")).status == 404
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_sse_resumes_without_duplicates_heartbeats_and_closes_on_terminal(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="running",
                next_event_sequence=3,
                idempotency_key="sse-run",
            )
            database.add(run)
            await database.flush()
            database.add_all(
                [
                    GenerationEvent(
                        id=201, run_id=run.id, sequence=1, event_type="one", payload={}
                    ),
                    GenerationEvent(
                        id=202, run_id=run.id, sequence=2, event_type="two", payload={}
                    ),
                ]
            )
            run_id = run.id

        await client.post("/test/login/10")
        response = await asyncio.wait_for(
            client.get(
                f"/api/runs/{run_id}/events",
                headers={"Last-Event-ID": "1"},
            ),
            timeout=2,
        )
        event_two = await asyncio.wait_for(
            response.content.readuntil(b"\n\n"),
            timeout=2,
        )
        heartbeat = await asyncio.wait_for(
            response.content.readuntil(b"\n\n"),
            timeout=2,
        )
        assert b"id: 1\n" not in event_two
        assert event_two.count(b"id: 2\n") == 1
        assert heartbeat == b": heartbeat\n\n"

        async with factory() as database, database.begin():
            run = await database.get(GenerationRun, run_id)
            run.state = "completed"
            run.progress = 100
            run.next_event_sequence = 4
            database.add(
                GenerationEvent(
                    id=203,
                    run_id=run_id,
                    sequence=3,
                    event_type="run.completed",
                    public_message="Done",
                    payload={"status": "completed"},
                )
            )

        while True:
            terminal_frame = await asyncio.wait_for(
                response.content.readuntil(b"\n\n"),
                timeout=2,
            )
            if b"id: 3\n" in terminal_frame:
                break
            assert terminal_frame == b": heartbeat\n\n"
        assert await asyncio.wait_for(response.content.read(), timeout=2) == b""
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["X-Accel-Buffering"] == "no"
        assert terminal_frame.count(b"id: 3\n") == 1
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_legacy_event_is_fail_closed_for_get_and_sse(tmp_path) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="completed",
                progress=100,
                next_event_sequence=3,
                idempotency_key="legacy-event-projection",
            )
            database.add(run)
            await database.flush()
            database.add_all(
                [
                    GenerationEvent(
                        id=204,
                        run_id=run.id,
                        sequence=1,
                        event_type="stage.started",
                        public_message="Начато; token=private-message-value",
                        payload={
                            "status": "running",
                            "stage": "foundation",
                            "attempt": 2,
                            "max_executions": 6,
                            "diagnostic": "private-known-diagnostic",
                            "request": {"token": "private-known-request"},
                        },
                    ),
                    GenerationEvent(
                        id=205,
                        run_id=run.id,
                        sequence=2,
                        event_type="legacy.secret_event",
                        public_message="token=private-legacy-message",
                        payload={
                            "status": "running",
                            "diagnostic": "private-legacy-diagnostic",
                        },
                    ),
                ]
            )
            run_id = run.id

        await client.post("/test/login/10")
        snapshot_response = await client.get(f"/api/runs/{run_id}")
        sse_response = await client.get(f"/api/runs/{run_id}/events")

        assert snapshot_response.status == 200
        snapshot_events = (await snapshot_response.json())["events"]
        assert snapshot_events[0] == {
            "sequence": 1,
            "type": "stage.started",
            "message": "Начато; token=[REDACTED]",
            "payload": {
                "status": "running",
                "stage": "foundation",
                "attempt": 2,
                "max_executions": 6,
            },
            "created_at": snapshot_events[0]["created_at"],
        }
        assert snapshot_events[1] == {
            "sequence": 2,
            "type": "generation.unknown",
            "message": None,
            "payload": {},
            "created_at": snapshot_events[1]["created_at"],
        }

        assert sse_response.status == 200
        sse_body = await sse_response.text()
        assert "event: stage.started" in sse_body
        assert "event: generation.unknown" in sse_body
        assert "event: legacy.secret_event" not in sse_body
        if "private-" in sse_body:
            pytest.fail("private legacy event material leaked into SSE")
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_sse_drains_more_than_one_page_without_duplicates(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="completed",
                progress=100,
                next_event_sequence=106,
                idempotency_key="terminal-sse-backlog",
            )
            database.add(run)
            await database.flush()
            database.add_all(
                [
                    GenerationEvent(
                        id=10_000 + sequence,
                        run_id=run.id,
                        sequence=sequence,
                        event_type="run.progress",
                        public_message=f"Event {sequence}",
                        payload={"status": "completed"},
                    )
                    for sequence in range(1, 106)
                ]
            )
            run_id = run.id

        await client.post("/test/login/10")
        response = await client.get(f"/api/runs/{run_id}/events")
        body = await response.text()
        event_ids = [
            int(line.removeprefix("id: "))
            for line in body.splitlines()
            if line.startswith("id: ")
        ]

        assert response.status == 200
        assert event_ids == list(range(1, 106))
        assert len(event_ids) == len(set(event_ids))
    finally:
        await client.close()
        await engine.dispose()


def _artifact_payload(revision: int) -> dict:
    return artifact(revision=revision).to_dict()


@pytest.mark.asyncio
async def test_preview_ignores_newer_unaccepted_artifact(tmp_path) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="failed",
                idempotency_key="accepted-preview",
            )
            database.add(run)
            await database.flush()
            accepted = artifact(revision=1)
            rejected = artifact(revision=2)
            database.add_all(
                [
                    GenerationArtifact(
                        run_id=run.id,
                        revision=1,
                        stage=accepted.stage.value,
                        html=accepted.body_html,
                        css=accepted.css,
                        javascript=accepted.javascript,
                        config={"artifact": accepted.to_dict()},
                        quality_status="verified",
                    ),
                    GenerationArtifact(
                        run_id=run.id,
                        revision=2,
                        stage=rejected.stage.value,
                        html=rejected.body_html,
                        css=rejected.css,
                        javascript=rejected.javascript,
                        config={"artifact": rejected.to_dict()},
                        quality_status="needs_repair",
                    ),
                ]
            )
            await database.flush()
            run_id = run.id
            accepted_id = (
                await database.execute(
                    select(GenerationArtifact.id).where(
                        GenerationArtifact.run_id == run.id,
                        GenerationArtifact.revision == 1,
                    )
                )
            ).scalar_one()
        await client.post("/test/login/10")
        response = await client.get(f"/api/runs/{run_id}/preview")
        assert response.status == 200
        assert (await response.json())["artifact"]["revision"] == 1
        artifact_response = await client.get(f"/api/artifacts/{accepted_id}")
        assert artifact_response.status == 200
        assert (await artifact_response.json())["revision"] == 1
        await client.post("/test/login/11")
        assert (await client.get(f"/api/artifacts/{accepted_id}")).status == 404
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_skips_malformed_verified_artifacts_and_uses_valid_draft(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        async with factory() as database, database.begin():
            with_draft = GenerationRun(
                project_id=project_id,
                mode="express",
                state="failed",
                idempotency_key="malformed-verified-with-draft",
            )
            malformed_only = GenerationRun(
                project_id=project_id,
                mode="express",
                state="failed",
                idempotency_key="malformed-verified-only",
            )
            database.add_all([with_draft, malformed_only])
            await database.flush()
            database.add_all(
                [
                    GenerationArtifact(
                        run_id=with_draft.id,
                        revision=2,
                        stage="foundation",
                        html="<main>malformed</main>",
                        css="",
                        javascript="",
                        config={"artifact": {"invalid": True}},
                        quality_status="verified",
                    ),
                    GenerationEvent(
                        id=401,
                        run_id=with_draft.id,
                        sequence=1,
                        event_type="artifact.draft_staged",
                        public_message="Restorable draft",
                        payload={"artifact": _artifact_payload(1)},
                    ),
                    GenerationArtifact(
                        run_id=malformed_only.id,
                        revision=1,
                        stage="foundation",
                        html="<main>malformed</main>",
                        css="",
                        javascript="",
                        config={"artifact": {"invalid": True}},
                        quality_status="accepted",
                    ),
                ]
            )
            with_draft_id = with_draft.id
            malformed_only_id = malformed_only.id

        await client.post("/test/login/10")
        restored = await client.get(f"/api/runs/{with_draft_id}/preview")
        missing = await client.get(f"/api/runs/{malformed_only_id}/preview")

        assert restored.status == 200
        restored_payload = (await restored.json())["artifact"]
        assert restored_payload["revision"] == 1
        assert restored_payload["source"] == "restorable_draft"
        assert missing.status == 404
        assert (await missing.json()) == {"error": {"code": "preview_not_found"}}
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_document_serves_fixed_runtime_for_exact_durable_artifact(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    channel = "channel-1234567890abcdef"
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="completed",
                progress=100,
                idempotency_key="document-runtime",
            )
            database.add(run)
            await database.flush()
            candidate = artifact(
                revision=3,
                javascript=(
                    "window.__owner_preview_payload='must-not-run';"
                    "document.body.replaceChildren();"
                ),
            )
            database.add(
                GenerationArtifact(
                    run_id=run.id,
                    revision=3,
                    stage=candidate.stage.value,
                    html=candidate.body_html,
                    css=candidate.css,
                    javascript=candidate.javascript,
                    config={"artifact": candidate.to_dict()},
                    quality_status="accepted",
                )
            )
            run_id = run.id

        unauthenticated = await client.get(
            f"/api/runs/{run_id}/preview/document?revision=3&channel={channel}"
        )
        assert unauthenticated.status == 401

        await client.post("/test/login/10")
        response = await client.get(
            f"/api/runs/{run_id}/preview/document?revision=3&channel={channel}"
        )

        assert response.status == 200
        document = await response.text()
        assert 'data-region="launcher"' in document
        assert "type: 'chat.request'" in document
        assert "kaigo-builder-preview" in document
        assert "window.__owner_preview_payload" not in document
        assert "data-kaigo-generated" not in document
        assert response.headers["Content-Security-Policy"] == PREVIEW_CSP
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_document_hides_foreign_runs_and_rejects_invalid_or_missing_revision(
    tmp_path,
) -> None:
    engine, factory, client, project_id, foreign_id = await _project_app(tmp_path)
    channel = "channel-1234567890abcdef"
    try:
        async with factory() as database, database.begin():
            owned = GenerationRun(
                project_id=project_id,
                mode="express",
                state="failed",
                idempotency_key="owned-document",
            )
            foreign = GenerationRun(
                project_id=foreign_id,
                mode="express",
                state="completed",
                idempotency_key="foreign-document",
            )
            database.add_all([owned, foreign])
            await database.flush()
            candidate = artifact(revision=1)
            mismatched = artifact(revision=2)
            database.add_all(
                [
                    GenerationArtifact(
                        run_id=owned.id,
                        revision=1,
                        stage=mismatched.stage.value,
                        html=mismatched.body_html,
                        css=mismatched.css,
                        javascript=mismatched.javascript,
                        config={"artifact": mismatched.to_dict()},
                        quality_status="accepted",
                    ),
                    GenerationArtifact(
                        run_id=foreign.id,
                        revision=1,
                        stage=candidate.stage.value,
                        html=candidate.body_html,
                        css=candidate.css,
                        javascript=candidate.javascript,
                        config={"artifact": candidate.to_dict()},
                        quality_status="verified",
                    ),
                ]
            )
            owned_id, foreign_run_id = owned.id, foreign.id

        await client.post("/test/login/10")
        foreign_response = await client.get(
            f"/api/runs/{foreign_run_id}/preview/document?revision=1&channel={channel}"
        )
        invalid_revision = await client.get(
            f"/api/runs/{owned_id}/preview/document?revision=zero&channel={channel}"
        )
        invalid_channel = await client.get(
            f"/api/runs/{owned_id}/preview/document?revision=1&channel=bad"
        )
        duplicate_query = await client.get(
            f"/api/runs/{owned_id}/preview/document?revision=1&revision=2&channel={channel}"
        )
        extra_query = await client.get(
            f"/api/runs/{owned_id}/preview/document?revision=1&channel={channel}&extra=1"
        )
        not_ready = await client.get(
            f"/api/runs/{owned_id}/preview/document?revision=1&channel={channel}"
        )

        assert foreign_response.status == 404
        assert invalid_revision.status == 400
        assert invalid_channel.status == 400
        assert duplicate_query.status == 400
        assert extra_query.status == 400
        assert not_ready.status == 409
        assert (await not_ready.json()) == {"error": {"code": "preview_not_ready"}}
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_preview_document_can_render_an_exact_restorable_draft(tmp_path) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    channel = "channel-1234567890abcdef"
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="failed",
                idempotency_key="draft-document",
            )
            database.add(run)
            await database.flush()
            database.add_all(
                [
                    GenerationEvent(
                        id=501,
                        run_id=run.id,
                        sequence=1,
                        event_type="artifact.draft_staged",
                        public_message="Restorable exact draft",
                        payload={"artifact": _artifact_payload(4)},
                    ),
                    *[
                        GenerationEvent(
                            id=501 + sequence,
                            run_id=run.id,
                            sequence=sequence,
                            event_type="artifact.draft_staged",
                            public_message="Newer unrelated draft",
                            payload={"artifact": _artifact_payload(4 + sequence)},
                        )
                        for sequence in range(2, 53)
                    ],
                ]
            )
            run_id = run.id

        await client.post("/test/login/10")
        response = await client.get(
            f"/api/runs/{run_id}/preview/document?revision=4&channel={channel}"
        )
        missing = await client.get(
            f"/api/runs/{run_id}/preview/document?revision=9999&channel={channel}"
        )

        assert response.status == 200
        assert 'data-region="launcher"' in await response.text()
        assert missing.status == 409
        assert (await missing.json()) == {"error": {"code": "preview_not_ready"}}
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_chat_is_owner_scoped_csrf_gated_idempotent_and_audited(
    tmp_path,
) -> None:
    public_fact_marker = "OWNER-PUBLIC-FACT-4f97c1"

    class FakeProvider:
        capabilities = ProviderCapabilities()

        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []
            self.release = asyncio.Event()

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            assert model == "fake-chat-model"
            self.requests.append(request)
            await self.release.wait()
            return ModelResponse(
                text="Ответ из routed chat",
                usage=ModelUsage(input_tokens=9, output_tokens=6, thinking_tokens=1),
                request_id="provider-chat-1",
            )

        async def aclose(self) -> None:
            return None

    provider = FakeProvider()
    service = None

    def configure_chat(app, factory) -> None:
        nonlocal service
        router = ModelRouter(
            providers={"fake": provider},
            policies={
                ("chat_visitor", "express"): ModelPolicy(
                    prompt_version="chat-visitor-v1",
                    targets=(
                        ProviderTarget("fake", "fake-chat-model", 1_000_000, 2_000_000),
                    ),
                )
            },
            audit=SqlModelCallAudit(factory),
        )
        service = RoutedChatService(router=router)
        app[CHAT_SERVICE_KEY] = service

    engine, factory, client, project_id, foreign_id = await _project_app(
        tmp_path,
        configure_app=configure_chat,
    )
    assert service is not None
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="completed",
                progress=100,
                idempotency_key="chat-route",
            )
            foreign = GenerationRun(
                project_id=foreign_id,
                mode="express",
                state="completed",
                progress=100,
                idempotency_key="foreign-chat-route",
            )
            database.add_all([run, foreign])
            await database.flush()
            candidate = artifact(revision=2, art_direction="Trusted identity marker")
            database.add_all(
                [
                    GenerationEvent(
                        id=601,
                        run_id=run.id,
                        sequence=1,
                        event_type="run.created",
                        public_message="Generation queued",
                        payload={
                            "status": "queued",
                            "request": BuilderRequest(
                                engine=EngineName.DIRECT,
                                brief="Build a sales assistant",
                                reference_context=json.dumps(
                                    {"public_facts": [public_fact_marker]}
                                ),
                                source_url="https://example.com/",
                            ).to_dict(),
                        },
                    ),
                    GenerationArtifact(
                        run_id=run.id,
                        revision=2,
                        stage=candidate.stage.value,
                        html=candidate.body_html,
                        css=candidate.css,
                        javascript=candidate.javascript,
                        config={"artifact": candidate.to_dict()},
                        quality_status="accepted",
                    ),
                    GenerationArtifact(
                        run_id=foreign.id,
                        revision=2,
                        stage=candidate.stage.value,
                        html=candidate.body_html,
                        css=candidate.css,
                        javascript=candidate.javascript,
                        config={"artifact": candidate.to_dict()},
                        quality_status="verified",
                    ),
                ]
            )
            run_id, foreign_run_id = run.id, foreign.id

        payload = {
            "request_id": "request-route-123",
            "message": "Что доступно?",
            "revision": 2,
        }
        unauthenticated = await client.post(
            f"/api/runs/{run_id}/chat",
            json=payload,
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert unauthenticated.status == 401

        await client.post("/test/login/10")
        without_csrf = await client.post(f"/api/runs/{run_id}/chat", json=payload)
        assert without_csrf.status == 403

        first_request = asyncio.create_task(
            client.post(
                f"/api/runs/{run_id}/chat",
                json=payload,
                headers={"X-CSRF-Token": "test-csrf"},
            )
        )
        duplicate_request = asyncio.create_task(
            client.post(
                f"/api/runs/{run_id}/chat",
                json=payload,
                headers={"X-CSRF-Token": "test-csrf"},
            )
        )
        await asyncio.wait_for(
            _wait_until(lambda: len(provider.requests) >= 1),
            timeout=2,
        )
        await asyncio.sleep(0.05)
        provider.release.set()
        first, duplicate = await asyncio.gather(first_request, duplicate_request)
        assert first.status == duplicate.status == 200
        assert (
            await first.json()
            == await duplicate.json()
            == {
                "request_id": "request-route-123",
                "reply": "Ответ из routed chat",
            }
        )
        assert len(provider.requests) == 1
        assert "Build a sales assistant" in provider.requests[0].prompt
        assert "Trusted identity marker" in provider.requests[0].prompt
        assert public_fact_marker in provider.requests[0].prompt
        async with factory() as database:
            call = (await database.execute(select(ModelCall))).scalar_one()
        assert call.role == "chat_visitor"
        assert call.run_id == run_id
        assert (call.input_tokens, call.output_tokens, call.thinking_tokens) == (
            9,
            6,
            1,
        )
        assert call.cost_microusd == 21

        await client.post("/test/login/11")
        foreign_to_owner = await client.post(
            f"/api/runs/{run_id}/chat",
            json=payload,
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert foreign_to_owner.status == 404
        owner_to_foreign = await client.post(
            f"/api/runs/{foreign_run_id}/chat",
            json=payload,
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert owner_to_foreign.status == 200
        assert len(provider.requests) == 2
        assert public_fact_marker not in provider.requests[1].prompt
    finally:
        await service.close()
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_chat_validates_payload_artifact_and_service_availability(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)
    try:
        async with factory() as database, database.begin():
            run = GenerationRun(
                project_id=project_id,
                mode="express",
                state="completed",
                idempotency_key="chat-not-ready",
            )
            database.add(run)
            await database.flush()
            run_id = run.id
        await client.post("/test/login/10")
        invalid = await client.post(
            f"/api/runs/{run_id}/chat",
            json={"request_id": "bad", "message": "", "revision": 0},
            headers={"X-CSRF-Token": "test-csrf"},
        )
        extra_field = await client.post(
            f"/api/runs/{run_id}/chat",
            json={
                "request_id": "request-route-123",
                "message": "Question",
                "revision": 1,
                "extra": True,
            },
            headers={"X-CSRF-Token": "test-csrf"},
        )
        oversized_raw_message = await client.post(
            f"/api/runs/{run_id}/chat",
            json={
                "request_id": "request-route-124",
                "message": "x" + (" " * 1_000),
                "revision": 1,
            },
            headers={"X-CSRF-Token": "test-csrf"},
        )
        not_ready = await client.post(
            f"/api/runs/{run_id}/chat",
            json={
                "request_id": "request-route-123",
                "message": "Вопрос",
                "revision": 1,
            },
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert invalid.status == 400
        assert extra_field.status == 400
        assert oversized_raw_message.status == 400
        assert not_ready.status == 409
        assert (await not_ready.json())["error"]["code"] == "chat_not_ready"

        candidate = artifact(revision=1)
        async with factory() as database, database.begin():
            database.add(
                GenerationArtifact(
                    run_id=run_id,
                    revision=1,
                    stage=candidate.stage.value,
                    html=candidate.body_html,
                    css=candidate.css,
                    javascript=candidate.javascript,
                    config={"artifact": candidate.to_dict()},
                    quality_status="accepted",
                )
            )
        unavailable = await client.post(
            f"/api/runs/{run_id}/chat",
            json={
                "request_id": "request-route-123",
                "message": "Вопрос",
                "revision": 1,
            },
            headers={"X-CSRF-Token": "test-csrf"},
        )
        assert unavailable.status == 503
        assert (await unavailable.json())["error"] == {
            "code": "chat_not_configured",
            "message": "Чат временно не настроен",
            "retryable": True,
            "request_id": "request-route-123",
        }
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_concurrent_idempotent_start_creates_one_reserved_run() -> None:
    engine = create_async_engine(POSTGRES_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Alpha", slug="alpha"))
            database.add(User(id=10, tenant_id=1, email="owner@example.com"))
            await database.flush()
            database.add(
                UserIdentity(
                    user_id=10,
                    provider="google",
                    provider_subject="postgres-owner",
                    email="owner@example.com",
                    email_verified=True,
                )
            )
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
                brief="Atomic run",
            )
            database.add(project)
            await database.flush()
            project_id = project.id

        app = web.Application()
        app[SESSION_FACTORY_KEY] = factory
        setup_session(app, SimpleCookieStorage(cookie_name="kaigo_pg_test"))

        async def login(request: web.Request) -> web.Response:
            session = await get_session(request)
            session.update({"user_id": 10, "tenant_id": 1, "csrf_token": "pg-csrf"})
            return web.json_response({})

        app.router.add_post("/test/login", login)
        setup_project_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await client.post("/test/login")
            responses = await asyncio.gather(
                *[
                    client.post(
                        f"/api/projects/{project_id}/runs",
                        json={"mode": "express"},
                        headers={
                            "Idempotency-Key": "same-request",
                            "X-CSRF-Token": "pg-csrf",
                        },
                    )
                    for _ in range(2)
                ]
            )
            payloads = [await response.json() for response in responses]
            assert [response.status for response in responses] == [202, 202]
            assert len({payload["id"] for payload in payloads}) == 1
            async with factory() as database:
                assert (
                    await database.scalar(
                        select(func.count()).select_from(GenerationRun)
                    )
                    == 1
                )
                assert (
                    await database.scalar(
                        select(func.count())
                        .select_from(UsageLedger)
                        .where(UsageLedger.entry_type == "trial.reserve")
                    )
                    == 2
                )
        finally:
            await client.close()
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
