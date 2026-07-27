from __future__ import annotations

from datetime import UTC, datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.session_storage import DatabaseSessionStorage
from app.db.base import Base
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import AuthSession


@pytest.mark.asyncio
async def test_database_never_contains_raw_cookie_and_session_restores() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    storage = DatabaseSessionStorage(cookie_name="kaigo_session", max_age=3600, secure=True)

    first_request = make_mocked_request("GET", "/", app=app)
    session = await storage.new_session()
    session["draft_id"] = "draft-1"
    first_response = web.Response()
    await storage.save_session(first_request, first_response, session)
    raw = first_response.cookies["kaigo_session"].value

    async with factory() as database:
        persisted = (await database.execute(select(AuthSession))).scalar_one()
        assert persisted.token_digest != raw
        assert raw not in persisted.payload.values()
        assert persisted.revoked_at is None

    restored_request = make_mocked_request(
        "GET",
        "/",
        headers={"Cookie": f"kaigo_session={raw}"},
        app=app,
    )
    restored = await storage.load_session(restored_request)
    assert restored["draft_id"] == "draft-1"
    assert restored.identity == raw

    await engine.dispose()


@pytest.mark.asyncio
async def test_invalidation_revokes_server_record_and_expires_cookie() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    storage = DatabaseSessionStorage(cookie_name="kaigo_session", max_age=3600, secure=True)

    request = make_mocked_request("GET", "/", app=app)
    session = await storage.new_session()
    session["user_id"] = 42
    response = web.Response()
    await storage.save_session(request, response, session)
    raw = response.cookies["kaigo_session"].value

    authenticated_request = make_mocked_request(
        "POST", "/logout", headers={"Cookie": f"kaigo_session={raw}"}, app=app
    )
    restored = await storage.load_session(authenticated_request)
    restored.invalidate()
    logout_response = web.Response()
    await storage.save_session(authenticated_request, logout_response, restored)

    async with factory() as database:
        persisted = (await database.execute(select(AuthSession))).scalar_one()
        assert persisted.revoked_at is not None
        assert persisted.revoked_at <= datetime.now(UTC).replace(tzinfo=None) or persisted.revoked_at.tzinfo is not None
    assert logout_response.cookies["kaigo_session"]["max-age"] == "0"

    await engine.dispose()
