import unittest

from builder_lab.model_config import generation_policy


class GeminiGenerationPolicyTests(unittest.TestCase):
    def test_gemini_36_high_omits_sampling(self):
        policy = generation_policy(
            "gemini-3.6-flash",
            "high",
            temperature=0.9,
        )

        self.assertEqual(policy.thinking_config.thinking_level.value, "HIGH")
        self.assertEqual(policy.sampling_kwargs, {})

    def test_gemini_35_flash_high_keeps_supported_sampling(self):
        policy = generation_policy(
            "gemini-3.5-flash",
            "high",
            temperature=0.4,
        )

        self.assertEqual(policy.thinking_config.thinking_level.value, "HIGH")
        self.assertEqual(
            policy.sampling_kwargs,
            {"temperature": 0.4, "top_p": 1.0},
        )

    def test_gemini_35_flash_lite_medium_omits_sampling(self):
        policy = generation_policy(
            "models/gemini-3.5-flash-lite",
            "medium",
            temperature=0.35,
            include_thoughts=False,
        )

        self.assertEqual(policy.thinking_config.thinking_level.value, "MEDIUM")
        self.assertFalse(policy.thinking_config.include_thoughts)
        self.assertEqual(policy.sampling_kwargs, {})

    def test_gemini_25_flash_preserves_budget_compatibility(self):
        policy = generation_policy(
            "gemini-2.5-flash",
            "low",
            temperature=0.2,
        )

        self.assertEqual(policy.thinking_config.thinking_budget, 0)
        self.assertIsNone(policy.thinking_config.thinking_level)
        self.assertEqual(
            policy.sampling_kwargs,
            {"temperature": 0.2, "top_p": 1.0},
        )

    def test_rejects_unknown_thinking_level(self):
        with self.assertRaisesRegex(ValueError, "thinking level"):
            generation_policy("gemini-3.6-flash", "maximum")


if __name__ == "__main__":
    unittest.main()
