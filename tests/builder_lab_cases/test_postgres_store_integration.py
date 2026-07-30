import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import GenerationRun, Project
from builder_lab.models import (
    BuilderRequest,
    EngineName,
    RunStatus,
    Stage,
    TokenUsage,
)
from builder_lab.postgres_store import PostgresRunStore


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.postgres,
    pytest.mark.skipif(
        not POSTGRES_URL,
        reason="set KAIGO_TEST_POSTGRES_URL to a disposable PostgreSQL 15 database",
    ),
]


async def test_postgres_15_concurrency_and_snapshot_consistency() -> None:
    """Exercise guarantees SQLite cannot prove against a disposable database."""
    first_engine = create_async_engine(POSTGRES_URL)
    second_engine = create_async_engine(POSTGRES_URL)
    first_factory = async_sessionmaker(first_engine, expire_on_commit=False)
    second_factory = async_sessionmaker(second_engine, expire_on_commit=False)

    try:
        async with first_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

        async with first_factory() as database, database.begin():
            database.add(Tenant(id=1, name="Alpha", slug="alpha"))
            await database.flush()
            database.add(User(id=10, tenant_id=1, email="owner@example.com"))
            await database.flush()
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
                brief="PostgreSQL probe",
            )
            database.add(project)
            await database.flush()
            project_id = project.id

        first = PostgresRunStore(first_factory, project_id=project_id)
        second = PostgresRunStore(second_factory, project_id=project_id)
        request = BuilderRequest(engine=EngineName.DIRECT, brief="PostgreSQL probe")
        stale_run = await first.create(request)

        events = await asyncio.gather(
            *(
                (first if index % 2 == 0 else second).append_event(
                    stale_run.run_id,
                    event_type="stage.started",
                    stage=Stage.FOUNDATION,
                    status="running",
                    message=f"sequence {index}",
                    usage=TokenUsage(prompt_tokens=1),
                )
                for index in range(16)
            )
        )
        assert sorted(event.sequence for event in events) == list(range(2, 18))

        active_run = await second.create(request)
        await first.finish(
            stale_run.run_id,
            RunStatus.COMPLETED,
            event_type="run.completed",
            stage=Stage.FOUNDATION,
            message="stale run finished",
        )
        async with first_factory() as database:
            project = await database.get(Project, project_id)
            assert str(project.active_run_id) == active_run.run_id
            assert project.status == "generating"

        run_uuid = first._run_uuid(active_run.run_id)
        writer_ready = asyncio.Event()
        release_writer = asyncio.Event()

        async def write_locked_state() -> None:
            async with second_factory() as database, database.begin():
                run = (
                    await database.execute(
                        select(GenerationRun)
                        .where(GenerationRun.id == run_uuid)
                        .with_for_update()
                    )
                ).scalar_one()
                run.state = RunStatus.RUNNING.value
                await second._append_record(
                    database,
                    run,
                    event_type="stage.started",
                    stage=Stage.FOUNDATION,
                    status="running",
                    message="coherent state",
                    usage=TokenUsage(output_tokens=7),
                )
                writer_ready.set()
                await release_writer.wait()

        writer = asyncio.create_task(write_locked_state())
        await writer_ready.wait()
        reader = asyncio.create_task(first.snapshot(active_run.run_id))
        try:
            await asyncio.sleep(0.1)
            assert not reader.done(), "snapshot must wait for the run row writer"
        finally:
            release_writer.set()
            await writer
        snapshot = await reader

        assert snapshot.status is RunStatus.RUNNING
        assert snapshot.latest_sequence == 2
        assert snapshot.usage.output_tokens == 7

        waiter = asyncio.create_task(
            first.wait_for_events(
                active_run.run_id, snapshot.latest_sequence, timeout=2.0
            )
        )
        await asyncio.sleep(0.06)
        await second.mark_terminal(active_run.run_id, RunStatus.FAILED)
        terminal_events = await asyncio.wait_for(waiter, timeout=0.5)
        assert [item.event_type for item in terminal_events] == [
            "run.terminal_marked"
        ]
    finally:
        async with first_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await second_engine.dispose()
        await first_engine.dispose()
