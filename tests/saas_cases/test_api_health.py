from __future__ import annotations

from datetime import UTC, datetime, timedelta
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes import setup_api_routes
from app.db.base import Base
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import WorkerServiceLease


async def _ready_client(tmp_path, *, heartbeat_at: datetime | None):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ready.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    if heartbeat_at is not None:
        async with factory() as database:
            database.add(
                WorkerServiceLease(
                    service_name="builder",
                    worker_id="readiness-worker-canary",
                    boot_id="test-boot",
                    deployment_id="test-release",
                    image_identity="sha256:" + "a" * 64,
                    started_at=heartbeat_at - timedelta(seconds=1),
                    heartbeat_at=heartbeat_at,
                )
            )
            await database.commit()
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    setup_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client, engine


@pytest.mark.asyncio
async def test_readiness_is_db_backed_and_reports_recent_worker_activity(
    tmp_path,
) -> None:
    client, engine = await _ready_client(
        tmp_path,
        heartbeat_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    try:
        response = await client.get("/api/ready")
        payload = await response.json()
    finally:
        await client.close()
        await engine.dispose()

    assert response.status == 200
    assert payload["status"] == "ready"
    assert payload["database"] == {"status": "ok"}
    assert payload["worker"]["status"] == "ok"
    assert payload["worker"]["heartbeat_at"]


@pytest.mark.asyncio
async def test_readiness_fails_without_recent_worker_activity(tmp_path) -> None:
    client, engine = await _ready_client(tmp_path, heartbeat_at=None)
    try:
        response = await client.get("/api/ready")
        payload = await response.json()
    finally:
        await client.close()
        await engine.dispose()

    assert response.status == 503
    assert payload["status"] == "not_ready"
    assert payload["database"] == {"status": "ok"}
    assert payload["worker"]["status"] == "not_started"


@pytest.mark.asyncio
async def test_readiness_fails_when_database_session_is_unavailable() -> None:
    app = web.Application()
    setup_api_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/api/ready")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 503
    assert payload["status"] == "not_ready"
    assert payload["database"] == {"status": "error"}
    assert payload["worker"]["status"] == "unknown"
