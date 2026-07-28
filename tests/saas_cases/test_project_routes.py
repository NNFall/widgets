from __future__ import annotations

import asyncio
import os
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.projects.routes import setup_project_routes
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    Project,
    TrialEntitlement,
    UsageLedger,
    UserIdentity,
)
from tests.builder_lab_cases.test_validation import artifact

POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


async def _project_app(tmp_path):
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
    setup_project_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, factory, client, owner_id, foreign_id


@pytest.mark.asyncio
async def test_create_run_is_202_idempotent_and_never_calls_engine_inline(tmp_path) -> None:
    engine, factory, client, project_id, _ = await _project_app(tmp_path)

    class InlineEngineMustNotRun:
        async def run(self, *_args, **_kwargs):
            raise AssertionError("HTTP project route must never execute the engine inline")

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
        assert first_payload["state"] == "queued"
        assert second_payload["id"] == first_payload["id"]

        async with factory() as database:
            assert await database.scalar(select(func.count()).select_from(GenerationRun)) == 1
            run = (await database.execute(select(GenerationRun))).scalar_one()
            event = (await database.execute(select(GenerationEvent))).scalar_one()
            project = await database.get(Project, project_id)
            entitlement = (
                await database.execute(
                    select(TrialEntitlement).where(TrialEntitlement.user_id == 10)
                )
            ).scalar_one()
            assert event.event_type == "run.created"
            assert event.run_id == run.id
            assert project.active_run_id == run.id
            assert entitlement.state == "reserved"
            assert await database.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.entry_type == "trial.reserve")
            ) == 2
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_run_and_preview_reads_are_owner_scoped_and_restore_state(tmp_path) -> None:
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
        assert [item["id"] for item in (await listed.json())["projects"]] == [str(project_id)]

        detail = await client.get(f"/api/projects/{project_id}")
        assert detail.status == 200
        assert (await detail.json())["active_run"]["id"] == str(run_id)

        snapshot = await client.get(f"/api/runs/{run_id}")
        payload = await snapshot.json()
        assert snapshot.status == 200
        assert [event["sequence"] for event in payload["events"]] == [1, 2]
        assert payload["events"][1]["payload"] == {
            "status": "needs_repair",
            "revision": 1,
        }
        assert payload["preview"]["revision"] == 1
        assert payload["preview"]["source"] == "restorable_draft"

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
async def test_sse_resumes_without_duplicates_heartbeats_and_closes_on_terminal(tmp_path) -> None:
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
                    GenerationEvent(id=201, run_id=run.id, sequence=1, event_type="one", payload={}),
                    GenerationEvent(id=202, run_id=run.id, sequence=2, event_type="two", payload={}),
                ]
            )
            run_id = run.id

        await client.post("/test/login/10")
        response_task = asyncio.create_task(
            client.get(
                f"/api/runs/{run_id}/events",
                headers={"Last-Event-ID": "1"},
            )
        )
        await asyncio.sleep(0.04)
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

        response = await asyncio.wait_for(response_task, timeout=2)
        body = await response.text()
        assert response.status == 200
        assert response.headers["Cache-Control"] == "no-cache"
        assert response.headers["X-Accel-Buffering"] == "no"
        assert "id: 1\n" not in body
        assert body.count("id: 2\n") == 1
        assert body.count("id: 3\n") == 1
        assert ": heartbeat\n\n" in body
        assert not body.split("id: 3\n", 1)[1].endswith(": heartbeat\n\n")
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_sse_drains_more_than_one_page_without_duplicates(tmp_path) -> None:
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
            database.add_all([
                GenerationEvent(
                    id=10_000 + sequence,
                    run_id=run.id,
                    sequence=sequence,
                    event_type="run.progress",
                    public_message=f"Event {sequence}",
                    payload={"status": "completed"},
                )
                for sequence in range(1, 106)
            ])
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
            database.add_all([
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
            ])
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
@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured")
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
            database.add(UserIdentity(
                user_id=10,
                provider="google",
                provider_subject="postgres-owner",
                email="owner@example.com",
                email_verified=True,
            ))
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
            responses = await asyncio.gather(*[
                client.post(
                    f"/api/projects/{project_id}/runs",
                    json={"mode": "express"},
                    headers={"Idempotency-Key": "same-request", "X-CSRF-Token": "pg-csrf"},
                )
                for _ in range(2)
            ])
            payloads = [await response.json() for response in responses]
            assert [response.status for response in responses] == [202, 202]
            assert len({payload["id"] for payload in payloads}) == 1
            async with factory() as database:
                assert await database.scalar(select(func.count()).select_from(GenerationRun)) == 1
                assert await database.scalar(
                    select(func.count()).select_from(UsageLedger).where(
                        UsageLedger.entry_type == "trial.reserve"
                    )
                ) == 2
        finally:
            await client.close()
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
