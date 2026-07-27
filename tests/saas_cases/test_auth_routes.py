from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import setup as setup_session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.oauth import OAuthIdentity
from app.auth.routes import OAUTH_PROVIDERS_KEY, setup_auth_routes
from app.auth.session_storage import DatabaseSessionStorage
from app.db.base import Base
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import Project


class FakeProvider:
    def __init__(self) -> None:
        self.transaction = None

    def authorization_url(self, transaction):
        self.transaction = transaction
        return f"https://identity.example/authorize?state={transaction.state}"

    async def exchange(self, transaction, callback):
        assert transaction.state == callback.state
        assert callback.code == "valid-code"
        return OAuthIdentity(
            provider="google",
            subject="subject-1",
            email="owner@example.com",
            email_verified=True,
            display_name="Owner",
            profile={"name": "Owner"},
        )


@pytest.mark.asyncio
async def test_draft_survives_oauth_round_trip_and_cookie_rotates() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    provider = FakeProvider()
    app[OAUTH_PROVIDERS_KEY] = {"google": provider}
    setup_session(
        app,
        DatabaseSessionStorage(
            cookie_name="kaigo_session", max_age=3600, secure=False, samesite="Lax"
        ),
    )
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()

    draft_response = await client.post(
        "/api/drafts",
        json={"url": "https://example.com", "brief": "Чат продаж"},
    )
    assert draft_response.status == 201
    draft = await draft_response.json()
    anonymous_cookie = client.session.cookie_jar.filter_cookies(client.make_url("/"))[
        "kaigo_session"
    ].value

    start_response = await client.get(
        f"/api/auth/google/start?draft_id={draft['id']}", allow_redirects=False
    )
    assert start_response.status == 302
    state = parse_qs(urlsplit(start_response.headers["Location"]).query)["state"][0]

    callback_response = await client.get(
        f"/api/auth/google/callback?state={state}&code=valid-code",
        allow_redirects=False,
    )
    assert callback_response.status == 302
    assert callback_response.headers["Location"].startswith("/studio?project=")
    authenticated_cookie = client.session.cookie_jar.filter_cookies(client.make_url("/"))[
        "kaigo_session"
    ].value
    assert authenticated_cookie != anonymous_cookie

    session_response = await client.get("/api/auth/session")
    payload = await session_response.json()
    assert payload["authenticated"] is True
    assert payload["email"] == "owner@example.com"

    async with factory() as database:
        project = (await database.execute(select(Project))).scalar_one()
        assert project.source_url == "https://example.com"
        assert project.brief == "Чат продаж"

    await client.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_private_or_loopback_draft_url_is_rejected() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()

    response = await client.post(
        "/api/drafts", json={"url": "http://127.0.0.1/private", "brief": ""}
    )

    assert response.status == 400
    assert "public" in (await response.json())["error"]
    await client.close()
    await engine.dispose()
