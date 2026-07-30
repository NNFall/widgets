from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.saas.models import (
    GenerationForensicManifest,
    GenerationRun,
    Project,
)
from builder_lab.generation_events import PreparedGenerationEvent

from .config import GenerationForensicsConfig
from .models import ForensicBlob, ForensicManifest, ForensicWriteResult, require_aware

if TYPE_CHECKING:
    from .storage import GenerationForensicStorage


_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
_DEGRADED_STATE = "degraded"
_FORENSIC_RETENTION_HOURS = 120


def forensic_storage_key(run_id: UUID) -> str:
    if not isinstance(run_id, UUID):
        raise ValueError("run_id must be a UUID")
    return f"runs/{run_id.hex[:2]}/{run_id}"


def _aware(value: datetime, *, name: str) -> datetime:
    return require_aware(value, name=name)


def _database_utc(value: datetime) -> datetime:
    """Normalize ORM timestamps; SQLite fixtures discard timezone metadata."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def ensure_pending_forensic_manifest(
    database: AsyncSession,
    *,
    config: GenerationForensicsConfig,
    user_id: int,
    project_id: UUID,
    run_id: UUID,
    created_at: datetime,
) -> GenerationForensicManifest | None:
    """Create only SQL metadata; the internet-facing app never touches the volume."""

    if not config.enabled:
        return None
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise ValueError("user_id must be a positive integer")
    if not isinstance(project_id, UUID) or not isinstance(run_id, UUID):
        raise ValueError("project_id and run_id must be UUIDs")
    existing = await database.scalar(
        select(GenerationForensicManifest)
        .where(GenerationForensicManifest.run_id == run_id)
        .with_for_update()
    )
    if existing is not None:
        if (
            type(existing.user_id) is not int
            or existing.user_id != user_id
            or existing.project_id != project_id
            or existing.run_id != run_id
            or existing.storage_key != forensic_storage_key(run_id)
        ):
            raise ValueError("forensic manifest identity conflicts with run")
        return existing
    row = GenerationForensicManifest(
        id=uuid4(),
        run_id=run_id,
        user_id=user_id,
        project_id=project_id,
        storage_key=forensic_storage_key(run_id),
        schema_version=1,
        state="pending",
        last_event_sequence=0,
        entry_count=0,
        byte_count=0,
        manifest_sha256=None,
        expires_at=None,
        metadata_json={},
        created_at=_aware(created_at, name="created_at"),
        updated_at=_aware(created_at, name="created_at"),
    )
    database.add(row)
    await database.flush()
    return row


class GenerationForensicRecorder:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: GenerationForensicsConfig,
        storage: GenerationForensicStorage,
    ) -> None:
        self._sessions = session_factory
        self.config = config
        self.storage = storage
        self._locks: dict[UUID, asyncio.Lock] = {}

    @classmethod
    async def open(
        cls,
        session_factory: async_sessionmaker[AsyncSession],
        config: GenerationForensicsConfig,
    ) -> GenerationForensicRecorder:
        from .storage import GenerationForensicStorage

        storage = await asyncio.to_thread(
            partial(GenerationForensicStorage.open, config, writable=True)
        )
        return cls(session_factory, config, storage)

    def _lock(self, run_id: UUID) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    @staticmethod
    def _advisory_key(run_id: UUID) -> int:
        digest = hashlib.sha256(b"kaigo-forensics-v1\x00" + run_id.bytes).digest()
        return int.from_bytes(digest[:8], byteorder="big", signed=True)

    async def _advisory_lock(self, database: AsyncSession, run_id: UUID) -> None:
        if database.get_bind().dialect.name == "postgresql":
            await database.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": self._advisory_key(run_id)},
            )

    async def _manifest_for_update(
        self,
        database: AsyncSession,
        run_id: UUID,
    ) -> GenerationForensicManifest | None:
        await self._advisory_lock(database, run_id)
        return await database.scalar(
            select(GenerationForensicManifest)
            .where(GenerationForensicManifest.run_id == run_id)
            .with_for_update()
        )

    async def _ensure_manifest(
        self,
        database: AsyncSession,
        run: GenerationRun,
    ) -> GenerationForensicManifest | None:
        row = await self._manifest_for_update(database, run.id)
        if not self.config.enabled:
            return row
        project = await database.get(Project, run.project_id)
        if project is None:
            return None
        if row is not None:
            if (
                type(row.user_id) is not int
                or row.user_id != project.owner_user_id
                or row.project_id != project.id
                or row.run_id != run.id
                or row.storage_key != forensic_storage_key(run.id)
            ):
                raise ValueError("forensic manifest identity conflicts with run")
            return row
        return await ensure_pending_forensic_manifest(
            database,
            config=self.config,
            user_id=project.owner_user_id,
            project_id=project.id,
            run_id=run.id,
            created_at=(
                _database_utc(run.created_at)
                if run.created_at is not None
                else datetime.now(timezone.utc)
            ),
        )

    @staticmethod
    def _mark_degraded(
        row: GenerationForensicManifest,
        *,
        failure_code: str,
    ) -> None:
        row.state = _DEGRADED_STATE
        row.metadata_json = {
            "degraded": True,
            "failure_code": failure_code[:64],
        }
        row.updated_at = datetime.now(timezone.utc)

    @staticmethod
    def _apply_lifecycle(
        row: GenerationForensicManifest,
        *,
        terminal_state: str | None,
        finished_at: datetime | None,
    ) -> None:
        if terminal_state is None:
            if row.state in _TERMINAL_STATES:
                return
            if row.state != _DEGRADED_STATE:
                row.state = "active"
            row.expires_at = None
            return
        if row.state not in _TERMINAL_STATES:
            row.state = terminal_state
        if row.expires_at is None:
            terminal_time = _aware(finished_at, name="finished_at")
            row.expires_at = terminal_time + timedelta(
                hours=_FORENSIC_RETENTION_HOURS
            )

    @staticmethod
    def _apply_snapshot(
        row: GenerationForensicManifest,
        manifest: ForensicManifest,
        manifest_sha256: str,
    ) -> None:
        row.entry_count = len(manifest.entries)
        row.byte_count = sum(entry.byte_count for entry in manifest.entries)
        row.manifest_sha256 = manifest_sha256
        event_sequences = [
            int(entry.relative_path.removeprefix("events/")[:8])
            for entry in manifest.entries
            if entry.kind == "event"
        ]
        if event_sequences:
            row.last_event_sequence = max(
                row.last_event_sequence,
                max(event_sequences),
            )
        row.updated_at = datetime.now(timezone.utc)

    async def _initialize_storage(
        self,
        row: GenerationForensicManifest,
    ) -> ForensicManifest:
        return await asyncio.to_thread(
            partial(
                self.storage.initialize_run,
                user_id=row.user_id,
                project_id=row.project_id,
                run_id=row.run_id,
                created_at=_database_utc(row.created_at),
            )
        )

    async def _reconcile_snapshot(
        self,
        row: GenerationForensicManifest,
    ) -> None:
        manifest = await asyncio.to_thread(self.storage.load_manifest, row.run_id)
        digest = await asyncio.to_thread(self.storage.manifest_digest, row.run_id)
        self._apply_snapshot(row, manifest, digest)

    async def _mark_identity_degraded(
        self,
        database: AsyncSession,
        run: GenerationRun,
        *,
        terminal_state: str | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        row = await self._manifest_for_update(database, run.id)
        if row is None:
            return
        self._mark_degraded(row, failure_code="manifest_identity_invalid")
        self._apply_lifecycle(
            row,
            terminal_state=terminal_state,
            finished_at=finished_at,
        )

    async def activate(
        self,
        database: AsyncSession,
        run: GenerationRun,
    ) -> bool:
        if not self.config.enabled:
            return False
        async with self._lock(run.id):
            try:
                row = await self._ensure_manifest(database, run)
            except ValueError:
                await self._mark_identity_degraded(database, run)
                return False
            if row is None:
                return False
            if row.state in _TERMINAL_STATES or row.state == _DEGRADED_STATE:
                return row.state != _DEGRADED_STATE
            try:
                await self._initialize_storage(row)
                await self._reconcile_snapshot(row)
            except Exception:
                self._mark_degraded(row, failure_code="storage_initialize_failed")
                return False
            row.state = "active"
            row.expires_at = None
            return True

    async def record_event(
        self,
        database: AsyncSession,
        run: GenerationRun,
        *,
        sequence: int,
        prepared: PreparedGenerationEvent,
        forensic_blobs: Sequence[ForensicBlob] = (),
        created_at: datetime,
        terminal_state: str | None = None,
        finished_at: datetime | None = None,
    ) -> ForensicWriteResult | None:
        if not self.config.enabled:
            return None
        if terminal_state is not None and terminal_state not in _TERMINAL_STATES:
            raise ValueError("terminal_state is invalid")
        if terminal_state is not None and finished_at is None:
            raise ValueError("terminal event requires finished_at")
        created_at = _aware(created_at, name="created_at")

        async with self._lock(run.id):
            try:
                row = await self._ensure_manifest(database, run)
            except ValueError:
                await self._mark_identity_degraded(
                    database,
                    run,
                    terminal_state=terminal_state,
                    finished_at=finished_at,
                )
                return ForensicWriteResult(
                    written=False,
                    degraded=True,
                    failure_code="manifest_identity_invalid",
                )
            if row is None:
                return None
            try:
                initialized_manifest = await self._initialize_storage(row)
                event_path = (
                    f"events/{sequence:08d}-{prepared.event_type.value}.json"
                )
                blob_refs = [
                    f"blobs/{blob.sha256}.jpg" for blob in forensic_blobs
                ]
                evidence_payload = dict(prepared.forensic_payload)
                if blob_refs:
                    evidence_payload["blob_refs"] = blob_refs
                sequence_prefix = f"events/{sequence:08d}-"
                sequence_events = tuple(
                    entry
                    for entry in initialized_manifest.entries
                    if entry.kind == "event"
                    and entry.relative_path.startswith(sequence_prefix)
                )
                if sequence_events:
                    existing_event = next(
                        (
                            entry
                            for entry in sequence_events
                            if entry.relative_path == event_path
                        ),
                        None,
                    )
                    semantic_match = False
                    if existing_event is not None and len(sequence_events) == 1:
                        semantic_match = await asyncio.to_thread(
                            partial(
                                self.storage.event_payload_matches,
                                run_id=run.id,
                                sequence=sequence,
                                event_type=prepared.event_type.value,
                                payload=evidence_payload,
                            )
                        )
                    manifest_sha256 = await asyncio.to_thread(
                        self.storage.manifest_digest,
                        run.id,
                    )
                    self._apply_snapshot(
                        row,
                        initialized_manifest,
                        manifest_sha256,
                    )
                    if not semantic_match:
                        self._mark_degraded(
                            row,
                            failure_code="orphan_event_conflict",
                        )
                        self._apply_lifecycle(
                            row,
                            terminal_state=terminal_state,
                            finished_at=finished_at,
                        )
                        return ForensicWriteResult(
                            written=False,
                            degraded=True,
                            failure_code="orphan_event_conflict",
                        )
                    if existing_event is None:  # pragma: no cover - match implies entry
                        raise RuntimeError("matching forensic event entry is missing")
                    metadata = dict(row.metadata_json or {})
                    metadata["reconciled_existing_event"] = True
                    row.metadata_json = metadata
                    self._apply_lifecycle(
                        row,
                        terminal_state=terminal_state,
                        finished_at=finished_at,
                    )
                    return ForensicWriteResult(
                        written=False,
                        degraded=False,
                        entry=existing_event,
                        manifest=initialized_manifest,
                        manifest_sha256=manifest_sha256,
                    )
                for blob in forensic_blobs:
                    blob_result = await asyncio.to_thread(
                        partial(self.storage.write_blob, run_id=run.id, blob=blob)
                    )
                    if (
                        blob_result.degraded
                        or blob_result.entry is None
                        or blob_result.manifest is None
                        or blob_result.manifest_sha256 is None
                    ):
                        self._mark_degraded(
                            row,
                            failure_code=blob_result.failure_code or "blob_write_failed",
                        )
                        await self._reconcile_snapshot(row)
                        self._apply_lifecycle(
                            row,
                            terminal_state=terminal_state,
                            finished_at=finished_at,
                        )
                        return ForensicWriteResult(
                            written=False,
                            degraded=True,
                            failure_code=(
                                blob_result.failure_code or "blob_write_failed"
                            ),
                        )
                    if blob_result.entry.relative_path != f"blobs/{blob.sha256}.jpg":
                        raise ValueError("forensic blob path does not match its digest")
                result = await asyncio.to_thread(
                    partial(
                        self.storage.write_event,
                        run_id=run.id,
                        sequence=sequence,
                        event_type=prepared.event_type.value,
                        payload=evidence_payload,
                        created_at=created_at,
                    )
                )
                if result.degraded:
                    self._mark_degraded(
                        row,
                        failure_code=result.failure_code or "event_write_failed",
                    )
                    await self._reconcile_snapshot(row)
                    self._apply_lifecycle(
                        row,
                        terminal_state=terminal_state,
                        finished_at=finished_at,
                    )
                    return result
                if (
                    result.entry is None
                    or result.manifest is None
                    or result.manifest_sha256 is None
                ):
                    self._mark_degraded(row, failure_code="manifest_snapshot_missing")
                    self._apply_lifecycle(
                        row,
                        terminal_state=terminal_state,
                        finished_at=finished_at,
                    )
                    return ForensicWriteResult(
                        written=False,
                        degraded=True,
                        failure_code="manifest_snapshot_missing",
                    )
                self._apply_snapshot(row, result.manifest, result.manifest_sha256)
            except Exception:
                try:
                    await self._reconcile_snapshot(row)
                except Exception:
                    pass
                self._mark_degraded(row, failure_code="storage_write_failed")
                self._apply_lifecycle(
                    row,
                    terminal_state=terminal_state,
                    finished_at=finished_at,
                )
                return ForensicWriteResult(
                    written=False,
                    degraded=True,
                    failure_code="storage_write_failed",
                )

            self._apply_lifecycle(
                row,
                terminal_state=terminal_state,
                finished_at=finished_at,
            )
            return result

    async def load_manifest(self, run_id: UUID) -> ForensicManifest:
        return await asyncio.to_thread(self.storage.load_manifest, run_id)

    async def manifest_digest(self, run_id: UUID) -> str:
        return await asyncio.to_thread(self.storage.manifest_digest, run_id)


__all__ = [
    "GenerationForensicRecorder",
    "ensure_pending_forensic_manifest",
    "forensic_storage_key",
]
