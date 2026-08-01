from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.generation_forensics import setup_generation_forensics_routes
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import (
    GenerationEvent,
    GenerationForensicAccessLog,
    GenerationForensicManifest,
    GenerationRun,
    ModelCall,
    Project,
)
from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.forensics.models import ForensicBlob
from builder_lab.forensics.storage import GenerationForensicStorage
from core.config import settings as core_settings


RUN_ID = UUID("72000000-0000-0000-0000-000000000001")
CALL_ID = UUID("72000000-0000-4000-8000-000000000002")
CREATED_AT = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'admin.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Admin", slug="admin"))
        database.add_all(
            [
                User(id=10, tenant_id=1, email="member@example.com"),
                User(id=11, tenant_id=1, email="customer@example.com"),
            ]
        )
        project = Project(
            tenant_id=1,
            owner_user_id=11,
            source_url="https://customer.example/private",
        )
        database.add(project)
        await database.flush()
        database.add(
            GenerationRun(
                id=RUN_ID,
                project_id=project.id,
                mode="express",
                state="failed",
                idempotency_key="private-key",
            )
        )
    return engine, factory, project.id


def _jpeg_blob() -> ForensicBlob:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(20, 120, 80)).save(output, format="JPEG")
    data = output.getvalue()
    return ForensicBlob(
        data=data,
        mime_type="image/jpeg",
        byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        created_at=CREATED_AT + timedelta(seconds=4),
    )


async def _seed_forensic_evidence(*, factory, config, project_id):
    storage = GenerationForensicStorage.open(config, writable=True)
    storage.initialize_run(
        user_id=11,
        project_id=project_id,
        run_id=RUN_ID,
        created_at=CREATED_AT,
    )
    storage.write_json_entry(
        run_id=RUN_ID,
        relative_path="events/00000001-run.failed.json",
        kind="event",
        payload={
            "diagnostic": "Authorization: Bearer private-token-123456",
            "legacy_html": "<script>private evidence</script>",
        },
        created_at=CREATED_AT + timedelta(seconds=1),
    )
    storage.write_model_call(
        run_id=RUN_ID,
        call_id=CALL_ID,
        attempt=1,
        payload={
            "request": {"prompt": "client_secret=legacy-secret-value"},
            "response": {"text": "<script>model evidence</script>"},
        },
        created_at=CREATED_AT + timedelta(seconds=2),
    )
    blob = _jpeg_blob()
    storage.write_blob(run_id=RUN_ID, blob=blob)
    manifest = storage.load_manifest(RUN_ID)
    digest = storage.manifest_digest(RUN_ID)
    async with factory() as database, database.begin():
        database.add_all(
            [
                GenerationEvent(
                    id=2,
                    run_id=RUN_ID,
                    sequence=2,
                    event_type="stage.failed",
                    public_message="second",
                    payload={"password": "legacy-password"},
                    created_at=CREATED_AT + timedelta(seconds=3),
                ),
                GenerationEvent(
                    id=1,
                    run_id=RUN_ID,
                    sequence=1,
                    event_type="run.created",
                    public_message="<script>legacy db</script>",
                    payload={"api_key": "legacy-key"},
                    forensic_ref="events/00000001-run.failed.json",
                    created_at=CREATED_AT + timedelta(seconds=1),
                ),
                ModelCall(
                    id=CALL_ID,
                    run_id=RUN_ID,
                    provider="gemini",
                    model="gemini-3.6-flash",
                    role="visual_critic",
                    prompt_version="critic-v1",
                    request_id="request-123",
                    input_tokens=120,
                    output_tokens=30,
                    thinking_tokens=10,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                    latency_ms=1500,
                    status="completed",
                    cost_state="reported",
                    cost_microusd=123456,
                    pricing_snapshot={},
                    created_at=CREATED_AT + timedelta(seconds=2),
                ),
                GenerationForensicManifest(
                    run_id=RUN_ID,
                    user_id=11,
                    project_id=project_id,
                    storage_key=f"runs/{RUN_ID.hex[:2]}/{RUN_ID}",
                    schema_version=1,
                    state="failed",
                    last_event_sequence=2,
                    entry_count=len(manifest.entries),
                    byte_count=sum(entry.byte_count for entry in manifest.entries),
                    manifest_sha256=digest,
                    expires_at=CREATED_AT + timedelta(hours=120),
                    metadata_json={},
                    created_at=CREATED_AT,
                    updated_at=CREATED_AT + timedelta(seconds=5),
                ),
            ]
        )
    return storage, blob


async def _client(*, factory, config, provider_spy=None) -> TestClient:
    app = web.Application()
    if factory is not None:
        app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(generation_forensics=config)
    if provider_spy is not None:
        app["provider_spy"] = provider_spy
    setup_session(app, SimpleCookieStorage(cookie_name="forensic-admin-test"))

    async def login(request: web.Request) -> web.Response:
        session = await get_session(request)
        session["email"] = request.match_info["email"]
        session["tenant_slug"] = "admin"
        return web.json_response({"ok": True})

    app.router.add_post("/test/login/{email}", login)
    setup_generation_forensics_routes(app, config=config)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_admin_forensics_disabled_is_404_before_database_or_storage(tmp_path) -> None:
    config = GenerationForensicsConfig.disabled()
    client = await _client(factory=None, config=config)
    try:
        response = await client.get("/admin/generation-runs", allow_redirects=False)
        assert response.status == 404
        assert not config.root.exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_admin_forensics_empty_allowlist_is_404(tmp_path) -> None:
    engine, factory, _ = await _database(tmp_path)
    config = GenerationForensicsConfig(
        enabled=True,
        root=tmp_path / "forensics",
        ttl_hours=120,
        max_bytes=1_000_000,
        admin_emails=(),
    )
    client = await _client(factory=factory, config=config)
    try:
        await client.post("/test/login/member@example.com")
        response = await client.get("/admin/generation-runs", allow_redirects=False)
        assert response.status == 404
        assert not config.root.exists()
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_forensics_exact_allowlist_and_bounded_search_are_audited(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(core_settings, "ADMIN_PASSWORD", "strong-test-password")
    monkeypatch.setattr(core_settings, "ADMIN_EMAILS", {"member@example.com"})
    engine, factory, project_id = await _database(tmp_path)
    async with factory() as database, database.begin():
        database.add_all(
            [
                GenerationRun(
                    id=uuid4(),
                    project_id=project_id,
                    mode="express",
                    state="completed",
                    idempotency_key=f"bounded-{index}",
                )
                for index in range(104)
            ]
        )
    config = GenerationForensicsConfig(
        enabled=True,
        root=tmp_path / "forensics",
        ttl_hours=120,
        max_bytes=1_000_000,
        admin_emails=("member@example.com",),
    )
    client = await _client(factory=factory, config=config)
    try:
        await client.post("/test/login/member@example.com.attacker")
        denied = await client.get(
            f"/admin/generation-runs?run_id={RUN_ID}", allow_redirects=False
        )
        assert denied.status == 404

        await client.post("/test/login/ MEMBER@EXAMPLE.COM ")
        form = await client.get("/admin/generation-runs")
        assert form.status == 200
        assert str(RUN_ID) not in await form.text()

        by_run = await client.get(f"/admin/generation-runs?run_id={RUN_ID}")
        assert by_run.status == 200
        assert str(RUN_ID) in await by_run.text()
        assert str(project_id) in await by_run.text()

        by_user = await client.get("/admin/generation-runs?user_id=11")
        assert by_user.status == 200
        assert (await by_user.text()).count("/admin/generation-runs/") == 100

        for invalid_query in (
            "run_id=not-a-uuid",
            "user_id=1%20OR%201=1",
            f"run_id={RUN_ID}&user_id=11",
        ):
            response = await client.get(f"/admin/generation-runs?{invalid_query}")
            assert response.status == 400

        async with factory() as database:
            logs = (
                await database.scalars(
                    select(GenerationForensicAccessLog).order_by(
                        GenerationForensicAccessLog.created_at,
                        GenerationForensicAccessLog.id,
                    )
                )
            ).all()
        assert [(row.actor_email, row.action, row.allowed) for row in logs] == [
            ("member@example.com.attacker", "search", False),
            ("member@example.com", "search", True),
            ("member@example.com", "search", True),
            ("member@example.com", "search", True),
        ]
        assert all(row.metadata_json == {} for row in logs)
        assert logs[0].run_id is None
        assert logs[0].project_id is None
        assert logs[0].user_id is None
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_timeline_and_streaming_export_are_verified_redacted_and_audited(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(core_settings, "ADMIN_PASSWORD", "strong-test-password")
    monkeypatch.setattr(core_settings, "ADMIN_EMAILS", {"member@example.com"})
    engine, factory, project_id = await _database(tmp_path)
    config = GenerationForensicsConfig(
        enabled=True,
        root=tmp_path / "forensics",
        ttl_hours=120,
        max_bytes=10_000_000,
        admin_emails=("member@example.com",),
    )
    _storage, blob = await _seed_forensic_evidence(
        factory=factory,
        config=config,
        project_id=project_id,
    )
    provider_spy = SimpleNamespace(calls=[])
    client = await _client(
        factory=factory,
        config=config,
        provider_spy=provider_spy,
    )
    try:
        await client.post("/test/login/member@example.com")
        page = await client.get(f"/admin/generation-runs/{RUN_ID}")
        assert page.status == 200
        assert page.headers["Cache-Control"] == "no-store"
        text = await page.text()
        for expected in (
            str(RUN_ID),
            str(project_id),
            "11",
            "failed",
            "gemini",
            "gemini-3.6-flash",
            "visual_critic",
            "request-123",
            "123456",
            "checksum: valid",
            "byte_count",
            "expires_at",
            "&lt;script&gt;legacy db&lt;/script&gt;",
            "&lt;script&gt;model evidence&lt;/script&gt;",
        ):
            assert expected in text
        for forbidden in (
            "<script>",
            "private-token-123456",
            "legacy-secret-value",
            "legacy-password",
            "legacy-key",
        ):
            assert forbidden not in text

        exported = await client.get(
            f"/admin/generation-runs/{RUN_ID}/export.ndjson"
        )
        assert exported.status == 200
        assert exported.content_type == "application/x-ndjson"
        assert exported.headers["Cache-Control"] == "no-store"
        assert "attachment" in exported.headers["Content-Disposition"]
        lines = [json.loads(line) for line in (await exported.text()).splitlines()]
        assert lines[0]["record_type"] == "run_metadata"
        assert lines[0]["run_id"] == str(RUN_ID)
        event_records = [row for row in lines if row["record_type"] == "event"]
        assert [row["sequence"] for row in event_records] == [1, 2]
        model_records = [row for row in lines if row["record_type"] == "model_call"]
        assert len(model_records) == 1
        assert model_records[0]["id"] == str(CALL_ID)
        assert model_records[0]["forensic"]["response"]["text"] == (
            "<script>model evidence</script>"
        )
        blob_records = [
            row
            for row in lines
            if row["record_type"] == "evidence" and row["kind"] == "blob"
        ]
        assert len(blob_records) == 1
        assert base64.b64decode(blob_records[0]["base64"]) == blob.data
        serialized = "\n".join(json.dumps(row) for row in lines)
        for forbidden in (
            "private-token-123456",
            "legacy-secret-value",
            "legacy-password",
            "legacy-key",
        ):
            assert forbidden not in serialized
        assert provider_spy.calls == []

        async with factory() as database:
            logs = (
                await database.scalars(
                    select(GenerationForensicAccessLog).where(
                        GenerationForensicAccessLog.run_id == RUN_ID
                    )
                )
            ).all()
        assert [(row.action, row.allowed) for row in logs] == [
            ("view", True),
            ("export", True),
        ]
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_checksum_mismatch_is_unavailable_and_never_exports_bytes(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(core_settings, "ADMIN_PASSWORD", "strong-test-password")
    monkeypatch.setattr(core_settings, "ADMIN_EMAILS", {"member@example.com"})
    engine, factory, project_id = await _database(tmp_path)
    config = GenerationForensicsConfig(
        enabled=True,
        root=tmp_path / "forensics",
        ttl_hours=120,
        max_bytes=10_000_000,
        admin_emails=("member@example.com",),
    )
    storage, _blob = await _seed_forensic_evidence(
        factory=factory,
        config=config,
        project_id=project_id,
    )
    entry = next(
        item for item in storage.load_manifest(RUN_ID).entries if item.kind == "event"
    )
    corrupt = b"corrupt-private-bytes"
    config.root.joinpath(
        "runs", RUN_ID.hex[:2], str(RUN_ID), *entry.relative_path.split("/")
    ).write_bytes(corrupt)
    client = await _client(factory=factory, config=config)
    try:
        await client.post("/test/login/member@example.com")
        page = await client.get(f"/admin/generation-runs/{RUN_ID}")
        assert page.status == 200
        text = await page.text()
        assert "checksum: unavailable" in text
        assert corrupt.decode() not in text

        exported = await client.get(
            f"/admin/generation-runs/{RUN_ID}/export.ndjson"
        )
        assert exported.status == 200
        exported_text = await exported.text()
        assert "evidence_unavailable" in exported_text
        assert base64.b64encode(corrupt).decode() not in exported_text
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_forensics_legacy_session_without_password_gate_is_404(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(core_settings, "ADMIN_PASSWORD", None)
    monkeypatch.setattr(core_settings, "ADMIN_EMAILS", {"member@example.com"})
    engine, factory, _project_id = await _database(tmp_path)
    config = GenerationForensicsConfig(
        enabled=True,
        root=tmp_path / "forensics",
        ttl_hours=120,
        max_bytes=1_000_000,
        admin_emails=("member@example.com",),
    )
    client = await _client(factory=factory, config=config)
    try:
        await client.post("/test/login/member@example.com")
        response = await client.get("/admin/generation-runs", allow_redirects=False)
        assert response.status == 404
        async with factory() as database:
            logs = (
                await database.scalars(select(GenerationForensicAccessLog))
            ).all()
        assert [(row.actor_email, row.action, row.allowed) for row in logs] == [
            ("member@example.com", "search", False)
        ]
    finally:
        await client.close()
        await engine.dispose()
