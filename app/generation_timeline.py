from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.saas.models import (
    GenerationEvent,
    GenerationRun,
    GenerationStageAttempt,
    ModelCall,
    Project,
)
from builder_lab.generation_events import GenerationEventType


_MAX_SAFE_INTEGER = 9_007_199_254_740_991
_RUN_STATES = frozenset(
    {"created", "queued", "running", "completed", "failed", "cancelled"}
)
_EVENT_TYPES = tuple(event_type.value for event_type in GenerationEventType)
_EVENT_BUCKETS = (*_EVENT_TYPES, "unknown")
_STAGES = (
    "reference_analysis",
    "art_direction",
    "composition",
    "foundation",
    "identity",
    "conversation",
    "motion_polish",
    "validation",
    "agent_build",
)
_STAGE_BUCKETS = (*_STAGES, "unknown")
_STAGE_STATUSES = (
    "running",
    "result_staged",
    "completed",
    "failed",
    "interrupted",
    "cancelled",
    "accounting_failed",
)
_STATUS_BUCKETS = (*_STAGE_STATUSES, "unknown")
_COST_STATES = ("reported", "estimated", "unknown", "not_billed")


def _bounded_int(value: object) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(max(number, 0), _MAX_SAFE_INTEGER)


def _utc_datetime(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: object) -> str | None:
    timestamp = _utc_datetime(value)
    if timestamp is None:
        return None
    return timestamp.isoformat().replace("+00:00", "Z")


def _earliest(left: object, right: object) -> datetime | None:
    left_time = _utc_datetime(left)
    right_time = _utc_datetime(right)
    if left_time is None:
        return right_time
    if right_time is None:
        return left_time
    return min(left_time, right_time)


def _latest(left: object, right: object) -> datetime | None:
    left_time = _utc_datetime(left)
    right_time = _utc_datetime(right)
    if left_time is None:
        return right_time
    if right_time is None:
        return left_time
    return max(left_time, right_time)


def _usd(microusd: int) -> float:
    return round(_bounded_int(microusd) / 1_000_000, 6)


async def load_owner_timeline_summary(
    database: AsyncSession,
    *,
    run_id: UUID,
    owner_user_id: int,
    tenant_id: int,
) -> dict[str, Any] | None:
    """Load a bounded, aggregate-only run summary for one project owner."""

    run_row = (
        await database.execute(
            select(
                GenerationRun.id,
                GenerationRun.state,
                GenerationRun.created_at,
                GenerationRun.started_at,
                GenerationRun.finished_at,
            )
            .join(Project, GenerationRun.project_id == Project.id)
            .where(
                GenerationRun.id == run_id,
                Project.owner_user_id == owner_user_id,
                Project.tenant_id == tenant_id,
            )
        )
    ).one_or_none()
    if run_row is None:
        return None

    event_bucket = case(
        {event_type: event_type for event_type in _EVENT_TYPES},
        value=GenerationEvent.event_type,
        else_="unknown",
    ).label("event_bucket")
    event_rows = (
        await database.execute(
            select(event_bucket, func.count(GenerationEvent.id).label("count"))
            .where(GenerationEvent.run_id == run_id)
            .group_by(event_bucket)
        )
    ).all()
    event_counts = {event_type: 0 for event_type in _EVENT_BUCKETS}
    for row in event_rows:
        bucket = row.event_bucket if row.event_bucket in event_counts else "unknown"
        event_counts[bucket] = _bounded_int(row.count)

    stage_bucket = case(
        {stage: stage for stage in _STAGES},
        value=GenerationStageAttempt.stage,
        else_="unknown",
    ).label("stage_bucket")
    status_bucket = case(
        {status: status for status in _STAGE_STATUSES},
        value=GenerationStageAttempt.status,
        else_="unknown",
    ).label("status_bucket")
    stage_rows = (
        await database.execute(
            select(
                stage_bucket,
                status_bucket,
                func.count(GenerationStageAttempt.id).label("count"),
                func.min(GenerationStageAttempt.ordinal).label("min_ordinal"),
                func.max(GenerationStageAttempt.ordinal).label("max_ordinal"),
                func.min(GenerationStageAttempt.started_at).label("started_at_min"),
                func.max(GenerationStageAttempt.finished_at).label(
                    "finished_at_max"
                ),
            )
            .where(GenerationStageAttempt.run_id == run_id)
            .group_by(stage_bucket, status_bucket)
        )
    ).all()

    stages: dict[str, dict[str, Any]] = {}
    for row in stage_rows:
        stage = row.stage_bucket if row.stage_bucket in _STAGE_BUCKETS else "unknown"
        status = (
            row.status_bucket if row.status_bucket in _STATUS_BUCKETS else "unknown"
        )
        stage_summary = stages.setdefault(
            stage,
            {
                "stage": stage,
                "count": 0,
                "status_counts": {name: 0 for name in _STATUS_BUCKETS},
                "min_ordinal": _MAX_SAFE_INTEGER,
                "max_ordinal": 0,
                "started_at_min": None,
                "finished_at_max": None,
            },
        )
        count = _bounded_int(row.count)
        stage_summary["count"] = _bounded_int(stage_summary["count"] + count)
        stage_summary["status_counts"][status] = _bounded_int(
            stage_summary["status_counts"][status] + count
        )
        stage_summary["min_ordinal"] = min(
            stage_summary["min_ordinal"], _bounded_int(row.min_ordinal)
        )
        stage_summary["max_ordinal"] = max(
            stage_summary["max_ordinal"], _bounded_int(row.max_ordinal)
        )
        stage_summary["started_at_min"] = _earliest(
            stage_summary["started_at_min"], row.started_at_min
        )
        stage_summary["finished_at_max"] = _latest(
            stage_summary["finished_at_max"], row.finished_at_max
        )

    stage_attempts: list[dict[str, Any]] = []
    for stage in _STAGE_BUCKETS:
        if stage not in stages:
            continue
        stage_summary = stages[stage]
        stage_summary["started_at_min"] = _iso_utc(
            stage_summary["started_at_min"]
        )
        stage_summary["finished_at_max"] = _iso_utc(
            stage_summary["finished_at_max"]
        )
        stage_attempts.append(stage_summary)

    valid_reported_cost = and_(
        ModelCall.cost_state == "reported",
        ModelCall.cost_microusd.is_not(None),
        ModelCall.cost_microusd >= 0,
    )
    valid_estimated_cost = and_(
        ModelCall.cost_state == "estimated",
        ModelCall.cost_microusd.is_not(None),
        ModelCall.cost_microusd >= 0,
    )
    invalid_cost = or_(
        ModelCall.cost_state.is_(None),
        ~ModelCall.cost_state.in_(_COST_STATES),
        ModelCall.cost_state == "unknown",
        and_(
            ModelCall.cost_state.in_(("reported", "estimated")),
            or_(
                ModelCall.cost_microusd.is_(None),
                ModelCall.cost_microusd < 0,
            ),
        ),
        and_(
            ModelCall.cost_state == "not_billed",
            or_(
                ModelCall.cost_microusd.is_(None),
                ModelCall.cost_microusd != 0,
            ),
        ),
    )
    usage_row = (
        await database.execute(
            select(
                func.count(ModelCall.id).label("call_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (ModelCall.input_tokens >= 0, ModelCall.input_tokens),
                            else_=0,
                        )
                    ),
                    0,
                ).label("input_tokens"),
                func.coalesce(
                    func.sum(
                        case(
                            (ModelCall.output_tokens >= 0, ModelCall.output_tokens),
                            else_=0,
                        )
                    ),
                    0,
                ).label("output_tokens"),
                func.coalesce(
                    func.sum(
                        case(
                            (ModelCall.latency_ms >= 0, ModelCall.latency_ms),
                            else_=0,
                        )
                    ),
                    0,
                ).label("latency_ms_total"),
                func.coalesce(
                    func.sum(
                        case(
                            (valid_reported_cost, ModelCall.cost_microusd),
                            else_=0,
                        )
                    ),
                    0,
                ).label("reported_microusd"),
                func.coalesce(
                    func.sum(
                        case(
                            (valid_estimated_cost, ModelCall.cost_microusd),
                            else_=0,
                        )
                    ),
                    0,
                ).label("estimated_microusd"),
                func.coalesce(
                    func.sum(case((invalid_cost, 1), else_=0)),
                    0,
                ).label("unknown_calls"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    ModelCall.cost_state == "not_billed",
                                    ModelCall.cost_microusd == 0,
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("not_billed_calls"),
            ).where(
                ModelCall.run_id == run_id,
                ModelCall.role != "chat_visitor",
            )
        )
    ).one()

    call_count = _bounded_int(usage_row.call_count)
    input_tokens = _bounded_int(usage_row.input_tokens)
    output_tokens = _bounded_int(usage_row.output_tokens)
    latency_ms_total = _bounded_int(usage_row.latency_ms_total)
    reported_microusd = _bounded_int(usage_row.reported_microusd)
    estimated_microusd = _bounded_int(usage_row.estimated_microusd)
    unknown_calls = _bounded_int(usage_row.unknown_calls)
    not_billed_calls = _bounded_int(usage_row.not_billed_calls)
    known_total_microusd = _bounded_int(
        reported_microusd + estimated_microusd
    )

    state = run_row.state if run_row.state in _RUN_STATES else "unknown"
    return {
        "run": {
            "id": str(run_row.id),
            "state": state,
            "created_at": _iso_utc(run_row.created_at),
            "started_at": _iso_utc(run_row.started_at),
            "finished_at": _iso_utc(run_row.finished_at),
        },
        "event_counts": event_counts,
        "stage_attempts": stage_attempts,
        "model_usage": {
            "call_count": call_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": _bounded_int(input_tokens + output_tokens),
            "latency_ms_total": latency_ms_total,
            "cost": {
                "reported_usd": _usd(reported_microusd),
                "estimated_usd": _usd(estimated_microusd),
                "unknown_calls": unknown_calls,
                "not_billed_calls": not_billed_calls,
                "known_total_usd": _usd(known_total_microusd),
                "cost_complete": unknown_calls == 0,
            },
        },
    }


async def load_operator_timeline_summary(
    database: AsyncSession,
    *,
    run_id: UUID,
) -> dict[str, Any] | None:
    """Load the same aggregate contract after resolving scope server-side."""

    scope = (
        await database.execute(
            select(Project.owner_user_id, Project.tenant_id)
            .join(GenerationRun, GenerationRun.project_id == Project.id)
            .where(GenerationRun.id == run_id)
        )
    ).one_or_none()
    if scope is None:
        return None
    return await load_owner_timeline_summary(
        database,
        run_id=run_id,
        owner_user_id=scope.owner_user_id,
        tenant_id=scope.tenant_id,
    )


async def load_operator_recent_runs(
    database: AsyncSession,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List a bounded set of identifiers and timestamps without run payloads."""

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("operator run limit must be between 1 and 100")
    rows = (
        await database.execute(
            select(
                GenerationRun.id,
                GenerationRun.project_id,
                Project.owner_user_id,
                GenerationRun.state,
                GenerationRun.created_at,
                GenerationRun.started_at,
                GenerationRun.finished_at,
            )
            .join(Project, GenerationRun.project_id == Project.id)
            .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "project_id": str(row.project_id),
            "owner_user_id": _bounded_int(row.owner_user_id),
            "state": row.state if row.state in _RUN_STATES else "unknown",
            "created_at": _iso_utc(row.created_at),
            "started_at": _iso_utc(row.started_at),
            "finished_at": _iso_utc(row.finished_at),
        }
        for row in rows
    ]


__all__ = [
    "load_operator_recent_runs",
    "load_operator_timeline_summary",
    "load_owner_timeline_summary",
]
