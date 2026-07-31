from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
import logging

from aiohttp import web
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.analytics.retention import (
    FUNNEL_RETENTION_ADVISORY_KEY,
    PurgeResult,
    purge_expired_funnel_data,
)
from app.db.session import ENGINE_KEY, get_session_factory


LOGGER = logging.getLogger(__name__)
ANALYTICS_CLEANUP_TASK_KEY = "analytics_cleanup_task"


def setup_analytics_runtime(app: web.Application) -> None:
    config = app["config"]
    if not config.funnel_journeys_enabled:
        return

    async def cleanup_context(application: web.Application):
        factory = get_session_factory(application)

        async def acquire_leader() -> tuple[AsyncConnection | None, bool]:
            engine = application.get(ENGINE_KEY)
            if engine is None:
                return None, True
            connection = await engine.connect()
            if connection.dialect.name != "postgresql":
                return connection, True
            acquired = bool(
                await connection.scalar(
                    text("SELECT pg_try_advisory_lock(:key)"),
                    {"key": FUNNEL_RETENTION_ADVISORY_KEY},
                )
            )
            if not acquired:
                await connection.close()
                return None, False
            return connection, True

        leader_connection, is_leader = await acquire_leader()
        if not is_leader:
            yield
            return

        async def run_batch() -> PurgeResult:
            database_context = (
                AsyncSession(bind=leader_connection, expire_on_commit=False)
                if leader_connection is not None
                else factory()
            )
            async with database_context as database:
                result = await purge_expired_funnel_data(
                    database,
                    now=datetime.now(UTC),
                    retention_days=config.funnel_retention_days,
                    batch_size=config.funnel_cleanup_batch_size,
                    time_budget_seconds=config.funnel_cleanup_time_budget_seconds,
                )
            LOGGER.info(
                "funnel_retention_batch_completed",
                extra={
                    "lock_acquired": result.lock_acquired,
                    "deleted_journeys": result.deleted_journeys,
                    "deleted_events": result.deleted_events,
                },
            )
            return result

        try:
            first = await run_batch()
            if not first.lock_acquired:
                yield
                return

            async def cleanup_loop() -> None:
                while True:
                    await asyncio.sleep(config.funnel_cleanup_interval_seconds)
                    try:
                        await run_batch()
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("funnel_retention_batch_failed")

            task = asyncio.create_task(cleanup_loop(), name="kaigo-funnel-retention")
            application[ANALYTICS_CLEANUP_TASK_KEY] = task
            try:
                yield
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                application.pop(ANALYTICS_CLEANUP_TASK_KEY, None)
        finally:
            if leader_connection is not None:
                if leader_connection.dialect.name == "postgresql":
                    with suppress(Exception):
                        await leader_connection.execute(
                            text("SELECT pg_advisory_unlock(:key)"),
                            {"key": FUNNEL_RETENTION_ADVISORY_KEY},
                        )
                await leader_connection.close()

    app.cleanup_ctx.append(cleanup_context)


__all__ = ["ANALYTICS_CLEANUP_TASK_KEY", "setup_analytics_runtime"]
