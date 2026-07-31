from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.saas.models import FunnelEvent, FunnelJourney


FUNNEL_RETENTION_ADVISORY_KEY = 0x4B4149474F46554E


@dataclass(frozen=True)
class PurgeResult:
    lock_acquired: bool
    deleted_journeys: int
    deleted_events: int


async def _try_lock(database: AsyncSession) -> bool:
    bind = database.get_bind()
    if bind.dialect.name != "postgresql":
        return True
    return bool(
        await database.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": FUNNEL_RETENTION_ADVISORY_KEY},
        )
    )


async def purge_expired_funnel_data(
    database: AsyncSession,
    *,
    now: datetime,
    retention_days: int,
    batch_size: int,
    time_budget_seconds: int,
) -> PurgeResult:
    if now.tzinfo is None:
        raise ValueError("retention clock must be timezone-aware")
    if not 7 <= retention_days <= 365:
        raise ValueError("retention_days must be between 7 and 365")
    if not 1 <= batch_size <= 10_000:
        raise ValueError("batch_size must be between 1 and 10000")
    if not 1 <= time_budget_seconds <= 60:
        raise ValueError("time_budget_seconds must be between 1 and 60")

    cutoff = now - timedelta(days=retention_days)
    async with asyncio.timeout(time_budget_seconds):
        async with database.begin():
            if not await _try_lock(database):
                return PurgeResult(False, 0, 0)

            journey_query = (
                select(FunnelJourney.id)
                .where(FunnelJourney.started_at < cutoff)
                .order_by(FunnelJourney.started_at, FunnelJourney.id)
                .limit(batch_size)
            )
            if database.get_bind().dialect.name == "postgresql":
                journey_query = journey_query.with_for_update(skip_locked=True)
            journey_ids = tuple((await database.execute(journey_query)).scalars())

            deleted_events = 0
            if journey_ids:
                linked = await database.execute(
                    delete(FunnelEvent).where(FunnelEvent.journey_id.in_(journey_ids))
                )
                deleted_events += max(int(linked.rowcount or 0), 0)

            legacy_ids = tuple(
                (
                    await database.execute(
                        select(FunnelEvent.id)
                        .where(
                            FunnelEvent.journey_id.is_(None),
                            FunnelEvent.occurred_at < cutoff,
                        )
                        .order_by(FunnelEvent.occurred_at, FunnelEvent.id)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if legacy_ids:
                legacy = await database.execute(
                    delete(FunnelEvent).where(FunnelEvent.id.in_(legacy_ids))
                )
                deleted_events += max(int(legacy.rowcount or 0), 0)

            deleted_journeys = 0
            if journey_ids:
                journeys = await database.execute(
                    delete(FunnelJourney).where(FunnelJourney.id.in_(journey_ids))
                )
                deleted_journeys = max(int(journeys.rowcount or 0), 0)

    return PurgeResult(True, deleted_journeys, deleted_events)


__all__ = [
    "FUNNEL_RETENTION_ADVISORY_KEY",
    "PurgeResult",
    "purge_expired_funnel_data",
]
