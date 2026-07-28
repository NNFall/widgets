from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import socket
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from builder_lab.worker import BuilderWorker, PostgresWorkerQueue, RunClaim


StageHandler = Callable[[RunClaim], Awaitable[None]]


def _database_url() -> str:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for the builder worker")
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not database_url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("builder worker requires PostgreSQL with asyncpg")
    return database_url


def load_stage_handler(reference: str | None = None) -> StageHandler:
    dotted = (reference or os.getenv("KAIGO_BUILDER_STAGE_HANDLER", "")).strip()
    if not dotted or ":" not in dotted:
        raise RuntimeError(
            "KAIGO_BUILDER_STAGE_HANDLER must name an async callable as module:attribute"
        )
    module_name, attribute_name = dotted.split(":", 1)
    if not module_name or not attribute_name:
        raise RuntimeError("KAIGO_BUILDER_STAGE_HANDLER is invalid")
    try:
        handler = getattr(importlib.import_module(module_name), attribute_name)
    except (AttributeError, ImportError) as exc:
        raise RuntimeError(
            f"cannot load builder stage handler {dotted!r}"
        ) from exc
    if not callable(handler) or not inspect.iscoroutinefunction(handler):
        raise RuntimeError("builder stage handler must be an async callable")
    return handler


def _positive_float(name: str, default: str) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be numeric") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be positive")
    return value


async def run() -> None:
    # Resolve the executable handler before opening PostgreSQL or claiming work.
    # A missing integration must fail fast instead of silently completing jobs.
    handler = load_stage_handler()
    engine = create_async_engine(_database_url(), future=True, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    lease_seconds = _positive_float("KAIGO_BUILDER_LEASE_SECONDS", "90")
    heartbeat_interval = _positive_float(
        "KAIGO_BUILDER_HEARTBEAT_SECONDS", "20"
    )
    if heartbeat_interval >= lease_seconds:
        await engine.dispose()
        raise RuntimeError(
            "KAIGO_BUILDER_HEARTBEAT_SECONDS must be shorter than the lease"
        )
    worker_id = os.getenv("KAIGO_BUILDER_WORKER_ID", "").strip() or (
        f"{socket.gethostname()}-{os.getpid()}"
    )
    queue = PostgresWorkerQueue(factory, lease_seconds=lease_seconds)
    worker = BuilderWorker(
        queue=queue,
        worker_id=worker_id,
        stage_handler=handler,
        heartbeat_interval=heartbeat_interval,
        idle_poll_interval=_positive_float("KAIGO_BUILDER_POLL_SECONDS", "0.5"),
    )
    logging.getLogger(__name__).info("starting durable builder worker %s", worker_id)
    try:
        await worker.run_forever()
    finally:
        await engine.dispose()


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
