from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import setup_api_routes
from app.db.base import Base
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import WorkerServiceLease


TOKEN = "r" * 32
DEPLOYMENT_ID = "release-2026-07-28"
IMAGE_IDENTITY = "sha256:" + "a" * 64
BOOT_ID = "boot-2026-07-28-01"


async def _client(tmp_path, *, lease: dict | None = None):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ready-lease.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    if lease is not None:
        async with factory() as database:
            database.add(WorkerServiceLease(service_name="builder", **lease))
            await database.commit()
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(
        readiness_token=TOKEN,
        expected_worker_deployment_id=DEPLOYMENT_ID,
        expected_worker_image_identity=IMAGE_IDENTITY,
        expected_worker_boot_id=BOOT_ID,
    )
    setup_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, engine


def _lease(**changes) -> dict:
    now = datetime.now(UTC)
    values = {
        "worker_id": "builder-worker-1",
        "boot_id": BOOT_ID,
        "deployment_id": DEPLOYMENT_ID,
        "image_identity": IMAGE_IDENTITY,
        "started_at": now - timedelta(seconds=10),
        "heartbeat_at": now - timedelta(seconds=2),
    }
    values.update(changes)
    return values


@pytest.mark.asyncio
async def test_readiness_rejects_unauthenticated_request_before_database_access() -> None:
    app = web.Application()
    app["config"] = SimpleNamespace(
        readiness_token=TOKEN,
        expected_worker_deployment_id=DEPLOYMENT_ID,
        expected_worker_image_identity=IMAGE_IDENTITY,
        expected_worker_boot_id=BOOT_ID,
    )
    setup_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/ready")
    finally:
        await client.close()

    assert response.status == 404


@pytest.mark.asyncio
async def test_readiness_accepts_only_current_worker_boot_identity(tmp_path) -> None:
    client, engine = await _client(tmp_path, lease=_lease())
    try:
        response = await client.get(
            "/api/ready", headers={"X-Kaigo-Readiness-Token": TOKEN}
        )
        payload = await response.json()
    finally:
        await client.close()
        await engine.dispose()

    assert response.status == 200
    assert payload == {
        "status": "ready",
        "database": {"status": "ok"},
        "worker": {
            "status": "ok",
            "heartbeat_at": payload["worker"]["heartbeat_at"],
            "max_age_seconds": 300,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("changes", "expected_status"),
    [
        ({"boot_id": "previous-boot"}, "identity_mismatch"),
        ({"deployment_id": "previous-release"}, "identity_mismatch"),
        ({"image_identity": "sha256:" + "b" * 64}, "identity_mismatch"),
        (
            {"heartbeat_at": datetime.now(UTC) - timedelta(minutes=6)},
            "no_recent_activity",
        ),
    ],
)
async def test_readiness_rejects_stale_or_previous_worker(
    tmp_path, changes, expected_status
) -> None:
    client, engine = await _client(tmp_path, lease=_lease(**changes))
    try:
        response = await client.get(
            "/api/ready", headers={"X-Kaigo-Readiness-Token": TOKEN}
        )
        payload = await response.json()
    finally:
        await client.close()
        await engine.dispose()

    assert response.status == 503
    assert payload["worker"]["status"] == expected_status
    assert "queued_runs" not in payload["worker"]
    assert "running_runs" not in payload["worker"]


@pytest.mark.asyncio
async def test_readiness_fails_when_current_worker_has_not_booted(tmp_path) -> None:
    client, engine = await _client(tmp_path)
    try:
        response = await client.get(
            "/api/ready", headers={"X-Kaigo-Readiness-Token": TOKEN}
        )
        payload = await response.json()
    finally:
        await client.close()
        await engine.dispose()

    assert response.status == 503
    assert payload["worker"]["status"] == "not_started"
