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
from sqlalchemy import select

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
_REVIEW_STATES = frozenset({"ready_for_review", "approved", "rejected"})
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
    raw = request.query.get(name)
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


def _json_text(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    except (TypeError, ValueError):
        return "{}"


async def _effective_review(database, row: WidgetPatternVersion) -> str:
    return await PatternCandidateRepository(database).effective_review_state(row.id)


async def _sync_builtin_registry(request: web.Request) -> None:
    registry = load_builtin_atomic_registry()
    async with session_scope(request.app) as database:
        await PatternCandidateRepository(database).sync_registry(registry)


async def _rows(request: web.Request) -> list[tuple[WidgetPatternVersion, str]]:
    category = _query_filter(request, "category", _CATEGORIES)
    status = _query_filter(request, "status", _STATUSES)
    review = _query_filter(request, "review", _REVIEW_STATES)
    await _sync_builtin_registry(request)
    factory = get_session_factory(request.app)
    async with factory() as database:
        statement = select(WidgetPatternVersion).order_by(
            WidgetPatternVersion.category,
            WidgetPatternVersion.pattern_id,
            WidgetPatternVersion.version,
        )
        if category is not None:
            statement = statement.where(WidgetPatternVersion.category == category)
        if status is not None:
            statement = statement.where(WidgetPatternVersion.status == status)
        records = list((await database.scalars(statement)).all())
        result: list[tuple[WidgetPatternVersion, str]] = []
        for row in records:
            effective = await _effective_review(database, row)
            if review is None or effective == review:
                result.append((row, effective))
        return result


def _list_content(rows: list[tuple[WidgetPatternVersion, str]], request: web.Request) -> str:
    query = "&".join(
        f"{escape(str(key))}={escape(str(value))}"
        for key, value in request.query.items()
        if key in {"category", "status", "review"}
    )
    suffix = f"?{query}" if query else ""
    filter_form = (
        "<section class='card'><form method='get' class='stack'>"
        "<div class='column'><label>Category<select name='category'><option value=''>Все</option>"
        + "".join(
            f"<option value='{escape(value)}'>{escape(value)}</option>"
            for value in sorted(_CATEGORIES)
        )
        + "</select></label></div>"
        "<div class='column'><label>Status<select name='status'><option value=''>Все</option>"
        + "".join(
            f"<option value='{escape(value)}'>{escape(value)}</option>"
            for value in sorted(_STATUSES)
        )
        + "</select></label></div>"
        "<div class='column'><label>Review<select name='review'><option value=''>Все</option>"
        + "".join(
            f"<option value='{escape(value)}'>{escape(value)}</option>"
            for value in sorted(_REVIEW_STATES)
        )
        + "</select></label></div>"
        "<div class='column'><button type='submit'>Фильтровать</button></div>"
        "</form></section>"
    )
    if not rows:
        return filter_form + (
            "<section class='card notice'><strong>Нет совпадений.</strong> "
            "Измените фильтры или сначала синхронизируйте встроенный технический каталог.</section>"
        )
    rendered = []
    for row, effective in rows:
        title = _field(row, "title", row.pattern_id)
        description = _field(row, "ai_description", row.description)
        rendered.append(
            "<tr>"
            f"<td><a href='/admin/pattern-lab/{escape(row.pattern_id)}/{int(row.version)}'>"
            f"{escape(title)}</a><br><span class='muted'>{escape(row.pattern_id)}</span></td>"
            f"<td>{escape(row.category)}</td><td>{int(row.version)}</td>"
            f"<td>{escape(row.status)}</td><td>{escape(effective)}</td>"
            f"<td><div>{escape(description)}</div><code>{escape(row.implementation_sha256)}</code></td>"
            "</tr>"
        )
    return filter_form + (
        "<section class='card'><table><thead><tr>"
        "<th>Pattern</th><th>Category</th><th>Version</th><th>Status</th>"
        "<th>Effective review</th><th>AI description / hash</th>"
        f"</tr></thead><tbody>{''.join(rendered)}</tbody></table>"
        f"<p class='muted'>Текущая выборка: {len(rows)}. <a href='/admin/pattern-lab{suffix}'>Обновить</a></p>"
        "</section>"
    )


async def pattern_lab_index(request: web.Request) -> web.Response:
    await _require_pattern_admin(request)
    rows = await _rows(request)
    response = render_layout(
        "Pattern Lab",
        "<section class='card'><p class='eyebrow'>Technical admin surface</p>"
        "<h2>Atomic pattern catalog</h2>"
        "<p class='muted'>Здесь отображаются версии, которые реально видит selector."
        " Исходные assets берутся только из Git-каталога.</p></section>"
        + _list_content(rows, request),
        nav_extra="<a href='/admin/pattern-lab' class='current'>Pattern Lab</a>",
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
    button.addEventListener('click', function() {{ patternLabSend(button.dataset.patternCommand); }});
  }});
  window.addEventListener('message', function(event) {{
    if (!iframe || event.source !== iframe.contentWindow) return;
    const data = event.data;
    if (!data || data.protocol !== protocol || data.version !== version || !allowed.has(data.command)) return;
    const output = document.getElementById('pattern-events');
    if (output) output.textContent = data.command;
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
                )
            ).all()
        )
    token = await _csrf_token(request)
    snapshot = _snapshot(row)
    title = _field(row, "title", row.pattern_id)
    summary = _field(row, "summary", row.description)
    ai_description = _field(row, "ai_description", row.description)
    technical_contract = _field(row, "technical_contract")
    policy = _field(row, "adaptation_policy")
    provenance = snapshot.get("provenance", {})
    review_rows = "".join(
        "<tr>"
        f"<td>{escape(review.reviewer_email)}</td><td>{escape(review.status)}</td>"
        f"<td>{escape(review.comment)}</td><td>{escape(str(review.created_at))}</td></tr>"
        for review in reviews
    ) or "<tr><td colspan='4'>Рецензий пока нет.</td></tr>"
    buttons = "".join(
        f"<button type='button' data-pattern-command='{escape(command)}'>{escape(command)}</button>"
        for command in _COMMANDS
    )
    path = f"/admin/pattern-lab/{escape(row.pattern_id)}/{int(row.version)}"
    content = (
        "<section class='card'>"
        "<p><a href='/admin/pattern-lab'>← К каталогу</a></p>"
        f"<p class='eyebrow'>{escape(row.category)} · v{int(row.version)}</p>"
        f"<h2>{escape(title)}</h2><p>{escape(summary)}</p>"
        f"<p><strong>Effective review:</strong> {escape(effective)} · "
        f"<strong>Lifecycle:</strong> {escape(row.status)}</p>"
        f"<h3>AI description</h3><p>{escape(ai_description)}</p>"
        f"<h3>Technical contract</h3><p>{escape(technical_contract)}</p>"
        f"<h3>Adaptation policy</h3><p>{escape(policy)}</p>"
        f"<h3>Implementation hash</h3><code>{escape(row.implementation_sha256)}</code>"
        f"<h3>Provenance</h3><pre>{escape(_json_text(provenance))}</pre>"
        f"<h3>Selector manifest snapshot</h3><pre>{escape(_json_text(snapshot))}</pre>"
        "</section>"
        "<section class='card'><h2>Sandbox preview</h2>"
        f"<iframe id='pattern-preview' class='preview-frame' sandbox=\"allow-scripts\" "
        f"src='{path}/preview' title='Pattern preview'></iframe>"
        f"<div class='actions'>{buttons}</div><p id='pattern-events' class='muted'>Нет событий</p>"
        "</section>"
        "<section class='card'><h2>Append review</h2>"
        f"<form method='post' action='{path}/review'>"
        f"<input type=\"hidden\" name=\"csrf_token\" value=\"{escape(token)}\">"
        "<label>Status<select name='status' required><option value='approved'>approved</option>"
        "<option value='rejected'>rejected</option></select></label>"
        "<label>Comment<textarea name='comment' maxlength='4000'></textarea></label>"
        "<button type='submit'>Append review</button></form></section>"
        "<section class='card'><h2>Review history</h2><table><thead><tr>"
        "<th>Reviewer</th><th>Status</th><th>Comment</th><th>Created</th>"
        f"</tr></thead><tbody>{review_rows}</tbody></table></section>"
        + _parent_bridge()
    )
    response = render_layout(f"Pattern {title}", content)
    response.headers.update(_NO_STORE)
    return response


_PREVIEW_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'none'; img-src 'none'; font-src 'none'; media-src 'none'; "
    "worker-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'"
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
    document.documentElement.dataset.patternCommand = data.command;
    window.parent.postMessage({{protocol, version, command: data.command}}, '*');
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
