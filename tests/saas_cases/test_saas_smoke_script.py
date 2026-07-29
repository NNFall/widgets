from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]


class _CanaryHandler(BaseHTTPRequestHandler):
    health_status = 200
    readiness_status = 200
    readiness_redirect_url: str | None = None
    authenticated = False
    webhook_first_processed = True
    requests: list[tuple[str, str, str | None]] = []

    def log_message(self, format, *args):  # noqa: A002
        return None

    def _response(self, status: int, body: str, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        self.__class__.requests.append(("GET", path, self.headers.get("Cookie")))
        if path == "/api/health":
            self._response(
                self.__class__.health_status,
                json.dumps({"status": "ok"}),
                "application/json",
            )
        elif path == "/api/ready":
            if self.__class__.readiness_redirect_url:
                self.send_response(302)
                self.send_header("Location", self.__class__.readiness_redirect_url)
                self.end_headers()
                return
            self._response(
                self.__class__.readiness_status,
                json.dumps(
                    {
                        "status": (
                            "ready"
                            if self.__class__.readiness_status == 200
                            else "not_ready"
                        ),
                        "database": {"status": "ok"},
                        "worker": {
                            "status": "ok",
                            "latest_heartbeat_at": "2026-07-28T19:00:00+00:00",
                            "queued_runs": 0,
                            "running_runs": 0,
                            "stale_running_runs": 0,
                        },
                    }
                ),
                "application/json",
            )
        elif path in {"/studio", "/studio/"}:
            self._response(200, "<html><title>Kaigo Studio</title></html>", "text/html")
        elif path == "/api/auth/session":
            self._response(
                200,
                json.dumps(
                    {
                        "enabled": True,
                        "authenticated": self.__class__.authenticated,
                        "csrf_token": (
                            "canary-csrf" if self.__class__.authenticated else None
                        ),
                        "providers": ["google", "yandex"],
                    }
                ),
                "application/json",
            )
        elif path == "/api/projects/project-123":
            self._response(
                200,
                json.dumps(
                    {
                        "id": "project-123",
                        "active_run": {
                            "id": "run-456",
                            "status": "completed",
                        },
                    }
                ),
                "application/json",
            )
        elif path == "/api/runs/run-456":
            self._response(
                200,
                json.dumps(
                    {
                        "id": "run-456",
                        "project_id": "project-123",
                        "status": "completed",
                        "latest_sequence": 8,
                    }
                ),
                "application/json",
            )
        elif path == "/api/runs/run-456/events":
            self._response(
                200,
                'id: 8\nevent: run.completed\ndata: {"sequence":8}\n\n',
                "text/event-stream",
            )
        elif path == "/api/runs/run-456/preview":
            self._response(
                200,
                json.dumps({"artifact": {"revision": 5}}),
                "application/json",
            )
        elif path == "/api/runs/run-456/preview/document":
            self._response(
                200,
                '<html><body data-kaigo-runtime-input="true"></body></html>',
                "text/html",
            )
        elif path == "/embed/public-key.js":
            self._response(
                200, "document.createElement('iframe');", "application/javascript"
            )
        elif path == "/runtime/public-key":
            self._response(
                200,
                '<html><body data-kaigo-runtime="kaigo-widget"></body></html>',
                "text/html",
            )
        else:
            self._response(404, "not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        self.__class__.requests.append(("POST", path, self.headers.get("Cookie")))
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size) or b"{}")
        if path == "/api/billing/webhooks/yookassa" and payload == {
            "payment_id": "verified-fixture"
        }:
            self._response(
                200,
                json.dumps(
                    {
                        "accepted": True,
                        "processed": (
                            self.__class__.webhook_first_processed
                            if len(
                                [
                                    request
                                    for request in self.__class__.requests
                                    if request[0] == "POST"
                                ]
                            )
                            == 1
                            else False
                        ),
                    }
                ),
                "application/json",
            )
        else:
            self._response(404, "not found", "text/plain")


def _serve_canary():
    _CanaryHandler.requests = []
    _CanaryHandler.health_status = 200
    _CanaryHandler.readiness_status = 200
    _CanaryHandler.readiness_redirect_url = None
    _CanaryHandler.authenticated = False
    _CanaryHandler.webhook_first_processed = True
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CanaryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class _ReadinessRedirectCaptureHandler(BaseHTTPRequestHandler):
    readiness_tokens: list[str | None] = []

    def log_message(self, format, *args):  # noqa: A002
        return None

    def do_GET(self) -> None:  # noqa: N802
        self.__class__.readiness_tokens.append(
            self.headers.get("X-Kaigo-Readiness-Token")
        )
        body = json.dumps(
            {
                "status": "ready",
                "database": {"status": "ok"},
                "worker": {"status": "ok"},
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


def _serve_redirect_capture():
    _ReadinessRedirectCaptureHandler.readiness_tokens = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ReadinessRedirectCaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _run_canary(base_url: str, *arguments: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "smoke_saas_foundation.py"),
            "--base-url",
            base_url,
            *arguments,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, **(env or {})},
    )


def test_smoke_script_emits_compact_json_and_never_requires_credentials(
    tmp_path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "smoke_saas_foundation.py"),
            "--database",
            str(tmp_path / "smoke.db"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    evidence = json.loads(completed.stdout)
    assert evidence["ok"] is True
    assert evidence["oauth"] == {
        "claimed_fresh_draft": True,
        "stale_draft_unclaimed": True,
    }
    assert evidence["generation"]["state"] == "completed"
    assert evidence["billing"]["webhook_replay_processed"] is False
    assert evidence["publication"]["rollback_restored_first_release"] is True


def test_deployed_canary_checks_public_contract_and_reports_real_oauth_as_skipped() -> (
    None
):
    server = _serve_canary()
    try:
        completed = _run_canary(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    evidence = json.loads(completed.stdout)
    assert evidence["ok"] is True
    assert evidence["mode"] == "deployed"
    assert evidence["checks"]["health"]["status"] == 200
    assert evidence["checks"]["readiness"]["status"] == 200
    assert evidence["checks"]["studio"]["content_type"] == "text/html"
    assert evidence["checks"]["auth_session"]["providers"] == ["google", "yandex"]
    assert evidence["oauth"] == {
        "status": "skipped",
        "reason": "real browser OAuth credentials were not supplied",
        "required": ["cookie", "project_id"],
    }
    assert evidence["owner_journey"]["status"] == "skipped"
    assert evidence["publication"]["status"] == "skipped"
    assert evidence["billing_webhook"]["status"] == "skipped"
    assert evidence["release_ready"] is False
    assert evidence["release_status"] == "incomplete"
    assert evidence["missing_release_checks"] == [
        "owner_journey",
        "publication",
        "billing_webhook",
    ]


def test_deployed_canary_returns_nonzero_and_json_evidence_for_failed_critical_route() -> (
    None
):
    server = _serve_canary()
    _CanaryHandler.health_status = 503
    try:
        completed = _run_canary(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 1
    evidence = json.loads(completed.stderr)
    assert evidence["ok"] is False
    assert evidence["mode"] == "deployed"
    assert evidence["failed_check"] == "health"
    assert evidence["status"] == 503


def test_deployed_canary_fails_when_db_backed_readiness_is_not_ready() -> None:
    server = _serve_canary()
    _CanaryHandler.readiness_status = 503
    try:
        completed = _run_canary(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 1
    evidence = json.loads(completed.stderr)
    assert evidence["failed_check"] == "readiness"
    assert evidence["status"] == 503


def test_deployed_canary_never_follows_cross_origin_readiness_redirect(
    tmp_path,
) -> None:
    token_file = tmp_path / "readiness-token"
    token_file.write_text("private-readiness-token", encoding="utf-8")
    capture = _serve_redirect_capture()
    server = _serve_canary()
    _CanaryHandler.readiness_redirect_url = (
        f"http://127.0.0.1:{capture.server_port}/capture"
    )
    try:
        completed = _run_canary(
            f"http://127.0.0.1:{server.server_port}",
            env={"KAIGO_SMOKE_READINESS_TOKEN_FILE": str(token_file)},
        )
    finally:
        server.shutdown()
        server.server_close()
        capture.shutdown()
        capture.server_close()

    assert completed.returncode == 1
    evidence = json.loads(completed.stderr)
    assert evidence["failed_check"] == "readiness"
    assert evidence["status"] == 302
    assert _ReadinessRedirectCaptureHandler.readiness_tokens == []


def test_deployed_canary_can_fail_closed_when_release_evidence_is_incomplete() -> None:
    server = _serve_canary()
    try:
        completed = _run_canary(
            f"http://127.0.0.1:{server.server_port}",
            "--require-release-ready",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 1
    evidence = json.loads(completed.stderr)
    assert evidence["failed_check"] == "release_readiness"
    assert "owner_journey" in evidence["message"]


def test_deployed_canary_rejects_cookie_value_on_command_line() -> None:
    completed = _run_canary(
        "http://127.0.0.1:1",
        "--cookie",
        "kaigo_session=must-not-enter-process-list",
    )

    assert completed.returncode == 2
    assert "unrecognized arguments: --cookie" in completed.stderr


def test_deployed_canary_uses_explicit_evidence_for_owner_publication_and_webhook(
    tmp_path,
) -> None:
    fixture = tmp_path / "webhook.json"
    fixture.write_text(
        json.dumps({"payment_id": "verified-fixture"}),
        encoding="utf-8",
    )
    cookie_file = tmp_path / "smoke-cookie"
    cookie_file.write_text(
        "kaigo_session=real-browser-session\n",
        encoding="utf-8",
    )
    server = _serve_canary()
    _CanaryHandler.authenticated = True
    env = {
        "KAIGO_SMOKE_COOKIE_FILE": str(cookie_file),
        "KAIGO_SMOKE_PROJECT_ID": "project-123",
        "KAIGO_SMOKE_PUBLIC_KEY": "public-key",
        "KAIGO_SMOKE_EMBED_ORIGIN": "https://customer.example",
        "KAIGO_SMOKE_WEBHOOK_FIXTURE": str(fixture),
    }
    try:
        completed = _run_canary(
            f"http://127.0.0.1:{server.server_port}",
            "--require-release-ready",
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["release_ready"] is True
    assert evidence["release_status"] == "complete"
    assert evidence["missing_release_checks"] == []
    assert evidence["oauth"]["status"] == "provided_session"
    assert evidence["owner_journey"] == {
        "status": "passed",
        "project_id": "project-123",
        "run_id": "run-456",
        "run_status": "completed",
        "sse_terminal": True,
        "preview_revision": 5,
        "preview_document": True,
    }
    assert evidence["publication"] == {
        "status": "passed",
        "stable_key": "public-key",
        "embed": True,
        "runtime": True,
    }
    assert evidence["billing_webhook"] == {
        "status": "passed",
        "first_processed": True,
        "replay_processed": False,
    }
    owner_requests = [
        request for request in _CanaryHandler.requests if "/api/" in request[1]
    ]
    assert all(
        cookie == "kaigo_session=real-browser-session"
        for method, path, cookie in owner_requests
        if path not in {
            "/api/health",
            "/api/ready",
            "/api/billing/webhooks/yookassa",
        }
    )


def test_deployed_canary_reads_owner_cookie_from_root_only_file(tmp_path) -> None:
    cookie_file = tmp_path / "smoke-cookie"
    cookie_file.write_text(
        "kaigo_session=real-browser-session\n",
        encoding="utf-8",
    )
    server = _serve_canary()
    _CanaryHandler.authenticated = True
    env = {
        "KAIGO_SMOKE_COOKIE_FILE": str(cookie_file),
        "KAIGO_SMOKE_PROJECT_ID": "project-123",
    }
    try:
        completed = _run_canary(
            f"http://127.0.0.1:{server.server_port}",
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    evidence = json.loads(completed.stdout)
    assert evidence["owner_journey"]["status"] == "passed"
    assert any(
        path == "/api/projects/project-123"
        and cookie == "kaigo_session=real-browser-session"
        for method, path, cookie in _CanaryHandler.requests
    )


def test_deployed_canary_rejects_webhook_false_false_as_unprocessed_fixture(
    tmp_path,
) -> None:
    fixture = tmp_path / "webhook.json"
    fixture.write_text(
        json.dumps({"payment_id": "verified-fixture"}),
        encoding="utf-8",
    )
    cookie_file = tmp_path / "smoke-cookie"
    cookie_file.write_text(
        "kaigo_session=real-browser-session\n",
        encoding="utf-8",
    )
    server = _serve_canary()
    _CanaryHandler.authenticated = True
    _CanaryHandler.webhook_first_processed = False
    env = {
        "KAIGO_SMOKE_COOKIE_FILE": str(cookie_file),
        "KAIGO_SMOKE_PROJECT_ID": "project-123",
        "KAIGO_SMOKE_PUBLIC_KEY": "public-key",
        "KAIGO_SMOKE_EMBED_ORIGIN": "https://customer.example",
        "KAIGO_SMOKE_WEBHOOK_FIXTURE": str(fixture),
    }
    try:
        completed = _run_canary(
            f"http://127.0.0.1:{server.server_port}",
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 1
    evidence = json.loads(completed.stderr)
    assert evidence["failed_check"] == "billing_webhook_first"
    assert "processed=true" in evidence["message"]


def test_deployed_canary_rejects_partial_owner_inputs_with_machine_readable_error() -> (
    None
):
    server = _serve_canary()
    try:
        completed = _run_canary(
            f"http://127.0.0.1:{server.server_port}",
            "--project-id",
            "project-123",
        )
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 1
    evidence = json.loads(completed.stderr)
    assert evidence["failed_check"] == "configuration"
    assert "cookie" in evidence["message"]


def test_runbook_uses_edge_smoke_and_secure_cookie_file() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    assert "--base-url https://kaigo.space" in runbook
    assert "--require-release-ready" in runbook
    assert "KAIGO_SMOKE_COOKIE_FILE" in runbook
    assert "read -rsp" in runbook
    assert "trap " in runbook and "rm -f" in runbook
    assert "export KAIGO_SMOKE_COOKIE='" not in runbook


def test_runbook_captures_atomic_rollback_and_verifies_active_digests() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )

    snapshot = runbook.index("rollback.env.tmp")
    switch = runbook.index("docker compose up -d --no-build app")
    assert snapshot < switch
    assert "mv -f" in runbook
    assert "systemctl restart kaigo-builder-worker" in runbook
    assert "docker inspect --format '{{.Image}}'" in runbook
    assert "KAIGO_ENTRY_TRUSTED_PROXY_CIDRS" in runbook


def test_runbook_snapshots_complete_rollback_identity_and_schema_tuple() -> None:
    runbook = (ROOT / "docs" / "SAAS_PRODUCTION_RUNBOOK.md").read_text(
        encoding="utf-8"
    )
    snapshot = runbook.split("## Fail-closed schema preflight", 1)[0]

    for name in (
        "KAIGO_APP_IMAGE",
        "KAIGO_BUILDER_WORKER_IMAGE",
        "KAIGO_BUILDER_WORKER_IMAGE_IDENTITY",
        "KAIGO_RELEASE_ID",
        "KAIGO_BUILDER_WORKER_BOOT_ID",
        "KAIGO_PREVIOUS_ALEMBIC_REVISION",
        "KAIGO_ROLLBACK_MIGRATION_IMAGE",
    ):
        assert f'"{name}=' in snapshot
    assert 'ROLLBACK_WORKER_BOOT_ID="$(cat /proc/sys/kernel/random/uuid)"' in snapshot
    assert 'ROLLBACK_MIGRATION_IMAGE="$KAIGO_APP_IMAGE"' in snapshot
    assert "rollback.env.tmp" in snapshot
    assert 'chmod 600 "$ROLLBACK_TMP"' in snapshot
