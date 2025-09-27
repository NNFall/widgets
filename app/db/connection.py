from __future__ import annotations

from aiohttp import web

from .session import init_engine


def init_db_signals(app: web.Application) -> None:
    app.cleanup_ctx.append(init_engine)
