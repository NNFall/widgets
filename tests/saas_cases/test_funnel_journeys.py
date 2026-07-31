from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.saas.models import FunnelEvent


@pytest_asyncio.fixture
async def funnel_database(tmp_path):
    import app.saas.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'journeys.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_funnel_journey_generates_uuid_and_freezes_first_touch(
    funnel_database,
) -> None:
    from app.analytics.service import ensure_funnel_journey

    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    async with funnel_database() as database, database.begin():
        first = await ensure_funnel_journey(
            database,
            journey_id=None,
            campaign={"utm_source": "telegram", "utm_campaign": "launch"},
            now=now,
        )
        replay = await ensure_funnel_journey(
            database,
            journey_id=first.journey.id,
            campaign={"utm_source": "google", "utm_campaign": "summer"},
            now=now + timedelta(minutes=1),
        )

    assert first.created is True
    assert first.journey.id.version == 4
    assert replay.created is False
    assert replay.journey.id == first.journey.id
    assert replay.journey.campaign_source == "telegram"
    assert replay.journey.campaign_name == "launch"
    assert replay.journey.started_at == now


@pytest.mark.asyncio
async def test_ensure_funnel_journey_replaces_missing_candidate_without_deriving_identity(
    funnel_database,
) -> None:
    from app.analytics.service import ensure_funnel_journey

    missing = uuid4()
    async with funnel_database() as database, database.begin():
        result = await ensure_funnel_journey(
            database,
            journey_id=missing,
            campaign={"utm_source": "telegram"},
        )

    assert result.created is True
    assert result.journey.id != missing
    assert result.journey.id.version == 4


@pytest.mark.asyncio
async def test_record_funnel_event_copies_only_journey_first_touch(
    funnel_database,
) -> None:
    from app.analytics.service import ensure_funnel_journey, record_funnel_event

    async with funnel_database() as database, database.begin():
        journey = (
            await ensure_funnel_journey(
                database,
                journey_id=None,
                campaign={"utm_source": "telegram", "utm_campaign": "launch"},
            )
        ).journey
        result = await record_funnel_event(
            database,
            event_type="landing_entered",
            event_key=f"landing_entered:journey:{journey.id}",
            journey_id=journey.id,
            campaign={"utm_source": "google", "utm_campaign": "summer"},
        )

    assert result.event.journey_id == journey.id
    assert result.event.campaign_source == "telegram"
    assert result.event.campaign_name == "launch"


@pytest.mark.asyncio
async def test_record_funnel_event_rejects_missing_or_conflicting_journey(
    funnel_database,
) -> None:
    from app.analytics.service import ensure_funnel_journey, record_funnel_event

    async with funnel_database() as database, database.begin():
        with pytest.raises(ValueError, match="journey"):
            await record_funnel_event(
                database,
                event_type="landing_entered",
                event_key="landing_entered:missing",
                journey_id=uuid4(),
            )

        first = (await ensure_funnel_journey(database, journey_id=None, campaign=None)).journey
        second = (await ensure_funnel_journey(database, journey_id=None, campaign=None)).journey
        await record_funnel_event(
            database,
            event_type="landing_entered",
            event_key="landing_entered:shared-key",
            journey_id=first.id,
        )
        with pytest.raises(ValueError, match="different journey"):
            await record_funnel_event(
                database,
                event_type="landing_entered",
                event_key="landing_entered:shared-key",
                journey_id=second.id,
            )

    async with funnel_database() as database:
        stored = await database.scalar(
            select(FunnelEvent).where(FunnelEvent.event_key == "landing_entered:shared-key")
        )
    assert stored.journey_id == first.id


def test_core_funnel_stage_order_is_fixed() -> None:
    from app.analytics.service import COMMERCIAL_FUNNEL_STAGES, CORE_FUNNEL_STAGES

    assert CORE_FUNNEL_STAGES == (
        "landing_entered",
        "authenticated_project",
        "run_queued",
        "free_result",
        "payment_completed",
        "published",
    )
    assert COMMERCIAL_FUNNEL_STAGES == ("upgrade_started", "payment_completed")
