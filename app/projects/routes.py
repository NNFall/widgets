from __future__ import annotations

import asyncio
import json
import secrets
from uuid import UUID

from aiohttp import web
from aiohttp_session import get_session
from sqlalchemy import select

from app.billing.service import TrialService, TrialUnavailable, UnverifiedTrialUser
from app.db.session import get_session_factory
from app.projects.serializers import serialize_artifact, serialize_event, serialize_project, serialize_run
from app.saas.models import GenerationArtifact, GenerationEvent, GenerationRun, Project, UserIdentity
from builder_lab.models import BuilderRequest, EngineName, WidgetArtifact
from builder_lab.validation import validate_artifact

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
SSE_PAGE_SIZE = 100


async def _scope(request: web.Request, *, verified: bool = False) -> tuple[int, int]:
    session = await get_session(request)
    user_id, tenant_id = session.get("user_id"), session.get("tenant_id")
    if not isinstance(user_id, int) or not isinstance(tenant_id, int):
        raise web.HTTPUnauthorized(text=_error("authentication_required"), content_type="application/json")
    if verified:
        factory = get_session_factory(request.app)
        async with factory() as database:
            identity = await database.scalar(
                select(UserIdentity.id).where(
                    UserIdentity.user_id == user_id,
                    UserIdentity.email_verified.is_(True),
                )
            )
        if identity is None:
            raise web.HTTPForbidden(text=_error("verified_oauth_required"), content_type="application/json")
    return user_id, tenant_id


async def _require_csrf(request: web.Request) -> None:
    session = await get_session(request)
    expected, supplied = session.get("csrf_token"), request.headers.get("X-CSRF-Token")
    if not isinstance(expected, str) or not supplied or not secrets.compare_digest(expected, supplied):
        raise web.HTTPForbidden(text=_error("csrf_failed"), content_type="application/json")


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise web.HTTPNotFound(text=_error("not_found"), content_type="application/json") from error


async def _owned_project(database, project_id: UUID, user_id: int, tenant_id: int, *, lock: bool = False):
    statement = select(Project).where(
        Project.id == project_id,
        Project.owner_user_id == user_id,
        Project.tenant_id == tenant_id,
    )
    project = await database.scalar(statement.with_for_update() if lock else statement)
    if project is None:
        raise web.HTTPNotFound(text=_error("not_found"), content_type="application/json")
    return project


async def _owned_run(database, run_id: UUID, user_id: int, tenant_id: int):
    row = (
        await database.execute(
            select(GenerationRun, Project)
            .join(Project, GenerationRun.project_id == Project.id)
            .where(
                GenerationRun.id == run_id,
                Project.owner_user_id == user_id,
                Project.tenant_id == tenant_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise web.HTTPNotFound(text=_error("not_found"), content_type="application/json")
    return row


async def list_projects(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        projects = list((await database.execute(
            select(Project)
            .where(Project.owner_user_id == user_id, Project.tenant_id == tenant_id)
            .order_by(Project.created_at.desc(), Project.id)
        )).scalars())
    return web.json_response({"projects": [serialize_project(project) for project in projects]})


async def get_project(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        project = await _owned_project(database, _uuid(request.match_info["project_id"]), user_id, tenant_id)
        active_run = (
            await database.scalar(
                select(GenerationRun).where(
                    GenerationRun.id == project.active_run_id,
                    GenerationRun.project_id == project.id,
                )
            )
            if project.active_run_id
            else None
        )
        payload = serialize_project(project, active_run=active_run)
    return web.json_response(payload)


async def create_run(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request, verified=True)
    await _require_csrf(request)
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 128 or "\x00" in key:
        raise web.HTTPBadRequest(text=_error("idempotency_key_required"), content_type="application/json")
    try:
        body = await request.json()
    except Exception as error:  # noqa: BLE001
        raise web.HTTPBadRequest(text=_error("invalid_json"), content_type="application/json") from error
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text=_error("invalid_body"), content_type="application/json")
    if str(body.get("mode", "express")).strip() != "express":
        raise web.HTTPBadRequest(text=_error("unsupported_mode"), content_type="application/json")
    factory = get_session_factory(request.app)
    try:
        async with factory() as database, database.begin():
            project = await _owned_project(
                database, _uuid(request.match_info["project_id"]), user_id, tenant_id, lock=True
            )
            run = await database.scalar(select(GenerationRun).where(
                GenerationRun.project_id == project.id,
                GenerationRun.idempotency_key == key,
            ))
            if run is None:
                builder_request = BuilderRequest(
                    engine=EngineName.DIRECT,
                    brief=project.brief or "Create a useful website assistant",
                    source_url=project.source_url,
                )
                run = GenerationRun(
                    project_id=project.id,
                    mode="express",
                    state="queued",
                    progress=0,
                    next_event_sequence=2,
                    idempotency_key=key,
                )
                database.add(run)
                await database.flush()
                await TrialService(factory).reserve_trial_in_session(
                    database, user_id, run.id, request_id=key
                )
                event_values = {
                    "id": secrets.randbits(62),
                } if database.get_bind().dialect.name == "sqlite" else {}
                database.add(GenerationEvent(
                    **event_values,
                    run_id=run.id,
                    sequence=1,
                    event_type="run.created",
                    public_message="Generation queued",
                    payload={"status": "queued", "request": builder_request.to_dict()},
                ))
                project.active_run_id = run.id
                project.active_revision = None
                project.status = "queued"
    except (TrialUnavailable, UnverifiedTrialUser) as error:
        return web.json_response(
            {"error": {"code": "trial_unavailable", "message": str(error)}}, status=409
        )
    return web.json_response(serialize_run(run), status=202)


async def _preview(database, run_id: UUID) -> dict | None:
    accepted = (await database.execute(
        select(GenerationArtifact)
        .where(
            GenerationArtifact.run_id == run_id,
            GenerationArtifact.quality_status.in_(("accepted", "verified")),
        )
        .order_by(GenerationArtifact.revision.desc()).limit(50)
    )).scalars().all()
    for artifact in accepted:
        candidate_payload = artifact.config.get("artifact") if isinstance(artifact.config, dict) else None
        if not isinstance(candidate_payload, dict):
            continue
        try:
            candidate = WidgetArtifact.from_dict(candidate_payload)
            valid = not validate_artifact(candidate, previous_revision=max(0, candidate.revision - 1))
        except (KeyError, TypeError, ValueError):
            valid = False
        if valid:
            return serialize_artifact(artifact, source="accepted_artifact")
    drafts = (await database.execute(
        select(GenerationEvent.payload)
        .where(
            GenerationEvent.run_id == run_id,
            GenerationEvent.event_type == "artifact.draft_staged",
        )
        .order_by(GenerationEvent.sequence.desc()).limit(50)
    )).scalars().all()
    for payload in drafts:
        candidate_payload = payload.get("artifact") if isinstance(payload, dict) else None
        if not isinstance(candidate_payload, dict):
            continue
        try:
            candidate = WidgetArtifact.from_dict(candidate_payload)
            valid = not validate_artifact(candidate, previous_revision=max(0, candidate.revision - 1))
        except (KeyError, TypeError, ValueError):
            valid = False
        if valid:
            return serialize_artifact(candidate_payload, source="restorable_draft")
    return None


async def get_run(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        run, _ = await _owned_run(database, _uuid(request.match_info["run_id"]), user_id, tenant_id)
        events = list((await database.execute(
            select(GenerationEvent).where(GenerationEvent.run_id == run.id).order_by(GenerationEvent.sequence)
        )).scalars())
        payload = serialize_run(run, events=events, preview=await _preview(database, run.id))
    return web.json_response(payload)


async def get_preview(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        run, _ = await _owned_run(database, _uuid(request.match_info["run_id"]), user_id, tenant_id)
        preview = await _preview(database, run.id)
    if preview is None:
        raise web.HTTPNotFound(text=_error("preview_not_found"), content_type="application/json")
    return web.json_response({"artifact": preview})


async def get_artifact(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request)
    artifact_id = _uuid(request.match_info["artifact_id"])
    factory = get_session_factory(request.app)
    async with factory() as database:
        artifact = await database.scalar(
            select(GenerationArtifact)
            .join(GenerationRun, GenerationArtifact.run_id == GenerationRun.id)
            .join(Project, GenerationRun.project_id == Project.id)
            .where(
                GenerationArtifact.id == artifact_id,
                Project.owner_user_id == user_id,
                Project.tenant_id == tenant_id,
            )
        )
    if artifact is None:
        raise web.HTTPNotFound(text=_error("not_found"), content_type="application/json")
    return web.json_response(serialize_artifact(artifact, source="artifact"))


async def stream_events(request: web.Request) -> web.StreamResponse:
    user_id, tenant_id = await _scope(request)
    run_id = _uuid(request.match_info["run_id"])
    try:
        cursor = max(0, int(request.headers.get("Last-Event-ID", "0")))
    except ValueError as error:
        raise web.HTTPBadRequest(text=_error("invalid_last_event_id"), content_type="application/json") from error
    factory = get_session_factory(request.app)
    async with factory() as database:
        await _owned_run(database, run_id, user_id, tenant_id)
    response = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })
    await response.prepare(request)
    poll_seconds = float(request.app.get("project_sse_poll_seconds", 1.0))
    while True:
        # Close the session before waiting; SSE must never hold an idle transaction.
        async with factory() as database:
            run, _ = await _owned_run(database, run_id, user_id, tenant_id)
            events = list((await database.execute(
                select(GenerationEvent)
                .where(GenerationEvent.run_id == run.id, GenerationEvent.sequence > cursor)
                .order_by(GenerationEvent.sequence).limit(SSE_PAGE_SIZE)
            )).scalars())
            terminal = run.state in TERMINAL_STATES
        if events:
            for event in events:
                data = json.dumps(serialize_event(event), ensure_ascii=False, separators=(",", ":"))
                await response.write(
                    f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n".encode()
                )
                cursor = event.sequence
            if terminal and len(events) < SSE_PAGE_SIZE:
                break
            if len(events) == SSE_PAGE_SIZE:
                continue
        elif not terminal:
            await response.write(b": heartbeat\n\n")
        if terminal:
            break
        if not terminal:
            await asyncio.sleep(poll_seconds)
    await response.write_eof()
    return response


def setup_project_routes(app: web.Application) -> None:
    app.router.add_get("/api/projects", list_projects)
    app.router.add_get("/api/projects/{project_id}", get_project)
    app.router.add_post("/api/projects/{project_id}/runs", create_run)
    app.router.add_get("/api/runs/{run_id}", get_run)
    app.router.add_get("/api/runs/{run_id}/preview", get_preview)
    app.router.add_get("/api/runs/{run_id}/events", stream_events)
    app.router.add_get("/api/artifacts/{artifact_id}", get_artifact)


def _error(code: str) -> str:
    return json.dumps({"error": {"code": code}}, separators=(",", ":"))


__all__ = ["setup_project_routes"]
