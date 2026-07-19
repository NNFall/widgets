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
        self.assertIn("./data/builder-demo:/app/data/builder-demo", builder)
        self.assertNotIn("MESSAGE_DATABASE_URL", builder)
        self.assertNotIn("POSTGRES_", builder)
        self.assertIn("dockerfile: Dockerfile.builder-lab", builder)
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
        self.assertIn("python-dotenv", builder_requirements)
        self.assertIn("playwright install --with-deps chromium", builder_dockerfile)
        self.assertIn("scripts/capture_reference_site.py", builder_dockerfile)
        self.assertRegex(builder_dockerfile, r"(?m)^USER kaigo$")
        self.assertNotIn("playwright install", production_dockerfile)

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
        self.assertIn("docker compose --profile builder-lab create", deploy_body)
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
