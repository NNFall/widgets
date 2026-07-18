import asyncio
import inspect
import os
import sys
import unittest
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
        }
        defaults.update(values)
        with patch.dict(os.environ, defaults, clear=True):
            return BuilderLabConfig.from_env()

    def test_registers_direct_and_optional_antigravity_factories(self):
        factories = run_builder_lab.make_engine_factories(self.config())
        self.assertEqual(set(factories), {EngineName.DIRECT, EngineName.ANTIGRAVITY})
        direct = factories[EngineName.DIRECT]()
        antigravity = factories[EngineName.ANTIGRAVITY]()
        self.assertEqual(direct.model, "gemini-3.5-flash")
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


if __name__ == "__main__":
    unittest.main()
