from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base

SESSION_FACTORY_KEY = "db_session_factory"
ENGINE_KEY = "db_engine"


def _build_engine(database_url: str) -> AsyncEngine:
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return create_async_engine(database_url, future=True, echo=False)


async def init_engine(app: web.Application) -> AsyncIterator[None]:
    config = app.get("config")
    if config is None:
        raise RuntimeError("Application config is not available")

    engine = _build_engine(config.database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    app[ENGINE_KEY] = engine
    app[SESSION_FACTORY_KEY] = session_factory

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    try:
        yield
    finally:
        await engine.dispose()


def get_session_factory(app: web.Application) -> async_sessionmaker[AsyncSession]:
    factory = app.get(SESSION_FACTORY_KEY)
    if factory is None:
        raise RuntimeError("Session factory is not initialised")
    return factory


@asynccontextmanager
async def session_scope(app: web.Application):
    session_factory = get_session_factory(app)
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:  # noqa: BLE001
            await session.rollback()
            raise
