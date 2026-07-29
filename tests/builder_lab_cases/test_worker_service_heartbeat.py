from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.saas.models import WorkerServiceLease
from builder_lab.worker import PostgresWorkerQueue
from builder_lab.worker import BuilderWorker, StageResult
from scripts import run_builder_worker


@pytest.mark.asyncio
async def test_worker_publishes_one_durable_service_heartbeat_row(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    queue = PostgresWorkerQueue(factory)
    started_at = datetime.now(UTC)
    try:
        await queue.publish_service_heartbeat(
            worker_id="worker-one",
            boot_id="boot-one",
            deployment_id="release-one",
            image_identity="sha256:" + "a" * 64,
            started_at=started_at,
        )
        await queue.publish_service_heartbeat(
            worker_id="worker-one",
            boot_id="boot-one",
            deployment_id="release-one",
            image_identity="sha256:" + "a" * 64,
            started_at=started_at,
        )
        async with factory() as database:
            lease = await database.get(WorkerServiceLease, "builder")
            assert lease is not None
            assert lease.worker_id == "worker-one"
            assert lease.boot_id == "boot-one"
            assert lease.deployment_id == "release-one"
            assert lease.image_identity == "sha256:" + "a" * 64
            assert lease.heartbeat_at >= lease.started_at
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_heartbeat_rejects_blank_or_oversized_identity(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'heartbeat-invalid.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    queue = PostgresWorkerQueue(factory)
    try:
        with pytest.raises(ValueError, match="boot_id"):
            await queue.publish_service_heartbeat(
                worker_id="worker-one",
                boot_id=" ",
                deployment_id="release-one",
                image_identity="sha256:" + "a" * 64,
                started_at=datetime.now(UTC),
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_worker_publishes_service_heartbeat_before_polling() -> None:
    events: list[str] = []

    class Queue:
        _validate_worker_id = staticmethod(lambda value: value)

        async def claim(self, _worker_id):
            events.append("claim")
            return None

    worker = None

    async def service_heartbeat() -> None:
        events.append("heartbeat")
        worker.stop()

    worker = BuilderWorker(
        queue=Queue(),
        worker_id="worker-one",
        stage_handler=lambda _claim: StageResult(public_message="unused"),
        service_heartbeat=service_heartbeat,
        heartbeat_interval=0.01,
        idle_poll_interval=0.01,
    )

    await worker.run_forever()

    assert events == ["heartbeat"]


def test_worker_service_identity_is_explicit_and_complete(monkeypatch) -> None:
    for name in (
        "KAIGO_BUILDER_WORKER_BOOT_ID",
        "KAIGO_RELEASE_ID",
        "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="KAIGO_BUILDER_WORKER_BOOT_ID"):
        run_builder_worker.worker_service_identity()

    monkeypatch.setenv("KAIGO_BUILDER_WORKER_BOOT_ID", "boot-1")
    monkeypatch.setenv("KAIGO_RELEASE_ID", "release-1")
    monkeypatch.setenv(
        "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY", "sha256:" + "a" * 64
    )

    assert run_builder_worker.worker_service_identity() == (
        "boot-1",
        "release-1",
        "sha256:" + "a" * 64,
    )


def test_worker_model_prices_prefer_builder_specific_values(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "300000")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "2500000")
    monkeypatch.setenv(
        "GEMINI_BUILDER_INPUT_PRICE_MICROUSD_PER_MILLION", "1500000"
    )
    monkeypatch.setenv(
        "GEMINI_BUILDER_OUTPUT_PRICE_MICROUSD_PER_MILLION", "7500000"
    )

    assert run_builder_worker.runtime_model_prices() == (1_500_000, 7_500_000)


def test_worker_model_prices_keep_legacy_fallback(monkeypatch) -> None:
    monkeypatch.delenv(
        "GEMINI_BUILDER_INPUT_PRICE_MICROUSD_PER_MILLION", raising=False
    )
    monkeypatch.delenv(
        "GEMINI_BUILDER_OUTPUT_PRICE_MICROUSD_PER_MILLION", raising=False
    )
    monkeypatch.setenv("GEMINI_INPUT_PRICE_MICROUSD_PER_MILLION", "1500000")
    monkeypatch.setenv("GEMINI_OUTPUT_PRICE_MICROUSD_PER_MILLION", "7500000")

    assert run_builder_worker.runtime_model_prices() == (1_500_000, 7_500_000)
