from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp_session import SimpleCookieStorage, setup as setup_session

from app.admin.operator_auth import require_read_operator, require_verified_operator
from builder_lab.forensics.config import GenerationForensicsConfig

@pytest.mark.asyncio
async def test_service_token_principal_accepts_only_exact_configured_bearer(
    tmp_path,
) -> None:
    token = "t" * 32
    app = web.Application()
    app["config"] = SimpleNamespace(
        operator_read_token=token,
        generation_forensics=GenerationForensicsConfig(
            enabled=True,
            root=tmp_path / "forensics",
            ttl_hours=120,
            max_bytes=1_000_000,
            admin_emails=(),
        ),
    )
    setup_session(app, SimpleCookieStorage(cookie_name="read-token-test"))

    async def read(request: web.Request) -> web.Response:
        principal = await require_read_operator(request)
        return web.json_response(
            {"user_id": principal.user_id, "auth_method": principal.auth_method}
        )

    async def oauth_only(request: web.Request) -> web.Response:
        await require_verified_operator(request)
        return web.json_response({"ok": True})

    app.router.add_get("/read", read)
    app.router.add_get("/oauth-only", oauth_only)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        valid = await client.get(
            "/read", headers={"Authorization": f"Bearer {token}"}
        )
        assert valid.status == 200
        assert await valid.json() == {
            "user_id": None,
            "auth_method": "service_token",
        }

        for header in (
            None,
            "Basic " + token,
            "Bearer " + "x" * 32,
            "Bearer " + token + " ",
        ):
            headers = {} if header is None else {"Authorization": header}
            rejected = await client.get("/read", headers=headers)
            assert rejected.status == 401
            assert token not in await rejected.text()

        mutation = await client.get(
            "/oauth-only", headers={"Authorization": f"Bearer {token}"}
        )
        assert mutation.status == 401
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_missing_service_token_configuration_keeps_read_route_oauth_only(
    tmp_path,
) -> None:
    app = web.Application()
    app["config"] = SimpleNamespace(
        operator_read_token=None,
        generation_forensics=GenerationForensicsConfig(
            enabled=True,
            root=tmp_path / "forensics",
            ttl_hours=120,
            max_bytes=1_000_000,
            admin_emails=(),
        ),
    )
    setup_session(app, SimpleCookieStorage(cookie_name="read-token-disabled"))

    async def read(request: web.Request) -> web.Response:
        await require_read_operator(request)
        return web.json_response({"ok": True})

    app.router.add_get("/read", read)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get(
            "/read", headers={"Authorization": "Bearer " + "t" * 32}
        )
        assert response.status == 401
    finally:
        await client.close()
