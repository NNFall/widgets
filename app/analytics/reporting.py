from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.service import sanitize_campaign
from app.saas.models import FunnelEvent, FunnelJourney


FUNNEL_REPORT_STAGES = (
    ("composer_submitted", "Заявка создана"),
    ("run_queued", "Генерация запущена"),
    ("free_result", "Получен бесплатный результат"),
    ("upgrade_started", "Начато оформление тарифа"),
    ("payment_completed", "Оплата подтверждена"),
    ("published", "Виджет опубликован"),
)


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


__all__ = ["FUNNEL_REPORT_STAGES", "load_funnel_report"]
