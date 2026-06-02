from __future__ import annotations

import secrets
from html import escape
from urllib.parse import quote_plus

from aiohttp import web
from aiohttp_session import get_session

from app.admin.auth import SESSION_EMAIL_KEY, SESSION_TENANT_KEY
from app.admin.layout import render_layout
from app.db import models
from app.db.repositories import TenantRepository, UserRepository, WidgetRepository
from app.db.session import session_scope
from core.security import hash_password


async def tenants_index(request: web.Request) -> web.Response:
    await _require_session(request)
    query = (request.query.get('q') or '').strip().lower()

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        widget_repo = WidgetRepository(db_session)
        tenants = await tenant_repo.list_all()

        cards: list[str] = []
        for tenant in tenants:
            if tenant.slug == 'unassigned':
                continue
            widgets = await widget_repo.list_for_tenant(tenant.id)
            if query:
                matches_tenant = query in tenant.name.lower() or query in tenant.slug.lower()
                matches_widgets = any(
                    query in (widget.name or '').lower() or query in widget.slug.lower()
                    for widget in widgets
                )
                if not (matches_tenant or matches_widgets):
                    continue
            badges = ''.join(
                f"<span class='chip'>{escape(widget.slug)}</span>"
                for widget in widgets
            ) or "<span class='muted'>Виджеты не подключены.</span>"
            cards.append(
                f"""
                <section class='card'>
                  <h2>{escape(tenant.name)}</h2>
                  <p class='muted'>Slug: {escape(tenant.slug)} · Виджетов: {len(widgets)}</p>
                  <p>{badges}</p>
                  <div class='actions'>
                    <a class='button' href='/admin/tenants/{tenant.id}'>Доступ</a>
                    <a class='button' href='/admin/tenants/{tenant.id}/edit'>Настройки</a>
                  </div>
                </section>
                """
            )

    filter_form = f"""
    <form method='get' class='stack' style='margin-bottom:24px;'>
      <div class='column' style='flex:2;'>
        <input type='text' name='q' value='{escape(query)}' placeholder='Поиск по имени, slug или виджету'>
      </div>
      <div class='column' style='max-width:200px;'>
        <button type='submit'>Искать</button>
      </div>
    </form>
    """
    create_button = "<div class='actions' style='margin-bottom:24px;'><a class='button' href='/admin/tenants/new'>Создать заказчика</a></div>"
    content = filter_form + create_button + (''.join(cards) or "<p class='muted'>Заказчики пока не созданы.</p>")
    return render_layout('Заказчики', content, nav_extra=_nav_links())


async def tenant_create_page(request: web.Request) -> web.Response:
    await _require_session(request)
    alert = request.query.get('alert')
    notice = f"<section class='card notice'>{escape(alert)}</section>" if alert else ''

    content = f"""
    {notice}
    <section class='card'>
      <h2>Создание заказчика</h2>
      <form method='post' action='/admin/tenants/new'>
        <label>Название
          <input type='text' name='name' placeholder='Компания' required>
        </label>
        <label>Slug
          <input type='text' name='slug' placeholder='company-slug' pattern='^[a-z0-9-]+$' required>
        </label>
        <label>Email для входа
          <input type='email' name='email' placeholder='client@example.com' required>
        </label>
        <label>Пароль
          <input type='text' name='password' placeholder='Оставьте пустым, чтобы сгенерировать автоматически'>
        </label>
        <div class='actions'>
          <button type='submit'>Создать</button>
        </div>
      </form>
    </section>
    """
    return render_layout('Новый заказчик', content, nav_extra=_nav_links())


async def tenant_create_submit(request: web.Request) -> web.StreamResponse:
    await _require_session(request)
    data = await request.post()
    name = (data.get('name') or '').strip()
    slug = (data.get('slug') or '').strip().lower()
    email = (data.get('email') or '').strip().lower()
    password = (data.get('password') or '').strip()

    if not name or not slug or not email:
        raise web.HTTPFound("/admin/tenants/new?alert=" + quote_plus('Заполните все обязательные поля'))
    if not _is_valid_slug(slug):
        raise web.HTTPFound("/admin/tenants/new?alert=" + quote_plus('Slug может содержать только латинские буквы, цифры и дефис'))

    generated = False
    if not password:
        password = _generate_password()
        generated = True

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        user_repo = UserRepository(db_session)
        if await tenant_repo.get_by_slug(slug):
            raise web.HTTPFound("/admin/tenants/new?alert=" + quote_plus('Slug уже используется'))
        if await user_repo.get_by_email(email):
            raise web.HTTPFound("/admin/tenants/new?alert=" + quote_plus('Email уже используется'))

        tenant = await tenant_repo.add(models.Tenant(name=name, slug=slug))
        await user_repo.add(
            models.User(
                tenant_id=tenant.id,
                email=email,
                password_hash=hash_password(password),
                role='tenant_admin',
            )
        )
        tenant_id = tenant.id

    if generated:
        content = f"""
        <section class='card notice'>
          <h2>Заказчик создан</h2>
          <p>Сохраните пароль сейчас, повторно он показан не будет.</p>
          <p><strong>Email:</strong> {escape(email)}</p>
          <p><strong>Пароль:</strong> <code>{escape(password)}</code></p>
          <div class='actions'>
            <a class='button' href='/admin/tenants/{tenant_id}'>Перейти к доступу</a>
          </div>
        </section>
        """
        return render_layout('Заказчик создан', content, nav_extra=_nav_links(tenant_id, active='credentials'))
    message = 'Заказчик создан.'
    raise web.HTTPFound(f"/admin/tenants/{tenant_id}?alert={quote_plus(message)}")


async def tenant_credentials(request: web.Request) -> web.Response:
    await _require_session(request)
    tenant_id = int(request.match_info['tenant_id'])
    alert = request.query.get('alert')

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        user_repo = UserRepository(db_session)
        widget_repo = WidgetRepository(db_session)
        tenant = await tenant_repo.get(tenant_id)
        if tenant is None:
            raise web.HTTPNotFound()
        users = await user_repo.list_for_tenant(tenant_id)
        widgets = await widget_repo.list_for_tenant(tenant_id)

    primary = users[0] if users else None
    duplicates = users[1:]

    notice = f"<section class='card notice'>{escape(alert)}</section>" if alert else ''
    duplicate_notice = ''
    if duplicates:
        duplicate_notice = (
            "<section class='card notice'>"
            "<strong>Обнаружено несколько учетных записей.</strong>"
            "<p class='muted'>После сохранения останется только одна основная запись.</p>"
            + ''.join(f"<p class='muted'>ID {dup.id}: {escape(dup.email)}</p>" for dup in duplicates)
            + "</section>"
        )

    email_value = escape(primary.email) if primary else ''
    widget_summary = ''.join(
        f"<span class='chip'>{escape(widget.slug)}</span>"
        for widget in widgets
    ) or "<span class='muted'>Виджеты не привязаны.</span>"

    content = f"""
    {notice}
    {duplicate_notice}
    <section class='card'>
      <h2>Учётные данные</h2>
      <form method='post' action='/admin/tenants/{tenant_id}/credentials'>
        <label>Email
          <input type='email' name='email' value='{email_value}' placeholder='client@example.com' required>
        </label>
        <label>Новый пароль (необязательно)
          <input type='text' name='password' placeholder='Оставьте пустым, чтобы не менять'>
        </label>
        <div class='actions'>
          <button type='submit' name='action' value='save'>Сохранить</button>
          <button type='submit' name='action' value='generate'>Сгенерировать пароль</button>
        </div>
      </form>
      <p class='muted'>Пароль хранится в зашифрованном виде. Сгенерированный пароль будет показан в уведомлении.</p>
    </section>
    <section class='card'>
      <h3>Привязанные виджеты</h3>
      <p>{widget_summary}</p>
      <p class='muted'>Добавить или удалить виджеты можно на вкладке «Настройки».</p>
    </section>
    """
    return render_layout(f'Доступ · {escape(tenant.name)}', content, nav_extra=_nav_links(tenant_id, active='credentials'))


async def tenant_credentials_save(request: web.Request) -> web.StreamResponse:
    await _require_session(request)
    tenant_id = int(request.match_info['tenant_id'])
    data = await request.post()
    action = (data.get('action') or 'save').strip().lower()
    email = (data.get('email') or '').strip().lower()
    password_input = (data.get('password') or '').strip()

    if not email:
        raise web.HTTPFound(f"/admin/tenants/{tenant_id}?alert={quote_plus('Укажите email')}")

    password_to_set: str | None = password_input or None
    generated = False
    if action == 'generate':
        password_to_set = _generate_password()
        generated = True

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        user_repo = UserRepository(db_session)
        tenant = await tenant_repo.get(tenant_id)
        if tenant is None:
            raise web.HTTPNotFound()

        users = await user_repo.list_for_tenant(tenant_id)
        primary = users[0] if users else None
        duplicates = users[1:]

        existing = await user_repo.get_by_email(email)
        if existing and (primary is None or existing.id != primary.id):
            raise web.HTTPFound(f"/admin/tenants/{tenant_id}?alert={quote_plus('Email уже используется другим пользователем')}")

        if primary is None:
            if password_to_set is None:
                password_to_set = _generate_password()
                generated = True
            primary = models.User(
                tenant_id=tenant_id,
                email=email,
                password_hash=hash_password(password_to_set),
                role='tenant_admin',
            )
            await user_repo.add(primary)
        else:
            updates: dict[str, str] = {}
            if email != primary.email:
                updates['email'] = email
            if updates:
                await user_repo.update(primary.id, **updates)
            if password_to_set:
                await user_repo.update_password(primary.id, hash_password(password_to_set))

        for extra in duplicates:
            await user_repo.delete(extra.id)

    if generated and password_to_set:
        content = f"""
        <section class='card notice'>
          <h2>Пароль обновлён</h2>
          <p>Сохраните пароль сейчас, повторно он показан не будет.</p>
          <p><strong>Email:</strong> {escape(email)}</p>
          <p><strong>Новый пароль:</strong> <code>{escape(password_to_set)}</code></p>
          <div class='actions'>
            <a class='button' href='/admin/tenants/{tenant_id}'>Вернуться к доступу</a>
          </div>
        </section>
        """
        return render_layout('Пароль обновлён', content, nav_extra=_nav_links(tenant_id, active='credentials'))
    message = 'Данные сохранены'
    raise web.HTTPFound(f"/admin/tenants/{tenant_id}?alert={quote_plus(message)}")


async def tenant_edit(request: web.Request) -> web.Response:
    await _require_session(request)
    tenant_id = int(request.match_info['tenant_id'])
    alert = request.query.get('alert')

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        widget_repo = WidgetRepository(db_session)
        tenant = await tenant_repo.get(tenant_id)
        if tenant is None:
            raise web.HTTPNotFound()
        widgets = await widget_repo.list_for_tenant(tenant_id)
        archive_tenant = await _get_or_create_archive_tenant(tenant_repo)
        available_widgets = await widget_repo.list_for_tenant(archive_tenant.id)

    notice = f"<section class='card notice'>{escape(alert)}</section>" if alert else ''

    widget_rows = ''.join(
        f"<tr><td>{widget.id}</td><td>{escape(widget.name)}</td><td>{escape(widget.slug)}</td><td>{escape(widget.status)}</td><td>{escape(widget.created_at.isoformat() if widget.created_at else '-')}</td>"
        f"<td><div class='actions'><a class='button' href='/admin/widgets/{widget.id}'>Открыть</a>"
        f"<form method='post' action='/admin/tenants/{tenant_id}/widgets/{widget.id}/detach' class='inline' onsubmit=\"return confirm('Убрать виджет из доступа?');\"><button type='submit'>Отвязать</button></form></div></td></tr>"
        for widget in widgets
    ) or "<tr><td colspan='6'>Виджеты не подключены.</td></tr>"

    select_options = ''.join(
        f"<option value='{widget.id}'>{escape(widget.name)} ({escape(widget.slug)})</option>"
        for widget in available_widgets
    )

    if available_widgets:
        select_form = f"""
        <form method='post' action='/admin/tenants/{tenant_id}/widgets/attach-select' class='stack' style='margin-top:16px;'>
        <div class='column'>
            <label>Выбрать свободный виджет
            <select name='widget_id'>
                <option value=''>-- выбрать --</option>
                {select_options}
            </select>
            </label>
        </div>
        <div class='column' style='max-width: 220px;'>
            <button type='submit'>Привязать выбранный</button>
        </div>
        </form>
        """
    else:
        select_form = "<p class='muted' style='margin-top:16px;'>Свободных виджетов пока нет. Отвяжите виджет у другого заказчика, чтобы он появился здесь.</p>"

    content = f"""
    {notice}
    <section class='card'>
      <h2>Настройки заказчика</h2>
      <form method='post' action='/admin/tenants/{tenant_id}/edit'>
        <label>Название
          <input type='text' name='name' value='{escape(tenant.name)}' required>
        </label>
        <label>Slug
          <input type='text' name='slug' value='{escape(tenant.slug)}' required pattern='^[a-z0-9-]+$'>
        </label>
        <button type='submit'>Сохранить</button>
      </form>
    </section>
    <section class='card'>
      <h3>Подключенные виджеты</h3>
      <table>
        <thead><tr><th>ID</th><th>Название</th><th>Slug</th><th>Статус</th><th>Создан</th><th>Действия</th></tr></thead>
        <tbody>{widget_rows}</tbody>
      </table>
      <h4 style='margin-top:24px;'>Привязать виджет</h4>
      <form method='post' action='/admin/tenants/{tenant_id}/widgets/attach' class='stack' style='margin-top:16px;'>
        <div class='column'>
          <label>Через slug
            <input type='text' name='slug' placeholder='widget-slug' required>
          </label>
        </div>
        <div class='column' style='max-width: 220px;'>
          <button type='submit'>Привязать по slug</button>
        </div>
      </form>
      {select_form}
    </section>
    """
    return render_layout(f'Настройки · {escape(tenant.name)}', content, nav_extra=_nav_links(tenant_id, active='settings'))


async def tenant_update(request: web.Request) -> web.StreamResponse:
    await _require_session(request)
    tenant_id = int(request.match_info['tenant_id'])
    data = await request.post()
    name = (data.get('name') or '').strip()
    slug = (data.get('slug') or '').strip().lower()

    if not name or not slug:
        raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Укажите название и slug')}")
    if not _is_valid_slug(slug):
        raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Недопустимый slug')}")

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        tenant = await tenant_repo.get(tenant_id)
        if tenant is None:
            raise web.HTTPNotFound()
        if slug != tenant.slug:
            existing = await tenant_repo.get_by_slug(slug)
            if existing and existing.id != tenant_id:
                raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Slug уже занят')}")
        await tenant_repo.update(tenant_id, name=name, slug=slug)

    raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Изменения сохранены')}")


async def tenant_widget_attach(request: web.Request) -> web.StreamResponse:
    await _require_session(request)
    tenant_id = int(request.match_info['tenant_id'])
    data = await request.post()
    slug = (data.get('slug') or '').strip().lower()
    if not slug:
        raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Укажите slug')}")

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        widget_repo = WidgetRepository(db_session)
        tenant = await tenant_repo.get(tenant_id)
        if tenant is None:
            raise web.HTTPNotFound()
        widget = await widget_repo.get_by_slug(slug)
        if widget is None:
            raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus(f'Виджет {slug} не найден')}")
        if widget.tenant_id == tenant_id:
            raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Виджет уже привязан')}")
        await widget_repo.update(widget.id, tenant_id=tenant_id)

    raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus(f'Виджет {slug} привязан')}")


async def tenant_widget_attach_select(request: web.Request) -> web.StreamResponse:
    await _require_session(request)
    tenant_id = int(request.match_info['tenant_id'])
    data = await request.post()
    widget_id_raw = (data.get('widget_id') or '').strip()
    if not widget_id_raw:
        raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Выберите виджет из списка')}")
    try:
        widget_id = int(widget_id_raw)
    except ValueError:
        raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Некорректный идентификатор виджета')}")

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        widget_repo = WidgetRepository(db_session)
        tenant = await tenant_repo.get(tenant_id)
        if tenant is None:
            raise web.HTTPNotFound()
        archive = await _get_or_create_archive_tenant(tenant_repo)
        widget = await widget_repo.get(widget_id)
        if widget is None:
            raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Виджет не найден')}")
        if widget.tenant_id != archive.id:
            raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Этот виджет уже используется другим заказчиком')}")
        await widget_repo.update(widget.id, tenant_id=tenant_id)

    raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Виджет привязан из списка')}")


async def tenant_widget_detach(request: web.Request) -> web.StreamResponse:
    await _require_session(request)
    tenant_id = int(request.match_info['tenant_id'])
    widget_id = int(request.match_info['widget_id'])

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        widget_repo = WidgetRepository(db_session)
        tenant = await tenant_repo.get(tenant_id)
        widget = await widget_repo.get(widget_id)
        if tenant is None or widget is None or widget.tenant_id != tenant_id:
            raise web.HTTPNotFound()
        archive = await _get_or_create_archive_tenant(tenant_repo)
        await widget_repo.update(widget.id, tenant_id=archive.id)

    raise web.HTTPFound(f"/admin/tenants/{tenant_id}/edit?alert={quote_plus('Виджет удалён из доступа и перемещён в архив')}")


def setup_tenant_admin_routes(app: web.Application) -> None:
    app.router.add_get('/admin/tenants', tenants_index)
    app.router.add_get('/admin/tenants/new', tenant_create_page)
    app.router.add_post('/admin/tenants/new', tenant_create_submit)
    app.router.add_get('/admin/tenants/{tenant_id}', tenant_credentials)
    app.router.add_post('/admin/tenants/{tenant_id}/credentials', tenant_credentials_save)
    app.router.add_get('/admin/tenants/{tenant_id}/edit', tenant_edit)
    app.router.add_post('/admin/tenants/{tenant_id}/edit', tenant_update)
    app.router.add_post('/admin/tenants/{tenant_id}/widgets/attach', tenant_widget_attach)
    app.router.add_post('/admin/tenants/{tenant_id}/widgets/attach-select', tenant_widget_attach_select)
    app.router.add_post('/admin/tenants/{tenant_id}/widgets/{widget_id}/detach', tenant_widget_detach)


async def _require_session(request: web.Request) -> tuple[str, str]:
    session = await get_session(request)
    email = session.get(SESSION_EMAIL_KEY)
    tenant_slug = session.get(SESSION_TENANT_KEY)
    if not email or not tenant_slug:
        raise web.HTTPFound('/admin/login')
    return email, tenant_slug


def _generate_password() -> str:
    return secrets.token_urlsafe(8)


def _is_valid_slug(slug: str) -> bool:
    return bool(slug) and all(char.isalnum() or char == '-' for char in slug)


async def _get_or_create_archive_tenant(tenant_repo: TenantRepository) -> models.Tenant:
    archive = await tenant_repo.get_by_slug('unassigned')
    if archive:
        return archive
    return await tenant_repo.add(models.Tenant(name='Архив виджетов', slug='unassigned'))


def _nav_links(active_tenant: int | None = None, *, active: str | None = None) -> str:
    if not active_tenant:
        return ''
    items = [
        ('credentials', 'Доступ', f'/admin/tenants/{active_tenant}'),
        ('settings', 'Настройки', f'/admin/tenants/{active_tenant}/edit'),
    ]
    return ''.join(
        f"<a href='{url}' class='{'current' if key == active else ''}'>{label}</a>"
        for key, label, url in items
    )
