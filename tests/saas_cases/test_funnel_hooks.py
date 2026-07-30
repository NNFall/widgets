from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.billing.payments import BillingService, CheckoutIdempotencyConflict
from app.publication.service import PublicationService
from app.saas.models import FunnelEvent, FunnelJourney, PaymentAttempt, Project, Publication
from tests.saas_cases.test_billing_service import FakeProvider
from tests.saas_cases.test_billing_service import billing_db as _billing_db_fixture
from tests.saas_cases.test_publication import publication_db as _publication_db_fixture


@pytest_asyncio.fixture(name="billing_db")
async def funnel_billing_db(tmp_path):
    generator = _billing_db_fixture.__wrapped__(tmp_path)
    value = await anext(generator)
    try:
        yield value
    finally:
        try:
            await anext(generator)
        except StopAsyncIteration:
            pass


@pytest_asyncio.fixture(name="publication_db")
async def funnel_publication_db(tmp_path):
    generator = _publication_db_fixture.__wrapped__(tmp_path)
    value = await anext(generator)
    try:
        yield value
    finally:
        try:
            await anext(generator)
        except StopAsyncIteration:
            pass


@pytest.mark.asyncio
async def test_checkout_and_webhook_replays_emit_one_server_owned_event_each(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    async with factory() as database, database.begin():
        journey = FunnelJourney(campaign_source="telegram")
        database.add(journey)
        await database.flush()
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            journey_id=journey.id,
            source_url="https://example.com/",
        )
        second_project = Project(
            tenant_id=1,
            owner_user_id=10,
            journey_id=journey.id,
            source_url="https://second.example.com/",
        )
        foreign_project = Project(
            tenant_id=1,
            owner_user_id=11,
            source_url="https://foreign.example.com/",
        )
        database.add_all([project, second_project, foreign_project])
        await database.flush()

    with pytest.raises(ValueError, match="not owned"):
        await service.create_checkout(
            10,
            "starter_monthly",
            "foreign-project-key",
            project_id=foreign_project.id,
        )

    checkout = await service.create_checkout(
        10,
        "starter_monthly",
        "funnel-checkout-key",
        project_id=project.id,
    )
    checkout_replay = await service.create_checkout(
        10,
        "starter_monthly",
        "funnel-checkout-key",
        project_id=project.id,
    )
    with pytest.raises(CheckoutIdempotencyConflict, match="another project"):
        await service.create_checkout(
            10,
            "starter_monthly",
            "funnel-checkout-key",
            project_id=second_project.id,
        )
    webhook_payload = {"payment_id": f"pay-{checkout.payment_id}"}
    fulfilled = await service.handle_notification(webhook_payload)
    webhook_replay = await service.handle_notification(webhook_payload)

    async with factory() as database:
        events = list(
            (
                await database.execute(
                    select(FunnelEvent).order_by(FunnelEvent.event_type)
                )
            ).scalars()
        )

    assert checkout_replay.created is False
    assert fulfilled.processed is True
    assert webhook_replay.processed is False
    assert [(event.event_type, event.user_id) for event in events] == [
        ("payment_completed", 10),
        ("upgrade_started", 10),
    ]
    assert {event.payment_attempt_id for event in events} == {checkout.payment_id}
    assert {event.project_id for event in events} == {project.id}
    assert {event.journey_id for event in events} == {journey.id}
    assert all(event.campaign_source is None for event in events)
    assert all(event.event_key for event in events)
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
    assert attempt.project_id == project.id
    assert attempt.journey_id == journey.id


@pytest.mark.asyncio
async def test_publish_replay_emits_one_published_event_and_expiry_keeps_it_live(
    publication_db,
) -> None:
    _engine, factory, ids = publication_db
    service = PublicationService(factory)
    async with factory() as database, database.begin():
        journey = FunnelJourney(campaign_source="telegram")
        database.add(journey)
        await database.flush()
        project = await database.get(Project, ids["project"])
        project.journey_id = journey.id

    first = await service.publish(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        artifact_id=ids["first"],
    )
    replay = await service.publish(
        ids["project"],
        actor_user_id=10,
        tenant_id=1,
        artifact_id=ids["first"],
    )

    async with factory() as database:
        events = list((await database.execute(select(FunnelEvent))).scalars())
        assert await database.scalar(select(func.count()).select_from(FunnelEvent)) == 1

    assert replay.created is False
    assert events[0].event_type == "published"
    assert events[0].event_key == f"published:publication:{first.publication_id}"
    assert events[0].user_id == 10
    assert events[0].project_id == ids["project"]
    assert events[0].artifact_id == ids["first"]
    assert events[0].publication_id == first.publication_id
    assert events[0].journey_id == journey.id
    async with factory() as database:
        publication = await database.get(Publication, first.publication_id)
    assert publication.journey_id == journey.id

    from app.saas.models import Subscription

    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(Subscription.user_id == 10)
        )
        subscription.status = "expired"
        subscription.current_period_end = datetime.now(UTC)

    still_live = await service.resolve(first.stable_key)
    assert still_live.release_id == first.release_id
