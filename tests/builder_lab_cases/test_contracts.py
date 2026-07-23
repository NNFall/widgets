import unittest

from builder_lab.contracts import resolve_widget_contract


class WidgetContractTests(unittest.TestCase):
    def test_chat_v1_exposes_chat_runtime_and_motion_invariants(self):
        contract = resolve_widget_contract("chat-v1")

        self.assertEqual(contract.contract_id, "chat-v1")
        self.assertEqual(contract.version, 1)
        self.assertEqual(contract.attention_delay_seconds, 15)
        self.assertEqual(
            contract.required_regions,
            (
                "root",
                "launcher",
                "panel",
                "header",
                "messages",
                "suggestions",
                "composer",
            ),
        )

        prompt = contract.prompt_block
        for required in (
            'data-region="launcher"',
            'data-region="header"',
            'data-region="messages"',
            'data-region="composer"',
            'data-kaigo-runtime-message="assistant"',
            'data-kaigo-runtime-message="user"',
            'data-kaigo-runtime-status="pending"',
            'data-kaigo-runtime-status="error"',
            'data-kaigo-runtime-retry="true"',
            "launcher, opening, open, pending, error, closing, and closed",
            "15-second",
            "stop permanently after the first interaction",
            "must not play sound",
            "must not block",
            "opening and closing feedback",
            "prefers-reduced-motion",
            "320 to 440 CSS px",
            "64% to 78%",
            "44px by 44px",
            "Art direction remains free",
        ):
            with self.subTest(required=required):
                self.assertIn(required, prompt)

    def test_chat_v1_does_not_fix_art_direction_or_one_panel_size(self):
        prompt = resolve_widget_contract("chat-v1").prompt_block.lower()

        self.assertNotIn("panel must be exactly", prompt)
        self.assertNotIn("avatar must be", prompt)
        self.assertNotIn("color must be", prompt)
        self.assertNotRegex(prompt, r"#[0-9a-f]{3,8}\b")

    def test_chat_v1_makes_attention_timing_and_state_runtime_owned(self):
        prompt = resolve_widget_contract("chat-v1").prompt_block

        self.assertIn(
            "fixed runtime owns and schedules the 15-second attention timer",
            prompt,
        )
        self.assertIn(
            "owns and applies the semantic attention state "
            "`.kaigo-preview-attention`",
            prompt,
        )
        self.assertIn(
            "When the attention capability is enabled, generated CSS/SVG must provide",
            prompt,
        )
        self.assertIn(
            "must not implement its own attention timer, delayed JavaScript, or "
            "attention-state toggling",
            prompt,
        )
        self.assertNotIn("launcher may use one", prompt)

    def test_unknown_contract_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "unsupported widget contract",
        ):
            resolve_widget_contract("chat-v2")


if __name__ == "__main__":
    unittest.main()
