from __future__ import annotations

from datetime import datetime
from typing import Any

from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    Project,
    ProjectVersion,
)
from builder_lab.generation_events import project_public_generation_event


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def serialize_event(event: GenerationEvent) -> dict[str, Any]:
    payload = (
        event.public_payload
        if event.registry_version == 1 and isinstance(event.public_payload, dict)
        else {}
        if event.registry_version == 1
        else event.payload
    )
    projected = project_public_generation_event(
        event_type=event.event_type,
        public_message=event.public_message,
        payload=payload,
    )
    return {
        "sequence": event.sequence,
        "type": projected.event_type,
        "message": projected.message,
        "payload": projected.payload,
        "created_at": _timestamp(event.created_at),
    }


def serialize_artifact(
    artifact: GenerationArtifact | dict[str, Any] | None,
    *,
    source: str,
) -> dict[str, Any] | None:
    if artifact is None:
        return None
    if isinstance(artifact, dict):
        payload = dict(artifact)
    else:
        configured = artifact.config.get("artifact") if artifact.config else None
        payload = dict(configured) if isinstance(configured, dict) else {
            "revision": artifact.revision,
            "stage": artifact.stage,
            "body_html": artifact.html,
            "css": artifact.css,
            "javascript": artifact.javascript,
        }
        payload["id"] = str(artifact.id)
        payload["quality_status"] = artifact.quality_status
    payload["source"] = source
    return payload


def serialize_run(
    run: GenerationRun,
    *,
    events: list[GenerationEvent] | None = None,
    preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "mode": run.mode,
        "status": run.state,
        "state": run.state,
        "progress": run.progress,
        "current_stage": run.current_stage,
        "last_completed_stage": run.last_completed_stage,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "created_at": _timestamp(run.created_at),
        "started_at": _timestamp(run.started_at),
        "finished_at": _timestamp(run.finished_at),
        "latest_sequence": max(0, run.next_event_sequence - 1),
        # This is owner-authored input, not the provider prompt. The run routes are
        # owner-scoped and the Studio needs it to restore an in-flight refinement.
        "change_request": run.change_request,
    }
    if events is not None:
        payload["events"] = [serialize_event(event) for event in events]
    if preview is not None:
        payload["preview"] = preview
    return payload


def serialize_project(
    project: Project,
    *,
    active_run: GenerationRun | None = None,
    owner_email: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "tenant_id": project.tenant_id,
        "owner_user_id": project.owner_user_id,
        "owner_email": owner_email,
        "source_url": project.source_url,
        "brief": project.brief,
        "status": project.status,
        "active_revision": project.active_revision,
        "active_version_id": (
            str(project.active_version_id) if project.active_version_id else None
        ),
        "active_run": serialize_run(active_run) if active_run is not None else None,
        "created_at": _timestamp(project.created_at),
        "updated_at": _timestamp(project.updated_at),
    }


def serialize_project_version(
    version: ProjectVersion,
    *,
    artifact_revision: int,
    refinable: bool,
) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "project_id": str(version.project_id),
        "ordinal": version.ordinal,
        "kind": version.kind,
        "change_request": version.change_request,
        "parent_version_id": (
            str(version.parent_version_id) if version.parent_version_id else None
        ),
        "run_id": str(version.run_id),
        "artifact_id": str(version.artifact_id),
        "artifact_revision": artifact_revision,
        "refinable": refinable,
        "created_at": _timestamp(version.created_at),
    }


__all__ = [
    "serialize_artifact",
    "serialize_event",
    "serialize_project",
    "serialize_project_version",
    "serialize_run",
]
