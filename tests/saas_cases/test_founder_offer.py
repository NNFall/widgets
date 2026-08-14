from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.payments import (
    BillingService,
    CheckoutResult,
    IntroOfferUnavailable,
)
from app.billing.offers import (
    FOUNDER_GENERATION_TOKENS,
    FounderClaimResult,
    FounderAccessService,
    FounderOfferUnavailable,
)
from app.billing.service import GenerationCreditService
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    FounderAccessGrant,
    GenerationRun,
    PaymentAttempt,
    Project,
    Subscription,
    UsageLedger,
)
from tests.saas_cases.test_billing_service import FakeProvider


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


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
async def test_founder_claim_rejects_same_normalized_origin_for_another_user(
    founder_db,
) -> None:
    factory, project_ids = founder_db
    async with factory() as database, database.begin():
        second_project = Project(
            tenant_id=1,
            owner_user_id=2,
            source_url="https://SITE-1.EXAMPLE:443/another-path",
            status="free_result_ready",
        )
        database.add(second_project)
        await database.flush()
        second_project_id = second_project.id

    service = FounderAccessService(factory)
    first = await service.claim(1, project_ids[0])

    offer = await service.offer(2, second_project_id)
    assert offer.eligible is False
    assert offer.reason == "domain_already_claimed"
    assert offer.remaining == 19

    with pytest.raises(FounderOfferUnavailable) as unavailable:
        await service.claim(2, second_project_id)
    assert str(unavailable.value) == "domain_already_claimed"

    async with factory() as database:
        assert await database.scalar(select(func.count()).select_from(FounderAccessGrant)) == 1
        assert await database.scalar(select(func.count()).select_from(Subscription)) == 1
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 1
        credit = await database.scalar(
            select(UsageLedger).where(
                UsageLedger.founder_grant_id == first.grant.id,
                UsageLedger.entry_type == "subscription.credit",
            )
        )
        assert credit is not None
        assert credit.amount == FOUNDER_GENERATION_TOKENS
        grant = await database.scalar(select(FounderAccessGrant))
        assert grant is not None
        assert grant.position == 1
        assert grant.user_id == 1
        assert grant.project_id == project_ids[0]


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


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_concurrent_founder_claim_and_intro_checkout_have_one_winner() -> None:
    assert POSTGRES_URL is not None
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")

    schema = f"founder_race_{uuid4().hex}"
    admin_engine = create_async_engine(
        source_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    engine = None
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        engine = create_async_engine(
            source_url.render_as_string(hide_password=False),
            connect_args={
                "server_settings": {
                    "search_path": f'"{schema}",public',
                }
            },
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Race", slug=f"race-{schema}"))
            database.add(User(id=10, tenant_id=1, email=f"{schema}@example.com"))
            await database.flush()
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url=f"https://{schema}.example/services",
                status="free_result_ready",
            )
            database.add(project)
            await database.flush()
            project_id = project.id

        payment_service = BillingService(factory, FakeProvider())
        founder_service = FounderAccessService(factory)
        start = asyncio.Event()

        async def claim_once():
            await start.wait()
            try:
                return await founder_service.claim(10, project_id)
            except Exception as error:  # assertions below classify the race winner
                return error

        async def checkout_once():
            await start.wait()
            try:
                return await payment_service.create_checkout(
                    10,
                    "starter_intro_15d",
                    f"{schema}-intro-race",
                    project_id=project_id,
                    auto_renew=False,
                )
            except Exception as error:  # assertions below classify the race winner
                return error

        claim_task = asyncio.create_task(claim_once())
        checkout_task = asyncio.create_task(checkout_once())
        start.set()
        claim_result, checkout_result = await asyncio.gather(
            claim_task,
            checkout_task,
        )

        founder_won = isinstance(claim_result, FounderClaimResult)
        intro_won = isinstance(checkout_result, CheckoutResult)
        assert founder_won ^ intro_won
        if founder_won:
            assert isinstance(checkout_result, IntroOfferUnavailable)
        else:
            assert isinstance(claim_result, FounderOfferUnavailable)

        async with factory() as database:
            founder_count = await database.scalar(
                select(func.count()).select_from(FounderAccessGrant)
            )
            intro_count = await database.scalar(
                select(func.count()).select_from(PaymentAttempt).where(
                    PaymentAttempt.purpose == "initial",
                    PaymentAttempt.plan_code == "starter_intro_15d",
                )
            )
        assert (founder_count, intro_count) in {(1, 0), (0, 1)}
        assert founder_count == int(founder_won)
        assert intro_count == int(intro_won)
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()
