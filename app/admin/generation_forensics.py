from __future__ import annotations

import asyncio
import base64
from html import escape
import json
import logging
from typing import Any, Protocol
from uuid import UUID

from aiohttp import web
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import require_admin_session
from app.admin.layout import render_layout
from app.admin.operator_auth import require_verified_operator
from app.db.session import get_session_factory
from app.generation_timeline import (
    load_operator_recent_runs,
    load_operator_timeline_summary,
)
from app.models.accounting import load_admin_model_waterfall
from app.saas.models import (
    GenerationEvent,
    GenerationForensicAccessLog,
    GenerationForensicManifest,
    GenerationRun,
    ModelCall,
    Project,
)
from builder_lab.forensics.config import GenerationForensicsConfig
from builder_lab.forensics.models import ForensicEntry, ForensicManifest
from builder_lab.redaction import redact_private_data
from core.config import settings as core_settings


LOGGER = logging.getLogger(__name__)
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_PAGE_HEADERS = {
    **_NO_STORE,
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
_FORENSICS_CONFIG_KEY = web.AppKey(
    "admin_generation_forensics_config",
    GenerationForensicsConfig,
)
_FORENSICS_STORAGE_KEY = web.AppKey(
    "admin_generation_forensics_storage",
    object,
)
_MAX_EXPORT_BLOB_BYTES = 1_000_000


class _ForensicStorage(Protocol):
    available: bool

    def load_manifest(self, run_id: UUID) -> ForensicManifest: ...

    def manifest_digest(self, run_id: UUID) -> str: ...

    def read_manifest_entry(self, run_id: UUID, entry: ForensicEntry) -> bytes: ...


def _run_id(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise web.HTTPNotFound(headers=_NO_STORE) from error


def _limit(request: web.Request) -> int:
    raw = request.query.get("limit", "50")
    try:
        value = int(raw)
    except ValueError as error:
        raise web.HTTPBadRequest(headers=_NO_STORE) from error
    if not 1 <= value <= 100:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    return value


def _admin_config(request: web.Request) -> GenerationForensicsConfig:
    config = request.app.get(_FORENSICS_CONFIG_KEY)
    if not isinstance(config, GenerationForensicsConfig):
        app_config = request.app.get("config")
        config = getattr(app_config, "generation_forensics", None)
    if (
        not isinstance(config, GenerationForensicsConfig)
        or not config.enabled
        or not config.admin_emails
    ):
        raise web.HTTPNotFound(headers=_NO_STORE)
    return config


async def _access_log_id(database: AsyncSession) -> int | None:
    if database.get_bind().dialect.name != "sqlite":
        return None
    current = await database.scalar(select(func.max(GenerationForensicAccessLog.id)))
    return int(current or 0) + 1


async def _write_access_log(
    request: web.Request,
    *,
    actor_email: str,
    action: str,
    allowed: bool,
    run_id: UUID | None = None,
    project_id: UUID | None = None,
    user_id: int | None = None,
) -> None:
    factory = get_session_factory(request.app)
    async with factory() as database, database.begin():
        database.add(
            GenerationForensicAccessLog(
                id=await _access_log_id(database),
                actor_email=actor_email[:320],
                action=action,
                allowed=allowed,
                run_id=run_id,
                project_id=project_id,
                user_id=user_id,
                metadata_json={},
            )
        )


async def _admin_actor(request: web.Request, *, action: str) -> str:
    config = _admin_config(request)
    actor_email, _tenant_slug = await require_admin_session(request)
    allowed = bool(core_settings.ADMIN_PASSWORD) and (
        actor_email in config.admin_emails
        and actor_email in core_settings.ADMIN_EMAILS
    )
    if not allowed:
        await _write_access_log(
            request,
            actor_email=actor_email,
            action=action,
            allowed=False,
        )
        raise web.HTTPNotFound(headers=_NO_STORE)
    return actor_email


def _search_filters(request: web.Request) -> tuple[UUID | None, int | None]:
    raw_run_id = request.query.get("run_id")
    raw_user_id = request.query.get("user_id")
    if raw_run_id is not None and raw_user_id is not None:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    run_id: UUID | None = None
    user_id: int | None = None
    if raw_run_id is not None:
        try:
            run_id = UUID(raw_run_id)
        except (TypeError, ValueError) as error:
            raise web.HTTPBadRequest(headers=_NO_STORE) from error
    if raw_user_id is not None:
        try:
            user_id = int(raw_user_id)
        except (TypeError, ValueError) as error:
            raise web.HTTPBadRequest(headers=_NO_STORE) from error
        if user_id < 1:
            raise web.HTTPBadRequest(headers=_NO_STORE)
    return run_id, user_id


async def _search_runs(
    database: AsyncSession,
    *,
    run_id: UUID | None,
    user_id: int | None,
) -> list[dict[str, object]]:
    if run_id is None and user_id is None:
        return []
    statement = (
        select(
            GenerationRun.id,
            GenerationRun.project_id,
            Project.owner_user_id,
            GenerationRun.state,
            GenerationRun.created_at,
            GenerationRun.finished_at,
        )
        .join(Project, GenerationRun.project_id == Project.id)
        .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        .limit(100)
    )
    if run_id is not None:
        statement = statement.where(GenerationRun.id == run_id)
    if user_id is not None:
        statement = statement.where(Project.owner_user_id == user_id)
    rows = (await database.execute(statement)).all()
    return [
        {
            "id": str(row.id),
            "project_id": str(row.project_id),
            "user_id": int(row.owner_user_id),
            "state": str(row.state),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }
        for row in rows
    ]


async def admin_generation_runs_page(request: web.Request) -> web.Response:
    actor_email = await _admin_actor(request, action="search")
    run_id, user_id = _search_filters(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        rows = await _search_runs(database, run_id=run_id, user_id=user_id)
    matched_run_id = UUID(str(rows[0]["id"])) if run_id is not None and rows else None
    matched_project_id = (
        UUID(str(rows[0]["project_id"])) if run_id is not None and rows else None
    )
    matched_user_id = user_id if user_id is not None and rows else None
    await _write_access_log(
        request,
        actor_email=actor_email,
        action="search",
        allowed=True,
        run_id=matched_run_id,
        project_id=matched_project_id,
        user_id=matched_user_id,
    )
    rendered_rows = "".join(
        "<tr>"
        f"<td><a href='/admin/generation-runs/{escape(str(row['id']))}'>{escape(str(row['id']))}</a></td>"
        f"<td>{escape(str(row['project_id']))}</td>"
        f"<td>{escape(str(row['user_id']))}</td>"
        f"<td>{escape(str(row['state']))}</td>"
        "</tr>"
        for row in rows
    )
    results = (
        "<table><thead><tr><th>Run</th><th>Project</th><th>User</th><th>State</th></tr></thead>"
        f"<tbody>{rendered_rows}</tbody></table>"
        if rows
        else ""
    )
    content = (
        "<section class='card'><h2>Forensic timeline</h2>"
        "<form method='get' action='/admin/generation-runs'>"
        "<label>Run ID<input name='run_id' type='text'></label>"
        "<label>User ID<input name='user_id' type='number' min='1'></label>"
        "<button type='submit'>Найти</button></form>"
        f"{results}</section>"
    )
    response = render_layout(
        "Forensic timeline",
        content,
        nav_extra="<a href='/admin/generation-runs'>Forensics</a>",
    )
    response.headers.update(_NO_STORE)
    return response


def _iso(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


async def _run_scope(
    database: AsyncSession,
    run_id: UUID,
) -> tuple[GenerationRun, Project, GenerationForensicManifest | None] | None:
    row = (
        await database.execute(
            select(GenerationRun, Project, GenerationForensicManifest)
            .join(Project, GenerationRun.project_id == Project.id)
            .outerjoin(
                GenerationForensicManifest,
                GenerationForensicManifest.run_id == GenerationRun.id,
            )
            .where(GenerationRun.id == run_id)
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1], row[2]


def _storage(request: web.Request) -> _ForensicStorage | None:
    value = request.app.get(_FORENSICS_STORAGE_KEY)
    if value is None:
        return None
    required = ("available", "load_manifest", "manifest_digest", "read_manifest_entry")
    return value if all(hasattr(value, name) for name in required) else None


async def _evidence_snapshot(
    request: web.Request,
    *,
    run_id: UUID,
    project: Project,
    manifest_row: GenerationForensicManifest | None,
) -> tuple[dict[str, object], ForensicManifest | None]:
    if manifest_row is None:
        return {
            "state": "missing",
            "availability": "missing",
            "checksum": "unavailable",
            "entry_count": 0,
            "byte_count": 0,
            "expires_at": None,
        }, None
    summary: dict[str, object] = {
        "state": manifest_row.state,
        "availability": "unavailable",
        "checksum": "unavailable",
        "entry_count": int(manifest_row.entry_count),
        "byte_count": int(manifest_row.byte_count),
        "expires_at": _iso(manifest_row.expires_at),
    }
    storage = _storage(request)
    if storage is None or not storage.available:
        return summary, None
    try:
        manifest = await asyncio.to_thread(storage.load_manifest, run_id)
        digest = await asyncio.to_thread(storage.manifest_digest, run_id)
        if (
            manifest.run_id != run_id
            or manifest.project_id != project.id
            or manifest.user_id != project.owner_user_id
            or (
                manifest_row.manifest_sha256 is not None
                and digest != manifest_row.manifest_sha256
            )
        ):
            raise ValueError("forensic manifest identity mismatch")
    except (OSError, RuntimeError, ValueError):
        return summary, None
    summary["availability"] = (
        "degraded" if manifest_row.state == "degraded" else "available"
    )
    summary["checksum"] = "valid"
    summary["entry_count"] = len(manifest.entries)
    summary["byte_count"] = sum(entry.byte_count for entry in manifest.entries)
    return summary, manifest


async def _read_json_evidence(
    storage: _ForensicStorage,
    *,
    run_id: UUID,
    entry: ForensicEntry,
) -> dict[str, Any]:
    data = await asyncio.to_thread(storage.read_manifest_entry, run_id, entry)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("forensic JSON evidence is invalid") from error
    sanitized = redact_private_data(value)
    if not isinstance(sanitized, dict):
        raise ValueError("forensic JSON evidence is not an object")
    return sanitized


async def _detail_payload(
    request: web.Request,
    *,
    run: GenerationRun,
    project: Project,
    manifest_row: GenerationForensicManifest | None,
    database: AsyncSession,
) -> dict[str, Any]:
    evidence_summary, manifest = await _evidence_snapshot(
        request,
        run_id=run.id,
        project=project,
        manifest_row=manifest_row,
    )
    event_rows = (
        await database.scalars(
            select(GenerationEvent)
            .where(GenerationEvent.run_id == run.id)
            .order_by(GenerationEvent.sequence, GenerationEvent.id)
        )
    ).all()
    model_rows = (
        await database.scalars(
            select(ModelCall)
            .where(ModelCall.run_id == run.id)
            .order_by(ModelCall.created_at, ModelCall.id)
        )
    ).all()
    event_evidence: dict[int, dict[str, Any]] = {}
    model_evidence: dict[UUID, dict[str, Any]] = {}
    evidence_records: list[dict[str, Any]] = []
    storage = _storage(request)
    if manifest is not None and storage is not None:
        for entry in manifest.entries:
            if entry.kind == "blob":
                evidence_records.append(
                    {
                        "kind": entry.kind,
                        "byte_count": entry.byte_count,
                        "sha256": entry.sha256,
                        "created_at": _iso(entry.created_at),
                    }
                )
                continue
            try:
                value = await _read_json_evidence(
                    storage,
                    run_id=run.id,
                    entry=entry,
                )
            except (OSError, RuntimeError, ValueError):
                evidence_summary["availability"] = "unavailable"
                evidence_summary["checksum"] = "unavailable"
                evidence_records = []
                event_evidence = {}
                model_evidence = {}
                break
            payload = value.get("payload")
            payload = payload if isinstance(payload, dict) else {}
            if entry.kind == "event":
                try:
                    sequence = int(entry.relative_path.split("/", 1)[1][:8])
                except (IndexError, ValueError):
                    continue
                event_evidence[sequence] = payload
            elif entry.kind == "model-call":
                try:
                    call_id = UUID(entry.relative_path.split("/", 1)[1][:36])
                except (IndexError, ValueError):
                    continue
                model_evidence[call_id] = payload
            evidence_records.append(
                {
                    "kind": entry.kind,
                    "byte_count": entry.byte_count,
                    "sha256": entry.sha256,
                    "created_at": _iso(entry.created_at),
                    "payload": payload,
                }
            )
    events = [
        {
            "sequence": int(row.sequence),
            "event_type": row.event_type,
            "public_message": redact_private_data(row.public_message),
            "payload": redact_private_data(row.payload),
            "created_at": _iso(row.created_at),
            "forensic": event_evidence.get(int(row.sequence)),
        }
        for row in event_rows
    ]
    model_calls = [
        {
            "id": str(row.id),
            "provider": row.provider,
            "model": row.model,
            "role": row.role,
            "request_id": redact_private_data(row.request_id),
            "status": row.status,
            "input_tokens": int(row.input_tokens),
            "output_tokens": int(row.output_tokens),
            "thinking_tokens": int(row.thinking_tokens),
            "latency_ms": int(row.latency_ms),
            "cost_state": row.cost_state,
            "cost_microusd": row.cost_microusd,
            "created_at": _iso(row.created_at),
            "forensic": model_evidence.get(row.id),
        }
        for row in model_rows
    ]
    return {
        "run": {
            "id": str(run.id),
            "project_id": str(project.id),
            "user_id": int(project.owner_user_id),
            "state": run.state,
            "created_at": _iso(run.created_at),
            "started_at": _iso(run.started_at),
            "finished_at": _iso(run.finished_at),
        },
        "evidence": evidence_summary,
        "events": events,
        "model_calls": model_calls,
        "evidence_records": evidence_records,
    }


async def admin_generation_run_page(request: web.Request) -> web.Response:
    actor_email = await _admin_actor(request, action="view")
    run_id = _run_id(request.match_info["run_id"])
    factory = get_session_factory(request.app)
    async with factory() as database:
        scope = await _run_scope(database, run_id)
        if scope is None:
            raise web.HTTPNotFound(headers=_NO_STORE)
        run, project, manifest_row = scope
        payload = await _detail_payload(
            request,
            run=run,
            project=project,
            manifest_row=manifest_row,
            database=database,
        )
    await _write_access_log(
        request,
        actor_email=actor_email,
        action="view",
        allowed=True,
        run_id=run_id,
        project_id=project.id,
        user_id=project.owner_user_id,
    )
    evidence = payload["evidence"]
    encoded = escape(json.dumps(payload, ensure_ascii=False, indent=2))
    content = (
        "<section class='card'>"
        f"<h2>Run {escape(str(run_id))}</h2>"
        f"<p>checksum: {escape(str(evidence['checksum']))}</p>"
        f"<p>byte_count: {escape(str(evidence['byte_count']))}</p>"
        f"<p>expires_at: {escape(str(evidence['expires_at']))}</p>"
        f"<pre>{encoded}</pre>"
        f"<a href='/admin/generation-runs/{run_id}/export.ndjson'>Export NDJSON</a>"
        "</section>"
    )
    response = render_layout(
        f"Run {run_id}",
        content,
        nav_extra="<a href='/admin/generation-runs'>Forensics</a>",
    )
    response.headers.update(_NO_STORE)
    return response


def _ndjson_bytes(record: object) -> bytes:
    sanitized = redact_private_data(record)
    return (
        json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


async def admin_generation_run_export(request: web.Request) -> web.StreamResponse:
    actor_email = await _admin_actor(request, action="export")
    run_id = _run_id(request.match_info["run_id"])
    factory = get_session_factory(request.app)
    async with factory() as database:
        scope = await _run_scope(database, run_id)
        if scope is None:
            raise web.HTTPNotFound(headers=_NO_STORE)
        run, project, manifest_row = scope
        evidence_summary, manifest = await _evidence_snapshot(
            request,
            run_id=run.id,
            project=project,
            manifest_row=manifest_row,
        )
    await _write_access_log(
        request,
        actor_email=actor_email,
        action="export",
        allowed=True,
        run_id=run_id,
        project_id=project.id,
        user_id=project.owner_user_id,
    )
    response = web.StreamResponse(
        status=200,
        headers={
            **_NO_STORE,
            "Content-Type": "application/x-ndjson; charset=utf-8",
            "Content-Disposition": f'attachment; filename="generation-{run_id}.ndjson"',
            "X-Content-Type-Options": "nosniff",
        },
    )
    await response.prepare(request)
    await response.write(
        _ndjson_bytes(
            {
                "record_type": "run_metadata",
                "run_id": str(run_id),
                "project_id": str(project.id),
                "user_id": int(project.owner_user_id),
                "state": run.state,
                "evidence": evidence_summary,
            }
        )
    )
    storage = _storage(request)
    event_entries: dict[int, ForensicEntry] = {}
    model_entries: dict[UUID, ForensicEntry] = {}
    if manifest is not None:
        for entry in manifest.entries:
            try:
                filename = entry.relative_path.split("/", 1)[1]
                if entry.kind == "event":
                    event_entries[int(filename[:8])] = entry
                elif entry.kind == "model-call":
                    model_entries[UUID(filename[:36])] = entry
            except (IndexError, ValueError):
                continue
    async with factory() as database:
        event_stream = await database.stream_scalars(
            select(GenerationEvent)
            .where(GenerationEvent.run_id == run_id)
            .order_by(GenerationEvent.sequence, GenerationEvent.id)
        )
        async for event in event_stream:
            forensic: dict[str, Any] | None = None
            entry = event_entries.get(int(event.sequence))
            if storage is not None and entry is not None:
                try:
                    envelope = await _read_json_evidence(
                        storage,
                        run_id=run_id,
                        entry=entry,
                    )
                    value = envelope.get("payload")
                    forensic = value if isinstance(value, dict) else None
                except (OSError, RuntimeError, ValueError):
                    forensic = None
            await response.write(
                _ndjson_bytes(
                    {
                        "record_type": "event",
                        "sequence": int(event.sequence),
                        "event_type": event.event_type,
                        "public_message": event.public_message,
                        "payload": event.payload,
                        "created_at": _iso(event.created_at),
                        "forensic": forensic,
                    }
                )
            )
        model_stream = await database.stream_scalars(
            select(ModelCall)
            .where(ModelCall.run_id == run_id)
            .order_by(ModelCall.created_at, ModelCall.id)
        )
        async for model_call in model_stream:
            forensic = None
            entry = model_entries.get(model_call.id)
            if storage is not None and entry is not None:
                try:
                    envelope = await _read_json_evidence(
                        storage,
                        run_id=run_id,
                        entry=entry,
                    )
                    value = envelope.get("payload")
                    forensic = value if isinstance(value, dict) else None
                except (OSError, RuntimeError, ValueError):
                    forensic = None
            await response.write(
                _ndjson_bytes(
                    {
                        "record_type": "model_call",
                        "id": str(model_call.id),
                        "provider": model_call.provider,
                        "model": model_call.model,
                        "role": model_call.role,
                        "request_id": model_call.request_id,
                        "status": model_call.status,
                        "input_tokens": int(model_call.input_tokens),
                        "output_tokens": int(model_call.output_tokens),
                        "thinking_tokens": int(model_call.thinking_tokens),
                        "latency_ms": int(model_call.latency_ms),
                        "cost_state": model_call.cost_state,
                        "cost_microusd": model_call.cost_microusd,
                        "created_at": _iso(model_call.created_at),
                        "forensic": forensic,
                    }
                )
            )
    if evidence_summary["checksum"] != "valid":
        await response.write(
            _ndjson_bytes(
                {
                    "record_type": "evidence_unavailable",
                    "reason": "checksum_or_storage_unavailable",
                }
            )
        )
    else:
        if (
            storage is not None
            and manifest is not None
            and evidence_summary["checksum"] == "valid"
        ):
            for entry in manifest.entries:
                if entry.kind != "blob":
                    try:
                        envelope = await _read_json_evidence(
                            storage,
                            run_id=run_id,
                            entry=entry,
                        )
                    except (OSError, RuntimeError, ValueError):
                        await response.write(
                            _ndjson_bytes(
                                {
                                    "record_type": "evidence_unavailable",
                                    "kind": entry.kind,
                                    "reason": "checksum_or_storage_unavailable",
                                }
                            )
                        )
                        continue
                    await response.write(
                        _ndjson_bytes(
                            {
                                "record_type": "evidence",
                                "kind": entry.kind,
                                "byte_count": entry.byte_count,
                                "sha256": entry.sha256,
                                "created_at": _iso(entry.created_at),
                                "payload": envelope.get("payload"),
                            }
                        )
                    )
                    continue
                if entry.byte_count > _MAX_EXPORT_BLOB_BYTES:
                    await response.write(
                        _ndjson_bytes(
                            {
                                "record_type": "evidence",
                                "kind": "blob",
                                "byte_count": entry.byte_count,
                                "sha256": entry.sha256,
                                "base64": None,
                                "availability": "bounded_out",
                            }
                        )
                    )
                    continue
                try:
                    data = await asyncio.to_thread(
                        storage.read_manifest_entry,
                        run_id,
                        entry,
                    )
                except (OSError, RuntimeError, ValueError):
                    await response.write(
                        _ndjson_bytes(
                            {
                                "record_type": "evidence_unavailable",
                                "kind": "blob",
                                "reason": "checksum_or_storage_unavailable",
                            }
                        )
                    )
                    continue
                await response.write(
                    _ndjson_bytes(
                        {
                            "record_type": "evidence",
                            "kind": "blob",
                            "byte_count": entry.byte_count,
                            "sha256": entry.sha256,
                            "created_at": _iso(entry.created_at),
                            "base64": base64.b64encode(data).decode("ascii"),
                        }
                    )
                )
    await response.write_eof()
    return response


async def operator_runs_json(request: web.Request) -> web.Response:
    operator_id = await require_verified_operator(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        rows = await load_operator_recent_runs(database, limit=_limit(request))
    LOGGER.info("operator_generation_runs_listed", extra={"operator_user_id": operator_id})
    return web.json_response({"runs": rows}, headers=_NO_STORE)


async def operator_run_json(request: web.Request) -> web.Response:
    operator_id = await require_verified_operator(request)
    run_id = _run_id(request.match_info["run_id"])
    factory = get_session_factory(request.app)
    async with factory() as database:
        summary = await load_operator_timeline_summary(database, run_id=run_id)
        if summary is not None:
            summary["model_waterfall"] = await load_admin_model_waterfall(
                database,
                run_id=run_id,
            )
    if summary is None:
        raise web.HTTPNotFound(headers=_NO_STORE)
    LOGGER.info(
        "operator_generation_run_viewed",
        extra={"operator_user_id": operator_id, "generation_run_id": str(run_id)},
    )
    return web.json_response(summary, headers=_NO_STORE)


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(title)}</title><style>
body{{font:15px/1.5 system-ui;background:#111;color:#eee;margin:0;padding:32px}}
main{{max-width:1180px;margin:auto}}a{{color:#8bd6c1}}table{{width:100%;border-collapse:collapse}}
th,td{{padding:10px;border-bottom:1px solid #333;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#191919;padding:20px;border-radius:12px}}
</style></head><body><main>{body}</main></body></html>"""


async def operator_runs_page(request: web.Request) -> web.Response:
    operator_id = await require_verified_operator(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        rows = await load_operator_recent_runs(database, limit=_limit(request))
    rendered_rows = "".join(
        "<tr>"
        f"<td><a href='/operator/generation-runs/{row['id']}'>{escape(row['id'])}</a></td>"
        f"<td>{escape(row['state'])}</td><td>{escape(row['created_at'] or '—')}</td>"
        f"<td>{escape(row['finished_at'] or '—')}</td></tr>"
        for row in rows
    )
    body = (
        "<p><a href='/operator/funnel'>Воронка Kaigo</a></p>"
        "<h1>Запуски генератора</h1>"
        "<p>Только агрегированные технические данные без промптов, ключей и снимков.</p>"
        "<table><thead><tr><th>Запуск</th><th>Статус</th><th>Создан</th><th>Завершён</th></tr></thead>"
        f"<tbody>{rendered_rows}</tbody></table>"
    )
    LOGGER.info("operator_generation_page_viewed", extra={"operator_user_id": operator_id})
    return web.Response(text=_layout("Запуски Kaigo", body), content_type="text/html", headers=_PAGE_HEADERS)


async def operator_run_page(request: web.Request) -> web.Response:
    operator_id = await require_verified_operator(request)
    run_id = _run_id(request.match_info["run_id"])
    factory = get_session_factory(request.app)
    async with factory() as database:
        summary = await load_operator_timeline_summary(database, run_id=run_id)
        if summary is not None:
            summary["model_waterfall"] = await load_admin_model_waterfall(
                database,
                run_id=run_id,
            )
    if summary is None:
        raise web.HTTPNotFound(headers=_NO_STORE)
    encoded = escape(json.dumps(summary, ensure_ascii=False, indent=2))
    body = (
        "<p><a href='/operator/generation-runs'>← Все запуски</a></p>"
        f"<h1>Запуск {escape(str(run_id))}</h1><pre>{encoded}</pre>"
    )
    LOGGER.info(
        "operator_generation_page_viewed",
        extra={"operator_user_id": operator_id, "generation_run_id": str(run_id)},
    )
    return web.Response(text=_layout("Запуск Kaigo", body), content_type="text/html", headers=_PAGE_HEADERS)


def setup_operator_forensics_routes(app: web.Application) -> None:
    app.router.add_get("/api/operator/generation-runs", operator_runs_json)
    app.router.add_get("/api/operator/generation-runs/{run_id}", operator_run_json)
    app.router.add_get("/operator/generation-runs", operator_runs_page)
    app.router.add_get("/operator/generation-runs/{run_id}", operator_run_page)


def setup_generation_forensics_routes(
    app: web.Application,
    *,
    config: GenerationForensicsConfig | None = None,
) -> None:
    if config is None:
        app_config = app.get("config")
        candidate = getattr(app_config, "generation_forensics", None)
        config = (
            candidate
            if isinstance(candidate, GenerationForensicsConfig)
            else GenerationForensicsConfig.disabled()
        )
    app[_FORENSICS_CONFIG_KEY] = config
    storage: _ForensicStorage | None = None
    if config.enabled:
        try:
            from builder_lab.forensics.storage import GenerationForensicStorage

            storage = GenerationForensicStorage.open(config, writable=False)
        except (ImportError, OSError, RuntimeError, ValueError):
            storage = None
    app[_FORENSICS_STORAGE_KEY] = storage
    app.router.add_get("/admin/generation-runs", admin_generation_runs_page)
    app.router.add_get(
        "/admin/generation-runs/{run_id}/export.ndjson",
        admin_generation_run_export,
    )
    app.router.add_get(
        "/admin/generation-runs/{run_id}",
        admin_generation_run_page,
    )


__all__ = [
    "setup_generation_forensics_routes",
    "setup_operator_forensics_routes",
]
