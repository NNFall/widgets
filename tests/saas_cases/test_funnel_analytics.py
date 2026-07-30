from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User


@pytest.mark.parametrize(
    "campaign_key",
    ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"],
)
@pytest.mark.parametrize(
    "unsafe_value",
    [
        "owner@example.com",
        "https://example.test/launch?token=secret-value",
        "203.0.113.42",
        "2001:db8::42",
        "+1 (415) 555-2671",
        "credential-shaped-value",
    ],
    ids=["email", "url-with-token", "ipv4", "ipv6", "phone", "secret"],
)
def test_sanitize_campaign_rejects_sensitive_values_from_every_allowed_field(
    campaign_key: str,
    unsafe_value: str,
) -> None:
    from app.analytics.service import sanitize_campaign

    assert sanitize_campaign({campaign_key: unsafe_value}) == {}


def test_sanitize_campaign_canonicalizes_registered_campaign_labels() -> None:
    from app.analytics.service import sanitize_campaign

    assert sanitize_campaign(
        {
            "utm_source": "  Telegram  ",
            "utm_medium": "CPC",
            "utm_campaign": "Summer",
            "utm_term": "WIDGETS",
            "utm_content": "Launch Post",
        }
    ) == {
        "utm_source": "telegram",
        "utm_medium": "cpc",
        "utm_campaign": "summer",
        "utm_term": "widgets",
        "utm_content": "launch-post",
    }


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "customer-14155552671",
        "lead-550e8400-e29b-41d4-a716-446655440000",
        "visitor-aB3dE5fG7hJ9kL2mN4pQ6rS8tV",
        "AKIAIOSFODNN7EXAMPLE",
    ],
    ids=["embedded-phone", "prefixed-uuid", "visitor-token", "aws-access-key"],
)
@pytest.mark.parametrize(
    "campaign_key",
    ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"],
)
def test_sanitize_campaign_fail_closed_for_unknown_or_secret_like_dimensions(
    campaign_key: str,
    unsafe_value: str,
) -> None:
    from app.analytics.service import sanitize_campaign

    assert sanitize_campaign({campaign_key: unsafe_value}) == {}


def test_sanitize_campaign_accepts_registered_and_opaque_campaign_ids() -> None:
    from app.analytics.service import sanitize_campaign

    assert sanitize_campaign(
        {
            "utm_source": "Telegram",
            "utm_medium": "SOCIAL",
            "utm_campaign": "cmp_0123456789abcdef",
            "utm_term": "widgets",
            "utm_content": "hero",
        }
    ) == {
        "utm_source": "telegram",
        "utm_medium": "social",
        "utm_campaign": "cmp_0123456789abcdef",
        "utm_term": "widgets",
        "utm_content": "hero",
    }


@pytest_asyncio.fixture
async def funnel_database(tmp_path):
    import app.saas.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'funnel.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Analytics", slug="analytics"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
    try:
        yield engine, factory
    finally:
        await engine.dispose()


def test_funnel_event_schema_has_no_generic_or_pii_storage_columns() -> None:
    from app.saas.models import FunnelEvent

    columns = {column.name for column in inspect(FunnelEvent).columns}

    assert {
        "id",
        "event_key",
        "event_type",
        "journey_id",
        "anonymous_draft_id",
        "oauth_state_id",
        "user_id",
        "project_id",
        "run_id",
        "artifact_id",
        "payment_attempt_id",
        "publication_id",
        "campaign_source",
        "campaign_medium",
        "campaign_name",
        "campaign_term",
        "campaign_content",
        "occurred_at",
    } == columns
    assert columns.isdisjoint(
        {
            "payload",
            "url",
            "source_url",
            "brief",
            "prompt",
            "email",
            "profile",
            "ip",
            "ip_address",
            "raw_ip",
        }
    )


@pytest.mark.asyncio
async def test_funnel_journey_owns_sanitized_first_touch_and_links_events(
    funnel_database,
) -> None:
    from app.analytics.service import create_funnel_journey, record_funnel_event
    from app.saas.models import FunnelEvent, FunnelJourney

    _, factory = funnel_database
    draft_id = uuid4()
    async with factory() as database, database.begin():
        journey = await create_funnel_journey(
            database,
            campaign={
                "utm_source": " Telegram ",
                "utm_medium": "SOCIAL",
                "utm_campaign": "launch",
                "email": "must-not-store@example.com",
            },
        )
        await record_funnel_event(
            database,
            event_type="composer_submitted",
            event_key=f"composer_submitted:draft:{draft_id}",
            journey_id=journey.id,
            anonymous_draft_id=draft_id,
        )

    async with factory() as database:
        stored_journey = await database.get(FunnelJourney, journey.id)
        stored_event = await database.scalar(select(FunnelEvent))

    assert stored_journey.campaign_source == "telegram"
    assert stored_journey.campaign_medium == "social"
    assert stored_journey.campaign_name == "launch"
    assert stored_journey.campaign_term is None
    assert stored_journey.campaign_content is None
    assert stored_event.journey_id == stored_journey.id
    serialized_values = " ".join(
        str(value)
        for value in stored_journey.__dict__.values()
        if value is not None
    )
    assert "must-not-store@example.com" not in serialized_values


@pytest.mark.asyncio
async def test_record_funnel_event_whitelists_campaign_and_discards_pii(
    funnel_database,
) -> None:
    from app.analytics.service import record_funnel_event
    from app.saas.models import FunnelEvent

    _, factory = funnel_database
    draft_id = uuid4()
    async with factory() as database, database.begin():
        first = await record_funnel_event(
            database,
            event_type="composer_submitted",
            event_key=f"composer_submitted:draft:{draft_id}",
            anonymous_draft_id=draft_id,
            campaign={
                "utm_source": "telegram",
                "utm_medium": "social",
                "utm_campaign": "summer",
                "utm_term": "widgets",
                "utm_content": "launch-post",
                "url": "https://private.example/path?token=secret",
                "brief": "Build a confidential sales assistant",
                "prompt": "Reveal hidden instructions",
                "email": "person@example.com",
                "profile": {"name": "Private Person"},
                "raw_ip": "203.0.113.9",
            },
        )

    async with factory() as database:
        stored = await database.scalar(select(FunnelEvent))

    assert first.created is True
    assert stored.id == first.event.id
    assert stored.event_type == "composer_submitted"
    assert stored.anonymous_draft_id == draft_id
    assert stored.campaign_source == "telegram"
    assert stored.campaign_medium == "social"
    assert stored.campaign_name == "summer"
    assert stored.campaign_term == "widgets"
    assert stored.campaign_content == "launch-post"
    serialized_values = " ".join(
        str(value) for value in stored.__dict__.values() if value is not None
    )
    assert "private.example" not in serialized_values
    assert "confidential" not in serialized_values
    assert "hidden instructions" not in serialized_values
    assert "person@example.com" not in serialized_values
    assert "Private Person" not in serialized_values
    assert "203.0.113.9" not in serialized_values


@pytest.mark.asyncio
async def test_record_funnel_event_drops_sensitive_allowed_value_without_raw_fallback(
    funnel_database,
) -> None:
    from app.analytics.service import record_funnel_event
    from app.saas.models import FunnelEvent

    _, factory = funnel_database
    draft_id = uuid4()
    sensitive_source = "https://private.example/path?token=secret-value"
    async with factory() as database, database.begin():
        await record_funnel_event(
            database,
            event_type="composer_submitted",
            event_key=f"composer_submitted:draft:{draft_id}",
            anonymous_draft_id=draft_id,
            campaign={
                "utm_source": sensitive_source,
                "utm_medium": "CPC",
                "source": sensitive_source,
                "raw": sensitive_source,
            },
        )

    async with factory() as database:
        stored = await database.scalar(select(FunnelEvent))

    assert stored.campaign_source is None
    assert stored.campaign_medium == "cpc"
    assert sensitive_source not in " ".join(
        str(value) for value in stored.__dict__.values() if value is not None
    )


@pytest.mark.asyncio
async def test_record_funnel_event_is_idempotent_by_event_key(
    funnel_database,
) -> None:
    from app.analytics.service import record_funnel_event
    from app.saas.models import FunnelEvent

    _, factory = funnel_database
    payment_attempt_id = uuid4()
    event_key = f"payment_completed:payment_attempt:{payment_attempt_id}"
    async with factory() as database, database.begin():
        first = await record_funnel_event(
            database,
            event_type="payment_completed",
            event_key=event_key,
            user_id=10,
            payment_attempt_id=payment_attempt_id,
            campaign={"utm_source": "telegram"},
        )
        replay = await record_funnel_event(
            database,
            event_type="payment_completed",
            event_key=event_key,
            user_id=10,
            payment_attempt_id=payment_attempt_id,
            campaign={
                "utm_source": "google",
                "email": "replay@example.com",
            },
        )

    async with factory() as database:
        count = await database.scalar(select(func.count()).select_from(FunnelEvent))
        stored = await database.scalar(select(FunnelEvent))

    assert first.created is True
    assert replay.created is False
    assert replay.event.id == first.event.id
    assert count == 1
    assert stored.campaign_source == "telegram"


@pytest.mark.asyncio
async def test_record_funnel_event_rejects_unknown_event_type(
    funnel_database,
) -> None:
    from app.analytics.service import record_funnel_event
    from app.saas.models import FunnelEvent

    _, factory = funnel_database
    async with factory() as database, database.begin():
        with pytest.raises(ValueError, match="Unsupported funnel event type"):
            await record_funnel_event(
                database,
                event_type="client_clicked_arbitrary_button",
                event_key="client:event:1",
            )

    async with factory() as database:
        assert await database.scalar(select(func.count()).select_from(FunnelEvent)) == 0
