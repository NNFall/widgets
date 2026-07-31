from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.saas.models import FunnelEvent, FunnelJourney


@pytest.mark.asyncio
async def test_build_funnel_report_counts_unique_cohort_journeys_and_serializes_allowlist(
    tmp_path,
) -> None:
    from app.analytics.reporting import build_funnel_report, serialize_funnel_report

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'report.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 7, 31, tzinfo=UTC)
    async with factory() as database, database.begin():
        a = FunnelJourney(campaign_source="telegram", campaign_name="launch", started_at=start + timedelta(days=1))
        b = FunnelJourney(campaign_source="telegram", campaign_name="launch", started_at=start + timedelta(days=2))
        c = FunnelJourney(campaign_source="google", campaign_name="summer", started_at=start + timedelta(days=3))
        d = FunnelJourney(started_at=start + timedelta(days=4))
        old = FunnelJourney(started_at=start - timedelta(days=1))
        database.add_all([a, b, c, d, old])
        await database.flush()

        def event(journey, stage: str, suffix: str = "1", *, at=None):
            return FunnelEvent(
                event_key=f"{stage}:{journey.id}:{suffix}",
                event_type=stage,
                journey_id=journey.id,
                occurred_at=at or journey.started_at + timedelta(minutes=1),
            )

        database.add_all(
            [event(a, stage) for stage in (
                "landing_entered", "authenticated_project", "run_queued",
                "free_result", "upgrade_started", "payment_completed", "published",
            )]
            + [
                event(b, "landing_entered"),
                event(b, "authenticated_project"),
                event(b, "run_queued", "1"),
                event(b, "run_queued", "2"),
                event(c, "landing_entered"),
                event(d, "landing_entered"),
                event(d, "authenticated_project"),
                event(old, "landing_entered"),
                event(a, "published", "future", at=end + timedelta(minutes=1)),
                FunnelEvent(
                    event_key="legacy:run",
                    event_type="run_queued",
                    occurred_at=start + timedelta(days=2),
                ),
            ]
        )

    async with factory() as database:
        report = await build_funnel_report(database, start=start, end=end)
    assert [(row.stage, row.journeys) for row in report.core] == [
        ("landing_entered", 4),
        ("authenticated_project", 3),
        ("run_queued", 2),
        ("free_result", 1),
        ("payment_completed", 1),
        ("published", 1),
    ]
    assert [(row.stage, row.journeys) for row in report.commercial] == [
        ("upgrade_started", 1),
        ("payment_completed", 1),
    ]
    assert report.core[1].from_entry_bps == 7500
    assert report.core[2].from_entry_bps == 5000

    payload = serialize_funnel_report(report, days=30, retention_days=90)
    assert set(payload) == {"window", "core", "commercial", "campaigns"}
    assert set(payload["window"]) == {"from", "to", "days", "retention_days"}
    assert all(set(row) == {"stage", "journeys", "from_entry_bps"} for row in payload["core"])
    campaign_keys = {
        "source", "medium", "campaign", "term", "content",
        "landing_entered", "authenticated_project", "run_queued",
        "free_result", "published", "upgrade_started", "payment_completed",
    }
    assert all(set(row) == campaign_keys for row in payload["campaigns"])
    serialized = str(payload)
    assert "legacy:run" not in serialized
    assert all("_id" not in key for row in payload["campaigns"] for key in row)
    await engine.dispose()
