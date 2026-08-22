from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    caplog,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'operator-funnel.db'}"
    )
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
                    event_key="scroll-end:private-event-key",
                    event_type="landing_scrolled_end",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="studio-click:private-event-key",
                    event_type="studio_cta_clicked",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="studio-entered:private-event-key",
                    event_type="studio_entered",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="auth-started:private-event-key",
                    event_type="auth_started",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="auth-completed:private-event-key",
                    event_type="auth_completed",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="composer:private-event-key",
                    event_type="composer_submitted",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="run-queued:private-event-key",
                    event_type="run_queued",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 15, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="free-result:private-event-key",
                    event_type="free_result",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="founder-claimed:private-event-key",
                    event_type="founder_claimed",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="upgrade-started:private-event-key",
                    event_type="upgrade_started",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="payment-completed:private-event-key",
                    event_type="payment_completed",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 7, 16, tzinfo=UTC),
                ),
                FunnelEvent(
                    event_key="future-published:private-event-key",
                    event_type="published",
                    journey_id=journey.id,
                    occurred_at=datetime(2026, 8, 1, tzinfo=UTC) + timedelta(seconds=1),
                ),
            ]
        )

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(
        operator_read_token="t" * 32,
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
        with caplog.at_level("INFO"):
            service_response = await client.get(
                "/api/operator/funnel?from=2026-07-01&to=2026-08-01&source=telegram",
                headers={"Authorization": "Bearer " + "t" * 32},
            )
        assert service_response.status == 200
        assert service_response.headers["Cache-Control"] == "no-store"
        assert "t" * 32 not in caplog.text
        service_payload = await service_response.json()
        assert set(service_payload) == {
            "period",
            "filters",
            "stages",
            "engagement",
            "sources",
        }
        service_page = await client.get(
            "/operator/funnel?from=2026-07-01&to=2026-08-01&source=telegram",
            headers={"Authorization": "Bearer " + "t" * 32},
        )
        assert service_page.status == 401

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
            "published",
        ]
        assert payload["stages"][0]["journeys"] == 1
        assert payload["stages"][1]["journeys"] == 1
        assert payload["stages"][2]["journeys"] == 1
        assert payload["stages"][-1]["journeys"] == 0
        assert [row["event_type"] for row in payload["engagement"]] == [
            "landing_scrolled_end",
            "studio_cta_clicked",
            "studio_entered",
            "composer_submitted",
            "auth_started",
            "auth_completed",
            "founder_claimed",
            "upgrade_started",
            "payment_completed",
        ]
        assert all(row["journeys"] == 1 for row in payload["engagement"])
        assert all(row["from_entry_percent"] == 100.0 for row in payload["engagement"])
        assert payload["filters"] == {"source": "telegram"}
        page_text = await page.text()
        assert "Ключевые действия" in page_text
        assert "Долистали до финального предложения" in page_text
        assert "Перешли в Studio" in page_text
        assert "Активировали Founder Pilot" in page_text
        text = f"{payload} {page_text}"
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
