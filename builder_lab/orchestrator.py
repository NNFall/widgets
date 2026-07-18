from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from .engines.base import BuilderEngine, BuilderEngineError, EngineResult
from .models import (
    BuilderRequest,
    BuilderRunSnapshot,
    EngineName,
    RunStatus,
    Stage,
    ValidationIssue,
    WidgetArtifact,
)
from .store import RunStore, RunTerminal, TERMINAL_STATUSES
from .validation import issue_fingerprint, validate_artifact


DIRECT_STAGES = (
    Stage.ART_DIRECTION,
    Stage.FOUNDATION,
    Stage.IDENTITY,
    Stage.CONVERSATION,
    Stage.MOTION_POLISH,
)


class BuilderOrchestrator:
    def __init__(
        self,
        *,
        store: RunStore,
        engine_factories: dict[EngineName, Callable[[], BuilderEngine]],
    ) -> None:
        self.store = store
        self._factories = dict(engine_factories)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_tasks: dict[str, asyncio.Task[None]] = {}
        self._engines: dict[str, BuilderEngine] = {}
        self._stages: dict[str, Stage | None] = {}

    async def start(self, request: BuilderRequest) -> BuilderRunSnapshot:
        factory = self._factories.get(request.engine)
        if factory is None:
            raise BuilderEngineError(
                "provider_unavailable",
                f"Режим {request.engine.value} не настроен в этом запуске",
            )
        engine = factory()
        snapshot = await self.store.create(request)
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

    async def _run(
        self,
        run_id: str,
        request: BuilderRequest,
        engine: BuilderEngine,
    ) -> None:
        started = time.monotonic()
        try:
            await self.store.set_running(run_id)
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
        previous: WidgetArtifact | None = None
        for stage in DIRECT_STAGES:
            if await self._cancelled(run_id):
                raise asyncio.CancelledError
            self._stages[run_id] = stage
            revision = (previous.revision if previous else 0) + 1
            await self.store.append_event(
                run_id,
                event_type="stage.started",
                stage=stage,
                status="running",
                message=f"Начат этап {stage.value}",
                revision=revision,
            )
            result = await engine.generate(
                request=request,
                stage=stage,
                revision=revision,
                previous_artifact=previous,
            )
            await self.store.append_event(
                run_id,
                event_type="stage.completed",
                stage=stage,
                status="completed",
                message=f"Модель завершила этап {stage.value}",
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
            )
            await self.store.commit_artifact(run_id, candidate)
            await self.store.append_event(
                run_id,
                event_type="artifact.committed",
                stage=candidate.stage,
                status="completed",
                message="Валидная ревизия передана в preview",
                revision=candidate.revision,
            )
            previous = candidate

    async def _validate_and_repair(
        self,
        *,
        run_id: str,
        request: BuilderRequest,
        engine: BuilderEngine,
        candidate: WidgetArtifact,
        previous: WidgetArtifact | None,
    ) -> WidgetArtifact:
        previous_revision = previous.revision if previous else 0
        issues = validate_artifact(candidate, previous_revision=previous_revision)
        await self._record_validation(run_id, candidate, issues)
        if not issues:
            return candidate
        seen = {issue_fingerprint(issues)}
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
            result = await engine.generate(
                request=request,
                stage=Stage.VALIDATION,
                revision=candidate.revision,
                previous_artifact=candidate,
                repair_issues=issues,
            )
            candidate = result.artifact
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
            fingerprint = issue_fingerprint(issues)
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
        result: EngineResult = await engine.generate(
            request=request,
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
        issues = validate_artifact(result.artifact, previous_revision=0)
        await self._record_validation(run_id, result.artifact, issues)
        if issues:
            raise BuilderEngineError(
                "invalid_artifact",
                "Артефакт Antigravity не прошёл безопасную проверку",
                diagnostic="; ".join(issue.code for issue in issues),
            )
        await self.store.commit_artifact(run_id, result.artifact)
        await self.store.append_event(
            run_id,
            event_type="artifact.committed",
            stage=stage,
            status="completed",
            message="Валидный Antigravity-артефакт передан в preview",
            revision=1,
        )
