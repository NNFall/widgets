from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    Project,
)
from builder_lab.models import BuilderRequest, TokenUsage, WidgetArtifact
from builder_lab.directions import run_direction_board
from builder_lab.engines.base import BuilderEngine, BuilderEngineError
from builder_lab.models import DirectionProposal, EngineName, Stage
from builder_lab.orchestrator import BuilderOrchestrator
from builder_lab.reference_pipeline import (
    ReferenceAnalysisResult,
    ReferencePipelineError,
)
from builder_lab.validation import (
    issue_fingerprint,
    strip_reserved_runtime_attributes,
    validate_artifact,
)


logger = logging.getLogger(__name__)

DIRECT_STAGE_SEQUENCE = (
    "reference_analysis",
    "art_direction",
    "foundation",
    "identity",
    "conversation",
    "motion_polish",
)
ANTIGRAVITY_STAGE_SEQUENCE = ("reference_analysis", "agent_build")
STAGE_PUBLIC_NAMES = {
    "reference_analysis": "анализ исходного сайта",
    "art_direction": "выбор визуального направления",
    "foundation": "создание основы виджета",
    "identity": "настройка фирменного стиля",
    "conversation": "настройка диалога",
    "motion_polish": "анимации и финальная отделка",
    "agent_build": "агентская сборка виджета",
}
_CLAIM_LOCKS: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


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
        message = self.public_message.strip()
        if not message or len(message) > 2_000 or "\x00" in message:
            raise ValueError("stage result public_message is invalid")
        refs = tuple(str(ref).strip() for ref in self.output_refs)
        if any(not ref or len(ref) > 512 for ref in refs):
            raise ValueError("stage result output_refs are invalid")
        object.__setattr__(self, "public_message", message)
        object.__setattr__(self, "output_refs", refs)
        object.__setattr__(
            self,
            "events",
            tuple(
                json.loads(json.dumps(event, ensure_ascii=False))
                for event in self.events
            ),
        )
        object.__setattr__(
            self,
            "context",
            json.loads(json.dumps(self.context, ensure_ascii=False)),
        )

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


class OrchestratorStageHandler:
    """Execute exactly the stage named by a durable worker claim."""

    def __init__(
        self,
        *,
        queue: "PostgresWorkerQueue",
        engine_factories: dict[EngineName, Callable[[], BuilderEngine]],
        reference_analyzer: Callable[[str], Awaitable[ReferenceAnalysisResult]],
    ) -> None:
        self._queue = queue
        self._factories = dict(engine_factories)
        self._reference_analyzer = reference_analyzer

    async def __call__(self, claim: RunClaim) -> StageResult:
        stage_input = await self._queue.stage_input(claim)
        if claim.next_stage == "reference_analysis":
            return await self._analyze_reference(stage_input.request)
        try:
            stage = Stage(claim.next_stage)
        except ValueError as exc:
            raise BuilderEngineError(
                "internal_error",
                "В очереди обнаружен неподдерживаемый этап генерации",
                diagnostic=claim.next_stage,
            ) from exc
        factory = self._factories.get(stage_input.request.engine)
        if factory is None:
            raise BuilderEngineError(
                "provider_unavailable",
                "Выбранный режим генерации сейчас недоступен",
            )
        engine = factory()
        try:
            return await self._generate_stage(
                request=stage_input.request,
                engine=engine,
                stage=stage,
                previous=stage_input.previous_artifact,
                context=stage_input.context,
            )
        finally:
            await engine.close()

    async def _analyze_reference(self, request: BuilderRequest) -> StageResult:
        if not request.source_url or request.reference_context:
            return StageResult(
                public_message="Анализ исходного сайта завершён",
                request=request,
            )
        try:
            analysis = await self._reference_analyzer(request.source_url)
        except ReferencePipelineError as exc:
            raise BuilderEngineError(
                exc.error_code,
                exc.public_message,
                diagnostic=exc.diagnostic,
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
        stage: Stage,
        previous: WidgetArtifact | None,
        context: dict,
    ) -> StageResult:
        usage = TokenUsage()
        selected_direction: DirectionProposal | None = None
        next_context = dict(context)
        if request.engine is EngineName.DIRECT:
            if stage is Stage.ART_DIRECTION:
                board = await run_direction_board(
                    engine=engine,  # type: ignore[arg-type]
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
        revision = (previous.revision if previous is not None else 0) + 1
        result = await BuilderOrchestrator.execute_stage(
            request=request,
            engine=engine,
            stage=stage,
            revision=revision,
            previous_artifact=previous,
            selected_direction=selected_direction,
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
                engine=engine,
                stage=stage,
                revision=revision,
                previous_artifact=candidate,
                selected_direction=selected_direction,
                repair_issues=issues,
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
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._sessions = session_factory
        self.lease_seconds = float(lease_seconds)
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

    @staticmethod
    def _validate_worker_id(worker_id: str) -> str:
        if not isinstance(worker_id, str):
            raise ValueError("worker_id must be text")
        normalized = worker_id.strip()
        if not normalized or len(normalized) > 128 or "\x00" in normalized:
            raise ValueError("worker_id is invalid")
        return normalized

    @staticmethod
    def stage_sequence(mode: str) -> tuple[str, ...]:
        return (
            ANTIGRAVITY_STAGE_SEQUENCE
            if mode.strip().lower() == "antigravity"
            else DIRECT_STAGE_SEQUENCE
        )

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
        return (
            select(GenerationRun)
            .where(
                GenerationRun.state.in_(("queued", "running")),
                available_lease,
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

    @staticmethod
    def _append_event(
        database: AsyncSession,
        run: GenerationRun,
        *,
        event_type: str,
        message: str,
        now: datetime,
        payload: dict,
    ) -> None:
        sequence = run.next_event_sequence
        run.next_event_sequence += 1
        values = {
            "run_id": run.id,
            "sequence": sequence,
            "event_type": event_type,
            "public_message": message,
            "payload": {
                "run_id": str(run.id),
                "sequence": sequence,
                "timestamp": now.isoformat(),
                "type": event_type,
                "message": message,
                **payload,
            },
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
    async def _latest_attempt_id(
        database: AsyncSession, run_id: UUID, stage: str
    ) -> UUID:
        payloads = (
            await database.execute(
                select(GenerationEvent.payload)
                .where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.event_type == "stage.started",
                )
                .order_by(GenerationEvent.sequence.desc())
                .limit(20)
            )
        ).scalars().all()
        for payload in payloads:
            if payload.get("stage") != stage:
                continue
            try:
                return UUID(str(payload["attempt_id"]))
            except (KeyError, TypeError, ValueError):
                break
        raise LeaseLostError(f"run {run_id} has no valid stage attempt")

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

    async def claim(self, worker_id: str) -> RunClaim | None:
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
                    return None
                run.state = "running"
                run.current_stage = next_stage
                run.started_at = run.started_at or now
                run.lease_owner = worker_id
                run.heartbeat_at = now
                run.lease_expires_at = self._lease_expiry(now)
                attempt_id = uuid4()
                self._append_event(
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
            self._append_event(
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
            self._append_event(
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
            run.state = "cancelled"
            run.finished_at = now
            run.heartbeat_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            self._append_event(
                database,
                run,
                event_type="run.cancelled",
                message="Генерация отменена",
                now=now,
                payload={"status": "cancelled", "worker_id": worker_id},
            )
            await database.execute(
                update(Project)
                .where(
                    Project.id == run.project_id,
                    Project.active_run_id == run.id,
                )
                .values(status="cancelled")
            )

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

    @staticmethod
    async def _staged_result_record(
        database: AsyncSession, run_id: UUID, stage: str
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
            (record for record in records if record.payload.get("stage") == stage),
            None,
        )

    async def staged_result(self, claim: RunClaim) -> StageResult | None:
        async with self._sessions() as database:
            record = await self._staged_result_record(
                database, claim.run_id, claim.next_stage
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
            context: dict = {}
            for payload in staged_payloads:
                result_payload = payload.get("result")
                candidate = (
                    result_payload.get("context")
                    if isinstance(result_payload, dict)
                    else None
                )
                if isinstance(candidate, dict) and candidate:
                    context = json.loads(json.dumps(candidate, ensure_ascii=False))
                    break
            return StageInput(
                request=request,
                previous_artifact=previous,
                context=context,
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
                database, claim.run_id, claim.next_stage
            )
            if existing is not None:
                return StageResult.from_dict(existing.payload["result"])
            self._append_event(
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

    async def _materialize_result(
        self,
        database: AsyncSession,
        run: GenerationRun,
        result: StageResult,
        now: datetime,
    ) -> None:
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
        artifact = result.artifact
        if artifact is not None:
            existing = (
                await database.execute(
                    select(GenerationArtifact).where(
                        GenerationArtifact.run_id == run.id,
                        GenerationArtifact.revision == artifact.revision,
                    )
                )
            ).scalar_one_or_none()
            artifact_payload = artifact.to_dict()
            if existing is None:
                database.add(
                    GenerationArtifact(
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
                )
            elif existing.config.get("artifact") != artifact_payload:
                raise RuntimeError("staged artifact revision conflicts with persistence")
        for event in result.events:
            event_type = str(event.get("event_type", "")).strip()
            message = str(event.get("message", "")).strip()
            if not event_type or not message:
                raise ValueError("stage result event is invalid")
            self._append_event(
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

    async def _checkpoint_locked(
        self,
        database: AsyncSession,
        run: GenerationRun,
        *,
        stage: str,
        worker_id: str,
        now: datetime,
        result: StageResult | None = None,
    ) -> str | None:
        expected_stage = self.next_stage(run.mode, run.last_completed_stage)
        if stage != expected_stage or run.current_stage != expected_stage:
            raise ValueError(
                f"cannot checkpoint stage {stage!r}; expected {expected_stage!r}"
            )
        stages = self.stage_sequence(run.mode)
        stage_index = stages.index(stage)
        next_stage = stages[stage_index + 1] if stage_index + 1 < len(stages) else None
        run.last_completed_stage = stage
        run.current_stage = next_stage
        run.progress = int(((stage_index + 1) * 100) / len(stages))
        run.heartbeat_at = now
        self._append_event(
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
            run.state = "completed"
            run.progress = 100
            run.finished_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            self._append_event(
                database,
                run,
                event_type="run.completed",
                message="Виджет готов",
                now=now,
                payload={"status": "completed", "worker_id": worker_id},
            )
            project_status = "free_result_ready"
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
                database, run.id, claim.next_stage
            )
            if staged is None or not isinstance(staged.payload.get("result"), dict):
                raise RuntimeError("stage result must be persisted before finalize")
            result = StageResult.from_dict(staged.payload["result"])
            await self._materialize_result(database, run, result, now)
            return await self._checkpoint_locked(
                database,
                run,
                stage=claim.next_stage,
                worker_id=claim.worker_id,
                now=now,
                result=result,
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
            return await self._checkpoint_locked(
                database,
                run,
                stage=stage,
                worker_id=worker_id,
                now=now,
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
    ) -> None:
        self.queue = queue
        self.worker_id = queue._validate_worker_id(worker_id)
        if heartbeat_interval <= 0 or idle_poll_interval <= 0:
            raise ValueError("worker intervals must be positive")
        self.stage_handler = stage_handler
        self.heartbeat_interval = float(heartbeat_interval)
        self.idle_poll_interval = float(idle_poll_interval)
        self._stop = asyncio.Event()

    async def _cancel_task(self, task: asyncio.Task[None]) -> None:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run_claim(self, claim: RunClaim) -> str | None:
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
        while not task.done():
            done, _ = await asyncio.wait(
                {task}, timeout=self.heartbeat_interval
            )
            if done:
                break
            if await self.queue.cancellation_requested(claim.run_id):
                await self._cancel_task(task)
                await self.queue.cancel_claim(
                    claim.run_id, worker_id=self.worker_id
                )
                return None
            try:
                await self.queue.heartbeat(
                    claim.run_id, worker_id=self.worker_id
                )
            except LeaseLostError:
                await self._cancel_task(task)
                raise
        result = await task
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
        claim = await self.queue.claim(self.worker_id)
        if claim is None:
            return False
        while True:
            next_stage = await self._run_claim(claim)
            if next_stage is None:
                break
            claim = await self.queue.continue_claim(
                claim.run_id, worker_id=self.worker_id
            )
        return True

    async def run_forever(self) -> None:
        while not self._stop.is_set():
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

    def stop(self) -> None:
        self._stop.set()


__all__ = [
    "BuilderWorker",
    "LeaseLostError",
    "OrchestratorStageHandler",
    "PostgresWorkerQueue",
    "RunCancelledError",
    "RunClaim",
    "StageInput",
    "StageResult",
]
