from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from aiohttp import web

from .engines.base import BuilderEngineError
from .demo import DemoUnavailable, load_demo, render_demo_page, render_demo_unavailable
from .models import BuilderRequest, EngineName, RunStatus
from .orchestrator import BuilderOrchestrator
from .preview import PREVIEW_CSP, build_preview_document
from .store import ArtifactNotFound, RunCapacityExceeded, RunNotFound, RunStore
from .ui import render_builder_page


PARENT_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; frame-src 'self'; img-src 'none'; font-src 'none'; "
    "form-action 'none'; base-uri 'none'; object-src 'none'"
)
STORE_KEY = web.AppKey("builder_store", RunStore)
ORCHESTRATOR_KEY = web.AppKey("builder_orchestrator", BuilderOrchestrator)
ENGINES_KEY = web.AppKey("builder_enabled_engines", tuple)
UI_DEFAULTS_KEY = web.AppKey("builder_ui_defaults", tuple)
DEMO_PATH_KEY = web.AppKey("builder_demo_path", object)


def _error(code: str, message: str, *, status: int) -> web.Response:
    return web.json_response(
        {"error": {"code": code, "message": message}}, status=status
    )


@web.middleware
async def security_headers(
    request: web.Request, handler: Any
) -> web.StreamResponse:
    response = await handler(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        PREVIEW_CSP if request.path.endswith("/preview") else PARENT_CSP
    )
    return response


async def page(request: web.Request) -> web.Response:
    default_engine, default_temperature, default_max_repairs = request.app[
        UI_DEFAULTS_KEY
    ]
    return web.Response(
        text=render_builder_page(
            request.app[ENGINES_KEY],
            default_engine=default_engine,
            default_temperature=default_temperature,
            default_max_repairs=default_max_repairs,
        ),
        content_type="text/html",
        charset="utf-8",
    )


def _configured_demo(request: web.Request):
    path = request.app[DEMO_PATH_KEY]
    if path is None:
        raise DemoUnavailable("demo path is not configured")
    return load_demo(path)


async def demo_page(request: web.Request) -> web.Response:
    try:
        demo = _configured_demo(request)
    except DemoUnavailable:
        return web.Response(
            text=render_demo_unavailable(),
            status=503,
            content_type="text/html",
            charset="utf-8",
        )
    return web.Response(
        text=render_demo_page(demo), content_type="text/html", charset="utf-8"
    )


async def demo_preview(request: web.Request) -> web.Response:
    try:
        demo = _configured_demo(request)
    except DemoUnavailable:
        return _error(
            "preview_not_ready", "Демонстрационный виджет пока не готов", status=503
        )
    return web.Response(
        text=build_preview_document(demo.artifact),
        content_type="text/html",
        charset="utf-8",
    )


async def create_run(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("JSON object is required")
        builder_request = BuilderRequest.from_dict(payload)
        if builder_request.engine not in request.app[ENGINES_KEY]:
            raise ValueError("selected engine is not enabled")
        snapshot = await request.app[ORCHESTRATOR_KEY].start(builder_request)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _error(
            "invalid_request", "Параметры запуска некорректны", status=400
        )
    except BuilderEngineError as exc:
        return _error(exc.error_code, exc.public_message, status=503)
    except RunCapacityExceeded:
        return _error(
            "run_capacity",
            "Все слоты генерации заняты; повторите запрос позже",
            status=429,
        )
    return web.json_response(snapshot.to_dict(), status=202)


async def get_run(request: web.Request) -> web.Response:
    try:
        snapshot = await request.app[STORE_KEY].snapshot(request.match_info["run_id"])
    except RunNotFound:
        return _error("run_not_found", "Запуск не найден", status=404)
    return web.json_response(snapshot.to_dict())


async def cancel_run(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    try:
        requested = await request.app[ORCHESTRATOR_KEY].cancel(run_id)
        snapshot = await request.app[STORE_KEY].snapshot(run_id)
    except RunNotFound:
        return _error("run_not_found", "Запуск не найден", status=404)
    return web.json_response(
        {"run_id": run_id, "cancel_requested": requested or snapshot.cancel_requested},
        status=202,
    )


async def retry_run(request: web.Request) -> web.Response:
    try:
        snapshot = await request.app[ORCHESTRATOR_KEY].retry(
            request.match_info["run_id"]
        )
    except RunNotFound:
        return _error("run_not_found", "Запуск не найден", status=404)
    except ValueError:
        return _error(
            "run_not_retryable", "Этот запуск пока нельзя повторить", status=409
        )
    except BuilderEngineError as exc:
        return _error(exc.error_code, exc.public_message, status=503)
    return web.json_response(snapshot.to_dict(), status=202)


async def preview(request: web.Request) -> web.Response:
    requested_revision = request.query.get("revision")
    try:
        revision = int(requested_revision) if requested_revision else None
        artifact = await request.app[STORE_KEY].artifact(
            request.match_info["run_id"], revision
        )
    except RunNotFound:
        return _error("run_not_found", "Запуск не найден", status=404)
    except (TypeError, ValueError):
        return _error("invalid_revision", "Номер ревизии некорректен", status=400)
    except ArtifactNotFound:
        return _error(
            "preview_not_ready", "Запрошенная валидная ревизия ещё не создана", status=409
        )
    return web.Response(
        text=build_preview_document(artifact),
        content_type="text/html",
        charset="utf-8",
    )


def _last_event_id(request: web.Request) -> int:
    raw = request.headers.get("Last-Event-ID") or request.query.get(
        "last_event_id", "0"
    )
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def event_stream(request: web.Request) -> web.StreamResponse:
    run_id = request.match_info["run_id"]
    store = request.app[STORE_KEY]
    try:
        await store.snapshot(run_id)
    except RunNotFound:
        return _error("run_not_found", "Запуск не найден", status=404)
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)
    sequence = _last_event_id(request)
    try:
        while True:
            events = await store.events_after(run_id, sequence)
            for event in events:
                payload = json.dumps(
                    event.to_dict(), ensure_ascii=False, separators=(",", ":")
                )
                await response.write(
                    f"id: {event.sequence}\ndata: {payload}\n\n".encode("utf-8")
                )
                sequence = event.sequence
            snapshot = await store.snapshot(run_id)
            if snapshot.status in {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
            }:
                break
            pending = await store.wait_for_events(run_id, sequence, timeout=12)
            if not pending:
                await response.write(b": heartbeat\n\n")
        await response.write_eof()
    except (ConnectionResetError, RuntimeError, asyncio.CancelledError):
        pass
    return response


async def _cleanup(app: web.Application) -> None:
    await app[ORCHESTRATOR_KEY].close()


def create_builder_lab_app(
    *,
    store: RunStore,
    orchestrator: BuilderOrchestrator,
    enabled_engines: Iterable[EngineName],
    default_engine: EngineName = EngineName.DIRECT,
    default_temperature: float = 0.9,
    default_max_repairs: int = 3,
    demo_path: Path | None = None,
) -> web.Application:
    engines = tuple(enabled_engines)
    if not engines:
        raise ValueError("at least one builder engine must be enabled")
    app = web.Application(middlewares=[security_headers])
    app[STORE_KEY] = store
    app[ORCHESTRATOR_KEY] = orchestrator
    app[ENGINES_KEY] = engines
    app[UI_DEFAULTS_KEY] = (
        default_engine if default_engine in engines else engines[0],
        default_temperature,
        default_max_repairs,
    )
    app[DEMO_PATH_KEY] = demo_path
    app.router.add_get("/", page)
    app.router.add_get("/demo", demo_page)
    app.router.add_get("/demo/preview", demo_preview)
    app.router.add_post("/api/runs", create_run)
    app.router.add_get("/api/runs/{run_id}", get_run)
    app.router.add_get("/api/runs/{run_id}/events", event_stream)
    app.router.add_get("/api/runs/{run_id}/preview", preview)
    app.router.add_post("/api/runs/{run_id}/cancel", cancel_run)
    app.router.add_post("/api/runs/{run_id}/retry", retry_run)
    app.on_cleanup.append(_cleanup)
    return app
