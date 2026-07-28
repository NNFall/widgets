from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID
from weakref import WeakValueDictionary

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.saas.models import GenerationEvent, GenerationRun, Project


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
    def _claim_from_run(run: GenerationRun) -> RunClaim:
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
                    },
                )
                await database.flush()
                return self._claim_from_run(run)

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
            return self._claim_from_run(run)

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
                },
            )
            await database.flush()
            return self._claim_from_run(run)

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

    async def complete_stage(
        self,
        run_id: UUID,
        stage: str,
        *,
        worker_id: str,
    ) -> str | None:
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
            if await self._cancel_requested(database, run_id):
                raise RunCancelledError(f"run {run_id} was cancelled")
            if run.last_completed_stage == stage:
                replayed_next_stage = self.next_stage(run.mode, stage)
                if run.current_stage != replayed_next_stage:
                    raise ValueError(
                        f"checkpoint {stage!r} does not match current run state"
                    )
                run.heartbeat_at = now
                run.lease_expires_at = self._lease_expiry(now)
                return replayed_next_stage
            expected_stage = self.next_stage(run.mode, run.last_completed_stage)
            if stage != expected_stage or run.current_stage != expected_stage:
                raise ValueError(
                    f"cannot checkpoint stage {stage!r}; expected {expected_stage!r}"
                )
            stages = self.stage_sequence(run.mode)
            stage_index = stages.index(stage)
            next_stage = (
                stages[stage_index + 1]
                if stage_index + 1 < len(stages)
                else None
            )
            run.last_completed_stage = stage
            run.current_stage = next_stage
            run.progress = int(((stage_index + 1) * 100) / len(stages))
            run.heartbeat_at = now
            self._append_event(
                database,
                run,
                event_type="stage.completed",
                message=(
                    "Анализ исходного сайта завершён"
                    if stage == "reference_analysis"
                    else f"Завершён этап: {STAGE_PUBLIC_NAMES[stage]}"
                ),
                now=now,
                payload={
                    "stage": stage,
                    "status": "completed",
                    "worker_id": worker_id,
                    "next_stage": next_stage,
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
                .where(
                    Project.id == run.project_id,
                    Project.active_run_id == run.id,
                )
                .values(status=project_status)
            )
            return next_stage


class BuilderWorker:
    def __init__(
        self,
        *,
        queue: PostgresWorkerQueue,
        worker_id: str,
        stage_handler: Callable[[RunClaim], Awaitable[None]],
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
        task = asyncio.create_task(
            self.stage_handler(claim),
            name=f"kaigo-stage-{claim.run_id}-{claim.next_stage}",
        )
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
        await task
        if await self.queue.cancellation_requested(claim.run_id):
            await self.queue.cancel_claim(claim.run_id, worker_id=self.worker_id)
            return None
        try:
            return await self.queue.complete_stage(
                claim.run_id,
                claim.next_stage,
                worker_id=self.worker_id,
            )
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
    "PostgresWorkerQueue",
    "RunCancelledError",
    "RunClaim",
]
