import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from builder_lab.reference_crawler import (
    GuardedRobotsPolicy,
    RobotsDenied,
    UnsafeReferenceUrl,
    UrlGuard,
    sanitize_url_for_log,
)
from builder_lab.reference_storage import (
    cleanup_expired_evidence,
    validate_evidence_output_dir,
    write_evidence_expiry_marker,
)


PUBLIC = "93.184.216.34"


class GuardedRobotsPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_origin_public_redirect_is_parsed_with_kaigo_user_agent(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            if request.url.path == "/robots.txt":
                return httpx.Response(302, headers={"location": "/robots-policy.txt"})
            return httpx.Response(
                200,
                text=(
                    "User-agent: KaigoVisualResearch\nDisallow: /private\n"
                    "User-agent: *\nAllow: /\n"
                ),
            )

        policy = GuardedRobotsPolicy(
            guard=UrlGuard(resolver=lambda _host: [PUBLIC]),
            transport=httpx.MockTransport(handler),
        )

        self.assertFalse(await policy.is_allowed("https://example.com/private"))
        self.assertTrue(await policy.is_allowed("https://example.com/public"))
        self.assertEqual(requests, [
            "https://example.com/robots.txt",
            "https://example.com/robots-policy.txt",
        ])

    async def test_private_robots_redirect_is_rejected_before_second_request(self):
        requests = []

        def resolver(host):
            return ["127.0.0.1"] if host == "metadata.test" else [PUBLIC]

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(
                302, headers={"location": "http://metadata.test/latest/meta-data"}
            )

        policy = GuardedRobotsPolicy(
            guard=UrlGuard(resolver=resolver),
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(UnsafeReferenceUrl):
            await policy.is_allowed("https://example.com/")
        self.assertEqual(requests, ["https://example.com/robots.txt"])

    async def test_verified_404_allows_but_5xx_fails_closed(self):
        statuses = {"allow.test": 404, "deny.test": 503}

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(statuses[request.url.host])

        policy = GuardedRobotsPolicy(
            guard=UrlGuard(resolver=lambda _host: [PUBLIC]),
            transport=httpx.MockTransport(handler),
        )
        self.assertTrue(await policy.is_allowed("https://allow.test/path"))
        with self.assertRaises(RobotsDenied):
            await policy.is_allowed("https://deny.test/path")


class PublicLogTests(unittest.TestCase):
    def test_url_log_removes_query_fragment_credentials_and_token_segments(self):
        value = sanitize_url_for_log(
            "https://user:pass@example.com/a/abcdefghijklmnopqrstuvwxyz0123456789"
            "?token=super-secret#debug"
        )
        self.assertEqual(value, "https://example.com/a/[redacted]")
        self.assertNotIn("secret", value)
        self.assertNotIn("token", value)


class EvidenceStorageTests(unittest.TestCase):
    def test_cleanup_deletes_only_expired_marked_run_directories(self):
        now = datetime.now(timezone.utc)
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expired = root / "expired"
            fresh = root / "fresh"
            unrelated = root / "unrelated"
            for path in (expired, fresh, unrelated):
                path.mkdir()
                (path / "payload.bin").write_bytes(b"raw")
            write_evidence_expiry_marker(expired, now - timedelta(seconds=1))
            write_evidence_expiry_marker(fresh, now + timedelta(hours=1))

            removed = cleanup_expired_evidence(root, now=now)

            self.assertEqual(removed, (expired.resolve(),))
            self.assertFalse(expired.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(unrelated.exists())

    def test_workspace_output_requires_explicit_override(self):
        workspace = Path(__file__).resolve().parents[2]
        with self.assertRaisesRegex(ValueError, "workspace"):
            validate_evidence_output_dir(
                workspace / "output" / "reference-run",
                workspace_root=workspace,
                allow_workspace=False,
            )
        accepted = validate_evidence_output_dir(
            workspace / "output" / "reference-run",
            workspace_root=workspace,
            allow_workspace=True,
        )
        self.assertTrue(accepted.is_absolute())

    def test_cleanup_command_removes_expired_and_keeps_fresh_without_capture(self):
        now = datetime.now(timezone.utc)
        workspace = Path(__file__).resolve().parents[2]
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expired = root / "expired"
            fresh = root / "fresh"
            expired.mkdir()
            fresh.mkdir()
            write_evidence_expiry_marker(expired, now - timedelta(seconds=1))
            write_evidence_expiry_marker(fresh, now + timedelta(hours=1))

            completed = subprocess.run(
                [
                    sys.executable,
                    str(workspace / "scripts" / "cleanup_reference_evidence.py"),
                    str(root),
                    "--now",
                    now.isoformat(),
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["removed_count"], 1)
            self.assertFalse(expired.exists())
            self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
