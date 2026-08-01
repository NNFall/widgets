from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.funnel_analytics import setup_operator_funnel_routes
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import FunnelEvent, FunnelJourney, UserIdentity
from builder_lab.forensics.config import GenerationForensicsConfig


@pytest.mark.asyncio
async def test_operator_funnel_report_is_allowlisted_aggregate_only_and_no_store(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'operator-funnel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Ops", slug="ops"))
        database.add_all(
            [
                User(id=10, tenant_id=1, email="operator@example.com"),
                User(id=11, tenant_id=1, email="outsider@example.com"),
            ]
        )
        await database.flush()
        database.add_all(
            [
                UserIdentity(
                    user_id=10,
                    provider="yandex",
                    provider_subject="operator",
                    email="operator@example.com",
                    email_verified=True,
                    profile={},
                ),
                UserIdentity(
                    user_id=11,
                    provider="yandex",
                    provider_subject="outsider",
                    email="outsider@example.com",
                    email_verified=True,
                    profile={},
                ),
            ]
        )
        journey = FunnelJourney(
            campaign_source="telegram",
            started_at=datetime(2026, 7, 15, tzinfo=UTC),
        )
        database.add(journey)
        await database.flush()
        database.add_all(
            [
                FunnelEvent(
                    event_key="landing:private-event-key",
                    event_type="landing_entered",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="authenticated:private-event-key",
                    event_type="authenticated_project",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="free-result:private-event-key",
                    event_type="free_result",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
                ),
            ]
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
    setup_session(app, SimpleCookieStorage(cookie_name="operator-funnel-test"))

    async def login(request: web.Request) -> web.Response:
        session = await get_session(request)
        session["user_id"] = int(request.match_info["user_id"])
        return web.json_response({"ok": True})

    app.router.add_post("/test/login/{user_id}", login)
    setup_operator_funnel_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        assert (await client.get("/api/operator/funnel")).status == 401
        await client.post("/test/login/11")
        assert (await client.get("/api/operator/funnel")).status == 403
        await client.post("/test/login/10")

        query = "?from=2026-07-01&to=2026-08-01&source=telegram"
        response = await client.get(f"/api/operator/funnel{query}")
        page = await client.get(f"/operator/funnel{query}")

        assert response.status == page.status == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in page.headers["Content-Security-Policy"]
        payload = await response.json()
        assert [stage["event_type"] for stage in payload["stages"]] == [
            "landing_entered",
            "authenticated_project",
            "run_queued",
            "free_result",
            "upgrade_started",
            "payment_completed",
            "published",
        ]
        assert payload["stages"][0]["journeys"] == 1
        assert payload["stages"][1]["journeys"] == 1
        assert payload["stages"][3]["journeys"] == 1
        assert payload["filters"] == {"source": "telegram"}
        text = f"{payload} {await page.text()}"
        assert "private-event-key" not in text
        assert str(journey.id) not in text
        assert "operator@example.com" not in text

        invalid = await client.get(
            "/api/operator/funnel?from=2026-07-01&to=2026-08-01"
            "&source=person@example.com"
        )
        assert invalid.status == 400
    finally:
        await client.close()
        await engine.dispose()
