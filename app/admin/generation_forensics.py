from __future__ import annotations

from html import escape
import json
import logging
from uuid import UUID

from aiohttp import web

from app.admin.operator_auth import require_verified_operator
from app.db.session import get_session_factory
from app.generation_timeline import (
    load_operator_recent_runs,
    load_operator_timeline_summary,
)


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


__all__ = ["setup_operator_forensics_routes"]
