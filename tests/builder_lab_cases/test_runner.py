import asyncio
import inspect
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from builder_lab.config import BuilderLabConfig
from builder_lab.models import EngineName
from scripts import run_builder_lab


class BuilderLabRunnerTests(unittest.TestCase):
    def config(self, **values):
        defaults = {
            "KAIGO_BUILDER_LAB_HOST": "127.0.0.1",
            "GEMINI_API_KEY": "test-key",
            "KAIGO_BUILDER_ENABLE_ANTIGRAVITY": "true",
            "KAIGO_CHAT_SESSION_SECRET": "test-chat-session-secret-32-bytes-minimum",
        }
        defaults.update(values)
        with patch.dict(os.environ, defaults, clear=True):
            return BuilderLabConfig.from_env()

    def test_registers_direct_and_optional_antigravity_factories(self):
        factories = run_builder_lab.make_engine_factories(self.config())
        self.assertEqual(set(factories), {EngineName.DIRECT, EngineName.ANTIGRAVITY})
        direct = factories[EngineName.DIRECT]()
        antigravity = factories[EngineName.ANTIGRAVITY]()
        self.assertEqual(direct.model, "gemini-3.6-flash")
        self.assertEqual(direct.thinking_level, "high")
        self.assertEqual(antigravity.agent, "antigravity-preview-05-2026")
        asyncio.run(direct.close())
        asyncio.run(antigravity.close())

    def test_antigravity_can_be_disabled(self):
        factories = run_builder_lab.make_engine_factories(
            self.config(KAIGO_BUILDER_ENABLE_ANTIGRAVITY="false")
        )
        self.assertEqual(set(factories), {EngineName.DIRECT})

    def test_missing_key_fails_before_server_start(self):
        with patch.dict(os.environ, {}, clear=True):
            config = BuilderLabConfig.from_env()
        with self.assertRaisesRegex(RuntimeError, "GEMINI"):
            run_builder_lab.build_app(config)

    def test_runner_does_not_import_production_server(self):
        source = inspect.getsource(run_builder_lab)
        self.assertNotIn("app.server", source)
        self.assertNotIn("app.server", sys.modules)

    def test_demo_path_is_configurable_and_passed_to_web_app(self):
        config = self.config(KAIGO_BUILDER_DEMO_PATH="data/builder-demo/latest.json")
        self.assertEqual(config.demo_path, "data/builder-demo/latest.json")
        app = run_builder_lab.build_app(config)
        self.assertEqual(app[run_builder_lab.DEMO_PATH_KEY], Path(config.demo_path))
        asyncio.run(app.cleanup())

    def test_demo_registry_directory_is_optional_and_passed_to_web_app(self):
        config = self.config(
            KAIGO_BUILDER_DEMO_DIR="data/builder-demo/direct-abc-v1/private"
        )
        self.assertEqual(
            config.demo_dir, "data/builder-demo/direct-abc-v1/private"
        )
        app = run_builder_lab.build_app(config)
        self.assertEqual(
            app[run_builder_lab.DEMO_DIR_KEY], Path(config.demo_dir)
        )
        asyncio.run(app.cleanup())

    def test_configures_separate_bounded_demo_chat_service(self):
        config = self.config(
            GEMINI_CHAT_MODEL="gemini-3.5-flash",
            GEMINI_CHAT_THINKING_LEVEL="high",
            GEMINI_CHAT_TIMEOUT_SECONDS="37",
            KAIGO_CHAT_SESSION_TTL_SECONDS="900",
            KAIGO_CHAT_MAX_SESSIONS="40",
            KAIGO_CHAT_RATE_LIMIT_REQUESTS="5",
            KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS="25",
            KAIGO_CHAT_RATE_LIMIT_WINDOW_SECONDS="30",
            KAIGO_CHAT_MAX_REQUESTS_PER_SESSION="16",
            KAIGO_CHAT_GLOBAL_CONCURRENCY="2",
            KAIGO_CHAT_SECURE_COOKIE="false",
        )
        app = run_builder_lab.build_app(config)
        service = app[run_builder_lab.CHAT_SERVICE_KEY]

        self.assertEqual(service.model, "gemini-3.5-flash")
        self.assertEqual(service.thinking_level, "high")
        self.assertEqual(service._timeout_seconds, 37)
        self.assertEqual(service._max_sessions, 40)
        self.assertEqual(service._rate_limit_requests, 5)
        self.assertEqual(service._ip_rate_limit_requests, 25)
        self.assertEqual(service._max_requests_per_session, 16)
        self.assertFalse(app[run_builder_lab.CHAT_SECURE_COOKIE_KEY])
        asyncio.run(app.cleanup())

    def test_secure_cookie_fails_closed_without_independent_session_secret(self):
        config = self.config(KAIGO_CHAT_SESSION_SECRET="", KAIGO_CHAT_SECURE_COOKIE="true")
        with self.assertRaisesRegex(RuntimeError, "KAIGO_CHAT_SESSION_SECRET"):
            run_builder_lab.build_app(config)


if __name__ == "__main__":
    unittest.main()
