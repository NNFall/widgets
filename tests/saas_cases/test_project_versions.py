from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.patterns.repository import PatternRepository
from app.projects.versions import (
    ProjectBusy,
    ProjectVersionConflict,
    ProjectVersionNotRefinable,
    ProjectVersionService,
)
from app.saas.models import (
    CompositionPlanItem,
    CompositionPlanRecord,
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    Project,
    ProjectVersion,
    UsageLedger,
)
from builder_lab.models import AssistantPersona, BuilderRequest, EngineName
from builder_lab.patterns.models import (
    CompositionPlan,
    PatternCategory,
    PatternSelection,
)
from builder_lab.patterns.registry import load_builtin_registry
from tests.builder_lab_cases.test_validation import artifact


USER_WISH_HEADING = "ПОЖЕЛАНИЕ ПОЛЬЗОВАТЕЛЯ"


def _assistant_persona() -> AssistantPersona:
    return AssistantPersona.from_dict(
        {
            "schema_version": "kaigo.assistant-persona.v1",
            "employee_type": "sales_advisor",
            "display_name": "Пекарь Печкин",
            "role_summary": "Тёпло и по делу помогает выбрать выпечку.",
            "voice_style": "cheerful",
            "opening_line": "Добрый день! Помочь выбрать выпечку?",
            "behavior_rules": [
                "Говори живо, но без рекламного нажима.",
                "Сначала уточни вкус и повод.",
            ],
            "safeguards": ["Не придумывай цены и ассортимент."],
            "decision_rationale": "Тематическое имя естественно для grounded-пекарни.",
        }
    )


def _plan() -> CompositionPlan:
    patterns = {
        PatternCategory.LAUNCHER: "orb-pulse",
        PatternCategory.SHELL: "compact-chat",
        PatternCategory.MESSAGES: "paired-bubbles",
        PatternCategory.COMPOSER: "single-line-pill",
        PatternCategory.MOTION: "spring-reveal",
    }
    return CompositionPlan(
        schema_version=1,
        direction_id="candidate-2",
        selections=tuple(
            PatternSelection(
                slot=slot,
                pattern_id=pattern_id,
                version=1,
                parameters={"variant": slot.value},
                reason=f"Persisted {slot.value}",
            )
            for slot, pattern_id in patterns.items()
        ),
        summary="Persisted source composition",
    )


@dataclass(frozen=True, slots=True)
class SeededVersion:
    engine: object
    factory: object
    project_id: UUID
    source_run_id: UUID
    source_artifact_id: UUID
    source_version_id: UUID
    request: BuilderRequest
    plan: CompositionPlan


async def _seed(
    tmp_path,
    *,
    source_brief: str = "Build the durable source widget",
    quality_status: str = "verified",
    persist_plan: bool = True,
    artifact_persona: AssistantPersona | None = None,
) -> SeededVersion:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / f'{uuid4()}.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    request = BuilderRequest(
        engine=EngineName.DIRECT,
        brief=source_brief,
        reference_context="Durable visual reference context",
        source_url="https://example.com/services",
        locale="ru",
        creativity=1.1,
        viewport_targets=("desktop", "mobile"),
        max_repairs=4,
        contract_id="chat-v1",
        visual_repair_limit=9,
    )
    plan = _plan()
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        await database.flush()
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        await database.flush()
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://mutable.example/",
            brief="MUTABLE PROJECT BRIEF MUST NOT BE USED",
        )
        database.add(project)
        await database.flush()
        source_run = GenerationRun(
            project_id=project.id,
            mode="express",
            state="completed",
            progress=100,
            next_event_sequence=2,
            idempotency_key="source-run",
            last_completed_stage="motion_polish",
            finished_at=datetime.now(UTC),
        )
        database.add(source_run)
        await database.flush()
        database.add(
            GenerationEvent(
                id=secrets.randbits(62),
                run_id=source_run.id,
                sequence=1,
                event_type="run.created",
                public_message="Source run created",
                payload={"status": "queued", "request": request.to_dict()},
            )
        )
        candidate = artifact(revision=5)
        artifact_config = {"artifact": candidate.to_dict()}
        if artifact_persona is not None:
            artifact_config["assistant_persona"] = artifact_persona.to_dict()
        source_artifact = GenerationArtifact(
            run_id=source_run.id,
            revision=candidate.revision,
            stage=candidate.stage.value,
            html=candidate.body_html,
            css=candidate.css,
            javascript=candidate.javascript,
            config=artifact_config,
            quality_status=quality_status,
        )
        database.add(source_artifact)
        await database.flush()
        source_version = ProjectVersion(
            project_id=project.id,
            ordinal=1,
            run_id=source_run.id,
            artifact_id=source_artifact.id,
            kind="initial",
        )
        database.add(source_version)
        await database.flush()
        project.active_run_id = source_run.id
        project.active_revision = source_artifact.revision
        project.active_version_id = source_version.id
        project.status = "free_result_ready"
        if persist_plan:
            await PatternRepository(database).create_plan(
                run_id=source_run.id,
                plan=plan,
                registry=load_builtin_registry(),
            )
        return SeededVersion(
            engine=engine,
            factory=factory,
            project_id=project.id,
            source_run_id=source_run.id,
            source_artifact_id=source_artifact.id,
            source_version_id=source_version.id,
            request=request,
            plan=plan,
        )


@pytest.mark.asyncio
async def test_enqueue_refinement_uses_durable_request_and_clones_plan_atomically(
    tmp_path,
) -> None:
    seeded = await _seed(tmp_path)
    change_request = "Сделай приветствие короче"
    try:
        async with seeded.factory() as database, database.begin():
            run = await ProjectVersionService(database).enqueue_refinement(
                seeded.project_id,
                source_version_id=seeded.source_version_id,
                expected_active_version_id=seeded.source_version_id,
                change_request=change_request,
                idempotency_key="refine-short-greeting",
                actor_user_id=10,
                tenant_id=1,
            )
            run_id = run.id

        async with seeded.factory() as database:
            run = await database.get(GenerationRun, run_id)
            project = await database.get(Project, seeded.project_id)
            created = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.event_type == "run.created",
                )
            )
            cloned = await PatternRepository(database).load_plan(run_id)
            source = await PatternRepository(database).load_plan(seeded.source_run_id)
            trial_rows = await database.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.entry_type == "trial.reserve")
            )

        assert run is not None
        assert project is not None
        assert created is not None
        assert run.mode == "express"
        assert run.state == "queued"
        assert run.last_completed_stage == "conversation"
        assert run.source_version_id == seeded.source_version_id
        assert run.change_request == change_request
        assert project.active_run_id == run.id
        assert project.active_version_id == seeded.source_version_id
        assert project.active_revision == 5
        request = BuilderRequest.from_dict(created.payload["request"])
        assert request.source_url == seeded.request.source_url
        assert request.reference_context == seeded.request.reference_context
        assert request.locale == seeded.request.locale
        assert request.creativity == seeded.request.creativity
        assert "MUTABLE PROJECT BRIEF" not in request.brief
        assert request.brief.endswith(f"{USER_WISH_HEADING}:\n{change_request}")
        assert len(request.brief) <= 12_000
        assert cloned is not None and source is not None
        assert cloned.id != source.id
        assert cloned.plan == source.plan == seeded.plan
        assert cloned.implementation_hashes == source.implementation_hashes
        assert trial_rows == 0
    finally:
        await seeded.engine.dispose()


@pytest.mark.asyncio
async def test_refinement_inherits_server_owned_persona_from_source_artifact(
    tmp_path,
) -> None:
    persona = _assistant_persona()
    seeded = await _seed(tmp_path, artifact_persona=persona)
    try:
        assert seeded.request.assistant_persona is None
        async with seeded.factory() as database, database.begin():
            run = await ProjectVersionService(database).enqueue_refinement(
                seeded.project_id,
                source_version_id=seeded.source_version_id,
                expected_active_version_id=seeded.source_version_id,
                change_request="Сделай приветствие короче",
                idempotency_key="refine-with-artifact-persona",
                actor_user_id=10,
                tenant_id=1,
            )
            created = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == run.id,
                    GenerationEvent.event_type == "run.created",
                )
            )

        assert created is not None
        request = BuilderRequest.from_dict(created.payload["request"])
        assert request.assistant_persona == persona
    finally:
        await seeded.engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_refinement_keeps_full_2000_character_wish_within_brief_bound(
    tmp_path,
) -> None:
    seeded = await _seed(tmp_path, source_brief="B" * 11_999)
    change_request = "🟠" * 2_000
    try:
        async with seeded.factory() as database, database.begin():
            run = await ProjectVersionService(database).enqueue_refinement(
                seeded.project_id,
                source_version_id=seeded.source_version_id,
                expected_active_version_id=seeded.source_version_id,
                change_request=change_request,
                idempotency_key="refine-unicode-bound",
                actor_user_id=10,
                tenant_id=1,
            )
            created = await database.scalar(
                select(GenerationEvent).where(
                    GenerationEvent.run_id == run.id,
                    GenerationEvent.event_type == "run.created",
                )
            )

        assert created is not None
        request = BuilderRequest.from_dict(created.payload["request"])
        assert len(request.brief) <= 12_000
        assert request.brief.endswith(change_request)
        assert request.brief.count("🟠") == 2_000
    finally:
        await seeded.engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_refinement_replay_is_semantic_and_precedes_busy_check(
    tmp_path,
) -> None:
    seeded = await _seed(tmp_path)
    try:
        async with seeded.factory() as database, database.begin():
            service = ProjectVersionService(database)
            first = await service.enqueue_refinement(
                seeded.project_id,
                source_version_id=seeded.source_version_id,
                expected_active_version_id=seeded.source_version_id,
                change_request="  Укороти приветствие  ",
                idempotency_key="semantic-refinement",
                actor_user_id=10,
                tenant_id=1,
            )
            first_id = first.id

        async with seeded.factory() as database, database.begin():
            replay = await ProjectVersionService(database).enqueue_refinement(
                seeded.project_id,
                source_version_id=seeded.source_version_id,
                expected_active_version_id=seeded.source_version_id,
                change_request="Укороти приветствие",
                idempotency_key="semantic-refinement",
                actor_user_id=10,
                tenant_id=1,
            )
            assert replay.id == first_id

        async with seeded.factory() as database, database.begin():
            with pytest.raises(ProjectVersionConflict):
                await ProjectVersionService(database).enqueue_refinement(
                    seeded.project_id,
                    source_version_id=seeded.source_version_id,
                    expected_active_version_id=seeded.source_version_id,
                    change_request="Измени другой блок",
                    idempotency_key="semantic-refinement",
                    actor_user_id=10,
                    tenant_id=1,
                )
    finally:
        await seeded.engine.dispose()


@pytest.mark.asyncio
async def test_enqueue_refinement_requires_persisted_plan_and_no_active_run(
    tmp_path,
) -> None:
    missing_plan = await _seed(tmp_path, persist_plan=False)
    try:
        async with missing_plan.factory() as database, database.begin():
            with pytest.raises(ProjectVersionNotRefinable):
                await ProjectVersionService(database).enqueue_refinement(
                    missing_plan.project_id,
                    source_version_id=missing_plan.source_version_id,
                    expected_active_version_id=missing_plan.source_version_id,
                    change_request="Сделай компактнее",
                    idempotency_key="missing-plan",
                    actor_user_id=10,
                    tenant_id=1,
                )
    finally:
        await missing_plan.engine.dispose()

    busy = await _seed(tmp_path)
    try:
        async with busy.factory() as database, database.begin():
            project = await database.get(Project, busy.project_id)
            queued = GenerationRun(
                project_id=busy.project_id,
                mode="express",
                state="queued",
                progress=0,
                idempotency_key="already-running",
            )
            database.add(queued)
            await database.flush()
            project.active_run_id = queued.id
        async with busy.factory() as database, database.begin():
            with pytest.raises(ProjectBusy):
                await ProjectVersionService(database).enqueue_refinement(
                    busy.project_id,
                    source_version_id=busy.source_version_id,
                    expected_active_version_id=busy.source_version_id,
                    change_request="Сделай компактнее",
                    idempotency_key="busy-refinement",
                    actor_user_id=10,
                    tenant_id=1,
                )
    finally:
        await busy.engine.dispose()


@pytest.mark.asyncio
async def test_clone_plan_copies_persisted_rows_without_registry_resolution(tmp_path) -> None:
    seeded = await _seed(tmp_path)
    try:
        async with seeded.factory() as database, database.begin():
            target = GenerationRun(
                project_id=seeded.project_id,
                mode="express",
                state="queued",
                progress=0,
                idempotency_key="clone-target",
            )
            database.add(target)
            await database.flush()
            source_record = await database.scalar(
                select(CompositionPlanRecord).where(
                    CompositionPlanRecord.run_id == seeded.source_run_id
                )
            )
            source_items = list(
                (
                    await database.execute(
                        select(CompositionPlanItem)
                        .where(
                            CompositionPlanItem.composition_plan_id
                            == source_record.id
                        )
                        .order_by(CompositionPlanItem.slot)
                    )
                ).scalars()
            )
            cloned = await PatternRepository(database).clone_plan(
                source_run_id=seeded.source_run_id,
                target_run_id=target.id,
            )
            cloned_items = list(
                (
                    await database.execute(
                        select(CompositionPlanItem)
                        .where(
                            CompositionPlanItem.composition_plan_id == cloned.id
                        )
                        .order_by(CompositionPlanItem.slot)
                    )
                ).scalars()
            )

        assert cloned.id != source_record.id
        assert cloned.plan == seeded.plan
        assert {item.id for item in cloned_items}.isdisjoint(
            {item.id for item in source_items}
        )
        assert [item.pattern_version_id for item in cloned_items] == [
            item.pattern_version_id for item in source_items
        ]
        assert [item.slot for item in cloned_items] == [item.slot for item in source_items]
        assert [item.parameters for item in cloned_items] == [
            item.parameters for item in source_items
        ]
        assert [item.reason for item in cloned_items] == [item.reason for item in source_items]
    finally:
        await seeded.engine.dispose()


@pytest.mark.asyncio
async def test_restore_replay_fingerprints_expected_active_version(tmp_path) -> None:
    seeded = await _seed(tmp_path)
    try:
        async with seeded.factory() as database, database.begin():
            project = await database.scalar(
                select(Project)
                .where(Project.id == seeded.project_id)
                .with_for_update()
            )
            first = await ProjectVersionService(database).restore(
                project,
                target_version_id=seeded.source_version_id,
                expected_active_version_id=seeded.source_version_id,
                idempotency_key="restore-fingerprint",
            )

        async with seeded.factory() as database, database.begin():
            project = await database.scalar(
                select(Project)
                .where(Project.id == seeded.project_id)
                .with_for_update()
            )
            replay = await ProjectVersionService(database).restore(
                project,
                target_version_id=seeded.source_version_id,
                expected_active_version_id=seeded.source_version_id,
                idempotency_key="restore-fingerprint",
            )
            assert replay.version.id == first.version.id

        async with seeded.factory() as database, database.begin():
            project = await database.scalar(
                select(Project)
                .where(Project.id == seeded.project_id)
                .with_for_update()
            )
            with pytest.raises(ProjectVersionConflict):
                await ProjectVersionService(database).restore(
                    project,
                    target_version_id=seeded.source_version_id,
                    expected_active_version_id=uuid4(),
                    idempotency_key="restore-fingerprint",
                )
    finally:
        await seeded.engine.dispose()
