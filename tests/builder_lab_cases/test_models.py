import unittest

from builder_lab.models import (
    BuilderEvent,
    BuilderRequest,
    EngineName,
    Stage,
    TokenUsage,
    ValidationIssue,
    WidgetArtifact,
)


class BuilderModelsTests(unittest.TestCase):
    def test_request_normalizes_defaults_and_round_trips(self):
        request = BuilderRequest.from_dict(
            {
                "engine": "direct",
                "brief": "  Создайте премиального AI-консультанта  ",
            }
        )

        self.assertEqual(request.engine, EngineName.DIRECT)
        self.assertEqual(request.brief, "Создайте премиального AI-консультанта")
        self.assertEqual(request.locale, "ru")
        self.assertEqual(request.creativity, 0.9)
        self.assertEqual(request.max_repairs, 3)
        self.assertEqual(request.viewport_targets, ("desktop", "mobile"))
        self.assertEqual(BuilderRequest.from_dict(request.to_dict()), request)

    def test_request_allows_a_bounded_fourth_repair(self):
        request = BuilderRequest.from_dict(
            {"engine": "direct", "brief": "x", "max_repairs": 4}
        )
        self.assertEqual(request.max_repairs, 4)

    def test_request_rejects_invalid_inputs(self):
        invalid = [
            {"engine": "unknown", "brief": "valid brief"},
            {"engine": "direct", "brief": " "},
            {"engine": "direct", "brief": "x", "creativity": 2.1},
            {"engine": "direct", "brief": "x", "max_repairs": 8},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                BuilderRequest.from_dict(payload)

    def test_artifact_round_trips_and_requires_positive_revision(self):
        artifact = WidgetArtifact.from_dict(
            {
                "schema_version": "1.0",
                "revision": 3,
                "stage": "conversation",
                "art_direction": "A warm editorial concierge",
                "body_html": '<section class="kaigo-widget"></section>',
                "css": ".kaigo-widget { color: #fff; }",
                "theme_tokens": {"accent": "#ff8d45"},
                "suggested_actions": ["Подобрать решение", "Узнать стоимость"],
            }
        )

        self.assertEqual(artifact.stage, Stage.CONVERSATION)
        self.assertEqual(WidgetArtifact.from_dict(artifact.to_dict()), artifact)
        with self.assertRaises(ValueError):
            WidgetArtifact.from_dict({**artifact.to_dict(), "revision": 0})

    def test_usage_addition_includes_thinking_tokens(self):
        total = TokenUsage(prompt_tokens=10, output_tokens=4, thinking_tokens=3)
        total += TokenUsage(prompt_tokens=5, output_tokens=2, thinking_tokens=1)
        self.assertEqual(total.to_dict(), {
            "prompt_tokens": 15,
            "output_tokens": 6,
            "thinking_tokens": 4,
            "total_tokens": 25,
        })

    def test_event_public_dict_contains_stable_fields(self):
        event = BuilderEvent.create(
            run_id="run-public",
            sequence=7,
            event_type="stage.failed",
            stage=Stage.IDENTITY,
            status="failed",
            message="Не удалось создать этап",
            revision=2,
            error_code="provider_unavailable",
            issues=(ValidationIssue("unsafe_html", "body_html", "Unsafe HTML"),),
        )

        payload = event.to_dict()
        self.assertEqual(payload["sequence"], 7)
        self.assertEqual(payload["stage"], "identity")
        self.assertEqual(payload["error_code"], "provider_unavailable")
        self.assertNotIn("diagnostic", payload)
        self.assertEqual(BuilderEvent.from_dict(payload), event)


if __name__ == "__main__":
    unittest.main()
