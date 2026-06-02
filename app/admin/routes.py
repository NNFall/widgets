from __future__ import annotations

from html import escape

from typing import Dict, Tuple

from aiohttp import web

from aiohttp_session import get_session

from app.admin.auth import SESSION_EMAIL_KEY, SESSION_TENANT_KEY, login_page, login_submit, logout
from app.admin.layout import render_layout as _render_layout
from app.admin.tenants import setup_tenant_admin_routes

from app.db import models

from app.db.repositories import TenantRepository, WidgetRepository, WidgetAssetRepository, WidgetBindingRepository

from app.db.session import session_scope

from core.config import settings as core_settings

from core import database as history_db

from app.widgets.templates import DEFAULT_TEMPLATE_KEY, TEMPLATES, get_template_html

def _default_widget_assets(template_key: str) -> Tuple[str, str | None, str | None]:

    return get_template_html(template_key), None, None

def _adjust_preview_urls(html: str, widget_slug: str) -> str:
    for endpoint in ('send', 'history', 'audio'):
        path = f'/api/{endpoint}'
        full_path = f'/w/{widget_slug}{path}'
        token = f'__KAIGO_PREVIEW_{endpoint.upper()}__'
        html = html.replace(full_path, token)
        html = html.replace(path, full_path)
        html = html.replace(token, full_path)
    return html

async def _collect_asset_fields(request: web.Request) -> dict[str, str]:

    fields: dict[str, str] = {"html": "", "css": "", "js": ""}

    content_type = request.content_type or ""

    if content_type.startswith('multipart/'):

        reader = await request.multipart()

        while True:

            part = await reader.next()

            if part is None:

                break

            name = part.name or ''

            if name in {'html', 'css', 'js'}:

                value = await part.text()

                fields[name] = value.strip()

            elif name in {'html_file', 'css_file', 'js_file'} and part.filename:

                data = await part.read()

                try:

                    decoded = data.decode('utf-8')

                except UnicodeDecodeError:

                    decoded = data.decode('utf-8', errors='ignore')

                fields[name.replace('_file', '')] = decoded.strip()

    else:

        data = await request.post()

        for key in ('html', 'css', 'js'):

            value = data.get(key)

            if value:

                fields[key] = value.strip()

    return fields

async def _require_session(request: web.Request) -> tuple[str, str]:

    session = await get_session(request)

    email = session.get(SESSION_EMAIL_KEY)

    tenant_slug = session.get(SESSION_TENANT_KEY)

    if not email or not tenant_slug:

        raise web.HTTPFound('/admin/login')

    return email, tenant_slug

async def _get_tenant_id(request: web.Request, tenant_slug: str) -> int:

    async with session_scope(request.app) as db_session:

        tenant_repo = TenantRepository(db_session)

        tenant = await tenant_repo.get_by_slug(tenant_slug)

        if tenant is None:

            raise web.HTTPFound('/admin/login')

        return tenant.id

async def admin_index(request: web.Request) -> web.Response:

    email, tenant_slug = await _require_session(request)

    tenant_id = await _get_tenant_id(request, tenant_slug)

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        widgets = await widget_repo.list_for_tenant(tenant_id)

    rows = ''.join(

        f"<tr><td>{w.id}</td><td>{escape(w.name)}</td><td>{escape(w.slug)}</td>"

        f"<td>{escape(w.status)}</td><td>"

        f"<a href='/admin/widgets/{w.id}'>Обзор</a> | "

        f"<a href='/admin/widgets/{w.id}/edit'>Редактировать</a> | "

        f"<a href='/admin/widgets/{w.id}/assets'>Ассеты</a> | "

        f"<a href='/admin/widgets/{w.id}/dialogs'>Диалоги</a> | "

        f"<form class='inline' method='post' action='/admin/widgets/{w.id}/delete'>"

        f"<button type='submit'>Удалить</button></form></td></tr>"

        for w in widgets

    )

    table = f"""

    <section class='card'>

      <p class='muted'>Вы вошли как {escape(email)}, тенант {escape(tenant_slug)}</p>

      <p><a href='/admin/widgets/new' class='button'>Создать виджет</a></p>

      <table>

        <thead><tr><th>ID</th><th>Название</th><th>Slug</th><th>Статус</th><th>Действия</th></tr></thead>

        <tbody>{rows or '<tr><td colspan=5>Нет виджетов</td></tr>'}</tbody>

      </table>

    </section>

    """

    return _render_layout('Виджеты', table)

def _widget_form(widget: models.Widget | None = None) -> str:

    name = escape(widget.name) if widget else ''

    slug = escape(widget.slug) if widget else ''

    ai_model = escape(widget.ai_model) if widget else core_settings.default_model

    prompt_source = escape(widget.prompt_source or '') if widget else ''

    intro_text = escape(widget.intro_text or '') if widget else ''

    status = escape(widget.status if widget else 'draft')

    template = escape(widget.template) if widget else DEFAULT_TEMPLATE_KEY

    stt_model = escape(widget.stt_model or '') if widget else ''

    temperature = widget.temperature if widget else core_settings.default_temperature

    max_tokens = widget.max_tokens if widget else core_settings.default_max_tokens

    options = ''.join(

        f"<option value='{escape(key)}'{ ' selected' if key == template else ''}>{escape(key.title())}</option>"

        for key in TEMPLATES

    )

    return f"""

    <section class='card'>

      <form method='post'>

        <label>Название<input type='text' name='name' value='{name}' required></label>

        <label>Slug<input type='text' name='slug' value='{slug}' required></label>

        <label>AI модель<input type='text' name='ai_model' value='{ai_model}' required></label>

        <label>Модель распознавания речи<input type='text' name='stt_model' value='{stt_model}' placeholder='{core_settings.default_stt_model}'></label>

        <label>Температура<input type='number' step='0.1' min='0' max='2' name='temperature' value='{temperature}'></label>

        <label>Максимум токенов<input type='number' min='1' name='max_tokens' value='{max_tokens}'></label>

        <label>Источник промпта<input type='text' name='prompt_source' value='{prompt_source}' placeholder='{core_settings.PROMPT_GOOGLE_DOC_URL}'></label>

        <label>Приветствие<textarea name='intro_text'>{intro_text}</textarea></label>

        <label>Статус<input type='text' name='status' value='{status}' required></label>

        <label>Шаблон вида<select name='template'>{options}</select></label>

        <button type='submit'>Сохранить</button>

      </form>

    </section>

    """

async def widget_create(request: web.Request) -> web.Response:

    await _require_session(request)

    form_html = _widget_form()

    return _render_layout('Новый виджет', form_html)

async def widget_create_submit(request: web.Request) -> web.StreamResponse:

    email, tenant_slug = await _require_session(request)

    tenant_id = await _get_tenant_id(request, tenant_slug)

    data = await request.post()

    template = (data.get('template', DEFAULT_TEMPLATE_KEY) or DEFAULT_TEMPLATE_KEY).strip()

    if template not in TEMPLATES:

        template = DEFAULT_TEMPLATE_KEY

    def _to_float(value: str | None, default: float) -> float:

        try:

            return float(value) if value not in (None, '') else default

        except ValueError:

            return default

    def _to_int(value: str | None, default: int) -> int:

        try:

            return int(value) if value not in (None, '') else default

        except ValueError:

            return default

    widget = models.Widget(

        tenant_id=tenant_id,

        name=data.get('name', '').strip(),

        slug=data.get('slug', '').strip(),

        ai_model=data.get('ai_model', '').strip() or core_settings.default_model,

        prompt_source=data.get('prompt_source', '').strip() or None,

        intro_text=data.get('intro_text', '').strip() or None,

        status=data.get('status', '').strip() or 'draft',

        template=template,

        stt_model=data.get('stt_model', '').strip() or None,

        temperature=_to_float(data.get('temperature'), core_settings.default_temperature),

        max_tokens=_to_int(data.get('max_tokens'), core_settings.default_max_tokens),

    )

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        asset_repo = WidgetAssetRepository(db_session)

        widget = await widget_repo.add(widget)

        html, css, js = _default_widget_assets(widget.template)

        await asset_repo.add_new_version(widget.id, html, css, js)

    raise web.HTTPFound('/admin')

async def widget_overview(request: web.Request) -> web.Response:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        asset_repo = WidgetAssetRepository(db_session)

        binding_repo = WidgetBindingRepository(db_session)

        widget = await widget_repo.get(widget_id)

        if widget is None:

            raise web.HTTPNotFound()

        bindings = await binding_repo.list_for_widget(widget_id)

        latest_asset = await asset_repo.get_latest(widget_id)

    history = await history_db.get_recent_widget_messages(

        widget_id=widget.id,

        widget_slug=widget.slug,

        limit=20,

    )

    info_rows = ''.join(

        f"<tr><th style='width:180px'>{escape(label)}</th><td>{escape(value)}</td></tr>"

        for label, value in (

            ('ID', str(widget.id)),

            ('Slug', widget.slug),

            ('Статус', widget.status),

            ('Шаблон', widget.template),

            ('AI модель', widget.ai_model),

            ('STT модель', widget.stt_model or core_settings.default_stt_model),

            ('Температура', f"{widget.temperature}"),

            ('Макс. токенов', str(widget.max_tokens)),

            ('Промпт', widget.prompt_source or 'стандартный'),

            ('Текущий ассет', f"v{latest_asset.version}" if latest_asset else 'не создан'),

        )

    )

    info_section = f"""

    <section class='card'>

      <h2>Параметры виджета</h2>

      <table>{info_rows}</table>

      <p><a href='/admin/widgets/{widget.id}/edit' class='button'>Редактировать настройки</a></p>

    </section>

    """

    binding_rows = ''.join(

        f"<tr><td>{escape(b.domain)}</td><td>{'Активен' if b.is_active else 'Отключен'}</td>"

        f"<td><form class='inline' method='post' action='/admin/widgets/{widget.id}/bindings/{b.id}/delete'>"

        f"<button type='submit'>Удалить</button></form></td></tr>"

        for b in bindings

    ) or "<tr><td colspan='3'>Нет привязанных доменов</td></tr>"

    bindings_section = f"""

    <section class='card'>

      <h2>Домены</h2>

      <table>

        <thead><tr><th>Домен</th><th>Статус</th><th></th></tr></thead>

        <tbody>{binding_rows}</tbody>

      </table>

      <form method='post' action='/admin/widgets/{widget.id}/bindings' style='margin-top:16px;'>

        <label>Новый домен

          <input type='text' name='domain' placeholder='example.com' required>

        </label>

        <button type='submit'>Добавить</button>

      </form>

    </section>

    """

    history_rows = ''.join(

        f"<tr><td>{escape(str(item['timestamp']))}</td>"

        f"<td>{escape(str(item['user_id']))}</td>"

        f"<td>{escape(item['role'])}</td>"

        f"<td>{escape(item['content'][:400])}{'...' if len(item['content']) > 400 else ''}</td></tr>"

        for item in history

    ) or "<tr><td colspan='4'>Сообщений пока нет</td></tr>"

    history_section = f"""

    <section class='card'>

      <h2>Последние сообщения (20)</h2>

      <table>

        <thead><tr><th>Время</th><th>User ID</th><th>Роль</th><th>Текст</th></tr></thead>

        <tbody>{history_rows}</tbody>

      </table>

    </section>

    """

    content = info_section + bindings_section + history_section

    nav = f"<a href='/admin/widgets'>Список</a> | <a href='/admin/widgets/{widget.id}/assets'>Ассеты</a>"

    return _render_layout(f"Виджет {escape(widget.name)}", content, nav_extra=nav)

async def widget_dialogs(request: web.Request) -> web.Response:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        widget = await widget_repo.get(widget_id)

        if widget is None:

            raise web.HTTPNotFound()

    history = await history_db.get_recent_widget_messages(

        widget_id=widget.id,

        widget_slug=widget.slug,

        limit=20,

    )

    history_rows = ''.join(

        f"<tr><td>{escape(str(item['timestamp']))}</td>"

        f"<td>{escape(str(item['user_id']))}</td>"

        f"<td>{escape(item['role'])}</td>"

        f"<td>{escape(item['content'][:400])}{'...' if len(item['content']) > 400 else ''}</td></tr>"

        for item in history

    ) or "<tr><td colspan='4'>Сообщений пока нет</td></tr>"

    content = f"""

    <section class='card'>

      <h2>Последние сообщения (20)</h2>

      <table>

        <thead><tr><th>Время</th><th>User ID</th><th>Роль</th><th>Текст</th></tr></thead>

        <tbody>{history_rows}</tbody>

      </table>

    </section>

    """

    nav = f"<a href='/admin/widgets/{widget.id}'>Обзор</a>"

    return _render_layout(f"Диалоги {escape(widget.name)}", content, nav_extra=nav)

async def widget_binding_add(request: web.Request) -> web.StreamResponse:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    data = await request.post()

    domain = (data.get('domain', '') or '').strip().lower()

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        binding_repo = WidgetBindingRepository(db_session)

        widget = await widget_repo.get(widget_id)

        if widget is None:

            raise web.HTTPNotFound()

        if domain:

            existing = await binding_repo.list_for_widget(widget_id)

            if domain not in {item.domain for item in existing}:

                await binding_repo.add(widget_id, domain)

    raise web.HTTPFound(f"/admin/widgets/{widget_id}")

async def widget_binding_delete(request: web.Request) -> web.StreamResponse:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    binding_id = int(request.match_info['binding_id'])

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        binding_repo = WidgetBindingRepository(db_session)

        widget = await widget_repo.get(widget_id)

        if widget is None:

            raise web.HTTPNotFound()

        bindings = await binding_repo.list_for_widget(widget_id)

        if any(item.id == binding_id for item in bindings):

            await binding_repo.delete(binding_id)

    raise web.HTTPFound(f"/admin/widgets/{widget_id}")

async def widget_edit(request: web.Request) -> web.Response:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    async with session_scope(request.app) as db_session:

        repo = WidgetRepository(db_session)

        widget = await repo.get(widget_id)

    if widget is None:

        raise web.HTTPNotFound()

    form_html = _widget_form(widget)

    assets_link = f"<p><a href='/admin/widgets/{widget.id}/assets' class='button'>Управлять ассетами</a></p>"

    return _render_layout(f'Редактирование {widget.name}', form_html + assets_link)

async def widget_update(request: web.Request) -> web.StreamResponse:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    data = await request.post()

    template = (data.get('template', DEFAULT_TEMPLATE_KEY) or DEFAULT_TEMPLATE_KEY).strip()

    if template not in TEMPLATES:

        template = DEFAULT_TEMPLATE_KEY

    def _to_float(value: str | None, default: float) -> float:

        try:

            return float(value) if value not in (None, '') else default

        except ValueError:

            return default

    def _to_int(value: str | None, default: int) -> int:

        try:

            return int(value) if value not in (None, '') else default

        except ValueError:

            return default

    fields = {

        'name': data.get('name', '').strip(),

        'slug': data.get('slug', '').strip(),

        'ai_model': data.get('ai_model', '').strip() or core_settings.default_model,

        'prompt_source': data.get('prompt_source', '').strip() or None,

        'intro_text': data.get('intro_text', '').strip() or None,

        'status': data.get('status', '').strip() or 'draft',

        'template': template,

        'stt_model': data.get('stt_model', '').strip() or None,

        'temperature': _to_float(data.get('temperature'), core_settings.default_temperature),

        'max_tokens': _to_int(data.get('max_tokens'), core_settings.default_max_tokens),

    }

    async with session_scope(request.app) as db_session:

        repo = WidgetRepository(db_session)

        await repo.update(widget_id, **fields)

    raise web.HTTPFound('/admin')

async def widget_delete(request: web.Request) -> web.StreamResponse:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    async with session_scope(request.app) as db_session:

        repo = WidgetRepository(db_session)

        await repo.delete(widget_id)

    raise web.HTTPFound('/admin')

async def widget_assets_list(request: web.Request) -> web.Response:

    email, tenant_slug = await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        asset_repo = WidgetAssetRepository(db_session)

        widget = await widget_repo.get(widget_id)

        if widget is None:

            raise web.HTTPNotFound()

        assets = await asset_repo.list_versions(widget_id)

    rows = ''.join(

        f"<tr><td>{a.version}</td><td>{escape(a.created_at.isoformat())}</td>"

        f"<td><a href='/admin/widgets/{widget_id}/assets/{a.version}/preview' target='_blank'>preview</a></td></tr>"

        for a in assets

    )

    if assets:

        preview_src = f"/admin/widgets/{widget_id}/assets/{assets[0].version}/preview"

        preview_block = f"""<section class='card'>

      <h3>Preview</h3>

      <iframe id='asset-preview' src='{preview_src}' style='width:100%;height:420px;border:1px solid #e5e7eb;border-radius:12px;background:#fff;'></iframe>

      <p class='muted'>The iframe shows the latest saved version.</p>

    </section>"""

    else:

        preview_block = """<section class='card'>

      <h3>Preview</h3>

      <iframe id='asset-preview' src='about:blank' srcdoc='<p class="muted">No versions yet. Changes will appear here.</p>' style='width:100%;height:420px;border:1px solid #e5e7eb;border-radius:12px;background:#fff;'></iframe>

    </section>"""

    upload_form = f"""

    <section class='card'>

      <h2>Add new version</h2>

      <form id='asset-form' method='post' action='/admin/widgets/{widget_id}/assets/new' enctype='multipart/form-data' data-preview-url='/admin/widgets/{widget_id}/assets/preview'>

        <p class='muted'>Leave inputs empty to reuse the {escape(widget.template)} template.</p>

        <details open>

          <summary><strong>HTML</strong></summary>

          <textarea name='html' placeholder='Inline HTML'></textarea>

          <p class='muted'>or upload: <input type='file' name='html_file' accept='.html,.htm,text/html'></p>

        </details>

        <details>

          <summary><strong>CSS</strong></summary>

          <textarea name='css' placeholder='Inline CSS'></textarea>

          <p class='muted'>or upload: <input type='file' name='css_file' accept='.css,text/css'></p>

        </details>

        <details>

          <summary><strong>JavaScript</strong></summary>

          <textarea name='js' placeholder='Inline JavaScript'></textarea>

          <p class='muted'>or upload: <input type='file' name='js_file' accept='.js,application/javascript'></p>

        </details>

        <button type='submit'>Save version</button>

      </form>

    </section>

    """

    script = """<script>

      (function () {

        var form = document.getElementById('asset-form');

        var iframe = document.getElementById('asset-preview');

        if (!form || !iframe) { return; }

        var url = form.getAttribute('data-preview-url');

        if (!url) { return; }

        var timer;

        var trigger = function () {

          if (timer) { clearTimeout(timer); }

          timer = setTimeout(function () {

            var data = new FormData(form);

            fetch(url, { method: 'POST', body: data })

              .then(function (res) { return res.text(); })

              .then(function (html) { iframe.srcdoc = html; })

              .catch(function (err) { console.error('Preview update failed', err); });

          }, 700);

        };

        Array.prototype.slice.call(form.querySelectorAll('textarea, input[type="file"]')).forEach(function (node) {

          node.addEventListener('input', trigger);

          node.addEventListener('change', trigger);

        });

      }());

    </script>"""

    content = f"""

    <section class='card'>

      <h2>Assets for {escape(widget.name)}</h2>

      <p class='muted'>Template: {escape(widget.template)}</p>

      <table>

        <thead><tr><th>Version</th><th>Created at</th><th>Action</th></tr></thead>

        <tbody>{rows or '<tr><td colspan=3>No versions yet</td></tr>'}</tbody>

      </table>

    </section>

    {preview_block}

    {upload_form}

    {script}

    """

    nav = f"<a href='/admin/widgets/{widget_id}/edit'>Back to widget</a>"

    return _render_layout('Assets', content, nav_extra=nav)

async def widget_assets_new(request: web.Request) -> web.Response:

    return await widget_assets_list(request)

async def widget_assets_create(request: web.Request) -> web.StreamResponse:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    fields = await _collect_asset_fields(request)

    html_code = fields.get('html', '')

    css_code = fields.get('css', '')

    js_code = fields.get('js', '')

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        asset_repo = WidgetAssetRepository(db_session)

        widget = await widget_repo.get(widget_id)

        if widget is None:

            raise web.HTTPNotFound()

        if not any((html_code, css_code, js_code)):

            html_code, css_code, js_code = _default_widget_assets(widget.template)

        else:

            html_code = html_code or None

            css_code = css_code or None

            js_code = js_code or None

        await asset_repo.add_new_version(widget_id, html_code, css_code, js_code)

    raise web.HTTPFound(f'/admin/widgets/{widget_id}/assets')

async def widget_assets_preview_live(request: web.Request) -> web.Response:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    fields = await _collect_asset_fields(request)

    html_code = fields.get('html', '')

    css_code = fields.get('css', '')

    js_code = fields.get('js', '')

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        widget = await widget_repo.get(widget_id)

        if widget is None:

            raise web.HTTPNotFound()

    if not any((html_code, css_code, js_code)):

        html_code, css_code, js_code = _default_widget_assets(widget.template)

    css_block = f"<style>{css_code}</style>" if css_code else ""

    js_block = f"<script>{js_code}</script>" if js_code else ""

    body = _adjust_preview_urls(html_code, widget.slug) if html_code else '<p>No HTML provided</p>'

    html_doc = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Preview</title>{css_block}</head><body>{body}{js_block}</body></html>"

    return web.Response(text=html_doc, content_type='text/html')

async def widget_assets_preview(request: web.Request) -> web.Response:

    await _require_session(request)

    widget_id = int(request.match_info['widget_id'])

    version = int(request.match_info['version'])

    async with session_scope(request.app) as db_session:

        widget_repo = WidgetRepository(db_session)

        asset_repo = WidgetAssetRepository(db_session)

        widget = await widget_repo.get(widget_id)

        if widget is None:

            raise web.HTTPNotFound()

        asset = await asset_repo.get_version(widget_id, version)

        if asset is None:

            raise web.HTTPNotFound()

    css_block = f"<style>{asset.css}</style>" if asset.css else ""

    js_block = f"<script>{asset.js}</script>" if asset.js else ""

    body = _adjust_preview_urls(asset.html, widget.slug) if asset.html else '<p>HTML не задан</p>'

    html_doc = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Preview</title>{css_block}</head><body>{body}{js_block}</body></html>"

    return web.Response(text=html_doc, content_type='text/html')

def setup_admin_routes(app: web.Application) -> None:

    app.router.add_get('/admin', admin_index)

    app.router.add_get('/admin/login', login_page)

    app.router.add_post('/admin/login', login_submit)

    app.router.add_get('/admin/logout', logout)

    app.router.add_get('/admin/widgets', admin_index)

    app.router.add_get('/admin/widgets/new', widget_create)

    app.router.add_post('/admin/widgets/new', widget_create_submit)

    app.router.add_get('/admin/widgets/{widget_id}', widget_overview)

    app.router.add_get('/admin/widgets/{widget_id}/edit', widget_edit)

    app.router.add_post('/admin/widgets/{widget_id}/edit', widget_update)

    app.router.add_post('/admin/widgets/{widget_id}/delete', widget_delete)

    app.router.add_get('/admin/widgets/{widget_id}/dialogs', widget_dialogs)

    app.router.add_post('/admin/widgets/{widget_id}/bindings', widget_binding_add)

    app.router.add_post('/admin/widgets/{widget_id}/bindings/{binding_id}/delete', widget_binding_delete)

    app.router.add_get('/admin/widgets/{widget_id}/assets', widget_assets_list)

    app.router.add_get('/admin/widgets/{widget_id}/assets/new', widget_assets_new)

    app.router.add_post('/admin/widgets/{widget_id}/assets/new', widget_assets_create)

    app.router.add_post('/admin/widgets/{widget_id}/assets/preview', widget_assets_preview_live)

    app.router.add_get('/admin/widgets/{widget_id}/assets/{version}/preview', widget_assets_preview)

    setup_tenant_admin_routes(app)

