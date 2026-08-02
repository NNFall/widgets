"""Safe accounting projections for generation model calls.

The functions in this module deliberately expose an allowlisted view of the
model-call ledger.  Provider request identifiers, prompts, error messages and
pricing snapshots stay in the forensic record and never leak into the admin
waterfall.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.saas.models import (
    GenerationArtifact,
    GenerationStageAttempt,
    ModelCall,
)

_OUTCOMES = ("completed", "failed", "timed_out", "cancelled")


async def aggregate_owner_run_usage(
    database: AsyncSession,
    *,
    run_id: UUID,
) -> dict[str, Any]:
    """Return owner-safe usage totals for one generation run.

    Every provider attempt is counted, including failed and timed-out calls.
    Thinking and cache tokens are subsets reported separately and are never
    added to input/output totals a second time.
    """

    rows = (
        await database.execute(
            select(
                ModelCall.logical_invocation_id,
                ModelCall.status,
                ModelCall.input_tokens,
                ModelCall.output_tokens,
                ModelCall.thinking_tokens,
                ModelCall.cache_read_tokens,
                ModelCall.cache_write_tokens,
                ModelCall.cost_state,
                ModelCall.cost_microusd,
            ).where(
                ModelCall.run_id == run_id,
                ModelCall.role != "chat_visitor",
            )
        )
    ).all()

    outcomes = {outcome: 0 for outcome in _OUTCOMES}
    tokens = {
        "input": 0,
        "output": 0,
        "thinking": 0,
        "cache_read": 0,
        "cache_write": 0,
    }
    logical_invocations: set[UUID] = set()
    reported_cost = 0
    estimated_cost = 0
    unknown_cost_attempts = 0

    for row in rows:
        logical_invocations.add(row.logical_invocation_id)
        if row.status in outcomes:
            outcomes[row.status] += 1
        tokens["input"] += int(row.input_tokens or 0)
        tokens["output"] += int(row.output_tokens or 0)
        tokens["thinking"] += int(row.thinking_tokens or 0)
        tokens["cache_read"] += int(row.cache_read_tokens or 0)
        tokens["cache_write"] += int(row.cache_write_tokens or 0)
        if row.cost_state == "reported":
            reported_cost += int(row.cost_microusd or 0)
        elif row.cost_state == "estimated":
            estimated_cost += int(row.cost_microusd or 0)
        elif row.cost_state == "unknown":
            unknown_cost_attempts += 1

    return {
        "logical_invocations": len(logical_invocations),
        "provider_attempts": len(rows),
        "outcomes": outcomes,
        "tokens": tokens,
        "known_cost_microusd": reported_cost + estimated_cost,
        "reported_cost_microusd": reported_cost,
        "estimated_cost_microusd": estimated_cost,
        "unknown_cost_attempts": unknown_cost_attempts,
        "cost_complete": unknown_cost_attempts == 0,
    }


def _fallback_projection(call: ModelCall) -> dict[str, Any]:
    return {
        "model_call_id": str(call.id),
        "fallback_index": call.fallback_index,
        "target_provider": call.provider,
        "target_model": call.model,
        "actual_provider": call.actual_provider,
        "actual_model": call.actual_model,
        "status": call.status,
        "provider_dispatched": call.provider_dispatched,
        "latency_ms": call.latency_ms,
        "tokens": {
            "input": call.input_tokens,
            "output": call.output_tokens,
            "thinking": call.thinking_tokens,
            "cache_read": call.cache_read_tokens,
            "cache_write": call.cache_write_tokens,
        },
        "cost_state": call.cost_state,
        "cost_microusd": call.cost_microusd,
        "artifact_id": str(call.artifact_id) if call.artifact_id else None,
    }


def _logical_projection(calls: list[ModelCall]) -> dict[str, Any]:
    ordered = sorted(calls, key=lambda call: (call.fallback_index, str(call.id)))
    first = ordered[0]
    return {
        "logical_invocation_id": str(first.logical_invocation_id),
        "operation": first.operation,
        "semantic_attempt": first.semantic_attempt,
        "candidate_id": first.candidate_id,
        "persona": first.persona,
        "fallbacks": [_fallback_projection(call) for call in ordered],
    }


async def load_admin_model_waterfall(
    database: AsyncSession,
    *,
    run_id: UUID,
) -> dict[str, Any]:
    """Build an allowlisted stage -> logical call -> fallback waterfall."""

    stage_attempts = list(
        (
            await database.scalars(
                select(GenerationStageAttempt)
                .where(GenerationStageAttempt.run_id == run_id)
                .order_by(
                    GenerationStageAttempt.started_at,
                    GenerationStageAttempt.ordinal,
                    GenerationStageAttempt.id,
                )
            )
        ).all()
    )
    calls = list(
        (
            await database.scalars(
                select(ModelCall)
                .where(
                    ModelCall.run_id == run_id,
                    ModelCall.role != "chat_visitor",
                )
                .order_by(ModelCall.created_at, ModelCall.id)
            )
        ).all()
    )

    calls_by_attempt: dict[UUID | None, list[ModelCall]] = defaultdict(list)
    for call in calls:
        calls_by_attempt[call.stage_attempt_id].append(call)

    attempt_rows: list[dict[str, Any]] = []
    for attempt in stage_attempts:
        logical_groups: dict[UUID, list[ModelCall]] = defaultdict(list)
        for call in calls_by_attempt.get(attempt.id, []):
            logical_groups[call.logical_invocation_id].append(call)
        projections = [_logical_projection(group) for group in logical_groups.values()]
        projections.sort(
            key=lambda logical: (
                min(
                    fallback["fallback_index"]
                    for fallback in logical["fallbacks"]
                ),
                -len(logical["fallbacks"]),
                logical["logical_invocation_id"],
            )
        )
        attempt_rows.append(
            {
                "stage_attempt_id": str(attempt.id),
                "stage": attempt.stage,
                "ordinal": attempt.ordinal,
                "status": attempt.status,
                "logical_invocations": projections,
            }
        )

    unscoped_groups: dict[UUID, list[ModelCall]] = defaultdict(list)
    for call in calls_by_attempt.get(None, []):
        unscoped_groups[call.logical_invocation_id].append(call)

    return {
        "run_id": str(run_id),
        "stage_attempts": attempt_rows,
        "unscoped_logical_invocations": [
            _logical_projection(group) for group in unscoped_groups.values()
        ],
    }


async def validate_run_lineage(
    database: AsyncSession,
    *,
    run_id: UUID,
) -> dict[str, Any]:
    """Validate durable linkage and fallback continuity for one run."""

    calls = list(
        (
            await database.scalars(
                select(ModelCall)
                .where(
                    ModelCall.run_id == run_id,
                    ModelCall.role != "chat_visitor",
                )
                .order_by(ModelCall.created_at, ModelCall.id)
            )
        ).all()
    )
    artifact_run_ids = {
        artifact_id: artifact_run_id
        for artifact_id, artifact_run_id in (
            await database.execute(
                select(GenerationArtifact.id, GenerationArtifact.run_id).where(
                    GenerationArtifact.id.in_(
                        [call.artifact_id for call in calls if call.artifact_id]
                    )
                )
            )
        ).all()
    }

    issues: list[dict[str, Any]] = []
    logical_groups: dict[UUID, list[ModelCall]] = defaultdict(list)
    for call in calls:
        logical_groups[call.logical_invocation_id].append(call)
        if call.stage_attempt_id is None:
            issues.append(
                {"code": "missing_stage_attempt", "model_call_id": str(call.id)}
            )
        if call.artifact_id and artifact_run_ids.get(call.artifact_id) != run_id:
            issues.append(
                {
                    "code": "artifact_run_mismatch",
                    "model_call_id": str(call.id),
                    "artifact_id": str(call.artifact_id),
                }
            )

    identity_fields = (
        "stage_attempt_id",
        "operation",
        "semantic_attempt",
        "candidate_id",
        "persona",
    )
    for logical_id, group in logical_groups.items():
        fallback_indexes = sorted(call.fallback_index for call in group)
        if fallback_indexes != list(range(1, len(fallback_indexes) + 1)):
            issues.append(
                {
                    "code": "non_contiguous_fallback_indexes",
                    "logical_invocation_id": str(logical_id),
                    "fallback_indexes": fallback_indexes,
                }
            )
        first = group[0]
        if any(
            any(getattr(call, field) != getattr(first, field) for field in identity_fields)
            for call in group[1:]
        ):
            issues.append(
                {
                    "code": "inconsistent_logical_invocation",
                    "logical_invocation_id": str(logical_id),
                }
            )

    return {"valid": not issues, "issues": issues}
