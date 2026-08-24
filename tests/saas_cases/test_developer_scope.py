from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, get_session, setup as setup_session
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.admin.operator_auth import (
    developer_session_snapshot,
    optional_developer_principal,
)
from app.admin.developer import setup_developer_routes
from app.auth.routes import OAUTH_PROVIDERS_KEY, auth_session
from app.db.base import Base
from app.db.models import Tenant, User
from app.db.session import SESSION_FACTORY_KEY
from app.saas.models import UserIdentity
from builder_lab.forensics.config import GenerationForensicsConfig


@pytest.mark.asyncio
async def test_only_verified_google_or_yandex_allowlisted_session_gets_developer_scope(
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'developer.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database:
        database.add_all(
            [
                Tenant(id=1, name="Developer", slug="developer"),
                Tenant(id=2, name="Member", slug="member"),
                User(id=10, tenant_id=1, email="developer@example.com"),
                User(id=11, tenant_id=2, email="member@example.com"),
                UserIdentity(
                    user_id=10,
                    provider="yandex",
                    provider_subject="developer-subject",
                    email="Developer@Example.com",
                    email_verified=True,
                ),
                UserIdentity(
                    user_id=11,
                    provider="vk",
                    provider_subject="vk-subject",
                    email="developer@example.com",
                    email_verified=True,
                ),
            ]
        )
        await database.commit()

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = SimpleNamespace(
        generation_forensics=GenerationForensicsConfig(
            enabled=True,
            root=tmp_path / "forensics",
            ttl_hours=120,
            max_bytes=1_000_000,
            admin_emails=("developer@example.com",),
        )
    )
    app["public_auth_enabled"] = True
    app[OAUTH_PROVIDERS_KEY] = {"google": object()}
    setup_session(app, SimpleCookieStorage(cookie_name="developer-scope"))

    async def set_user(request: web.Request) -> web.Response:
        session = await get_session(request)
        session["user_id"] = int(request.match_info["user_id"])
        session["tenant_id"] = 1 if session["user_id"] == 10 else 2
        return web.json_response({"ok": True})

    async def inspect(request: web.Request) -> web.Response:
        principal = await optional_developer_principal(request)
        return web.json_response(
            {
                "user_id": principal.user_id if principal else None,
                "email": principal.email if principal else None,
                "snapshot": await developer_session_snapshot(request),
            }
        )

    app.router.add_get("/login/{user_id}", set_user)
    app.router.add_get("/inspect", inspect)
    app.router.add_get("/session", auth_session)
    setup_developer_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        developer = await client.get("/login/10")
        assert developer.status == 200
        assert await (await client.get("/inspect")).json() == {
            "user_id": 10,
            "email": "Developer@Example.com",
            "snapshot": {"enabled": True, "scope": "all_projects"},
        }
        session_payload = await (await client.get("/session")).json()
        assert session_payload["developer"] == {
            "enabled": True,
            "scope": "all_projects",
        }
        access = await client.get("/admin/developer")
        assert access.status == 200
        assert await access.json() == {
            "email": "Developer@Example.com",
            "scope": "all_projects",
            "url": "/studio",
        }

        member = TestClient(TestServer(app))
        await member.start_server()
        try:
            await member.get("/login/11")
            assert await (await member.get("/inspect")).json() == {
                "user_id": None,
                "email": None,
                "snapshot": {"enabled": False, "scope": None},
            }
            assert (await member.get("/admin/developer")).status == 403
        finally:
            await member.close()
    finally:
        await client.close()
        await engine.dispose()
