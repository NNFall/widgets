from __future__ import annotations

import asyncio
import json
import logging
import math
import secrets
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.service import record_funnel_event
from app.billing.service import TrialFailureKind
from app.patterns.candidate_repository import PatternCandidateRepository
from app.patterns.repository import PatternOutcomeMetrics, PatternRepository
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    GenerationStageAttempt,
    ModelCall,
    Project,
    ProjectVersion,
    WidgetPatternVersion,
    WorkerServiceLease,
)
from builder_lab.models import BuilderRequest, TokenUsage, WidgetArtifact
from builder_lab.forensics.models import ForensicBlob
from builder_lab.forensics.recorder import GenerationForensicRecorder
from builder_lab.generation_events import REGISTRY_VERSION, prepare_generation_event
from builder_lab.directions import run_direction_board
from builder_lab.engines.base import (
    BuilderEngine,
    BuilderEngineError,
    DirectBuilderEngine,
)
from builder_lab.models import DirectionProposal, DirectionRole, EngineName, Stage
from builder_lab.modes import get_mode_policy
from builder_lab.patterns.models import CompositionPlan
from builder_lab.patterns.atomic_models import PatternCandidatePlan
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry
from builder_lab.patterns.candidate_planner import plan_pattern_candidates
from builder_lab.patterns.candidate_resolver import (
    ResolvedPatternCandidatePack,
    resolve_pattern_candidate_pack,
)
from builder_lab.patterns.planner import CompositionPlanningError, plan_composition
from builder_lab.patterns.registry import load_builtin_registry
from builder_lab.patterns.resolver import ResolvedComposition, resolve_composition
from builder_lab.visual_models import VisualCritique, VisualSeverity
from builder_lab.validation import (
    issue_fingerprint,
    strip_reserved_runtime_attributes,
    validate_artifact,
)

if TYPE_CHECKING:
    from builder_lab.reference_pipeline import ReferenceAnalysisResult


logger = logging.getLogger(__name__)

DIRECT_STAGE_SEQUENCE = (
    "reference_analysis",
    "art_direction",
    "composition",
    "foundation",
    "identity",
    "conversation",
    "motion_polish",
)
ANTIGRAVITY_STAGE_SEQUENCE = ("reference_analysis", "agent_build")
STAGE_PUBLIC_NAMES = {
    "reference_analysis": "анализ исходного сайта",
    "art_direction": "выбор визуального направления",
    "composition": "выбор проверенной композиции",
    "foundation": "создание основы виджета",
    "identity": "настройка фирменного стиля",
    "conversation": "настройка диалога",
    "motion_polish": "анимации и финальная отделка",
    "agent_build": "агентская сборка виджета",
}
_CLAIM_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
RETRYABLE_ENGINE_ERRORS = frozenset({"quota_exceeded", "generation_timeout"})
MAX_STAGE_EXECUTIONS = 3
MAX_STAGE_RESULT_BYTES = 1_048_576
MAX_STAGE_CONTEXT_BYTES = 131_072
MAX_STAGE_OUTPUT_REFS = 32
MAX_STAGE_EVENTS = 64
ALLOWED_STAGE_RESULT_EVENTS = frozenset(
    {"reference.completed", "repair.completed"}
)

_PROVIDER_FAILURE_CODES = frozenset({
    "provider_permission_denied",
    "provider_unavailable",
    "model_unavailable",
    "agent_unavailable",
    "quota_exceeded",
    "model_not_found",
    "route_exhausted",
})
_INFRASTRUCTURE_FAILURE_CODES = frozenset({
    "generation_timeout",
    "reference_capture_failed",
    "reference_capture_incomplete",
    "reference_analysis_failed",
    "snapshot_download_failed",
})
_MODEL_OUTPUT_FAILURE_CODES = frozenset({
    "invalid_artifact",
    "invalid_structured_output",
    "invalid_response",
    "reference_analysis_invalid",
    "visual_quality_failed",
    "visual_review_inconclusive",
    "visual_review_invalid",
})
_CONTENT_FAILURE_CODES = frozenset({
    "content_blocked",
    "content_safety",
    "reference_analysis_too_large",
})
_VALIDATION_FAILURE_CODES = frozenset({
    "reference_url_unsafe",
    "snapshot_rejected",
})


def failure_category_for_error(error: BuilderEngineError) -> TrialFailureKind:
    """Classify a technical error without inspecting its public wording."""

    code = error.error_code
    if code in _PROVIDER_FAILURE_CODES:
        return TrialFailureKind.PROVIDER
    if code in _INFRASTRUCTURE_FAILURE_CODES:
        return TrialFailureKind.INFRASTRUCTURE
    if code in _MODEL_OUTPUT_FAILURE_CODES:
        return TrialFailureKind.MODEL_INVALID_OUTPUT
    if code in _CONTENT_FAILURE_CODES:
        return TrialFailureKind.CONTENT
    if code in _VALIDATION_FAILURE_CODES:
        return TrialFailureKind.VALIDATION
    return TrialFailureKind.PLATFORM


def _strict_json_clone(value: Any, *, field_name: str) -> Any:
    def validate(item: Any) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError(f"stage result {field_name} contains a non-finite number")
            return
        if isinstance(item, list):
            for child in item:
                validate(child)
            return
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError(f"stage result {field_name} has a non-string key")
            for child in item.values():
                validate(child)
            return
        raise ValueError(f"stage result {field_name} is not strict JSON")

    validate(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"stage result {field_name} is not strict JSON") from exc
    return json.loads(encoded)


def _visual_score_from_event(
    payload: dict[str, Any],
) -> tuple[float | None, str | None]:
    explicit = payload.get("visual_score_normalized")
    if (
        not isinstance(explicit, bool)
        and isinstance(explicit, (int, float))
        and math.isfinite(float(explicit))
        and 0 <= float(explicit) <= 1
    ):
        return float(explicit), "visual_score_normalized"
    diagnostic = payload.get("diagnostic")
    if not isinstance(diagnostic, str):
        return None, None
    try:
        raw = json.loads(diagnostic)
        if not isinstance(raw, dict):
            return None, None
        critique = VisualCritique.from_dict(raw)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None, None
    weights = {
        VisualSeverity.BLOCKER: 0.35,
        VisualSeverity.MAJOR: 0.20,
        VisualSeverity.MINOR: 0.05,
    }
    penalty = sum(
        weights[finding.severity] * finding.confidence
        for finding in critique.findings
    )
    return round(max(0.0, min(1.0, 1.0 - penalty)), 6), "findings_v1"


class LeaseLostError(RuntimeError):
    """The worker no longer owns the run lease."""


class RunCancelledError(RuntimeError):
    """Cancellation was requested before the checkpoint could be committed."""


@dataclass(frozen=True, slots=True)
class RunClaim:
    run_id: UUID
    project_id: UUID
    worker_id: str
    mode: str
    next_stage: str
    last_completed_stage: str | None
    lease_expires_at: datetime
    attempt_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class _TerminalRun:
    run_id: UUID


@dataclass(frozen=True, slots=True)
class StageResult:
    """All externally visible persisted output produced by one stage attempt."""

    public_message: str
    output_refs: tuple[str, ...] = ()
    artifact: WidgetArtifact | None = None
    request: BuilderRequest | None = None
    usage: TokenUsage = TokenUsage()
    events: tuple[dict, ...] = ()
    context: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.public_message, str):
            raise ValueError("stage result public_message is invalid")
        message = self.public_message.strip()
        if not message or len(message) > 2_000 or "\x00" in message:
            raise ValueError("stage result public_message is invalid")
        if any(not isinstance(ref, str) for ref in self.output_refs):
            raise ValueError("stage result output_refs are invalid")
        refs = tuple(ref.strip() for ref in self.output_refs)
        if len(refs) > MAX_STAGE_OUTPUT_REFS or any(
            not ref or len(ref) > 512 for ref in refs
        ):
            raise ValueError("stage result output_refs are invalid")
        if len(self.events) > MAX_STAGE_EVENTS:
            raise ValueError("stage result events are invalid")
        events = _strict_json_clone(list(self.events), field_name="events")
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("stage result events are invalid")
            event_type = event.get("event_type")
            event_message = event.get("message")
            if event_type not in ALLOWED_STAGE_RESULT_EVENTS:
                raise ValueError("stage result event type is not allowed")
            if (
                not isinstance(event_message, str)
                or not event_message.strip()
                or len(event_message) > 2_000
            ):
                raise ValueError("stage result events are invalid")
            if set(event) - {"event_type", "message", "status"}:
                raise ValueError("stage result events contain unsupported fields")
            if "status" in event and not isinstance(event["status"], str):
                raise ValueError("stage result events are invalid")
        context = _strict_json_clone(self.context, field_name="context")
        if not isinstance(context, dict):
            raise ValueError("stage result context must be an object")
        context_size = len(
            json.dumps(
                context, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode("utf-8")
        )
        if context_size > MAX_STAGE_CONTEXT_BYTES:
            raise ValueError("stage result context is too large")
        object.__setattr__(self, "public_message", message)
        object.__setattr__(self, "output_refs", refs)
        object.__setattr__(self, "events", tuple(events))
        object.__setattr__(self, "context", context)
        total_size = len(
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if total_size > MAX_STAGE_RESULT_BYTES:
            raise ValueError("stage result is too large")

    def to_dict(self) -> dict:
        return {
            "public_message": self.public_message,
            "output_refs": list(self.output_refs),
            "artifact": self.artifact.to_dict() if self.artifact is not None else None,
            "request": self.request.to_dict() if self.request is not None else None,
            "usage": self.usage.to_dict(),
            "events": list(self.events),
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "StageResult":
        artifact = payload.get("artifact")
        request = payload.get("request")
        return cls(
            public_message=str(payload.get("public_message", "")),
            output_refs=tuple(payload.get("output_refs", ())),
            artifact=(
                WidgetArtifact.from_dict(artifact)
                if isinstance(artifact, dict)
                else None
            ),
            request=(
                BuilderRequest.from_dict(request)
                if isinstance(request, dict)
                else None
            ),
            usage=TokenUsage.from_dict(payload.get("usage")),
            events=tuple(payload.get("events", ())),
            context=payload.get("context", {}),
        )


@dataclass(frozen=True, slots=True)
class StageInput:
    request: BuilderRequest
    previous_artifact: WidgetArtifact | None = None
    context: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _VisualSnapshot:
    cancel_requested: bool


class DurableVisualStore:
    """Persist visual audit evidence without advancing the durable checkpoint."""

    def __init__(self, queue: "PostgresWorkerQueue", claim: RunClaim) -> None:
        self._queue = queue
        self._claim = claim

    def _assert_run(self, run_id: str) -> None:
        if run_id != str(self._claim.run_id):
            raise ValueError("visual store run does not match the worker claim")

    async def snapshot(self, run_id: str) -> _VisualSnapshot:
        self._assert_run(run_id)
        return _VisualSnapshot(
            cancel_requested=await self._queue.cancellation_requested(
                self._claim.run_id
            )
        )

    async def append_event(self, run_id: str, **event: Any) -> None:
        self._assert_run(run_id)
        await self._queue.append_attempt_event(self._claim, **event)

    async def stage_visual_candidate(
        self, run_id: str, artifact: WidgetArtifact
    ) -> None:
        self._assert_run(run_id)
        if artifact.stage is not Stage.MOTION_POLISH:
            raise ValueError("visual candidate must be motion_polish")

    async def stage_visual_draft(
        self, run_id: str, artifact: WidgetArtifact
    ) -> None:
        self._assert_run(run_id)
        await self._queue.stage_visual_draft(self._claim, artifact)


class OrchestratorStageHandler:
    """Execute exactly the stage named by a durable worker claim."""

    def __init__(
        self,
        *,
        queue: "PostgresWorkerQueue",
        engine_factories: dict[EngineName, Callable[[], BuilderEngine]],
        reference_analyzer: Callable[[str], Awaitable[ReferenceAnalysisResult]],
        visual_gate_factory: Callable[[RunClaim], Any] | None = None,
        routed_engine_factory: Callable[[RunClaim, str], BuilderEngine] | None = None,
        routed_reference_analyzer: Callable[
            [RunClaim, str], Awaitable[ReferenceAnalysisResult]
        ] | None = None,
        pattern_candidate_plan_v2_enabled: bool = False,
    ) -> None:
        self._queue = queue
        self._factories = dict(engine_factories)
        self._reference_analyzer = reference_analyzer
        self._visual_gate_factory = visual_gate_factory
        self._routed_engine_factory = routed_engine_factory
        self._routed_reference_analyzer = routed_reference_analyzer
        self._pattern_candidate_plan_v2_enabled = bool(
            pattern_candidate_plan_v2_enabled
        )

    async def _candidate_plan(self, run_id: UUID):
        """Load a durable candidate plan without consulting the selector."""

        if not self._pattern_candidate_plan_v2_enabled:
            return None
        loader = getattr(self._queue, "load_pattern_candidate_plan", None)
        if not callable(loader):
            raise BuilderEngineError(
                "internal_error",
                "Хранилище плана кандидатов не настроено",
            )
        return await loader(run_id)

    async def _resolve_candidate_pack(
        self,
        *,
        claim: RunClaim,
        stage: Stage,
    ) -> ResolvedPatternCandidatePack:
        persisted = await self._candidate_plan(claim.run_id)
        if persisted is None:
            raise BuilderEngineError(
                "internal_error",
                "Сначала необходимо сохранить план кандидатов",
                diagnostic=f"missing candidate plan for {claim.run_id}",
            )
        review_loader = getattr(
            self._queue, "load_effective_pattern_candidate_reviews", None
        )
        if not callable(review_loader):
            raise BuilderEngineError(
                "internal_error",
                "Хранилище плана кандидатов не настроено",
            )
        registry = load_builtin_atomic_registry()
        approved = await review_loader(registry)
        try:
            return resolve_pattern_candidate_pack(
                persisted.plan,
                stage,
                registry,
                effective_approved=approved,
            )
        except ValueError as exc:
            raise BuilderEngineError(
                "internal_error",
                "Сохранённый план кандидатов повреждён",
                diagnostic=str(exc),
            ) from exc

    async def __call__(self, claim: RunClaim) -> StageResult:
        stage_input = await self._queue.stage_input(claim)
        policy = get_mode_policy(claim.mode)
        request = policy.apply_to_request(stage_input.request)
        if claim.next_stage == "reference_analysis":
            return await self._analyze_reference(request, claim)
        try:
            stage = Stage(claim.next_stage)
        except ValueError as exc:
            raise BuilderEngineError(
                "internal_error",
                "В очереди обнаружен неподдерживаемый этап генерации",
                diagnostic=claim.next_stage,
            ) from exc
        factory = self._factories.get(request.engine)
        if factory is None and self._routed_engine_factory is None:
            raise BuilderEngineError(
                "provider_unavailable",
                "Выбранный режим генерации сейчас недоступен",
            )
        role = policy.model_role_for(claim.next_stage)
        engine = (
            self._routed_engine_factory(claim, role)
            if self._routed_engine_factory is not None and request.engine is EngineName.DIRECT
            else factory()  # type: ignore[misc]
        )
        repair_engine = (
            self._routed_engine_factory(claim, "repair")
            if self._routed_engine_factory is not None and request.engine is EngineName.DIRECT
            else engine
        )
        direction_proposal_engine = engine
        direction_judge_engine = engine
        if (
            stage is Stage.ART_DIRECTION
            and self._routed_engine_factory is not None
            and request.engine is EngineName.DIRECT
        ):
            direction_proposal_engine = self._routed_engine_factory(
                claim,
                "direction_candidate",
            )
            direction_judge_engine = self._routed_engine_factory(
                claim,
                "direction_judge",
            )
        try:
            return await self._generate_stage(
                request=request,
                engine=engine,
                repair_engine=repair_engine,
                direction_proposal_engine=direction_proposal_engine,
                direction_judge_engine=direction_judge_engine,
                stage=stage,
                previous=stage_input.previous_artifact,
                context=stage_input.context,
                claim=claim,
            )
        finally:
            closed: set[int] = set()
            for routed_engine in (
                engine,
                repair_engine,
                direction_proposal_engine,
                direction_judge_engine,
            ):
                identity = id(routed_engine)
                if identity in closed:
                    continue
                closed.add(identity)
                await routed_engine.close()

    async def _analyze_reference(
        self,
        request: BuilderRequest,
        claim: RunClaim,
    ) -> StageResult:
        # Browser crawling belongs to the isolated builder image.  Keep the
        # import on the execution path so the production API can enqueue and
        # cancel runs without installing Pillow, Crawlee, or Chromium.
        from builder_lab.reference_pipeline import (
            ReferenceAnalysisResult,
            ReferencePipelineError,
        )

        if not request.source_url or request.reference_context:
            return StageResult(
                public_message="Анализ исходного сайта завершён",
                request=request,
            )
        try:
            analysis = (
                await self._routed_reference_analyzer(claim, request.source_url)
                if self._routed_reference_analyzer is not None
                else await self._reference_analyzer(request.source_url)
            )
        except ReferencePipelineError as exc:
            raise BuilderEngineError(
                exc.error_code,
                exc.public_message,
                diagnostic=exc.diagnostic,
                usage=exc.usage,
            ) from exc
        if not isinstance(analysis, ReferenceAnalysisResult):
            raise BuilderEngineError(
                "reference_analysis_invalid",
                "Автоматический анализ сайта вернул некорректный результат",
            )
        grounded = replace(request, reference_context=analysis.context)
        return StageResult(
            public_message="Визуальный стиль и подтверждённые факты сайта собраны",
            request=grounded,
            usage=analysis.usage,
            events=(
                {
                    "event_type": "reference.completed",
                    "status": "completed",
                    "message": "Анализ исходного сайта завершён",
                },
            ),
        )

    async def _generate_stage(
        self,
        *,
        request: BuilderRequest,
        engine: BuilderEngine,
        repair_engine: BuilderEngine,
        direction_proposal_engine: BuilderEngine,
        direction_judge_engine: BuilderEngine,
        stage: Stage,
        previous: WidgetArtifact | None,
        context: dict,
        claim: RunClaim,
    ) -> StageResult:
        # The orchestrator imports the visual/browser audit stack.  Only a
        # worker executes it; importing PostgresWorkerQueue in the API process
        # must stay browser-dependency-free.
        from builder_lab.orchestrator import BuilderOrchestrator

        usage = TokenUsage()
        selected_direction: DirectionProposal | None = None
        composition: ResolvedComposition | None = None
        next_context = dict(context)
        if request.engine is EngineName.DIRECT:
            if stage is Stage.ART_DIRECTION:
                board = await run_direction_board(
                    proposal_engine=direction_proposal_engine,  # type: ignore[arg-type]
                    judge_engine=direction_judge_engine,  # type: ignore[arg-type]
                    request=request,
                )
                selected_direction = board.selected
                usage = usage + board.usage
                next_context["selected_direction"] = selected_direction.to_dict()
            else:
                raw_direction = next_context.get("selected_direction")
                if not isinstance(raw_direction, dict):
                    raise BuilderEngineError(
                        "internal_error",
                        "Не найдено выбранное визуальное направление",
                    )
                selected_direction = DirectionProposal.from_dict(raw_direction)
            if stage is Stage.COMPOSITION:
                if self._pattern_candidate_plan_v2_enabled:
                    persisted = await self._candidate_plan(claim.run_id)
                    if persisted is not None:
                        # A staged composition may be replayed after a worker
                        # restart.  The database plan is canonical; never run
                        # the selector again or replace its exact versions.
                        next_context["pattern_candidate_plan"] = (
                            persisted.plan.to_dict()
                        )
                        next_context.pop("composition_plan", None)
                        return StageResult(
                            public_message=persisted.plan.summary,
                            usage=usage,
                            context=next_context,
                        )
                    registry = load_builtin_atomic_registry()
                    effective_approved: set[tuple[str, int]] | None = None
                    selector_catalog = registry.selector_catalog()
                    review_loader = getattr(
                        self._queue, "load_effective_pattern_candidate_reviews", None
                    )
                    if callable(review_loader):
                        effective_approved = await review_loader(registry)
                        selector_catalog = registry.selector_catalog(
                            effective_review_states={
                                key: "approved" if key in effective_approved else "rejected"
                                for key in (
                                    (item.pattern_id, item.version)
                                    for item in registry.definitions
                                )
                            }
                        )
                    try:
                        planned = await plan_pattern_candidates(
                            cast(DirectBuilderEngine, engine),
                            request,
                            selected_direction,
                            registry,
                            selector_catalog=selector_catalog,
                            effective_approved=effective_approved,
                        )
                    except Exception as exc:
                        raise BuilderEngineError(
                            "invalid_structured_output",
                            "Не удалось подобрать кандидатов для виджета",
                            diagnostic=str(exc),
                            usage=getattr(exc, "usage", TokenUsage()),
                        ) from exc
                    next_context["pattern_candidate_plan"] = planned.plan.to_dict()
                    next_context["pattern_candidate_selector_request_ids"] = list(
                        planned.provider_request_ids
                    )
                    return StageResult(
                        public_message=planned.plan.summary,
                        output_refs=planned.provider_request_ids,
                        usage=usage + planned.usage,
                        context=next_context,
                    )
                registry = load_builtin_registry()
                try:
                    planned = await plan_composition(
                        cast(DirectBuilderEngine, engine),
                        request,
                        selected_direction,
                        registry,
                    )
                except CompositionPlanningError as exc:
                    raise BuilderEngineError(
                        "invalid_structured_output",
                        "Не удалось подобрать проверенную композицию виджета",
                        diagnostic=str(exc),
                        usage=exc.usage,
                    ) from exc
                next_context["composition_plan"] = planned.plan.to_dict()
                return StageResult(
                    public_message=planned.plan.summary,
                    output_refs=planned.provider_request_ids,
                    usage=usage + planned.usage,
                    context=next_context,
                )
            if stage is not Stage.ART_DIRECTION and not self._pattern_candidate_plan_v2_enabled:
                raw_plan = next_context.get("composition_plan")
                if not isinstance(raw_plan, dict):
                    raise BuilderEngineError(
                        "internal_error",
                        "Не найден сохранённый план композиции",
                    )
                try:
                    composition = resolve_composition(
                        CompositionPlan.from_dict(raw_plan),
                        load_builtin_registry(),
                    )
                except ValueError as exc:
                    raise BuilderEngineError(
                        "internal_error",
                        "Сохранённый план композиции повреждён",
                        diagnostic=str(exc),
                    ) from exc
        pattern_candidate_pack: ResolvedPatternCandidatePack | None = None
        if (
            self._pattern_candidate_plan_v2_enabled
            and request.engine is EngineName.DIRECT
            and stage
            in {
                Stage.FOUNDATION,
                Stage.IDENTITY,
                Stage.CONVERSATION,
                Stage.MOTION_POLISH,
            }
        ):
            pattern_candidate_pack = await self._resolve_candidate_pack(
                claim=claim,
                stage=stage,
            )
        revision = (previous.revision if previous is not None else 0) + 1
        if request.engine is EngineName.ANTIGRAVITY:
            await self._queue.arm_provider_dispatch(
                claim,
                provider="antigravity",
            )
        result = await BuilderOrchestrator.execute_stage(
            request=request,
            engine=engine,
            stage=stage,
            revision=revision,
            previous_artifact=previous,
            selected_direction=selected_direction,
            composition=composition,
            pattern_candidate_pack=pattern_candidate_pack,
        )
        usage = usage + result.usage
        output_refs = [
            result.provider_request_id
        ] if result.provider_request_id is not None else []
        candidate = strip_reserved_runtime_attributes(result.artifact)
        previous_revision = previous.revision if previous is not None else 0
        issues = validate_artifact(candidate, previous_revision=previous_revision)
        seen = {issue_fingerprint(issues)}
        repair_events: list[dict] = []
        for attempt in range(1, request.max_repairs + 1):
            if not issues:
                break
            repaired = await BuilderOrchestrator.execute_stage(
                request=request,
                engine=repair_engine,
                stage=stage,
                revision=revision,
                previous_artifact=candidate,
                selected_direction=selected_direction,
                repair_issues=issues,
                composition=composition,
                pattern_candidate_pack=pattern_candidate_pack,
            )
            usage = usage + repaired.usage
            if repaired.provider_request_id is not None:
                output_refs.append(repaired.provider_request_id)
            candidate = strip_reserved_runtime_attributes(repaired.artifact)
            repair_events.append(
                {
                    "event_type": "repair.completed",
                    "status": "completed",
                    "message": f"Модель завершила исправление {attempt}",
                }
            )
            issues = validate_artifact(
                candidate,
                previous_revision=previous_revision,
            )
            fingerprint = issue_fingerprint(issues)
            if issues and fingerprint in seen:
                break
            seen.add(fingerprint)
        if issues:
            raise BuilderEngineError(
                "invalid_artifact",
                "Артефакт не прошёл безопасную проверку",
                diagnostic="; ".join(issue.code for issue in issues),
            )
        if stage is Stage.MOTION_POLISH:
            if (
                self._visual_gate_factory is None
                or previous is None
                or selected_direction is None
            ):
                raise BuilderEngineError(
                    "provider_unavailable",
                    "Финальная визуальная проверка не настроена",
                )
            visual_gate = self._visual_gate_factory(claim)
            candidate = await visual_gate.evaluate(
                run_id=str(claim.run_id),
                request=request,
                engine=cast(Any, repair_engine),
                candidate=candidate,
                previous=previous,
                selected_direction=selected_direction,
                composition=composition,
                pattern_candidate_pack=pattern_candidate_pack,
            )
        return StageResult(
            public_message=(
                candidate.change_summary
                or f"Завершён этап: {STAGE_PUBLIC_NAMES[stage.value]}"
            ),
            output_refs=tuple(output_refs),
            artifact=candidate,
            usage=usage,
            events=tuple(repair_events),
            context=next_context,
        )


class PostgresWorkerQueue:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        lease_seconds: float = 60.0,
        retry_backoff_seconds: float = 5.0,
        forensic_recorder: GenerationForensicRecorder | None = None,
    ) -> None:
        if lease_seconds <= 0 or retry_backoff_seconds <= 0:
            raise ValueError("worker timing values must be positive")
        self._sessions = session_factory
        self.lease_seconds = float(lease_seconds)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self._forensic_recorder = forensic_recorder
        bind = session_factory.kw.get("bind")
        self._dialect_name = bind.dialect.name if bind is not None else ""
        namespace = id(bind)
        lock = _CLAIM_LOCKS.get(namespace)
        if lock is None:
            lock = asyncio.Lock()
            _CLAIM_LOCKS[namespace] = lock
        self._claim_lock = lock

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    async def load_pattern_candidate_plan(self, run_id: UUID):
        """Read the run's immutable selector plan through the queue boundary."""

        async with self._sessions() as database:
            return await PatternCandidateRepository(database).load_plan(run_id)

    async def load_effective_pattern_candidate_reviews(
        self,
        registry,
    ) -> set[tuple[str, int]]:
        """Return effective approvals for the exact atomic registry versions."""

        approved: set[tuple[str, int]] = set()
        # The session must remain open while effective review rows are queried;
        # use a fresh context rather than leaking the session across stages.
        async with self._sessions() as database:
            persisted_rows = (
                await database.execute(
                    select(
                        WidgetPatternVersion.id,
                        WidgetPatternVersion.pattern_id,
                        WidgetPatternVersion.version,
                    )
                )
            ).all()
            persisted = {
                (str(pattern_id), int(version)): pattern_version_id
                for pattern_version_id, pattern_id, version in persisted_rows
            }
            repository = PatternCandidateRepository(database)
            for definition in registry.definitions:
                key = (definition.pattern_id, definition.version)
                if key in persisted:
                    state = await repository.effective_review_state(persisted[key])
                else:
                    state = definition.provenance.get("review_state")
                if state == "approved":
                    approved.add(key)
        return approved

    @staticmethod
    def _validate_worker_id(worker_id: str) -> str:
        if not isinstance(worker_id, str):
            raise ValueError("worker_id must be text")
        normalized = worker_id.strip()
        if not normalized or len(normalized) > 128 or "\x00" in normalized:
            raise ValueError("worker_id is invalid")
        return normalized

    @staticmethod
    def _validate_service_identity(name: str, value: str, *, maximum: int) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text")
        normalized = value.strip()
        if not normalized or len(normalized) > maximum or "\x00" in normalized:
            raise ValueError(f"{name} is invalid")
        return normalized

    async def publish_service_heartbeat(
        self,
        *,
        worker_id: str,
        boot_id: str,
        deployment_id: str,
        image_identity: str,
        started_at: datetime,
    ) -> None:
        """Upsert the O(1) singleton lease used by application readiness."""

        worker_id = self._validate_worker_id(worker_id)
        boot_id = self._validate_service_identity("boot_id", boot_id, maximum=128)
        deployment_id = self._validate_service_identity(
            "deployment_id", deployment_id, maximum=128
        )
        image_identity = self._validate_service_identity(
            "image_identity", image_identity, maximum=512
        )
        if not isinstance(started_at, datetime):
            raise ValueError("started_at must be a datetime")
        started_at = self._utc(started_at)
        async with self._sessions() as database, database.begin():
            now = self._now()
            lease = await database.get(
                WorkerServiceLease,
                "builder",
                with_for_update=self._dialect_name != "sqlite",
            )
            if lease is None:
                database.add(
                    WorkerServiceLease(
                        service_name="builder",
                        worker_id=worker_id,
                        boot_id=boot_id,
                        deployment_id=deployment_id,
                        image_identity=image_identity,
                        started_at=started_at,
                        heartbeat_at=now,
                    )
                )
                return
            lease.worker_id = worker_id
            lease.boot_id = boot_id
            lease.deployment_id = deployment_id
            lease.image_identity = image_identity
            lease.started_at = started_at
            lease.heartbeat_at = now

    async def activate_forensics(self, claim: RunClaim) -> bool:
        """Materialize private evidence before any stage/provider work begins."""

        if self._forensic_recorder is None:
            return False
        async with self._sessions() as database, database.begin():
            run = await database.scalar(
                select(GenerationRun)
                .where(GenerationRun.id == claim.run_id)
                .with_for_update()
            )
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, self._now())
            return await self._forensic_recorder.activate(database, run)

    @staticmethod
    def stage_sequence(mode: str) -> tuple[str, ...]:
        return get_mode_policy(mode).stage_sequence

    @classmethod
    def next_stage(cls, mode: str, last_completed_stage: str | None) -> str | None:
        stages = cls.stage_sequence(mode)
        if last_completed_stage is None:
            return stages[0]
        try:
            index = stages.index(last_completed_stage)
        except ValueError as exc:
            raise RuntimeError(
                f"run has unsupported checkpoint {last_completed_stage!r}"
            ) from exc
        return stages[index + 1] if index + 1 < len(stages) else None

    @staticmethod
    def claim_statement(
        now: datetime,
    ):
        available_lease = or_(
            GenerationRun.lease_owner.is_(None),
            GenerationRun.lease_expires_at.is_(None),
            GenerationRun.lease_expires_at <= now,
        )
        retry_ready = or_(
            GenerationRun.retry_not_before.is_(None),
            GenerationRun.retry_not_before <= now,
        )
        return (
            select(GenerationRun)
            .where(
                GenerationRun.state.in_(("queued", "running")),
                available_lease,
                retry_ready,
            )
            .order_by(GenerationRun.created_at, GenerationRun.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )

    @asynccontextmanager
    async def _claim_guard(self):
        if self._dialect_name == "sqlite":
            async with self._claim_lock:
                yield
        else:
            yield

    def _lease_expiry(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.lease_seconds)

    @staticmethod
    async def _cancel_requested(database: AsyncSession, run_id: UUID) -> bool:
        result = await database.execute(
            select(GenerationEvent.id)
            .where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.event_type == "run.cancel_requested",
            )
            .limit(1)
        )
        return result.first() is not None

    async def _append_event(
        self,
        database: AsyncSession,
        run: GenerationRun,
        *,
        event_type: str,
        message: str,
        now: datetime,
        payload: dict,
        forensic_payload: Mapping[str, object] | None = None,
        forensic_blobs: tuple[ForensicBlob, ...] = (),
        terminal_state: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        inferred_terminal = {
            "run.completed": "completed",
            "run.failed": "failed",
            "run.cancelled": "cancelled",
        }.get(event_type)
        if inferred_terminal is not None:
            if terminal_state is not None and terminal_state != inferred_terminal:
                raise ValueError("terminal event state conflicts with its type")
            terminal_state = inferred_terminal
            finished_at = finished_at or run.finished_at
            if finished_at is None:
                raise ValueError("terminal event requires persisted finished_at")
            finished_at = self._utc(finished_at)
        elif event_type == "run.terminal_marked" and terminal_state is None:
            raise ValueError("run.terminal_marked requires terminal state")
        sequence = run.next_event_sequence
        operational_payload = {
            "run_id": str(run.id),
            "sequence": sequence,
            "timestamp": now.isoformat(),
            "type": event_type,
            "message": message,
            **payload,
        }
        visual_score, score_source = _visual_score_from_event(operational_payload)
        if visual_score is not None:
            operational_payload["visual_score_normalized"] = visual_score
            operational_payload["visual_score_source"] = score_source
        private_payload = dict(forensic_payload or {})
        for private_field in (
            "stage",
            "worker_id",
            "attempt_id",
            "attempt",
            "max_executions",
            "provider",
            "provider_dispatch_count",
        ):
            if private_field in operational_payload:
                private_payload.setdefault(
                    private_field,
                    operational_payload[private_field],
                )
        prepared = prepare_generation_event(
            event_type=event_type,
            public_message=message,
            operational_payload=operational_payload,
            forensic_payload=private_payload,
        )
        run.next_event_sequence += 1
        forensic_ref = None
        if self._forensic_recorder is not None:
            result = await self._forensic_recorder.record_event(
                database,
                run,
                sequence=sequence,
                prepared=prepared,
                forensic_blobs=forensic_blobs,
                created_at=now,
                terminal_state=terminal_state,
                finished_at=finished_at,
            )
            if result is not None and not result.degraded and result.entry is not None:
                forensic_ref = result.entry.relative_path
        values = {
            "run_id": run.id,
            "sequence": sequence,
            "event_type": prepared.event_type.value,
            "public_message": prepared.public_message,
            "payload": prepared.operational_payload,
            "registry_version": REGISTRY_VERSION,
            "public_payload": prepared.public_payload,
            "forensic_ref": forensic_ref,
            "created_at": now,
        }
        if database.get_bind().dialect.name == "sqlite":
            values["id"] = secrets.randbits(62)
        database.add(GenerationEvent(**values))

    @staticmethod
    def _claim_from_run(run: GenerationRun, *, attempt_id: UUID) -> RunClaim:
        if run.lease_owner is None or run.lease_expires_at is None:
            raise RuntimeError("claimed run has no active lease")
        next_stage = PostgresWorkerQueue.next_stage(
            run.mode, run.last_completed_stage
        )
        if next_stage is None:
            raise RuntimeError("completed run was left in the worker queue")
        return RunClaim(
            run_id=run.id,
            project_id=run.project_id,
            worker_id=run.lease_owner,
            mode=run.mode,
            next_stage=next_stage,
            last_completed_stage=run.last_completed_stage,
            lease_expires_at=PostgresWorkerQueue._utc(run.lease_expires_at),
            attempt_id=attempt_id,
        )

    @staticmethod
    async def _latest_stage_started_record(
        database: AsyncSession,
        run_id: UUID,
        stage: str,
    ) -> GenerationEvent:
        records = (
            await database.execute(
                select(GenerationEvent)
                .where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.event_type == "stage.started",
                )
                .order_by(GenerationEvent.sequence.desc())
                .limit(20)
            )
        ).scalars().all()
        record = next(
            (record for record in records if record.payload.get("stage") == stage),
            None,
        )
        if record is None:
            raise LeaseLostError(f"run {run_id} has no valid stage attempt")
        return record

    @classmethod
    async def _latest_attempt_id(
        cls, database: AsyncSession, run_id: UUID, stage: str
    ) -> UUID:
        record = await cls._latest_stage_started_record(database, run_id, stage)
        try:
            return UUID(str(record.payload["attempt_id"]))
        except (KeyError, TypeError, ValueError):
            pass
        raise LeaseLostError(f"run {run_id} has no valid stage attempt")

    @classmethod
    async def _stage_attempt(
        cls,
        database: AsyncSession,
        *,
        run_id: UUID,
        stage: str,
        attempt_id: UUID,
    ) -> GenerationStageAttempt:
        started = await cls._latest_stage_started_record(
            database,
            run_id,
            stage,
        )
        try:
            started_attempt_id = UUID(str(started.payload["attempt_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise LeaseLostError(f"run {run_id} has no valid stage attempt") from exc
        if started_attempt_id != attempt_id:
            raise LeaseLostError(f"attempt fence moved for run {run_id}")

        attempt = await database.get(
            GenerationStageAttempt,
            attempt_id,
            with_for_update=True,
        )
        if attempt is not None:
            if attempt.run_id != run_id or attempt.stage != stage:
                raise LeaseLostError(f"run {run_id} has no matching stage attempt")
            return attempt

        attempt = GenerationStageAttempt(
            id=attempt_id,
            run_id=run_id,
            stage=stage,
            ordinal=await cls._next_stage_attempt_ordinal(
                database,
                run_id=run_id,
                stage=stage,
            ),
            status="running",
            started_at=cls._utc(started.created_at),
        )
        database.add(attempt)
        return attempt

    @classmethod
    async def _transition_stage_attempt(
        cls,
        database: AsyncSession,
        *,
        run_id: UUID,
        stage: str,
        attempt_id: UUID,
        status: str,
        now: datetime,
        expected_statuses: tuple[str, ...] = ("running", "result_staged"),
    ) -> GenerationStageAttempt:
        attempt = await cls._stage_attempt(
            database,
            run_id=run_id,
            stage=stage,
            attempt_id=attempt_id,
        )
        if attempt.status not in expected_statuses:
            raise LeaseLostError(f"attempt fence moved for run {run_id}")
        attempt.status = status
        attempt.finished_at = (
            None if status in {"running", "result_staged"} else now
        )
        return attempt

    @staticmethod
    async def _next_stage_attempt_ordinal(
        database: AsyncSession,
        *,
        run_id: UUID,
        stage: str,
    ) -> int:
        highest = await database.scalar(
            select(func.max(GenerationStageAttempt.ordinal)).where(
                GenerationStageAttempt.run_id == run_id,
                GenerationStageAttempt.stage == stage,
            )
        )
        return int(highest or 0) + 1

    @classmethod
    async def _start_stage_attempt(
        cls,
        database: AsyncSession,
        run: GenerationRun,
        *,
        stage: str,
        attempt_id: UUID,
        now: datetime,
    ) -> GenerationStageAttempt:
        # Every caller holds the GenerationRun row lock, which serializes both
        # interruption of the old active attempt and 1-based ordinal allocation.
        active_attempts = (
            await database.execute(
                select(GenerationStageAttempt)
                .where(
                    GenerationStageAttempt.run_id == run.id,
                    GenerationStageAttempt.stage == stage,
                    GenerationStageAttempt.status.in_(
                        ("running", "result_staged")
                    ),
                )
                .with_for_update()
            )
        ).scalars().all()
        for active in active_attempts:
            active.status = "interrupted"
            active.finished_at = now
        attempt = GenerationStageAttempt(
            id=attempt_id,
            run_id=run.id,
            stage=stage,
            ordinal=await cls._next_stage_attempt_ordinal(
                database,
                run_id=run.id,
                stage=stage,
            ),
            status="running",
            started_at=now,
        )
        database.add(attempt)
        return attempt

    @classmethod
    async def _recover_staged_attempt(
        cls,
        database: AsyncSession,
        run: GenerationRun,
        *,
        stage: str,
        staged: GenerationEvent,
        now: datetime,
    ) -> UUID:
        try:
            attempt_id = UUID(str(staged.payload["attempt_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise LeaseLostError(
                f"run {run.id} has no valid staged attempt"
            ) from exc
        attempt = await database.get(
            GenerationStageAttempt,
            attempt_id,
            with_for_update=True,
        )
        if attempt is not None:
            if (
                attempt.run_id != run.id
                or attempt.stage != stage
                or attempt.status != "result_staged"
            ):
                raise LeaseLostError(f"attempt fence moved for run {run.id}")
            return attempt_id

        started_at = now
        started_records = (
            await database.execute(
                select(GenerationEvent)
                .where(
                    GenerationEvent.run_id == run.id,
                    GenerationEvent.event_type == "stage.started",
                )
                .order_by(GenerationEvent.sequence.desc())
                .limit(20)
            )
        ).scalars().all()
        for record in started_records:
            if (
                record.payload.get("stage") == stage
                and record.payload.get("attempt_id") == str(attempt_id)
            ):
                started_at = cls._utc(record.created_at)
                break
        database.add(
            GenerationStageAttempt(
                id=attempt_id,
                run_id=run.id,
                stage=stage,
                ordinal=await cls._next_stage_attempt_ordinal(
                    database,
                    run_id=run.id,
                    stage=stage,
                ),
                status="result_staged",
                started_at=started_at,
            )
        )
        return attempt_id

    @staticmethod
    async def _ambiguous_dispatched_claim(
        database: AsyncSession,
        run: GenerationRun,
        stage: str,
    ) -> RunClaim | None:
        started_records = (
            await database.execute(
                select(GenerationEvent)
                .where(
                    GenerationEvent.run_id == run.id,
                    GenerationEvent.event_type == "stage.started",
                )
                .order_by(GenerationEvent.sequence.desc())
                .limit(20)
            )
        ).scalars().all()
        started = next(
            (
                record
                for record in started_records
                if record.payload.get("stage") == stage
            ),
            None,
        )
        if started is None:
            return None
        try:
            attempt_id = UUID(str(started.payload["attempt_id"]))
            worker_id = PostgresWorkerQueue._validate_worker_id(
                started.payload["worker_id"]
            )
        except (KeyError, TypeError, ValueError):
            return None
        dispatched_count = await PostgresWorkerQueue._provider_dispatch_count(
            database,
            run.id,
        )
        baseline = started.payload.get("provider_dispatch_count")
        if (
            not isinstance(baseline, bool)
            and isinstance(baseline, int)
            and baseline >= 0
        ):
            ambiguous = dispatched_count > baseline
        else:
            # Events created before dispatch baselines were introduced cannot be
            # correlated safely. Any paid call makes the expired attempt ambiguous.
            ambiguous = dispatched_count > 0
        if not ambiguous:
            return None
        if run.lease_expires_at is None:
            return None
        return RunClaim(
            run_id=run.id,
            project_id=run.project_id,
            worker_id=worker_id,
            mode=run.mode,
            next_stage=stage,
            last_completed_stage=run.last_completed_stage,
            lease_expires_at=PostgresWorkerQueue._utc(run.lease_expires_at),
            attempt_id=attempt_id,
        )

    @staticmethod
    async def _provider_dispatch_count(
        database: AsyncSession,
        run_id: UUID,
    ) -> int:
        model_calls = int(
            await database.scalar(
                select(func.count())
                .select_from(ModelCall)
                .where(
                    ModelCall.run_id == run_id,
                    ModelCall.provider_dispatched.is_(True),
                    ModelCall.role != "chat_visitor",
                )
            )
            or 0
        )
        dispatch_receipts = int(
            await database.scalar(
                select(func.count())
                .select_from(GenerationEvent)
                .where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.event_type == "provider.dispatch_armed",
                )
            )
            or 0
        )
        return model_calls + dispatch_receipts

    async def arm_provider_dispatch(
        self,
        claim: RunClaim,
        *,
        provider: str,
    ) -> None:
        """Persist dispatch intent before a non-router provider can incur cost."""

        provider = provider.strip()
        if not provider or len(provider) > 64:
            raise ValueError("provider is invalid")
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            receipt_records = (
                await database.execute(
                    select(GenerationEvent)
                    .where(
                        GenerationEvent.run_id == claim.run_id,
                        GenerationEvent.event_type == "provider.dispatch_armed",
                    )
                    .order_by(GenerationEvent.sequence.desc())
                    .limit(20)
                )
            ).scalars().all()
            if any(
                record.payload.get("stage") == claim.next_stage
                and record.payload.get("attempt_id") == str(claim.attempt_id)
                and record.payload.get("provider") == provider
                for record in receipt_records
            ):
                return
            await self._append_event(
                database,
                run,
                event_type="provider.dispatch_armed",
                message="Отправка провайдеру зафиксирована",
                now=now,
                payload={
                    "stage": claim.next_stage,
                    "status": "armed",
                    "worker_id": claim.worker_id,
                    "attempt_id": str(claim.attempt_id),
                    "provider": provider,
                },
            )

    @staticmethod
    def _assert_live_lease(
        run: GenerationRun,
        *,
        worker_id: str,
        now: datetime,
    ) -> None:
        expires_at = (
            PostgresWorkerQueue._utc(run.lease_expires_at)
            if run.lease_expires_at is not None
            else None
        )
        if (
            run.state != "running"
            or run.lease_owner != worker_id
            or expires_at is None
            or expires_at <= now
        ):
            raise LeaseLostError(f"worker {worker_id!r} lost lease for run {run.id}")

    async def claim(self, worker_id: str) -> RunClaim | _TerminalRun | None:
        worker_id = self._validate_worker_id(worker_id)
        async with self._claim_guard():
            async with self._sessions() as database, database.begin():
                now = self._now()
                run = (
                    await database.execute(
                        self.claim_statement(now)
                    )
                ).scalar_one_or_none()
                if run is None:
                    return None
                next_stage = self.next_stage(run.mode, run.last_completed_stage)
                if next_stage is None:
                    run.state = "completed"
                    run.progress = 100
                    run.finished_at = run.finished_at or now
                    run.lease_owner = None
                    run.lease_expires_at = None
                    await self._append_event(
                        database,
                        run,
                        event_type="run.terminal_marked",
                        message="Генерация завершена по сохранённому checkpoint",
                        now=now,
                        payload={"status": "completed"},
                        terminal_state="completed",
                        finished_at=self._utc(run.finished_at),
                    )
                    return _TerminalRun(run_id=run.id)
                expired_attempt = (
                    run.lease_owner is not None
                    and run.lease_expires_at is not None
                    and self._utc(run.lease_expires_at) <= now
                )
                staged = await self._staged_result_record(
                    database,
                    run.id,
                    next_stage,
                )
                if expired_attempt:
                    ambiguous_claim = (
                        await self._ambiguous_dispatched_claim(
                            database,
                            run,
                            next_stage,
                        )
                        if staged is None
                        else None
                    )
                    if ambiguous_claim is not None:
                        await self._fail_locked(
                            database,
                            run,
                            ambiguous_claim,
                            BuilderEngineError(
                                "provider_dispatch_ambiguous",
                                "Generation stopped because the provider result could not "
                                "be reconciled safely",
                            ),
                            now,
                        )
                        return _TerminalRun(run_id=run.id)
                run.state = "running"
                run.current_stage = next_stage
                run.started_at = run.started_at or now
                run.lease_owner = worker_id
                run.heartbeat_at = now
                run.lease_expires_at = self._lease_expiry(now)
                if staged is None:
                    attempt_id = uuid4()
                    await self._start_stage_attempt(
                        database,
                        run,
                        stage=next_stage,
                        attempt_id=attempt_id,
                        now=now,
                    )
                else:
                    attempt_id = await self._recover_staged_attempt(
                        database,
                        run,
                        stage=next_stage,
                        staged=staged,
                        now=now,
                    )
                provider_dispatch_count = await self._provider_dispatch_count(
                    database,
                    run.id,
                )
                await self._append_event(
                    database,
                    run,
                    event_type="stage.started",
                    message=(
                        "Начат анализ исходного сайта"
                        if next_stage == "reference_analysis"
                        else f"Начат этап: {STAGE_PUBLIC_NAMES[next_stage]}"
                    ),
                    now=now,
                    payload={
                        "stage": next_stage,
                        "status": "running",
                        "worker_id": worker_id,
                        "attempt_id": str(attempt_id),
                        "provider_dispatch_count": provider_dispatch_count,
                    },
                )
                await database.flush()
                return self._claim_from_run(run, attempt_id=attempt_id)

    async def heartbeat(self, run_id: UUID, *, worker_id: str) -> RunClaim:
        worker_id = self._validate_worker_id(worker_id)
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {run_id} no longer exists")
            self._assert_live_lease(run, worker_id=worker_id, now=now)
            run.heartbeat_at = now
            run.lease_expires_at = self._lease_expiry(now)
            await database.flush()
            attempt_id = await self._latest_attempt_id(
                database, run.id, run.current_stage or ""
            )
            return self._claim_from_run(run, attempt_id=attempt_id)

    async def continue_claim(self, run_id: UUID, *, worker_id: str) -> RunClaim:
        worker_id = self._validate_worker_id(worker_id)
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {run_id} no longer exists")
            self._assert_live_lease(run, worker_id=worker_id, now=now)
            next_stage = self.next_stage(run.mode, run.last_completed_stage)
            if next_stage is None or run.current_stage != next_stage:
                raise LeaseLostError(f"run {run_id} has no next claimed stage")
            run.heartbeat_at = now
            run.lease_expires_at = self._lease_expiry(now)
            attempt_id = uuid4()
            await self._start_stage_attempt(
                database,
                run,
                stage=next_stage,
                attempt_id=attempt_id,
                now=now,
            )
            provider_dispatch_count = await self._provider_dispatch_count(
                database,
                run.id,
            )
            await self._append_event(
                database,
                run,
                event_type="stage.started",
                message=f"Начат этап: {STAGE_PUBLIC_NAMES[next_stage]}",
                now=now,
                payload={
                    "stage": next_stage,
                    "status": "running",
                    "worker_id": worker_id,
                    "attempt_id": str(attempt_id),
                    "provider_dispatch_count": provider_dispatch_count,
                },
            )
            await database.flush()
            return self._claim_from_run(run, attempt_id=attempt_id)

    async def cancellation_requested(self, run_id: UUID) -> bool:
        async with self._sessions() as database:
            return await self._cancel_requested(database, run_id)

    async def request_cancel(self, run_id: UUID) -> bool:
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                return False
            if run.state in {"completed", "failed", "cancelled"}:
                return False
            if await self._cancel_requested(database, run_id):
                return False
            await self._append_event(
                database,
                run,
                event_type="run.cancel_requested",
                message="Запрошена отмена генерации",
                now=now,
                payload={"status": run.state},
            )
            return True

    async def cancel_claim(self, run_id: UUID, *, worker_id: str) -> None:
        worker_id = self._validate_worker_id(worker_id)
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {run_id} no longer exists")
            self._assert_live_lease(run, worker_id=worker_id, now=now)
            if not await self._cancel_requested(database, run_id):
                raise RunCancelledError(f"run {run_id} has no cancellation request")
            stage = run.current_stage or ""
            attempt_id = await self._latest_attempt_id(database, run.id, stage)
            await self._transition_stage_attempt(
                database,
                run_id=run.id,
                stage=stage,
                attempt_id=attempt_id,
                status="cancelled",
                now=now,
            )
            run.state = "cancelled"
            run.finished_at = now
            run.heartbeat_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            await self._append_event(
                database,
                run,
                event_type="run.cancelled",
                message="Генерация отменена",
                now=now,
                payload={"status": "cancelled", "worker_id": worker_id},
                terminal_state="cancelled",
                finished_at=now,
            )
            await database.execute(
                update(Project)
                .where(
                    Project.id == run.project_id,
                    Project.active_run_id == run.id,
                )
                .values(status="cancelled")
            )

    async def release_claim(self, claim: RunClaim) -> None:
        """Release an interrupted attempt without discarding a staged result."""

        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            staged = await self._staged_result_record(
                database,
                run.id,
                claim.next_stage,
                attempt_id=claim.attempt_id,
            )
            ambiguous_claim = (
                await self._ambiguous_dispatched_claim(
                    database,
                    run,
                    claim.next_stage,
                )
                if staged is None
                else None
            )
            if (
                ambiguous_claim is not None
                and ambiguous_claim.attempt_id == claim.attempt_id
            ):
                await self._fail_locked(
                    database,
                    run,
                    claim,
                    BuilderEngineError(
                        "provider_dispatch_ambiguous",
                        "Generation stopped because the provider result could not "
                        "be reconciled safely",
                    ),
                    now,
                )
                return
            if staged is None:
                await self._transition_stage_attempt(
                    database,
                    run_id=run.id,
                    stage=claim.next_stage,
                    attempt_id=claim.attempt_id,
                    status="interrupted",
                    now=now,
                )
            run.state = "queued"
            run.heartbeat_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            await self._append_event(
                database,
                run,
                event_type="stage.interrupted",
                message="Работа этапа безопасно приостановлена",
                now=now,
                payload={
                    "stage": claim.next_stage,
                    "status": "queued",
                    "worker_id": claim.worker_id,
                    "attempt_id": str(claim.attempt_id),
                },
            )

    async def _fail_locked(
        self,
        database: AsyncSession,
        run: GenerationRun,
        claim: RunClaim,
        error: BuilderEngineError,
        now: datetime,
    ) -> None:
        attempt_status = (
            "accounting_failed"
            if error.error_code == "provider_dispatch_ambiguous"
            else "failed"
        )
        await self._transition_stage_attempt(
            database,
            run_id=run.id,
            stage=claim.next_stage,
            attempt_id=claim.attempt_id,
            status=attempt_status,
            now=now,
        )
        run.state = "failed"
        run.finished_at = now
        run.heartbeat_at = now
        run.retry_not_before = None
        run.error_code = error.error_code
        run.error_message = error.public_message[:2_000]
        run.failure_category = failure_category_for_error(error).value
        run.lease_owner = None
        run.lease_expires_at = None
        await self._append_event(
            database,
            run,
            event_type="stage.failed",
            message=error.public_message,
            now=now,
            payload={
                "stage": claim.next_stage,
                "status": "failed",
                "worker_id": claim.worker_id,
                "attempt_id": str(claim.attempt_id),
                "error_code": error.error_code,
                "diagnostic": error.diagnostic,
                "usage": error.usage.to_dict(),
            },
        )
        await self._append_event(
            database,
            run,
            event_type="run.failed",
            message=error.public_message,
            now=now,
            payload={
                "stage": claim.next_stage,
                "status": "failed",
                "error_code": error.error_code,
            },
            terminal_state="failed",
            finished_at=now,
        )
        await database.execute(
            update(Project)
            .where(
                Project.id == run.project_id,
                Project.active_run_id == run.id,
            )
            .values(status="failed")
        )
        await self._record_free_result_if_available(database, run)

    @staticmethod
    async def _record_free_result_if_available(
        database: AsyncSession,
        run: GenerationRun,
    ) -> None:
        artifact_record = await database.scalar(
            select(GenerationArtifact)
            .where(
                GenerationArtifact.run_id == run.id,
                GenerationArtifact.quality_status.in_(("accepted", "verified")),
            )
            .order_by(GenerationArtifact.revision.desc())
            .limit(1)
        )
        if artifact_record is None:
            return
        await record_funnel_event(
            database,
            event_type="free_result",
            event_key=f"free_result:run:{run.id}",
            journey_id=run.journey_id,
            project_id=run.project_id,
            run_id=run.id,
            artifact_id=artifact_record.id,
        )

    async def fail_claim(
        self,
        claim: RunClaim,
        error: BuilderEngineError,
    ) -> None:
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            await self._fail_locked(database, run, claim, error, now)

    async def retry_claim(
        self,
        claim: RunClaim,
        error: BuilderEngineError,
    ) -> bool:
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            failed_execution = run.stage_retry_count + 1
            if failed_execution >= MAX_STAGE_EXECUTIONS:
                await self._fail_locked(database, run, claim, error, now)
                return False
            await self._transition_stage_attempt(
                database,
                run_id=run.id,
                stage=claim.next_stage,
                attempt_id=claim.attempt_id,
                status="interrupted",
                now=now,
            )
            delay = self.retry_backoff_seconds * (2 ** (failed_execution - 1))
            not_before = now + timedelta(seconds=delay)
            run.stage_retry_count = failed_execution
            run.retry_not_before = not_before
            run.state = "queued"
            run.heartbeat_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            await self._append_event(
                database,
                run,
                event_type="stage.retry_scheduled",
                message="Этап будет повторён после временной ошибки провайдера",
                now=now,
                payload={
                    "stage": claim.next_stage,
                    "status": "retry_scheduled",
                    "attempt": failed_execution,
                    "max_executions": MAX_STAGE_EXECUTIONS,
                    "not_before": not_before.isoformat(),
                    "error_code": error.error_code,
                    "worker_id": claim.worker_id,
                    "attempt_id": str(claim.attempt_id),
                },
            )
            return True

    async def _assert_claim(
        self,
        database: AsyncSession,
        run: GenerationRun,
        claim: RunClaim,
        now: datetime,
    ) -> None:
        self._assert_live_lease(run, worker_id=claim.worker_id, now=now)
        if run.current_stage != claim.next_stage:
            raise LeaseLostError(f"stage fence moved for run {run.id}")
        current_attempt = await self._latest_attempt_id(
            database, run.id, claim.next_stage
        )
        if current_attempt != claim.attempt_id:
            raise LeaseLostError(f"attempt fence moved for run {run.id}")
        attempt = await self._stage_attempt(
            database,
            run_id=run.id,
            stage=claim.next_stage,
            attempt_id=claim.attempt_id,
        )
        if attempt.status not in {"running", "result_staged"}:
            raise LeaseLostError(f"attempt fence moved for run {run.id}")

    @staticmethod
    async def _staged_result_record(
        database: AsyncSession,
        run_id: UUID,
        stage: str,
        *,
        attempt_id: UUID | None = None,
    ) -> GenerationEvent | None:
        records = (
            await database.execute(
                select(GenerationEvent)
                .where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.event_type == "stage.result_staged",
                )
                .order_by(GenerationEvent.sequence.desc())
                .limit(20)
            )
        ).scalars().all()
        return next(
            (
                record
                for record in records
                if record.payload.get("stage") == stage
                and (
                    attempt_id is None
                    or record.payload.get("attempt_id") == str(attempt_id)
                )
            ),
            None,
        )

    async def staged_result(self, claim: RunClaim) -> StageResult | None:
        async with self._sessions() as database:
            record = await self._staged_result_record(
                database,
                claim.run_id,
                claim.next_stage,
                attempt_id=claim.attempt_id,
            )
            if record is None:
                return None
            payload = record.payload.get("result")
            return StageResult.from_dict(payload) if isinstance(payload, dict) else None

    async def stage_input(self, claim: RunClaim) -> StageInput:
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            created = (
                await database.execute(
                    select(GenerationEvent).where(
                        GenerationEvent.run_id == run.id,
                        GenerationEvent.event_type == "run.created",
                    )
                )
            ).scalar_one_or_none()
            request_payload = created.payload.get("request") if created is not None else None
            if not isinstance(request_payload, dict):
                raise RuntimeError(f"run {run.id} has no durable builder request")
            request = BuilderRequest.from_dict(request_payload)
            if run.source_version_id is not None:
                source_row = (
                    await database.execute(
                        select(ProjectVersion, GenerationArtifact)
                        .join(
                            GenerationArtifact,
                            (GenerationArtifact.id == ProjectVersion.artifact_id)
                            & (
                                GenerationArtifact.run_id
                                == ProjectVersion.run_id
                            ),
                        )
                        .where(
                            ProjectVersion.id == run.source_version_id,
                            ProjectVersion.project_id == run.project_id,
                            GenerationArtifact.quality_status.in_(
                                ("accepted", "verified")
                            ),
                        )
                    )
                ).one_or_none()
                if source_row is None:
                    raise RuntimeError(
                        f"run {run.id} has no exact refinable source artifact"
                    )
                _, artifact_record = source_row
                artifact_payload = artifact_record.config.get("artifact")
                if not isinstance(artifact_payload, dict):
                    raise RuntimeError(
                        f"run {run.id} has an invalid source version artifact"
                    )
                previous = WidgetArtifact.from_dict(artifact_payload)
                composition = await PatternRepository(database).load_plan(run.id)
                if composition is None:
                    raise RuntimeError(
                        f"run {run.id} has no cloned composition plan"
                    )
                selected_direction = DirectionProposal(
                    proposal_id=composition.plan.direction_id,
                    role=DirectionRole.INTERACTION_INVENTOR,
                    title="Сохранённое визуальное направление",
                    art_direction=previous.art_direction,
                    interaction_model=(
                        "Сохранить принятую структуру и внести только пожелание "
                        "пользователя."
                    ),
                    safeguards=(
                        "Не менять подтверждённый визуальный язык без необходимости",
                    ),
                )
                context = {
                    "composition_plan": composition.plan.to_dict(),
                    "selected_direction": selected_direction.to_dict(),
                }
            else:
                artifact_record = (
                    await database.execute(
                        select(GenerationArtifact)
                        .where(GenerationArtifact.run_id == run.id)
                        .order_by(GenerationArtifact.revision.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                previous = None
                if artifact_record is not None:
                    artifact_payload = artifact_record.config.get("artifact")
                    if not isinstance(artifact_payload, dict):
                        raise RuntimeError(
                            f"run {run.id} has an invalid durable artifact"
                        )
                    previous = WidgetArtifact.from_dict(artifact_payload)
                staged_payloads = (
                    await database.execute(
                        select(GenerationEvent.payload)
                        .where(
                            GenerationEvent.run_id == run.id,
                            GenerationEvent.event_type == "stage.result_staged",
                        )
                        .order_by(GenerationEvent.sequence.desc())
                    )
                ).scalars().all()
                context = {}
                for payload in staged_payloads:
                    result_payload = payload.get("result")
                    candidate = (
                        result_payload.get("context")
                        if isinstance(result_payload, dict)
                        else None
                    )
                    if isinstance(candidate, dict) and candidate:
                        context = json.loads(
                            json.dumps(candidate, ensure_ascii=False)
                        )
                        break
            return StageInput(
                request=request,
                previous_artifact=previous,
                context=context,
            )

    async def append_attempt_event(
        self,
        claim: RunClaim,
        *,
        event_type: str,
        stage: Stage | None,
        status: str,
        message: str,
        revision: int | None = None,
        usage: TokenUsage | None = None,
        issues: tuple = (),
        changes: tuple[str, ...] = (),
        error_code: str | None = None,
        diagnostic: str | None = None,
        output_refs: tuple[str, ...] = (),
        forensic_payload: Mapping[str, object] | None = None,
        forensic_blobs: tuple[ForensicBlob, ...] = (),
    ) -> None:
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            await self._append_event(
                database,
                run,
                event_type=event_type,
                message=message,
                now=now,
                payload={
                    "stage": stage.value if stage is not None else None,
                    "status": status,
                    "revision": revision,
                    "usage": (usage or TokenUsage()).to_dict(),
                    "issues": [issue.to_dict() for issue in issues],
                    "changes": list(changes),
                    "error_code": error_code,
                    "output_refs": list(output_refs),
                    "diagnostic": diagnostic,
                    "worker_id": claim.worker_id,
                    "attempt_id": str(claim.attempt_id),
                },
                forensic_payload=forensic_payload,
                forensic_blobs=forensic_blobs,
            )

    async def stage_visual_draft(
        self,
        claim: RunClaim,
        artifact: WidgetArtifact,
    ) -> None:
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            previous_draft = (
                await database.execute(
                    select(GenerationEvent)
                    .where(
                        GenerationEvent.run_id == run.id,
                        GenerationEvent.event_type == "artifact.draft_staged",
                    )
                    .order_by(GenerationEvent.sequence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            await self._append_event(
                database,
                run,
                event_type="artifact.draft_staged",
                message="Черновик визуальной проверки сохранён",
                now=now,
                payload={
                    "stage": artifact.stage.value,
                    "status": "needs_repair",
                    "revision": artifact.revision,
                    "artifact": artifact.to_dict(),
                    "supersedes_sequence": (
                        previous_draft.sequence
                        if previous_draft is not None
                        else None
                    ),
                    "worker_id": claim.worker_id,
                    "attempt_id": str(claim.attempt_id),
                },
            )

    async def stage_result(
        self, claim: RunClaim, result: StageResult
    ) -> StageResult:
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            existing = await self._staged_result_record(
                database,
                claim.run_id,
                claim.next_stage,
                attempt_id=claim.attempt_id,
            )
            if existing is not None:
                return StageResult.from_dict(existing.payload["result"])
            await self._validate_stage_result_boundary(
                database,
                claim=claim,
                run=run,
                result=result,
            )
            await self._transition_stage_attempt(
                database,
                run_id=run.id,
                stage=claim.next_stage,
                attempt_id=claim.attempt_id,
                status="result_staged",
                now=now,
                expected_statuses=("running",),
            )
            await self._append_event(
                database,
                run,
                event_type="stage.result_staged",
                message="Результат этапа сохранён",
                now=now,
                payload={
                    "stage": claim.next_stage,
                    "status": "staged",
                    "attempt_id": str(claim.attempt_id),
                    "result": result.to_dict(),
                },
            )
            return result

    @staticmethod
    async def _validate_stage_result_boundary(
        database: AsyncSession,
        *,
        claim: RunClaim,
        run: GenerationRun,
        result: StageResult,
    ) -> None:
        if claim.next_stage == "reference_analysis":
            if result.artifact is not None:
                raise ValueError("reference analysis cannot stage an artifact")
            return
        if claim.next_stage == "composition":
            if result.artifact is not None:
                raise ValueError("composition cannot stage an artifact")
            legacy_plan = result.context.get("composition_plan")
            candidate_plan = result.context.get("pattern_candidate_plan")
            has_legacy = isinstance(legacy_plan, dict)
            has_candidate = isinstance(candidate_plan, dict)
            if has_legacy == has_candidate:
                raise ValueError(
                    "composition must stage exactly one durable plan"
                )
            if has_candidate:
                try:
                    PatternCandidatePlan.from_dict(candidate_plan)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "composition candidate plan is invalid"
                    ) from exc
            return
        if result.request is not None:
            raise ValueError("only reference analysis can update the builder request")
        if result.artifact is None:
            return
        expected_stage = Stage(claim.next_stage)
        if result.artifact.stage is not expected_stage:
            raise ValueError(
                f"artifact stage {result.artifact.stage.value!r} does not match "
                f"claimed stage {expected_stage.value!r}"
            )
        previous = (
            await database.execute(
                select(GenerationArtifact)
                .where(GenerationArtifact.run_id == claim.run_id)
                .order_by(GenerationArtifact.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if previous is None and run.source_version_id is not None:
            previous = (
                await database.execute(
                    select(GenerationArtifact)
                    .join(
                        ProjectVersion,
                        (ProjectVersion.artifact_id == GenerationArtifact.id)
                        & (ProjectVersion.run_id == GenerationArtifact.run_id),
                    )
                    .where(
                        ProjectVersion.id == run.source_version_id,
                        ProjectVersion.project_id == run.project_id,
                    )
                )
            ).scalar_one_or_none()
            if previous is None:
                raise ValueError("refinement source artifact is unavailable")
        previous_revision = previous.revision if previous is not None else 0
        if result.artifact.revision != previous_revision + 1:
            raise ValueError(
                "artifact revision must be exactly one greater than the latest revision"
            )
        issues = validate_artifact(
            result.artifact,
            previous_revision=previous_revision,
        )
        if issues:
            codes = ", ".join(sorted({issue.code for issue in issues}))
            raise ValueError(f"artifact failed stage boundary validation: {codes}")

    async def _materialize_result(
        self,
        database: AsyncSession,
        run: GenerationRun,
        result: StageResult,
        now: datetime,
        *,
        attempt_id: UUID | None = None,
    ) -> GenerationArtifact | None:
        artifact_record: GenerationArtifact | None = None
        if result.request is not None:
            created = (
                await database.execute(
                    select(GenerationEvent).where(
                        GenerationEvent.run_id == run.id,
                        GenerationEvent.event_type == "run.created",
                    )
                )
            ).scalar_one()
            payload = dict(created.payload)
            payload["request"] = result.request.to_dict()
            created.payload = payload
        if run.current_stage == "composition":
            raw_candidate_plan = result.context.get("pattern_candidate_plan")
            if isinstance(raw_candidate_plan, dict):
                plan = PatternCandidatePlan.from_dict(raw_candidate_plan)
                repository = PatternCandidateRepository(database)
                existing = await repository.load_plan(run.id)
                if existing is not None:
                    if existing.plan != plan:
                        raise RuntimeError(
                            "staged candidate composition conflicts with persisted plan"
                        )
                else:
                    direction_artifact_id = await database.scalar(
                        select(GenerationArtifact.id)
                        .where(
                            GenerationArtifact.run_id == run.id,
                            GenerationArtifact.stage == Stage.ART_DIRECTION.value,
                        )
                        .order_by(GenerationArtifact.revision.desc())
                        .limit(1)
                    )
                    selector_query = (
                        select(ModelCall.id)
                        .where(
                            ModelCall.run_id == run.id,
                            ModelCall.operation == "pattern_candidate_plan",
                            ModelCall.status == "completed",
                        )
                        .order_by(ModelCall.created_at.desc(), ModelCall.id.desc())
                        .limit(1)
                    )
                    if attempt_id is not None:
                        selector_query = selector_query.where(
                            ModelCall.stage_attempt_id == attempt_id
                        )
                    if result.output_refs:
                        selector_query = selector_query.where(
                            ModelCall.request_id.in_(result.output_refs)
                        )
                    selector_model_call_id = await database.scalar(selector_query)
                    await repository.create_plan(
                        run_id=run.id,
                        plan=plan,
                        registry=load_builtin_atomic_registry(),
                        direction_artifact_id=direction_artifact_id,
                        selector_model_call_id=selector_model_call_id,
                    )
            else:
                raw_plan = result.context.get("composition_plan")
                if not isinstance(raw_plan, dict):
                    raise ValueError("composition result has no durable plan")
                plan = CompositionPlan.from_dict(raw_plan)
                repository = PatternRepository(database)
                existing = await repository.load_plan(run.id)
                if existing is not None:
                    if existing.plan != plan:
                        raise RuntimeError(
                            "staged composition conflicts with persisted plan"
                        )
                else:
                    direction_artifact_id = await database.scalar(
                        select(GenerationArtifact.id)
                        .where(
                            GenerationArtifact.run_id == run.id,
                            GenerationArtifact.stage == Stage.ART_DIRECTION.value,
                        )
                        .order_by(GenerationArtifact.revision.desc())
                        .limit(1)
                    )
                    planner_query = (
                        select(ModelCall.id)
                        .where(
                            ModelCall.run_id == run.id,
                            ModelCall.role == "composition_planner",
                            ModelCall.status == "completed",
                        )
                        .order_by(ModelCall.created_at.desc(), ModelCall.id.desc())
                        .limit(1)
                    )
                    if result.output_refs:
                        planner_query = planner_query.where(
                            ModelCall.request_id.in_(result.output_refs)
                        )
                    planner_model_call_id = await database.scalar(planner_query)
                    await repository.create_plan(
                        run_id=run.id,
                        plan=plan,
                        registry=load_builtin_registry(),
                        direction_artifact_id=direction_artifact_id,
                        planner_model_call_id=planner_model_call_id,
                    )
        artifact = result.artifact
        if artifact is not None:
            artifact_record = (
                await database.execute(
                    select(GenerationArtifact).where(
                        GenerationArtifact.run_id == run.id,
                        GenerationArtifact.revision == artifact.revision,
                    )
                )
            ).scalar_one_or_none()
            artifact_payload = artifact.to_dict()
            if artifact_record is None:
                artifact_record = GenerationArtifact(
                    run_id=run.id,
                    revision=artifact.revision,
                    stage=artifact.stage.value,
                    html=artifact.body_html,
                    css=artifact.css,
                    javascript=artifact.javascript,
                    config={"artifact": artifact_payload},
                    quality_status="verified",
                    provenance={"output_refs": list(result.output_refs)},
                )
                database.add(artifact_record)
                await database.flush()
            elif artifact_record.config.get("artifact") != artifact_payload:
                raise RuntimeError("staged artifact revision conflicts with persistence")
            await record_funnel_event(
                database,
                event_type="first_artifact",
                event_key=f"first_artifact:run:{run.id}",
                journey_id=run.journey_id,
                project_id=run.project_id,
                run_id=run.id,
                artifact_id=artifact_record.id,
            )
        await self._persist_pattern_stage_provenance(
            database,
            run_id=run.id,
            stage=run.current_stage,
            result=result,
            attempt_id=attempt_id,
        )
        for event in result.events:
            event_type = str(event.get("event_type", "")).strip()
            message = str(event.get("message", "")).strip()
            if not event_type or not message:
                raise ValueError("stage result event is invalid")
            await self._append_event(
                database,
                run,
                event_type=event_type,
                message=message,
                now=now,
                payload={
                    "stage": run.current_stage,
                    "status": str(event.get("status", "completed")),
                    "output_refs": list(result.output_refs),
                },
            )
        return artifact_record

    async def _persist_pattern_stage_provenance(
        self,
        database: AsyncSession,
        *,
        run_id: UUID,
        stage: str | None,
        result: StageResult,
        attempt_id: UUID | None,
    ) -> None:
        """Persist exact stage exposures/claims when a v3 plan exists.

        The durable plan is the source of truth.  Re-resolving it during the
        finalize transaction means a worker restart cannot silently expose a
        different registry version or selector result.
        """

        if stage not in {
            Stage.FOUNDATION.value,
            Stage.IDENTITY.value,
            Stage.CONVERSATION.value,
            Stage.MOTION_POLISH.value,
        }:
            return
        repository = PatternCandidateRepository(database)
        persisted = await repository.load_plan(run_id)
        if persisted is None:
            return
        registry = load_builtin_atomic_registry()
        rows = (
            await database.execute(
                select(
                    WidgetPatternVersion.id,
                    WidgetPatternVersion.pattern_id,
                    WidgetPatternVersion.version,
                )
            )
        ).all()
        persisted_versions = {
            (str(pattern_id), int(version)): pattern_version_id
            for pattern_version_id, pattern_id, version in rows
        }
        approved: set[tuple[str, int]] = set()
        for definition in registry.definitions:
            key = (definition.pattern_id, definition.version)
            if key in persisted_versions:
                review_state = await repository.effective_review_state(
                    persisted_versions[key]
                )
            else:
                review_state = definition.provenance.get("review_state")
            if review_state == "approved":
                approved.add(key)
        try:
            pack = resolve_pattern_candidate_pack(
                persisted.plan,
                Stage(stage),
                registry,
                effective_approved=approved,
            )
        except (TypeError, ValueError):
            # A malformed plan is already rejected at stage.result_staged; do
            # not turn a replay of an older legacy run into a new failure.
            return
        item_by_key = {
            (item.pattern_id, item.version): item for item in persisted.items
        }
        exposed_items = [
            item_by_key[(version.pattern_id, version.version)]
            for version in pack.exposed_versions
            if (version.pattern_id, version.version) in item_by_key
        ]
        if not exposed_items:
            return
        model_calls = (
            (
                await database.execute(
                    select(ModelCall)
                    .where(
                        ModelCall.run_id == run_id,
                        ModelCall.stage_attempt_id == attempt_id,
                    )
                    .order_by(ModelCall.created_at, ModelCall.id)
                )
            ).scalars().all()
            if attempt_id is not None
            else []
        )
        exposure_by_key_call: dict[tuple[tuple[str, int], UUID | None], Any] = {}
        for item in exposed_items:
            calls = model_calls or [None]
            for model_call in calls:
                exposure = await repository.record_exposure(
                    run_id=run_id,
                    stage=stage,
                    candidate_item_id=item.id,
                    model_call_id=model_call.id if model_call is not None else None,
                )
                exposure_by_key_call[
                    ((item.pattern_id, item.version), model_call.id if model_call else None)
                ] = exposure

        raw_claims = result.context.get("pattern_usage_claims")
        if not isinstance(raw_claims, list):
            return
        seen_claims: set[tuple[str, int]] = set()
        calls_by_request: dict[str, list[ModelCall]] = {}
        for model_call in model_calls:
            if model_call.request_id:
                calls_by_request.setdefault(model_call.request_id, []).append(model_call)
        for raw_claim in raw_claims[:32]:
            if not isinstance(raw_claim, dict):
                continue
            if set(raw_claim) - {
                "pattern_id",
                "version",
                "usage_mode",
                "model_call_id",
                "request_id",
            }:
                continue
            pattern_id = raw_claim.get("pattern_id")
            version = raw_claim.get("version")
            mode = raw_claim.get("usage_mode")
            if (
                not isinstance(pattern_id, str)
                or isinstance(version, bool)
                or not isinstance(version, int)
                or mode not in {"primary", "combined", "inspiration"}
            ):
                continue
            key = (pattern_id, version)
            if key in seen_claims or key not in item_by_key:
                continue
            seen_claims.add(key)
            model_call_id: UUID | None = None
            if isinstance(raw_claim.get("model_call_id"), str):
                try:
                    model_call_id = UUID(raw_claim["model_call_id"])
                except (TypeError, ValueError):
                    continue
                if model_call_id not in {call.id for call in model_calls}:
                    continue
            elif isinstance(raw_claim.get("request_id"), str):
                matches = calls_by_request.get(raw_claim["request_id"], [])
                if len(matches) != 1:
                    continue
                model_call_id = matches[0].id
            elif len(model_calls) == 1:
                model_call_id = model_calls[0].id
            exposure = exposure_by_key_call.get((key, model_call_id))
            if exposure is None:
                continue
            try:
                await repository.record_usage_claim(
                    run_id=run_id,
                    stage=stage,
                    usage_mode=mode,
                    candidate_item_id=item_by_key[key].id,
                    exposure_id=exposure.id,
                    model_call_id=model_call_id,
                )
            except (TypeError, ValueError):
                continue

    async def _checkpoint_locked(
        self,
        database: AsyncSession,
        run: GenerationRun,
        *,
        stage: str,
        worker_id: str,
        result: StageResult | None = None,
        attempt_id: UUID | None = None,
    ) -> str | None:
        # Lease extension belongs at the checkpoint boundary. In particular,
        # never reuse the timestamp captured before staged-result parsing and
        # artifact materialization, which may take longer than a short lease.
        now = self._now()
        expected_stage = self.next_stage(run.mode, run.last_completed_stage)
        if stage != expected_stage or run.current_stage != expected_stage:
            raise ValueError(
                f"cannot checkpoint stage {stage!r}; expected {expected_stage!r}"
            )
        attempt_id = attempt_id or await self._latest_attempt_id(
            database,
            run.id,
            stage,
        )
        await self._transition_stage_attempt(
            database,
            run_id=run.id,
            stage=stage,
            attempt_id=attempt_id,
            status="completed",
            now=now,
        )
        stages = self.stage_sequence(run.mode)
        stage_index = stages.index(stage)
        next_stage = stages[stage_index + 1] if stage_index + 1 < len(stages) else None
        run.last_completed_stage = stage
        run.current_stage = next_stage
        run.stage_retry_count = 0
        run.retry_not_before = None
        run.error_code = None
        run.error_message = None
        run.progress = int(((stage_index + 1) * 100) / len(stages))
        run.heartbeat_at = now
        await self._append_event(
            database,
            run,
            event_type="stage.completed",
            message=(
                result.public_message
                if result is not None
                else "Анализ исходного сайта завершён"
                if stage == "reference_analysis"
                else f"Завершён этап: {STAGE_PUBLIC_NAMES[stage]}"
            ),
            now=now,
            payload={
                "stage": stage,
                "status": "completed",
                "worker_id": worker_id,
                "next_stage": next_stage,
                "usage": result.usage.to_dict() if result is not None else {},
                "output_refs": list(result.output_refs) if result is not None else [],
            },
        )
        if next_stage is None:
            # Import lazily: app.projects exports route setup, and routes import
            # this queue. Deferring the repository import keeps module startup
            # acyclic while preserving the transaction boundary below.
            from app.projects.versions import ProjectVersionRepository

            final_artifact = await self._record_pattern_outcomes(database, run)
            if final_artifact is None:
                raise RuntimeError("completed run has no verified artifact")
            activation = await ProjectVersionRepository(
                database
            ).materialize_completed_run(
                run=run,
                artifact=final_artifact,
                now=now,
            )
            version_payload = {
                "version_id": str(activation.version.id),
                "ordinal": activation.version.ordinal,
                "kind": activation.version.kind,
                "activated": activation.activated,
            }
            if activation.created:
                await self._append_event(
                    database,
                    run,
                    event_type="project.version_created",
                    message="Версия проекта сохранена",
                    now=now,
                    payload=version_payload,
                )
                if not activation.activated:
                    await self._append_event(
                        database,
                        run,
                        event_type="project.version_activation_conflict",
                        message=(
                            "Версия сохранена в истории, но активная версия "
                            "проекта уже изменилась"
                        ),
                        now=now,
                        payload=version_payload,
                    )
            run.state = "completed"
            run.progress = 100
            run.finished_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            await self._append_event(
                database,
                run,
                event_type="run.completed",
                message="Виджет готов",
                now=now,
                payload={"status": "completed", "worker_id": worker_id},
                terminal_state="completed",
                finished_at=now,
            )
            project_status = "free_result_ready"
            await self._record_free_result_if_available(database, run)
        else:
            run.state = "running"
            run.lease_expires_at = self._lease_expiry(now)
            project_status = "generating"
        await database.execute(
            update(Project)
            .where(Project.id == run.project_id, Project.active_run_id == run.id)
            .values(status=project_status)
        )
        return next_stage

    @staticmethod
    async def _record_pattern_outcomes(
        database: AsyncSession,
        run: GenerationRun,
    ) -> GenerationArtifact | None:
        final_artifact = (
            await database.execute(
                select(GenerationArtifact)
                .where(
                    GenerationArtifact.run_id == run.id,
                    GenerationArtifact.quality_status.in_(("accepted", "verified")),
                )
                .order_by(GenerationArtifact.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if final_artifact is None:
            return None
        totals = (
            await database.execute(
                select(
                    func.coalesce(func.sum(ModelCall.input_tokens), 0),
                    func.coalesce(func.sum(ModelCall.output_tokens), 0),
                    func.coalesce(func.sum(ModelCall.thinking_tokens), 0),
                    func.coalesce(func.sum(ModelCall.latency_ms), 0),
                    func.coalesce(func.sum(ModelCall.cost_microusd), 0),
                    func.count(ModelCall.id),
                ).where(
                    ModelCall.run_id == run.id,
                    ModelCall.role != "chat_visitor",
                )
            )
        ).one()
        latest_model_call_id = await database.scalar(
            select(ModelCall.id)
            .where(
                ModelCall.run_id == run.id,
                ModelCall.role != "chat_visitor",
                ModelCall.status == "completed",
            )
            .order_by(ModelCall.created_at.desc(), ModelCall.id.desc())
            .limit(1)
        )
        repair_payloads = (
            await database.execute(
                select(GenerationEvent.payload).where(
                    GenerationEvent.run_id == run.id,
                    GenerationEvent.event_type.in_(
                        ("repair.completed", "visual_repair.completed")
                    ),
                )
            )
        ).scalars().all()
        repair_count = sum(
            1
            for payload in repair_payloads
            if payload.get("status") in {None, "completed"}
        )
        visual_score: float | None = None
        visual_score_source: str | None = None
        visual_payloads = (
            await database.execute(
                select(GenerationEvent.payload)
                .where(
                    GenerationEvent.run_id == run.id,
                    GenerationEvent.event_type == "visual_audit.completed",
                )
                .order_by(GenerationEvent.sequence.desc())
                .limit(20)
            )
        ).scalars().all()
        for payload in visual_payloads:
            if payload.get("status") not in {None, "completed"}:
                continue
            visual_score, visual_score_source = _visual_score_from_event(payload)
            if visual_score is not None:
                break
        metrics = PatternOutcomeMetrics(
            technical_pass=final_artifact.quality_status in {"accepted", "verified"},
            visual_score=visual_score,
            repair_count=repair_count,
            input_tokens=int(totals[0]),
            output_tokens=int(totals[1]),
            thinking_tokens=int(totals[2]),
            latency_ms=int(totals[3]),
            cost_microusd=int(totals[4]),
            published=False,
            adopted=False,
            payload={
                "quality_status": final_artifact.quality_status,
                "final_revision": final_artifact.revision,
                "model_call_count": int(totals[5]),
                "visual_score_source": visual_score_source,
            },
        )
        await PatternRepository(database).record_terminal_outcomes(
            run_id=run.id,
            final_artifact_id=final_artifact.id,
            model_call_id=latest_model_call_id,
            metrics=metrics,
        )
        return final_artifact

    async def finalize_stage(self, claim: RunClaim) -> str | None:
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == claim.run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {claim.run_id} no longer exists")
            await self._assert_claim(database, run, claim, now)
            if await self._cancel_requested(database, run.id):
                raise RunCancelledError(f"run {run.id} was cancelled")
            staged = await self._staged_result_record(
                database,
                run.id,
                claim.next_stage,
                attempt_id=claim.attempt_id,
            )
            if staged is None or not isinstance(staged.payload.get("result"), dict):
                raise RuntimeError("stage result must be persisted before finalize")
            result = StageResult.from_dict(staged.payload["result"])
            artifact_record = await self._materialize_result(
                database,
                run,
                result,
                now,
                attempt_id=claim.attempt_id,
            )
            if artifact_record is not None:
                await database.execute(
                    update(ModelCall)
                    .where(
                        ModelCall.stage_attempt_id == claim.attempt_id,
                        ModelCall.artifact_id.is_(None),
                    )
                    .values(artifact_id=artifact_record.id)
                )
            return await self._checkpoint_locked(
                database,
                run,
                stage=claim.next_stage,
                worker_id=claim.worker_id,
                result=result,
                attempt_id=claim.attempt_id,
            )

    async def complete_stage(
        self, run_id: UUID, stage: str, *, worker_id: str
    ) -> str | None:
        """Compatibility checkpoint helper; workers use stage_result/finalize_stage."""
        worker_id = self._validate_worker_id(worker_id)
        async with self._sessions() as database, database.begin():
            now = self._now()
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise LeaseLostError(f"run {run_id} no longer exists")
            self._assert_live_lease(run, worker_id=worker_id, now=now)
            expected = self.next_stage(run.mode, run.last_completed_stage)
            if run.last_completed_stage == stage:
                return expected
            if expected != stage or run.current_stage != stage:
                raise ValueError(f"cannot checkpoint stage {stage!r}")
            try:
                attempt_id = await self._latest_attempt_id(
                    database,
                    run.id,
                    stage,
                )
            except LeaseLostError:
                attempt_id = uuid4()
                await self._start_stage_attempt(
                    database,
                    run,
                    stage=stage,
                    attempt_id=attempt_id,
                    now=now,
                )
                provider_dispatch_count = await self._provider_dispatch_count(
                    database,
                    run.id,
                )
                await self._append_event(
                    database,
                    run,
                    event_type="stage.started",
                    message=f"РќР°С‡Р°С‚ СЌС‚Р°Рї: {STAGE_PUBLIC_NAMES[stage]}",
                    now=now,
                    payload={
                        "stage": stage,
                        "status": "running",
                        "worker_id": worker_id,
                        "attempt_id": str(attempt_id),
                        "provider_dispatch_count": provider_dispatch_count,
                    },
                )
            return await self._checkpoint_locked(
                database,
                run,
                stage=stage,
                worker_id=worker_id,
                attempt_id=attempt_id,
            )


class BuilderWorker:
    def __init__(
        self,
        *,
        queue: PostgresWorkerQueue,
        worker_id: str,
        stage_handler: Callable[[RunClaim], Awaitable[StageResult]],
        heartbeat_interval: float = 15.0,
        idle_poll_interval: float = 0.5,
        terminal_hook: Callable[[UUID], Awaitable[object]] | None = None,
        terminal_reconciler: Callable[[], Awaitable[object]] | None = None,
        service_heartbeat: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.queue = queue
        self.worker_id = queue._validate_worker_id(worker_id)
        if heartbeat_interval <= 0 or idle_poll_interval <= 0:
            raise ValueError("worker intervals must be positive")
        self.stage_handler = stage_handler
        self.heartbeat_interval = float(heartbeat_interval)
        self.idle_poll_interval = float(idle_poll_interval)
        self.terminal_hook = terminal_hook
        self.terminal_reconciler = terminal_reconciler
        self.service_heartbeat = service_heartbeat
        self._stop = asyncio.Event()
        self._active_claim: RunClaim | None = None
        self._active_stage_task: asyncio.Task[StageResult] | None = None
        self._active_execution_task: asyncio.Task[bool] | None = None
        self._active_done = asyncio.Event()
        self._active_done.set()
        self._shutdown_lock = asyncio.Lock()

    async def _cancel_task(self, task: asyncio.Task[None]) -> None:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _abort_stage_task(task: asyncio.Task[StageResult]) -> None:
        """Cancel provider work and wait for its cleanup without masking control errors."""

        if not task.done():
            task.cancel()
        try:
            await task
        except BaseException:
            pass

    async def _run_claim(self, claim: RunClaim) -> str | None:
        if self._stop.is_set():
            await self.queue.release_claim(claim)
            return None
        await self.queue.activate_forensics(claim)
        if await self.queue.cancellation_requested(claim.run_id):
            await self.queue.cancel_claim(claim.run_id, worker_id=self.worker_id)
            return None
        staged = await self.queue.staged_result(claim)
        task = (
            asyncio.create_task(
                self.stage_handler(claim),
                name=f"kaigo-stage-{claim.run_id}-{claim.next_stage}",
            )
            if staged is None
            else None
        )
        if task is None:
            return await self.queue.finalize_stage(claim)
        self._active_stage_task = task
        try:
            while not task.done():
                done, _ = await asyncio.wait(
                    {task}, timeout=self.heartbeat_interval
                )
                if done:
                    break
                try:
                    cancel_requested = await self.queue.cancellation_requested(
                        claim.run_id
                    )
                except BaseException:
                    await self._abort_stage_task(task)
                    raise
                if cancel_requested:
                    await self._abort_stage_task(task)
                    await self.queue.cancel_claim(
                        claim.run_id, worker_id=self.worker_id
                    )
                    return None
                try:
                    await self.queue.heartbeat(
                        claim.run_id, worker_id=self.worker_id
                    )
                except BaseException:
                    await self._abort_stage_task(task)
                    raise
            result = await task
        except asyncio.CancelledError:
            if self._stop.is_set():
                await asyncio.shield(self.queue.release_claim(claim))
                return None
            raise
        except BuilderEngineError as error:
            if error.error_code in RETRYABLE_ENGINE_ERRORS:
                await self.queue.retry_claim(claim, error)
            else:
                await self.queue.fail_claim(claim, error)
            return None
        finally:
            if self._active_stage_task is task:
                self._active_stage_task = None
        if not isinstance(result, StageResult):
            raise TypeError("stage handler must return StageResult")
        if await self.queue.cancellation_requested(claim.run_id):
            await self.queue.cancel_claim(claim.run_id, worker_id=self.worker_id)
            return None
        try:
            await self.queue.stage_result(claim, result)
            return await self.queue.finalize_stage(claim)
        except RunCancelledError:
            await self.queue.cancel_claim(claim.run_id, worker_id=self.worker_id)
            return None

    async def run_once(self) -> bool:
        if self._stop.is_set():
            return False
        self._active_done.clear()
        self._active_execution_task = asyncio.current_task()
        try:
            if self.terminal_reconciler is not None:
                await self.terminal_reconciler()
            if self._stop.is_set():
                return False
            claim_result = await self.queue.claim(self.worker_id)
            if isinstance(claim_result, _TerminalRun):
                if self.terminal_hook is not None:
                    await self.terminal_hook(claim_result.run_id)
                return True
            if claim_result is None:
                return False
            claim = claim_result
            self._active_claim = claim
            try:
                while True:
                    self._active_claim = claim
                    next_stage = await self._run_claim(claim)
                    if next_stage is None or self._stop.is_set():
                        if next_stage is None and self.terminal_hook is not None:
                            await self.terminal_hook(claim.run_id)
                        break
                    claim = await self.queue.continue_claim(
                        claim.run_id, worker_id=self.worker_id
                    )
                return True
            except asyncio.CancelledError:
                if self._stop.is_set() and self._active_claim is not None:
                    try:
                        await asyncio.shield(self.queue.release_claim(self._active_claim))
                    except LeaseLostError:
                        pass
                raise
        finally:
            self._active_claim = None
            self._active_execution_task = None
            self._active_done.set()

    async def run_forever(self) -> None:
        heartbeat_task: asyncio.Task[None] | None = None
        if self.service_heartbeat is not None:
            await self.service_heartbeat()

            async def publish_heartbeats() -> None:
                while not self._stop.is_set():
                    try:
                        await asyncio.wait_for(
                            self._stop.wait(), timeout=self.heartbeat_interval
                        )
                    except TimeoutError:
                        await self.service_heartbeat()

            heartbeat_task = asyncio.create_task(
                publish_heartbeats(), name="kaigo-worker-service-heartbeat"
            )
        try:
            while not self._stop.is_set():
                if heartbeat_task is not None and heartbeat_task.done():
                    await heartbeat_task
                try:
                    worked = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("builder worker iteration failed")
                    worked = True
                if not worked:
                    try:
                        await asyncio.wait_for(
                            self._stop.wait(), timeout=self.idle_poll_interval
                        )
                    except TimeoutError:
                        pass
        finally:
            if heartbeat_task is not None:
                await self._cancel_task(heartbeat_task)

    def stop(self) -> None:
        self._stop.set()

    async def shutdown(self) -> None:
        """Stop new claims, cancel provider work, and release the active lease."""

        async with self._shutdown_lock:
            self._stop.set()
            stage_task = self._active_stage_task
            if stage_task is not None and not stage_task.done():
                stage_task.cancel()
            execution_task = self._active_execution_task
            if (
                execution_task is not None
                and execution_task is not asyncio.current_task()
                and not execution_task.done()
            ):
                execution_task.cancel()
                try:
                    await execution_task
                except asyncio.CancelledError:
                    pass
            await self._active_done.wait()


__all__ = [
    "BuilderWorker",
    "DurableVisualStore",
    "LeaseLostError",
    "OrchestratorStageHandler",
    "PostgresWorkerQueue",
    "RunCancelledError",
    "RunClaim",
    "StageInput",
    "StageResult",
    "failure_category_for_error",
]
