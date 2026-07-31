from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import ANY
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.saas.models import (
    GenerationArtifact,
    GenerationRun,
    PaymentAttempt,
    Project,
    ProjectVersion,
    Subscription,
    UsageLedger,
)
from tests.builder_lab_cases.test_validation import artifact
from tests.saas_cases.test_project_routes import _project_app


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


@pytest.mark.asyncio
async def test_owner_lists_versions_and_starts_idempotent_paid_refinement(tmp_path) -> None:
    engine, factory, client, project_id, foreign_id = await _project_app(tmp_path)
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
            f"/api/projects/{project_id}/refinements",
            json={"change_request": "Сделай приветствие короче"},
            headers=headers,
        )
        replay = await client.post(
            f"/api/projects/{project_id}/refinements",
            json={"change_request": "Сделай приветствие короче"},
            headers=headers,
        )
        conflict = await client.post(
            f"/api/projects/{project_id}/refinements",
            json={"change_request": "Измени другой блок"},
            headers=headers,
        )

        assert listing.status == 200
        assert foreign.status == 404
        assert await listing.json() == {
            "active_version_id": str(version_id),
            "versions": [
                {
                    "id": str(version_id),
                    "ordinal": 1,
                    "kind": "initial",
                    "change_request": None,
                    "parent_version_id": None,
                    "run_id": str(_run_id),
                    "artifact_id": str(_artifact_id),
                    "active": True,
                    "created_at": ANY,
                }
            ],
        }
        assert first.status == replay.status == 202
        assert conflict.status == 409
        first_payload = await first.json()
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
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_paid_refinement_rejects_insufficient_generation_credits(tmp_path) -> None:
    engine, factory, client, project_id, _foreign_id = await _project_app(tmp_path)
    try:
        await _seed_versions(factory, project_id, credit_tokens=100_000)
        await client.post("/test/login/10")

        response = await client.post(
            f"/api/projects/{project_id}/refinements",
            json={"change_request": "Сделай карточку компактнее"},
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
    engine, factory, client, project_id, _foreign_id = await _project_app(tmp_path)
    try:
        version_id, _run_id, _artifact_id = await _seed_versions(factory, project_id)
        await client.post("/test/login/10")
        first = await client.post(
            f"/api/projects/{project_id}/refinements",
            json={"change_request": "Сделай карточку компактнее"},
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
    engine, factory, client, project_id, _foreign_id = await _project_app(tmp_path)
    try:
        first_version_id, first_run_id, first_artifact_id = await _seed_versions(
            factory, project_id
        )
        async with factory() as database, database.begin():
            project = await database.get(Project, project_id)
            second_run = GenerationRun(
                project_id=project.id,
                mode="express",
                state="completed",
                progress=100,
                idempotency_key="second-version-run",
                finished_at=datetime.now(UTC),
            )
            database.add(second_run)
            await database.flush()
            candidate = artifact(revision=6)
            second_artifact = GenerationArtifact(
                run_id=second_run.id,
                revision=6,
                stage=candidate.stage.value,
                html=candidate.body_html,
                css=candidate.css,
                javascript=candidate.javascript,
                config={"artifact": candidate.to_dict()},
                quality_status="verified",
            )
            database.add(second_artifact)
            await database.flush()
            second_version = ProjectVersion(
                project_id=project.id,
                ordinal=2,
                run_id=second_run.id,
                artifact_id=second_artifact.id,
                parent_version_id=first_version_id,
                kind="refinement",
                change_request="Вторая версия",
            )
            database.add(second_version)
            await database.flush()
            project.active_run_id = second_run.id
            project.active_revision = 6
            project.active_version_id = second_version.id

        await client.post("/test/login/10")
        headers = {
            "X-CSRF-Token": "test-csrf",
            "Idempotency-Key": "restore-first-version",
        }
        first = await client.post(
            f"/api/projects/{project_id}/versions/{first_version_id}/restore",
            json={},
            headers=headers,
        )
        replay = await client.post(
            f"/api/projects/{project_id}/versions/{first_version_id}/restore",
            json={},
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
