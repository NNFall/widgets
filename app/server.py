import asyncio
import logging
from html import escape

from aiohttp import web
from aiohttp_session import setup as setup_session, SimpleCookieStorage, get_session
from sqlalchemy import select

from app.admin.routes import setup_admin_routes
from app.api.routes import setup_api_routes
from app.config import AppConfig, load_config
from app.db import models
from app.db import init_db_signals
from app.db.session import session_scope
from app.logging_config import setup_logging
from app.widgets.routes import setup_widget_routes
from app.client.routes import CLIENT_SESSION_USER_ID, setup_client_routes
from core import database as history_db

async def _init_history_db(app: web.Application) -> None:
    await history_db.init_db()


async def _load_home_widgets(request: web.Request) -> list[models.Widget]:
    try:
        async with session_scope(request.app) as db_session:
            result = await db_session.execute(
                select(models.Widget).order_by(models.Widget.id).limit(8)
            )
            return list(result.scalars())
    except Exception:  # noqa: BLE001
        logger.warning("Failed to load widgets for home page", exc_info=True)
        return []


async def _home(request: web.Request) -> web.StreamResponse:
    session = await get_session(request)
    is_client = bool(session.get(CLIENT_SESSION_USER_ID))
    primary_href = "/client" if is_client else "/client/login"
    primary_label = "Открыть кабинет" if is_client else "Войти в кабинет"
    widgets = await _load_home_widgets(request)
    if widgets:
        widget_cards = "\n".join(
            f"""
        <article class="template-card">
          <div>
            <span class="tag">{escape(widget.status)}</span>
            <h3>{escape(widget.name)}</h3>
            <p>{escape((widget.intro_text or 'AI-консультант с отдельным сценарием и публичной страницей.')[:180])}</p>
          </div>
          <a class="button" href="/w/{escape(widget.slug)}">Открыть /w/{escape(widget.slug)}</a>
        </article>"""
            for widget in widgets
        )
    else:
        widget_cards = """
        <article class="template-card">
          <div>
            <span class="tag">demo</span>
            <h3>Демо-виджет</h3>
            <p>База временно недоступна для списка, но публичный демо-чат можно открыть напрямую.</p>
          </div>
          <a class="button" href="/w/demka">Открыть /w/demka</a>
        </article>"""

    html = f"""<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Kaigo Widgets</title>
    <style>
      :root {{
        --bg: #f6f8fb;
        --surface: #ffffff;
        --ink: #111827;
        --muted: #64748b;
        --line: #dbe3ee;
        --blue: #2563eb;
        --green: #059669;
        --amber: #b45309;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        min-height: 100vh;
        background: var(--bg);
        color: var(--ink);
        font-family: Inter, "Segoe UI", system-ui, -apple-system, sans-serif;
      }}
      body::before {{
        content: "";
        position: fixed;
        inset: 0 0 auto;
        height: 4px;
        background: linear-gradient(90deg, var(--blue), var(--green), var(--amber));
      }}
      main {{
        width: min(1120px, calc(100% - 32px));
        margin: 0 auto;
        padding: 52px 0 40px;
      }}
      header {{
        display: flex;
        justify-content: space-between;
        gap: 24px;
        align-items: flex-start;
        margin-bottom: 28px;
      }}
      .eyebrow {{
        margin: 0 0 8px;
        color: var(--green);
        font-size: 13px;
        font-weight: 800;
        text-transform: uppercase;
      }}
      h1 {{
        margin: 0;
        font-size: 52px;
        line-height: 1;
        letter-spacing: 0;
      }}
      .lead {{
        margin: 14px 0 0;
        max-width: 680px;
        color: var(--muted);
        font-size: 17px;
        line-height: 1.6;
      }}
      nav {{
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
        justify-content: flex-end;
      }}
      a {{
        color: inherit;
      }}
      .button {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 40px;
        padding: 10px 14px;
        border-radius: 8px;
        border: 1px solid var(--line);
        background: var(--surface);
        color: var(--ink);
        font-weight: 800;
        text-decoration: none;
      }}
      .button.primary {{
        border-color: var(--blue);
        background: var(--blue);
        color: #ffffff;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 16px;
      }}
      .card {{
        min-height: 210px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        gap: 20px;
        padding: 22px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--surface);
        box-shadow: 0 14px 30px rgba(15, 23, 42, 0.06);
      }}
      .card h2 {{
        margin: 0 0 8px;
        font-size: 21px;
      }}
      .card p {{
        margin: 0;
        color: var(--muted);
        line-height: 1.55;
      }}
      .meta {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin-top: 16px;
      }}
      .metric {{
        padding: 14px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: rgba(255, 255, 255, 0.72);
      }}
      .metric strong {{
        display: block;
        margin-bottom: 4px;
      }}
      .metric span {{
        color: var(--muted);
        font-size: 14px;
      }}
      .section-head {{
        margin: 30px 0 14px;
        display: flex;
        justify-content: space-between;
        align-items: end;
        gap: 16px;
      }}
      .section-head h2 {{
        margin: 0;
        font-size: 24px;
      }}
      .section-head p {{
        margin: 6px 0 0;
        color: var(--muted);
        line-height: 1.5;
      }}
      .template-grid {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 14px;
      }}
      .template-card {{
        min-height: 190px;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        gap: 18px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--surface);
        padding: 20px;
        box-shadow: 0 12px 24px rgba(15, 23, 42, 0.05);
      }}
      .template-card h3 {{
        margin: 10px 0 8px;
        font-size: 20px;
      }}
      .template-card p {{
        margin: 0;
        color: var(--muted);
        line-height: 1.55;
      }}
      .tag {{
        display: inline-flex;
        align-items: center;
        min-height: 26px;
        padding: 4px 9px;
        border-radius: 8px;
        background: #ecfdf5;
        color: #047857;
        font-size: 12px;
        font-weight: 900;
        text-transform: uppercase;
      }}
      footer {{
        margin-top: 28px;
        color: var(--muted);
        font-size: 14px;
      }}
      @media (max-width: 860px) {{
        h1 {{
          font-size: 36px;
        }}
        header {{
          flex-direction: column;
        }}
        nav {{
          justify-content: flex-start;
        }}
        .grid,
        .meta,
        .template-grid {{
          grid-template-columns: 1fr;
        }}
      }}
    </style>
  </head>
  <body>
    <main>
      <header>
        <div>
          <p class="eyebrow">Kaigo Widgets</p>
          <h1>Панель AI-виджетов</h1>
          <p class="lead">Управление клиентскими виджетами, диалогами и демо-чатом на Gemini 3 Flash.</p>
        </div>
        <nav>
          <a class="button primary" href="{escape(primary_href)}">{escape(primary_label)}</a>
          <a class="button" href="/w/demka">Демо-виджет</a>
          <a class="button" href="/real-time/">Real-time</a>
        </nav>
      </header>

      <section class="grid" aria-label="Основные разделы">
        <article class="card">
          <div>
            <h2>Кабинет клиента</h2>
            <p>Просмотр виджетов и последних диалогов по каждому проекту.</p>
          </div>
          <a class="button primary" href="{escape(primary_href)}">{escape(primary_label)}</a>
        </article>
        <article class="card">
          <div>
            <h2>Публичный виджет</h2>
            <p>Живой демо-чат, подключенный к текущей базе и модели Gemini.</p>
          </div>
          <a class="button" href="/w/demka">Открыть demka</a>
        </article>
        <article class="card">
          <div>
            <h2>Real-time агенты</h2>
            <p>Голосовой интерфейс остается отдельным сервисом внутри этого домена.</p>
          </div>
          <a class="button" href="/real-time/">Открыть раздел</a>
        </article>
      </section>

      <section class="meta" aria-label="Состояние сервисов">
        <div class="metric"><strong>Backend</strong><span><a href="/api/health">/api/health</a></span></div>
        <div class="metric"><strong>Gemini</strong><span><a href="/api/health/ai">/api/health/ai</a></span></div>
        <div class="metric"><strong>Админка</strong><span><a href="/admin">/admin</a></span></div>
      </section>

      <section class="section-head" aria-label="Демо-шаблоны">
        <div>
          <h2>Готовые демо-шаблоны</h2>
          <p>Эти виджеты уже лежат в базе, имеют свои промпты и публичные страницы.</p>
        </div>
        <a class="button" href="/admin/widgets">Управлять</a>
      </section>
      <section class="template-grid">
{widget_cards}
      </section>

      <footer>kaigo.space разделяет widgets на корне и real-time по пути /real-time/.</footer>
    </main>
  </body>
</html>"""
    return web.Response(text=html, content_type="text/html")


setup_logging()
logger = logging.getLogger(__name__)


async def create_app(config: AppConfig | None = None) -> web.Application:
    cfg = config or load_config()
    app = web.Application()
    app['config'] = cfg

    setup_session(app, SimpleCookieStorage(max_age=14 * 24 * 3600))
    app.on_startup.append(_init_history_db)

    init_db_signals(app)
    app.router.add_get('/', _home)
    setup_admin_routes(app)
    setup_api_routes(app)
    setup_client_routes(app)
    setup_widget_routes(app)
    return app


async def run() -> None:
    config = load_config()
    app = await create_app(config)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port)
    await site.start()
    logger.info("Server started on http://%s:%s", config.host, config.port)
    try:
        await asyncio.Future()
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    finally:
        await runner.cleanup()
        logger.info("Server stopped")


if __name__ == '__main__':
    asyncio.run(run())
