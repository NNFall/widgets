from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.config import AppConfig
from app.server import create_app as create_production_app
from scripts.run_saas_browser_acceptance import (
    ACCEPTANCE_PREFIX,
    create_browser_acceptance_app,
    require_loopback_host,
)


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.filterwarnings("ignore::aiohttp.web_app.NotAppKeyWarning")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "example.com"])
def test_browser_acceptance_refuses_non_loopback_bind(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        require_loopback_host(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_browser_acceptance_accepts_literal_loopback_bind(host: str) -> None:
    assert require_loopback_host(host) == host


@pytest.mark.asyncio
async def test_browser_acceptance_starts_real_studio_and_completes_mock_oauth(
    tmp_path: Path,
) -> None:
    app = await create_browser_acceptance_app(
        database=tmp_path / "browser-acceptance.db",
        frontend_dist=ROOT / "frontend" / "dist",
        public_base_url="http://127.0.0.1:8765",
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        studio = await client.get("/studio")
        studio_html = await studio.text()
        assert studio.status == 200
        assert '<div id="root"></div>' in studio_html
        asset_path = re.search(r'src="(/assets/[^"]+)"', studio_html)
        assert asset_path is not None
        assert (await client.get(asset_path.group(1))).status == 200

        draft = await client.post(
            "/api/drafts",
            json={"url": "https://acceptance.example/", "brief": "Browser flow"},
        )
        draft_payload = await draft.json()
        assert draft.status == 201

        start = await client.get(
            f"/api/auth/google/start?draft_id={draft_payload['id']}",
            allow_redirects=False,
        )
        assert start.status == 302
        consent_location = start.headers["Location"]
        assert consent_location.startswith("/__acceptance__/oauth/google?")
        state = parse_qs(urlsplit(consent_location).query)["state"][0]

        consent = await client.get(consent_location)
        consent_html = await consent.text()
        assert consent.status == 200
        assert "Approve local Google sign-in" in consent_html
        assert "client_secret" not in consent_html.lower()

        callback = await client.get(
            f"/api/auth/google/callback?state={state}&code=acceptance-code",
            allow_redirects=False,
        )
        assert callback.status == 302
        assert callback.headers["Location"].startswith("/studio?project=")
        project_id = parse_qs(urlsplit(callback.headers["Location"]).query)["project"][
            0
        ]

        session = await client.get("/api/auth/session")
        session_payload = await session.json()
        assert session_payload["authenticated"] is True
        assert session_payload["providers"] == ["google"]

        diagnostics = await client.get("/__acceptance__/diagnostics")
        diagnostic_payload = await diagnostics.json()
        assert diagnostics.status == 200
        assert diagnostic_payload["project_id"] == project_id
        assert "csrf_token" not in diagnostic_payload
        assert "session" not in diagnostic_payload
        assert "secret" not in (await diagnostics.text()).lower()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_browser_acceptance_serves_living_fold_favicon_routes(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "frontend-dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    versioned_path = assets / "favicon-living-fold-a96d189f.png"
    versioned_payload = b"versioned-living-fold"
    versioned_path.write_bytes(versioned_payload)
    stable_payloads = {
        "favicon.png": b"stable-png",
        "favicon.ico": b"stable-ico",
        "apple-touch-icon.png": b"stable-apple-touch-icon",
    }
    for filename, payload in stable_payloads.items():
        (dist / filename).write_bytes(payload)

    app = await create_browser_acceptance_app(
        database=tmp_path / "browser-favicon.db",
        frontend_dist=dist,
        public_base_url="http://127.0.0.1:8765",
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        versioned = await client.get("/assets/favicon-living-fold-a96d189f.png")
        assert versioned.status == 200
        assert await versioned.read() == versioned_payload

        for route, payload in stable_payloads.items():
            response = await client.get(f"/{route}")
            assert response.status == 200
            assert await response.read() == payload

        legacy = await client.get("/favicon.svg", allow_redirects=False)
        assert legacy.status == 308
        assert legacy.headers["Location"] == "/assets/favicon-living-fold-a96d189f.png"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_acceptance_routes_are_not_registered_by_the_production_app(
    tmp_path: Path,
) -> None:
    production = await create_production_app(
        AppConfig(database_url=f"sqlite+aiosqlite:///{tmp_path / 'production.db'}")
    )
    production_paths = {
        route.resource.canonical
        for route in production.router.routes()
        if route.resource is not None
    }

    assert all(not path.startswith(ACCEPTANCE_PREFIX) for path in production_paths)
    harness_source = (ROOT / "scripts" / "run_saas_browser_acceptance.py").read_text(
        encoding="utf-8"
    )
    assert "GOOGLE_OAUTH_CLIENT_SECRET" not in harness_source
    assert "YOOKASSA_SECRET_KEY" not in harness_source


@pytest.mark.asyncio
async def test_harness_ai_health_never_calls_a_provider_when_key_is_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = 0

    async def forbidden_provider_call(*, model=None):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError(f"provider call is forbidden: {model}")

    monkeypatch.setenv("GOOGLE_AI_API_KEY", "must-not-be-used-by-browser-acceptance")
    monkeypatch.setattr("core.ai_service.check_provider", forbidden_provider_call)
    app = await create_browser_acceptance_app(
        database=tmp_path / "browser-health.db",
        frontend_dist=ROOT / "frontend" / "dist",
        public_base_url="http://127.0.0.1:8765",
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        health = await client.get("/api/health")
        readiness = await client.get("/api/ready")
        response = await client.get("/api/health/ai?model=external-model")
        health_payload = await health.json()
        readiness_payload = await readiness.json()
        payload = await response.json()
    finally:
        await client.close()

    assert health.status == 200
    assert health_payload == {
        "status": "ok",
        "mode": "localhost_browser_acceptance",
    }
    assert readiness.status == 200
    assert readiness_payload == {
        "status": "ready",
        "database": {"status": "ok"},
        "worker": {"status": "ok"},
    }
    assert response.status == 200
    assert payload == {
        "status": "disabled",
        "mode": "localhost_browser_acceptance",
        "network": False,
    }
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_browser_acceptance_worker_activation_and_real_publication_flow(
    tmp_path: Path,
) -> None:
    app = await create_browser_acceptance_app(
        database=tmp_path / "browser-publication.db",
        frontend_dist=ROOT / "frontend" / "dist",
        public_base_url="http://127.0.0.1:8765",
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        draft = await client.post(
            "/api/drafts",
            json={"url": "https://publish.example/", "brief": "Publish browser flow"},
        )
        draft_id = (await draft.json())["id"]
        start = await client.get(
            f"/api/auth/google/start?draft_id={draft_id}", allow_redirects=False
        )
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]
        callback = await client.get(
            f"/api/auth/google/callback?state={state}&code=acceptance-code",
            allow_redirects=False,
        )
        project_id = parse_qs(urlsplit(callback.headers["Location"]).query)["project"][
            0
        ]
        session = await (await client.get("/api/auth/session")).json()
        csrf = session["csrf_token"]

        queued = await client.post(
            f"/api/projects/{project_id}/runs",
            json={"mode": "express"},
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": "browser-acceptance-run",
            },
        )
        assert queued.status == 202, await queued.text()

        diagnostics_payload = {}
        for _attempt in range(100):
            diagnostics_payload = await (
                await client.get("/__acceptance__/diagnostics")
            ).json()
            if (
                diagnostics_payload["run_status"] in {"completed", "failed"}
                and diagnostics_payload["run_trial_settlement"] is not None
            ):
                break
            await asyncio.sleep(0.05)
        assert diagnostics_payload["run_status"] == "completed"
        assert diagnostics_payload["artifact_revision"] == 5
        assert diagnostics_payload["run_trial_settlement"] == "consumed"
        assert diagnostics_payload["trial"] == {
            "state": "consumed",
            "granted_units": 1,
            "reserved_units": 0,
            "consumed_units": 1,
        }
        assert diagnostics_payload["composition_patterns"] == [
            "orb-pulse@1",
            "compact-chat@1",
            "paired-bubbles@1",
            "single-line-pill@1",
            "spring-reveal@1",
        ]

        run_id = diagnostics_payload["run_id"]
        preview = await client.get(
            f"/api/runs/{run_id}/preview/document"
            "?revision=5&channel=browser_acceptance_channel_123"
        )
        assert preview.status == 200
        assert "data-kaigo-runtime-input" in await preview.text()

        activation = await client.get("/__acceptance__/subscription")
        activation_html = await activation.text()
        activation_csrf = re.search(r'name="csrf" value="([^"]+)"', activation_html)
        assert activation.status == 200
        assert activation_csrf is not None
        activated = await client.post(
            "/__acceptance__/subscription/activate",
            data={"csrf": activation_csrf.group(1)},
            allow_redirects=False,
        )
        assert activated.status == 302
        assert activated.headers["Location"] == "/studio#studio-publication"
        subscription = await (await client.get("/api/billing/subscription")).json()
        assert subscription["subscription"]["status"] == "active"

        published = await client.post(
            f"/api/projects/{project_id}/publish",
            json={
                "artifact_id": diagnostics_payload["artifact_id"],
                "revision": diagnostics_payload["artifact_revision"],
            },
            headers={"X-CSRF-Token": csrf},
        )
        publication = await published.json()
        assert published.status == 201, publication
        assert publication["stable_key"]
        assert publication["artifact_id"] == diagnostics_payload["artifact_id"]
        assert publication["revision"] == diagnostics_payload["artifact_revision"]

        embed = await client.get(f"/embed/{publication['stable_key']}.js")
        embed_script = await embed.text()
        assert embed.status == 200
        runtime_prefix = re.search(
            r"new URL\('(/runtime/)' \+ encodeURIComponent\(key\)",
            embed_script,
        )
        assert runtime_prefix is not None
        runtime = await client.get(
            f"{runtime_prefix.group(1)}{publication['stable_key']}"
        )
        runtime_document = await runtime.text()
        assert runtime.status == 200
        assert 'data-kaigo-runtime="kaigo-widget"' in runtime_document
        assert f'data-kaigo-release="{publication["stable_key"]}"' in runtime_document
        assert f"const revision={publication['revision']};" in runtime_document
        encoded_inner = re.search(r"atob\('([^']+)'\)", runtime_document)
        assert encoded_inner is not None
        inner_document = base64.b64decode(encoded_inner.group(1)).decode("utf-8")
        assert "Kaigo assistant" in inner_document
        assert f"const revision = {publication['revision']};" in inner_document

        final_diagnostics = await (
            await client.get("/__acceptance__/diagnostics")
        ).json()
        assert final_diagnostics["stable_key"] == publication["stable_key"]
        assert final_diagnostics["subscription_status"] == "active"
    finally:
        await client.close()
