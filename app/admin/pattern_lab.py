"""Technical, admin-only Pattern Lab for immutable atomic pattern versions.

This module deliberately keeps the UI small.  The page is a provenance
inspector and a safe preview surface, not a second Studio.  Pattern assets are
always resolved from the checked-in registry; database rows provide the
identity/hash and review history used by the admin surface.
"""

from __future__ import annotations

import json
import secrets
from html import escape
from typing import Any

from aiohttp import web
from aiohttp_session import get_session
from sqlalchemy import func, select

from app.admin.auth import require_admin_session
from app.admin.layout import render_layout
from app.db.session import get_session_factory, session_scope
from app.patterns.candidate_repository import PatternCandidateRepository
from app.saas.models import PatternReview, WidgetPatternVersion
from builder_lab.patterns.atomic_models import (
    AtomicPatternCategory,
    AtomicPatternStatus,
)
from builder_lab.patterns.atomic_registry import load_builtin_atomic_registry
from core.config import settings


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_CSRF_SESSION_KEY = "pattern_lab_csrf"
_CSRF_MIN_LENGTH = 32
_MAX_COMMENT = 4_000
_MAX_FILTER_LENGTH = 64
_MAX_REVIEW_HISTORY = 100
_REVIEW_STATES = frozenset({"ready_for_review", "approved", "rejected"})
_REVIEW_FILTERS = _REVIEW_STATES | {"all"}
_STATUSES = frozenset(item.value for item in AtomicPatternStatus)
_CATEGORIES = frozenset(item.value for item in AtomicPatternCategory)
_COMMANDS = (
    "run",
    "replay",
    "open",
    "close",
    "assistant-message",
    "user-message",
    "typing",
    "desktop",
    "mobile",
)
_CATEGORY_LABELS = {
    AtomicPatternCategory.LAUNCHER_SHAPE.value: "Форма кнопки открытия",
    AtomicPatternCategory.LAUNCHER_IDLE.value: "Спокойная анимация кнопки",
    AtomicPatternCategory.LAUNCHER_ATTENTION.value: "Привлечение внимания",
    AtomicPatternCategory.SHELL_LAYOUT.value: "Компоновка окна чата",
    AtomicPatternCategory.WIDGET_OPEN.value: "Открытие виджета",
    AtomicPatternCategory.WIDGET_CLOSE.value: "Закрытие виджета",
    AtomicPatternCategory.BACKGROUND_EFFECT.value: "Фоновый эффект",
    AtomicPatternCategory.ASSISTANT_MESSAGE_ENTER.value: "Появление ответа AI",
    AtomicPatternCategory.USER_MESSAGE_ENTER.value: "Появление сообщения пользователя",
    AtomicPatternCategory.TYPING_INDICATOR.value: "Индикатор набора",
    AtomicPatternCategory.MESSAGE_SEND.value: "Отправка сообщения",
    AtomicPatternCategory.COMPOSER_FOCUS.value: "Поле ввода в фокусе",
    AtomicPatternCategory.CONTROL_HOVER.value: "Наведение на кнопки",
    AtomicPatternCategory.RESPONSIVE_TRANSITION.value: "Адаптация desktop/mobile",
}
_REVIEW_LABELS = {
    "ready_for_review": "Ждёт проверки",
    "approved": "Одобрено",
    "rejected": "Отклонено",
    "all": "Все версии",
}
_CATEGORY_COMMANDS = {
    AtomicPatternCategory.WIDGET_OPEN.value: ("open",),
    AtomicPatternCategory.WIDGET_CLOSE.value: ("close",),
    AtomicPatternCategory.ASSISTANT_MESSAGE_ENTER.value: ("assistant-message",),
    AtomicPatternCategory.USER_MESSAGE_ENTER.value: ("user-message",),
    AtomicPatternCategory.TYPING_INDICATOR.value: ("typing",),
}


def _allowlisted(email: str) -> bool:
    configured = getattr(settings, "ADMIN_EMAILS", ())
    try:
        allowlist = {
            value.strip().casefold()
            for value in configured
            if isinstance(value, str) and value.strip()
        }
    except TypeError:
        allowlist = set()
    return bool(allowlist) and email.casefold() in allowlist


async def _require_pattern_admin(request: web.Request) -> tuple[str, str]:
    email, tenant = await require_admin_session(request)
    # The legacy admin gate intentionally remains permissive for existing
    # routes. Pattern Lab is a new technical surface and fails closed when the
    # explicit global allowlist is absent.
    if not _allowlisted(email):
        raise web.HTTPForbidden(headers=_NO_STORE)
    return email, tenant


def _version(request: web.Request) -> int:
    raw = request.match_info.get("version", "")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(headers=_NO_STORE) from exc
    if isinstance(value, bool) or not 1 <= value <= 9_999:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    return value


def _query_filter(request: web.Request, name: str, allowed: frozenset[str]) -> str | None:
    values = request.query.getall(name, [])
    if len(values) > 1:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    raw = values[0] if values else None
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or len(raw) > _MAX_FILTER_LENGTH or raw not in allowed:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    return raw


def _snapshot(row: WidgetPatternVersion) -> dict[str, Any]:
    value = row.manifest_snapshot
    return dict(value) if isinstance(value, dict) else {}


def _field(row: WidgetPatternVersion, name: str, fallback: str = "") -> str:
    value = _snapshot(row).get(name)
    if isinstance(value, str):
        return value
    return fallback


async def _effective_review(database, row: WidgetPatternVersion) -> str:
    return await PatternCandidateRepository(database).effective_review_state(row.id)


async def _sync_builtin_registry(request: web.Request) -> None:
    registry = load_builtin_atomic_registry()
    async with session_scope(request.app) as database:
        await PatternCandidateRepository(database).sync_registry(registry)


async def _rows(
    request: web.Request,
) -> tuple[
    list[tuple[WidgetPatternVersion, str]],
    list[tuple[WidgetPatternVersion, str]],
]:
    category = _query_filter(request, "category", _CATEGORIES)
    status = _query_filter(request, "status", _STATUSES)
    review = _query_filter(request, "review", _REVIEW_FILTERS)
    if review is None:
        review = "ready_for_review"
    effective_review_filter = None if review == "all" else review
    await _sync_builtin_registry(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        statement = select(WidgetPatternVersion).order_by(
            WidgetPatternVersion.category,
            WidgetPatternVersion.pattern_id,
            WidgetPatternVersion.version,
        )
        records = list((await database.scalars(statement)).all())
        latest_reviews: dict[object, str] = {}
        if records:
            review_ranked = (
                select(
                    PatternReview.pattern_version_id,
                    PatternReview.status,
                    func.row_number()
                    .over(
                        partition_by=PatternReview.pattern_version_id,
                        order_by=(
                            PatternReview.created_at.desc(),
                            PatternReview.id.desc(),
                        ),
                    )
                    .label("review_rank"),
                )
                .where(
                    PatternReview.pattern_version_id.in_([row.id for row in records])
                )
                .subquery()
            )
            review_rows = (
                await database.execute(
                    select(
                        review_ranked.c.pattern_version_id,
                        review_ranked.c.status,
                    ).where(review_ranked.c.review_rank == 1)
                )
            ).all()
            for review_row in review_rows:
                latest_reviews.setdefault(
                    review_row.pattern_version_id, review_row.status
                )
        catalog: list[tuple[WidgetPatternVersion, str]] = []
        for row in records:
            effective = latest_reviews.get(row.id)
            if effective is None:
                provenance = _snapshot(row).get("provenance")
                if isinstance(provenance, dict) and provenance.get(
                    "review_state"
                ) in _REVIEW_STATES:
                    effective = str(provenance["review_state"])
                else:
                    effective = "ready_for_review"
            catalog.append((row, effective))
        result = [
            (row, effective)
            for row, effective in catalog
            if (category is None or row.category == category)
            and (status is None or row.status == status)
            and (effective_review_filter is None or effective == effective_review_filter)
        ]
        return result, catalog


def _selected_option(value: str, current: str | None) -> str:
    selected = " selected" if value == current else ""
    label = _CATEGORY_LABELS.get(value) or _REVIEW_LABELS.get(value) or value
    return f"<option value='{escape(value)}'{selected}>{escape(label)}</option>"


def _coverage_content(
    catalog: list[tuple[WidgetPatternVersion, str]],
) -> str:
    approved = sum(effective == "approved" for _row, effective in catalog)
    ready = sum(effective == "ready_for_review" for _row, effective in catalog)
    rejected = sum(effective == "rejected" for _row, effective in catalog)
    represented = len({row.category for row, _effective in catalog})
    empty = len(_CATEGORIES) - represented
    return (
        "<section class='card pattern-coverage'><p class='eyebrow'>Библиотека эффектов</p>"
        "<h2>Шаблоны для будущих виджетов</h2>"
        "<p class='muted'>Новый эффект сначала проверяется здесь. После одобрения "
        "генератор сможет предлагать его для подходящих виджетов.</p>"
        "<div class='stack'>"
        f"<div class='column'><strong>{len(catalog)}</strong><br><span class='muted'>всего версий</span></div>"
        f"<div class='column'><strong>{represented} категорий</strong><br><span class='muted'>{empty} без вариантов</span></div>"
        f"<div class='column'><strong>{approved}</strong><br><span class='muted'>одобрено</span></div>"
        f"<div class='column'><strong>{ready}</strong><br><span class='muted'>нужно проверить</span></div>"
        f"<div class='column'><strong>{rejected}</strong><br><span class='muted'>отклонено</span></div>"
        "</div><div class='actions'>"
        "<a href='/admin/pattern-lab?review=ready_for_review'>Нужно проверить →</a>"
        "<a href='/admin/pattern-lab?review=approved'>Одобренные</a>"
        "</div></section>"
    )


def _list_content(
    rows: list[tuple[WidgetPatternVersion, str]],
    catalog: list[tuple[WidgetPatternVersion, str]],
    request: web.Request,
) -> str:
    category_filter = request.query.get("category") or None
    review_filter = request.query.get("review") or "ready_for_review"
    query = "&".join(
        f"{escape(str(key))}={escape(str(value))}"
        for key, value in request.query.items()
        if key in {"category", "status", "review"}
    )
    suffix = f"?{query}" if query else ""
    filter_form = (
        "<section class='card pattern-filter-card'><form method='get' class='stack pattern-filters'>"
        "<div class='column'><label>Категория<select name='category'><option value=''>Все категории</option>"
        + "".join(
            _selected_option(value, category_filter)
            for value in sorted(_CATEGORIES, key=lambda item: _CATEGORY_LABELS[item])
        )
        + "</select></label></div>"
        "<div class='column'><label>Состояние<select name='review'>"
        + "".join(
            _selected_option(value, review_filter)
            for value in ("ready_for_review", "approved", "rejected", "all")
        )
        + "</select></label></div>"
        "<div class='column filter-action'><button type='submit'>Показать</button></div>"
        "</form></section>"
    )
    if not rows:
        return _coverage_content(catalog) + filter_form + (
            "<section class='card notice'><strong>Нет совпадений.</strong> "
            "Измените фильтры или добавьте новую версию в Git-каталог.</section>"
        )
    rendered = []
    for row, effective in rows:
        title = _field(row, "title", row.pattern_id)
        description = _field(row, "ai_description", row.description)
        rendered.append(
            "<article class='pattern-row'>"
            "<div class='pattern-copy'>"
            f"<h3>{escape(title)}</h3>"
            f"<p><span class='field-label'>Описание для AI</span>{escape(description)}</p>"
            "</div><div class='pattern-row-actions'>"
            f"<span class='category-label'>{escape(_CATEGORY_LABELS[row.category])}</span>"
            f"<span class='review-state review-{escape(effective)}'>{escape(_REVIEW_LABELS[effective])}</span>"
            f"<a class='review-link' href='/admin/pattern-lab/{escape(row.pattern_id)}/{int(row.version)}'>"
            "Проверить →</a></div></article>"
        )
    return _coverage_content(catalog) + filter_form + (
        "<style>"
        ".pattern-filter-card{padding-block:18px}.pattern-filters{align-items:end}"
        ".pattern-filters label{margin-bottom:0}.pattern-filters .filter-action{flex:0 0 auto;min-width:auto}"
        ".pattern-list{padding:0!important;overflow:hidden}.pattern-list-header{padding:24px 24px 8px}"
        ".pattern-row{display:grid;grid-template-columns:minmax(0,1fr) 240px;gap:28px;padding:24px;border-top:1px solid #e5e7eb}"
        ".pattern-copy h3{margin:0 0 12px;font-size:18px;line-height:1.25}.pattern-copy p{max-width:72ch;margin:0;color:#4b5563;line-height:1.6}"
        ".field-label{display:block;margin-bottom:5px;color:#6b7280;font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}"
        ".pattern-row-actions{display:flex;align-content:flex-start;align-items:flex-start;justify-content:flex-start;gap:8px;flex-wrap:wrap}"
        ".category-label,.review-state{display:inline-flex;padding:6px 9px;border-radius:8px;background:#f3f4f6;color:#374151;font-size:12px;font-weight:700}"
        ".review-ready_for_review{background:#fff7ed;color:#9a3412}.review-approved{background:#ecfdf5;color:#047857}.review-rejected{background:#fef2f2;color:#b91c1c}"
        ".review-link{width:100%;margin-top:8px;font-weight:700;text-decoration:none}"
        "@media(max-width:760px){.pattern-row{grid-template-columns:1fr;gap:16px;padding:20px}.pattern-list-header{padding:20px 20px 6px}}"
        "</style>"
        "<section class='card pattern-list'><div class='pattern-list-header'>"
        f"<h2>{escape(_REVIEW_LABELS[review_filter]) if review_filter in _REVIEW_LABELS else 'Шаблоны'}</h2>"
        f"<p class='muted'>Найдено: {len(rows)}. <a href='/admin/pattern-lab{suffix}'>Обновить</a></p></div>"
        f"<div>{''.join(rendered)}</div></section>"
    )


async def pattern_lab_index(request: web.Request) -> web.Response:
    await _require_pattern_admin(request)
    rows, catalog = await _rows(request)
    response = render_layout(
        "Лаборатория шаблонов",
        _list_content(rows, catalog, request),
        nav_extra="<a href='/admin/pattern-lab' class='current'>Шаблоны</a>",
    )
    response.headers.update(_NO_STORE)
    return response


async def _load_row(request: web.Request) -> WidgetPatternVersion:
    pattern_id = request.match_info.get("pattern_id", "")
    if not pattern_id or len(pattern_id) > 80:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    version = _version(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        row = await database.scalar(
            select(WidgetPatternVersion).where(
                WidgetPatternVersion.pattern_id == pattern_id,
                WidgetPatternVersion.version == version,
            )
        )
        if row is None:
            raise web.HTTPNotFound(headers=_NO_STORE)
        # Detach a plain value snapshot before the session closes; SQLAlchemy
        # rows are expire_on_commit=False in production but not guaranteed in
        # every disposable test application.
        row.manifest_snapshot = _snapshot(row)
        return row


async def _csrf_token(request: web.Request) -> str:
    session = await get_session(request)
    token = session.get(_CSRF_SESSION_KEY)
    if not isinstance(token, str) or len(token) < _CSRF_MIN_LENGTH:
        token = secrets.token_urlsafe(32)
        session[_CSRF_SESSION_KEY] = token
    return token


def _parent_bridge() -> str:
    commands = json.dumps(_COMMANDS, ensure_ascii=False, separators=(",", ":"))
    return f"""
<script>
(function() {{
  const iframe = document.getElementById('pattern-preview');
  const allowed = new Set({commands});
  const protocol = 'kaigo.pattern-lab';
  const version = 1;
  window.patternLabSend = function(command, payload) {{
    if (!allowed.has(command) || !iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage({{protocol, version, command, payload: payload || {{}}}}, '*');
  }};
  document.querySelectorAll('[data-pattern-command]').forEach(function(button) {{
    button.addEventListener('click', function() {{
      const command = button.dataset.patternCommand;
      if (command === 'mobile' || command === 'desktop') {{
        iframe.dataset.viewport = command;
        document.querySelectorAll('[data-viewport-control]').forEach(function(control) {{
          const selected = control.dataset.patternCommand === command;
          control.classList.toggle('is-active', selected);
          control.setAttribute('aria-pressed', selected ? 'true' : 'false');
        }});
      }} else {{
        const output = document.getElementById('pattern-events');
        if (output) output.textContent = 'Перезапускаем…';
      }}
      patternLabSend(command);
    }});
  }});
  window.addEventListener('message', function(event) {{
    if (!iframe || event.source !== iframe.contentWindow) return;
    const data = event.data;
    if (!data || data.protocol !== protocol || data.version !== version || !allowed.has(data.command)) return;
    const output = document.getElementById('pattern-events');
    if (!output) return;
    if (data.command === 'desktop') output.textContent = 'Режим предпросмотра: desktop';
    else if (data.command === 'mobile') output.textContent = 'Режим предпросмотра: mobile';
    else output.textContent = 'Анимация воспроизведена';
  }});
}})();
</script>"""


async def pattern_lab_detail(request: web.Request) -> web.Response:
    await _require_pattern_admin(request)
    row = await _load_row(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        effective = await _effective_review(database, row)
        reviews = list(
            (
                await database.scalars(
                    select(PatternReview)
                    .where(PatternReview.pattern_version_id == row.id)
                    .order_by(PatternReview.created_at.desc(), PatternReview.id.desc())
                    .limit(_MAX_REVIEW_HISTORY + 1)
                )
            ).all()
        )
    review_history_truncated = len(reviews) > _MAX_REVIEW_HISTORY
    reviews = reviews[:_MAX_REVIEW_HISTORY]
    token = await _csrf_token(request)
    title = _field(row, "title", row.pattern_id)
    summary = _field(row, "summary", row.description)
    ai_description = _field(row, "ai_description", row.description)
    review_rows = "".join(
        "<tr>"
        f"<td>{escape(review.reviewer_email)}</td><td>{escape(_REVIEW_LABELS.get(review.status, review.status))}</td>"
        f"<td>{escape(review.comment)}</td><td>{escape(str(review.created_at))}</td></tr>"
        for review in reviews
    ) or "<tr><td colspan='4'>Рецензий пока нет.</td></tr>"
    review_history_note = (
        "<p class='muted'>Показаны последние 100 проверок. Более ранние решения "
        "сохранены в истории.</p>"
        if review_history_truncated
        else ""
    )
    replay_command = _CATEGORY_COMMANDS.get(row.category, ("replay",))[0]
    buttons = (
        f"<button class='replay-control' type='button' data-pattern-command='{escape(replay_command)}'>"
        "Повторить анимацию</button>"
        "<div class='viewport-controls' role='group' aria-label='Размер предпросмотра'>"
        "<button class='viewport-control is-active' type='button' data-viewport-control "
        "data-pattern-command='desktop' aria-pressed='true'>Desktop</button>"
        "<button class='viewport-control' type='button' data-viewport-control "
        "data-pattern-command='mobile' aria-pressed='false'>Mobile</button></div>"
    )
    path = f"/admin/pattern-lab/{escape(row.pattern_id)}/{int(row.version)}"
    content = (
        "<style>"
        ".pattern-lab-detail{max-width:1180px;margin-inline:auto}.pattern-lab-detail .intro{padding-bottom:20px}"
        ".detail-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.detail-heading h2{margin-bottom:8px}"
        ".detail-description{max-width:72ch;line-height:1.65;color:#374151}.detail-description .field-label{display:block;margin-bottom:6px;color:#6b7280;font-size:11px;font-weight:800;letter-spacing:.08em;text-transform:uppercase}"
        ".preview-frame[data-viewport='mobile']{display:block;width:390px;max-width:100%;margin-left:auto;margin-right:auto}.preview-frame[data-viewport='desktop']{width:100%}"
        ".preview-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:16px}"
        ".viewport-controls{display:inline-flex;padding:3px;border-radius:10px;background:#f3f4f6}.viewport-control{background:transparent;color:#4b5563;padding:7px 11px}.viewport-control:hover{background:#e5e7eb}.viewport-control.is-active{background:#fff;color:#111827;box-shadow:0 1px 4px rgba(15,23,42,.12)}"
        ".replay-control{min-height:44px}.review-current{margin-top:8px}"
        "@media(max-width:720px){.detail-heading{display:block}.preview-toolbar{align-items:stretch}.replay-control{width:100%}.viewport-controls{justify-content:center}}"
        "</style>"
        "<div class='pattern-lab-detail'>"
        "<section class='card intro'>"
        "<p><a href='/admin/pattern-lab'>← К каталогу</a></p>"
        f"<p class='eyebrow'>{escape(_CATEGORY_LABELS[row.category])}</p>"
        "<div class='detail-heading'><div>"
        f"<h2>{escape(title)}</h2><p>{escape(summary)}</p></div>"
        f"<span class='review-state review-{escape(effective)}'>{escape(_REVIEW_LABELS[effective])}</span></div>"
        f"<p class='detail-description'><span class='field-label'>Описание для AI</span>{escape(ai_description)}</p>"
        "</section>"
        "<section class='card'><h2>Предпросмотр</h2>"
        "<p class='muted'>Повторите эффект в любой момент или проверьте его на другой ширине.</p>"
        f"<iframe id='pattern-preview' class='preview-frame' data-viewport='desktop' sandbox=\"allow-scripts\" "
        f"src='{path}/preview' title='Предпросмотр шаблона'></iframe>"
        f"<div class='preview-toolbar'>{buttons}</div><p id='pattern-events' class='muted'>Готово к проверке</p>"
        "</section>"
        "<section class='card'><h2>Решение по шаблону</h2>"
        "<p class='muted'>Одобрите эффект или оставьте конкретное замечание для следующей версии.</p>"
        f"<form method='post' action='{path}/review'>"
        f"<input type=\"hidden\" name=\"csrf_token\" value=\"{escape(token)}\">"
        "<label>Решение<select name='status' required><option value='approved'>Одобрить</option>"
        "<option value='rejected'>Отклонить</option></select></label>"
        "<label>Комментарий<textarea name='comment' maxlength='4000' placeholder='Что понравилось или что нужно изменить'></textarea></label>"
        "<button type='submit'>Сохранить решение</button></form></section>"
        "<section class='card'><h2>История проверок</h2><table><thead><tr>"
        "<th>Кто проверил</th><th>Решение</th><th>Комментарий</th><th>Дата</th>"
        f"</tr></thead><tbody>{review_rows}</tbody></table>{review_history_note}</section></div>"
        + _parent_bridge()
    )
    response = render_layout(
        f"Проверка: {title}",
        content,
        nav_extra="<a href='/admin/pattern-lab' class='current'>Шаблоны</a>",
    )
    response.headers.update(_NO_STORE)
    return response


_PREVIEW_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'none'; img-src 'none'; font-src 'none'; media-src 'none'; "
    "worker-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'; "
    "object-src 'none'; sandbox allow-scripts; frame-ancestors 'self'"
)


def _preview_bridge() -> str:
    commands = json.dumps(_COMMANDS, ensure_ascii=False, separators=(",", ":"))
    return f"""
<script>
(function() {{
  const allowed = new Set({commands});
  const protocol = 'kaigo.pattern-lab';
  const version = 1;
  window.addEventListener('message', function(event) {{
    if (event.source !== window.parent) return;
    const data = event.data;
    if (!data || data.protocol !== protocol || data.version !== version || !allowed.has(data.command)) return;
    const root = document.documentElement;
    delete root.dataset.patternCommand;
    void document.documentElement.offsetWidth;
    root.dataset.patternCommand = data.command;
    requestAnimationFrame(function() {{
      window.parent.postMessage({{protocol, version, command: data.command}}, '*');
    }});
  }});
}})();
</script>"""


async def pattern_lab_preview(request: web.Request) -> web.Response:
    await _require_pattern_admin(request)
    row = await _load_row(request)
    try:
        definition = load_builtin_atomic_registry().resolve(row.pattern_id, row.version)
    except Exception as exc:  # registry errors fail closed for preview
        raise web.HTTPNotFound(headers=_NO_STORE) from exc
    if row.implementation_sha256 != definition.implementation_sha256:
        raise web.HTTPConflict(headers=_NO_STORE)
    assets = definition.implementation_dict()
    html = assets["html"]
    css = assets["css"]
    javascript = assets["javascript"]
    document = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(definition.title)}</title><style>{css}</style></head>"
        f"<body>{html}<script>{javascript}</script>{_preview_bridge()}</body></html>"
    )
    return web.Response(
        text=document,
        content_type="text/html",
        headers={**_NO_STORE, "Content-Security-Policy": _PREVIEW_CSP, "X-Content-Type-Options": "nosniff"},
    )


async def pattern_lab_review(request: web.Request) -> web.StreamResponse:
    reviewer_email, _tenant = await _require_pattern_admin(request)
    token = (await get_session(request)).get(_CSRF_SESSION_KEY)
    data = await request.post()
    supplied = data.get("csrf_token")
    if (
        not isinstance(token, str)
        or not isinstance(supplied, str)
        or len(token) < _CSRF_MIN_LENGTH
        or not secrets.compare_digest(token, supplied)
    ):
        raise web.HTTPForbidden(headers=_NO_STORE)
    status = data.get("status")
    comment = data.get("comment", "")
    if not isinstance(status, str) or status not in {"approved", "rejected"}:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    if not isinstance(comment, str) or len(comment) > _MAX_COMMENT or "\x00" in comment:
        raise web.HTTPBadRequest(headers=_NO_STORE)
    row = await _load_row(request)
    factory = get_session_factory(request.app)
    async with factory() as database, database.begin():
        await PatternCandidateRepository(database).append_review(
            row.id,
            reviewer_email,
            status,
            comment,
        )
    raise web.HTTPSeeOther(
        f"/admin/pattern-lab/{escape(row.pattern_id)}/{int(row.version)}"
    )


def setup_pattern_lab_routes(app: web.Application) -> None:
    app.router.add_get("/admin/pattern-lab", pattern_lab_index)
    app.router.add_get(
        "/admin/pattern-lab/{pattern_id}/{version}", pattern_lab_detail
    )
    app.router.add_get(
        "/admin/pattern-lab/{pattern_id}/{version}/preview", pattern_lab_preview
    )
    app.router.add_post(
        "/admin/pattern-lab/{pattern_id}/{version}/review", pattern_lab_review
    )


__all__ = [
    "pattern_lab_detail",
    "pattern_lab_index",
    "pattern_lab_preview",
    "pattern_lab_review",
    "setup_pattern_lab_routes",
]
