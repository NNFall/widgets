from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .models import (
    BuilderEvent,
    BuilderRequest,
    BuilderRunSnapshot,
    RunStatus,
    Stage,
    TokenUsage,
    ValidationIssue,
    WidgetArtifact,
    artifact_changed_fields,
    artifact_commit_message,
)


TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


class RunNotFound(KeyError):
    pass


class RunTerminal(RuntimeError):
    pass


class RunCapacityExceeded(RuntimeError):
    pass


class VisualCandidateCapacityExceeded(RuntimeError):
    pass


class ArtifactNotFound(KeyError):
    pass


@dataclass
class _RunRecord:
    run_id: str
    request: BuilderRequest
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    events: list[BuilderEvent] = field(default_factory=list)
    artifact: WidgetArtifact | None = None
    visual_candidate: WidgetArtifact | None = None
    draft_artifact: WidgetArtifact | None = None
    quality_status: str = "pending"
    artifacts: dict[int, WidgetArtifact] = field(default_factory=dict)
    usage: TokenUsage = field(default_factory=TokenUsage)
    elapsed_seconds: float = 0.0
    error_code: str | None = None
    cancel_requested: bool = False


def _copy_artifact(artifact: WidgetArtifact | None) -> WidgetArtifact | None:
    return WidgetArtifact.from_dict(artifact.to_dict()) if artifact else None


class RunStore:
    def __init__(self, *, ttl_seconds: int = 3600, max_runs: int = 100) -> None:
        if ttl_seconds < 1 or max_runs < 1:
            raise ValueError("store limits must be positive")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max_runs = max_runs
        self._runs: dict[str, _RunRecord] = {}
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition(self._lock)

    def _record(self, run_id: str) -> _RunRecord:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFound(run_id) from exc

    def _snapshot(self, record: _RunRecord) -> BuilderRunSnapshot:
        return BuilderRunSnapshot(
            run_id=record.run_id,
            request=record.request,
            status=record.status,
            created_at=record.created_at,
            updated_at=record.updated_at,
            latest_sequence=record.events[-1].sequence if record.events else 0,
            artifact=_copy_artifact(record.artifact),
            draft_artifact=_copy_artifact(record.draft_artifact),
            quality_status=record.quality_status,
            usage=record.usage,
            elapsed_seconds=record.elapsed_seconds,
            error_code=record.error_code,
            cancel_requested=record.cancel_requested,
        )

    def _append_locked(
        self,
        record: _RunRecord,
        *,
        event_type: str,
        stage: Stage | None,
        status: str,
        message: str,
        revision: int | None = None,
        usage: TokenUsage | None = None,
        issues: tuple[ValidationIssue, ...] = (),
        changes: tuple[str, ...] = (),
        error_code: str | None = None,
        diagnostic: str | None = None,
    ) -> BuilderEvent:
        delta = usage or TokenUsage()
        event = BuilderEvent.create(
            run_id=record.run_id,
            sequence=len(record.events) + 1,
            event_type=event_type,
            stage=stage,
            status=status,
            message=message,
            revision=revision,
            usage=delta,
            issues=issues,
            changes=changes,
            error_code=error_code,
            diagnostic=diagnostic,
        )
        record.events.append(event)
        record.usage = record.usage + delta
        record.updated_at = event.timestamp
        self._changed.notify_all()
        return event

    def _prune_expired_locked(self, now: datetime) -> int:
        expired = [
            run_id
            for run_id, record in self._runs.items()
            if record.status in TERMINAL_STATUSES
            and now - record.updated_at >= self._ttl
        ]
        for run_id in expired:
            del self._runs[run_id]
        return len(expired)

    def _prune_capacity_locked(self, target: int) -> int:
        removed = 0
        while len(self._runs) > target:
            terminal = [
                record
                for record in self._runs.values()
                if record.status in TERMINAL_STATUSES
            ]
            if not terminal:
                break
            oldest = min(
                terminal,
                key=lambda record: (record.updated_at, record.created_at),
            )
            del self._runs[oldest.run_id]
            removed += 1
        return removed

    async def create(self, request: BuilderRequest) -> BuilderRunSnapshot:
        now = datetime.now(timezone.utc)
        async with self._changed:
            self._prune_expired_locked(now)
            self._prune_capacity_locked(self._max_runs - 1)
            if len(self._runs) >= self._max_runs:
                raise RunCapacityExceeded("all builder run slots are active")
            run_id = secrets.token_urlsafe(24)
            while run_id in self._runs:
                run_id = secrets.token_urlsafe(24)
            record = _RunRecord(
                run_id=run_id,
                request=request,
                status=RunStatus.CREATED,
                created_at=now,
                updated_at=now,
            )
            self._runs[run_id] = record
            self._append_locked(
                record,
                event_type="run.created",
                stage=None,
                status="created",
                message="Запуск создан",
            )
            return self._snapshot(record)

    async def create_seeded(
        self,
        request: BuilderRequest,
        artifact: WidgetArtifact,
    ) -> BuilderRunSnapshot:
        now = datetime.now(timezone.utc)
        async with self._changed:
            self._prune_expired_locked(now)
            self._prune_capacity_locked(self._max_runs - 1)
            if len(self._runs) >= self._max_runs:
                raise RunCapacityExceeded("all builder run slots are active")
            run_id = secrets.token_urlsafe(24)
            while run_id in self._runs:
                run_id = secrets.token_urlsafe(24)
            seed = _copy_artifact(artifact)
            record = _RunRecord(
                run_id=run_id,
                request=request,
                status=RunStatus.CREATED,
                created_at=now,
                updated_at=now,
                artifact=seed,
                artifacts={artifact.revision: _copy_artifact(artifact)},
            )
            self._runs[run_id] = record
            self._append_locked(
                record,
                event_type="run.created",
                stage=None,
                status="created",
                message="Запуск создан",
            )
            self._append_locked(
                record,
                event_type="artifact.seeded",
                stage=artifact.stage,
                status="completed",
                message="Принятая версия перенесена в доработку",
                revision=artifact.revision,
            )
            return self._snapshot(record)

    async def snapshot(self, run_id: str) -> BuilderRunSnapshot:
        async with self._lock:
            return self._snapshot(self._record(run_id))

    async def update_request(
        self,
        run_id: str,
        request: BuilderRequest,
    ) -> None:
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES or record.cancel_requested:
                raise RunTerminal(run_id)
            record.request = request
            record.updated_at = datetime.now(timezone.utc)
            self._changed.notify_all()

    async def append_event(
        self,
        run_id: str,
        *,
        event_type: str,
        stage: Stage | None,
        status: str,
        message: str,
        revision: int | None = None,
        usage: TokenUsage | None = None,
        issues: tuple[ValidationIssue, ...] = (),
        changes: tuple[str, ...] = (),
        error_code: str | None = None,
        diagnostic: str | None = None,
    ) -> BuilderEvent:
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES:
                raise RunTerminal(run_id)
            return self._append_locked(
                record,
                event_type=event_type,
                stage=stage,
                status=status,
                message=message,
                revision=revision,
                usage=usage,
                issues=issues,
                changes=changes,
                error_code=error_code,
                diagnostic=diagnostic,
            )

    async def set_running(self, run_id: str) -> None:
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES:
                raise RunTerminal(run_id)
            record.status = RunStatus.RUNNING
            record.updated_at = datetime.now(timezone.utc)
            self._changed.notify_all()

    async def commit_artifact(self, run_id: str, artifact: WidgetArtifact) -> None:
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES or record.cancel_requested:
                raise RunTerminal(run_id)
            if record.artifact and artifact.revision <= record.artifact.revision:
                raise ValueError("artifact revision must increase monotonically")
            record.artifact = _copy_artifact(artifact)
            record.artifacts[artifact.revision] = _copy_artifact(artifact)
            record.quality_status = "verified"
            record.updated_at = datetime.now(timezone.utc)
            self._changed.notify_all()

    async def stage_visual_candidate(
        self, run_id: str, artifact: WidgetArtifact
    ) -> None:
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES or record.cancel_requested:
                raise RunTerminal(run_id)
            if record.artifact and artifact.revision <= record.artifact.revision:
                raise ValueError("visual candidate revision must exceed public revision")
            record.visual_candidate = _copy_artifact(artifact)
            record.updated_at = datetime.now(timezone.utc)
            self._changed.notify_all()

    async def visual_candidate(self, run_id: str) -> WidgetArtifact:
        async with self._lock:
            candidate = self._record(run_id).visual_candidate
            if candidate is None:
                raise ArtifactNotFound((run_id, "visual_candidate"))
            return _copy_artifact(candidate)

    async def stage_visual_draft(
        self,
        run_id: str,
        artifact: WidgetArtifact,
    ) -> None:
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES or record.cancel_requested:
                raise RunTerminal(run_id)
            if record.artifact and artifact.revision <= record.artifact.revision:
                raise ValueError("visual draft revision must exceed public revision")
            record.draft_artifact = _copy_artifact(artifact)
            record.quality_status = "needs_repair"
            record.updated_at = datetime.now(timezone.utc)
            self._changed.notify_all()

    async def commit_visual_candidate(self, run_id: str) -> WidgetArtifact:
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES or record.cancel_requested:
                raise RunTerminal(run_id)
            candidate = record.visual_candidate
            if candidate is None:
                raise ArtifactNotFound((run_id, "visual_candidate"))
            if record.artifact and candidate.revision <= record.artifact.revision:
                raise ValueError("artifact revision must increase monotonically")
            committed = _copy_artifact(candidate)
            previous = _copy_artifact(record.artifact)
            record.artifact = committed
            record.artifacts[candidate.revision] = _copy_artifact(candidate)
            record.visual_candidate = None
            record.draft_artifact = None
            record.quality_status = "verified"
            self._append_locked(
                record,
                event_type="artifact.committed",
                stage=candidate.stage,
                status="completed",
                message=artifact_commit_message(candidate),
                revision=candidate.revision,
                changes=artifact_changed_fields(previous, candidate),
            )
            return _copy_artifact(committed)

    async def artifact(
        self, run_id: str, revision: int | None = None
    ) -> WidgetArtifact:
        async with self._lock:
            record = self._record(run_id)
            if revision is None:
                candidate = record.artifact
            else:
                candidate = record.artifacts.get(revision)
            if candidate is None:
                raise ArtifactNotFound((run_id, revision))
            return _copy_artifact(candidate)

    async def preview_artifact(
        self,
        run_id: str,
        revision: int | None = None,
    ) -> WidgetArtifact:
        async with self._lock:
            record = self._record(run_id)
            candidate = (
                record.artifacts.get(revision)
                if revision is not None
                else record.artifact
            )
            if candidate is None and record.draft_artifact is not None:
                if revision is None or record.draft_artifact.revision == revision:
                    candidate = record.draft_artifact
            if candidate is None:
                raise ArtifactNotFound((run_id, revision))
            return _copy_artifact(candidate)

    async def events_after(self, run_id: str, sequence: int) -> tuple[BuilderEvent, ...]:
        async with self._lock:
            record = self._record(run_id)
            return tuple(event for event in record.events if event.sequence > sequence)

    async def wait_for_events(
        self,
        run_id: str,
        sequence: int,
        *,
        timeout: float = 15.0,
    ) -> tuple[BuilderEvent, ...]:
        async with self._changed:
            record = self._record(run_id)
            available = tuple(event for event in record.events if event.sequence > sequence)
            if available or record.status in TERMINAL_STATUSES:
                return available
            try:
                await asyncio.wait_for(
                    self._changed.wait_for(
                        lambda: any(event.sequence > sequence for event in record.events)
                        or record.status in TERMINAL_STATUSES
                    ),
                    timeout=timeout,
                )
            except TimeoutError:
                return ()
            return tuple(event for event in record.events if event.sequence > sequence)

    async def request_cancel(self, run_id: str) -> bool:
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES or record.cancel_requested:
                return False
            record.cancel_requested = True
            record.updated_at = datetime.now(timezone.utc)
            self._changed.notify_all()
            return True

    async def finish(
        self,
        run_id: str,
        status: RunStatus,
        *,
        event_type: str,
        stage: Stage | None,
        message: str,
        revision: int | None = None,
        error_code: str | None = None,
        diagnostic: str | None = None,
        elapsed_seconds: float = 0.0,
    ) -> BuilderEvent:
        if status not in TERMINAL_STATUSES:
            raise ValueError("terminal status is required")
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES:
                raise RunTerminal(run_id)
            event = self._append_locked(
                record,
                event_type=event_type,
                stage=stage,
                status=status.value,
                message=message,
                revision=revision,
                error_code=error_code,
                diagnostic=diagnostic,
            )
            record.status = status
            record.error_code = error_code
            record.elapsed_seconds = max(0.0, elapsed_seconds)
            return event

    async def mark_terminal(
        self,
        run_id: str,
        status: RunStatus,
        *,
        error_code: str | None = None,
        elapsed_seconds: float = 0.0,
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("terminal status is required")
        async with self._changed:
            record = self._record(run_id)
            if record.status in TERMINAL_STATUSES:
                raise RunTerminal(run_id)
            record.status = status
            record.error_code = error_code
            record.elapsed_seconds = max(0.0, elapsed_seconds)
            record.updated_at = datetime.now(timezone.utc)
            self._changed.notify_all()

    async def prune(self, *, now: datetime | None = None) -> int:
        async with self._changed:
            removed = self._prune_expired_locked(now or datetime.now(timezone.utc))
            removed += self._prune_capacity_locked(self._max_runs)
            if removed:
                self._changed.notify_all()
            return removed
