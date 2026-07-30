import asyncio
import gc
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationForensicManifest,
    GenerationRun,
    Project,
)
from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.forensics.recorder import (
    GenerationForensicRecorder,
    ensure_pending_forensic_manifest,
)
from builder_lab.generation_events import prepare_generation_event
from builder_lab.models import BuilderRequest, EngineName, RunStatus, Stage, TokenUsage
from builder_lab import postgres_store as postgres_store_module
from builder_lab.postgres_store import PostgresRunStore
from builder_lab.store import ArtifactNotFound, VisualCandidateCapacityExceeded
from builder_lab.store_protocol import RunStoreProtocol
from tests.builder_lab_cases.test_validation import artifact


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'builder.db'}")
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
            brief="Premium widget",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    return engine, factory, project_id


async def _forensic_recorder(tmp_path: Path, factory) -> GenerationForensicRecorder:
    return await GenerationForensicRecorder.open(
        factory,
        GenerationForensicsConfig(
            enabled=True,
            root=tmp_path / "generation-forensics",
            ttl_hours=120,
            max_bytes=32 * 1024 * 1024,
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_user_id", (True, "10", 0, -1))
async def test_pending_forensic_manifest_requires_exact_positive_integer_user_id(
    tmp_path,
    invalid_user_id,
) -> None:
    engine, factory, _project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)

    try:
        async with factory() as database, database.begin():
            with pytest.raises(ValueError, match="user_id"):
                await ensure_pending_forensic_manifest(
                    database,
                    config=recorder.config,
                    user_id=invalid_user_id,
                    project_id=uuid4(),
                    run_id=uuid4(),
                    created_at=datetime.now(timezone.utc),
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_store_prepares_public_and_forensic_event_payloads(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        event = await store.append_event(
            run.run_id,
            event_type="stage.started",
            stage=Stage.FOUNDATION,
            status="running",
            message="Этап начат",
            diagnostic="Authorization: Bearer private-token-value",
            forensic_payload={
                "critic": {"email": "private@example.com", "finding": "spacing"},
                "worker_id": "worker-1",
            },
        )

        async with factory() as database:
            row = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == UUID(run.run_id),
                    GenerationEvent.sequence == event.sequence,
                )
            )
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == UUID(run.run_id)
                )
            )

        assert row is not None
        assert row.registry_version == 1
        assert row.public_payload == {
            "status": "running",
            "stage": "foundation",
        }
        assert row.payload["status"] == "running"
        assert row.payload["stage"] == "foundation"
        assert "diagnostic" not in row.payload
        assert "forensic_payload" not in row.payload
        assert row.forensic_ref == (
            f"events/{event.sequence:08d}-stage.started.json"
        )
        assert manifest is not None
        assert manifest.state == "active"
        assert manifest.last_event_sequence == event.sequence
        assert manifest.entry_count >= 2
        assert manifest.manifest_sha256

        evidence_path = (
            tmp_path
            / "generation-forensics"
            / "runs"
            / UUID(run.run_id).hex[:2]
            / run.run_id
            / row.forensic_ref
        )
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        serialized = json.dumps(evidence, ensure_ascii=False)
        assert "private-token-value" not in serialized
        assert "private@example.com" not in serialized
        assert evidence["payload"]["worker_id"] == "worker-1"
        assert evidence["payload"]["critic"]["finding"] == "spacing"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recorder_fails_closed_on_existing_manifest_identity_mismatch(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        async with factory() as database, database.begin():
            database.add(User(id=11, tenant_id=1, email="other@example.com"))
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == UUID(run.run_id)
                )
            )
            assert manifest is not None
            manifest.user_id = 11

        event = await store.append_event(
            run.run_id,
            event_type="stage.started",
            stage=Stage.FOUNDATION,
            status="running",
            message="must persist only in the core timeline",
            forensic_payload={"critic": {"finding": "private"}},
        )

        async with factory() as database:
            row = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == UUID(run.run_id),
                    GenerationEvent.sequence == event.sequence,
                )
            )
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == UUID(run.run_id)
                )
            )

        assert row is not None and row.forensic_ref is None
        assert manifest is not None and manifest.user_id == 11
        assert manifest.state == "degraded"
        assert manifest.metadata_json == {
            "degraded": True,
            "failure_code": "manifest_identity_invalid",
        }
        assert manifest.last_event_sequence == event.sequence - 1
        run_dir = (
            recorder.config.root
            / "runs"
            / UUID(run.run_id).hex[:2]
            / run.run_id
        )
        assert not (
            run_dir / f"events/{event.sequence:08d}-stage.started.json"
        ).exists()

        await store.mark_terminal(run.run_id, RunStatus.FAILED)
        async with factory() as database:
            durable_run = await database.get(GenerationRun, UUID(run.run_id))
            terminal_manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == UUID(run.run_id)
                )
            )

        assert durable_run is not None and durable_run.finished_at is not None
        assert terminal_manifest is not None
        assert terminal_manifest.state == "failed"
        assert terminal_manifest.metadata_json["degraded"] is True
        assert terminal_manifest.expires_at == (
            durable_run.finished_at + timedelta(hours=120)
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_store_rejects_unknown_new_event_type(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        with pytest.raises(ValueError, match="unknown generation event type"):
            await store.append_event(
                run.run_id,
                event_type="legacy.private_event",
                stage=None,
                status="running",
                message="must not persist",
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_forensic_write_failure_keeps_core_event_and_degrades_manifest(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )
    private_value = "private-forensic-value-must-not-leak"

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )

        def fail_write_event(**_kwargs):
            raise OSError(private_value)

        monkeypatch.setattr(recorder.storage, "write_event", fail_write_event)
        event = await store.append_event(
            run.run_id,
            event_type="stage.started",
            stage=Stage.FOUNDATION,
            status="running",
            message="Этап начат",
            forensic_payload={"diagnostic": private_value},
        )

        async with factory() as database:
            row = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == UUID(run.run_id),
                    GenerationEvent.sequence == event.sequence,
                )
            )
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == UUID(run.run_id)
                )
            )

        assert row is not None and row.forensic_ref is None
        assert manifest is not None and manifest.state == "degraded"
        assert manifest.last_event_sequence == event.sequence - 1
        assert manifest.metadata_json["failure_code"] == "storage_write_failed"
        assert private_value not in caplog.text
        assert private_value not in json.dumps(row.payload, ensure_ascii=False)
        assert private_value not in json.dumps(manifest.metadata_json, ensure_ascii=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_forensic_events_reconcile_complete_manifest(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    first = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )
    second = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await first.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await asyncio.gather(
            *(
                (first if index % 2 == 0 else second).append_event(
                    run.run_id,
                    event_type="stage.started",
                    stage=Stage.FOUNDATION,
                    status="running",
                    message=f"attempt {index}",
                    forensic_payload={"attempt": index, "worker_id": f"worker-{index}"},
                )
                for index in range(20)
            )
        )

        reopened = await GenerationForensicRecorder.open(
            factory,
            recorder.config,
        )
        file_manifest = await reopened.load_manifest(UUID(run.run_id))
        async with factory() as database:
            sql_manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == UUID(run.run_id)
                )
            )

        assert sql_manifest is not None
        assert len(file_manifest.entries) == 21
        assert len({item.relative_path for item in file_manifest.entries}) == 21
        assert sql_manifest.entry_count == 21
        assert sql_manifest.byte_count == sum(
            item.byte_count for item in file_manifest.entries
        )
        assert sql_manifest.last_event_sequence == 21
        assert sql_manifest.manifest_sha256 == await reopened.manifest_digest(
            UUID(run.run_id)
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_reconciles_forensic_event_left_by_rolled_back_transaction(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        run_id = UUID(run.run_id)
        orphan_created_at = datetime.now(timezone.utc)
        async with factory() as database:
            transaction = await database.begin()
            durable_run = await database.get(GenerationRun, run_id)
            assert durable_run is not None
            orphan = await recorder.record_event(
                database,
                durable_run,
                sequence=2,
                prepared=prepare_generation_event(
                    event_type="stage.started",
                    public_message="orphaned attempt",
                    operational_payload={
                        "status": "running",
                        "stage": "foundation",
                    },
                    forensic_payload={"attempt_id": "rolled-back-attempt"},
                ),
                created_at=orphan_created_at,
            )
            assert orphan is not None and orphan.written
            await transaction.rollback()

        async with factory() as database:
            stale_manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )
        assert stale_manifest is not None
        assert stale_manifest.last_event_sequence == 1

        retried = await store.append_event(
            run.run_id,
            event_type="stage.started",
            stage=Stage.FOUNDATION,
            status="running",
            message="retried attempt",
            forensic_payload={"attempt_id": "rolled-back-attempt"},
        )

        async with factory() as database:
            row = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.sequence == retried.sequence,
                )
            )
            sql_manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )
        file_manifest = await recorder.load_manifest(run_id)

        assert row is not None
        assert row.forensic_ref == "events/00000002-stage.started.json"
        assert sql_manifest is not None
        assert sql_manifest.state == "active"
        assert sql_manifest.last_event_sequence == 2
        assert sql_manifest.entry_count == len(file_manifest.entries)
        assert sql_manifest.byte_count == sum(
            entry.byte_count for entry in file_manifest.entries
        )
        assert sql_manifest.manifest_sha256 == await recorder.manifest_digest(run_id)
        assert sql_manifest.metadata_json["reconciled_existing_event"] is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_does_not_reuse_conflicting_orphan_forensic_event(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        run_id = UUID(run.run_id)
        async with factory() as database:
            transaction = await database.begin()
            durable_run = await database.get(GenerationRun, run_id)
            assert durable_run is not None
            orphan = await recorder.record_event(
                database,
                durable_run,
                sequence=2,
                prepared=prepare_generation_event(
                    event_type="stage.started",
                    public_message="orphaned attempt",
                    operational_payload={
                        "status": "running",
                        "stage": "foundation",
                    },
                    forensic_payload={
                        "attempt_id": "rolled-back-attempt",
                        "diagnostic": "first private failure",
                    },
                ),
                created_at=datetime.now(timezone.utc),
            )
            assert orphan is not None and orphan.written
            await transaction.rollback()

        retried = await store.append_event(
            run.run_id,
            event_type="stage.started",
            stage=Stage.FOUNDATION,
            status="running",
            message="retried attempt",
            forensic_payload={
                "attempt_id": "different-attempt",
                "diagnostic": "different private failure",
            },
        )

        async with factory() as database:
            row = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.sequence == retried.sequence,
                )
            )
            sql_manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )

        assert row is not None
        assert row.forensic_ref is None
        assert sql_manifest is not None
        assert sql_manifest.state == "degraded"
        assert sql_manifest.last_event_sequence == 2
        assert sql_manifest.metadata_json["failure_code"] == "orphan_event_conflict"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retry_rejects_other_event_type_at_orphaned_sequence(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        run_id = UUID(run.run_id)
        async with factory() as database:
            transaction = await database.begin()
            durable_run = await database.get(GenerationRun, run_id)
            assert durable_run is not None
            orphan = await recorder.record_event(
                database,
                durable_run,
                sequence=2,
                prepared=prepare_generation_event(
                    event_type="stage.started",
                    public_message="orphaned start",
                    operational_payload={
                        "status": "running",
                        "stage": "foundation",
                    },
                    forensic_payload={"attempt_id": "rolled-back-attempt"},
                ),
                created_at=datetime.now(timezone.utc),
            )
            assert orphan is not None and orphan.written
            await transaction.rollback()

        retried = await store.append_event(
            run.run_id,
            event_type="stage.completed",
            stage=Stage.FOUNDATION,
            status="completed",
            message="different event at reused sequence",
            forensic_payload={"attempt_id": "different-attempt"},
        )

        async with factory() as database:
            row = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.sequence == retried.sequence,
                )
            )
            sql_manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == run_id
                )
            )
        file_manifest = await recorder.load_manifest(run_id)

        assert row is not None and row.forensic_ref is None
        assert sql_manifest is not None
        assert sql_manifest.state == "degraded"
        assert sql_manifest.metadata_json["failure_code"] == "orphan_event_conflict"
        assert [
            entry.relative_path
            for entry in file_manifest.entries
            if entry.kind == "event" and entry.relative_path.startswith("events/00000002-")
        ] == ["events/00000002-stage.started.json"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED),
)
async def test_terminal_forensic_manifest_has_exact_five_day_expiry(
    tmp_path,
    status: RunStatus,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.mark_terminal(run.run_id, status)

        async with factory() as database:
            durable_run = await database.get(GenerationRun, UUID(run.run_id))
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == UUID(run.run_id)
                )
            )

        assert durable_run is not None and durable_run.finished_at is not None
        assert manifest is not None and manifest.expires_at is not None
        assert manifest.state == status.value
        assert manifest.expires_at == durable_run.finished_at + timedelta(hours=120)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_terminal_forensic_write_does_not_shift_expiry(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.mark_terminal(run.run_id, RunStatus.FAILED)

        async with factory() as database, database.begin():
            durable_run = await database.get(GenerationRun, UUID(run.run_id))
            terminal_event = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == UUID(run.run_id),
                    GenerationEvent.event_type == "run.terminal_marked",
                )
            )
            manifest = await database.scalar(
                select(GenerationForensicManifest)
                .where(GenerationForensicManifest.run_id == UUID(run.run_id))
                .with_for_update()
            )
            assert durable_run is not None and durable_run.finished_at is not None
            assert terminal_event is not None and terminal_event.created_at is not None
            assert manifest is not None and manifest.expires_at is not None
            original_expiry = manifest.expires_at
            created_at = terminal_event.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            later_finished_at = durable_run.finished_at
            if later_finished_at.tzinfo is None:
                later_finished_at = later_finished_at.replace(tzinfo=timezone.utc)
            later_finished_at += timedelta(hours=12)

            result = await recorder.record_event(
                database,
                durable_run,
                sequence=terminal_event.sequence,
                prepared=prepare_generation_event(
                    event_type="run.terminal_marked",
                    public_message=terminal_event.public_message,
                    operational_payload=terminal_event.payload,
                ),
                created_at=created_at,
                terminal_state="failed",
                finished_at=later_finished_at,
            )

            assert result is not None and result.degraded is False
            assert result.entry is not None
            assert result.entry.relative_path == (
                f"events/{terminal_event.sequence:08d}-run.terminal_marked.json"
            )
            assert manifest.expires_at == original_expiry
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_storage_failure_keeps_terminal_state_and_exact_expiry(
    tmp_path,
    monkeypatch,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    recorder = await _forensic_recorder(tmp_path, factory)
    store = PostgresRunStore(
        factory,
        project_id=project_id,
        forensic_recorder=recorder,
    )

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )

        def fail_write_event(**_kwargs):
            raise OSError("private terminal storage failure")

        monkeypatch.setattr(recorder.storage, "write_event", fail_write_event)
        await store.mark_terminal(run.run_id, RunStatus.FAILED)

        async with factory() as database:
            durable_run = await database.get(GenerationRun, UUID(run.run_id))
            manifest = await database.scalar(
                select(GenerationForensicManifest).where(
                    GenerationForensicManifest.run_id == UUID(run.run_id)
                )
            )

        assert durable_run is not None and durable_run.finished_at is not None
        assert manifest is not None and manifest.expires_at is not None
        assert manifest.state == "failed"
        assert manifest.metadata_json == {
            "degraded": True,
            "failure_code": "storage_write_failed",
        }
        assert manifest.last_event_sequence == 1
        assert manifest.expires_at == durable_run.finished_at + timedelta(hours=120)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_appends_reserve_monotonic_sequences_and_restore(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    request = BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
    first = PostgresRunStore(factory, project_id=project_id)
    second = PostgresRunStore(factory, project_id=project_id)

    try:
        created = await first.create(request)
        await first.set_running(created.run_id)

        events = await asyncio.gather(
            *(
                (first if index % 2 == 0 else second).append_event(
                    created.run_id,
                    event_type="stage.started",
                    stage=Stage.FOUNDATION,
                    status="running",
                    message=f"step {index}",
                    usage=TokenUsage(prompt_tokens=1),
                )
                for index in range(12)
            )
        )
        assert sorted(event.sequence for event in events) == list(range(2, 14))

        committed = artifact(revision=2, stage=Stage.FOUNDATION)
        await first.commit_artifact(created.run_id, committed)

        restored_store = PostgresRunStore(factory, project_id=project_id)
        restored = await restored_store.snapshot(created.run_id)
        restored_events = await restored_store.events_after(created.run_id, 0)

        assert restored.request == request
        assert restored.status is RunStatus.RUNNING
        assert restored.latest_sequence == 13
        assert restored.usage.prompt_tokens == 12
        assert [event.sequence for event in restored_events] == list(range(1, 14))
        assert await restored_store.artifact(created.run_id) == committed
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_artifact_json_is_immutable_and_revisioned(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        first = artifact(revision=1, stage=Stage.ART_DIRECTION)
        original_accent = first.theme_tokens["accent"]
        await store.commit_artifact(run.run_id, first)
        first.theme_tokens["accent"] = "mutated after commit"

        with pytest.raises(ValueError, match="monotonically"):
            await store.commit_artifact(run.run_id, artifact(revision=1))

        second = artifact(revision=2, stage=Stage.FOUNDATION)
        await store.commit_artifact(run.run_id, second)
        restored_store = PostgresRunStore(factory, project_id=project_id)

        assert (await restored_store.artifact(run.run_id, 1)).theme_tokens[
            "accent"
        ] == original_accent
        assert await restored_store.artifact(run.run_id) == second
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_committing_staged_draft_promotes_identical_revision(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.commit_artifact(
            run.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        candidate = artifact(revision=2, stage=Stage.FOUNDATION)
        await store.stage_visual_candidate(run.run_id, candidate)
        await store.stage_visual_draft(run.run_id, candidate)

        committed = await store.commit_visual_candidate(run.run_id)

        restored = await PostgresRunStore(
            factory, project_id=project_id
        ).snapshot(run.run_id)
        assert committed == candidate
        assert restored.artifact == candidate
        assert restored.draft_artifact is None
        assert restored.quality_status == "verified"
        async with factory() as database:
            rows = (
                await database.execute(
                    select(GenerationArtifact).where(
                        GenerationArtifact.run_id == UUID(run.run_id),
                        GenerationArtifact.revision == candidate.revision,
                    )
                )
            ).scalars().all()
        assert len(rows) == 1
        assert rows[0].quality_status == "verified"
        assert rows[0].config["artifact"] == candidate.to_dict()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_draft_promotion_rejects_changed_candidate_at_same_revision(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        candidate = artifact(revision=1, stage=Stage.ART_DIRECTION)
        changed_draft = artifact(
            revision=1,
            stage=Stage.ART_DIRECTION,
            css=candidate.css + "\n.changed {}",
        )
        await store.stage_visual_candidate(run.run_id, candidate)
        await store.stage_visual_draft(run.run_id, changed_draft)

        with pytest.raises(ValueError, match="differs from persisted draft"):
            await store.commit_visual_candidate(run.run_id)
        with pytest.raises(ArtifactNotFound):
            await store.visual_candidate(run.run_id)

        restored = await store.snapshot(run.run_id)
        assert restored.artifact is None
        assert restored.draft_artifact == changed_draft
        assert restored.quality_status == "needs_repair"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_repeated_visual_repair_drafts_keep_history_and_commit_latest(
    tmp_path,
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.commit_artifact(
            run.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        first_draft = artifact(revision=2, stage=Stage.FOUNDATION)
        repaired_draft = artifact(
            revision=2,
            stage=Stage.FOUNDATION,
            css=first_draft.css + "\n.repaired {}",
        )

        await store.stage_visual_candidate(run.run_id, first_draft)
        await store.stage_visual_draft(run.run_id, first_draft)
        assert (await store.snapshot(run.run_id)).draft_artifact == first_draft

        await store.stage_visual_candidate(run.run_id, repaired_draft)
        await store.stage_visual_draft(run.run_id, repaired_draft)
        before_commit = await store.snapshot(run.run_id)
        assert before_commit.artifact.revision == 1
        assert before_commit.draft_artifact == repaired_draft
        assert before_commit.quality_status == "needs_repair"

        committed = await store.commit_visual_candidate(run.run_id)
        restored = await PostgresRunStore(
            factory, project_id=project_id
        ).snapshot(run.run_id)

        assert committed == repaired_draft
        assert restored.artifact == repaired_draft
        assert restored.draft_artifact is None
        assert restored.quality_status == "verified"
        async with factory() as database:
            artifact_rows = (
                await database.execute(
                    select(GenerationArtifact).where(
                        GenerationArtifact.run_id == UUID(run.run_id),
                        GenerationArtifact.revision == repaired_draft.revision,
                    )
                )
            ).scalars().all()
            draft_events = (
                await database.execute(
                    select(GenerationEvent)
                    .where(
                        GenerationEvent.run_id == UUID(run.run_id),
                        GenerationEvent.event_type == "artifact.draft_staged",
                    )
                    .order_by(GenerationEvent.sequence)
                )
            ).scalars().all()
        assert len(artifact_rows) == 1
        assert artifact_rows[0].config["artifact"] == repaired_draft.to_dict()
        assert [event.payload["artifact"] for event in draft_events] == [
            first_draft.to_dict(),
            repaired_draft.to_dict(),
        ]
        assert draft_events[0].payload["supersedes_sequence"] is None
        assert (
            draft_events[1].payload["supersedes_sequence"]
            == draft_events[0].sequence
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_verified_artifact_cannot_be_restaged_at_same_revision(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        verified = artifact(revision=1, stage=Stage.ART_DIRECTION)
        await store.commit_artifact(run.run_id, verified)

        with pytest.raises(
            ValueError, match="visual draft revision must exceed public revision"
        ):
            await store.stage_visual_draft(
                run.run_id,
                artifact(
                    revision=1,
                    stage=Stage.ART_DIRECTION,
                    css=verified.css + "\n.changed {}",
                ),
            )

        assert await store.artifact(run.run_id, 1) == verified
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stale_run_cannot_overwrite_active_project_summary(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        stale = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="First run")
        )
        active = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Second run")
        )

        await store.set_running(stale.run_id)
        await store.commit_artifact(
            stale.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        await store.finish(
            stale.run_id,
            RunStatus.COMPLETED,
            event_type="run.completed",
            stage=Stage.ART_DIRECTION,
            message="stale done",
            revision=1,
        )

        async with factory() as database:
            project = await database.get(Project, project_id)
            assert project.active_run_id == UUID(active.run_id)
            assert project.active_revision is None
            assert project.status == "generating"

        await store.commit_artifact(
            active.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        await store.finish(
            active.run_id,
            RunStatus.COMPLETED,
            event_type="run.completed",
            stage=Stage.ART_DIRECTION,
            message="active done",
            revision=1,
        )
        async with factory() as database:
            project = await database.get(Project, project_id)
            assert project.active_run_id == UUID(active.run_id)
            assert project.active_revision == 1
            assert project.status == "completed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mark_terminal_restores_elapsed_seconds(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.mark_terminal(
            run.run_id,
            RunStatus.FAILED,
            error_code="provider_unavailable",
            elapsed_seconds=4.25,
        )

        restored = await PostgresRunStore(
            factory, project_id=project_id
        ).snapshot(run.run_id)
        assert restored.status is RunStatus.FAILED
        assert restored.error_code == "provider_unavailable"
        assert restored.elapsed_seconds == 4.25
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_uses_only_bounded_event_queries(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        for index in range(12):
            await store.append_event(
                run.run_id,
                event_type="stage.started",
                stage=Stage.FOUNDATION,
                status="running",
                message=f"step {index}",
                usage=TokenUsage(prompt_tokens=1),
            )

        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        try:
            restored = await store.snapshot(run.run_id)
        finally:
            event.remove(
                engine.sync_engine, "before_cursor_execute", record_statement
            )

        assert restored.usage.prompt_tokens == 12
        event_selects = [
            statement.upper()
            for statement in statements
            if "FROM GENERATION_EVENTS" in statement.upper()
        ]
        assert event_selects
        assert all(
            " LIMIT " in statement
            or "SUM(" in statement
            or "EXISTS" in statement
            for statement in event_selects
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_events_after_returns_bounded_pages(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        for index in range(105):
            await store.append_event(
                run.run_id,
                event_type="stage.started",
                stage=Stage.FOUNDATION,
                status="running",
                message=f"step {index}",
            )

        first_page = await store.events_after(run.run_id, 0)
        second_page = await store.events_after(
            run.run_id, first_page[-1].sequence
        )
        assert len(first_page) == 100
        assert len(second_page) == 6
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wait_for_events_does_not_poll_full_snapshot(
    tmp_path, monkeypatch
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )

        async def reject_snapshot(_run_id: str):
            raise AssertionError("wait_for_events must not call snapshot")

        monkeypatch.setattr(store, "snapshot", reject_snapshot)
        assert await store.wait_for_events(
            run.run_id, run.latest_sequence, timeout=0.01
        ) == ()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wait_for_events_uses_bounded_query_load(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        started = monotonic()
        try:
            assert await store.wait_for_events(
                run.run_id, run.latest_sequence, timeout=0.75
            ) == ()
        finally:
            elapsed = monotonic() - started
            event.remove(
                engine.sync_engine, "before_cursor_execute", record_statement
            )

        assert elapsed < 1.0
        assert len(statements) <= 7
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wait_for_events_wakes_promptly_for_terminal_event(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        started = monotonic()
        waiter = asyncio.create_task(
            store.wait_for_events(
                run.run_id, run.latest_sequence, timeout=2.0
            )
        )
        await asyncio.sleep(0.06)
        await store.mark_terminal(run.run_id, RunStatus.FAILED)

        events = await waiter
        assert monotonic() - started < 0.4
        assert [item.event_type for item in events] == ["run.terminal_marked"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_locks_are_evicted_after_operations(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        run = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")
        )
        await store.append_event(
            run.run_id,
            event_type="stage.started",
            stage=Stage.FOUNDATION,
            status="running",
            message="step",
        )
        key = (store._namespace, UUID(run.run_id))
        gc.collect()
        assert key not in postgres_store_module._RUN_LOCKS
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_and_cancel_paths_clear_visual_candidates(tmp_path) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)

    try:
        terminal = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Terminal")
        )
        cancelled = await store.create(
            BuilderRequest(engine=EngineName.DIRECT, brief="Cancelled")
        )
        await store.stage_visual_candidate(
            terminal.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )
        await store.stage_visual_candidate(
            cancelled.run_id, artifact(revision=1, stage=Stage.ART_DIRECTION)
        )

        await store.mark_terminal(terminal.run_id, RunStatus.FAILED)
        assert await store.request_cancel(cancelled.run_id)

        with pytest.raises(ArtifactNotFound):
            await store.visual_candidate(terminal.run_id)
        with pytest.raises(ArtifactNotFound):
            await store.visual_candidate(cancelled.run_id)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_visual_candidate_capacity_rejects_without_evicting_staged_work(
    tmp_path, monkeypatch
) -> None:
    engine, factory, project_id = await _database(tmp_path)
    store = PostgresRunStore(factory, project_id=project_id)
    monkeypatch.setattr(postgres_store_module, "_MAX_VISUAL_CANDIDATES", 1)

    try:
        runs = [
            await store.create(
                BuilderRequest(engine=EngineName.DIRECT, brief=f"Run {index}")
            )
            for index in range(2)
        ]
        candidate = artifact(revision=1, stage=Stage.ART_DIRECTION)
        await store.stage_visual_candidate(runs[0].run_id, candidate)
        restaged = artifact(
            revision=1,
            stage=Stage.ART_DIRECTION,
            css=candidate.css + "\n.restaged {}",
        )
        await store.stage_visual_candidate(runs[0].run_id, restaged)

        with pytest.raises(
            VisualCandidateCapacityExceeded,
            match="visual candidate capacity",
        ):
            await store.stage_visual_candidate(runs[1].run_id, candidate)

        assert await store.commit_visual_candidate(runs[0].run_id) == restaged
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "live_read_method",
    ("artifact", "preview_artifact", "events_after", "wait_for_events"),
)
def test_orchestrator_store_protocol_excludes_live_read_methods(
    live_read_method: str,
) -> None:
    assert live_read_method not in RunStoreProtocol.__dict__
