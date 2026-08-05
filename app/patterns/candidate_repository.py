"""Flush-only persistence for stage-aware pattern candidate provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.saas.models import (
    GenerationArtifact,
    GenerationRun,
    ModelCall,
    PatternCandidateGroupRecord,
    PatternCandidateItemRecord,
    PatternCandidatePlanRecord,
    PatternReview,
    PatternStageExposure,
    PatternStageUsageClaim,
    WidgetPatternVersion,
)
from builder_lab.models import Stage
from builder_lab.patterns.atomic_models import (
    AtomicPatternCategory,
    AtomicPatternStatus,
    PatternCandidate,
    PatternCandidateGroup,
    PatternCandidatePlan,
)
from builder_lab.patterns.atomic_registry import AtomicPatternRegistry
from builder_lab.patterns.candidate_resolver import STAGE_PATTERN_CATEGORIES


# Keep validation intentionally bounded rather than imposing a public-domain
# policy: admin installations may use an internal hostname without a dot.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+$")
_REVIEW_COMMENT_MAX = 4_000
_STAGES = frozenset(stage.value for stage in STAGE_PATTERN_CATEGORIES)


def _json_clone(value: Any) -> Any:
    return json.loads(
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    )


def _freeze_json(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise ValueError("manifest metadata is nested too deeply")
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item, depth=depth + 1) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    return value


def _stage_value(stage: Stage | str) -> str:
    value = stage.value if isinstance(stage, Stage) else stage
    if not isinstance(value, str) or value not in _STAGES:
        raise ValueError("stage is not a permitted pattern generation stage")
    return value


@dataclass(frozen=True, slots=True)
class PersistedPatternCandidateItem:
    id: UUID
    pattern_version_id: UUID
    pattern_id: str
    version: int
    category: AtomicPatternCategory
    rank: int
    reason: str
    implementation_sha256: str
    manifest_snapshot: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_snapshot", _freeze_json(_json_clone(self.manifest_snapshot)))

    @property
    def item_id(self) -> UUID:
        return self.id

    @property
    def hash(self) -> str:
        return self.implementation_sha256

    @property
    def implementation_hash(self) -> str:
        return self.implementation_sha256


@dataclass(frozen=True, slots=True)
class PersistedPatternCandidateGroup:
    id: UUID
    category: AtomicPatternCategory
    stage_mapping: tuple[str, ...]
    candidates: tuple[PatternCandidate, ...]
    items: tuple[PersistedPatternCandidateItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_mapping", tuple(self.stage_mapping))
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "items", tuple(self.items))


@dataclass(frozen=True, slots=True)
class PersistedPatternCandidatePlan:
    id: UUID
    plan: PatternCandidatePlan
    groups: tuple[PersistedPatternCandidateGroup, ...]
    registry_digest: str
    direction_artifact_id: UUID | None
    selector_model_call_id: UUID | None
    created_at: datetime | None = None

    @property
    def items(self) -> tuple[PersistedPatternCandidateItem, ...]:
        return tuple(item for group in self.groups for item in group.items)

    @property
    def implementation_hashes(self) -> tuple[str, ...]:
        return tuple(item.implementation_sha256 for item in self.items)

    @property
    def stage_mapping(self) -> Mapping[str, tuple[str, ...]]:
        return MappingProxyType({group.category.value: group.stage_mapping for group in self.groups})

    @property
    def stage_values(self) -> tuple[str, ...]:
        return tuple(sorted({stage for group in self.groups for stage in group.stage_mapping}))


class PatternCandidateRepository:
    """Persistence API that never commits the injected session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def sync_registry(self, registry: AtomicPatternRegistry) -> None:
        for definition in registry.definitions:
            existing = await self._session.scalar(
                select(WidgetPatternVersion).where(
                    WidgetPatternVersion.pattern_id == definition.pattern_id,
                    WidgetPatternVersion.version == definition.version,
                )
            )
            snapshot = _json_clone(definition.selector_dict())
            if any(name in snapshot for name in ("html", "css", "javascript")):
                raise ValueError("manifest snapshot must not contain implementation assets")
            expected = (
                definition.category.value,
                definition.summary,
                snapshot,
                definition.implementation_sha256,
            )
            if existing is None:
                self._session.add(
                    WidgetPatternVersion(
                        id=uuid4(),
                        pattern_id=definition.pattern_id,
                        version=definition.version,
                        category=definition.category.value,
                        status=definition.status.value,
                        description=definition.summary,
                        manifest_snapshot=snapshot,
                        implementation_sha256=definition.implementation_sha256,
                    )
                )
                continue
            immutable = (
                existing.category,
                existing.description,
                _json_clone(existing.manifest_snapshot),
                existing.implementation_sha256,
            )
            persisted_snapshot = dict(immutable[2])
            expected_snapshot = dict(expected[2])
            # Manifest lifecycle is represented both in the selector snapshot
            # and in the indexed status column.  It is the sole mutable field;
            # all other canonical metadata remains drift-protected.
            persisted_snapshot.pop("status", None)
            expected_snapshot.pop("status", None)
            if immutable[:2] + (persisted_snapshot, immutable[3]) != (
                expected[0],
                expected[1],
                expected_snapshot,
                expected[3],
            ):
                raise ValueError(f"persisted pattern version drift: {definition.pattern_id}@{definition.version}")
            existing.status = definition.status.value
        await self._session.flush()

    @staticmethod
    def registry_digest(registry: AtomicPatternRegistry) -> str:
        catalog = sorted(
            (_json_clone(item) for item in registry.selector_catalog()),
            key=lambda item: (str(item.get("category", "")), str(item.get("pattern_id", "")), int(item.get("version", 0))),
        )
        return hashlib.sha256(
            json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    async def _validate_run_lineage(
        self,
        *,
        run_id: UUID,
        direction_artifact_id: UUID | None,
        selector_model_call_id: UUID | None,
    ) -> None:
        if await self._session.scalar(select(GenerationRun.id).where(GenerationRun.id == run_id)) is None:
            raise ValueError("unknown generation run")
        if direction_artifact_id is not None:
            artifact = await self._session.scalar(select(GenerationArtifact).where(GenerationArtifact.id == direction_artifact_id))
            if artifact is None or artifact.run_id != run_id:
                raise ValueError("direction artifact does not belong to run")
        if selector_model_call_id is not None:
            call = await self._session.scalar(select(ModelCall).where(ModelCall.id == selector_model_call_id))
            if call is None or call.run_id != run_id:
                raise ValueError("selector model call does not belong to run")

    async def create_plan(
        self,
        *,
        run_id: UUID,
        plan: PatternCandidatePlan,
        registry: AtomicPatternRegistry,
        direction_artifact_id: UUID | None = None,
        selector_model_call_id: UUID | None = None,
    ) -> PatternCandidatePlanRecord:
        if not isinstance(plan, PatternCandidatePlan) or plan.schema_version != 2:
            raise ValueError("candidate plan must use schema version 2")
        await self.sync_registry(registry)
        digest = self.registry_digest(registry)
        existing = await self._session.scalar(select(PatternCandidatePlanRecord).where(PatternCandidatePlanRecord.run_id == run_id))
        if existing is not None:
            loaded = await self.load_plan(run_id)
            if loaded is None or loaded.plan != plan or loaded.direction_artifact_id != direction_artifact_id or loaded.selector_model_call_id != selector_model_call_id:
                raise ValueError("conflicting candidate plan replay")
            return existing
        await self._validate_run_lineage(run_id=run_id, direction_artifact_id=direction_artifact_id, selector_model_call_id=selector_model_call_id)
        persisted_versions: dict[tuple[str, int], WidgetPatternVersion] = {}
        for group in plan.groups:
            for candidate in group.candidates:
                try:
                    definition = registry.resolve(candidate.pattern_id, candidate.version)
                except Exception as exc:
                    raise ValueError(f"unknown candidate version: {candidate.pattern_id}@{candidate.version}") from exc
                if definition.status is not AtomicPatternStatus.ACTIVE:
                    raise ValueError("candidate lifecycle is not active")
                if definition.category is not group.category:
                    raise ValueError("candidate category does not match its group")
                version = await self._session.scalar(
                    select(WidgetPatternVersion).where(
                        WidgetPatternVersion.pattern_id == candidate.pattern_id,
                        WidgetPatternVersion.version == candidate.version,
                    )
                )
                if version is None:
                    raise ValueError("candidate version was not synchronized")
                if version.category != group.category.value:
                    raise ValueError("persisted candidate category mismatch")
                if await self.effective_review_state(version.id) != "approved":
                    raise ValueError("candidate is not effectively approved")
                persisted_versions[(candidate.pattern_id, candidate.version)] = version
        record = PatternCandidatePlanRecord(
            id=uuid4(), run_id=run_id, direction_artifact_id=direction_artifact_id,
            selector_model_call_id=selector_model_call_id, schema_version=2,
            direction_id=plan.direction_id, summary=plan.summary, registry_digest=digest,
        )
        self._session.add(record)
        group_records: list[PatternCandidateGroupRecord] = []
        item_records: list[PatternCandidateItemRecord] = []
        for group in plan.groups:
            stage_mapping = [stage.value for stage, categories in STAGE_PATTERN_CATEGORIES.items() if group.category in categories]
            group_record = PatternCandidateGroupRecord(
                id=uuid4(), plan_id=record.id, category=group.category.value, stage_mapping=stage_mapping,
            )
            group_records.append(group_record)
            for candidate in group.candidates:
                version = persisted_versions[(candidate.pattern_id, candidate.version)]
                item_records.append(
                    PatternCandidateItemRecord(
                        id=uuid4(), group_id=group_record.id, pattern_version_id=version.id,
                        rank=candidate.rank, reason=candidate.reason,
                    )
                )
        self._session.add_all([*group_records, *item_records])
        await self._session.flush()
        return record

    async def load_plan(self, run_id: UUID) -> PersistedPatternCandidatePlan | None:
        record = await self._session.scalar(select(PatternCandidatePlanRecord).where(PatternCandidatePlanRecord.run_id == run_id))
        if record is None:
            return None
        group_rows = (await self._session.execute(
            select(PatternCandidateGroupRecord).where(PatternCandidateGroupRecord.plan_id == record.id).order_by(PatternCandidateGroupRecord.category)
        )).scalars().all()
        plan_groups: list[PatternCandidateGroup] = []
        persisted_groups: list[PersistedPatternCandidateGroup] = []
        for group_row in group_rows:
            try:
                category = AtomicPatternCategory(group_row.category)
            except ValueError as exc:
                raise ValueError("persisted candidate category is invalid") from exc
            rows = (await self._session.execute(
                select(PatternCandidateItemRecord, WidgetPatternVersion)
                .join(WidgetPatternVersion, WidgetPatternVersion.id == PatternCandidateItemRecord.pattern_version_id)
                .where(PatternCandidateItemRecord.group_id == group_row.id)
                .order_by(PatternCandidateItemRecord.rank)
            )).all()
            candidates: list[PatternCandidate] = []
            persisted_items: list[PersistedPatternCandidateItem] = []
            for item_row, version_row in rows:
                snapshot = _json_clone(version_row.manifest_snapshot)
                if snapshot.get("category") not in (None, category.value):
                    raise ValueError("persisted manifest category drift")
                candidates.append(PatternCandidate(pattern_id=version_row.pattern_id, version=version_row.version, rank=item_row.rank, reason=item_row.reason))
                persisted_items.append(
                    PersistedPatternCandidateItem(
                        id=item_row.id, pattern_version_id=version_row.id,
                        pattern_id=version_row.pattern_id, version=version_row.version,
                        category=category, rank=item_row.rank, reason=item_row.reason,
                        implementation_sha256=version_row.implementation_sha256,
                        manifest_snapshot=snapshot,
                    )
                )
            raw_mapping = group_row.stage_mapping
            if not isinstance(raw_mapping, (list, tuple)):
                raise ValueError("persisted stage mapping is invalid")
            stage_mapping = tuple(str(stage) for stage in raw_mapping)
            group = PatternCandidateGroup(category=category, candidates=tuple(candidates))
            plan_groups.append(group)
            persisted_groups.append(PersistedPatternCandidateGroup(id=group_row.id, category=category, stage_mapping=stage_mapping, candidates=tuple(candidates), items=tuple(persisted_items)))
        plan = PatternCandidatePlan(schema_version=record.schema_version, direction_id=record.direction_id, groups=tuple(plan_groups), summary=record.summary)
        return PersistedPatternCandidatePlan(
            id=record.id, plan=plan, groups=tuple(persisted_groups), registry_digest=record.registry_digest,
            direction_artifact_id=record.direction_artifact_id, selector_model_call_id=record.selector_model_call_id,
            created_at=record.created_at,
        )

    async def effective_review_state(
        self,
        pattern_version_id: UUID | None = None,
        *,
        pattern_id: str | None = None,
        version: int | None = None,
    ) -> str:
        if pattern_version_id is None:
            if pattern_id is None or version is None:
                raise ValueError("pattern version identity is required")
            pattern_version_id = await self._session.scalar(select(WidgetPatternVersion.id).where(WidgetPatternVersion.pattern_id == pattern_id, WidgetPatternVersion.version == version))
            if pattern_version_id is None:
                raise ValueError("unknown persisted pattern version")
        review = (await self._session.execute(
            select(PatternReview).where(PatternReview.pattern_version_id == pattern_version_id).order_by(PatternReview.created_at.desc(), PatternReview.id.desc()).limit(1)
        )).scalar_one_or_none()
        if review is not None:
            return review.status
        snapshot = await self._session.scalar(select(WidgetPatternVersion.manifest_snapshot).where(WidgetPatternVersion.id == pattern_version_id))
        if isinstance(snapshot, Mapping):
            provenance = snapshot.get("provenance")
            if isinstance(provenance, Mapping) and provenance.get("review_state") in {"ready_for_review", "approved", "rejected"}:
                return str(provenance["review_state"])
        return "ready_for_review"

    async def append_review(
        self,
        pattern_version_id: UUID,
        reviewer_email: str,
        status: str,
        comment: str = "",
    ) -> PatternReview:
        if not isinstance(reviewer_email, str):
            raise ValueError("reviewer email is invalid")
        reviewer_email = reviewer_email.strip()
        if not 3 <= len(reviewer_email) <= 320 or not _EMAIL_RE.fullmatch(reviewer_email):
            raise ValueError("reviewer email is invalid")
        if status not in {"approved", "rejected"}:
            raise ValueError("review status is invalid")
        if not isinstance(comment, str) or len(comment) > _REVIEW_COMMENT_MAX or "\x00" in comment:
            raise ValueError("review comment is invalid")
        if await self._session.scalar(select(WidgetPatternVersion.id).where(WidgetPatternVersion.id == pattern_version_id)) is None:
            raise ValueError("unknown persisted pattern version")
        latest_created = await self._session.scalar(
            select(PatternReview.created_at)
            .where(PatternReview.pattern_version_id == pattern_version_id)
            .order_by(PatternReview.created_at.desc(), PatternReview.id.desc())
            .limit(1)
        )
        created_at = datetime.now(timezone.utc)
        if latest_created is not None and latest_created.tzinfo is None:
            latest_created = latest_created.replace(tzinfo=timezone.utc)
        if latest_created is not None and created_at <= latest_created:
            created_at = latest_created + timedelta(microseconds=1)
        review = PatternReview(id=uuid4(), pattern_version_id=pattern_version_id, reviewer_email=reviewer_email, status=status, comment=comment)
        review.created_at = created_at
        self._session.add(review)
        await self._session.flush()
        return review

    async def _candidate_context(self, candidate_item_id: UUID):
        row = (await self._session.execute(
            select(PatternCandidateItemRecord, PatternCandidateGroupRecord, PatternCandidatePlanRecord, WidgetPatternVersion)
            .join(PatternCandidateGroupRecord, PatternCandidateGroupRecord.id == PatternCandidateItemRecord.group_id)
            .join(PatternCandidatePlanRecord, PatternCandidatePlanRecord.id == PatternCandidateGroupRecord.plan_id)
            .join(WidgetPatternVersion, WidgetPatternVersion.id == PatternCandidateItemRecord.pattern_version_id)
            .where(PatternCandidateItemRecord.id == candidate_item_id)
        )).one_or_none()
        if row is None:
            raise ValueError("unknown candidate item")
        return row

    async def _validate_model_call(self, model_call_id: UUID | None, run_id: UUID) -> None:
        if model_call_id is None:
            return
        call = await self._session.scalar(select(ModelCall).where(ModelCall.id == model_call_id))
        if call is None or call.run_id != run_id:
            raise ValueError("model call does not belong to run")

    async def record_exposure(
        self,
        *,
        run_id: UUID,
        stage: Stage | str,
        candidate_item_id: UUID,
        model_call_id: UUID | None = None,
    ) -> PatternStageExposure:
        stage_value = _stage_value(stage)
        _item, group, plan, _version = await self._candidate_context(candidate_item_id)
        if plan.run_id != run_id:
            raise ValueError("candidate item does not belong to run")
        if stage_value not in tuple(group.stage_mapping or ()):
            raise ValueError("candidate category is not permitted for stage")
        await self._validate_model_call(model_call_id, run_id)
        filters = [PatternStageExposure.run_id == run_id, PatternStageExposure.stage == stage_value, PatternStageExposure.candidate_item_id == candidate_item_id]
        filters.append(PatternStageExposure.model_call_id.is_(None) if model_call_id is None else PatternStageExposure.model_call_id == model_call_id)
        existing = await self._session.scalar(select(PatternStageExposure).where(and_(*filters)))
        if existing is not None:
            return existing
        exposure = PatternStageExposure(id=uuid4(), run_id=run_id, stage=stage_value, candidate_item_id=candidate_item_id, model_call_id=model_call_id)
        self._session.add(exposure)
        await self._session.flush()
        return exposure

    async def record_usage_claim(
        self,
        *,
        run_id: UUID,
        stage: Stage | str,
        usage_mode: str,
        candidate_item_id: UUID | None = None,
        exposure_id: UUID | None = None,
        model_call_id: UUID | None = None,
    ) -> PatternStageUsageClaim:
        stage_value = _stage_value(stage)
        if usage_mode not in {"primary", "combined", "inspiration"}:
            raise ValueError("usage mode is invalid")
        if candidate_item_id is None and exposure_id is None:
            raise ValueError("candidate item or exposure is required")
        if exposure_id is not None:
            exposure = await self._session.scalar(select(PatternStageExposure).where(PatternStageExposure.id == exposure_id))
            if exposure is None:
                raise ValueError("exposure not found")
            if exposure.run_id != run_id or exposure.stage != stage_value:
                raise ValueError("exposure does not belong to run and stage")
            if candidate_item_id is not None and exposure.candidate_item_id != candidate_item_id:
                raise ValueError("exposure candidate does not match")
        else:
            _item, group, plan, _version = await self._candidate_context(candidate_item_id)  # type: ignore[arg-type]
            if plan.run_id != run_id or stage_value not in tuple(group.stage_mapping or ()):
                raise ValueError("candidate item is not exposed for run and stage")
            exposures = (await self._session.execute(
                select(PatternStageExposure)
                .where(PatternStageExposure.run_id == run_id, PatternStageExposure.stage == stage_value, PatternStageExposure.candidate_item_id == candidate_item_id)
                .order_by(PatternStageExposure.created_at, PatternStageExposure.id)
            )).scalars().all()
            matching = [row for row in exposures if row.model_call_id == model_call_id]
            if not matching:
                raise ValueError("candidate item is not exposed")
            if len(matching) > 1:
                raise ValueError("candidate exposure is ambiguous")
            exposure = matching[0]
        if candidate_item_id is not None and exposure.candidate_item_id != candidate_item_id:
            raise ValueError("exposure candidate does not match")
        if exposure.model_call_id != model_call_id:
            raise ValueError("model call does not match exposure")
        await self._validate_model_call(model_call_id, run_id)
        existing = await self._session.scalar(select(PatternStageUsageClaim).where(PatternStageUsageClaim.exposure_id == exposure.id))
        if existing is not None:
            if existing.usage_mode == usage_mode and existing.model_call_id == model_call_id:
                return existing
            raise ValueError("exposure already has a different usage claim")
        claim = PatternStageUsageClaim(id=uuid4(), exposure_id=exposure.id, usage_mode=usage_mode, model_call_id=model_call_id)
        self._session.add(claim)
        await self._session.flush()
        return claim


__all__ = [
    "PatternCandidateRepository",
    "PersistedPatternCandidateGroup",
    "PersistedPatternCandidateItem",
    "PersistedPatternCandidatePlan",
    "PersistedCandidateGroup",
    "PersistedCandidateItem",
    "PersistedCandidatePlan",
]

# Short aliases keep the persistence value names ergonomic for worker callers
# while retaining the explicit pattern prefix in the public classes above.
PersistedCandidatePlan = PersistedPatternCandidatePlan
PersistedCandidateItem = PersistedPatternCandidateItem
PersistedCandidateGroup = PersistedPatternCandidateGroup
