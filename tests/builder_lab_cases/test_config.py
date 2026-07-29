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
            "GEMINI_BUILDER_THINKING_LEVEL": None,
            "GEMINI_BUILDER_TEMPERATURE": None,
            "GEMINI_BUILDER_MAX_REPAIRS": None,
            "KAIGO_BUILDER_HYBRID_ROUTING_ENABLED": None,
            "AGENTROUTER_API_KEY": None,
            "AGENTROUTER_BASE_URL": None,
            "AGENTROUTER_TIMEOUT_SECONDS": None,
            "AGENTROUTER_QWEN_EXECUTABLE": None,
            "AGENTROUTER_GPT_MODEL": None,
            "AGENTROUTER_GLM_MODEL": None,
            "AGENTROUTER_GPT_INPUT_PRICE_MICROUSD_PER_MILLION": None,
            "AGENTROUTER_GPT_OUTPUT_PRICE_MICROUSD_PER_MILLION": None,
            "AGENTROUTER_GLM_INPUT_PRICE_MICROUSD_PER_MILLION": None,
            "AGENTROUTER_GLM_OUTPUT_PRICE_MICROUSD_PER_MILLION": None,
            "GEMINI_API_KEY": None,
            "GOOGLE_AI_API_KEY": None,
            "GOOGLE_AI_NATIVE_BASE_URL": None,
            "GEMINI_CHAT_MODEL": None,
            "GEMINI_CHAT_THINKING_LEVEL": None,
            "GEMINI_CHAT_TIMEOUT_SECONDS": None,
            "GEMINI_VISUAL_CRITIC_MODEL": None,
            "GEMINI_VISUAL_CRITIC_THINKING_LEVEL": None,
            "GEMINI_VISUAL_CRITIC_TIMEOUT_SECONDS": None,
            "GEMINI_REFERENCE_ANALYZER_MODEL": None,
            "GEMINI_REFERENCE_ANALYZER_THINKING_LEVEL": None,
            "GEMINI_ANTIGRAVITY_MAX_TOTAL_TOKENS": None,
            "KAIGO_BROWSER_AUDIT_TIMEOUT_MS": None,
            "KAIGO_BROWSER_AUDIT_TOTAL_TIMEOUT_SECONDS": None,
            "KAIGO_CHAT_SESSION_TTL_SECONDS": None,
            "KAIGO_CHAT_MAX_SESSIONS": None,
            "KAIGO_CHAT_RATE_LIMIT_REQUESTS": None,
            "KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS": None,
            "KAIGO_CHAT_RATE_LIMIT_WINDOW_SECONDS": None,
            "KAIGO_CHAT_MAX_REQUESTS_PER_SESSION": None,
            "KAIGO_CHAT_GLOBAL_CONCURRENCY": None,
            "KAIGO_CHAT_SECURE_COOKIE": None,
            "KAIGO_CHAT_SESSION_SECRET": None,
            "KAIGO_REFERENCE_MAX_PAGES": None,
            "KAIGO_REFERENCE_MAX_DEPTH": None,
            "KAIGO_REFERENCE_TIMEOUT_SECONDS": None,
            "KAIGO_REFERENCE_PAGE_TIMEOUT_SECONDS": None,
            "KAIGO_REFERENCE_MAX_TOTAL_BYTES": None,
            "KAIGO_REFERENCE_MAX_PAGE_BYTES": None,
            "KAIGO_REFERENCE_MAX_RETRIES": None,
            "KAIGO_REFERENCE_MAX_SCROLL_STEPS": None,
            "KAIGO_REFERENCE_SCROLL_DELAY_MS": None,
            "KAIGO_REFERENCE_WARMUP_MS": None,
            "KAIGO_REFERENCE_FINAL_SETTLE_MS": None,
            "KAIGO_REFERENCE_MAX_SCROLL_HEIGHT": None,
            "KAIGO_REFERENCE_TRACE_TTL_SECONDS": None,
            "KAIGO_REFERENCE_RESPECT_ROBOTS": None,
        }
        base.update(values)
        clean = {key: value for key, value in base.items() if value is not None}
        with patch.dict(os.environ, clean, clear=True):
            return BuilderLabConfig.from_env()

    def test_safe_defaults(self):
        config = self.load()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8091)
        self.assertEqual(config.direct_model, "gemini-3.6-flash")
        self.assertEqual(config.builder_thinking_level, "high")
        self.assertEqual(config.temperature, 0.9)
        self.assertEqual(config.max_repairs, 3)
        self.assertFalse(config.hybrid_routing_enabled)
        self.assertIsNone(config.agentrouter_api_key)
        self.assertEqual(config.agentrouter_base_url, "https://agentrouter.org/v1")
        self.assertEqual(config.agentrouter_timeout_seconds, 900)
        self.assertEqual(config.agentrouter_qwen_executable, "qwen")
        self.assertEqual(config.agentrouter_gpt_model, "gpt-5.5")
        self.assertEqual(config.agentrouter_glm_model, "glm-5.2")
        self.assertEqual(config.agentrouter_gpt_input_price_microusd_per_million, 7_000_000)
        self.assertEqual(config.agentrouter_gpt_output_price_microusd_per_million, 7_000_000)
        self.assertEqual(config.agentrouter_glm_input_price_microusd_per_million, 6_000_000)
        self.assertEqual(config.agentrouter_glm_output_price_microusd_per_million, 6_000_000)
        self.assertEqual(config.chat_model, "gemini-3.5-flash-lite")
        self.assertEqual(config.chat_thinking_level, "medium")
        self.assertEqual(config.chat_timeout_seconds, 45)
        self.assertEqual(config.visual_critic_model, "gemini-3.6-flash")
        self.assertEqual(config.visual_critic_thinking_level, "high")
        self.assertEqual(config.visual_critic_timeout_seconds, 60)
        self.assertEqual(config.reference_analyzer_model, "gemini-3.6-flash")
        self.assertEqual(config.reference_analyzer_thinking_level, "high")
        self.assertEqual(config.antigravity_max_total_tokens, 500_000)
        self.assertEqual(config.browser_audit_timeout_ms, 10_000)
        self.assertEqual(config.browser_audit_total_timeout_seconds, 120)
        self.assertEqual(config.chat_ip_rate_limit_requests, 60)
        self.assertTrue(config.chat_secure_cookie)
        self.assertIsNone(config.chat_session_secret)
        self.assertIsNone(config.gemini_api_key)
        self.assertEqual(config.reference_max_pages, 5)
        self.assertEqual(config.reference_timeout_seconds, 600)
        self.assertEqual(config.reference_scroll_delay_ms, 750)
        self.assertEqual(config.reference_warmup_ms, 5000)
        self.assertTrue(config.reference_respect_robots)

    def test_accepts_configured_values(self):
        config = self.load(
            KAIGO_BUILDER_LAB_PORT="9012",
            GEMINI_BUILDER_TEMPERATURE="1.25",
            GEMINI_BUILDER_THINKING_LEVEL="medium",
            GEMINI_BUILDER_MAX_REPAIRS="1",
            KAIGO_BUILDER_HYBRID_ROUTING_ENABLED="true",
            AGENTROUTER_API_KEY="router-secret",
            AGENTROUTER_BASE_URL="https://router.example/v1/",
            AGENTROUTER_TIMEOUT_SECONDS="321",
            AGENTROUTER_QWEN_EXECUTABLE="/usr/local/bin/qwen",
            AGENTROUTER_GPT_MODEL="gpt-test",
            AGENTROUTER_GLM_MODEL="glm-test",
            AGENTROUTER_GPT_INPUT_PRICE_MICROUSD_PER_MILLION="7100000",
            AGENTROUTER_GPT_OUTPUT_PRICE_MICROUSD_PER_MILLION="7200000",
            AGENTROUTER_GLM_INPUT_PRICE_MICROUSD_PER_MILLION="6100000",
            AGENTROUTER_GLM_OUTPUT_PRICE_MICROUSD_PER_MILLION="6200000",
            GEMINI_API_KEY="secret",
            GOOGLE_AI_NATIVE_BASE_URL="https://example.test/v1beta",
            GEMINI_VISUAL_CRITIC_MODEL="gemini-3.5-flash",
            GEMINI_VISUAL_CRITIC_THINKING_LEVEL="medium",
            GEMINI_VISUAL_CRITIC_TIMEOUT_SECONDS="75",
            GEMINI_REFERENCE_ANALYZER_MODEL="gemini-3.6-flash",
            GEMINI_REFERENCE_ANALYZER_THINKING_LEVEL="low",
            GEMINI_CHAT_MODEL="gemini-3.5-flash-lite",
            GEMINI_CHAT_THINKING_LEVEL="high",
            GEMINI_ANTIGRAVITY_MAX_TOTAL_TOKENS="750000",
            KAIGO_BROWSER_AUDIT_TIMEOUT_MS="15000",
            KAIGO_BROWSER_AUDIT_TOTAL_TIMEOUT_SECONDS="150",
        )
        self.assertEqual(config.port, 9012)
        self.assertEqual(config.temperature, 1.25)
        self.assertEqual(config.builder_thinking_level, "medium")
        self.assertEqual(config.max_repairs, 1)
        self.assertTrue(config.hybrid_routing_enabled)
        self.assertEqual(config.agentrouter_api_key, "router-secret")
        self.assertEqual(config.agentrouter_base_url, "https://router.example/v1")
        self.assertEqual(config.agentrouter_timeout_seconds, 321)
        self.assertEqual(config.agentrouter_qwen_executable, "/usr/local/bin/qwen")
        self.assertEqual(config.agentrouter_gpt_model, "gpt-test")
        self.assertEqual(config.agentrouter_glm_model, "glm-test")
        self.assertEqual(config.agentrouter_gpt_input_price_microusd_per_million, 7_100_000)
        self.assertEqual(config.agentrouter_gpt_output_price_microusd_per_million, 7_200_000)
        self.assertEqual(config.agentrouter_glm_input_price_microusd_per_million, 6_100_000)
        self.assertEqual(config.agentrouter_glm_output_price_microusd_per_million, 6_200_000)
        self.assertEqual(config.gemini_base_url, "https://example.test/v1beta")
        self.assertEqual(config.visual_critic_timeout_seconds, 75)
        self.assertEqual(config.visual_critic_thinking_level, "medium")
        self.assertEqual(config.reference_analyzer_model, "gemini-3.6-flash")
        self.assertEqual(config.reference_analyzer_thinking_level, "low")
        self.assertEqual(config.chat_thinking_level, "high")
        self.assertEqual(config.antigravity_max_total_tokens, 750_000)
        self.assertEqual(config.browser_audit_timeout_ms, 15_000)
        self.assertEqual(config.browser_audit_total_timeout_seconds, 150)

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
            ("AGENTROUTER_TIMEOUT_SECONDS", "0"),
            ("AGENTROUTER_GPT_INPUT_PRICE_MICROUSD_PER_MILLION", "0"),
            ("AGENTROUTER_GLM_OUTPUT_PRICE_MICROUSD_PER_MILLION", "100000001"),
            ("KAIGO_REFERENCE_MAX_PAGES", "6"),
            ("KAIGO_REFERENCE_SCROLL_DELAY_MS", "599"),
            ("KAIGO_REFERENCE_SCROLL_DELAY_MS", "1201"),
            ("KAIGO_REFERENCE_WARMUP_MS", "999"),
            ("GEMINI_CHAT_TIMEOUT_SECONDS", "181"),
            ("GEMINI_VISUAL_CRITIC_TIMEOUT_SECONDS", "181"),
            ("GEMINI_BUILDER_THINKING_LEVEL", "maximum"),
            ("GEMINI_CHAT_THINKING_LEVEL", "none"),
            ("GEMINI_VISUAL_CRITIC_THINKING_LEVEL", "turbo"),
            ("GEMINI_REFERENCE_ANALYZER_THINKING_LEVEL", ""),
            ("GEMINI_ANTIGRAVITY_MAX_TOTAL_TOKENS", "9999"),
            ("KAIGO_BROWSER_AUDIT_TIMEOUT_MS", "999"),
            ("KAIGO_BROWSER_AUDIT_TIMEOUT_MS", "30001"),
            ("KAIGO_BROWSER_AUDIT_TOTAL_TIMEOUT_SECONDS", "29"),
            ("KAIGO_BROWSER_AUDIT_TOTAL_TIMEOUT_SECONDS", "301"),
            ("KAIGO_CHAT_IP_RATE_LIMIT_REQUESTS", "0"),
            ("KAIGO_CHAT_GLOBAL_CONCURRENCY", "33"),
            ("KAIGO_CHAT_SESSION_SECRET", "too-short"),
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.load(**{name: value})

    def test_existing_google_ai_key_alias_is_supported(self):
        config = self.load(GOOGLE_AI_API_KEY="existing-key")
        self.assertEqual(config.gemini_api_key, "existing-key")

    def test_blank_high_priority_key_does_not_mask_valid_alias(self):
        config = self.load(
            GEMINI_API_KEY="   ",
            GOOGLE_AI_API_KEY="existing-key",
        )
        self.assertEqual(config.gemini_api_key, "existing-key")


if __name__ == "__main__":
    unittest.main()
