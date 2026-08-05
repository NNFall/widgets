from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.auth import SESSION_EMAIL_KEY, SESSION_TENANT_KEY
from app.admin.routes import setup_admin_routes
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import PatternReview, WidgetPatternVersion
from core.config import settings


async def _client(tmp_path, monkeypatch, *, email: str | None = None):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pattern-lab.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Admin", slug="admin"))
        database.add(User(id=1, tenant_id=1, email="operator@example.com"))

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(funnel_journeys_enabled=False)
    setup_session(app, SimpleCookieStorage(cookie_name="pattern-lab-test"))

    async def login(request: web.Request) -> web.Response:
        session = await get_session(request)
        raw = request.match_info["email"]
        session[SESSION_EMAIL_KEY] = raw
        session[SESSION_TENANT_KEY] = "admin"
        return web.json_response({"ok": True})

    app.router.add_post("/test/login/{email}", login)
    monkeypatch.setattr(settings, "ADMIN_EMAILS", {"operator@example.com"})
    setup_admin_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    if email is not None:
        await client.post(f"/test/login/{email}")
    return client, factory, engine


@pytest.mark.asyncio
async def test_pattern_lab_requires_session_and_configured_allowlist(tmp_path, monkeypatch):
    client, factory, engine = await _client(tmp_path, monkeypatch)
    try:
        response = await client.get("/admin/pattern-lab", allow_redirects=False)
        assert response.status == 302
        assert response.headers["Location"] == "/admin/login"
        await client.post("/test/login/member@example.com")
        response = await client.get("/admin/pattern-lab", allow_redirects=False)
        assert response.status == 403
        monkeypatch.setattr(settings, "ADMIN_EMAILS", set())
        response = await client.get("/admin/pattern-lab", allow_redirects=False)
        assert response.status == 403
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_pattern_lab_list_syncs_registry_and_escapes_filters_and_description(
    tmp_path, monkeypatch
):
    client, factory, engine = await _client(tmp_path, monkeypatch, email="operator@example.com")
    try:
        response = await client.get("/admin/pattern-lab")
        assert response.status == 200
        html = await response.text()
        assert "AI description" in html
        async with factory() as database:
            rows = (await database.scalars(select(WidgetPatternVersion))).all()
            assert rows
        for query in (
            "category=not-a-category",
            "status=not-a-status",
            "review=not-a-review",
        ):
            invalid = await client.get(f"/admin/pattern-lab?{query}")
            assert invalid.status == 400
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_pattern_lab_preview_is_opaque_sandbox_and_fixed_bridge(
    tmp_path, monkeypatch
):
    client, factory, engine = await _client(tmp_path, monkeypatch, email="operator@example.com")
    try:
        await client.get("/admin/pattern-lab")
        async with factory() as database:
            row = await database.scalar(select(WidgetPatternVersion))
        assert row is not None
        detail = await client.get(f"/admin/pattern-lab/{row.pattern_id}/{row.version}")
        detail_html = await detail.text()
        assert 'sandbox="allow-scripts"' in detail_html
        assert "allow-same-origin" not in detail_html
        preview = await client.get(
            f"/admin/pattern-lab/{row.pattern_id}/{row.version}/preview"
        )
        assert preview.status == 200
        assert "default-src 'none'" in preview.headers["Content-Security-Policy"]
        preview_html = await preview.text()
        assert "event.source !== window.parent" in preview_html
        for command in (
            "run",
            "replay",
            "open",
            "close",
            "assistant-message",
            "user-message",
            "typing",
            "desktop",
            "mobile",
        ):
            assert command in detail_html
        assert "localStorage" not in preview_html
        assert "fetch(" not in preview_html
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_pattern_lab_preview_fails_closed_on_persisted_hash_drift(
    tmp_path, monkeypatch
):
    client, factory, engine = await _client(tmp_path, monkeypatch, email="operator@example.com")
    try:
        await client.get("/admin/pattern-lab")
        async with factory() as database, database.begin():
            row = await database.scalar(select(WidgetPatternVersion))
            assert row is not None
            row.implementation_sha256 = "0" * 64
            pattern_id, version = row.pattern_id, row.version
        response = await client.get(
            f"/admin/pattern-lab/{pattern_id}/{version}/preview"
        )
        assert response.status == 409
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_pattern_lab_escapes_hostile_manifest_metadata(
    tmp_path, monkeypatch
):
    client, factory, engine = await _client(tmp_path, monkeypatch, email="operator@example.com")
    try:
        await client.get("/admin/pattern-lab")
        async with factory() as database, database.begin():
            row = await database.scalar(select(WidgetPatternVersion))
            assert row is not None
            snapshot = dict(row.manifest_snapshot)
            snapshot["ai_description"] = "<script>alert(1)</script> hostile"
            snapshot["provenance"] = {"origin": "<img src=x>", "review_state": "approved"}
            row.manifest_snapshot = snapshot
            pattern_id, version = row.pattern_id, row.version
        detail = await client.get(f"/admin/pattern-lab/{pattern_id}/{version}")
        html = await detail.text()
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "&lt;img src=x&gt;" in html
        assert "<script>alert(1)</script> hostile" not in html
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_pattern_lab_review_csrf_append_only_and_normalized_email(
    tmp_path, monkeypatch
):
    client, factory, engine = await _client(tmp_path, monkeypatch, email=" Operator@Example.com ")
    try:
        await client.get("/admin/pattern-lab")
        async with factory() as database:
            row = await database.scalar(select(WidgetPatternVersion))
            assert row is not None
            before_snapshot = dict(row.manifest_snapshot)
        detail_url = f"/admin/pattern-lab/{row.pattern_id}/{row.version}"
        detail = await client.get(detail_url)
        assert detail.status == 200
        detail_html = await detail.text()
        marker = 'name="csrf_token" value="'
        start = detail_html.index(marker) + len(marker)
        token = detail_html[start : detail_html.index('"', start)]
        missing = await client.post(
            f"{detail_url}/review", data={"status": "approved", "comment": "ok"}
        )
        assert missing.status == 403
        invalid = await client.post(
            f"{detail_url}/review",
            data={"csrf_token": token, "status": "invalid", "comment": "ok"},
        )
        assert invalid.status == 400
        response = await client.post(
            f"{detail_url}/review",
            data={"csrf_token": token, "status": "approved", "comment": "checked"},
            allow_redirects=False,
        )
        assert response.status in {302, 303}
        async with factory() as database:
            reviews = (await database.scalars(select(PatternReview))).all()
            assert len(reviews) == 1
            assert reviews[0].reviewer_email == "operator@example.com"
            persisted = await database.scalar(
                select(WidgetPatternVersion).where(WidgetPatternVersion.id == row.id)
            )
            assert persisted.manifest_snapshot == before_snapshot
    finally:
        await client.close()
        await engine.dispose()
