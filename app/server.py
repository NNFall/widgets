import asyncio
import logging

from aiohttp import web
from aiohttp_session import setup as setup_session, SimpleCookieStorage

from app.admin.routes import setup_admin_routes
from app.api.routes import setup_api_routes
from app.config import AppConfig, load_config
from app.db import init_db_signals
from app.logging_config import setup_logging
from app.widgets.routes import setup_widget_routes
from app.client.routes import setup_client_routes
from core import database as history_db

async def _init_history_db(app: web.Application) -> None:
    await history_db.init_db()


setup_logging()
logger = logging.getLogger(__name__)


async def create_app(config: AppConfig | None = None) -> web.Application:
    cfg = config or load_config()
    app = web.Application()
    app['config'] = cfg

    setup_session(app, SimpleCookieStorage(max_age=14 * 24 * 3600))
    app.on_startup.append(_init_history_db)

    init_db_signals(app)
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
