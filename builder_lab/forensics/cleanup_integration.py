from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Callable
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.saas.models import GenerationForensicManifest, GenerationRun

from .cleanup import ForensicCleanupTarget, GenerationForensicFilesystemCleanup
from .config import GenerationForensicsConfig
from .models import require_aware
from .policy import (
    FORENSIC_RETENTION,
    TERMINAL_RUN_STATES,
    cleanup_advisory_key,
    is_ttl_cleanup_eligible,
    process_run_lock,
    run_advisory_key,
)


CLEANUP_SUMMARY_FIELDS = frozenset(
    {
        "timestamp",
        "dry_run",
        "bytes_before",
        "bytes_after",
        "expired_eligible",
        "expired_removed",
        "quota_removed",
        "foreign_skipped",
        "degraded",
        "quota_exceeded",
        "processed",
        "batch_limited",
        "time_limited",
        "lock_acquired",
    }
)


@dataclass(frozen=True, slots=True)
class GenerationForensicCleanupSummary:
    timestamp: str
    dry_run: bool
    bytes_before: int
    bytes_after: int
    expired_eligible: int
    expired_removed: int
    quota_removed: int
    foreign_skipped: int
    degraded: int
    quota_exceeded: bool
    processed: int
    batch_limited: bool
    time_limited: bool
    lock_acquired: bool

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        if set(result) != CLEANUP_SUMMARY_FIELDS:  # pragma: no cover - invariant
            raise RuntimeError("cleanup summary field allowlist drifted")
        return result


def _database_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class GenerationForensicCleanup:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: GenerationForensicsConfig,
        *,
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> None:
        self._sessions = session_factory
        self.config = config
        self._monotonic = monotonic_clock

    @staticmethod
    async def _try_global_lock(database: AsyncSession) -> bool:
        if database.get_bind().dialect.name != "postgresql":
            return True
        acquired = await database.scalar(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": cleanup_advisory_key()},
        )
        return acquired is True

    @staticmethod
    async def _run_lock(database: AsyncSession, run_id: UUID) -> None:
        if database.get_bind().dialect.name == "postgresql":
            await database.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": run_advisory_key(run_id)},
            )

    async def _candidate_ids(
        self,
        database: AsyncSession,
        *,
        now: datetime,
        batch_size: int,
    ) -> tuple[tuple[UUID, ...], bool]:
        cutoff = now - FORENSIC_RETENTION
        values = tuple(
            await database.scalars(
                select(GenerationForensicManifest.run_id)
                .join(
                    GenerationRun,
                    GenerationRun.id == GenerationForensicManifest.run_id,
                )
                .where(
                    GenerationForensicManifest.state.in_(TERMINAL_RUN_STATES),
                    GenerationRun.state.in_(TERMINAL_RUN_STATES),
                    GenerationForensicManifest.expires_at.is_not(None),
                    GenerationForensicManifest.expires_at <= now,
                    GenerationRun.finished_at.is_not(None),
                    GenerationRun.finished_at <= cutoff,
                )
                .order_by(
                    GenerationRun.finished_at.asc(),
                    GenerationForensicManifest.run_id.asc(),
                )
                .limit(batch_size + 1)
            )
        )
        return values[:batch_size], len(values) > batch_size

    async def _locked_rows(
        self,
        database: AsyncSession,
        run_id: UUID,
    ) -> tuple[GenerationForensicManifest, GenerationRun] | None:
        await self._run_lock(database, run_id)
        result = await database.execute(
            select(GenerationForensicManifest, GenerationRun)
            .join(
                GenerationRun,
                GenerationRun.id == GenerationForensicManifest.run_id,
            )
            .where(GenerationForensicManifest.run_id == run_id)
            .with_for_update()
        )
        row = result.one_or_none()
        if row is None:
            return None
        return row[0], row[1]

    async def run(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
        batch_size: int = 100,
        time_budget_seconds: float = 30.0,
    ) -> GenerationForensicCleanupSummary:
        now = require_aware(now or datetime.now(timezone.utc), name="now")
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or not 1 <= batch_size <= 10_000
        ):
            raise ValueError("batch_size must be between 1 and 10000")
        if (
            isinstance(time_budget_seconds, bool)
            or not isinstance(time_budget_seconds, (int, float))
            or not 0 < time_budget_seconds <= 3_600
        ):
            raise ValueError("time_budget_seconds must be between 0 and 3600")

        filesystem = await asyncio.to_thread(
            GenerationForensicFilesystemCleanup.open,
            self.config,
        )
        usage_before = await asyncio.to_thread(filesystem.usage)
        deadline = self._monotonic() + float(time_budget_seconds)
        expired_eligible = 0
        expired_removed = 0
        degraded = 0
        processed = 0
        batch_limited = False
        time_limited = False
        lock_acquired = False
        finalized: list[UUID] = []

        async with self._sessions() as database, database.begin():
            lock_acquired = await self._try_global_lock(database)
            if lock_acquired:
                candidate_ids, batch_limited = await self._candidate_ids(
                    database,
                    now=now,
                    batch_size=batch_size,
                )
                for run_id in candidate_ids:
                    if self._monotonic() >= deadline:
                        time_limited = True
                        break
                    async with process_run_lock(run_id):
                        locked = await self._locked_rows(database, run_id)
                        if locked is None:
                            continue
                        manifest, run = locked
                        processed += 1
                        finished_at = (
                            _database_utc(run.finished_at)
                            if run.finished_at is not None
                            else None
                        )
                        expires_at = (
                            _database_utc(manifest.expires_at)
                            if manifest.expires_at is not None
                            else None
                        )
                        if not is_ttl_cleanup_eligible(
                            run_state=run.state,
                            manifest_state=manifest.state,
                            finished_at=finished_at,
                            expires_at=expires_at,
                            now=now,
                        ):
                            continue
                        try:
                            target = ForensicCleanupTarget(
                                run_id=manifest.run_id,
                                user_id=manifest.user_id,
                                project_id=manifest.project_id,
                                storage_key=manifest.storage_key,
                            )
                        except ValueError:
                            degraded += 1
                            continue
                        expired_eligible += 1
                        if dry_run:
                            continue
                        try:
                            purge = await asyncio.to_thread(
                                filesystem.purge,
                                target,
                            )
                        except (OSError, ValueError):
                            degraded += 1
                            continue
                        if purge.missing:
                            manifest.state = "degraded"
                            manifest.metadata_json = {
                                "degraded": True,
                                "failure_code": "cleanup_missing_evidence",
                                "detected_at": now.isoformat(),
                                "expected_byte_count": manifest.byte_count,
                                "expected_manifest_sha256": manifest.manifest_sha256,
                            }
                            manifest.updated_at = now
                            degraded += 1
                            continue
                        if purge.removed:
                            await database.delete(manifest)
                            finalized.append(run_id)
                            expired_removed += 1

        if not dry_run:
            for run_id in finalized:
                try:
                    await asyncio.to_thread(filesystem.finalize, run_id)
                except (OSError, ValueError):
                    degraded += 1
        usage_after = (
            usage_before
            if dry_run
            else await asyncio.to_thread(filesystem.usage)
        )
        return GenerationForensicCleanupSummary(
            timestamp=now.astimezone(timezone.utc).isoformat(),
            dry_run=dry_run,
            bytes_before=usage_before.bytes_total,
            bytes_after=usage_after.bytes_total,
            expired_eligible=expired_eligible,
            expired_removed=expired_removed,
            quota_removed=0,
            foreign_skipped=max(
                usage_before.foreign_skipped,
                usage_after.foreign_skipped,
            ),
            degraded=degraded,
            quota_exceeded=usage_after.bytes_total > self.config.max_bytes,
            processed=processed,
            batch_limited=batch_limited,
            time_limited=time_limited,
            lock_acquired=lock_acquired,
        )


async def cleanup_generation_forensics(
    session_factory: async_sessionmaker[AsyncSession],
    config: GenerationForensicsConfig,
    **options: object,
) -> GenerationForensicCleanupSummary:
    return await GenerationForensicCleanup(session_factory, config).run(**options)
