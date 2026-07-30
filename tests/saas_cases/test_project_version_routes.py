from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import ANY
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.saas.models import (
    GenerationArtifact,
    GenerationRun,
    Project,
    ProjectVersion,
    Subscription,
    UsageLedger,
)
from tests.builder_lab_cases.test_validation import artifact
from tests.saas_cases.test_project_routes import _project_app


async def _seed_versions(factory, project_id):
    async with factory() as database, database.begin():
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
        database.add(
            Subscription(
                user_id=10,
                provider="test",
                plan_code="starter_monthly",
                plan_snapshot={},
                plan_fingerprint="test",
                status="active",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
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
        assert run.source_version_id == version_id
        assert run.change_request == "Сделай приветствие короче"
        assert trial_reservations == 0
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
