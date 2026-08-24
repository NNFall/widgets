from __future__ import annotations

from aiohttp import web

from app.admin.operator_auth import optional_developer_principal, require_verified_operator


async def developer_access_page(request: web.Request) -> web.Response:
    """Return the protected browser-only developer access capability.

    Access is derived from the verified OAuth session and the server-side
    allowlist. No URL key or bearer token is accepted here.
    """

    await require_verified_operator(request)
    principal = await optional_developer_principal(request)
    if principal is None:  # pragma: no cover - the shared guard is authoritative
        raise web.HTTPForbidden(
            text='{"error":{"code":"developer_access_denied"}}',
            content_type="application/json",
            headers={"Cache-Control": "no-store"},
        )
    return web.json_response(
        {
            "email": principal.email,
            "scope": "all_projects",
            "url": "/studio",
        },
        headers={"Cache-Control": "no-store"},
    )


def setup_developer_routes(app: web.Application) -> None:
    app.router.add_get("/admin/developer", developer_access_page)


__all__ = ["developer_access_page", "setup_developer_routes"]
