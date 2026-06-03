from __future__ import annotations

import secrets

from aiohttp import web
from aiohttp_session import get_session

from app.admin.layout import render_layout
from app.db import models
from app.db.repositories import TenantRepository, UserRepository
from app.db.session import session_scope
from core.config import settings

SESSION_EMAIL_KEY = 'email'
SESSION_TENANT_KEY = 'tenant_slug'
DEFAULT_TENANT_SLUG = 'demo'
DEFAULT_TENANT_NAME = 'Demo Tenant'


async def login_page(request: web.Request) -> web.Response:
    form = """
    <section class='card login-card'>
      <p class='eyebrow'>kAIgo widgets</p>
      <h2>Вход в админку</h2>
      <p class='muted'>Управление виджетами, ассетами и диалогами.</p>
      <form method="post">
        <label>Email
          <input name="email" type="email" placeholder="admin@example.com" autocomplete="email" required>
        </label>
        <label>Password
          <input name="password" type="password" placeholder="Пароль" autocomplete="current-password">
        </label>
        <button type="submit">Войти</button>
      </form>
    </section>
    """
    return render_layout("Вход", form)


async def login_submit(request: web.Request) -> web.Response:
    session = await get_session(request)
    data = await request.post()
    email = data.get('email', '').strip().lower()
    if not email:
        raise web.HTTPFound('/admin/login')
    if settings.ADMIN_EMAILS and email not in settings.ADMIN_EMAILS:
        raise web.HTTPFound('/admin/login')
    if settings.ADMIN_PASSWORD:
        password = data.get('password', '').strip()
        if not secrets.compare_digest(password, settings.ADMIN_PASSWORD):
            raise web.HTTPFound('/admin/login')

    tenant_slug = DEFAULT_TENANT_SLUG

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        user_repo = UserRepository(db_session)

        tenant = await tenant_repo.get_by_slug(tenant_slug)
        if tenant is None:
            tenant = await tenant_repo.add(models.Tenant(name=DEFAULT_TENANT_NAME, slug=tenant_slug))

        user = await user_repo.get_by_email(email)
        if user is None:
            await user_repo.add(models.User(tenant_id=tenant.id, email=email, password_hash='', role='tenant_admin'))

    session[SESSION_EMAIL_KEY] = email
    session[SESSION_TENANT_KEY] = tenant_slug
    raise web.HTTPFound('/admin')


async def logout(request: web.Request) -> web.StreamResponse:
    session = await get_session(request)
    session.invalidate()
    raise web.HTTPFound('/admin/login')
