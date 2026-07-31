from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from scripts import run_publication_https_canary as canary


def _environment(tmp_path: Path) -> dict[str, str]:
    cookie_file = tmp_path / "owner-cookie.txt"
    cookie_file.write_text("kaigo_session=opaque-cookie-value", encoding="utf-8")
    if os.name == "posix":
        cookie_file.chmod(0o600)
    return {
        "KAIGO_CANARY_APP_ORIGIN": "https://kaigo.space",
        "KAIGO_CANARY_ALLOWED_ORIGIN": "https://canary.kaigo.space",
        "KAIGO_CANARY_DENIED_ORIGIN": "https://denied-canary.kaigo.space",
        "KAIGO_CANARY_PROJECT_ID": str(uuid4()),
        "KAIGO_CANARY_BASELINE_VERSION_ID": str(uuid4()),
        "KAIGO_CANARY_CANDIDATE_VERSION_ID": str(uuid4()),
        "KAIGO_CANARY_COOKIE_FILE": str(cookie_file),
        "KAIGO_CANARY_EVIDENCE_FILE": str(tmp_path / "evidence.json"),
    }


@pytest.mark.parametrize(
    "value",
    [
        "http://kaigo.space",
        "https://user:password@kaigo.space",
        "https://kaigo.space/path",
        "https://kaigo.space/?query=1",
        "https://kaigo.space/#fragment",
        "//kaigo.space",
    ],
)
def test_parse_https_origin_rejects_non_origin_values(value: str) -> None:
    with pytest.raises(ValueError):
        canary.parse_https_origin(value, field="TEST_ORIGIN")


def test_parse_https_origin_normalizes_default_tls_port() -> None:
    assert (
        canary.parse_https_origin(
            "https://KAIGO.space:443/", field="TEST_ORIGIN"
        )
        == "https://kaigo.space"
    )


def test_load_config_requires_three_distinct_origins_and_distinct_versions(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    config = canary.load_config(environment)
    assert config.app_origin == "https://kaigo.space"

    duplicate_origin = dict(environment)
    duplicate_origin["KAIGO_CANARY_DENIED_ORIGIN"] = duplicate_origin[
        "KAIGO_CANARY_ALLOWED_ORIGIN"
    ]
    with pytest.raises(ValueError, match="origins must be distinct"):
        canary.load_config(duplicate_origin)

    duplicate_version = dict(environment)
    duplicate_version["KAIGO_CANARY_CANDIDATE_VERSION_ID"] = duplicate_version[
        "KAIGO_CANARY_BASELINE_VERSION_ID"
    ]
    with pytest.raises(ValueError, match="version IDs must be distinct"):
        canary.load_config(duplicate_version)


def test_cookie_is_read_only_from_owner_only_regular_file(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    config = canary.load_config(environment)
    assert canary.read_owner_cookie(config.cookie_file).startswith("kaigo_session=")

    empty = tmp_path / "empty-cookie.txt"
    empty.write_text("", encoding="utf-8")
    if os.name == "posix":
        empty.chmod(0o600)
    with pytest.raises(ValueError, match="cookie file is empty"):
        canary.read_owner_cookie(empty)

    if os.name == "posix":
        unsafe = tmp_path / "unsafe-cookie.txt"
        unsafe.write_text("session=value", encoding="utf-8")
        unsafe.chmod(0o644)
        with pytest.raises(ValueError, match="owner-only"):
            canary.read_owner_cookie(unsafe)

        symlink = tmp_path / "cookie-link.txt"
        symlink.symlink_to(Path(environment["KAIGO_CANARY_COOKIE_FILE"]))
        with pytest.raises(ValueError, match="symlink"):
            canary.read_owner_cookie(symlink)

        linked_environment = dict(environment)
        linked_environment["KAIGO_CANARY_COOKIE_FILE"] = str(symlink)
        linked_config = canary.load_config(linked_environment)
        with pytest.raises(ValueError, match="symlink"):
            canary.read_owner_cookie(linked_config.cookie_file)


def test_sanitized_evidence_rejects_secret_or_content_fields(tmp_path: Path) -> None:
    allowed = {
        "status": "passed",
        "started_at": "2026-07-31T00:00:00+00:00",
        "finished_at": "2026-07-31T00:02:00+00:00",
        "origins": {
            "app": "https://kaigo.space",
            "allowed": "https://canary.kaigo.space",
            "denied": "https://denied-canary.kaigo.space",
        },
        "preflight": {"authenticated": True, "entitled": True},
        "publication": {
            "stable_key": "public_key_12345678901234567890",
            "baseline_release_id": str(uuid4()),
            "candidate_release_id": str(uuid4()),
            "rollback_release_id": str(uuid4()),
        },
        "browser": {
            "allowed_loaded": True,
            "launcher_roundtrip": True,
            "chat_replied": True,
            "candidate_loaded": True,
            "rollback_loaded": True,
            "denied_blocked": True,
        },
        "responses": {"auth": 200, "publish_baseline": 201},
    }
    path = tmp_path / "evidence.json"
    canary.write_evidence(path, allowed)
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "passed"

    for forbidden in (
        "cookie",
        "csrf_token",
        "capability",
        "authorization",
        "raw_html",
        "prompt",
        "response_text",
    ):
        contaminated = dict(allowed)
        contaminated[forbidden] = "secret"
        with pytest.raises(ValueError, match="forbidden evidence field"):
            canary.write_evidence(path, contaminated)


def test_missing_configuration_returns_machine_readable_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in canary.REQUIRED_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)

    assert canary.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    assert payload["reason"] == "invalid_configuration"


def test_unexpected_runtime_failure_is_machine_readable_blocked_without_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    environment = _environment(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    async def fail(_config) -> dict[str, object]:
        raise RuntimeError("opaque-cookie-value must never be printed")

    monkeypatch.setattr(canary, "run", fail)
    assert canary.main() == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {
        "status": "blocked",
        "reason": "acceptance_not_proven",
        "detail": "RuntimeError",
    }
    assert "opaque-cookie-value" not in captured.out
    evidence = json.loads(
        Path(environment["KAIGO_CANARY_EVIDENCE_FILE"]).read_text(encoding="utf-8")
    )
    assert evidence["status"] == "blocked"


@pytest.mark.asyncio
async def test_authenticated_request_rejects_redirect_without_following(
    unused_tcp_port: int,
) -> None:
    from aiohttp import ClientSession, web

    redirected = False

    async def start(_request: web.Request) -> web.Response:
        raise web.HTTPFound("/secret-target")

    async def target(_request: web.Request) -> web.Response:
        nonlocal redirected
        redirected = True
        return web.json_response({"secret": "must not be reached"})

    app = web.Application()
    app.router.add_get("/start", start)
    app.router.add_get("/secret-target", target)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()
    try:
        async with ClientSession() as session:
            with pytest.raises(canary.CanaryBlocked, match="redirect"):
                await canary.owner_json_request(
                    session,
                    "GET",
                    f"http://127.0.0.1:{unused_tcp_port}/start",
                    cookie_header="session=opaque",
                )
        assert redirected is False
    finally:
        await runner.cleanup()
