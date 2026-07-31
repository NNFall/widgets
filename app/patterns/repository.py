from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.saas.models import (
    CompositionPlanItem,
    CompositionPlanRecord,
    GenerationRun,
    PatternOutcome,
    WidgetPatternVersion,
)
from builder_lab.patterns.models import (
    CompositionPlan,
    CustomPatternEscape,
    PatternCategory,
    PatternSelection,
)
from builder_lab.patterns.registry import PatternDefinition, PatternRegistry


@dataclass(frozen=True, slots=True)
class PersistedComposition:
    id: UUID
    plan: CompositionPlan
    implementation_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PatternOutcomeMetrics:
    technical_pass: bool
    visual_score: float | None
    repair_count: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    latency_ms: int
    cost_microusd: int
    published: bool
    adopted: bool
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        counters = (
            self.repair_count,
            self.input_tokens,
            self.output_tokens,
            self.thinking_tokens,
            self.latency_ms,
            self.cost_microusd,
        )
        if any(isinstance(value, bool) or value < 0 for value in counters):
            raise ValueError("pattern outcome counters must be non-negative integers")
        if self.visual_score is not None and (
            isinstance(self.visual_score, bool)
            or not math.isfinite(self.visual_score)
            or not 0 <= self.visual_score <= 1
        ):
            raise ValueError("visual_score must be between zero and one")
        if not isinstance(self.payload, dict):
            raise ValueError("pattern outcome payload must be an object")
        object.__setattr__(self, "payload", _json_clone(self.payload))


class PatternRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sync_registry(self, registry: PatternRegistry) -> None:
        for definition in registry.definitions:
            await self._sync_definition(definition)
        await self._session.flush()

    async def _sync_definition(self, definition: PatternDefinition) -> None:
        existing = await self._session.scalar(
            select(WidgetPatternVersion).where(
                WidgetPatternVersion.pattern_id == definition.pattern_id,
                WidgetPatternVersion.version == definition.version,
            )
        )
        snapshot = _json_clone(definition.public_dict())
        if existing is None:
            self._session.add(
                WidgetPatternVersion(
                    pattern_id=definition.pattern_id,
                    version=definition.version,
                    category=definition.category.value,
                    status=definition.status.value,
                    description=definition.description,
                    manifest_snapshot=snapshot,
                    implementation_sha256=definition.implementation_sha256,
                )
            )
            return
        immutable = (
            existing.category,
            existing.description,
            existing.manifest_snapshot,
            existing.implementation_sha256,
        )
        expected = (
            definition.category.value,
            definition.description,
            snapshot,
            definition.implementation_sha256,
        )
        if immutable != expected:
            raise ValueError(
                f"persisted pattern version drift: {definition.pattern_id}@{definition.version}"
            )
        existing.status = definition.status.value

    async def create_empty_plan(
        self,
        *,
        run_id: UUID,
        schema_version: int,
        direction_id: str,
        summary: str,
        custom_escape: dict[str, Any] | None = None,
        registry_digest: str = "0" * 64,
        direction_artifact_id: UUID | None = None,
        planner_model_call_id: UUID | None = None,
    ) -> CompositionPlanRecord:
        record = CompositionPlanRecord(
            run_id=run_id,
            direction_artifact_id=direction_artifact_id,
            planner_model_call_id=planner_model_call_id,
            schema_version=schema_version,
            direction_id=direction_id,
            summary=summary,
            custom_escape=_json_clone(custom_escape) if custom_escape else None,
            registry_digest=registry_digest,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def create_plan(
        self,
        *,
        run_id: UUID,
        plan: CompositionPlan,
        registry: PatternRegistry,
        direction_artifact_id: UUID | None = None,
        planner_model_call_id: UUID | None = None,
    ) -> CompositionPlanRecord:
        await self.sync_registry(registry)
        digest = hashlib.sha256(
            json.dumps(
                registry.public_catalog(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        record = await self.create_empty_plan(
            run_id=run_id,
            schema_version=plan.schema_version,
            direction_id=plan.direction_id,
            summary=plan.summary,
            custom_escape=plan.custom_escape.to_dict() if plan.custom_escape else None,
            registry_digest=digest,
            direction_artifact_id=direction_artifact_id,
            planner_model_call_id=planner_model_call_id,
        )
        for selection in plan.selections:
            await self.add_item(
                record.id,
                selection.slot.value,
                selection.pattern_id,
                selection.version,
                _json_clone(selection.parameters),
                reason=selection.reason,
            )
        return record

    async def add_item(
        self,
        plan_id: UUID,
        slot: str,
        pattern_id: str,
        version: int,
        parameters: dict[str, Any],
        *,
        reason: str,
    ) -> CompositionPlanItem:
        pattern = await self._session.scalar(
            select(WidgetPatternVersion).where(
                WidgetPatternVersion.pattern_id == pattern_id,
                WidgetPatternVersion.version == version,
            )
        )
        if pattern is None:
            raise ValueError(f"unknown persisted pattern: {pattern_id}@{version}")
        if pattern.category != slot:
            raise ValueError("persisted pattern category does not match plan slot")
        item = CompositionPlanItem(
            composition_plan_id=plan_id,
            pattern_version_id=pattern.id,
            slot=slot,
            parameters=_json_clone(parameters),
            reason=reason,
        )
        async with self._session.begin_nested():
            self._session.add(item)
            await self._session.flush()
        return item

    async def load_plan(self, run_id: UUID) -> PersistedComposition | None:
        record = await self._session.scalar(
            select(CompositionPlanRecord).where(CompositionPlanRecord.run_id == run_id)
        )
        if record is None:
            return None
        rows = (
            await self._session.execute(
                select(CompositionPlanItem, WidgetPatternVersion)
                .join(
                    WidgetPatternVersion,
                    WidgetPatternVersion.id == CompositionPlanItem.pattern_version_id,
                )
                .where(CompositionPlanItem.composition_plan_id == record.id)
                .order_by(CompositionPlanItem.slot)
            )
        ).all()
        selections = tuple(
            PatternSelection(
                slot=PatternCategory(item.slot),
                pattern_id=pattern.pattern_id,
                version=pattern.version,
                parameters=item.parameters,
                reason=item.reason,
            )
            for item, pattern in rows
        )
        custom_escape = (
            CustomPatternEscape.from_dict(record.custom_escape)
            if record.custom_escape is not None
            else None
        )
        plan = CompositionPlan(
            schema_version=record.schema_version,
            direction_id=record.direction_id,
            selections=selections,
            summary=record.summary,
            custom_escape=custom_escape,
        )
        return PersistedComposition(
            id=record.id,
            plan=plan,
            implementation_hashes=tuple(
                pattern.implementation_sha256 for _, pattern in rows
            ),
        )

    async def clone_plan(
        self,
        *,
        source_run_id: UUID,
        target_run_id: UUID,
    ) -> PersistedComposition:
        """Clone the immutable persisted plan without consulting the live registry."""

        existing = await self.load_plan(target_run_id)
        if existing is not None:
            return existing
        source = await self._session.scalar(
            select(CompositionPlanRecord).where(
                CompositionPlanRecord.run_id == source_run_id
            )
        )
        if source is None:
            raise ValueError("source run has no persisted composition plan")
        source_items = list(
            (
                await self._session.execute(
                    select(CompositionPlanItem)
                    .where(CompositionPlanItem.composition_plan_id == source.id)
                    .order_by(CompositionPlanItem.slot)
                )
            ).scalars()
        )
        clone = CompositionPlanRecord(
            run_id=target_run_id,
            direction_artifact_id=None,
            planner_model_call_id=None,
            schema_version=source.schema_version,
            direction_id=source.direction_id,
            summary=source.summary,
            custom_escape=(
                _json_clone(source.custom_escape)
                if source.custom_escape is not None
                else None
            ),
            registry_digest=source.registry_digest,
        )
        self._session.add(clone)
        await self._session.flush()
        self._session.add_all(
            [
                CompositionPlanItem(
                    composition_plan_id=clone.id,
                    pattern_version_id=item.pattern_version_id,
                    slot=item.slot,
                    parameters=_json_clone(item.parameters),
                    reason=item.reason,
                )
                for item in source_items
            ]
        )
        await self._session.flush()
        cloned = await self.load_plan(target_run_id)
        if cloned is None:  # pragma: no cover - transaction invariant
            raise RuntimeError("cloned composition plan was not persisted")
        return cloned

    async def record_terminal_outcomes(
        self,
        *,
        run_id: UUID,
        final_artifact_id: UUID | None,
        model_call_id: UUID | None,
        metrics: PatternOutcomeMetrics,
    ) -> tuple[PatternOutcome, ...]:
        plan = await self._session.scalar(
            select(CompositionPlanRecord).where(CompositionPlanRecord.run_id == run_id)
        )
        if plan is None:
            return ()
        items = (
            (
                await self._session.execute(
                    select(CompositionPlanItem)
                    .where(CompositionPlanItem.composition_plan_id == plan.id)
                    .order_by(CompositionPlanItem.slot)
                )
            )
            .scalars()
            .all()
        )
        outcomes: list[PatternOutcome] = []
        item_count = len(items)
        for index, item in enumerate(items):
            artifact_key = str(final_artifact_id) if final_artifact_id else "none"
            idempotency_key = f"{run_id}:{item.id}:{artifact_key}"
            outcome = await self._session.scalar(
                select(PatternOutcome).where(
                    PatternOutcome.idempotency_key == idempotency_key
                )
            )
            values = {
                "composition_plan_item_id": item.id,
                "run_id": run_id,
                "final_artifact_id": final_artifact_id,
                "model_call_id": model_call_id,
                "technical_pass": metrics.technical_pass,
                "visual_score": metrics.visual_score,
                "repair_count": metrics.repair_count,
                "input_tokens": _allocate(metrics.input_tokens, item_count, index),
                "output_tokens": _allocate(metrics.output_tokens, item_count, index),
                "thinking_tokens": _allocate(
                    metrics.thinking_tokens,
                    item_count,
                    index,
                ),
                "latency_ms": _allocate(metrics.latency_ms, item_count, index),
                "cost_microusd": _allocate(
                    metrics.cost_microusd,
                    item_count,
                    index,
                ),
                "published": metrics.published,
                "adopted": metrics.adopted,
                "payload": _json_clone(metrics.payload),
            }
            if outcome is None:
                outcome = PatternOutcome(
                    idempotency_key=idempotency_key,
                    **values,
                )
                self._session.add(outcome)
            else:
                # Publication and adoption are live product signals. They may
                # advance after the immutable terminal metrics were recorded,
                # so a worker replay validates the facts without reverting or
                # treating those signal changes as outcome drift.
                immutable_values = {
                    field: value
                    for field, value in values.items()
                    if field not in {"published", "adopted"}
                }
                persisted = {
                    field: getattr(outcome, field) for field in immutable_values
                }
                if persisted != immutable_values:
                    raise ValueError(f"pattern outcome drift for {idempotency_key}")
            outcomes.append(outcome)
        await self._session.flush()
        return tuple(outcomes)

    async def mark_active_publication(
        self,
        *,
        project_id: UUID,
        artifact_id: UUID,
    ) -> None:
        """Mark outcomes for the project's currently active release.

        Publication is a mutable signal over immutable outcome facts: exactly
        the outcomes attributed to the active release are marked published.
        Adoption is intentionally untouched because publication and rollback
        are not evidence that a customer adopted a pattern.
        """

        project_run_ids = select(GenerationRun.id).where(
            GenerationRun.project_id == project_id
        )
        await self._session.execute(
            update(PatternOutcome)
            .where(
                PatternOutcome.run_id.in_(project_run_ids),
                PatternOutcome.published.is_(True),
                or_(
                    PatternOutcome.final_artifact_id.is_(None),
                    PatternOutcome.final_artifact_id != artifact_id,
                ),
            )
            .values(published=False)
        )
        await self._session.execute(
            update(PatternOutcome)
            .where(
                PatternOutcome.run_id.in_(project_run_ids),
                PatternOutcome.final_artifact_id == artifact_id,
                PatternOutcome.published.is_(False),
            )
            .values(published=True)
        )
        await self._session.flush()


def _json_clone(value: Any) -> Any:
    normalized = _plain_json(value)
    return json.loads(
        json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )


def _plain_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_plain_json(item) for item in value]
    return value


def _allocate(total: int, count: int, index: int) -> int:
    if count <= 0:
        return 0
    quotient, remainder = divmod(total, count)
    return quotient + int(index < remainder)
