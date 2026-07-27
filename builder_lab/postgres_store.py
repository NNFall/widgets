from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import replace
from datetime import datetime, timezone
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.saas.models import GenerationArtifact, GenerationEvent, GenerationRun, Project

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
from .store import ArtifactNotFound, RunNotFound, RunTerminal, TERMINAL_STATUSES


_RUN_LOCKS: dict[tuple[int, UUID], asyncio.Lock] = {}


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _json_copy(payload: dict) -> dict:
    return json.loads(json.dumps(payload, ensure_ascii=False))


class PostgresRunStore:
    """Durable builder store backed by the SaaS generation tables.

    PostgreSQL serializes sequence reservation with ``SELECT ... FOR UPDATE``.
    The process-local lock also makes the established SQLite test fixture model
    deterministic, where row-level ``FOR UPDATE`` is not implemented.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        project_id: UUID,
    ) -> None:
        self._sessions = session_factory
        self._project_id = project_id
        self._namespace = id(session_factory.kw.get("bind"))
        self._visual_candidates: dict[UUID, WidgetArtifact] = {}

    def _run_uuid(self, run_id: str) -> UUID:
        try:
            return UUID(run_id)
        except (TypeError, ValueError) as exc:
            raise RunNotFound(run_id) from exc

    def _lock(self, run_id: UUID) -> asyncio.Lock:
        return _RUN_LOCKS.setdefault((self._namespace, run_id), asyncio.Lock())

    async def _locked_run(
        self, database: AsyncSession, run_id: UUID
    ) -> GenerationRun:
        result = await database.execute(
            select(GenerationRun)
            .where(
                GenerationRun.id == run_id,
                GenerationRun.project_id == self._project_id,
            )
            .with_for_update()
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise RunNotFound(str(run_id))
        return record

    async def _run(
        self, database: AsyncSession, run_id: UUID
    ) -> GenerationRun:
        record = await database.get(GenerationRun, run_id)
        if record is None or record.project_id != self._project_id:
            raise RunNotFound(str(run_id))
        return record

    @staticmethod
    def _event_from_record(record: GenerationEvent) -> BuilderEvent:
        payload = dict(record.payload)
        payload.setdefault("run_id", str(record.run_id))
        payload.setdefault("sequence", record.sequence)
        payload.setdefault("timestamp", _utc(record.created_at).isoformat())
        payload.setdefault("type", record.event_type)
        payload.setdefault("status", "")
        payload.setdefault("message", record.public_message or "")
        event = BuilderEvent.from_dict(payload)
        return replace(event, diagnostic=payload.get("diagnostic"))

    @staticmethod
    def _artifact_from_record(record: GenerationArtifact) -> WidgetArtifact:
        payload = record.config.get("artifact") if isinstance(record.config, dict) else None
        if isinstance(payload, dict):
            return WidgetArtifact.from_dict(payload)
        return WidgetArtifact(
            schema_version="1",
            revision=record.revision,
            stage=Stage(record.stage),
            art_direction="",
            body_html=record.html,
            css=record.css,
            javascript=record.javascript,
        )

    @staticmethod
    def _new_artifact_record(
        run_id: UUID,
        artifact: WidgetArtifact,
        *,
        quality_status: str,
    ) -> GenerationArtifact:
        return GenerationArtifact(
            run_id=run_id,
            revision=artifact.revision,
            stage=artifact.stage.value,
            html=artifact.body_html,
            css=artifact.css,
            javascript=artifact.javascript,
            config={"artifact": _json_copy(artifact.to_dict())},
            quality_status=quality_status,
            provenance={},
        )

    @staticmethod
    def _ensure_mutable(record: GenerationRun, *, cancelled: bool = False) -> None:
        if RunStatus(record.state) in TERMINAL_STATUSES or cancelled:
            raise RunTerminal(str(record.id))

    async def _cancel_requested(
        self, database: AsyncSession, run_id: UUID
    ) -> bool:
        result = await database.execute(
            select(GenerationEvent.id).where(
                GenerationEvent.run_id == run_id,
                GenerationEvent.event_type == "run.cancel_requested",
            )
        )
        return result.first() is not None

    def _append_record(
        self,
        database: AsyncSession,
        run: GenerationRun,
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
        payload_extra: dict | None = None,
    ) -> BuilderEvent:
        sequence = run.next_event_sequence
        run.next_event_sequence += 1
        event = BuilderEvent.create(
            run_id=str(run.id),
            sequence=sequence,
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
        payload = event.to_dict()
        if diagnostic is not None:
            payload["diagnostic"] = diagnostic
        if payload_extra:
            payload.update(_json_copy(payload_extra))
        values = {
            "run_id": run.id,
            "sequence": sequence,
            "event_type": event_type,
            "public_message": message,
            "payload": payload,
            "created_at": event.timestamp,
        }
        if database.get_bind().dialect.name == "sqlite":
            values["id"] = secrets.randbits(62)
        database.add(GenerationEvent(**values))
        run.heartbeat_at = event.timestamp
        if stage is not None:
            run.current_stage = stage.value
            if status == "completed":
                run.last_completed_stage = stage.value
        return event

    async def _create(
        self,
        request: BuilderRequest,
        seed: WidgetArtifact | None = None,
    ) -> BuilderRunSnapshot:
        run_id = uuid4()
        async with self._sessions() as database, database.begin():
            project = await database.get(Project, self._project_id)
            if project is None:
                raise RunNotFound(f"project {self._project_id}")
            run = GenerationRun(
                id=run_id,
                project_id=self._project_id,
                mode=request.engine.value,
                state=RunStatus.CREATED.value,
                progress=0,
                next_event_sequence=1,
                idempotency_key=f"builder:{uuid4().hex}",
            )
            database.add(run)
            await database.flush()
            self._append_record(
                database,
                run,
                event_type="run.created",
                stage=None,
                status="created",
                message="Builder run created",
                payload_extra={"request": request.to_dict()},
            )
            if seed is not None:
                database.add(
                    self._new_artifact_record(run.id, seed, quality_status="verified")
                )
                self._append_record(
                    database,
                    run,
                    event_type="artifact.seeded",
                    stage=seed.stage,
                    status="completed",
                    message="Accepted artifact copied into refinement",
                    revision=seed.revision,
                )
                project.active_revision = seed.revision
            project.active_run_id = run.id
            project.status = "generating"
        return await self.snapshot(str(run_id))

    async def create(self, request: BuilderRequest) -> BuilderRunSnapshot:
        return await self._create(request)

    async def create_seeded(
        self, request: BuilderRequest, artifact: WidgetArtifact
    ) -> BuilderRunSnapshot:
        return await self._create(request, artifact)

    async def snapshot(self, run_id: str) -> BuilderRunSnapshot:
        run_uuid = self._run_uuid(run_id)
        async with self._sessions() as database:
            run = await self._run(database, run_uuid)
            event_result = await database.execute(
                select(GenerationEvent)
                .where(GenerationEvent.run_id == run_uuid)
                .order_by(GenerationEvent.sequence)
            )
            event_records = list(event_result.scalars())
            events = [self._event_from_record(record) for record in event_records]
            created_payload = next(
                (
                    record.payload
                    for record in event_records
                    if record.event_type == "run.created"
                ),
                None,
            )
            if not isinstance(created_payload, dict) or not isinstance(
                created_payload.get("request"), dict
            ):
                raise RuntimeError(f"run {run_id} has no persisted request")
            artifact = await self._latest_artifact(
                database, run_uuid, quality_status="verified"
            )
            draft = await self._latest_artifact(
                database, run_uuid, quality_status="needs_repair"
            )
            terminal_payload = next(
                (
                    record.payload
                    for record in reversed(event_records)
                    if "elapsed_seconds" in record.payload
                ),
                {},
            )
            usage = TokenUsage()
            for event in events:
                usage += event.usage
            updated_at = events[-1].timestamp if events else _utc(run.created_at)
            return BuilderRunSnapshot(
                run_id=str(run.id),
                request=BuilderRequest.from_dict(created_payload["request"]),
                status=RunStatus(run.state),
                created_at=_utc(run.created_at),
                updated_at=updated_at,
                latest_sequence=run.next_event_sequence - 1,
                artifact=artifact,
                draft_artifact=draft,
                quality_status=(
                    "needs_repair" if draft is not None else "verified" if artifact else "pending"
                ),
                usage=usage,
                elapsed_seconds=float(terminal_payload.get("elapsed_seconds", 0.0)),
                error_code=run.error_code,
                cancel_requested=any(
                    record.event_type == "run.cancel_requested"
                    for record in event_records
                ),
            )

    async def update_request(self, run_id: str, request: BuilderRequest) -> None:
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                self._ensure_mutable(
                    run, cancelled=await self._cancel_requested(database, run_uuid)
                )
                result = await database.execute(
                    select(GenerationEvent).where(
                        GenerationEvent.run_id == run_uuid,
                        GenerationEvent.event_type == "run.created",
                    )
                )
                created = result.scalar_one()
                payload = dict(created.payload)
                payload["request"] = _json_copy(request.to_dict())
                created.payload = payload

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
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                self._ensure_mutable(run)
                return self._append_record(
                    database,
                    run,
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
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                self._ensure_mutable(run)
                run.state = RunStatus.RUNNING.value
                run.started_at = run.started_at or datetime.now(timezone.utc)
                project = await database.get(Project, self._project_id)
                if project is not None:
                    project.status = "generating"

    async def _latest_artifact(
        self,
        database: AsyncSession,
        run_id: UUID,
        *,
        quality_status: str,
    ) -> WidgetArtifact | None:
        result = await database.execute(
            select(GenerationArtifact)
            .where(
                GenerationArtifact.run_id == run_id,
                GenerationArtifact.quality_status == quality_status,
            )
            .order_by(GenerationArtifact.revision.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        return self._artifact_from_record(record) if record is not None else None

    async def _store_artifact(
        self,
        database: AsyncSession,
        run: GenerationRun,
        artifact: WidgetArtifact,
        *,
        quality_status: str,
    ) -> None:
        self._ensure_mutable(
            run, cancelled=await self._cancel_requested(database, run.id)
        )
        result = await database.execute(
            select(GenerationArtifact.revision)
            .where(GenerationArtifact.run_id == run.id)
            .order_by(GenerationArtifact.revision.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        if latest is not None and artifact.revision <= latest:
            raise ValueError("artifact revision must increase monotonically")
        database.add(
            self._new_artifact_record(
                run.id, artifact, quality_status=quality_status
            )
        )

    async def commit_artifact(self, run_id: str, artifact: WidgetArtifact) -> None:
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                await self._store_artifact(
                    database, run, artifact, quality_status="verified"
                )
                project = await database.get(Project, self._project_id)
                if project is not None:
                    project.active_revision = artifact.revision

    async def stage_visual_candidate(
        self, run_id: str, artifact: WidgetArtifact
    ) -> None:
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database:
                run = await self._run(database, run_uuid)
                self._ensure_mutable(
                    run, cancelled=await self._cancel_requested(database, run_uuid)
                )
                latest = await self._latest_artifact(
                    database, run_uuid, quality_status="verified"
                )
                if latest is not None and artifact.revision <= latest.revision:
                    raise ValueError(
                        "visual candidate revision must exceed public revision"
                    )
            self._visual_candidates[run_uuid] = WidgetArtifact.from_dict(
                artifact.to_dict()
            )

    async def visual_candidate(self, run_id: str) -> WidgetArtifact:
        run_uuid = self._run_uuid(run_id)
        candidate = self._visual_candidates.get(run_uuid)
        if candidate is None:
            raise ArtifactNotFound((run_id, "visual_candidate"))
        return WidgetArtifact.from_dict(candidate.to_dict())

    async def stage_visual_draft(
        self, run_id: str, artifact: WidgetArtifact
    ) -> None:
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                await self._store_artifact(
                    database, run, artifact, quality_status="needs_repair"
                )

    async def commit_visual_candidate(self, run_id: str) -> WidgetArtifact:
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            candidate = self._visual_candidates.get(run_uuid)
            if candidate is None:
                raise ArtifactNotFound((run_id, "visual_candidate"))
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                previous = await self._latest_artifact(
                    database, run_uuid, quality_status="verified"
                )
                persisted_result = await database.execute(
                    select(GenerationArtifact)
                    .where(
                        GenerationArtifact.run_id == run_uuid,
                        GenerationArtifact.revision == candidate.revision,
                    )
                    .with_for_update()
                )
                persisted = persisted_result.scalar_one_or_none()
                if persisted is None:
                    await self._store_artifact(
                        database, run, candidate, quality_status="verified"
                    )
                elif persisted.quality_status != "needs_repair":
                    raise ValueError(
                        "artifact revision must increase monotonically"
                    )
                elif persisted.config.get("artifact") != candidate.to_dict():
                    raise ValueError(
                        "visual candidate differs from persisted draft"
                    )
                else:
                    persisted.quality_status = "verified"
                self._append_record(
                    database,
                    run,
                    event_type="artifact.committed",
                    stage=candidate.stage,
                    status="completed",
                    message=artifact_commit_message(candidate),
                    revision=candidate.revision,
                    changes=artifact_changed_fields(previous, candidate),
                )
                project = await database.get(Project, self._project_id)
                if project is not None:
                    project.active_revision = candidate.revision
            self._visual_candidates.pop(run_uuid, None)
            return WidgetArtifact.from_dict(candidate.to_dict())

    async def artifact(
        self, run_id: str, revision: int | None = None
    ) -> WidgetArtifact:
        return await self._get_artifact(
            run_id, revision=revision, include_draft=False
        )

    async def preview_artifact(
        self, run_id: str, revision: int | None = None
    ) -> WidgetArtifact:
        return await self._get_artifact(
            run_id, revision=revision, include_draft=True
        )

    async def _get_artifact(
        self,
        run_id: str,
        *,
        revision: int | None,
        include_draft: bool,
    ) -> WidgetArtifact:
        run_uuid = self._run_uuid(run_id)
        async with self._sessions() as database:
            await self._run(database, run_uuid)
            statement = select(GenerationArtifact).where(
                GenerationArtifact.run_id == run_uuid
            )
            if not include_draft:
                statement = statement.where(
                    GenerationArtifact.quality_status == "verified"
                )
            if revision is not None:
                statement = statement.where(GenerationArtifact.revision == revision)
            else:
                statement = statement.order_by(GenerationArtifact.revision.desc()).limit(1)
            result = await database.execute(statement)
            record = result.scalar_one_or_none()
            if record is None:
                raise ArtifactNotFound((run_id, revision))
            return self._artifact_from_record(record)

    async def events_after(
        self, run_id: str, sequence: int
    ) -> tuple[BuilderEvent, ...]:
        run_uuid = self._run_uuid(run_id)
        async with self._sessions() as database:
            await self._run(database, run_uuid)
            result = await database.execute(
                select(GenerationEvent)
                .where(
                    GenerationEvent.run_id == run_uuid,
                    GenerationEvent.sequence > sequence,
                )
                .order_by(GenerationEvent.sequence)
            )
            return tuple(self._event_from_record(record) for record in result.scalars())

    async def wait_for_events(
        self,
        run_id: str,
        sequence: int,
        *,
        timeout: float = 15.0,
    ) -> tuple[BuilderEvent, ...]:
        deadline = monotonic() + max(0.0, timeout)
        while True:
            available = await self.events_after(run_id, sequence)
            if available:
                return available
            if (await self.snapshot(run_id)).status in TERMINAL_STATUSES:
                return ()
            remaining = deadline - monotonic()
            if remaining <= 0:
                return ()
            await asyncio.sleep(min(0.05, remaining))

    async def request_cancel(self, run_id: str) -> bool:
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                if RunStatus(run.state) in TERMINAL_STATUSES or await self._cancel_requested(
                    database, run_uuid
                ):
                    return False
                self._append_record(
                    database,
                    run,
                    event_type="run.cancel_requested",
                    stage=None,
                    status=run.state,
                    message="Cancellation requested",
                )
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
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                self._ensure_mutable(run)
                event = self._append_record(
                    database,
                    run,
                    event_type=event_type,
                    stage=stage,
                    status=status.value,
                    message=message,
                    revision=revision,
                    error_code=error_code,
                    diagnostic=diagnostic,
                    payload_extra={"elapsed_seconds": max(0.0, elapsed_seconds)},
                )
                run.state = status.value
                run.error_code = error_code
                run.finished_at = event.timestamp
                run.progress = 100 if status is RunStatus.COMPLETED else run.progress
                project = await database.get(Project, self._project_id)
                if project is not None:
                    project.status = status.value
                    if revision is not None:
                        project.active_revision = revision
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
        run_uuid = self._run_uuid(run_id)
        async with self._lock(run_uuid):
            async with self._sessions() as database, database.begin():
                run = await self._locked_run(database, run_uuid)
                self._ensure_mutable(run)
                run.state = status.value
                run.error_code = error_code
                run.finished_at = datetime.now(timezone.utc)
                run.progress = 100 if status is RunStatus.COMPLETED else run.progress
                project = await database.get(Project, self._project_id)
                if project is not None:
                    project.status = status.value


__all__ = ["PostgresRunStore"]
