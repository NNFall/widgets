from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.patterns.candidate_repository import (
    PatternCandidateRepository,
    PersistedPatternCandidatePlan,
)
from app.patterns.repository import PatternRepository, PersistedComposition
from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    Project,
    ProjectVersion,
)
from builder_lab.generation_events import REGISTRY_VERSION, prepare_generation_event
from builder_lab.models import BuilderRequest, WidgetArtifact
from builder_lab.persona import assistant_persona_from_artifact_config


TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
MAX_BUILDER_BRIEF_CHARS = 12_000
USER_WISH_HEADING = "\u041f\u041e\u0416\u0415\u041b\u0410\u041d\u0418\u0415 \u041f\u041e\u041b\u042c\u0417\u041e\u0412\u0410\u0422\u0415\u041b\u042f"


class ProjectVersionError(RuntimeError):
    pass


class ProjectVersionNotFound(ProjectVersionError):
    pass


class ProjectVersionConflict(ProjectVersionError):
    pass


class ProjectVersionNotRefinable(ProjectVersionError):
    pass


class ProjectBusy(ProjectVersionError):
    pass


@dataclass(frozen=True, slots=True)
class ProjectVersionReadiness:
    artifact: GenerationArtifact
    parsed_artifact: WidgetArtifact
    request: BuilderRequest
    composition: PersistedComposition | PersistedPatternCandidatePlan


@dataclass(frozen=True, slots=True)
class RefinementSource:
    existing_run: GenerationRun | None
    source_version: ProjectVersion | None
    readiness: ProjectVersionReadiness | None


@dataclass(frozen=True, slots=True)
class RestoredVersion:
    version: ProjectVersion
    artifact: GenerationArtifact
    created: bool


@dataclass(frozen=True, slots=True)
class MaterializedProjectVersion:
    version: ProjectVersion
    created: bool
    activated: bool


def _normalize_change_request(value: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 2_000 or "\x00" in normalized:
        raise ProjectVersionNotRefinable("refinement change request is invalid")
    return normalized


def _refinement_request(source: BuilderRequest, change_request: str) -> BuilderRequest:
    suffix = f"\n\n{USER_WISH_HEADING}:\n{change_request}"
    available = MAX_BUILDER_BRIEF_CHARS - len(suffix)
    if available < 0:  # pragma: no cover - bounded request invariant
        raise ProjectVersionNotRefinable("refinement change request is too large")
    base = source.brief[:available].rstrip()
    brief = f"{base}{suffix}" if base else suffix.lstrip()
    return replace(source, brief=brief)


def _restore_key_prefix(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    return f"restore-v1:{digest}:"


class ProjectVersionService:
    """Version mutation rules executed inside a caller-owned transaction."""

    def __init__(self, database: AsyncSession) -> None:
        self._database = database

    async def version_readiness(
        self,
        version: ProjectVersion,
        *,
        artifact: GenerationArtifact | None = None,
    ) -> ProjectVersionReadiness | None:
        """Return all durable inputs required to refine or restore a version."""

        if artifact is None:
            artifact = await self._database.scalar(
                select(GenerationArtifact).where(
                    GenerationArtifact.id == version.artifact_id,
                    GenerationArtifact.run_id == version.run_id,
                )
            )
        if artifact is None or artifact.quality_status not in {
            "accepted",
            "verified",
        }:
            return None

        artifact_payload = (
            artifact.config.get("artifact")
            if isinstance(artifact.config, dict)
            else None
        )
        if not isinstance(artifact_payload, dict):
            return None
        try:
            parsed_artifact = WidgetArtifact.from_dict(artifact_payload)
        except (KeyError, TypeError, ValueError):
            return None

        created = await self._database.scalar(
            select(GenerationEvent)
            .where(
                GenerationEvent.run_id == version.run_id,
                GenerationEvent.event_type == "run.created",
            )
            .order_by(GenerationEvent.sequence)
            .limit(1)
        )
        request_payload = (
            created.payload.get("request")
            if created is not None and isinstance(created.payload, dict)
            else None
        )
        if not isinstance(request_payload, dict):
            return None
        try:
            request = BuilderRequest.from_dict(request_payload)
            artifact_persona = assistant_persona_from_artifact_config(
                artifact.config
            )
            if artifact_persona is not None:
                request = replace(request, assistant_persona=artifact_persona)
            composition = await PatternCandidateRepository(self._database).load_plan(
                version.run_id
            )
            if composition is None:
                composition = await PatternRepository(self._database).load_plan(
                    version.run_id
                )
        except (KeyError, TypeError, ValueError):
            return None
        if composition is None:
            return None
        return ProjectVersionReadiness(
            artifact=artifact,
            parsed_artifact=parsed_artifact,
            request=request,
            composition=composition,
        )

    async def _require_version_readiness(
        self,
        version: ProjectVersion,
        *,
        artifact: GenerationArtifact | None = None,
    ) -> ProjectVersionReadiness:
        readiness = await self.version_readiness(version, artifact=artifact)
        if readiness is None:
            raise ProjectVersionNotRefinable(
                "project version durable inputs are unavailable"
            )
        return readiness

    async def prepare_refinement(
        self,
        project: Project,
        *,
        source_version_id: UUID,
        expected_active_version_id: UUID,
        change_request: str,
        idempotency_key: str,
    ) -> RefinementSource:
        normalized_change = _normalize_change_request(change_request)
        existing = await self._database.scalar(
            select(GenerationRun).where(
                GenerationRun.project_id == project.id,
                GenerationRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.source_version_id != source_version_id
                or source_version_id != expected_active_version_id
                or existing.change_request != normalized_change
            ):
                raise ProjectVersionConflict("refinement idempotency key was reused")
            return RefinementSource(
                existing_run=existing,
                source_version=None,
                readiness=None,
            )

        if (
            source_version_id != expected_active_version_id
            or project.active_version_id != expected_active_version_id
        ):
            raise ProjectVersionConflict("active project version changed")

        active_run = (
            await self._database.get(GenerationRun, project.active_run_id)
            if project.active_run_id is not None
            else None
        )
        if active_run is not None and active_run.state not in TERMINAL_STATES:
            raise ProjectBusy("project already has an active run")

        row = (
            await self._database.execute(
                select(ProjectVersion, GenerationArtifact)
                .join(
                    GenerationArtifact,
                    (GenerationArtifact.id == ProjectVersion.artifact_id)
                    & (GenerationArtifact.run_id == ProjectVersion.run_id),
                )
                .where(
                    ProjectVersion.id == source_version_id,
                    ProjectVersion.project_id == project.id,
                )
            )
        ).one_or_none()
        if row is None:
            raise ProjectVersionNotFound("project version was not found")
        source, artifact = row
        readiness = await self._require_version_readiness(source, artifact=artifact)
        return RefinementSource(
            existing_run=None,
            source_version=source,
            readiness=readiness,
        )

    async def enqueue_refinement(
        self,
        project_id: UUID,
        *,
        source_version_id: UUID,
        expected_active_version_id: UUID,
        change_request: str,
        idempotency_key: str,
        actor_user_id: int,
        tenant_id: int,
    ) -> GenerationRun:
        normalized_change = _normalize_change_request(change_request)
        project = await self._database.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.owner_user_id == actor_user_id,
                Project.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if project is None:
            raise ProjectVersionNotFound("project was not found")
        prepared = await self.prepare_refinement(
            project,
            source_version_id=source_version_id,
            expected_active_version_id=expected_active_version_id,
            change_request=normalized_change,
            idempotency_key=idempotency_key,
        )
        if prepared.existing_run is not None:
            return prepared.existing_run
        source = prepared.source_version
        readiness = prepared.readiness
        if source is None or readiness is None:  # pragma: no cover - service invariant
            raise RuntimeError("refinement source is missing")
        try:
            request = _refinement_request(readiness.request, normalized_change)
        except (TypeError, ValueError) as error:
            raise ProjectVersionNotRefinable(
                "project version builder request is invalid"
            ) from error

        run = GenerationRun(
            project_id=project.id,
            journey_id=project.journey_id,
            mode="express",
            state="queued",
            progress=85,
            last_completed_stage="conversation",
            next_event_sequence=2,
            idempotency_key=idempotency_key,
            source_version_id=source.id,
            change_request=normalized_change,
        )
        self._database.add(run)
        await self._database.flush()
        prepared_event = prepare_generation_event(
            event_type="run.created",
            public_message="Доработка поставлена в очередь",
            operational_payload={
                "status": "queued",
                "request": request.to_dict(),
            },
        )
        event_values = (
            {"id": secrets.randbits(62)}
            if self._database.get_bind().dialect.name == "sqlite"
            else {}
        )
        self._database.add(
            GenerationEvent(
                **event_values,
                run_id=run.id,
                sequence=1,
                event_type=prepared_event.event_type.value,
                public_message=prepared_event.public_message,
                payload=prepared_event.operational_payload,
                registry_version=REGISTRY_VERSION,
                public_payload=prepared_event.public_payload,
                forensic_ref=None,
            )
        )
        try:
            if isinstance(readiness.composition, PersistedPatternCandidatePlan):
                await PatternCandidateRepository(self._database).clone_plan(
                    source_run_id=source.run_id,
                    target_run_id=run.id,
                )
            else:
                await PatternRepository(self._database).clone_plan(
                    source_run_id=source.run_id,
                    target_run_id=run.id,
                )
        except ValueError as error:
            raise ProjectVersionNotRefinable(
                "project version has no persisted pattern plan"
            ) from error
        project.active_run_id = run.id
        project.status = "queued"
        return run

    async def restore(
        self,
        project: Project,
        *,
        target_version_id: UUID,
        expected_active_version_id: UUID,
        idempotency_key: str,
    ) -> RestoredVersion:
        key_prefix = _restore_key_prefix(idempotency_key)
        stored_key = f"{key_prefix}{expected_active_version_id.hex}"
        replay = await self._database.scalar(
            select(ProjectVersion).where(
                ProjectVersion.project_id == project.id,
                ProjectVersion.idempotency_key.like(f"{key_prefix}%"),
            )
        )
        if replay is not None:
            if (
                replay.kind != "restore"
                or replay.parent_version_id != target_version_id
                or replay.idempotency_key != stored_key
            ):
                raise ProjectVersionConflict("restore idempotency key was reused")
            replay_artifact = await self._artifact_for_version(
                replay, publishable=False
            )
            return RestoredVersion(
                version=replay,
                artifact=replay_artifact,
                created=False,
            )

        if project.active_version_id != expected_active_version_id:
            raise ProjectVersionConflict("active project version changed")

        active_run = (
            await self._database.get(GenerationRun, project.active_run_id)
            if project.active_run_id is not None
            else None
        )
        if active_run is not None and active_run.state not in TERMINAL_STATES:
            raise ProjectBusy("project already has an active run")

        target = await self._database.scalar(
            select(ProjectVersion).where(
                ProjectVersion.id == target_version_id,
                ProjectVersion.project_id == project.id,
            )
        )
        if target is None:
            raise ProjectVersionNotFound("project version was not found")
        # Restore only needs the immutable preview payload. Legacy versions may
        # predate durable builder requests or composition plans and remain
        # restorable even though they cannot be refined.
        artifact = await self._artifact_for_version(target, publishable=True)
        last_ordinal = await self._database.scalar(
            select(func.max(ProjectVersion.ordinal)).where(
                ProjectVersion.project_id == project.id
            )
        )
        restored = ProjectVersion(
            project_id=project.id,
            ordinal=int(last_ordinal or 0) + 1,
            run_id=target.run_id,
            artifact_id=target.artifact_id,
            parent_version_id=target.id,
            kind="restore",
            idempotency_key=stored_key,
        )
        self._database.add(restored)
        await self._database.flush()
        project.active_version_id = restored.id
        project.active_run_id = target.run_id
        project.active_revision = artifact.revision
        project.status = "free_result_ready"
        return RestoredVersion(version=restored, artifact=artifact, created=True)

    async def _artifact_for_version(
        self,
        version: ProjectVersion,
        *,
        publishable: bool,
    ) -> GenerationArtifact:
        statement = select(GenerationArtifact).where(
            GenerationArtifact.id == version.artifact_id,
            GenerationArtifact.run_id == version.run_id,
        )
        if publishable:
            statement = statement.where(
                GenerationArtifact.quality_status.in_(("accepted", "verified"))
            )
        artifact = await self._database.scalar(statement)
        if artifact is None:
            raise ProjectVersionNotRefinable("project version artifact is unavailable")
        return artifact


class ProjectVersionRepository:
    """Atomically materialize terminal run history and guarded activation."""

    def __init__(self, database: AsyncSession) -> None:
        self._database = database

    async def materialize_completed_run(
        self,
        *,
        run: GenerationRun,
        artifact: GenerationArtifact,
        now: datetime,
    ) -> MaterializedProjectVersion:
        del now  # The database owns the immutable created_at timestamp.
        if artifact.run_id != run.id or artifact.quality_status not in {
            "accepted",
            "verified",
        }:
            raise RuntimeError("completed run artifact is not publishable")
        project = await self._database.scalar(
            select(Project).where(Project.id == run.project_id).with_for_update()
        )
        if project is None:
            raise RuntimeError("completed run project is missing")
        existing = await self._database.scalar(
            select(ProjectVersion).where(
                ProjectVersion.project_id == run.project_id,
                ProjectVersion.run_id == run.id,
                ProjectVersion.artifact_id == artifact.id,
            )
        )
        if existing is not None:
            return MaterializedProjectVersion(
                version=existing,
                created=False,
                activated=(
                    project.active_version_id == existing.id
                    and project.active_run_id == run.id
                ),
            )

        last_ordinal = await self._database.scalar(
            select(func.max(ProjectVersion.ordinal)).where(
                ProjectVersion.project_id == run.project_id
            )
        )
        if run.source_version_id is None:
            if last_ordinal is not None:
                raise RuntimeError(
                    "a new project version requires an explicit source version"
                )
            kind = "initial"
            parent_version_id = None
            change_request = None
        else:
            parent = await self._database.scalar(
                select(ProjectVersion.id).where(
                    ProjectVersion.id == run.source_version_id,
                    ProjectVersion.project_id == run.project_id,
                )
            )
            if parent is None:
                raise RuntimeError("refinement source version is invalid")
            kind = "refinement"
            parent_version_id = parent
            change_request = _normalize_change_request(run.change_request or "")
        version = ProjectVersion(
            project_id=run.project_id,
            ordinal=int(last_ordinal or 0) + 1,
            run_id=run.id,
            artifact_id=artifact.id,
            parent_version_id=parent_version_id,
            kind=kind,
            change_request=change_request,
        )
        self._database.add(version)
        await self._database.flush()

        activation_guard = [
            Project.id == run.project_id,
            Project.active_run_id == run.id,
        ]
        if run.source_version_id is None:
            activation_guard.append(Project.active_version_id.is_(None))
        else:
            activation_guard.append(
                Project.active_version_id == run.source_version_id
            )
        activation = await self._database.execute(
            update(Project)
            .where(*activation_guard)
            .values(
                active_version_id=version.id,
                active_run_id=run.id,
                active_revision=artifact.revision,
                status="free_result_ready",
            )
        )
        return MaterializedProjectVersion(
            version=version,
            created=True,
            activated=activation.rowcount == 1,
        )


__all__ = [
    "MaterializedProjectVersion",
    "ProjectBusy",
    "ProjectVersionConflict",
    "ProjectVersionNotFound",
    "ProjectVersionNotRefinable",
    "ProjectVersionReadiness",
    "ProjectVersionRepository",
    "ProjectVersionService",
    "RefinementSource",
    "RestoredVersion",
]
