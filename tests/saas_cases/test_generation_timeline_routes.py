from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.projects.routes import setup_project_routes
from app.saas.models import (
    GenerationEvent,
    GenerationRun,
    GenerationStageAttempt,
    ModelCall,
    Project,
)
from builder_lab.generation_events import GenerationEventType


pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web_app.NotAppKeyWarning")


OWNER_RUN_ID = UUID("61000000-0000-0000-0000-000000000001")
FOREIGN_RUN_ID = UUID("61000000-0000-0000-0000-000000000002")
CREATED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


async def _timeline_app(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add_all(
            [
                User(id=10, tenant_id=1, email="owner@example.com"),
                User(id=11, tenant_id=1, email="other@example.com"),
            ]
        )
        owner_project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://private-owner.example",
        )
        foreign_project = Project(
            tenant_id=1,
            owner_user_id=11,
            source_url="https://private-foreign.example",
        )
        database.add_all([owner_project, foreign_project])
        await database.flush()
        owner_run = GenerationRun(
            id=OWNER_RUN_ID,
            project_id=owner_project.id,
            mode="express",
            state="running",
            idempotency_key="private-owner-key",
            created_at=CREATED_AT,
            started_at=CREATED_AT + timedelta(minutes=1),
        )
        foreign_run = GenerationRun(
            id=FOREIGN_RUN_ID,
            project_id=foreign_project.id,
            mode="express",
            state="queued",
            idempotency_key="private-foreign-key",
            created_at=CREATED_AT,
        )
        database.add_all([owner_run, foreign_run])
        await database.flush()
        database.add_all(
            [
                GenerationEvent(
                    id=11,
                    run_id=OWNER_RUN_ID,
                    sequence=1,
                    event_type="run.created",
                    public_message="private-route-message",
                    payload={"request_id": "private-route-request"},
                    forensic_ref="C:/private/route-forensic.jsonl",
                ),
                GenerationEvent(
                    id=12,
                    run_id=OWNER_RUN_ID,
                    sequence=2,
                    event_type="future.private_event",
                    payload={"provider": "private-route-provider"},
                ),
                GenerationStageAttempt(
                    id=UUID("62000000-0000-0000-0000-000000000001"),
                    run_id=OWNER_RUN_ID,
                    stage="reference_analysis",
                    ordinal=1,
                    status="running",
                    started_at=CREATED_AT + timedelta(minutes=1),
                ),
                ModelCall(
                    id=UUID("63000000-0000-0000-0000-000000000001"),
                    run_id=OWNER_RUN_ID,
                    logical_invocation_id=UUID(
                        "64000000-0000-0000-0000-000000000001"
                    ),
                    operation="private-route-operation",
                    semantic_attempt=1,
                    fallback_index=1,
                    provider="private-route-provider",
                    model="private-route-model",
                    role="private-route-role",
                    mode="direct",
                    prompt_version="private-route-prompt",
                    request_id="private-route-request-id",
                    attempt=1,
                    provider_dispatched=True,
                    input_tokens=10,
                    output_tokens=5,
                    thinking_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    latency_ms=25,
                    status="completed",
                    cost_state="reported",
                    cost_microusd=125_000,
                    pricing_snapshot={"secret": "private-route-pricing"},
                ),
            ]
        )

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    setup_session(app, SimpleCookieStorage(cookie_name="kaigo_timeline_test"))

    async def login(request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        session = await get_session(request)
        session["user_id"] = user_id
        session["tenant_id"] = 1
        return web.json_response({"ok": True})

    app.router.add_post("/test/login/{user_id}", login)
    setup_project_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, client


@pytest.mark.asyncio
async def test_timeline_summary_route_is_owner_scoped_and_aggregate_only(
    tmp_path,
) -> None:
    engine, client = await _timeline_app(tmp_path)
    try:
        unauthenticated = await client.get(
            f"/api/runs/{OWNER_RUN_ID}/timeline-summary"
        )
        assert unauthenticated.status == 401

        await client.post("/test/login/10")
        invalid = await client.get("/api/runs/not-a-uuid/timeline-summary")
        foreign = await client.get(
            f"/api/runs/{FOREIGN_RUN_ID}/timeline-summary"
        )
        owned = await client.get(f"/api/runs/{OWNER_RUN_ID}/timeline-summary")

        assert invalid.status == 404
        assert foreign.status == 404
        assert owned.status == 200

        payload = await owned.json()
        expected_events = {
            event_type.value: 0 for event_type in GenerationEventType
        }
        expected_events.update({"run.created": 1, "unknown": 1})
        assert payload == {
            "run": {
                "id": str(OWNER_RUN_ID),
                "state": "running",
                "created_at": "2026-07-30T12:00:00Z",
                "started_at": "2026-07-30T12:01:00Z",
                "finished_at": None,
            },
            "event_counts": expected_events,
            "stage_attempts": [
                {
                    "stage": "reference_analysis",
                    "count": 1,
                    "status_counts": {
                        "running": 1,
                        "result_staged": 0,
                        "completed": 0,
                        "failed": 0,
                        "interrupted": 0,
                        "cancelled": 0,
                        "accounting_failed": 0,
                        "unknown": 0,
                    },
                    "min_ordinal": 1,
                    "max_ordinal": 1,
                    "started_at_min": "2026-07-30T12:01:00Z",
                    "finished_at_max": None,
                }
            ],
            "model_usage": {
                "call_count": 1,
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "latency_ms_total": 25,
                "cost": {
                    "reported_usd": 0.125,
                    "estimated_usd": 0.0,
                    "unknown_calls": 0,
                    "not_billed_calls": 0,
                    "known_total_usd": 0.125,
                    "cost_complete": True,
                },
            },
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        for forbidden in (
            "private-route-message",
            "private-route-request",
            "private-route-provider",
            "private-route-model",
            "private-route-role",
            "private-route-prompt",
            "private-route-pricing",
            "route-forensic",
        ):
            assert forbidden not in serialized

        await client.post("/test/login/11")
        cross_owner = await client.get(
            f"/api/runs/{OWNER_RUN_ID}/timeline-summary"
        )
        assert cross_owner.status == 404
    finally:
        await client.close()
        await engine.dispose()
