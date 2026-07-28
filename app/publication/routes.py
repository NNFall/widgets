from __future__ import annotations

import json
from uuid import UUID

from aiohttp import web
from app.db.session import get_session_factory
from app.projects.routes import _require_csrf, _scope, _uuid
from app.publication.service import (
    InvalidAllowedDomain,
    InvalidPublicationArtifact,
    PublicationIdentityUnverified,
    PublicationNotFound,
    PublicationService,
    PublicationUpgradeRequired,
    ReleaseCorrupt,
)
from app.widgets.loader import render_loader, render_runtime


def _error(code: str, *, message: str | None = None) -> dict:
    payload = {"code": code}
    if message:
        payload["message"] = message
    return {"error": payload}


async def _body(request: web.Request) -> dict:
    try:
        payload = await request.json()
    except Exception as error:  # noqa: BLE001
        raise web.HTTPBadRequest(
            text=json.dumps(_error("invalid_json")), content_type="application/json"
        ) from error
    if not isinstance(payload, dict):
        raise web.HTTPBadRequest(
            text=json.dumps(_error("invalid_body")), content_type="application/json"
        )
    return payload


def _service(request: web.Request) -> PublicationService:
    config = request.app.get("config")
    allow_insecure = bool(
        config is not None
        and getattr(config, "publication_allow_insecure_origins", False)
        and getattr(config, "environment", "production") != "production"
    )
    return PublicationService(
        get_session_factory(request.app), allow_insecure_origins=allow_insecure
    )


def _published_payload(request: web.Request, published, *, created: bool) -> web.Response:
    base = f"{request.scheme}://{request.host}"
    return web.json_response(
        {
            "publication_id": str(published.publication_id),
            "release_id": str(published.release_id),
            "artifact_id": str(published.artifact_id),
            "stable_key": published.stable_key,
            "revision": published.revision,
            "allowed_domains": list(published.allowed_domains),
            "checksum": published.checksum,
            "embed_url": f"{base}/embed/{published.stable_key}.js",
            "runtime_url": f"{base}/runtime/{published.stable_key}",
        },
        status=201 if created else 200,
    )


async def publish_project(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request, verified=True)
    await _require_csrf(request)
    project_id = _uuid(request.match_info["project_id"])
    payload = await _body(request)
    artifact_id = payload.get("artifact_id")
    revision = payload.get("revision")
    try:
        parsed_artifact_id = UUID(artifact_id) if isinstance(artifact_id, str) else None
        parsed_revision = int(revision) if revision is not None else None
        allowed_domains = payload.get("allowed_domains") if "allowed_domains" in payload else None
        published = await _service(request).publish(
            project_id,
            actor_user_id=user_id,
            tenant_id=tenant_id,
            artifact_id=parsed_artifact_id,
            revision=parsed_revision,
            allowed_domains=allowed_domains,
        )
    except (TypeError, ValueError, InvalidAllowedDomain, InvalidPublicationArtifact) as error:
        return web.json_response(_error("publication_invalid", message=str(error)), status=422)
    except PublicationNotFound:
        raise web.HTTPNotFound(text=json.dumps(_error("not_found")), content_type="application/json")
    except PublicationUpgradeRequired:
        return web.json_response(_error("upgrade_required"), status=402)
    except PublicationIdentityUnverified:
        return web.json_response(_error("verified_oauth_required"), status=403)
    return _published_payload(request, published, created=True)


async def rollback_publication(request: web.Request) -> web.Response:
    user_id, tenant_id = await _scope(request, verified=True)
    await _require_csrf(request)
    publication_id = _uuid(request.match_info["publication_id"])
    payload = await _body(request)
    try:
        target = UUID(str(payload.get("target_release_id", "")))
        published = await _service(request).rollback(
            publication_id,
            actor_user_id=user_id,
            tenant_id=tenant_id,
            target_release_id=target,
        )
    except ValueError:
        return web.json_response(_error("invalid_release_id"), status=400)
    except (PublicationNotFound, ReleaseCorrupt):
        raise web.HTTPNotFound(text=json.dumps(_error("not_found")), content_type="application/json")
    except PublicationUpgradeRequired:
        return web.json_response(_error("upgrade_required"), status=402)
    except PublicationIdentityUnverified:
        return web.json_response(_error("verified_oauth_required"), status=403)
    return _published_payload(request, published, created=False)


async def embed_loader(request: web.Request) -> web.Response:
    key = request.match_info["stable_key"]
    try:
        await _service(request).resolve(key)
    except (PublicationNotFound, ReleaseCorrupt):
        raise web.HTTPNotFound(text=json.dumps(_error("not_found")), content_type="application/json")
    return web.Response(
        text=render_loader(),
        content_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


async def runtime(request: web.Request) -> web.Response:
    key = request.match_info["stable_key"]
    try:
        published = await _service(request).resolve(key)
    except (PublicationNotFound, ReleaseCorrupt):
        raise web.HTTPNotFound(text=json.dumps(_error("not_found")), content_type="application/json")
    ancestors = " ".join(published.allowed_domains) if published.allowed_domains else "'none'"
    csp = (
        "default-src 'none'; style-src 'unsafe-inline' data:; "
        "script-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; "
        "connect-src 'none'; media-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'; frame-src blob:; navigate-to 'none'; "
        f"frame-ancestors {ancestors}"
    )
    return web.Response(
        text=render_runtime(published),
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": csp,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


def setup_publication_routes(app: web.Application) -> None:
    app.router.add_post("/api/projects/{project_id}/publish", publish_project)
    app.router.add_post(
        "/api/publications/{publication_id}/rollback", rollback_publication
    )
    app.router.add_get("/embed/{stable_key}.js", embed_loader)
    app.router.add_get("/runtime/{stable_key}", runtime)


__all__ = ["setup_publication_routes"]
