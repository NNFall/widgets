from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.offers import (
    FOUNDER_GENERATION_TOKENS,
    FounderAccessService,
    FounderOfferUnavailable,
)
from app.billing.service import GenerationCreditService
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    FounderAccessGrant,
    GenerationRun,
    Project,
    Subscription,
    UsageLedger,
)


@pytest_asyncio.fixture
async def founder_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'founder.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add_all(
            User(id=user_id, tenant_id=1, email=f"owner{user_id}@example.com")
            for user_id in range(1, 25)
        )
        await database.flush()
        projects = []
        for user_id in range(1, 25):
            projects.append(
                Project(
                    tenant_id=1,
                    owner_user_id=user_id,
                    source_url=f"https://site-{user_id}.example/path",
                    status="free_result_ready",
                )
            )
        database.add_all(projects)
        await database.flush()
        ids = [project.id for project in projects]
    try:
        yield factory, ids
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_founder_claim_creates_free_period_without_payment_method(founder_db) -> None:
    factory, project_ids = founder_db
    now = datetime(2026, 8, 13, 12, tzinfo=UTC)
    service = FounderAccessService(factory, clock=lambda: now)

    result = await service.claim(1, project_ids[0])

    assert result.created is True
    assert result.grant.position == 1
    async with factory() as database:
        subscription = await database.get(Subscription, result.grant.subscription_id)
        credit = await database.scalar(
            select(UsageLedger).where(UsageLedger.entry_type == "subscription.credit")
        )
    assert subscription.provider == "founder"
    assert subscription.plan_code == "founder_14d"
    assert subscription.payment_attempt_id is None
    assert subscription.payment_method_id is None
    assert subscription.auto_renew is False
    end = subscription.current_period_end
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    assert end == now + timedelta(days=14)
    assert credit.amount == FOUNDER_GENERATION_TOKENS
    assert await GenerationCreditService(factory).available_tokens(1) == FOUNDER_GENERATION_TOKENS

    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=project_ids[0],
            mode="express",
            state="queued",
            progress=0,
            next_event_sequence=1,
            idempotency_key="founder-run",
        )
        database.add(run)
        await database.flush()
        reservation = await GenerationCreditService(factory).reserve_in_session(
            database,
            user_id=1,
            project_id=project_ids[0],
            run_id=run.id,
            amount=100_000,
        )
        assert reservation.payment_attempt_id is None
        assert reservation.founder_grant_id == result.grant.id
        assert reservation.amount == -100_000


@pytest.mark.asyncio
async def test_founder_claim_is_idempotent_but_not_reusable_on_another_project(founder_db) -> None:
    factory, project_ids = founder_db
    service = FounderAccessService(factory)

    first = await service.claim(1, project_ids[0])
    replay = await service.claim(1, project_ids[0])
    assert replay.created is False
    assert replay.grant.id == first.grant.id

    async with factory() as database, database.begin():
        second = Project(
            tenant_id=1,
            owner_user_id=1,
            source_url="https://second.example/path",
            status="free_result_ready",
        )
        database.add(second)
        await database.flush()
        second_id = second.id
    with pytest.raises(FounderOfferUnavailable, match="already_claimed"):
        await service.claim(1, second_id)


@pytest.mark.asyncio
async def test_founder_quota_stops_after_twenty_claims(founder_db) -> None:
    factory, project_ids = founder_db
    service = FounderAccessService(factory)

    for user_id, project_id in enumerate(project_ids[:20], start=1):
        result = await service.claim(user_id, project_id)
        assert result.grant.position == user_id

    assert (await service.offer(21, project_ids[20])).eligible is False
    with pytest.raises(FounderOfferUnavailable, match="quota_exhausted"):
        await service.claim(21, project_ids[20])
    async with factory() as database:
        count = await database.scalar(select(func.count()).select_from(FounderAccessGrant))
    assert count == 20
