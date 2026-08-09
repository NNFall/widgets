from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy import event, select
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
        assert "Описание для AI" in html
        assert "Библиотека эффектов" in html
        assert "14 категорий" in html
        assert "Нужно проверить" in html
        assert "review=ready_for_review" in html
        assert "Появление ответа AI" in html
        assert "Ждёт проверки" in html
        assert "Assistant Message Enter technical fixture" not in html
        assert "assistant-message-cascade@1" not in html
        assert "Lifecycle" not in html
        assert "AI description" not in html
        assert "Описание для AI / hash" not in html
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
        for query in (
            "category=launcher_shape&category=not-a-category",
            "status=active&status=active",
        ):
            duplicate = await client.get(f"/admin/pattern-lab?{query}")
            assert duplicate.status == 400
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_pattern_lab_review_queue_and_category_aware_preview_controls(
    tmp_path, monkeypatch
):
    client, factory, engine = await _client(
        tmp_path, monkeypatch, email="operator@example.com"
    )
    try:
        queue = await client.get("/admin/pattern-lab?review=ready_for_review")
        assert queue.status == 200
        queue_html = await queue.text()
        assert "Быстрый вход сообщения пользователя" in queue_html
        assert "Проверить →" in queue_html
        assert "Появление сообщения пользователя" in queue_html
        assert "Пользовательское сообщение быстро входит справа" in queue_html
        assert "<details><summary>AI description</summary>" not in queue_html

        detail = await client.get(
            "/admin/pattern-lab/user-message-slide-settle/1"
        )
        assert detail.status == 200
        detail_html = await detail.text()
        assert "Появление сообщения пользователя" in detail_html
        assert "Описание для AI" in detail_html
        assert ">Повторить анимацию</button>" in detail_html
        assert ">Запустить</button>" not in detail_html
        assert ">Сообщение пользователя</button>" not in detail_html
        assert ">Сообщение AI</button>" not in detail_html
        assert "Effective review" not in detail_html
        assert "Lifecycle" not in detail_html
        assert "Technical" not in detail_html
        assert "Implementation hash" not in detail_html
        assert "data-pattern-command='user-message'" in detail_html
        assert "Перезапускаем…" in detail_html
        assert "iframe.dataset.viewport = command" in detail_html
        assert "data-pattern-command='mobile'" in detail_html
        assert "data-pattern-command='desktop'" in detail_html
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_pattern_lab_list_loads_review_states_with_one_bounded_query(
    tmp_path, monkeypatch
):
    client, factory, engine = await _client(tmp_path, monkeypatch, email="operator@example.com")
    await client.get("/admin/pattern-lab")
    async with factory() as database:
        rows = (await database.scalars(select(WidgetPatternVersion))).all()
        assert rows
        now = datetime.now(timezone.utc)
        database.add_all(
            [
                PatternReview(
                    pattern_version_id=rows[0].id,
                    reviewer_email=f"reviewer-{index}@example.com",
                    status="approved" if index == 120 else "rejected",
                    comment=f"review {index}",
                    created_at=now + timedelta(seconds=index),
                )
                for index in range(121)
            ]
        )
        await database.commit()
    review_selects: list[str] = []

    def _track_review_select(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = statement.lstrip().lower()
        if normalized.startswith("select") and "pattern_reviews" in normalized:
            review_selects.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _track_review_select)
    try:
        response = await client.get("/admin/pattern-lab")
        assert response.status == 200
        assert len(review_selects) == 1
        assert "row_number" in review_selects[0].lower()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _track_review_select)
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
        csp = preview.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "sandbox allow-scripts" in csp
        assert "frame-ancestors 'self'" in csp
        preview_html = await preview.text()
        assert "event.source !== window.parent" in preview_html
        assert "delete root.dataset.patternCommand" in preview_html
        assert "void document.documentElement.offsetWidth" in preview_html
        assert "Анимация воспроизведена" in detail_html
        assert "localStorage" not in preview_html
        assert "fetch(" not in preview_html
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_pattern_lab_review_history_is_bounded_with_truncation_indicator(
    tmp_path, monkeypatch
):
    client, factory, engine = await _client(tmp_path, monkeypatch, email="operator@example.com")
    try:
        await client.get("/admin/pattern-lab")
        async with factory() as database:
            row = await database.scalar(select(WidgetPatternVersion))
            assert row is not None
            now = datetime.now(timezone.utc)
            database.add_all(
                [
                    PatternReview(
                        pattern_version_id=row.id,
                        reviewer_email=f"reviewer-{index}@example.com",
                        status="approved",
                        comment=f"review {index}",
                        created_at=now + timedelta(seconds=index),
                    )
                    for index in range(101)
                ]
            )
            await database.commit()
            pattern_id, version = row.pattern_id, row.version
        detail = await client.get(f"/admin/pattern-lab/{pattern_id}/{version}")
        assert detail.status == 200
        html = await detail.text()
        assert "Показаны последние 100 проверок" in html
        assert "reviewer-0@example.com" not in html
        assert "reviewer-100@example.com" in html
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
        assert "&lt;img src=x&gt;" not in html
        assert "<img src=x>" not in html
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
