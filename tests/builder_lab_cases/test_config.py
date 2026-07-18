import os
import unittest
from unittest.mock import patch

from builder_lab.config import BuilderLabConfig


class BuilderLabConfigTests(unittest.TestCase):
    def load(self, **values):
        base = {
            "KAIGO_BUILDER_LAB_HOST": None,
            "KAIGO_BUILDER_LAB_PORT": None,
            "KAIGO_BUILDER_LAB_ALLOW_REMOTE": None,
            "KAIGO_BUILDER_DEFAULT_ENGINE": None,
            "GEMINI_BUILDER_MODEL": None,
            "GEMINI_BUILDER_TEMPERATURE": None,
            "GEMINI_BUILDER_MAX_REPAIRS": None,
            "GEMINI_API_KEY": None,
            "GOOGLE_AI_API_KEY": None,
            "GOOGLE_AI_NATIVE_BASE_URL": None,
        }
        base.update(values)
        clean = {key: value for key, value in base.items() if value is not None}
        with patch.dict(os.environ, clean, clear=True):
            return BuilderLabConfig.from_env()

    def test_safe_defaults(self):
        config = self.load()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8091)
        self.assertEqual(config.direct_model, "gemini-3.5-flash")
        self.assertEqual(config.temperature, 0.9)
        self.assertEqual(config.max_repairs, 2)
        self.assertIsNone(config.gemini_api_key)

    def test_accepts_configured_values(self):
        config = self.load(
            KAIGO_BUILDER_LAB_PORT="9012",
            GEMINI_BUILDER_TEMPERATURE="1.25",
            GEMINI_BUILDER_MAX_REPAIRS="1",
            GEMINI_API_KEY="secret",
            GOOGLE_AI_NATIVE_BASE_URL="https://example.test/v1beta",
        )
        self.assertEqual(config.port, 9012)
        self.assertEqual(config.temperature, 1.25)
        self.assertEqual(config.max_repairs, 1)
        self.assertEqual(config.gemini_base_url, "https://example.test/v1beta")

    def test_non_loopback_is_rejected_by_default(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            self.load(KAIGO_BUILDER_LAB_HOST="0.0.0.0")

    def test_non_loopback_requires_explicit_override(self):
        config = self.load(
            KAIGO_BUILDER_LAB_HOST="0.0.0.0",
            KAIGO_BUILDER_LAB_ALLOW_REMOTE="true",
        )
        self.assertEqual(config.host, "0.0.0.0")

    def test_numeric_bounds_fail_closed(self):
        for name, value in (
            ("KAIGO_BUILDER_LAB_PORT", "0"),
            ("GEMINI_BUILDER_TEMPERATURE", "2.1"),
            ("GEMINI_BUILDER_MAX_REPAIRS", "5"),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.load(**{name: value})

    def test_existing_google_ai_key_alias_is_supported(self):
        config = self.load(GOOGLE_AI_API_KEY="existing-key")
        self.assertEqual(config.gemini_api_key, "existing-key")


if __name__ == "__main__":
    unittest.main()
