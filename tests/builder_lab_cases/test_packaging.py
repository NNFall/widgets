import os
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from importlib.metadata import version
from pathlib import Path

from google import genai


ROOT = Path(__file__).resolve().parents[2]


class BuilderLabPackagingTests(unittest.TestCase):
    def test_compose_service_receives_only_explicit_builder_variables(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        builder = compose.split("  builder-lab:", 1)[1].split("\n  db:", 1)[0]
        self.assertNotIn("env_file:", builder)
        self.assertIn("GOOGLE_AI_API_KEY:", builder)
        self.assertIn("GOOGLE_AI_NATIVE_BASE_URL:", builder)
        self.assertIn("KAIGO_BUILDER_DEFAULT_ENGINE:", builder)
        self.assertIn(
            "KAIGO_BUILDER_DEMO_PATH: /app/data/builder-demo/latest.json", builder
        )
        self.assertIn(
            "KAIGO_BUILDER_DEMO_DIR: "
            "${KAIGO_BUILDER_DEMO_DIR:-/app/data/builder-demo/direct-abc-v1}",
            builder,
        )
        self.assertIn("./data/builder-demo:/app/data/builder-demo", builder)
        self.assertNotIn("MESSAGE_DATABASE_URL", builder)
        self.assertNotIn("POSTGRES_", builder)
        self.assertIn("dockerfile: Dockerfile.builder-lab", builder)
        self.assertIn("image: ai_project-builder-lab", builder)
        self.assertIn("cap_drop:", builder)
        self.assertIn("no-new-privileges:true", builder)
        self.assertIn("kaigo_builder_research", builder)
        self.assertIn("enable_ipv6: false", compose)
        self.assertIn("172.30.240.0/28", compose)

    def test_declared_sdk_floor_matches_interactions_contract(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("google-genai>=2.12.1,<3.0.0", requirements)
        installed = tuple(int(part) for part in version("google-genai").split(".")[:3])
        self.assertGreaterEqual(installed, (2, 12, 1))
        client = genai.Client(api_key="contract-test")
        try:
            self.assertTrue(callable(client.aio.interactions.create))
            self.assertTrue(callable(client.aio.interactions.get))
            self.assertTrue(callable(client.aio.interactions.cancel))
        finally:
            client.close()

    def test_builder_browser_dependencies_are_isolated_from_production_image(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        builder_requirements = (ROOT / "requirements.builder-lab.txt").read_text(
            encoding="utf-8"
        )
        builder_dockerfile = (ROOT / "Dockerfile.builder-lab").read_text(
            encoding="utf-8"
        )
        production_dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertNotIn("crawlee[playwright]", requirements)
        self.assertIn("crawlee[playwright]", builder_requirements)
        self.assertIn("playwright==1.61.0", builder_requirements)
        self.assertIn("Pillow", builder_requirements)
        self.assertIn("python-dotenv", builder_requirements)
        self.assertIn("playwright install --with-deps chromium", builder_dockerfile)
        self.assertIn("scripts/capture_reference_site.py", builder_dockerfile)
        self.assertRegex(builder_dockerfile, r"(?m)^USER kaigo$")
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("init: true", compose)
        self.assertIn("shm_size: 1gb", compose)
        self.assertIn("GEMINI_VISUAL_CRITIC_MODEL:", compose)
        self.assertIn("KAIGO_BROWSER_AUDIT_TIMEOUT_MS:", compose)
        self.assertIn("KAIGO_BROWSER_AUDIT_TOTAL_TIMEOUT_SECONDS:", compose)
        self.assertIn(
            "CRAWLEE_MEMORY_MBYTES: ${CRAWLEE_MEMORY_MBYTES:-4096}",
            compose,
        )
        self.assertIn('CRAWLEE_DISABLE_BROWSER_SANDBOX: "true"', compose)
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn(
            "KAIGO_BUILDER_DEMO_DIR=/app/data/builder-demo/direct-abc-v1",
            env_example,
        )
        self.assertIn("GEMINI_VISUAL_CRITIC_MODEL=gemini-3.6-flash", env_example)
        self.assertIn("KAIGO_BROWSER_AUDIT_TIMEOUT_MS=10000", env_example)
        self.assertIn("KAIGO_BROWSER_AUDIT_TOTAL_TIMEOUT_SECONDS=120", env_example)
        self.assertIn("KAIGO_REFERENCE_TIMEOUT_SECONDS=600", env_example)
        self.assertIn(
            "KAIGO_REFERENCE_TIMEOUT_SECONDS: "
            "${KAIGO_REFERENCE_TIMEOUT_SECONDS:-600}",
            compose,
        )
        self.assertIn("CRAWLEE_MEMORY_MBYTES=4096", env_example)
        self.assertIn("CRAWLEE_DISABLE_BROWSER_SANDBOX=true", env_example)
        self.assertNotIn("playwright install", production_dockerfile)

    def test_production_chat_contract_is_explicit_in_compose_and_env_example(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        builder = compose.split("  builder-lab:", 1)[1].split("\n  db:", 1)[0]
        env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
        expected = {
            "GEMINI_CHAT_MODEL": "gemini-3.5-flash-lite",
            "GEMINI_CHAT_THINKING_LEVEL": "medium",
            "GEMINI_CHAT_TIMEOUT_SECONDS": "45",
            "KAIGO_CHAT_SESSION_SECRET": "",
            "KAIGO_CHAT_SESSION_TTL_SECONDS": "3600",
            "KAIGO_CHAT_MAX_SESSIONS": "500",
            "KAIGO_CHAT_RATE_LIMIT_REQUESTS": "12",
            "KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS": "60",
            "KAIGO_CHAT_RATE_LIMIT_WINDOW_SECONDS": "60",
            "KAIGO_CHAT_MAX_REQUESTS_PER_SESSION": "40",
            "KAIGO_CHAT_GLOBAL_CONCURRENCY": "4",
            "KAIGO_CHAT_SECURE_COOKIE": "true",
        }

        self.assertNotIn("env_file:", builder)
        for name, default in expected.items():
            self.assertIn(f"      {name}: ${{{name}:-{default}}}", builder)
            self.assertRegex(env_example, rf"(?m)^{re.escape(name)}={re.escape(default)}$")
        self.assertNotRegex(env_example, r"(?m)^KAIGO_CHAT_SESSION_SECRET=.+$")

    def test_builder_image_contains_server_analysis_and_smoke_scripts(self):
        dockerfile = (ROOT / "Dockerfile.builder-lab").read_text(encoding="utf-8")
        self.assertIn("scripts/analyze_reference_site.py", dockerfile)
        self.assertIn("scripts/smoke_builder_lab.py", dockerfile)
        self.assertRegex(dockerfile, r"(?m)^USER kaigo$")
        self.assertIn("PLAYWRIGHT_BROWSERS_PATH=/ms-playwright", dockerfile)

    def test_deploy_prepares_demo_mount_for_the_unprivileged_image_user(self):
        deploy = (ROOT / "scripts" / "deploy_builder_lab.sh").read_text(
            encoding="utf-8"
        )
        build_index = deploy.index("docker compose --profile builder-lab build builder-lab")
        ownership_index = deploy.index('chown "$builder_uid:$builder_gid" "$demo_path"')
        create_index = deploy.index(
            "docker compose --profile builder-lab up --no-start --force-recreate --no-deps builder-lab"
        )

        self.assertLess(build_index, ownership_index)
        self.assertLess(ownership_index, create_index)
        self.assertIn('[[ ! -L "$demo_path" ]]', deploy)
        self.assertIn('install -d -m 0700 "$demo_path"', deploy)
        self.assertIn('docker run --rm --network none --read-only --cap-drop ALL', deploy)
        self.assertIn(
            "docker compose --profile builder-lab config --format json",
            deploy,
        )
        self.assertNotIn(
            "docker compose --profile builder-lab images -q builder-lab",
            deploy,
        )

    def test_operations_document_exact_post_only_public_chat_route(self):
        operations = (ROOT / "docs" / "KAIGO_BUILDER_LAB_OPERATIONS.md").read_text(
            encoding="utf-8"
        )
        expected_route = """location = /builder-demo/chat {
    limit_except POST { deny all; }
    client_max_body_size 4k;
    proxy_pass http://127.0.0.1:8091/demo/chat;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_request_buffering off;
    proxy_read_timeout 60s;
}"""
        self.assertIn(expected_route, operations)
        self.assertEqual(operations.count("location = /builder-demo/chat"), 1)
        self.assertEqual(
            operations.count("proxy_pass http://127.0.0.1:8091/demo/chat;"), 1
        )
        self.assertNotRegex(
            operations,
            r"location\s+(?:\^~\s+)?/builder-demo/(?!chat\s*\{)",
        )
        self.assertIn("nginx -t", operations)
        self.assertIn("systemctl reload nginx", operations)

    def test_operations_document_stable_unprinted_production_chat_secret(self):
        operations = (ROOT / "docs" / "KAIGO_BUILDER_LAB_OPERATIONS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("KAIGO_CHAT_SESSION_SECRET", operations)
        self.assertIn("openssl rand", operations)
        self.assertIn("chmod 0600", operations)
        self.assertIn("не вывод", operations.lower())
        self.assertIn("32", operations)
        self.assertIn(
            "secret_definition_pattern='^[[:space:]]*(export[[:space:]]+)?KAIGO_CHAT_SESSION_SECRET([^A-Za-z0-9_]|$)'",
            operations,
        )
        self.assertIn(
            'secret_definition_count="$(grep -Ec "$secret_definition_pattern" '
            '"$env_file" || true)"',
            operations,
        )
        self.assertIn('case "$secret_definition_count" in', operations)
        self.assertIn("0)", operations)
        self.assertIn("1)", operations)
        self.assertIn("определён больше одного раза", operations)
        self.assertIn("существует, но пуст или некорректен", operations)
        self.assertIn("без `=`", operations)
        self.assertIn("ведущими пробелами", operations)
        self.assertIn("`export`", operations)
        self.assertGreaterEqual(operations.count("exit 1"), 2)
        self.assertNotIn("sed -i '/^KAIGO_CHAT_SESSION_SECRET=/d'", operations)

    def test_operations_secret_bootstrap_executes_fail_closed_behavioral_matrix(self):
        if os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0:
            self.skipTest("requires a root POSIX runner")
        if shutil.which("bash") is None:
            self.skipTest("requires bash")

        operations = (ROOT / "docs" / "KAIGO_BUILDER_LAB_OPERATIONS.md").read_text(
            encoding="utf-8"
        )
        match = re.search(
            r"```bash\n(?P<script>set -euo pipefail\n"
            r"env_file=/root/ai_project/\.env\n.*?"
            r"unset secret_definition_count secret_definition_pattern\n)```",
            operations,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "production secret bootstrap block was not found")
        runbook_script = match.group("script")
        self.assertEqual(runbook_script.count("env_file=/root/ai_project/.env"), 1)

        valid_secret = "Canonical_session-secret_0123456789ABCDEFGH"
        invalid_cases = {
            "blank": "KAIGO_CHAT_SESSION_SECRET=\n",
            "short_or_invalid": "KAIGO_CHAT_SESSION_SECRET=short/value\n",
            "missing_equals": "KAIGO_CHAT_SESSION_SECRET\n",
            "leading_whitespace": f"  KAIGO_CHAT_SESSION_SECRET={valid_secret}\n",
            "export_form": f"export KAIGO_CHAT_SESSION_SECRET={valid_secret}\n",
            "duplicate_canonical_and_blank": (
                f"KAIGO_CHAT_SESSION_SECRET={valid_secret}\n"
                "KAIGO_CHAT_SESSION_SECRET=\n"
            ),
            "malformed_and_canonical": (
                "KAIGO_CHAT_SESSION_SECRET:broken\n"
                f"KAIGO_CHAT_SESSION_SECRET={valid_secret}\n"
            ),
            "colon_delimiter": "KAIGO_CHAT_SESSION_SECRET:broken-colon\n",
            "dot_delimiter": "KAIGO_CHAT_SESSION_SECRET.broken-dot\n",
            "dash_delimiter": "KAIGO_CHAT_SESSION_SECRET-broken-dash\n",
        }

        def run_case(initial: bytes):
            with tempfile.TemporaryDirectory() as temporary_directory:
                env_path = Path(temporary_directory) / ".env"
                env_path.write_bytes(initial)
                script = runbook_script.replace(
                    "env_file=/root/ai_project/.env",
                    f"env_file={shlex.quote(str(env_path))}",
                    1,
                )
                result = subprocess.run(
                    ["bash", "-c", script],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                return result, env_path.read_bytes(), stat.S_IMODE(env_path.stat().st_mode)

        with self.subTest(case="absent"):
            result, after, mode = run_case(b"OTHER_SETTING=kept\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(mode, 0o600)
            self.assertEqual(
                len(re.findall(rb"(?m)^KAIGO_CHAT_SESSION_SECRET=[A-Za-z0-9_-]{32,}$", after)),
                1,
            )
            generated_secret = re.search(
                rb"(?m)^KAIGO_CHAT_SESSION_SECRET=([A-Za-z0-9_-]{32,})$", after
            ).group(1)
            captured = (result.stdout + result.stderr).encode()
            self.assertNotIn(generated_secret, captured)

        canonical = f"KAIGO_CHAT_SESSION_SECRET={valid_secret}\n".encode()
        with self.subTest(case="one_canonical"):
            result, after, mode = run_case(canonical)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(after, canonical)
            self.assertEqual(mode, 0o600)
            self.assertNotIn(valid_secret, result.stdout + result.stderr)

        suffix_only = f"KAIGO_CHAT_SESSION_SECRET_SUFFIX={valid_secret}\n".encode()
        with self.subTest(case="identifier_suffix_is_absent"):
            result, after, mode = run_case(suffix_only)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(mode, 0o600)
            self.assertTrue(after.startswith(suffix_only))
            self.assertEqual(after.count(b"KAIGO_CHAT_SESSION_SECRET_SUFFIX="), 1)
            self.assertEqual(
                len(re.findall(rb"(?m)^KAIGO_CHAT_SESSION_SECRET=[A-Za-z0-9_-]{32,}$", after)),
                1,
            )
            self.assertNotIn(valid_secret, result.stdout + result.stderr)

        for case_name, initial_text in invalid_cases.items():
            with self.subTest(case=case_name):
                before = initial_text.encode()
                result, after, _mode = run_case(before)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(after, before)
                for secret_value in re.findall(
                    r"KAIGO_CHAT_SESSION_SECRET(?:=|:|\.|-)([^\n]+)", initial_text
                ):
                    self.assertNotIn(secret_value, result.stdout + result.stderr)

    def test_operations_document_public_chat_boundary_and_two_turn_validation(self):
        operations = (ROOT / "docs" / "KAIGO_BUILDER_LAB_OPERATIONS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "`/builder-demo/` и `/builder-demo/preview` остаются read-only",
            operations,
        )
        self.assertIn("Полный `/builder/` по-прежнему закрыт Basic Auth", operations)
        self.assertEqual(
            operations.count('-c "$cookie_jar" -b "$cookie_jar"'), 2
        )
        self.assertIn("production-chat-$request_prefix-1", operations)
        self.assertIn("production-chat-$request_prefix-2", operations)
        self.assertEqual(
            operations.count(
                "https://kaigo.space/builder-demo/chat | jq -e "
                "'.reply | strings | length > 0' >/dev/null"
            ),
            1,
        )
        self.assertIn('history_marker="kaigo-history-$request_prefix"', operations)
        self.assertIn(
            '\\"message\\":\\"Запомните уникальный маркер $history_marker и подтвердите получение\\"',
            operations,
        )
        self.assertIn(
            '\\"message\\":\\"Какой уникальный маркер я просил запомнить в предыдущем сообщении? '
            'Верните его дословно\\"',
            operations,
        )
        self.assertIn(
            'jq -e --arg marker "$history_marker" '
            "'.reply | strings | contains($marker)' >/dev/null",
            operations,
        )
        self.assertIn("trap 'rm -f \"$cookie_jar\"' EXIT", operations)

    def test_egress_guard_runbook_is_server_operable_and_does_not_claim_dns_pinning(self):
        operations = (ROOT / "docs" / "KAIGO_BUILDER_LAB_OPERATIONS.md").read_text(
            encoding="utf-8"
        )
        script = ROOT / "scripts" / "apply_builder_egress_guard.sh"
        self.assertTrue(script.exists())
        body = script.read_text(encoding="utf-8")
        self.assertIn("DOCKER-USER", body)
        self.assertIn("KAIGO-BUILDER-EGRESS", body)
        self.assertIn("172.30.240.0/28", body)
        self.assertIn("--ctstate NEW", body)
        self.assertIn("--reject-with icmp-port-unreachable", body)
        self.assertIn("RULESET_GENERATION", body)
        self.assertIn("iptables-restore", body)
        self.assertIn("--noflush", body)
        self.assertNotIn('-F "${chain}"', body)
        self.assertNotIn('-F "${FORWARD_CHAIN}"', body)
        self.assertNotIn('-F "${HOST_CHAIN}"', body)
        self.assertIn("169.254.0.0/16", body)
        self.assertIn("LEGACY_DESTINATIONS", body)
        self.assertIn("verify_exact_chain", body)
        self.assertIn("immutable egress generation does not exactly match", body)
        self.assertIn("legacy_rule_count", body)
        self.assertIn("complete_legacy_signature", body)
        self.assertIn('"${canonical/-A /-D }"', body)
        self.assertNotIn('[[ "${rule}" == "-A DOCKER-USER -s "* ]]', body)
        self.assertIn("incomplete immutable egress generation", body)
        self.assertIn("grep -Fxc", body)
        self.assertLess(
            body.index("Build the immutable generation"),
            body.index("saved_rules="),
        )
        self.assertLess(
            body.index("-I DOCKER-USER 1"),
            body.index('"${RESTORE[@]}" < "${transaction}"'),
        )
        self.assertIn("DNS TOCTOU", operations)
        self.assertIn("apply_builder_egress_guard.sh", operations)

        deploy = (ROOT / "scripts" / "deploy_builder_lab.sh")
        self.assertTrue(deploy.exists())
        deploy_body = deploy.read_text(encoding="utf-8")
        self.assertIn("apply_builder_egress_guard.sh", deploy_body)
        self.assertIn(
            "docker compose --profile builder-lab up --no-start "
            "--force-recreate --no-deps builder-lab",
            deploy_body,
        )
        self.assertNotIn("create --force-recreate --no-deps", deploy_body)
        self.assertIn("docker compose --profile builder-lab start", deploy_body)
        self.assertLess(
            deploy_body.index("apply_builder_egress_guard.sh"),
            deploy_body.index("docker compose --profile builder-lab start"),
        )
        self.assertEqual(deploy_body.count("apply_builder_egress_guard.sh"), 1)
        self.assertIn("install_reference_cleanup_timer.sh", deploy_body)
        self.assertLess(
            deploy_body.index("install_reference_cleanup_timer.sh"),
            deploy_body.index("docker compose --profile builder-lab start"),
        )

    def test_ttl_cleanup_timer_is_repo_shipped_for_real_server_checkout(self):
        service = ROOT / "deploy" / "systemd" / "kaigo-reference-cleanup.service"
        timer = ROOT / "deploy" / "systemd" / "kaigo-reference-cleanup.timer"
        installer = ROOT / "scripts" / "install_reference_cleanup_timer.sh"
        self.assertTrue(service.exists())
        self.assertTrue(timer.exists())
        self.assertTrue(installer.exists())

        service_body = service.read_text(encoding="utf-8")
        timer_body = timer.read_text(encoding="utf-8")
        installer_body = installer.read_text(encoding="utf-8")
        self.assertIn("WorkingDirectory=/root/ai_project", service_body)
        self.assertIn(
            "/root/ai_project/scripts/cleanup_reference_evidence.py", service_body
        )
        self.assertIn("/root/ai_project/data/reference-evidence", service_body)
        self.assertNotIn("/opt/kaigo", service_body)
        self.assertIn("Persistent=true", timer_body)
        self.assertIn("systemctl daemon-reload", installer_body)
        self.assertIn("systemctl enable --now kaigo-reference-cleanup.timer", installer_body)
        self.assertIn("systemctl is-enabled --quiet", installer_body)
        self.assertIn("systemctl is-active --quiet", installer_body)


if __name__ == "__main__":
    unittest.main()
