from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Tenant, User
from app.patterns.repository import PatternRepository
from app.projects.versions import ProjectVersionConflict, ProjectVersionService
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    CompositionPlanItem,
    CompositionPlanRecord,
    PaymentAttempt,
    Project,
    ProjectVersion,
    Subscription,
    UsageLedger,
)
from builder_lab.models import BuilderRequest, EngineName
from builder_lab.patterns.registry import load_builtin_registry
from tests.builder_lab_cases.test_validation import artifact
from tests.saas_cases.test_project_routes import _project_app
from tests.saas_cases.test_project_versions import _plan


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


async def _version_app(tmp_path, *, enabled: bool = True):
    def configure(app, _factory) -> None:
        app["config"] = SimpleNamespace(project_versions_enabled=enabled)

    return await _project_app(tmp_path, configure_app=configure)


async def _seed_versions(factory, project_id, *, credit_tokens: int = 1_000_000):
    async with factory() as database, database.begin():
        now = datetime.now(UTC)
        project = await database.get(Project, project_id)
        run = GenerationRun(
            project_id=project.id,
            journey_id=project.journey_id,
            mode="express",
            state="completed",
            progress=100,
            idempotency_key="initial-version-run",
            finished_at=datetime.now(UTC),
        )
        database.add(run)
        await database.flush()
        database.add(
            GenerationEvent(
                id=abs(hash((str(run.id), "created"))),
                run_id=run.id,
                sequence=1,
                event_type="run.created",
                public_message="Source run created",
                payload={
                    "request": BuilderRequest(
                        engine=EngineName.DIRECT,
                        brief=project.brief or "Durable project request",
                        source_url=project.source_url,
                    ).to_dict()
                },
            )
        )
        candidate = artifact(revision=5)
        stored = GenerationArtifact(
            run_id=run.id,
            revision=5,
            stage=candidate.stage.value,
            html=candidate.body_html,
            css=candidate.css,
            javascript=candidate.javascript,
            config={"artifact": candidate.to_dict()},
            quality_status="verified",
        )
        database.add(stored)
        await database.flush()
        version = ProjectVersion(
            project_id=project.id,
            ordinal=1,
            run_id=run.id,
            artifact_id=stored.id,
            kind="initial",
        )
        database.add(version)
        await database.flush()
        project.active_run_id = run.id
        project.active_revision = stored.revision
        project.active_version_id = version.id
        project.status = "free_result_ready"
        await PatternRepository(database).create_plan(
            run_id=run.id,
            plan=_plan(),
            registry=load_builtin_registry(),
        )
        payment_attempt = PaymentAttempt(
            user_id=10,
            project_id=project.id,
            provider="test",
            merchant_account_fingerprint="a" * 64,
            idempotency_key=f"test-payment:{project.id}",
            plan_code="starter_monthly",
            plan_snapshot={},
            plan_fingerprint="test",
            amount_minor=100,
            currency="RUB",
            status="succeeded",
            payload={},
        )
        database.add(payment_attempt)
        await database.flush()
        subscription = Subscription(
            user_id=10,
            provider="test",
            payment_attempt_id=payment_attempt.id,
            plan_code="starter_monthly",
            plan_snapshot={},
            plan_fingerprint="test",
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        database.add(subscription)
        database.add(
            UsageLedger(
                user_id=10,
                project_id=project.id,
                payment_attempt_id=payment_attempt.id,
                bucket="generation_tokens",
                entry_type="subscription.credit",
                amount=credit_tokens,
                idempotency_key=f"test-credit:{project.id}",
                payload={},
            )
        )
        return version.id, run.id, stored.id


async def _append_version(factory, project_id, *, ordinal: int = 2):
    async with factory() as database, database.begin():
        project = await database.get(Project, project_id)
        run = GenerationRun(
            project_id=project.id,
            journey_id=project.journey_id,
            mode="express",
            state="completed",
            progress=100,
            idempotency_key=f"version-{ordinal}-run",
            finished_at=datetime.now(UTC),
        )
        database.add(run)
        await database.flush()
        candidate = artifact(revision=ordinal + 4)
        stored = GenerationArtifact(
            run_id=run.id,
            revision=ordinal + 4,
            stage=candidate.stage.value,
            html=candidate.body_html,
            css=candidate.css,
            javascript=candidate.javascript,
            config={"artifact": candidate.to_dict()},
            quality_status="verified",
        )
        database.add(stored)
        await database.flush()
        version = ProjectVersion(
            project_id=project.id,
            ordinal=ordinal,
            run_id=run.id,
            artifact_id=stored.id,
            parent_version_id=project.active_version_id,
            kind="refinement",
            change_request=f"Версия {ordinal}",
        )
        database.add(version)
        await database.flush()
        project.active_run_id = run.id
        project.active_revision = stored.revision
        project.active_version_id = version.id
        return version.id, run.id, stored.id


async def _break_version_readiness(
    factory,
    *,
    run_id: UUID,
    artifact_id: UUID,
    prerequisite: str,
) -> None:
    async with factory() as database, database.begin():
        if prerequisite == "missing_plan":
            plan = await database.scalar(
                select(CompositionPlanRecord).where(
                    CompositionPlanRecord.run_id == run_id
                )
            )
            assert plan is not None
            await database.execute(
                delete(CompositionPlanItem).where(
                    CompositionPlanItem.composition_plan_id == plan.id
                )
            )
            await database.delete(plan)
            return
        if prerequisite == "missing_request":
            await database.execute(
                delete(GenerationEvent).where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.event_type == "run.created",
                )
            )
            return
        if prerequisite == "corrupt_artifact":
            stored = await database.get(GenerationArtifact, artifact_id)
            assert stored is not None
            stored.config = {"artifact": {"revision": "not-an-integer"}}
            return
        raise AssertionError(f"unknown prerequisite: {prerequisite}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured_enabled",
    [None, False],
    ids=("missing_config", "explicitly_disabled"),
)
async def test_version_routes_are_owner_safe_hidden_when_feature_is_disabled(
    tmp_path,
    configured_enabled: bool | None,
) -> None:
    def configure(app, _factory) -> None:
        app["config"] = SimpleNamespace(
            project_versions_enabled=configured_enabled
        )

    engine, factory, client, project_id, foreign_id = await _project_app(
        tmp_path,
        configure_app=configure if configured_enabled is not None else None,
    )
    try:
        version_id, _run_id, _artifact_id = await _seed_versions(factory, project_id)
        expected = {"error": {"code": "not_found"}}

        unauthenticated = await client.get(f"/api/projects/{project_id}/versions")
        await client.post("/test/login/10")
        responses = (
            unauthenticated,
            await client.get(f"/api/projects/{project_id}/versions"),
            await client.get(f"/api/projects/{foreign_id}/versions"),
            await client.post(
                f"/api/projects/{project_id}/versions/{version_id}/refine",
                json={
                    "change_request": "This must stay disabled",
                    "expected_active_version_id": str(version_id),
                },
                headers={
                    "X-CSRF-Token": "test-csrf",
                    "Idempotency-Key": "disabled-refinement",
                },
            ),
            await client.post(
                f"/api/projects/{project_id}/versions/{version_id}/restore",
                json={"expected_active_version_id": str(version_id)},
                headers={
                    "X-CSRF-Token": "test-csrf",
                    "Idempotency-Key": "disabled-restore",
                },
            ),
        )

        for response in responses:
            assert response.status == 404
            assert await response.json() == expected
        async with factory() as database:
            assert await database.scalar(select(func.count()).select_from(GenerationRun)) == 1
            assert await database.scalar(select(func.count()).select_from(ProjectVersion)) == 1
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_version_mutations_require_authentication_csrf_and_project_ownership(
    tmp_path,
) -> None:
    engine, factory, client, project_id, foreign_id = await _version_app(tmp_path)
    try:
        version_id, _run_id, _artifact_id = await _seed_versions(factory, project_id)
        refine_url = f"/api/projects/{project_id}/versions/{version_id}/refine"
        body = {
            "change_request": "Keep the contract protected",
            "expected_active_version_id": str(version_id),
        }

        unauthenticated = await client.post(
            refine_url,
            json=body,
            headers={"Idempotency-Key": "unauthenticated-refinement"},
        )
        assert unauthenticated.status == 401
        assert (await unauthenticated.json())["error"]["code"] == "authentication_required"

        await client.post("/test/login/10")
        missing_csrf = await client.post(
            refine_url,
            json=body,
            headers={"Idempotency-Key": "missing-csrf-refinement"},
        )
        foreign_project = await client.post(
            f"/api/projects/{foreign_id}/versions/{version_id}/restore",
            json={"expected_active_version_id": str(version_id)},
            headers={
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": "foreign-project-restore",
            },
        )

        assert missing_csrf.status == 403
        assert (await missing_csrf.json())["error"]["code"] == "csrf_failed"
        assert foreign_project.status == 404
        assert (await foreign_project.json())["error"]["code"] == "not_found"
        async with factory() as database:
            assert await database.scalar(select(func.count()).select_from(GenerationRun)) == 1
            assert await database.scalar(select(func.count()).select_from(ProjectVersion)) == 1
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_owner_lists_versions_and_starts_idempotent_paid_refinement(tmp_path) -> None:
    engine, factory, client, project_id, foreign_id = await _version_app(tmp_path)
    try:
        version_id, _run_id, _artifact_id = await _seed_versions(factory, project_id)
        await client.post("/test/login/10")

        listing = await client.get(f"/api/projects/{project_id}/versions")
        foreign = await client.get(f"/api/projects/{foreign_id}/versions")
        headers = {
            "X-CSRF-Token": "test-csrf",
            "Idempotency-Key": "refine-version-key",
        }
        first = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/refine",
            json={
                "change_request": "Сделай приветствие короче",
                "expected_active_version_id": str(version_id),
            },
            headers=headers,
        )
        async with factory() as database, database.begin():
            subscription = await database.scalar(select(Subscription))
            assert subscription is not None
            subscription.status = "inactive"
        replay = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/refine",
            json={
                "change_request": "Сделай приветствие короче",
                "expected_active_version_id": str(version_id),
            },
            headers=headers,
        )
        conflict = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/refine",
            json={
                "change_request": "Измени другой блок",
                "expected_active_version_id": str(version_id),
            },
            headers=headers,
        )

        assert listing.status == 200
        assert foreign.status == 404
        assert await listing.json() == {
            "active_version_id": str(version_id),
            "versions": [
                {
                    "id": str(version_id),
                    "project_id": str(project_id),
                    "ordinal": 1,
                    "kind": "initial",
                    "change_request": None,
                    "parent_version_id": None,
                    "run_id": str(_run_id),
                    "artifact_id": str(_artifact_id),
                    "artifact_revision": 5,
                    "refinable": True,
                    "created_at": ANY,
                }
            ],
        }
        assert first.status == replay.status == 202
        assert conflict.status == 409
        first_payload = await first.json()
        assert first_payload["change_request"] == "Сделай приветствие короче"
        assert (await replay.json())["id"] == first_payload["id"]

        async with factory() as database:
            run = await database.get(GenerationRun, UUID(first_payload["id"]))
            trial_reservations = await database.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.entry_type == "trial.reserve")
            )
            generation_reservations = list(
                (
                    await database.execute(
                        select(UsageLedger).where(
                            UsageLedger.entry_type == "generation.reserve"
                        )
                    )
                ).scalars()
            )
        assert run.source_version_id == version_id
        assert run.change_request == "Сделай приветствие короче"
        assert trial_reservations == 0
        assert [(entry.bucket, entry.amount) for entry in generation_reservations] == [
            ("generation_tokens", -500_000)
        ]

        old_route = await client.post(
            f"/api/projects/{project_id}/refinements",
            json={"change_request": "Старый путь не должен мутировать"},
            headers={
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": "old-refinement-path",
            },
        )
        assert old_route.status == 404
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_prerequisite",
    ("missing_plan", "missing_request", "corrupt_artifact"),
)
async def test_unready_version_is_not_refinable_or_charged_but_can_restore_exact_preview(
    tmp_path,
    missing_prerequisite: str,
) -> None:
    engine, factory, client, project_id, _foreign_id = await _version_app(tmp_path)
    try:
        version_id, run_id, artifact_id = await _seed_versions(factory, project_id)
        await _break_version_readiness(
            factory,
            run_id=run_id,
            artifact_id=artifact_id,
            prerequisite=missing_prerequisite,
        )
        await client.post("/test/login/10")

        listing = await client.get(f"/api/projects/{project_id}/versions")
        refinement = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/refine",
            json={
                "change_request": "Не списывать токены для сломанной версии",
                "expected_active_version_id": str(version_id),
            },
            headers={
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": f"unready-refine-{missing_prerequisite}",
            },
        )
        restoration = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/restore",
            json={"expected_active_version_id": str(version_id)},
            headers={
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": f"unready-restore-{missing_prerequisite}",
            },
        )

        assert listing.status == 200
        assert (await listing.json())["versions"][0]["refinable"] is False
        assert refinement.status == 409
        assert (await refinement.json())["error"]["code"] == "version_unavailable"
        assert restoration.status == 201
        restoration_payload = await restoration.json()
        assert restoration_payload["version"]["refinable"] is False
        restored_version_id = UUID(restoration_payload["version"]["id"])

        async with factory() as database:
            run_count = await database.scalar(
                select(func.count()).select_from(GenerationRun)
            )
            version_count = await database.scalar(
                select(func.count()).select_from(ProjectVersion)
            )
            reservations = await database.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.entry_type == "generation.reserve")
            )
            project = await database.get(Project, project_id)
        assert run_count == 1
        assert version_count == 2
        assert reservations == 0
        assert project is not None
        assert project.active_run_id == run_id
        assert project.active_version_id == restored_version_id
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_restore_rejects_non_publishable_version_artifact(tmp_path) -> None:
    engine, factory, client, project_id, _foreign_id = await _version_app(tmp_path)
    try:
        version_id, _run_id, artifact_id = await _seed_versions(factory, project_id)
        async with factory() as database, database.begin():
            stored = await database.get(GenerationArtifact, artifact_id)
            assert stored is not None
            stored.quality_status = "draft"
        await client.post("/test/login/10")

        response = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/restore",
            json={"expected_active_version_id": str(version_id)},
            headers={
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": "restore-non-publishable",
            },
        )

        assert response.status == 409
        assert (await response.json())["error"]["code"] == "version_unavailable"
        async with factory() as database:
            version_count = await database.scalar(
                select(func.count()).select_from(ProjectVersion)
            )
            project = await database.get(Project, project_id)
        assert version_count == 1
        assert project is not None
        assert project.active_version_id == version_id
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_paid_refinement_rejects_insufficient_generation_credits(tmp_path) -> None:
    engine, factory, client, project_id, _foreign_id = await _version_app(tmp_path)
    try:
        version_id, _run_id, _artifact_id = await _seed_versions(
            factory, project_id, credit_tokens=100_000
        )
        await client.post("/test/login/10")

        response = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/refine",
            json={
                "change_request": "Сделай карточку компактнее",
                "expected_active_version_id": str(version_id),
            },
            headers={
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": "insufficient-credit-refinement",
            },
        )

        assert response.status == 409
        assert (await response.json())["error"]["code"] == (
            "generation_credits_unavailable"
        )
        async with factory() as database:
            reservations = await database.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.entry_type == "generation.reserve")
            )
        assert reservations == 0
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_paid_refinement_retries_with_a_new_period_bound_reservation(
    tmp_path,
) -> None:
    engine, factory, client, project_id, _foreign_id = await _version_app(tmp_path)
    try:
        version_id, _run_id, _artifact_id = await _seed_versions(factory, project_id)
        await client.post("/test/login/10")
        first = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/refine",
            json={
                "change_request": "Сделай карточку компактнее",
                "expected_active_version_id": str(version_id),
            },
            headers={
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": "paid-refinement-first",
            },
        )
        assert first.status == 202
        source_id = UUID((await first.json())["id"])
        async with factory() as database, database.begin():
            source = await database.get(GenerationRun, source_id)
            source.state = "failed"

        retry_headers = {
            "X-CSRF-Token": "test-csrf",
            "Idempotency-Key": "paid-refinement-retry",
        }
        retried = await client.post(
            f"/api/runs/{source_id}/retry",
            headers=retry_headers,
        )
        replay = await client.post(
            f"/api/runs/{source_id}/retry",
            headers=retry_headers,
        )

        assert retried.status == replay.status == 202
        replacement_id = UUID((await retried.json())["id"])
        assert UUID((await replay.json())["id"]) == replacement_id
        async with factory() as database:
            source = await database.get(GenerationRun, source_id)
            replacement = await database.get(GenerationRun, replacement_id)
            reservations = list(
                (
                    await database.execute(
                        select(UsageLedger)
                        .where(UsageLedger.entry_type == "generation.reserve")
                        .order_by(UsageLedger.created_at, UsageLedger.id)
                    )
                ).scalars()
            )
        assert source.trial_settlement == "paid"
        assert replacement.source_version_id == version_id
        assert replacement.change_request == "Сделай карточку компактнее"
        assert len(reservations) == 2
        assert reservations[0].payment_attempt_id is not None
        assert {
            reservation.payment_attempt_id for reservation in reservations
        } == {reservations[0].payment_attempt_id}
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_restore_creates_auditable_version_and_repoints_active_preview(tmp_path) -> None:
    engine, factory, client, project_id, _foreign_id = await _version_app(tmp_path)
    try:
        first_version_id, first_run_id, first_artifact_id = await _seed_versions(
            factory, project_id
        )
        second_version_id, _second_run_id, _second_artifact_id = await _append_version(
            factory, project_id
        )

        await client.post("/test/login/10")
        headers = {
            "X-CSRF-Token": "test-csrf",
            "Idempotency-Key": "restore-first-version",
        }
        first = await client.post(
            f"/api/projects/{project_id}/versions/{first_version_id}/restore",
            json={"expected_active_version_id": str(second_version_id)},
            headers=headers,
        )
        replay = await client.post(
            f"/api/projects/{project_id}/versions/{first_version_id}/restore",
            json={"expected_active_version_id": str(second_version_id)},
            headers=headers,
        )

        assert first.status == 201
        assert replay.status == 200
        assert (await replay.json())["version"]["id"] == (await first.json())["version"]["id"]
        async with factory() as database:
            project = await database.get(Project, project_id)
            restored = await database.get(ProjectVersion, project.active_version_id)
        assert restored.kind == "restore"
        assert restored.parent_version_id == first_version_id
        assert restored.artifact_id == first_artifact_id
        assert project.active_run_id == first_run_id
        assert project.active_revision == 5
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_refinement_rejects_stale_or_mismatched_source_without_mutation(tmp_path) -> None:
    engine, factory, client, project_id, _foreign_id = await _version_app(tmp_path)
    try:
        version_id, _run_id, _artifact_id = await _seed_versions(factory, project_id)
        await client.post("/test/login/10")
        headers = {
            "X-CSRF-Token": "test-csrf",
            "Idempotency-Key": "stale-refinement",
        }
        stale_expected = await client.post(
            f"/api/projects/{project_id}/versions/{version_id}/refine",
            json={
                "change_request": "Не должно запуститься",
                "expected_active_version_id": str(uuid4()),
            },
            headers=headers,
        )
        mismatched_source = await client.post(
            f"/api/projects/{project_id}/versions/{uuid4()}/refine",
            json={
                "change_request": "Тоже не должно запуститься",
                "expected_active_version_id": str(version_id),
            },
            headers={**headers, "Idempotency-Key": "mismatched-source"},
        )

        assert stale_expected.status == mismatched_source.status == 409
        assert (await stale_expected.json())["error"]["code"] == "project_version_conflict"
        assert (await mismatched_source.json())["error"]["code"] == "project_version_conflict"
        async with factory() as database:
            assert await database.scalar(select(func.count()).select_from(GenerationRun)) == 1
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_version_mutations_reject_unknown_missing_and_oversized_json(tmp_path) -> None:
    engine, factory, client, project_id, _foreign_id = await _version_app(tmp_path)
    try:
        version_id, _run_id, _artifact_id = await _seed_versions(factory, project_id)
        await client.post("/test/login/10")
        headers = {
            "X-CSRF-Token": "test-csrf",
            "Idempotency-Key": "invalid-version-body",
            "Content-Type": "application/json",
        }
        refine_url = f"/api/projects/{project_id}/versions/{version_id}/refine"
        restore_url = f"/api/projects/{project_id}/versions/{version_id}/restore"
        responses = [
            await client.post(
                refine_url,
                json={
                    "change_request": "Текст",
                    "expected_active_version_id": str(version_id),
                    "extra": True,
                },
                headers=headers,
            ),
            await client.post(
                restore_url,
                json={},
                headers={**headers, "Idempotency-Key": "missing-restore-body"},
            ),
        ]
        for response in responses:
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "invalid_body"

        oversized = await client.post(
            restore_url,
            data='{"expected_active_version_id":"' + ("x" * 33_000) + '"}',
            headers={**headers, "Idempotency-Key": "oversized-restore-body"},
        )
        assert oversized.status == 413
        assert (await oversized.json())["error"]["code"] == "request_too_large"
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_restore_rejects_stale_active_version_without_mutation(tmp_path) -> None:
    engine, factory, client, project_id, _foreign_id = await _version_app(tmp_path)
    try:
        first_version_id, _run_id, _artifact_id = await _seed_versions(factory, project_id)
        second_version_id, _second_run_id, _second_artifact_id = await _append_version(
            factory, project_id
        )
        await client.post("/test/login/10")

        response = await client.post(
            f"/api/projects/{project_id}/versions/{first_version_id}/restore",
            json={"expected_active_version_id": str(first_version_id)},
            headers={
                "X-CSRF-Token": "test-csrf",
                "Idempotency-Key": "stale-restore",
            },
        )

        assert response.status == 409
        assert (await response.json())["error"]["code"] == "project_version_conflict"
        async with factory() as database:
            project = await database.get(Project, project_id)
            version_count = await database.scalar(
                select(func.count()).select_from(ProjectVersion)
            )
        assert project.active_version_id == second_version_id
        assert version_count == 2
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_restore_compare_and_swap_allows_one_concurrent_winner() -> None:
    engine = create_async_engine(POSTGRES_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)

        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Alpha", slug="alpha"))
            await database.flush()
            database.add(User(id=10, tenant_id=1, email="owner@example.com"))
            await database.flush()
            project = Project(
                tenant_id=1,
                owner_user_id=10,
                source_url="https://example.com/",
                brief="Concurrent restore",
            )
            database.add(project)
            await database.flush()
            project_id = project.id

            versions: list[ProjectVersion] = []
            for ordinal in (1, 2):
                run = GenerationRun(
                    project_id=project.id,
                    mode="express",
                    state="completed",
                    progress=100,
                    idempotency_key=f"postgres-version-{ordinal}",
                    finished_at=datetime.now(UTC),
                )
                database.add(run)
                await database.flush()
                database.add(
                    GenerationEvent(
                        run_id=run.id,
                        sequence=1,
                        event_type="run.created",
                        public_message="Source run created",
                        payload={
                            "request": BuilderRequest(
                                engine=EngineName.DIRECT,
                                brief=f"Concurrent restore version {ordinal}",
                                source_url="https://example.com/",
                            ).to_dict()
                        },
                    )
                )
                candidate = artifact(revision=ordinal)
                stored = GenerationArtifact(
                    run_id=run.id,
                    revision=ordinal,
                    stage=candidate.stage.value,
                    html=candidate.body_html,
                    css=candidate.css,
                    javascript=candidate.javascript,
                    config={"artifact": candidate.to_dict()},
                    quality_status="verified",
                )
                database.add(stored)
                await database.flush()
                version = ProjectVersion(
                    project_id=project.id,
                    ordinal=ordinal,
                    run_id=run.id,
                    artifact_id=stored.id,
                    parent_version_id=versions[-1].id if versions else None,
                    kind="refinement" if versions else "initial",
                    change_request=f"Version {ordinal}" if versions else None,
                )
                database.add(version)
                await database.flush()
                await PatternRepository(database).create_plan(
                    run_id=run.id,
                    plan=_plan(),
                    registry=load_builtin_registry(),
                )
                versions.append(version)

            project.active_run_id = versions[-1].run_id
            project.active_revision = 2
            project.active_version_id = versions[-1].id
            project.status = "free_result_ready"
            target_version_id = versions[0].id
            expected_active_version_id = versions[1].id

        async def restore(idempotency_key: str):
            async with factory() as database, database.begin():
                project = await database.scalar(
                    select(Project)
                    .where(Project.id == project_id)
                    .with_for_update()
                )
                assert project is not None
                return await ProjectVersionService(database).restore(
                    project,
                    target_version_id=target_version_id,
                    expected_active_version_id=expected_active_version_id,
                    idempotency_key=idempotency_key,
                )

        outcomes = await asyncio.gather(
            restore("concurrent-restore-a"),
            restore("concurrent-restore-b"),
            return_exceptions=True,
        )
        winners = [outcome for outcome in outcomes if not isinstance(outcome, Exception)]
        conflicts = [
            outcome
            for outcome in outcomes
            if isinstance(outcome, ProjectVersionConflict)
        ]

        assert len(winners) == 1
        assert len(conflicts) == 1
        async with factory() as database:
            project = await database.get(Project, project_id)
            version_count = await database.scalar(
                select(func.count()).select_from(ProjectVersion)
            )
        assert project is not None
        assert project.active_version_id == winners[0].version.id
        assert version_count == 3
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
