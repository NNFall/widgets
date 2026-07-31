from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.auth import SESSION_EMAIL_KEY, SESSION_TENANT_KEY
from app.admin.funnel import setup_funnel_admin_routes
from app.admin.routes import setup_admin_routes
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import FunnelEvent, FunnelJourney, UserIdentity
from builder_lab.forensics.config import GenerationForensicsConfig


def test_funnel_admin_routes_are_absent_during_default_off_rollout() -> None:
    app = web.Application()
    app["config"] = SimpleNamespace(funnel_journeys_enabled=False)
    setup_admin_routes(app)
    resources = {resource.canonical for resource in app.router.resources()}
    assert "/admin/funnel" not in resources
    assert "/admin/api/funnel" not in resources


@pytest.mark.asyncio
async def test_funnel_admin_is_global_aggregate_only_and_validates_window(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'funnel-admin.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Ops", slug="ops"))
        database.add_all(
            [
                User(id=10, tenant_id=1, email="operator@example.com"),
                User(id=11, tenant_id=1, email="tenant-admin@example.com"),
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
                    provider_subject="tenant-admin",
                    email="tenant-admin@example.com",
                    email_verified=True,
                    profile={},
                ),
            ]
        )
        journey = FunnelJourney(
            campaign_source="telegram",
            campaign_medium="social",
            campaign_name="launch",
            started_at=now - timedelta(days=20),
        )
        database.add(journey)
        await database.flush()
        database.add_all(
            [
                FunnelEvent(
                    event_key="landing:private-owner@example.com:https://private.invalid",
                    event_type="landing_entered",
                    journey_id=journey.id,
                    occurred_at=now - timedelta(days=20),
                ),
                FunnelEvent(
                    event_key="free-result:203.0.113.42:private-provider-reference",
                    event_type="free_result",
                    journey_id=journey.id,
                    occurred_at=now - timedelta(days=19),
                ),
            ]
        )

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(
        funnel_retention_days=90,
        generation_forensics=GenerationForensicsConfig(
            enabled=True,
            root=tmp_path / "forensics",
            ttl_hours=120,
            max_bytes=1_000_000,
            admin_emails=("operator@example.com",),
        ),
    )
    setup_session(app, SimpleCookieStorage(cookie_name="funnel-admin-test"))

    async def login(request: web.Request) -> web.Response:
        session = await get_session(request)
        mode = request.match_info["mode"]
        if mode == "tenant":
            session[SESSION_EMAIL_KEY] = "tenant-admin@example.com"
            session[SESSION_TENANT_KEY] = "ops"
        elif mode == "outsider":
            session["user_id"] = 11
        elif mode == "operator":
            session["user_id"] = 10
        return web.json_response({"ok": True})

    app.router.add_get("/admin/login", lambda _request: web.Response(text="login"))
    app.router.add_post("/test/login/{mode}", login)
    setup_funnel_admin_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        for path in ("/admin/api/funnel", "/admin/funnel"):
            response = await client.get(path, allow_redirects=False)
            assert response.status == 302
            assert response.headers["Location"] == "/admin/login"

        await client.post("/test/login/tenant")
        response = await client.get("/admin/api/funnel", allow_redirects=False)
        assert response.status == 302
        assert response.headers["Location"] == "/admin/login"

        await client.post("/test/login/outsider")
        assert (await client.get("/admin/api/funnel")).status == 403

        await client.post("/test/login/operator")
        for invalid_days in ("1", "nope", "0", "-1", "91"):
            assert (
                await client.get(f"/admin/api/funnel?days={invalid_days}")
            ).status == 400
        for valid_days in ("7", "30", "90"):
            assert (
                await client.get(f"/admin/api/funnel?days={valid_days}")
            ).status == 200

        api = await client.get("/admin/api/funnel")
        page = await client.get("/admin/funnel")
        assert api.status == page.status == 200
        assert api.headers["Cache-Control"] == "no-store"
        payload = await api.json()
        assert set(payload) == {"window", "core", "commercial", "campaigns"}
        assert payload["window"]["days"] == 30
        assert set(payload["window"]) == {
            "from",
            "to",
            "days",
            "retention_days",
        }
        assert payload["core"][0] == {
            "stage": "landing_entered",
            "journeys": 1,
            "from_entry_bps": 10_000,
        }
        assert payload["campaigns"][0]["source"] == "telegram"
        assert set(payload["campaigns"][0]) == {
            "source",
            "medium",
            "campaign",
            "term",
            "content",
            "landing_entered",
            "authenticated_project",
            "run_queued",
            "free_result",
            "published",
            "upgrade_started",
            "payment_completed",
        }

        page_text = await page.text()
        assert "Основная воронка" in page_text
        assert "Коммерческие этапы" in page_text
        assert "Кампании" in page_text
        assert "UTC" in page_text
        assert "90 дней" in page_text
        empty_page = await client.get("/admin/funnel?days=7")
        assert "За выбранный период данных нет" in await empty_page.text()

        disclosed = f"{payload} {page_text}"
        for secret in (
            str(journey.id),
            "private-owner@example.com",
            "https://private.invalid",
            "203.0.113.42",
            "private-provider-reference",
        ):
            assert secret not in disclosed
        assert "href=\"/admin/funnel/" not in page_text
        assert "/admin/api/funnel?journey" not in page_text
    finally:
        await client.close()
        await engine.dispose()
