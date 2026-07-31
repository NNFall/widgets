from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html import escape
import logging

from aiohttp import web
from aiohttp_session import get_session

from app.admin.layout import render_layout
from app.admin.operator_auth import require_verified_operator
from app.analytics.reporting import build_funnel_report, serialize_funnel_report
from app.db.session import get_session_factory


LOGGER = logging.getLogger(__name__)
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_STAGE_LABELS = {
    "landing_entered": "Открыли лендинг",
    "authenticated_project": "Авторизовались и создали проект",
    "run_queued": "Запустили генерацию",
    "free_result": "Получили бесплатный результат",
    "payment_completed": "Оплатили",
    "published": "Опубликовали виджет",
    "upgrade_started": "Начали оформление тарифа",
}


async def _require_global_operator(request: web.Request) -> int:
    session = await get_session(request)
    if not isinstance(session.get("user_id"), int):
        raise web.HTTPFound("/admin/login")
    return await require_verified_operator(request)


def _days(request: web.Request) -> tuple[int, int]:
    retention_days = request.app["config"].funnel_retention_days
    raw = request.query.get("days", "30")
    try:
        days = int(raw)
    except (TypeError, ValueError) as error:
        raise web.HTTPBadRequest(headers=_NO_STORE) from error
    if isinstance(retention_days, bool) or not 7 <= days <= retention_days:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    return days, retention_days


async def _payload(request: web.Request) -> tuple[int, dict[str, object]]:
    operator_id = await _require_global_operator(request)
    days, retention_days = _days(request)
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    factory = get_session_factory(request.app)
    async with factory() as database:
        report = await build_funnel_report(database, start=start, end=end)
    payload = serialize_funnel_report(
        report,
        days=days,
        retention_days=retention_days,
    )
    return operator_id, payload


async def funnel_report_api(request: web.Request) -> web.Response:
    operator_id, payload = await _payload(request)
    LOGGER.info("admin_funnel_report_viewed", extra={"operator_user_id": operator_id})
    return web.json_response(payload, headers=_NO_STORE)


def _percent(value: object) -> str:
    if value is None:
        return "—"
    return f"{int(value) / 100:.2f}%"


def _stage_table(title: str, rows: list[dict[str, object]]) -> str:
    body = "".join(
        "<tr>"
        f"<td>{escape(_STAGE_LABELS.get(str(row['stage']), str(row['stage'])))}</td>"
        f"<td>{int(row['journeys'])}</td>"
        f"<td>{_percent(row['from_entry_bps'])}</td>"
        "</tr>"
        for row in rows
    )
    return (
        f"<h2>{escape(title)}</h2>"
        "<table><thead><tr><th>Этап</th><th>Уникальные пути</th>"
        f"<th>От входа</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _campaign_table(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "<h2>Кампании</h2><p>За выбранный период данных нет.</p>"

    def label(value: object) -> str:
        return escape(str(value)) if value is not None else "—"

    body = "".join(
        "<tr>"
        f"<td>{label(row['source'])}</td><td>{label(row['medium'])}</td>"
        f"<td>{label(row['campaign'])}</td><td>{label(row['term'])}</td>"
        f"<td>{label(row['content'])}</td>"
        f"<td>{int(row['landing_entered'])}</td>"
        f"<td>{int(row['authenticated_project'])}</td>"
        f"<td>{int(row['run_queued'])}</td><td>{int(row['free_result'])}</td>"
        f"<td>{int(row['payment_completed'])}</td><td>{int(row['published'])}</td>"
        "</tr>"
        for row in rows
    )
    return (
        "<h2>Кампании</h2><table><thead><tr><th>Источник</th><th>Канал</th>"
        "<th>Кампания</th><th>Термин</th><th>Контент</th><th>Вход</th>"
        "<th>Проект</th><th>Запуск</th><th>Результат</th><th>Оплата</th>"
        f"<th>Публикация</th></tr></thead><tbody>{body}</tbody></table>"
    )


async def funnel_report_page(request: web.Request) -> web.Response:
    operator_id, payload = await _payload(request)
    window = payload["window"]
    core = payload["core"]
    commercial = payload["commercial"]
    campaigns = payload["campaigns"]
    assert isinstance(window, dict)
    assert isinstance(core, list)
    assert isinstance(commercial, list)
    assert isinstance(campaigns, list)
    empty = not any(int(row["journeys"]) for row in core)
    empty_state = (
        "<p><strong>За выбранный период данных нет.</strong> "
        "Конверсия появится после первого зафиксированного пути.</p>"
        if empty
        else ""
    )
    content = (
        "<section class='card'>"
        "<p class='eyebrow'>Aggregate only</p>"
        f"<p>Окно UTC: {escape(str(window['from']))} — {escape(str(window['to']))}. "
        f"Сырые данные хранятся не более {int(window['retention_days'])} дней.</p>"
        f"{empty_state}{_stage_table('Основная воронка', core)}"
        f"{_stage_table('Коммерческие этапы', commercial)}"
        f"{_campaign_table(campaigns)}</section>"
    )
    response = render_layout("Воронка", content)
    response.headers.update(_NO_STORE)
    LOGGER.info("admin_funnel_page_viewed", extra={"operator_user_id": operator_id})
    return response


def setup_funnel_admin_routes(app: web.Application) -> None:
    app.router.add_get("/admin/api/funnel", funnel_report_api)
    app.router.add_get("/admin/funnel", funnel_report_page)


__all__ = ["setup_funnel_admin_routes"]
