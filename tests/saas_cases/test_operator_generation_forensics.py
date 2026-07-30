from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.generation_forensics import setup_operator_forensics_routes
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import GenerationEvent, GenerationRun, Project, UserIdentity
from builder_lab.forensics.config import GenerationForensicsConfig


RUN_ID = UUID("71000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
async def test_operator_timeline_requires_verified_oauth_allowlist_and_leaks_no_payload(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'operator.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Ops", slug="ops"))
        database.add_all(
            [
                User(id=10, tenant_id=1, email="operator@example.com"),
                User(id=11, tenant_id=1, email="outsider@example.com"),
                User(id=12, tenant_id=1, email="unverified@example.com"),
            ]
        )
    async with factory() as database, database.begin():
        database.add_all(
            [
                UserIdentity(
                    user_id=10,
                    provider="yandex",
                    provider_subject="operator-subject",
                    email="Operator@Example.com",
                    email_verified=True,
                    profile={},
                ),
                UserIdentity(
                    user_id=11,
                    provider="yandex",
                    provider_subject="outsider-subject",
                    email="outsider@example.com",
                    email_verified=True,
                    profile={},
                ),
                UserIdentity(
                    user_id=12,
                    provider="yandex",
                    provider_subject="unverified-subject",
                    email="operator@example.com",
                    email_verified=False,
                    profile={},
                ),
            ]
        )
        project = Project(
            tenant_id=1,
            owner_user_id=11,
            source_url="https://private-customer.example/secret",
        )
        database.add(project)
        await database.flush()
        database.add(
            GenerationRun(
                id=RUN_ID,
                project_id=project.id,
                mode="express",
                state="failed",
                idempotency_key="private-idempotency-key",
                error_message="private-provider-error",
                created_at=datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc),
                finished_at=datetime(2026, 7, 30, 12, 5, tzinfo=timezone.utc),
            )
        )
        await database.flush()
        database.add(
            GenerationEvent(
                id=1,
                run_id=RUN_ID,
                sequence=1,
                event_type="run.failed",
                public_message="Безопасное публичное сообщение",
                payload={"prompt": "private-prompt", "api_key": "private-key"},
                forensic_ref="C:/private/evidence.jsonl",
            )
        )

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(
        generation_forensics=GenerationForensicsConfig(
            enabled=True,
            root=tmp_path / "forensics",
            ttl_hours=120,
            max_bytes=1_000_000,
            admin_emails=("operator@example.com",),
        )
    )
    setup_session(app, SimpleCookieStorage(cookie_name="operator-test"))

    async def login(request: web.Request) -> web.Response:
        session = await get_session(request)
        session["user_id"] = int(request.match_info["user_id"])
        session["tenant_id"] = 1
        return web.json_response({"ok": True})

    app.router.add_post("/test/login/{user_id}", login)
    setup_operator_forensics_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        anonymous = await client.get("/api/operator/generation-runs")
        assert anonymous.status == 401

        await client.post("/test/login/11")
        outsider = await client.get("/api/operator/generation-runs")
        assert outsider.status == 403

        await client.post("/test/login/12")
        unverified = await client.get("/api/operator/generation-runs")
        assert unverified.status == 403

        await client.post("/test/login/10")
        listing = await client.get("/api/operator/generation-runs")
        detail = await client.get(f"/api/operator/generation-runs/{RUN_ID}")
        page = await client.get(f"/operator/generation-runs/{RUN_ID}")

        assert listing.status == detail.status == page.status == 200
        assert listing.headers["Cache-Control"] == "no-store"
        assert detail.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in page.headers["Content-Security-Policy"]
        assert await listing.json() == {
            "runs": [
                {
                    "id": str(RUN_ID),
                    "project_id": str(project.id),
                    "owner_user_id": 11,
                    "state": "failed",
                    "created_at": "2026-07-30T12:00:00Z",
                    "started_at": None,
                    "finished_at": "2026-07-30T12:05:00Z",
                }
            ]
        }
        detail_text = await detail.text()
        page_text = await page.text()
        assert str(RUN_ID) in detail_text and str(RUN_ID) in page_text
        for private_value in (
            "private-customer",
            "private-idempotency",
            "private-provider-error",
            "private-prompt",
            "private-key",
            "evidence.jsonl",
            "Безопасное публичное сообщение",
        ):
            assert private_value not in detail_text
            assert private_value not in page_text
    finally:
        await client.close()
        await engine.dispose()
