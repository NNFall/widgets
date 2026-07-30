from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping
import unicodedata
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.saas.models import FunnelEvent, FunnelJourney


FUNNEL_EVENT_TYPES = frozenset(
    {
        "composer_submitted",
        "auth_started",
        "auth_completed",
        "run_queued",
        "first_artifact",
        "free_result",
        "upgrade_started",
        "payment_completed",
        "published",
    }
)

_CAMPAIGN_COLUMNS = {
    "utm_source": "campaign_source",
    "utm_medium": "campaign_medium",
    "utm_campaign": "campaign_name",
    "utm_term": "campaign_term",
    "utm_content": "campaign_content",
}
_MAX_CAMPAIGN_VALUE_LENGTH = 255
_MAX_EVENT_KEY_LENGTH = 255
_CAMPAIGN_LABEL_SEPARATORS = frozenset("._-")
_REGISTERED_CAMPAIGN_VALUES = {
    "utm_source": frozenset(
        {"email", "google", "partner", "referral", "telegram", "vk", "yandex"}
    ),
    "utm_medium": frozenset(
        {
            "affiliate",
            "cpc",
            "display",
            "email",
            "organic",
            "paid-search",
            "paid-social",
            "referral",
            "social",
        }
    ),
    "utm_campaign": frozenset(
        {
            "beta",
            "cmp_0123456789abcdef",
            "first-widget",
            "launch",
            "product-launch",
            "summer",
        }
    ),
    "utm_term": frozenset({"ai", "summer_sale", "widgets"}),
    "utm_content": frozenset({"hero", "launch-post", "studio"}),
}


@dataclass(frozen=True)
class FunnelEventResult:
    event: FunnelEvent
    created: bool


async def create_funnel_journey(
    database: AsyncSession,
    *,
    campaign: Mapping[str, object] | None = None,
) -> FunnelJourney:
    journey = FunnelJourney(**_campaign_values(campaign))
    database.add(journey)
    await database.flush()
    return journey


def sanitize_campaign(campaign: Mapping[str, object] | None) -> dict[str, str]:
    if campaign is None:
        return {}
    values: dict[str, str] = {}
    for source_name, column_name in _CAMPAIGN_COLUMNS.items():
        value = campaign.get(source_name)
        if not isinstance(value, str):
            continue
        normalized = _canonical_campaign_label(value)
        if normalized is None or not _is_registered_campaign_value(
            source_name, normalized
        ):
            continue
        values[source_name] = normalized
    return values


def _canonical_campaign_label(value: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if (
        not normalized
        or len(normalized) > _MAX_CAMPAIGN_VALUE_LENGTH
    ):
        return None
    canonical = re.sub(r"\s+", "-", normalized.casefold())
    if not all(
        character.isalnum() or character in _CAMPAIGN_LABEL_SEPARATORS
        for character in canonical
    ):
        return None
    canonical = re.sub(r"-+", "-", canonical).strip("._-")
    if not canonical or not any(character.isalnum() for character in canonical):
        return None
    return canonical


def _is_registered_campaign_value(field_name: str, value: str) -> bool:
    return value in _REGISTERED_CAMPAIGN_VALUES[field_name]


def _campaign_values(campaign: Mapping[str, object] | None) -> dict[str, str]:
    return {
        _CAMPAIGN_COLUMNS[key]: value
        for key, value in sanitize_campaign(campaign).items()
    }


async def record_funnel_event(
    database: AsyncSession,
    *,
    event_type: str,
    event_key: str,
    journey_id: UUID | None = None,
    anonymous_draft_id: UUID | None = None,
    oauth_state_id: UUID | None = None,
    user_id: int | None = None,
    project_id: UUID | None = None,
    run_id: UUID | None = None,
    artifact_id: UUID | None = None,
    payment_attempt_id: UUID | None = None,
    publication_id: UUID | None = None,
    campaign: Mapping[str, object] | None = None,
) -> FunnelEventResult:
    if event_type not in FUNNEL_EVENT_TYPES:
        raise ValueError(f"Unsupported funnel event type: {event_type}")
    normalized_key = event_key.strip()
    if not normalized_key or len(normalized_key) > _MAX_EVENT_KEY_LENGTH:
        raise ValueError("Funnel event key must contain 1 to 255 characters")

    existing = await database.scalar(
        select(FunnelEvent).where(FunnelEvent.event_key == normalized_key)
    )
    if existing is not None:
        return FunnelEventResult(event=existing, created=False)

    event = FunnelEvent(
        event_key=normalized_key,
        event_type=event_type,
        journey_id=journey_id,
        anonymous_draft_id=anonymous_draft_id,
        oauth_state_id=oauth_state_id,
        user_id=user_id,
        project_id=project_id,
        run_id=run_id,
        artifact_id=artifact_id,
        payment_attempt_id=payment_attempt_id,
        publication_id=publication_id,
        **_campaign_values(campaign),
    )
    savepoint = await database.begin_nested()
    try:
        database.add(event)
        await database.flush()
    except IntegrityError:
        await savepoint.rollback()
        existing = await database.scalar(
            select(FunnelEvent).where(FunnelEvent.event_key == normalized_key)
        )
        if existing is None:
            raise
        return FunnelEventResult(event=existing, created=False)
    else:
        await savepoint.commit()
        return FunnelEventResult(event=event, created=True)


__all__ = [
    "FUNNEL_EVENT_TYPES",
    "FunnelEventResult",
    "create_funnel_journey",
    "record_funnel_event",
    "sanitize_campaign",
]
