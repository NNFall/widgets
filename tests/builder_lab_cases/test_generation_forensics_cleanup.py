from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationForensicManifest,
    GenerationRun,
    ModelCall,
    Project,
)
from builder_lab.forensics.cleanup import (
    ForensicCleanupTarget,
    GenerationForensicFilesystemCleanup,
)
from builder_lab.forensics.cleanup_integration import (
    CLEANUP_SUMMARY_FIELDS,
    GenerationForensicCleanup,
)
from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.forensics.policy import (
    FORENSIC_RETENTION,
    TERMINAL_RUN_STATES,
    canonical_storage_key,
    cleanup_advisory_key,
    is_ttl_cleanup_eligible,
    process_run_lock,
    retention_expires_at,
    run_advisory_key,
)
from builder_lab.forensics.storage import GenerationForensicStorage
from builder_lab.forensics.recorder import GenerationForensicRecorder


UTC = timezone.utc
RUN_ID = UUID("12345678-1234-5678-9234-567812345678")
PROJECT_ID = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")


def _config(root: Path, *, max_bytes: int = 32 * 1024 * 1024) -> GenerationForensicsConfig:
    return GenerationForensicsConfig(
        enabled=True,
        root=root,
        ttl_hours=120,
        max_bytes=max_bytes,
    )


def _target(run_id: UUID = RUN_ID) -> ForensicCleanupTarget:
    return ForensicCleanupTarget(
        run_id=run_id,
        user_id=10,
        project_id=PROJECT_ID,
        storage_key=canonical_storage_key(run_id),
    )


def _initialized_storage(root: Path) -> GenerationForensicStorage:
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    storage.initialize_run(
        user_id=10,
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        created_at=datetime(2026, 7, 25, 12, 0, tzinfo=UTC),
    )
    return storage


async def _database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cleanup.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://example.com/",
            brief="Cleanup fixture",
        )
        database.add(project)
        await database.flush()
    return engine, factory, project.id


async def _add_run(
    factory,
    storage: GenerationForensicStorage,
    project_id: UUID,
    *,
    state: str,
    finished_at: datetime | None,
    expires_at: datetime | None,
    create_evidence: bool = True,
    add_core_rows: bool = False,
) -> UUID:
    run_id = uuid4()
    if create_evidence:
        storage.initialize_run(
            user_id=10,
            project_id=project_id,
            run_id=run_id,
            created_at=(finished_at or datetime(2026, 7, 25, tzinfo=UTC)),
        )
    async with factory() as database, database.begin():
        database.add(
            GenerationRun(
                id=run_id,
                project_id=project_id,
                mode="direct",
                state=state,
                progress=100 if state in TERMINAL_RUN_STATES else 50,
                idempotency_key=f"cleanup-{run_id}",
                finished_at=finished_at,
            )
        )
        database.add(
            GenerationForensicManifest(
                run_id=run_id,
                user_id=10,
                project_id=project_id,
                storage_key=canonical_storage_key(run_id),
                schema_version=1,
                state=state,
                last_event_sequence=1 if add_core_rows else 0,
                entry_count=0,
                byte_count=0,
                expires_at=expires_at,
                metadata_json={},
                created_at=finished_at or datetime(2026, 7, 25, tzinfo=UTC),
                updated_at=finished_at or datetime(2026, 7, 25, tzinfo=UTC),
            )
        )
        if add_core_rows:
            artifact = GenerationArtifact(
                run_id=run_id,
                revision=1,
                stage="finalize",
                html="<main>durable</main>",
                css="",
                javascript="",
                config={},
                provenance={},
            )
            database.add(artifact)
            database.add(
                GenerationEvent(
                    id=1,
                    run_id=run_id,
                    sequence=1,
                    event_type="run.completed",
                    payload={},
                )
            )
            database.add(
                ModelCall(
                    run_id=run_id,
                    provider="fixture",
                    model="fixture-model",
                    role="planner",
                    prompt_version="v1",
                    status="succeeded",
                    cost_state="not_billed",
                    cost_microusd=0,
                )
            )
    return run_id


def test_cleanup_policy_uses_exact_finished_at_plus_120_hours() -> None:
    finished_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    expires_at = finished_at + timedelta(hours=120)

    assert FORENSIC_RETENTION == timedelta(hours=120)
    assert retention_expires_at(finished_at) == expires_at
    for state in sorted(TERMINAL_RUN_STATES):
        assert not is_ttl_cleanup_eligible(
            run_state=state,
            manifest_state=state,
            finished_at=finished_at,
            expires_at=expires_at,
            now=expires_at - timedelta(microseconds=1),
        )
        assert is_ttl_cleanup_eligible(
            run_state=state,
            manifest_state=state,
            finished_at=finished_at,
            expires_at=expires_at,
            now=expires_at,
        )


@pytest.mark.parametrize("state", ["pending", "active", "degraded", "running"])
def test_cleanup_policy_never_selects_nonterminal_or_null_expiry(state: str) -> None:
    finished_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    expires_at = finished_at + FORENSIC_RETENTION

    assert not is_ttl_cleanup_eligible(
        run_state=state,
        manifest_state=state,
        finished_at=finished_at,
        expires_at=expires_at,
        now=expires_at,
    )
    assert not is_ttl_cleanup_eligible(
        run_state="completed",
        manifest_state="completed",
        finished_at=finished_at,
        expires_at=None,
        now=expires_at,
    )


def test_cleanup_policy_rejects_early_or_noncanonical_expiry() -> None:
    finished_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    exact_expiry = finished_at + FORENSIC_RETENTION

    for stored_expiry in (
        exact_expiry - timedelta(microseconds=1),
        exact_expiry + timedelta(microseconds=1),
    ):
        assert not is_ttl_cleanup_eligible(
            run_state="completed",
            manifest_state="completed",
            finished_at=finished_at,
            expires_at=stored_expiry,
            now=exact_expiry + timedelta(days=1),
        )


def test_cleanup_policy_requires_aware_timestamps() -> None:
    aware = datetime(2026, 7, 30, tzinfo=UTC)
    naive = aware.replace(tzinfo=None)

    with pytest.raises(ValueError, match="timezone"):
        retention_expires_at(naive)
    with pytest.raises(ValueError, match="timezone"):
        is_ttl_cleanup_eligible(
            run_state="completed",
            manifest_state="completed",
            finished_at=aware,
            expires_at=aware + FORENSIC_RETENTION,
            now=naive,
        )


def test_cleanup_policy_rejects_run_manifest_state_disagreement() -> None:
    finished_at = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    expires_at = finished_at + FORENSIC_RETENTION

    assert not is_ttl_cleanup_eligible(
        run_state="completed",
        manifest_state="failed",
        finished_at=finished_at,
        expires_at=expires_at,
        now=expires_at,
    )


def test_storage_and_lock_keys_are_canonical_and_stable() -> None:
    assert canonical_storage_key(RUN_ID) == (
        "runs/12/12345678-1234-5678-9234-567812345678"
    )
    assert run_advisory_key(RUN_ID) == run_advisory_key(RUN_ID)
    assert -(1 << 63) <= run_advisory_key(RUN_ID) < (1 << 63)
    assert -(1 << 63) <= cleanup_advisory_key() < (1 << 63)
    assert cleanup_advisory_key() != run_advisory_key(RUN_ID)

    with pytest.raises(ValueError, match="UUID"):
        canonical_storage_key(str(RUN_ID))  # type: ignore[arg-type]


def test_recorder_and_cleanup_share_the_same_process_run_lock(tmp_path: Path) -> None:
    config = _config(tmp_path / "forensics")
    recorder = GenerationForensicRecorder(
        object(),  # type: ignore[arg-type]
        config,
        object(),  # type: ignore[arg-type]
    )

    assert recorder._lock(RUN_ID) is process_run_lock(RUN_ID)
    assert isinstance(process_run_lock(RUN_ID), asyncio.Lock)
    assert GenerationForensicRecorder._advisory_key(RUN_ID) == run_advisory_key(
        RUN_ID
    )


def test_cleanup_open_requires_an_existing_marked_private_root(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises((FileNotFoundError, ValueError), match="root|missing"):
        GenerationForensicFilesystemCleanup.open(_config(missing))

    assert not missing.exists()

    unmarked = tmp_path / "unmarked"
    unmarked.mkdir()
    with pytest.raises(ValueError, match="marker|marked"):
        GenerationForensicFilesystemCleanup.open(_config(unmarked))


def test_cleanup_quarantines_deletes_and_finalizes_a_canonical_run(
    tmp_path: Path,
) -> None:
    root = tmp_path / "forensics"
    _initialized_storage(root)
    cleaner = GenerationForensicFilesystemCleanup.open(_config(root))
    run_dir = root / "runs" / RUN_ID.hex[:2] / str(RUN_ID)

    result = cleaner.purge(_target())

    assert result.removed
    assert not run_dir.exists()
    assert cleaner.has_tombstone(RUN_ID)

    cleaner.finalize(RUN_ID)

    assert not cleaner.has_tombstone(RUN_ID)


def test_cleanup_recovers_idempotently_from_a_quarantined_run(tmp_path: Path) -> None:
    root = tmp_path / "forensics"
    _initialized_storage(root)
    run_dir = root / "runs" / RUN_ID.hex[:2] / str(RUN_ID)
    trash = root / ".trash"
    trash.mkdir(mode=0o700)
    trash.chmod(0o700)
    quarantined = trash / str(RUN_ID)
    os.replace(run_dir, quarantined)
    cleaner = GenerationForensicFilesystemCleanup.open(_config(root))

    first = cleaner.purge(_target())
    second = cleaner.purge(_target())

    assert first.removed and first.recovered
    assert second.removed and second.recovered
    assert not quarantined.exists()
    assert cleaner.has_tombstone(RUN_ID)


def test_cleanup_rejects_noncanonical_storage_key_without_touching_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "forensics"
    _initialized_storage(root)
    cleaner = GenerationForensicFilesystemCleanup.open(_config(root))
    run_dir = root / "runs" / RUN_ID.hex[:2] / str(RUN_ID)
    with pytest.raises(ValueError, match="canonical"):
        cleaner.purge(
            ForensicCleanupTarget(
                run_id=RUN_ID,
                user_id=10,
                project_id=PROJECT_ID,
                storage_key=f"runs/../{RUN_ID}",
            )
        )

    assert run_dir.is_dir()


def test_cleanup_never_traverses_a_symlinked_run_directory(tmp_path: Path) -> None:
    root = tmp_path / "forensics"
    storage = GenerationForensicStorage.open(_config(root), writable=True)
    external = tmp_path / "external"
    external.mkdir()
    proof = external / "proof.txt"
    proof.write_text("keep", encoding="utf-8")
    run_dir = root / "runs" / RUN_ID.hex[:2] / str(RUN_ID)
    run_dir.parent.mkdir(mode=0o700)
    run_dir.parent.chmod(0o700)
    try:
        run_dir.symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")
    cleaner = GenerationForensicFilesystemCleanup.open(storage.config)

    with pytest.raises(ValueError, match="symlink|unsafe"):
        cleaner.purge(_target())

    assert proof.read_text(encoding="utf-8") == "keep"
    assert run_dir.is_symlink()


def test_cleanup_usage_counts_foreign_bytes_without_following_symlinks(
    tmp_path: Path,
) -> None:
    root = tmp_path / "forensics"
    _initialized_storage(root)
    foreign = root / "foreign.bin"
    foreign.write_bytes(b"f" * 37)
    external = tmp_path / "large-private.bin"
    external.write_bytes(b"x" * 100_000)
    link = root / "foreign-link"
    try:
        link.symlink_to(external)
    except OSError as error:
        pytest.skip(f"file symlinks unavailable: {error}")
    cleaner = GenerationForensicFilesystemCleanup.open(_config(root))

    usage = cleaner.usage()

    assert usage.bytes_total >= 37
    assert usage.bytes_total < external.stat().st_size
    assert usage.foreign_skipped >= 2


@pytest.mark.asyncio
async def test_cleanup_removes_only_exactly_expired_terminal_manifests(
    tmp_path: Path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    config = _config(tmp_path / "forensics")
    storage = GenerationForensicStorage.open(config, writable=True)
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    finished = now - FORENSIC_RETENTION
    eligible: list[UUID] = []
    protected: list[UUID] = []
    try:
        for state in sorted(TERMINAL_RUN_STATES):
            eligible.append(
                await _add_run(
                    factory,
                    storage,
                    project_id,
                    state=state,
                    finished_at=finished,
                    expires_at=now,
                    add_core_rows=state == "completed",
                )
            )
        protected.append(
            await _add_run(
                factory,
                storage,
                project_id,
                state="completed",
                finished_at=finished + timedelta(microseconds=1),
                expires_at=now + timedelta(microseconds=1),
            )
        )
        protected.append(
            await _add_run(
                factory,
                storage,
                project_id,
                state="active",
                finished_at=None,
                expires_at=None,
            )
        )
        protected.append(
            await _add_run(
                factory,
                storage,
                project_id,
                state="pending",
                finished_at=None,
                expires_at=None,
            )
        )
        protected.append(
            await _add_run(
                factory,
                storage,
                project_id,
                state="failed",
                finished_at=finished,
                expires_at=None,
            )
        )
        cleaner = GenerationForensicCleanup(factory, config)

        summary = await cleaner.run(now=now, batch_size=100, time_budget_seconds=5)

        assert summary.expired_removed == 3
        assert summary.quota_removed == 0
        async with factory() as database:
            remaining = set(
                await database.scalars(select(GenerationForensicManifest.run_id))
            )
            assert remaining == set(protected)
            assert await database.scalar(
                select(func.count()).select_from(GenerationRun)
            ) == len(eligible) + len(protected)
            assert await database.scalar(
                select(func.count()).select_from(GenerationEvent)
            ) == 1
            assert await database.scalar(
                select(func.count()).select_from(GenerationArtifact)
            ) == 1
            assert await database.scalar(
                select(func.count()).select_from(ModelCall)
            ) == 1
        for run_id in eligible:
            assert not (
                config.root / "runs" / run_id.hex[:2] / str(run_id)
            ).exists()
        for run_id in protected:
            assert (
                config.root / "runs" / run_id.hex[:2] / str(run_id)
            ).is_dir()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_evidence_becomes_a_degraded_sql_tombstone(
    tmp_path: Path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    config = _config(tmp_path / "forensics")
    storage = GenerationForensicStorage.open(config, writable=True)
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    finished = now - FORENSIC_RETENTION
    try:
        run_id = await _add_run(
            factory,
            storage,
            project_id,
            state="failed",
            finished_at=finished,
            expires_at=now,
            create_evidence=False,
        )

        summary = await GenerationForensicCleanup(factory, config).run(now=now)

        assert summary.expired_removed == 0
        assert summary.degraded == 1
        async with factory() as database:
            row = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )
            assert row is not None
            assert row.state == "degraded"
            assert row.metadata_json == {
                "degraded": True,
                "failure_code": "cleanup_missing_evidence",
                "detected_at": now.isoformat(),
                "expected_byte_count": 0,
                "expected_manifest_sha256": None,
            }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_filesystem_failure_keeps_terminal_sql_row_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    config = _config(tmp_path / "forensics")
    storage = GenerationForensicStorage.open(config, writable=True)
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    finished = now - FORENSIC_RETENTION
    try:
        run_id = await _add_run(
            factory,
            storage,
            project_id,
            state="completed",
            finished_at=finished,
            expires_at=now,
        )
        cleanup = GenerationForensicCleanup(factory, config)
        original = GenerationForensicFilesystemCleanup.purge

        def fail_delete(self, target):
            raise OSError("private filesystem detail")

        monkeypatch.setattr(GenerationForensicFilesystemCleanup, "purge", fail_delete)
        failed = await cleanup.run(now=now)

        assert failed.degraded == 1
        async with factory() as database:
            row = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )
            assert row is not None
            assert row.state == "completed"
            assert row.expires_at.replace(tzinfo=UTC) == now

        monkeypatch.setattr(GenerationForensicFilesystemCleanup, "purge", original)
        retried = await cleanup.run(now=now)

        assert retried.expired_removed == 1
        async with factory() as database:
            assert await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            ) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dry_run_is_side_effect_free_and_summary_is_allowlisted(
    tmp_path: Path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    config = _config(tmp_path / "forensics", max_bytes=1)
    storage = GenerationForensicStorage.open(config, writable=True)
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    run_id = await _add_run(
        factory,
        storage,
        project_id,
        state="completed",
        finished_at=now - FORENSIC_RETENTION,
        expires_at=now,
    )
    run_dir = config.root / "runs" / run_id.hex[:2] / str(run_id)
    try:
        summary = await GenerationForensicCleanup(factory, config).run(
            now=now,
            dry_run=True,
        )

        assert set(summary.to_dict()) == CLEANUP_SUMMARY_FIELDS
        assert summary.dry_run
        assert summary.expired_removed == 0
        assert summary.expired_eligible == 1
        assert summary.quota_removed == 0
        assert summary.bytes_after == summary.bytes_before
        assert summary.quota_exceeded
        assert run_dir.is_dir()
        async with factory() as database:
            row = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )
            assert row is not None and row.state == "completed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_batch_is_bounded_and_quota_never_purges_early(
    tmp_path: Path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    config = _config(tmp_path / "forensics", max_bytes=1)
    storage = GenerationForensicStorage.open(config, writable=True)
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    expired = [
        await _add_run(
            factory,
            storage,
            project_id,
            state="failed",
            finished_at=now - FORENSIC_RETENTION,
            expires_at=now,
        )
        for _ in range(2)
    ]
    unexpired = await _add_run(
        factory,
        storage,
        project_id,
        state="completed",
        finished_at=now - FORENSIC_RETENTION + timedelta(seconds=1),
        expires_at=now + timedelta(seconds=1),
    )
    try:
        summary = await GenerationForensicCleanup(factory, config).run(
            now=now,
            batch_size=1,
        )

        assert summary.expired_removed == 1
        assert summary.batch_limited
        assert summary.quota_removed == 0
        assert summary.quota_exceeded
        assert (
            config.root / "runs" / unexpired.hex[:2] / str(unexpired)
        ).is_dir()
        async with factory() as database:
            remaining_expired = await database.scalar(
                select(func.count())
                .select_from(GenerationForensicManifest)
                .where(GenerationForensicManifest.run_id.in_(expired))
            )
            assert remaining_expired == 1
    finally:
        await engine.dispose()
