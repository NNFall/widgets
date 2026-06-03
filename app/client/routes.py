from __future__ import annotations

from html import escape
from typing import Iterable

from aiohttp import web
from aiohttp_session import get_session

from app.db.repositories import TenantRepository, UserRepository, WidgetRepository
from app.db.session import session_scope
from core import database as history_db
from core.security import verify_password

CLIENT_SESSION_EMAIL = "client_email"
CLIENT_SESSION_USER_ID = "client_user_id"
CLIENT_SESSION_TENANT_ID = "client_tenant_id"


async def client_login_page(request: web.Request) -> web.Response:
    session = await get_session(request)
    if session.get(CLIENT_SESSION_USER_ID):
        raise web.HTTPFound("/client")

    html = _render_layout(
        "Вход для заказчика",
        """
        <section class='card'>
          <form method='post'>
            <label>Email
              <input type='email' name='email' placeholder='email@example.com' required>
            </label>
            <label>Пароль
              <input type='password' name='password' placeholder='Ваш пароль' required>
            </label>
            <button type='submit'>Войти</button>
          </form>
        </section>
        <p class='muted'>Пароль можно запросить у менеджера или сбросить в админке.</p>
        """
    )
    return web.Response(text=html, content_type="text/html")


async def client_login_submit(request: web.Request) -> web.StreamResponse:
    session = await get_session(request)
    if session.get(CLIENT_SESSION_USER_ID):
        raise web.HTTPFound("/client")

    data = await request.post()
    email = (data.get("email", "") or "").strip().lower()
    password = data.get("password", "") or ""

    if not email or not password:
        return _login_error("Введите email и пароль.")

    async with session_scope(request.app) as db_session:
        user_repo = UserRepository(db_session)
        tenant_repo = TenantRepository(db_session)
        user = await user_repo.get_by_email(email)
        if not user:
            return _login_error("Неверный email или пароль.")
        tenant = await tenant_repo.get(user.tenant_id)
        if tenant is None:
            return _login_error("Учетная запись отключена.")
        if not user.password_hash or not verify_password(password, user.password_hash):
            return _login_error("Неверный email или пароль.")

    session[CLIENT_SESSION_EMAIL] = email
    session[CLIENT_SESSION_USER_ID] = user.id
    session[CLIENT_SESSION_TENANT_ID] = user.tenant_id
    raise web.HTTPFound("/client")


async def client_logout(request: web.Request) -> web.StreamResponse:
    session = await get_session(request)
    session.pop(CLIENT_SESSION_EMAIL, None)
    session.pop(CLIENT_SESSION_USER_ID, None)
    session.pop(CLIENT_SESSION_TENANT_ID, None)
    raise web.HTTPFound("/client/login")


async def client_dashboard(request: web.Request) -> web.Response:
    tenant_id, email = await _require_client(request)

    async with session_scope(request.app) as db_session:
        tenant_repo = TenantRepository(db_session)
        widget_repo = WidgetRepository(db_session)
        tenant = await tenant_repo.get(tenant_id)
        widgets = await widget_repo.list_for_tenant(tenant_id)

    cards: list[str] = []
    for widget in widgets:
        cards.append(
            f"""
            <section class='card'>
              <h3>{escape(widget.name)}</h3>
              <p class='muted'>Slug: {escape(widget.slug)} · Статус: {escape(widget.status)}</p>
              <div class='actions'>
                <a class='button' href='/client/widgets/{widget.id}/dialogs'>Диалоги</a>
              </div>
            </section>
            """
        )

    content = f"""
    <section class='card'>
      <h2>Здравствуйте, {escape(email)}</h2>
      <p class='muted'>Компания: {escape(tenant.name if tenant else 'Без названия')}</p>
    </section>
    {''.join(cards) or "<p class='muted'>Виджеты пока не созданы.</p>"}
    """
    html = _render_layout("Личный кабинет", content, nav_links=[("Мои виджеты", "/client"), ("Выйти", "/client/logout")])
    return web.Response(text=html, content_type="text/html")


async def client_widget_dialogs(request: web.Request) -> web.Response:
    tenant_id, email = await _require_client(request)
    widget_id = int(request.match_info['widget_id'])

    async with session_scope(request.app) as db_session:
        widget_repo = WidgetRepository(db_session)
        widget = await widget_repo.get(widget_id)
        if widget is None or widget.tenant_id != tenant_id:
            raise web.HTTPNotFound()

    conversations = await history_db.get_recent_widget_conversations(
        widget_id=widget.id,
        widget_slug=widget.slug,
        limit=20,
    )

    items: list[str] = []
    for conv in conversations:
        messages = await history_db.get_conversation_messages(
            conv['user_id'],
            widget_id=widget.id,
            widget_slug=widget.slug,
        )
        body = _render_messages(messages)
        last_ts = escape(conv['last_timestamp']) if conv['last_timestamp'] else '—'
        items.append(
            f"""
            <details class='dialog-card'>
              <summary><strong>User {conv['user_id']}</strong><span class='muted'>{last_ts} · {conv['message_count']} сообщений</span></summary>
              <div class='dialog-body'>{body}</div>
            </details>
            """
        )

    content = (
        f"""
        <section class='card'>
          <h2>Диалоги по виджету {escape(widget.name)}</h2>
          <p class='muted'>Показаны последние 20 диалогов. Нажмите на строку, чтобы раскрыть подробности.</p>
        </section>
        """
        + (''.join(items) or "<p class='muted'>Диалогов пока нет.</p>")
    )

    html = _render_layout(
        f"Диалоги · {widget.name}",
        content,
        nav_links=[("Мои виджеты", "/client"), ("Выйти", "/client/logout")]
    )
    return web.Response(text=html, content_type="text/html")


def setup_client_routes(app: web.Application) -> None:
    app.router.add_get('/client/login', client_login_page)
    app.router.add_post('/client/login', client_login_submit)
    app.router.add_get('/client/logout', client_logout)
    app.router.add_get('/client', client_dashboard)
    app.router.add_get('/client/widgets/{widget_id}/dialogs', client_widget_dialogs)


async def _require_client(request: web.Request) -> tuple[int, str]:
    session = await get_session(request)
    tenant_id = session.get(CLIENT_SESSION_TENANT_ID)
    email = session.get(CLIENT_SESSION_EMAIL)
    if not tenant_id or not email:
        raise web.HTTPFound('/client/login')
    return int(tenant_id), str(email)


def _render_layout(title: str, content: str, *, nav_links: Iterable[tuple[str, str]] | None = None) -> str:
    nav_html = ''.join(f"<a href='{escape(url)}'>{escape(label)}</a>" for label, url in (nav_links or []))
    return f"""
    <html>
      <head>
        <title>{escape(title)}</title>
        <style>
          body {{ font-family: Inter, Arial, sans-serif; background: #f6f8fb; margin: 0; color: #111827; }}
          body::before {{ content: ''; position: fixed; inset: 0 0 auto; height: 4px; background: linear-gradient(90deg, #2563eb, #10b981, #f59e0b); }}
          header, section.card, details.dialog-card, body > p {{ max-width: 1080px; margin-left: auto; margin-right: auto; }}
          header {{ display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 32px 24px 20px; margin-bottom: 8px; }}
          h1 {{ margin: 0; font-size: 28px; }}
          h2, h3 {{ margin-top: 0; }}
          nav {{ display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }}
          nav a {{ color: #2563eb; text-decoration: none; font-weight: 700; padding: 7px 10px; border-radius: 8px; }}
          nav a:hover {{ background: #eff6ff; }}
          .button {{ display: inline-block; padding: 8px 16px; background: #2563eb; color: #fff; border-radius: 8px; text-decoration: none; }}
          section.card {{ background: #fff; padding: 24px; border-radius: 8px; border: 1px solid #e5e7eb; box-shadow: 0 12px 26px rgba(15, 23, 42, 0.06); margin-bottom: 20px; }}
          label {{ display: block; margin-bottom: 12px; font-weight: 600; }}
          input {{ width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 8px; box-sizing: border-box; }}
          input:focus {{ border-color: #2563eb; box-shadow: 0 0 0 4px rgba(37, 99, 235, .12); outline: none; }}
          button {{ padding: 10px 18px; border: none; border-radius: 8px; background: #2563eb; color: #fff; cursor: pointer; font-weight: 700; }}
          .muted {{ color: #6b7280; font-size: 14px; }}
          .actions {{ margin-top: 16px; }}
          ul.conv-list {{ list-style: none; margin: 0; padding: 0; }}
          ul.conv-list li {{ background: #f3f4f6; padding: 12px 16px; border-radius: 12px; margin-bottom: 12px; display: flex; flex-direction: column; gap: 4px; }}
          ul.conv-list li .muted {{ font-size: 13px; color: #6b7280; }}
          ul.conv-list li .preview {{ color: #374151; font-size: 14px; }}
          details.dialog-card {{ border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; padding: 0 16px; margin-bottom: 16px; box-shadow: 0 10px 22px rgba(15, 23, 42, 0.05); }}
          details.dialog-card summary {{ cursor: pointer; padding: 16px 0; display: flex; flex-direction: column; gap: 4px; }}
          details.dialog-card summary::-webkit-details-marker {{ display: none; }}
          .dialog-body {{ padding: 12px 0 16px; display: flex; flex-direction: column; gap: 12px; }}
          .message {{ background: #f9fafb; border-radius: 8px; padding: 12px 16px; }}
          .message.user {{ border-left: 4px solid #2563eb; }}
          .message.assistant {{ border-left: 4px solid #10b981; }}
          .message .message-meta {{ font-size: 13px; color: #6b7280; margin-bottom: 6px; }}
          .message .message-text {{ white-space: pre-wrap; line-height: 1.5; color: #111827; }}
          @media (max-width: 720px) {{
            header {{ align-items: flex-start; flex-direction: column; padding: 28px 16px 16px; }}
            h1 {{ font-size: 24px; }}
            section.card, details.dialog-card, body > p {{ margin-left: 16px; margin-right: 16px; }}
            section.card {{ padding: 18px; }}
          }}
        </style>
      </head>
      <body>
        <header>
          <h1>{escape(title)}</h1>
          <nav>{nav_html}</nav>
        </header>
        {content}
      </body>
    </html>
    """


def _login_error(message: str) -> web.Response:
    html = _render_layout(
        "Ошибка авторизации",
        f"""
        <section class='card'>
          <p class='muted'>{escape(message)}</p>
          <p><a class='button' href='/client/login'>Вернуться к форме входа</a></p>
        </section>
        """
    )
    return web.Response(text=html, content_type='text/html', status=401)


def _render_messages(messages: list[dict[str, str]]) -> str:
    if not messages:
        return "<p class='muted'>Сообщений нет</p>"
    blocks = []
    for msg in messages:
        role_label = 'Пользователь' if msg['role'] == 'user' else 'Ассистент'
        role_class = 'user' if msg['role'] == 'user' else 'assistant'
        timestamp = escape(msg.get('timestamp') or '—')
        content = escape(msg.get('content') or '').replace('\n', '<br>')
        blocks.append(
            f"<div class='message {role_class}'>"
            f"<div class='message-meta'>{role_label} · {timestamp}</div>"
            f"<div class='message-text'>{content}</div>"
            f"</div>"
        )
    return ''.join(blocks)
