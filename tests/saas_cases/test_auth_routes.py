from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import get_session, setup as setup_session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.oauth import OAuthError, OAuthIdentity, VKOAuthProvider
from app.auth.routes import OAUTH_PROVIDERS_KEY, build_oauth_providers, setup_auth_routes
from app.auth.session_storage import DatabaseSessionStorage
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.config import AppConfig
from app.saas.models import (
    AnonymousDraft,
    AuthSession,
    OAuthState,
    Project,
    UserIdentity,
)


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


class InspectingFailureProvider(FakeProvider):
    def __init__(self, factory) -> None:
        super().__init__()
        self._factory = factory
        self.exchange_calls = 0
        self.state_was_committed = False

    async def exchange(self, transaction, callback):
        self.exchange_calls += 1
        async with self._factory() as database:
            state = (await database.execute(select(OAuthState))).scalar_one()
            self.state_was_committed = state.consumed_at is not None
        raise web.HTTPBadGateway(text="provider exchange failed")


class OAuthFailureProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.exchange_calls = 0

    async def exchange(self, transaction, callback):
        self.exchange_calls += 1
        raise OAuthError("token exchange included client-secret-value")


class UnexpectedFailureProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.exchange_calls = 0

    async def exchange(self, transaction, callback):
        self.exchange_calls += 1
        raise RuntimeError("transport included client-secret-value")


class ConcurrencyTrackingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def exchange(self, transaction, callback):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.05)
            return await super().exchange(transaction, callback)
        finally:
            self.active -= 1


class FakeVkProvider(FakeProvider):
    app_id = 54_721_213

    def __init__(self) -> None:
        super().__init__()
        self.callback = None

    async def exchange(self, transaction, callback):
        self.callback = callback
        assert transaction.state == callback.state
        assert callback.code == "valid-code"
        return OAuthIdentity(
            provider="vk",
            subject="vk-subject-1",
            email="owner@example.com",
            email_verified=True,
            display_name="Owner",
            profile={"name": "Owner"},
        )


def test_build_oauth_providers_includes_public_vk_app() -> None:
    providers = build_oauth_providers(
        AppConfig(
            database_url="sqlite+aiosqlite:///:memory:",
            environment="test",
            vk_oauth_app_id=54_721_213,
        )
    )

    assert set(providers) == {"vk"}
    assert isinstance(providers["vk"], VKOAuthProvider)


@pytest.mark.asyncio
async def test_vk_bootstrap_binds_draft_and_callback_passes_device_id() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    provider = FakeVkProvider()
    app[OAUTH_PROVIDERS_KEY] = {"vk": provider}
    setup_session(
        app,
        DatabaseSessionStorage(
            cookie_name="kaigo_session", max_age=3600, secure=False, samesite="Lax"
        ),
    )
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()

    try:
        draft_response = await client.post(
            "/api/drafts",
            json={"url": "https://example.com", "brief": "VK draft"},
        )
        draft_id = (await draft_response.json())["id"]

        bootstrap_response = await client.post(
            f"/api/auth/vk/bootstrap?draft_id={draft_id}"
        )

        assert bootstrap_response.status == 200
        assert bootstrap_response.headers["Cache-Control"] == "no-store"
        bootstrap = await bootstrap_response.json()
        assert set(bootstrap) == {
            "app_id",
            "redirect_uri",
            "state",
            "code_verifier",
        }
        assert bootstrap["app_id"] == 54_721_213
        assert bootstrap["redirect_uri"] == "https://kaigo.space/api/auth/vk/callback"
        assert bootstrap["state"]
        assert bootstrap["code_verifier"]
        assert not any("secret" in key or "service" in key for key in bootstrap)

        async with factory() as database:
            oauth_state = (await database.execute(select(OAuthState))).scalar_one()
            assert oauth_state.provider == "vk"
            assert str(oauth_state.draft_id) == draft_id
            assert oauth_state.pkce_verifier == bootstrap["code_verifier"]

        callback_response = await client.get(
            "/api/auth/vk/callback",
            params={
                "state": bootstrap["state"],
                "code": "valid-code",
                "device_id": "vk-device-1",
            },
            allow_redirects=False,
        )

        assert callback_response.status == 302
        assert callback_response.headers["Location"].startswith("/studio")
        assert provider.callback.device_id == "vk-device-1"
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_vk_callback_refuses_implicit_link_to_existing_email_account() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        tenant = Tenant(name="Existing owner", slug="existing-owner")
        database.add(tenant)
        await database.flush()
        user = User(
            tenant_id=tenant.id,
            email="owner@example.com",
            password_hash=None,
            role="tenant_admin",
        )
        database.add(user)
        await database.flush()
        database.add(
            UserIdentity(
                user_id=user.id,
                provider="google",
                provider_subject="existing-google-subject",
                email="owner@example.com",
                email_verified=True,
                profile={},
            )
        )

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {"vk": FakeVkProvider()}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        bootstrap = await (await client.post("/api/auth/vk/bootstrap")).json()
        callback = await client.get(
            "/api/auth/vk/callback",
            params={
                "state": bootstrap["state"],
                "code": "valid-code",
                "device_id": "vk-device-1",
            },
            allow_redirects=False,
        )

        assert callback.status == 302
        assert callback.headers["Location"] == (
            "/studio?auth_error=account_link_required"
        )
        async with factory() as database:
            identities = list((await database.scalars(select(UserIdentity))).all())
        assert [(identity.provider, identity.provider_subject) for identity in identities] == [
            ("google", "existing-google-subject")
        ]
    finally:
        await client.close()
        await engine.dispose()


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
    authenticated_cookie = client.session.cookie_jar.filter_cookies(
        client.make_url("/")
    )["kaigo_session"].value
    assert authenticated_cookie != anonymous_cookie

    session_response = await client.get("/api/auth/session")
    payload = await session_response.json()
    assert payload["authenticated"] is True
    assert payload["email"] == "owner@example.com"
    assert isinstance(payload["csrf_token"], str)
    assert len(payload["csrf_token"]) >= 32

    async with factory() as database:
        project = (await database.execute(select(Project))).scalar_one()
        assert project.source_url == "https://example.com/"
        assert project.brief == "Чат продаж"

    await client.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_oauth_start_ignores_and_clears_a_stale_pending_draft() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {"google": FakeProvider()}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        draft_response = await client.post(
            "/api/drafts",
            json={"url": "https://stale.example", "brief": ""},
        )
        draft_id = (await draft_response.json())["id"]
        async with factory() as database, database.begin():
            draft = await database.get(AnonymousDraft, UUID(draft_id))
            assert draft is not None
            await database.delete(draft)

        start = await client.get(
            f"/api/auth/google/start?draft_id={draft_id}",
            allow_redirects=False,
        )

        assert start.status == 302
        async with factory() as database:
            oauth_state = (await database.execute(select(OAuthState))).scalar_one()
        assert oauth_state.draft_id is None
        session_payload = await (await client.get("/api/auth/session")).json()
        assert session_payload["pending_draft_id"] is None
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_callback_error_is_consumed_and_redirected_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    provider = OAuthFailureProvider()
    app[OAUTH_PROVIDERS_KEY] = {"google": provider}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()
    caplog.set_level(logging.WARNING, logger="app.auth.routes")
    try:
        start = await client.get("/api/auth/google/start", allow_redirects=False)
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]

        callback = await client.get(
            "/api/auth/google/callback"
            f"?state={state}&error=access_denied"
            "&error_description=client-secret-value",
            allow_redirects=False,
        )

        assert callback.status == 302
        assert callback.headers["Location"] == "/studio?auth_error=access_denied"
        assert "client-secret-value" not in await callback.text()
        assert provider.exchange_calls == 0
        assert "provider=google" in caplog.text
        assert "public_code=access_denied" in caplog.text
        assert "client-secret-value" not in caplog.text
        async with factory() as database:
            oauth_state = (await database.execute(select(OAuthState))).scalar_one()
        assert oauth_state.consumed_at is not None
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_exchange_oauth_error_redirects_without_exposing_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    provider = OAuthFailureProvider()
    app[OAUTH_PROVIDERS_KEY] = {"google": provider}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()
    caplog.set_level(logging.WARNING, logger="app.auth.routes")
    try:
        start = await client.get("/api/auth/google/start", allow_redirects=False)
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]

        callback = await client.get(
            f"/api/auth/google/callback?state={state}&code=valid-code",
            allow_redirects=False,
        )

        assert callback.status == 302
        assert callback.headers["Location"] == "/studio?auth_error=oauth_failed"
        assert "client-secret-value" not in await callback.text()
        assert provider.exchange_calls == 1
        assert "provider=google" in caplog.text
        assert "public_code=oauth_failed" in caplog.text
        assert "failure=OAuthError" in caplog.text
        assert "client-secret-value" not in caplog.text

        retry = await client.get("/api/auth/google/start", allow_redirects=False)
        retry_state = parse_qs(urlsplit(retry.headers["Location"]).query)["state"][0]
        assert retry.status == 302
        assert retry_state != state
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_provider_exchange_unexpected_error_redirects_without_http_500_or_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    provider = UnexpectedFailureProvider()
    app[OAUTH_PROVIDERS_KEY] = {"google": provider}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()
    caplog.set_level(logging.WARNING, logger="app.auth.routes")
    try:
        start = await client.get("/api/auth/google/start", allow_redirects=False)
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]

        callback = await client.get(
            f"/api/auth/google/callback?state={state}&code=valid-code",
            allow_redirects=False,
        )

        assert callback.status == 302
        assert callback.headers["Location"] == "/studio?auth_error=provider_unavailable"
        assert "client-secret-value" not in await callback.text()
        assert provider.exchange_calls == 1
        assert "provider=google" in caplog.text
        assert "public_code=provider_unavailable" in caplog.text
        assert "failure=RuntimeError" in caplog.text
        assert "client-secret-value" not in caplog.text
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_new_url_supersedes_stale_pending_draft_and_callback_returns_new_project() -> (
    None
):
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
    try:
        stale = await (
            await client.post(
                "/api/drafts",
                json={"url": "https://stale.example.com", "brief": "Старый запрос"},
            )
        ).json()
        fresh = await (
            await client.post(
                "/api/drafts",
                json={"url": "https://fresh.example.com", "brief": "Новый запрос"},
            )
        ).json()
        assert fresh["id"] != stale["id"]

        start = await client.get(
            f"/api/auth/google/start?draft_id={fresh['id']}",
            allow_redirects=False,
        )
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]
        async with factory() as database:
            anonymous_session = (
                await database.execute(
                    select(AuthSession).where(AuthSession.revoked_at.is_(None))
                )
            ).scalar_one()
        binding = anonymous_session.payload["session"]
        assert isinstance(binding["oauth_state_id"], str)
        assert binding["oauth_session_binding"]
        assert state not in str(anonymous_session.payload)
        callback = await client.get(
            f"/api/auth/google/callback?state={state}&code=valid-code",
            allow_redirects=False,
        )

        assert callback.status == 302
        project_id = parse_qs(urlsplit(callback.headers["Location"]).query)["project"][
            0
        ]
        async with factory() as database:
            projects = list((await database.execute(select(Project))).scalars())
        assert len(projects) == 1
        assert str(projects[0].id) == project_id
        assert projects[0].source_url == "https://fresh.example.com/"
        assert projects[0].brief == "Новый запрос"
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com:8443",
        "https://example.com/?token=secret",
        "https://example.com/#section",
        "https://127.0.0.1/private",
        "https://localhost/private",
        "https://service.internal/private",
        r"https://example.com\private",
    ],
)
async def test_incompatible_draft_url_is_rejected(source_url: str) -> None:
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

    response = await client.post("/api/drafts", json={"url": source_url, "brief": ""})

    assert response.status == 400
    assert "public" in (await response.json())["error"]
    await client.close()
    await engine.dispose()


@pytest.mark.asyncio
async def test_draft_url_is_canonicalized_before_storage() -> None:
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
    try:
        response = await client.post(
            "/api/drafts",
            json={
                "url": "https://FAẞ.DE:443/Services",
                "brief": "  Спокойный консультант  ",
            },
        )
        assert response.status == 201
        payload = await response.json()
        assert payload["url"] == "https://fass.de/Services"
        assert payload["brief"] == "Спокойный консультант"
        async with factory() as database:
            draft = (await database.execute(select(AnonymousDraft))).scalar_one()
        assert draft.source_url == "https://fass.de/Services"
        assert draft.brief == "Спокойный консультант"
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_authenticated_browser_claims_bound_draft_once_with_csrf_and_keeps_brief() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add_all(
            [
                Tenant(id=1, name="Owner", slug="owner"),
                Tenant(id=2, name="Other", slug="other"),
                User(id=10, tenant_id=1, email="owner@example.com"),
                User(id=20, tenant_id=2, email="other@example.com"),
            ]
        )
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))

    async def login(request: web.Request) -> web.Response:
        user_id = int(request.match_info["user_id"])
        tenant_id = 1 if user_id == 10 else 2
        session = await get_session(request)
        session["user_id"] = user_id
        session["tenant_id"] = tenant_id
        session["email"] = f"user-{user_id}@example.com"
        session["csrf_token"] = "claim-csrf"
        return web.json_response({"authenticated": True})

    app.router.add_post("/test/login/{user_id}", login)
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    server = TestServer(app)
    browser = TestClient(server)
    other_browser = TestClient(server)
    await browser.start_server()
    await other_browser.start_server()
    try:
        draft_response = await browser.post(
            "/api/drafts",
            json={
                "url": "https://example.com/services",
                "brief": "Спокойный консультант",
            },
        )
        assert draft_response.status == 201
        draft_id = (await draft_response.json())["id"]
        await browser.post("/test/login/10")
        await other_browser.post("/test/login/10")

        without_csrf = await browser.post(f"/api/drafts/{draft_id}/claim")
        assert without_csrf.status == 403
        unbound = await other_browser.post(
            f"/api/drafts/{draft_id}/claim",
            headers={"X-CSRF-Token": "claim-csrf"},
        )
        assert unbound.status == 404

        first = await browser.post(
            f"/api/drafts/{draft_id}/claim",
            headers={"X-CSRF-Token": "claim-csrf"},
        )
        repeated = await browser.post(
            f"/api/drafts/{draft_id}/claim",
            headers={"X-CSRF-Token": "claim-csrf"},
        )
        assert first.status == 201
        assert repeated.status == 200
        first_payload = await first.json()
        repeated_payload = await repeated.json()
        assert repeated_payload == first_payload
        assert first_payload["project"]["source_url"] == "https://example.com/services"
        assert first_payload["project"]["brief"] == "Спокойный консультант"
        async with factory() as database:
            projects = list((await database.execute(select(Project))).scalars())
            draft = await database.get(AnonymousDraft, UUID(draft_id))
        assert len(projects) == 1
        assert str(projects[0].id) == first_payload["project"]["id"]
        assert draft is not None
        assert draft.claimed_by_user_id == 10

        await browser.post("/test/login/20")
        foreign_owner = await browser.post(
            f"/api/drafts/{draft_id}/claim",
            headers={"X-CSRF-Token": "claim-csrf"},
        )
        assert foreign_owner.status == 409
    finally:
        await browser.close()
        await other_browser.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_entry_prunes_only_expired_auth_records() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    now = datetime.now(UTC)
    expired_draft_id = uuid4()
    expired_state_id = uuid4()
    expired_session_id = uuid4()
    async with factory() as database:
        database.add_all(
            [
                AnonymousDraft(
                    id=expired_draft_id,
                    source_url="https://expired.example",
                    brief=None,
                    campaign={},
                    claim_token_digest="a" * 64,
                    expires_at=now - timedelta(minutes=1),
                ),
                OAuthState(
                    id=expired_state_id,
                    state_digest="b" * 64,
                    session_binding_digest="c" * 64,
                    provider="google",
                    pkce_verifier="verifier",
                    nonce="nonce",
                    return_path="/studio",
                    expires_at=now - timedelta(minutes=1),
                ),
                AuthSession(
                    id=expired_session_id,
                    token_digest="d" * 64,
                    payload={"session": {}},
                    expires_at=now - timedelta(minutes=1),
                ),
            ]
        )
        await database.commit()

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/api/drafts",
            json={"url": "https://fresh.example", "brief": ""},
        )
        assert response.status == 201
        async with factory() as database:
            assert await database.get(AnonymousDraft, expired_draft_id) is None
            assert await database.get(OAuthState, expired_state_id) is None
            assert await database.get(AuthSession, expired_session_id) is None
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_oauth_callback_is_bound_to_the_browser_that_started_login() -> None:
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
    server = TestServer(app)
    initiator = TestClient(server)
    other_browser = TestClient(server)
    await initiator.start_server()
    await other_browser.start_server()
    try:
        draft = await (
            await initiator.post(
                "/api/drafts", json={"url": "https://example.com", "brief": ""}
            )
        ).json()
        start = await initiator.get(
            f"/api/auth/google/start?draft_id={draft['id']}",
            allow_redirects=False,
        )
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]

        swapped = await other_browser.get(
            f"/api/auth/google/callback?state={state}&code=valid-code",
            allow_redirects=False,
        )
        assert swapped.status == 400
        assert "browser session" in (await swapped.json())["error"]

        legitimate = await initiator.get(
            f"/api/auth/google/callback?state={state}&code=valid-code",
            allow_redirects=False,
        )
        assert legitimate.status == 302
        assert legitimate.headers["Location"].startswith("/studio?project=")
        async with factory() as database:
            authenticated_session = (
                await database.execute(
                    select(AuthSession).where(AuthSession.revoked_at.is_(None))
                )
            ).scalar_one()
            stored_sessions = list(
                (await database.execute(select(AuthSession))).scalars()
            )
        authenticated_payload = authenticated_session.payload["session"]
        assert "oauth_state_id" not in authenticated_payload
        assert "oauth_session_binding" not in authenticated_payload
        assert all(
            "oauth_state_id" not in record.payload["session"]
            and "oauth_session_binding" not in record.payload["session"]
            for record in stored_sessions
        )
    finally:
        await initiator.close()
        await other_browser.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_oauth_callback_commits_one_time_claim_before_provider_exchange() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    provider = InspectingFailureProvider(factory)
    app[OAUTH_PROVIDERS_KEY] = {"google": provider}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(app, public_base_url="https://kaigo.space")
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        start = await client.get("/api/auth/google/start", allow_redirects=False)
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]

        failed = await client.get(
            f"/api/auth/google/callback?state={state}&code=valid-code",
            allow_redirects=False,
        )
        replay = await client.get(
            f"/api/auth/google/callback?state={state}&code=valid-code",
            allow_redirects=False,
        )

        assert failed.status == 502
        assert provider.state_was_committed is True
        assert replay.status == 400
        assert provider.exchange_calls == 1
        async with factory() as database:
            stored_state = (await database.execute(select(OAuthState))).scalar_one()
            assert stored_state.consumed_at is not None
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_oauth_callback_attempts_are_rate_limited_separately_from_start() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {"google": FakeProvider()}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(
        app,
        public_base_url="https://kaigo.space",
        entry_rate_limit_requests=1,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        start = await client.get("/api/auth/google/start", allow_redirects=False)
        first = await client.get(
            "/api/auth/google/callback?state=invalid&code=invalid",
            allow_redirects=False,
        )
        second = await client.get(
            "/api/auth/google/callback?state=invalid&code=invalid",
            allow_redirects=False,
        )

        assert start.status == 302
        assert first.status == 400
        assert second.status == 429
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_oauth_provider_exchange_concurrency_is_bounded(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oauth-concurrency.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    provider = ConcurrencyTrackingProvider()
    app[OAUTH_PROVIDERS_KEY] = {"google": provider}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(
        app,
        public_base_url="https://kaigo.space",
        oauth_callback_concurrency=1,
    )
    server = TestServer(app)
    first_browser = TestClient(server)
    second_browser = TestClient(server)
    await first_browser.start_server()
    await second_browser.start_server()
    try:
        first_start, second_start = await asyncio.gather(
            first_browser.get("/api/auth/google/start", allow_redirects=False),
            second_browser.get("/api/auth/google/start", allow_redirects=False),
        )
        first_state = parse_qs(urlsplit(first_start.headers["Location"]).query)["state"][0]
        second_state = parse_qs(urlsplit(second_start.headers["Location"]).query)["state"][0]

        first_callback, second_callback = await asyncio.gather(
            first_browser.get(
                f"/api/auth/google/callback?state={first_state}&code=valid-code",
                allow_redirects=False,
            ),
            second_browser.get(
                f"/api/auth/google/callback?state={second_state}&code=valid-code",
                allow_redirects=False,
            ),
        )

        assert first_callback.status == second_callback.status == 302
        assert provider.max_active == 1
    finally:
        await first_browser.close()
        await second_browser.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_entry_rejects_oversized_payloads_and_rate_limits_device() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(
        app,
        public_base_url="https://kaigo.space",
        entry_rate_limit_requests=2,
        entry_rate_limit_window_seconds=60,
        entry_max_body_bytes=512,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        oversized = await client.post(
            "/api/drafts",
            data=b"{" + b'"brief":"' + (b"x" * 600) + b'"}',
            headers={"Content-Type": "application/json", "User-Agent": "device-a"},
        )
        assert oversized.status == 413

        first = await client.post(
            "/api/drafts",
            json={"url": "https://one.example", "brief": ""},
            headers={"User-Agent": "device-a"},
        )
        assert first.status == 201
        limited = await client.post(
            "/api/drafts",
            json={"url": "https://two.example", "brief": ""},
            headers={"User-Agent": "device-a"},
        )
        assert limited.status == 429
        assert (await limited.json())["error"] == "too many entry requests"
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_common_user_agent_does_not_share_bucket_across_exact_client_ips() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(
        app,
        public_base_url="https://kaigo.space",
        entry_rate_limit_requests=1,
        trusted_proxy_cidrs=("127.0.0.0/8",),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        common_headers = {"User-Agent": "Common Chrome"}
        first = await client.post(
            "/api/drafts",
            json={"url": "https://one.example", "brief": ""},
            headers={**common_headers, "X-Forwarded-For": "198.51.100.1"},
        )
        second = await client.post(
            "/api/drafts",
            json={"url": "https://two.example", "brief": ""},
            headers={**common_headers, "X-Forwarded-For": "198.51.100.2"},
        )
        assert first.status == second.status == 201
    finally:
        await client.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_untrusted_peer_cannot_spoof_entry_client_ip_with_xff() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app[OAUTH_PROVIDERS_KEY] = {}
    setup_session(app, DatabaseSessionStorage(max_age=3600, secure=False))
    setup_auth_routes(
        app,
        public_base_url="https://kaigo.space",
        entry_rate_limit_requests=1,
        trusted_proxy_cidrs=(),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        first = await client.post(
            "/api/drafts",
            json={"url": "https://one.example", "brief": ""},
            headers={"X-Forwarded-For": "198.51.100.1"},
        )
        spoofed = await client.post(
            "/api/drafts",
            json={"url": "https://two.example", "brief": ""},
            headers={"X-Forwarded-For": "203.0.113.1"},
        )

        assert first.status == 201
        assert spoofed.status == 429
    finally:
        await client.close()
        await engine.dispose()
