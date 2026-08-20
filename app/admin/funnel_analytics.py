from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from html import escape
import logging

from aiohttp import web

from app.admin.operator_auth import require_verified_operator
from app.analytics.reporting import load_funnel_report
from app.db.session import get_session_factory


LOGGER = logging.getLogger(__name__)
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_PAGE_HEADERS = {
    **_NO_STORE,
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def _date_range(request: web.Request) -> tuple[datetime, datetime]:
    tomorrow = datetime.combine(
        datetime.now(UTC).date() + timedelta(days=1),
        time.min,
        tzinfo=UTC,
    )

    def parse(name: str, fallback: datetime) -> datetime:
        raw = request.query.get(name)
        if raw is None:
            return fallback
        try:
            parsed = date.fromisoformat(raw)
        except ValueError as error:
            raise web.HTTPBadRequest(headers=_NO_STORE) from error
        if raw != parsed.isoformat():
            raise web.HTTPBadRequest(headers=_NO_STORE)
        return datetime.combine(parsed, time.min, tzinfo=UTC)

    end = parse("to", tomorrow)
    start = parse("from", end - timedelta(days=30))
    if start >= end or end - start > timedelta(days=366):
        raise web.HTTPBadRequest(headers=_NO_STORE)
    return start, end


async def _report(request: web.Request) -> tuple[int, dict[str, object]]:
    operator_id = await require_verified_operator(request)
    start, end = _date_range(request)
    source = request.query.get("source") or None
    factory = get_session_factory(request.app)
    try:
        async with factory() as database:
            report = await load_funnel_report(
                database,
                start=start,
                end=end,
                source=source,
            )
    except ValueError as error:
        raise web.HTTPBadRequest(headers=_NO_STORE) from error
    return operator_id, report


async def operator_funnel_json(request: web.Request) -> web.Response:
    operator_id, report = await _report(request)
    LOGGER.info(
        "operator_funnel_report_viewed", extra={"operator_user_id": operator_id}
    )
    return web.json_response(report, headers=_NO_STORE)


def _layout(body: str) -> str:
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Воронка Kaigo</title><style>
body{{font:15px/1.5 system-ui;background:#111;color:#eee;margin:0;padding:32px}}
main{{max-width:1180px;margin:auto}}a{{color:#8bd6c1}}form{{display:flex;gap:12px;align-items:end;flex-wrap:wrap;margin:24px 0}}
label{{display:grid;gap:6px}}input,select,button{{font:inherit;padding:9px 11px;border-radius:8px;border:1px solid #444;background:#191919;color:#eee}}
button{{background:#277c68;border-color:#277c68;cursor:pointer}}table{{width:100%;border-collapse:collapse;margin:22px 0}}
th,td{{padding:10px;border-bottom:1px solid #333;text-align:left}}.muted{{color:#aaa}}
</style></head><body><main>{body}</main></body></html>"""


def _engagement_table(rows: list[dict[str, object]]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{escape(str(row['label']))}</td>"
        f"<td>{int(row['journeys'])}</td>"
        f"<td>{float(row['from_entry_percent']):.2f}%</td>"
        "</tr>"
        for row in rows
    )
    return (
        "<h2>Ключевые действия</h2>"
        "<table><thead><tr><th>Действие</th><th>Уникальные пути</th>"
        f"<th>От визитов</th></tr></thead><tbody>{body}</tbody></table>"
    )


async def operator_funnel_page(request: web.Request) -> web.Response:
    operator_id, report = await _report(request)
    period = report["period"]
    filters = report["filters"]
    selected_source = filters["source"] or ""
    source_options = ["", "telegram", "google", "yandex", "vk", "unattributed"]
    options = "".join(
        f'<option value="{escape(value)}"'
        f"{' selected' if value == selected_source else ''}>"
        f"{escape(value or 'все источники')}</option>"
        for value in source_options
    )
    stage_rows = "".join(
        "<tr>"
        f"<td>{escape(str(stage['label']))}</td>"
        f"<td>{int(stage['journeys'])}</td>"
        f"<td>{float(stage['step_conversion_percent']):.2f}%</td>"
        f"<td>{float(stage['cumulative_conversion_percent']):.2f}%</td>"
        "</tr>"
        for stage in report["stages"]
    )
    source_rows = "".join(
        f"<tr><td>{escape(str(row['source']))}</td><td>{int(row['journeys'])}</td></tr>"
        for row in report["sources"]
    )
    body = (
        "<p><a href='/operator/generation-runs'>Запуски генератора</a></p>"
        "<h1>Воронка Kaigo</h1>"
        "<p class='muted'>Только агрегаты по анонимным путям. URL, промпты, email и внутренние идентификаторы не выводятся.</p>"
        "<form method='get' action='/operator/funnel'>"
        f"<label>С <input type='date' name='from' value='{escape(str(period['from'])[:10])}'></label>"
        f"<label>До, не включая дату <input type='date' name='to' value='{escape(str(period['to'])[:10])}'></label>"
        f"<label>Первый источник <select name='source'>{options}</select></label>"
        "<button type='submit'>Показать</button></form>"
        "<h2>Основной путь</h2><table><thead><tr><th>Этап</th><th>Пути</th><th>От прошлого этапа</th><th>От визитов</th></tr></thead>"
        f"<tbody>{stage_rows}</tbody></table>"
        f"{_engagement_table(report['engagement'])}"
        "<h2>Первый источник</h2><table><thead><tr><th>Источник</th><th>Пути</th></tr></thead>"
        f"<tbody>{source_rows}</tbody></table>"
    )
    LOGGER.info("operator_funnel_page_viewed", extra={"operator_user_id": operator_id})
    return web.Response(
        text=_layout(body),
        content_type="text/html",
        headers=_PAGE_HEADERS,
    )


def setup_operator_funnel_routes(app: web.Application) -> None:
    app.router.add_get("/api/operator/funnel", operator_funnel_json)
    app.router.add_get("/operator/funnel", operator_funnel_page)


__all__ = ["setup_operator_funnel_routes"]
