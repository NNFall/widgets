from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analytics.reporting import load_funnel_report
from app.db.base import Base
from app.saas.models import FunnelEvent, FunnelJourney


def _at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=UTC)


@pytest.mark.asyncio
async def test_report_counts_unique_cohort_journeys_in_fixed_stage_order(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'report.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as database, database.begin():
            journeys = [
                FunnelJourney(started_at=_at(10), campaign_source="telegram"),
                FunnelJourney(started_at=_at(11), campaign_source="telegram"),
                FunnelJourney(started_at=_at(12), campaign_source="google"),
                FunnelJourney(started_at=_at(13)),
                # Half-open range: this journey must not be counted.
                FunnelJourney(started_at=_at(20), campaign_source="google"),
            ]
            database.add_all(journeys)
            await database.flush()

            def event(journey_index: int, event_type: str, suffix: str, day: int):
                return FunnelEvent(
                    event_key=f"{event_type}:{suffix}",
                    event_type=event_type,
                    journey_id=journeys[journey_index].id,
                    occurred_at=_at(day, 12),
                )

            database.add_all(
                [
                    event(index, "composer_submitted", f"composer-{index}", 14)
                    for index in range(5)
                ]
                + [
                    event(index, "run_queued", f"run-{index}", 15)
                    for index in range(3)
                ]
                + [
                    # Replay-like second row for the same journey must not inflate.
                    event(0, "run_queued", "run-retry", 16),
                    event(0, "free_result", "free-0", 21),
                    event(1, "free_result", "free-1", 21),
                    event(0, "upgrade_started", "upgrade-0", 22),
                    event(0, "payment_completed", "payment-0", 22),
                    event(0, "published", "published-0", 23),
                ]
            )

        async with factory() as database:
            report = await load_funnel_report(
                database,
                start=_at(10),
                end=_at(20),
            )

        assert [stage["event_type"] for stage in report["stages"]] == [
            "composer_submitted",
            "run_queued",
            "free_result",
            "upgrade_started",
            "payment_completed",
            "published",
        ]
        assert [stage["journeys"] for stage in report["stages"]] == [4, 3, 2, 1, 1, 1]
        assert [stage["step_conversion_percent"] for stage in report["stages"]] == [
            100.0,
            75.0,
            66.67,
            50.0,
            100.0,
            100.0,
        ]
        assert report["stages"][-1]["cumulative_conversion_percent"] == 25.0
        assert report["sources"] == [
            {"source": "telegram", "journeys": 2},
            {"source": "google", "journeys": 1},
            {"source": "unattributed", "journeys": 1},
        ]
        serialized = str(report)
        assert "journey_id" not in serialized
        assert "event_key" not in serialized
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_report_filters_registered_first_touch_source_and_rejects_raw_values(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'filters.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as database, database.begin():
            telegram = FunnelJourney(started_at=_at(10), campaign_source="telegram")
            unattributed = FunnelJourney(started_at=_at(10))
            database.add_all([telegram, unattributed])
            await database.flush()
            database.add_all(
                [
                    FunnelEvent(
                        event_key=f"composer:{uuid4()}",
                        event_type="composer_submitted",
                        journey_id=telegram.id,
                        occurred_at=_at(10),
                    ),
                    FunnelEvent(
                        event_key=f"composer:{uuid4()}",
                        event_type="composer_submitted",
                        journey_id=unattributed.id,
                        occurred_at=_at(10),
                    ),
                ]
            )

        async with factory() as database:
            telegram_report = await load_funnel_report(
                database,
                start=_at(10),
                end=_at(20),
                source="telegram",
            )
            unattributed_report = await load_funnel_report(
                database,
                start=_at(10),
                end=_at(20),
                source="unattributed",
            )
            with pytest.raises(ValueError, match="unsupported campaign source"):
                await load_funnel_report(
                    database,
                    start=_at(10),
                    end=_at(20),
                    source="person@example.com",
                )

        assert telegram_report["stages"][0]["journeys"] == 1
        assert unattributed_report["stages"][0]["journeys"] == 1
    finally:
        await engine.dispose()
