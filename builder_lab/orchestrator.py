from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

from .directions import DirectionBoardError, run_direction_board
from .engines.base import (
    BuilderEngine,
    BuilderEngineError,
    DirectBuilderEngine,
    EngineResult,
)
from .models import (
    BuilderRequest,
    BuilderRunSnapshot,
    DirectionProposal,
    DirectionRole,
    EngineName,
    RunStatus,
    Stage,
    ValidationIssue,
    WidgetArtifact,
    artifact_changed_fields,
    artifact_commit_message,
    stage_display_name,
)
from .store import RunTerminal, TERMINAL_STATUSES
from .store_protocol import RunStoreProtocol
from .validation import (
    issue_fingerprint,
    strip_reserved_runtime_attributes,
    validate_artifact,
)
from .visual_gate import VisualRepairGate
from .reference_pipeline import ReferenceAnalysisResult, ReferencePipelineError
from .modes import get_mode_policy
from .patterns.planner import CompositionPlanningError, plan_composition
from .patterns.registry import load_builtin_registry


DIRECT_STAGES = (
    Stage.ART_DIRECTION,
    Stage.FOUNDATION,
    Stage.IDENTITY,
    Stage.CONVERSATION,
    Stage.MOTION_POLISH,
)


def stages_for_mode(mode: str) -> tuple[Stage, ...]:
    return tuple(
        Stage(stage)
        for stage in get_mode_policy(mode).stage_sequence
        if stage != "reference_analysis"
    )


def _artifact_fingerprint(candidate: WidgetArtifact) -> str:
    return json.dumps(
        candidate.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class BuilderOrchestrator:
    def __init__(
        self,
        *,
        store: RunStoreProtocol,
        engine_factories: dict[EngineName, Callable[[], BuilderEngine]],
        visual_audit_factory: Callable[[], Any] | None = None,
        visual_critic_factory: Callable[[], Any] | None = None,
        visual_repair_verifier_factory: Callable[[], Any] | None = None,
        reference_analyzer: (
            Callable[[str], Any] | None
        ) = None,
    ) -> None:
        if (visual_audit_factory is None) != (visual_critic_factory is None):
            raise ValueError("visual audit and critic factories must be configured together")
        self.store = store
        self._factories = dict(engine_factories)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_tasks: dict[str, asyncio.Task[None]] = {}
        self._engines: dict[str, BuilderEngine] = {}
        self._stages: dict[str, Stage | None] = {}
        self._reference_analyzer = reference_analyzer
        self._visual_gate = (
            VisualRepairGate(
                store=store,
                audit_factory=visual_audit_factory,
                critic_factory=visual_critic_factory,
                verifier_factory=visual_repair_verifier_factory,
            )
            if visual_audit_factory is not None and visual_critic_factory is not None
            else None
        )

    async def start(self, request: BuilderRequest) -> BuilderRunSnapshot:
        factory = self._factories.get(request.engine)
        if factory is None:
            raise BuilderEngineError(
                "provider_unavailable",
                f"Режим {request.engine.value} не настроен в этом запуске",
            )
        engine = factory()
        try:
            snapshot = await self.store.create(request)
        except Exception:
            try:
                await engine.close()
            except Exception:
                pass
            raise
        self._engines[snapshot.run_id] = engine
        self._stages[snapshot.run_id] = None
        task = asyncio.create_task(
            self._run(snapshot.run_id, request, engine),
            name=f"kaigo-builder-{snapshot.run_id}",
        )
        self._tasks[snapshot.run_id] = task
        task.add_done_callback(
            lambda completed, run_id=snapshot.run_id: self._discard_task(
                run_id, completed
            )
        )
        return snapshot

    def _discard_task(self, run_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(run_id) is task:
            self._tasks.pop(run_id, None)
        if not task.cancelled():
            task.exception()

    async def wait(self, run_id: str) -> BuilderRunSnapshot:
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return await self.store.snapshot(run_id)

    async def cancel(self, run_id: str) -> bool:
        requested = await self.store.request_cancel(run_id)
        if not requested:
            return False
        engine = self._engines.get(run_id)
        remote_cancel: asyncio.Task[None] | None = None
        if engine is not None:
            remote_cancel = asyncio.create_task(
                self._best_effort_cancel(engine),
                name=f"kaigo-builder-provider-cancel-{run_id}",
            )
            self._cancel_tasks[run_id] = remote_cancel
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        if remote_cancel is not None:
            await asyncio.shield(remote_cancel)
        return True

    @staticmethod
    async def _best_effort_cancel(engine: BuilderEngine) -> None:
        try:
            await asyncio.wait_for(engine.cancel(), timeout=10)
        except (BuilderEngineError, TimeoutError, asyncio.TimeoutError):
            pass

    async def retry(self, run_id: str) -> BuilderRunSnapshot:
        snapshot = await self.store.snapshot(run_id)
        if snapshot.status not in {RunStatus.FAILED, RunStatus.CANCELLED}:
            raise ValueError("only a failed or cancelled run can be retried")
        return await self.start(snapshot.request)

    async def refine(
        self,
        source_run_id: str,
        message: str,
    ) -> BuilderRunSnapshot:
        if not isinstance(message, str):
            raise ValueError("refinement message must be text")
        instruction = message.strip()
        if not instruction or len(instruction) > 2_000 or "\x00" in instruction:
            raise ValueError("refinement message is invalid")
        source = await self.store.snapshot(source_run_id)
        if (
            source.status is not RunStatus.COMPLETED
            or source.request.engine is not EngineName.DIRECT
            or source.artifact is None
            or source.artifact.stage is not Stage.MOTION_POLISH
        ):
            raise ValueError("only a completed direct widget can be refined")
        factory = self._factories.get(EngineName.DIRECT)
        if factory is None:
            raise BuilderEngineError(
                "provider_unavailable",
                "Direct-режим не настроен в этом запуске",
            )
        separator = "\n\nПОЖЕЛАНИЕ ПОЛЬЗОВАТЕЛЯ К СЛЕДУЮЩЕЙ РЕВИЗИИ:\n"
        available = max(0, 12_000 - len(separator) - len(instruction))
        request = replace(
            source.request,
            brief=source.request.brief[:available] + separator + instruction,
        )
        engine = factory()
        try:
            snapshot = await self.store.create_seeded(request, source.artifact)
        except Exception:
            try:
                await engine.close()
            except Exception:
                pass
            raise
        self._engines[snapshot.run_id] = engine
        self._stages[snapshot.run_id] = Stage.MOTION_POLISH
        task = asyncio.create_task(
            self._run_refinement(
                snapshot.run_id,
                request,
                cast(DirectBuilderEngine, engine),
                source.artifact,
            ),
            name=f"kaigo-builder-refinement-{snapshot.run_id}",
        )
        self._tasks[snapshot.run_id] = task
        task.add_done_callback(
            lambda completed, run_id=snapshot.run_id: self._discard_task(
                run_id, completed
            )
        )
        return snapshot

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        await asyncio.gather(
            *(self.cancel(run_id) for run_id in list(self._tasks)),
            return_exceptions=True,
        )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cancelled(self, run_id: str) -> bool:
        return (await self.store.snapshot(run_id)).cancel_requested

    @staticmethod
    async def execute_stage(
        *,
        request: BuilderRequest,
        engine: BuilderEngine,
        stage: Stage,
        revision: int,
        previous_artifact: WidgetArtifact | None = None,
        selected_direction: DirectionProposal | None = None,
        repair_issues: tuple[ValidationIssue, ...] = (),
        composition: Any | None = None,
        pattern_candidate_pack: Any | None = None,
    ) -> EngineResult:
        """Execute one requested generation stage without advancing a run."""
        if request.engine is EngineName.DIRECT:
            return await cast(DirectBuilderEngine, engine).generate(
                request=request,
                stage=stage,
                revision=revision,
                previous_artifact=previous_artifact,
                selected_direction=selected_direction,
                repair_issues=repair_issues,
                composition=composition,
                pattern_candidate_pack=pattern_candidate_pack,
            )
        kwargs: dict[str, Any] = {
            "request": request,
            "stage": stage,
            "revision": revision,
            "previous_artifact": previous_artifact,
            "repair_issues": repair_issues,
            "composition": composition,
        }
        if pattern_candidate_pack is not None:
            kwargs["pattern_candidate_pack"] = pattern_candidate_pack
        return await engine.generate(**kwargs)

    async def _run(
        self,
        run_id: str,
        request: BuilderRequest,
        engine: BuilderEngine,
    ) -> None:
        started = time.monotonic()
        try:
            await self.store.set_running(run_id)
            request = await self._prepare_reference(run_id, request)
            if request.engine is EngineName.DIRECT:
                await self._run_direct(run_id, request, engine)
            else:
                await self._run_antigravity(run_id, request, engine)
            completed = await self.store.snapshot(run_id)
            await self.store.finish(
                run_id,
                RunStatus.COMPLETED,
                event_type="run.completed",
                stage=self._stages.get(run_id),
                message="Генерация завершена",
                revision=completed.artifact.revision,
                elapsed_seconds=time.monotonic() - started,
            )
        except asyncio.CancelledError:
            await self._mark_cancelled(run_id, started)
        except BuilderEngineError as exc:
            if exc.error_code == "run_cancelled" or await self._cancelled(run_id):
                await self._mark_cancelled(run_id, started)
            else:
                await self._mark_failed(run_id, exc, started)
        except RunTerminal:
            if await self._cancelled(run_id):
                await self._mark_cancelled(run_id, started)
            else:
                raise
        except Exception as exc:
            await self._mark_failed(
                run_id,
                BuilderEngineError(
                    "internal_error",
                    "Внутренняя ошибка builder-lab",
                    diagnostic=f"{type(exc).__name__}: {exc}",
                ),
                started,
            )
        finally:
            cancel_task = self._cancel_tasks.get(run_id)
            if cancel_task is not None and cancel_task is not asyncio.current_task():
                await asyncio.gather(asyncio.shield(cancel_task), return_exceptions=True)
            try:
                await engine.close()
            except Exception:
                pass
            self._cancel_tasks.pop(run_id, None)
            self._engines.pop(run_id, None)
            self._stages.pop(run_id, None)

    async def _prepare_reference(
        self,
        run_id: str,
        request: BuilderRequest,
    ) -> BuilderRequest:
        if not request.source_url or request.reference_context:
            return request
        if self._reference_analyzer is None:
            raise BuilderEngineError(
                "provider_unavailable",
                "Автоматический анализ сайта не настроен",
            )
        await self.store.append_event(
            run_id,
            event_type="reference.started",
            stage=None,
            status="running",
            message="Открываем сайт и делаем desktop/mobile снимки",
        )
        try:
            analysis = await self._reference_analyzer(request.source_url)
        except asyncio.CancelledError:
            raise
        except ReferencePipelineError as exc:
            await self.store.append_event(
                run_id,
                event_type="reference.failed",
                stage=None,
                status="failed",
                message=exc.public_message,
                error_code=exc.error_code,
                diagnostic=exc.diagnostic,
            )
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
        await self.store.update_request(run_id, grounded)
        await self.store.append_event(
            run_id,
            event_type="reference.completed",
            stage=None,
            status="completed",
            message="Визуальный стиль и подтверждённые факты сайта собраны",
            usage=analysis.usage,
            diagnostic=analysis.summary[:1_000],
        )
        return grounded

    async def _run_refinement(
        self,
        run_id: str,
        request: BuilderRequest,
        engine: DirectBuilderEngine,
        previous: WidgetArtifact,
    ) -> None:
        started = time.monotonic()
        direction = DirectionProposal(
            proposal_id="candidate-1",
            role=DirectionRole.INTERACTION_INVENTOR,
            title="Сохранить принятое направление",
            art_direction=(
                previous.art_direction[:1_200]
                or "Сохранить визуальный язык принятой версии."
            ),
            interaction_model=(
                "Изменить только то, что просит пользователь, сохранив работающий чат."
            ),
            safeguards=(
                "Не превращать доработку в новую несвязанную концепцию.",
                "Сохранить все реальные chat controls.",
            ),
        )
        try:
            await self.store.set_running(run_id)
            revision = previous.revision + 1
            self._stages[run_id] = Stage.MOTION_POLISH
            await self.store.append_event(
                run_id,
                event_type="refinement.started",
                stage=Stage.MOTION_POLISH,
                status="running",
                message="Gemini дорабатывает принятую версию по сообщению",
                revision=revision,
            )
            result = await engine.generate(
                request=request,
                stage=Stage.MOTION_POLISH,
                revision=revision,
                previous_artifact=previous,
                selected_direction=direction,
            )
            await self.store.append_event(
                run_id,
                event_type="stage.completed",
                stage=Stage.MOTION_POLISH,
                status="completed",
                message="Gemini завершил пользовательскую доработку",
                revision=revision,
                usage=result.usage,
                diagnostic=result.diagnostic,
            )
            candidate = await self._validate_and_repair(
                run_id=run_id,
                request=request,
                engine=engine,
                candidate=result.artifact,
                previous=previous,
                selected_direction=direction,
            )
            self._stages[run_id] = Stage.MOTION_POLISH
            if self._visual_gate is not None:
                candidate = await self._visual_gate.evaluate(
                    run_id=run_id,
                    request=request,
                    engine=engine,
                    candidate=candidate,
                    previous=previous,
                    selected_direction=direction,
                )
                await self.store.commit_visual_candidate(run_id)
            else:
                await self.store.commit_artifact(run_id, candidate)
                await self.store.append_event(
                    run_id,
                    event_type="artifact.committed",
                    stage=candidate.stage,
                    status="completed",
                    message=artifact_commit_message(candidate),
                    revision=candidate.revision,
                    changes=artifact_changed_fields(previous, candidate),
                )
            await self.store.finish(
                run_id,
                RunStatus.COMPLETED,
                event_type="run.completed",
                stage=Stage.MOTION_POLISH,
                message="Доработка завершена и проверена",
                revision=candidate.revision,
                elapsed_seconds=time.monotonic() - started,
            )
        except asyncio.CancelledError:
            await self._mark_cancelled(run_id, started)
        except BuilderEngineError as exc:
            if exc.error_code == "run_cancelled" or await self._cancelled(run_id):
                await self._mark_cancelled(run_id, started)
            else:
                await self._mark_failed(run_id, exc, started)
        except RunTerminal:
            if await self._cancelled(run_id):
                await self._mark_cancelled(run_id, started)
            else:
                raise
        except Exception as exc:
            await self._mark_failed(
                run_id,
                BuilderEngineError(
                    "internal_error",
                    "Внутренняя ошибка доработки",
                    diagnostic=f"{type(exc).__name__}: {exc}",
                ),
                started,
            )
        finally:
            cancel_task = self._cancel_tasks.get(run_id)
            if cancel_task is not None and cancel_task is not asyncio.current_task():
                await asyncio.gather(
                    asyncio.shield(cancel_task),
                    return_exceptions=True,
                )
            try:
                await engine.close()
            except Exception:
                pass
            self._cancel_tasks.pop(run_id, None)
            self._engines.pop(run_id, None)
            self._stages.pop(run_id, None)

    async def _mark_cancelled(self, run_id: str, started: float) -> None:
        snapshot = await self.store.snapshot(run_id)
        if snapshot.status in TERMINAL_STATUSES:
            return
        try:
            await self.store.finish(
                run_id,
                RunStatus.CANCELLED,
                event_type="run.cancelled",
                stage=self._stages.get(run_id),
                message="Генерация отменена",
                revision=snapshot.artifact.revision if snapshot.artifact else None,
                error_code="run_cancelled",
                elapsed_seconds=time.monotonic() - started,
            )
        except RunTerminal:
            pass

    async def _mark_failed(
        self,
        run_id: str,
        error: BuilderEngineError,
        started: float,
    ) -> None:
        snapshot = await self.store.snapshot(run_id)
        if snapshot.status in TERMINAL_STATUSES:
            return
        stage = self._stages.get(run_id)
        if stage is not None:
            await self.store.append_event(
                run_id,
                event_type="stage.failed",
                stage=stage,
                status="failed",
                message=error.public_message,
                revision=snapshot.artifact.revision if snapshot.artifact else None,
                error_code=error.error_code,
                diagnostic=error.diagnostic,
            )
        try:
            await self.store.finish(
                run_id,
                RunStatus.FAILED,
                event_type="run.failed",
                stage=stage,
                message=error.public_message,
                revision=snapshot.artifact.revision if snapshot.artifact else None,
                error_code=error.error_code,
                diagnostic=error.diagnostic,
                elapsed_seconds=time.monotonic() - started,
            )
        except RunTerminal:
            pass

    async def _run_direct(
        self,
        run_id: str,
        request: BuilderRequest,
        engine: BuilderEngine,
    ) -> None:
        if not all(
            hasattr(engine, method)
            for method in ("propose_direction", "judge_directions")
        ):
            raise BuilderEngineError(
                "provider_unavailable",
                "Direct-режим не поддерживает выбор визуального направления",
            )
        direct_engine = cast(DirectBuilderEngine, engine)
        try:
            direction = await run_direction_board(engine=direct_engine, request=request)
        except DirectionBoardError as exc:
            await self.store.append_event(
                run_id,
                event_type="direction.failed",
                stage=None,
                status="failed",
                message=exc.public_message,
                usage=exc.usage,
                error_code=exc.error_code,
                diagnostic=exc.diagnostic,
            )
            raise
        selected_direction = direction.selected
        await self.store.append_event(
            run_id,
            event_type="direction.judged",
            stage=None,
            status="completed",
            message=f"Выбрано направление: {selected_direction.title}",
            usage=direction.usage,
        )
        previous: WidgetArtifact | None = None
        composition: Any | None = None
        for stage in stages_for_mode("direct"):
            if await self._cancelled(run_id):
                raise asyncio.CancelledError
            self._stages[run_id] = stage
            revision = (previous.revision if previous else 0) + 1
            await self.store.append_event(
                run_id,
                event_type="stage.started",
                stage=stage,
                status="running",
                message=f"Начат этап: {stage_display_name(stage)}",
                revision=revision,
            )
            if stage is Stage.COMPOSITION:
                try:
                    planned = await plan_composition(
                        direct_engine,
                        request,
                        selected_direction,
                        load_builtin_registry(),
                    )
                except CompositionPlanningError as exc:
                    raise BuilderEngineError(
                        "invalid_structured_output",
                        "Не удалось подобрать проверенную композицию виджета",
                        diagnostic=str(exc),
                        usage=exc.usage,
                    ) from exc
                composition = planned.resolved
                await self.store.append_event(
                    run_id,
                    event_type="stage.completed",
                    stage=stage,
                    status="completed",
                    message=planned.plan.summary,
                    revision=previous.revision if previous else None,
                    usage=planned.usage,
                    diagnostic="pattern composition persisted in the durable worker",
                )
                continue
            result = await self.execute_stage(
                request=request,
                engine=direct_engine,
                stage=stage,
                revision=revision,
                previous_artifact=previous,
                selected_direction=selected_direction,
                composition=composition,
            )
            await self.store.append_event(
                run_id,
                event_type="stage.completed",
                stage=stage,
                status="completed",
                message=f"Модель завершила этап: {stage_display_name(stage)}",
                revision=revision,
                usage=result.usage,
                diagnostic=result.diagnostic,
            )
            candidate = await self._validate_and_repair(
                run_id=run_id,
                request=request,
                engine=direct_engine,
                candidate=result.artifact,
                previous=previous,
                selected_direction=selected_direction,
                composition=composition,
            )
            self._stages[run_id] = stage
            commit_event_recorded = False
            if stage is Stage.MOTION_POLISH and self._visual_gate is not None:
                if previous is None:
                    raise BuilderEngineError(
                        "visual_quality_failed",
                        "Финальная визуальная проверка не может быть запущена",
                        diagnostic="missing revision 4",
                    )
                candidate = await self._visual_gate.evaluate(
                    run_id=run_id,
                    request=request,
                    engine=direct_engine,
                    candidate=candidate,
                    previous=previous,
                    selected_direction=selected_direction,
                    composition=composition,
                )
                await self.store.commit_visual_candidate(run_id)
                commit_event_recorded = True
            else:
                await self.store.commit_artifact(run_id, candidate)
            if not commit_event_recorded:
                await self.store.append_event(
                    run_id,
                    event_type="artifact.committed",
                    stage=candidate.stage,
                    status="completed",
                    message=artifact_commit_message(candidate),
                    revision=candidate.revision,
                    changes=artifact_changed_fields(previous, candidate),
                )
            previous = candidate

    async def _validate_and_repair(
        self,
        *,
        run_id: str,
        request: BuilderRequest,
        engine: DirectBuilderEngine,
        candidate: WidgetArtifact,
        previous: WidgetArtifact | None,
        selected_direction: DirectionProposal,
        composition: Any | None = None,
        pattern_candidate_pack: Any | None = None,
    ) -> WidgetArtifact:
        previous_revision = previous.revision if previous else 0
        candidate = strip_reserved_runtime_attributes(candidate)
        issues = validate_artifact(candidate, previous_revision=previous_revision)
        await self._record_validation(run_id, candidate, issues)
        if not issues:
            return candidate
        seen = {(issue_fingerprint(issues), _artifact_fingerprint(candidate))}
        for attempt in range(1, request.max_repairs + 1):
            self._stages[run_id] = Stage.VALIDATION
            await self.store.append_event(
                run_id,
                event_type="repair.started",
                stage=Stage.VALIDATION,
                status="running",
                message=f"Исправление артефакта: попытка {attempt}",
                revision=candidate.revision,
                issues=issues,
            )
            result = await self.execute_stage(
                request=request,
                engine=engine,
                stage=candidate.stage,
                revision=candidate.revision,
                previous_artifact=candidate,
                repair_issues=issues,
                selected_direction=selected_direction,
                composition=composition,
                pattern_candidate_pack=pattern_candidate_pack,
            )
            candidate = strip_reserved_runtime_attributes(result.artifact)
            await self.store.append_event(
                run_id,
                event_type="repair.completed",
                stage=Stage.VALIDATION,
                status="completed",
                message=f"Модель завершила исправление {attempt}",
                revision=candidate.revision,
                usage=result.usage,
                diagnostic=result.diagnostic,
            )
            issues = validate_artifact(candidate, previous_revision=previous_revision)
            await self._record_validation(run_id, candidate, issues)
            if not issues:
                return candidate
            fingerprint = (
                issue_fingerprint(issues),
                _artifact_fingerprint(candidate),
            )
            if fingerprint in seen:
                break
            seen.add(fingerprint)
        raise BuilderEngineError(
            "invalid_artifact",
            "Артефакт не прошёл безопасную проверку",
            diagnostic="; ".join(issue.code for issue in issues),
        )

    async def _record_validation(
        self,
        run_id: str,
        candidate: WidgetArtifact,
        issues: tuple[ValidationIssue, ...],
    ) -> None:
        await self.store.append_event(
            run_id,
            event_type="artifact.validated",
            stage=candidate.stage,
            status="failed" if issues else "completed",
            message=(
                f"Валидатор нашёл ошибок: {len(issues)}"
                if issues
                else "Артефакт прошёл безопасную проверку"
            ),
            revision=candidate.revision,
            issues=issues,
        )

    async def _run_antigravity(
        self,
        run_id: str,
        request: BuilderRequest,
        engine: BuilderEngine,
    ) -> None:
        stage = Stage.AGENT_BUILD
        self._stages[run_id] = stage
        await self.store.append_event(
            run_id,
            event_type="stage.started",
            stage=stage,
            status="running",
            message="Antigravity начал агентскую сборку",
            revision=1,
        )
        result: EngineResult = await self.execute_stage(
            request=request,
            engine=engine,
            stage=stage,
            revision=1,
        )
        await self.store.append_event(
            run_id,
            event_type="stage.completed",
            stage=stage,
            status="completed",
            message="Antigravity завершил агентскую сборку",
            revision=1,
            usage=result.usage,
            diagnostic=result.diagnostic,
        )
        candidate = result.artifact
        issues = validate_artifact(candidate, previous_revision=0)
        await self._record_validation(run_id, candidate, issues)
        seen = {(issue_fingerprint(issues), _artifact_fingerprint(candidate))}
        for attempt in range(1, request.max_repairs + 1):
            if not issues:
                break
            self._stages[run_id] = Stage.VALIDATION
            await self.store.append_event(
                run_id,
                event_type="repair.started",
                stage=Stage.VALIDATION,
                status="running",
                message=f"Antigravity исправляет серверные ошибки: попытка {attempt}",
                revision=candidate.revision,
                issues=issues,
            )
            repaired = await self.execute_stage(
                request=request,
                engine=engine,
                stage=Stage.AGENT_BUILD,
                revision=1,
                previous_artifact=candidate,
                repair_issues=issues,
            )
            candidate = repaired.artifact
            await self.store.append_event(
                run_id,
                event_type="repair.completed",
                stage=Stage.VALIDATION,
                status="completed",
                message=f"Antigravity завершил исправление {attempt}",
                revision=candidate.revision,
                usage=repaired.usage,
                diagnostic=repaired.diagnostic,
            )
            issues = validate_artifact(candidate, previous_revision=0)
            await self._record_validation(run_id, candidate, issues)
            if not issues:
                break
            fingerprint = (
                issue_fingerprint(issues),
                _artifact_fingerprint(candidate),
            )
            if fingerprint in seen:
                break
            seen.add(fingerprint)
        if issues:
            raise BuilderEngineError(
                "invalid_artifact",
                "Артефакт Antigravity не прошёл безопасную проверку",
                diagnostic="; ".join(issue.code for issue in issues),
            )
        await self.store.commit_artifact(run_id, candidate)
        await self.store.append_event(
            run_id,
            event_type="artifact.committed",
            stage=stage,
            status="completed",
            message=artifact_commit_message(candidate),
            revision=1,
            changes=artifact_changed_fields(None, candidate),
        )
