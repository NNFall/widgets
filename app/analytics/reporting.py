from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import (
    COMMERCIAL_FUNNEL_STAGES,
    CORE_FUNNEL_STAGES,
    sanitize_campaign,
)
from app.saas.models import FunnelEvent, FunnelJourney


FUNNEL_REPORT_STAGES = (
    ("composer_submitted", "Заявка создана"),
    ("run_queued", "Генерация запущена"),
    ("free_result", "Получен бесплатный результат"),
    ("upgrade_started", "Начато оформление тарифа"),
    ("payment_completed", "Оплата подтверждена"),
    ("published", "Виджет опубликован"),
)


@dataclass(frozen=True)
class FunnelStageAggregate:
    stage: str
    journeys: int
    from_entry_bps: int | None


@dataclass(frozen=True)
class FunnelCampaignAggregate:
    source: str | None
    medium: str | None
    campaign: str | None
    term: str | None
    content: str | None
    counts: dict[str, int]


@dataclass(frozen=True)
class FunnelReport:
    start: datetime
    end: datetime
    core: tuple[FunnelStageAggregate, ...]
    commercial: tuple[FunnelStageAggregate, ...]
    campaigns: tuple[FunnelCampaignAggregate, ...]


def _stage_count(stage: str):
    return func.count(
        func.distinct(
            case(
                (FunnelEvent.event_type == stage, FunnelEvent.journey_id),
                else_=None,
            )
        )
    ).label(stage)


def _stage_aggregates(
    stages: tuple[str, ...], counts: dict[str, int], entry_count: int
) -> tuple[FunnelStageAggregate, ...]:
    return tuple(
        FunnelStageAggregate(
            stage=stage,
            journeys=counts.get(stage, 0),
            from_entry_bps=(
                None
                if entry_count == 0
                else round(counts.get(stage, 0) * 10_000 / entry_count)
            ),
        )
        for stage in stages
    )


async def build_funnel_report(
    database: AsyncSession,
    *,
    start: datetime,
    end: datetime,
) -> FunnelReport:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("funnel report requires an ordered timezone-aware range")
    all_stages = tuple(dict.fromkeys((*CORE_FUNNEL_STAGES, *COMMERCIAL_FUNNEL_STAGES)))
    event_join = and_(
        FunnelEvent.journey_id == FunnelJourney.id,
        FunnelEvent.occurred_at >= FunnelJourney.started_at,
        FunnelEvent.occurred_at < end,
    )
    cohort = (
        FunnelJourney.started_at >= start,
        FunnelJourney.started_at < end,
    )
    row = (
        await database.execute(
            select(*(_stage_count(stage) for stage in all_stages))
            .select_from(FunnelJourney)
            .outerjoin(FunnelEvent, event_join)
            .where(*cohort)
        )
    ).one()
    counts = {stage: int(row[index] or 0) for index, stage in enumerate(all_stages)}
    entry_count = counts.get("landing_entered", 0)

    dimensions = (
        FunnelJourney.campaign_source,
        FunnelJourney.campaign_medium,
        FunnelJourney.campaign_name,
        FunnelJourney.campaign_term,
        FunnelJourney.campaign_content,
    )
    campaign_rows = (
        await database.execute(
            select(*dimensions, *(_stage_count(stage) for stage in all_stages))
            .select_from(FunnelJourney)
            .outerjoin(FunnelEvent, event_join)
            .where(*cohort)
            .group_by(*dimensions)
        )
    ).all()
    campaigns = [
        FunnelCampaignAggregate(
            source=row[0],
            medium=row[1],
            campaign=row[2],
            term=row[3],
            content=row[4],
            counts={
                stage: int(row[5 + index] or 0)
                for index, stage in enumerate(all_stages)
            },
        )
        for row in campaign_rows
    ]
    campaigns.sort(
        key=lambda item: (
            -item.counts.get("landing_entered", 0),
            *(value or "" for value in (
                item.source, item.medium, item.campaign, item.term, item.content
            )),
        )
    )
    return FunnelReport(
        start=start,
        end=end,
        core=_stage_aggregates(CORE_FUNNEL_STAGES, counts, entry_count),
        commercial=_stage_aggregates(
            COMMERCIAL_FUNNEL_STAGES, counts, entry_count
        ),
        campaigns=tuple(campaigns),
    )


def serialize_funnel_report(
    report: FunnelReport,
    *,
    days: int,
    retention_days: int,
) -> dict[str, object]:
    def stage_rows(rows: tuple[FunnelStageAggregate, ...]) -> list[dict[str, object]]:
        return [
            {
                "stage": row.stage,
                "journeys": row.journeys,
                "from_entry_bps": row.from_entry_bps,
            }
            for row in rows
        ]

    return {
        "window": {
            "from": _iso(report.start),
            "to": _iso(report.end),
            "days": days,
            "retention_days": retention_days,
        },
        "core": stage_rows(report.core),
        "commercial": stage_rows(report.commercial),
        "campaigns": [
            {
                "source": row.source,
                "medium": row.medium,
                "campaign": row.campaign,
                "term": row.term,
                "content": row.content,
                "landing_entered": row.counts.get("landing_entered", 0),
                "authenticated_project": row.counts.get("authenticated_project", 0),
                "run_queued": row.counts.get("run_queued", 0),
                "free_result": row.counts.get("free_result", 0),
                "published": row.counts.get("published", 0),
                "upgrade_started": row.counts.get("upgrade_started", 0),
                "payment_completed": row.counts.get("payment_completed", 0),
            }
            for row in report.campaigns
        ],
    }


def _normalized_source(source: str | None) -> str | None:
    if source is None:
        return None
    if source == "unattributed":
        return source
    normalized = sanitize_campaign({"utm_source": source}).get("utm_source")
    if normalized is None:
        raise ValueError("unsupported campaign source")
    return normalized


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


async def load_funnel_report(
    database: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    source: str | None = None,
) -> dict[str, object]:
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("funnel report requires an ordered timezone-aware range")
    normalized_source = _normalized_source(source)

    cohort_filters = [
        FunnelJourney.started_at >= start,
        FunnelJourney.started_at < end,
    ]
    if normalized_source == "unattributed":
        cohort_filters.append(FunnelJourney.campaign_source.is_(None))
    elif normalized_source is not None:
        cohort_filters.append(FunnelJourney.campaign_source == normalized_source)

    stage_rows = await database.execute(
        select(
            FunnelEvent.event_type,
            func.count(func.distinct(FunnelEvent.journey_id)),
        )
        .join(FunnelJourney, FunnelJourney.id == FunnelEvent.journey_id)
        .where(
            *cohort_filters,
            FunnelEvent.event_type.in_([stage for stage, _label in FUNNEL_REPORT_STAGES]),
        )
        .group_by(FunnelEvent.event_type)
    )
    counts = {str(event_type): int(count) for event_type, count in stage_rows}

    entry_count = counts.get(FUNNEL_REPORT_STAGES[0][0], 0)
    previous_count = entry_count
    stages: list[dict[str, object]] = []
    for index, (event_type, label) in enumerate(FUNNEL_REPORT_STAGES):
        count = counts.get(event_type, 0)
        stages.append(
            {
                "event_type": event_type,
                "label": label,
                "journeys": count,
                "step_conversion_percent": (
                    100.0 if index == 0 and count > 0 else _percent(count, previous_count)
                ),
                "cumulative_conversion_percent": (
                    100.0 if index == 0 and count > 0 else _percent(count, entry_count)
                ),
            }
        )
        previous_count = count

    source_label = func.coalesce(FunnelJourney.campaign_source, "unattributed")
    source_rows = await database.execute(
        select(source_label, func.count(FunnelJourney.id))
        .where(*cohort_filters)
        .group_by(source_label)
        .order_by(func.count(FunnelJourney.id).desc(), source_label.asc())
    )

    return {
        "period": {"from": _iso(start), "to": _iso(end)},
        "filters": {"source": normalized_source},
        "stages": stages,
        "sources": [
            {"source": str(row_source), "journeys": int(count)}
            for row_source, count in source_rows
        ],
    }


__all__ = [
    "FUNNEL_REPORT_STAGES",
    "FunnelCampaignAggregate",
    "FunnelReport",
    "FunnelStageAggregate",
    "build_funnel_report",
    "load_funnel_report",
    "serialize_funnel_report",
]
