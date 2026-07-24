import unittest

from builder_lab.models import (
    BuilderEvent,
    BuilderRequest,
    CreativeProfile,
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
        self.assertEqual(request.contract_id, "chat-v1")
        self.assertEqual(request.creative_profile, CreativeProfile.BALANCED)
        self.assertEqual(request.visual_repair_limit, 5)
        self.assertEqual(BuilderRequest.from_dict(request.to_dict()), request)

    def test_request_round_trips_explicit_contract_and_profile(self):
        request = BuilderRequest(
            engine=EngineName.DIRECT,
            brief="Собери чат",
            contract_id="chat-v1",
            creative_profile=CreativeProfile.BRAND_MOTION,
            visual_repair_limit=1,
        )

        self.assertEqual(BuilderRequest.from_dict(request.to_dict()), request)
        self.assertEqual(request.to_dict()["creative_profile"], "brand_motion")

    def test_request_accepts_bounded_visual_repair_limits(self):
        for limit in (0, 5):
            with self.subTest(limit=limit):
                request = BuilderRequest.from_dict(
                    {
                        "engine": "direct",
                        "brief": "x",
                        "visual_repair_limit": limit,
                    }
                )
                self.assertEqual(request.visual_repair_limit, limit)

    def test_request_allows_a_bounded_fourth_repair(self):
        request = BuilderRequest.from_dict(
            {"engine": "direct", "brief": "x", "max_repairs": 4}
        )
        self.assertEqual(request.max_repairs, 4)

    def test_request_preserves_bounded_grounded_reference_context(self):
        context = '{"visual_summary":"sharp editorial grid"}'
        request = BuilderRequest.from_dict(
            {"engine": "direct", "brief": "x", "reference_context": context}
        )

        self.assertEqual(request.reference_context, context)
        self.assertEqual(BuilderRequest.from_dict(request.to_dict()), request)
        for invalid in ("x" * 8_001, "safe\x00unsafe"):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                BuilderRequest.from_dict(
                    {"engine": "direct", "brief": "x", "reference_context": invalid}
                )

    def test_request_round_trips_optional_public_https_source_url(self):
        request = BuilderRequest.from_dict(
            {
                "engine": "direct",
                "brief": "Сделай консультанта",
                "source_url": "  https://example.com/services  ",
            }
        )

        self.assertEqual(request.source_url, "https://example.com/services")
        self.assertEqual(
            BuilderRequest.from_dict(request.to_dict()).source_url,
            "https://example.com/services",
        )

    def test_request_rejects_unsafe_or_ambiguous_source_urls(self):
        invalid_urls = (
            "http://example.com/",
            "https://user:pass@example.com/",
            "https://example.com:8443/",
            "https://example.com/#fragment",
            "https://example.com/?token=secret",
            "file:///etc/passwd",
            "x" * 2_049,
        )

        for source_url in invalid_urls:
            with self.subTest(source_url=source_url), self.assertRaises(ValueError):
                BuilderRequest.from_dict(
                    {
                        "engine": "direct",
                        "brief": "x",
                        "source_url": source_url,
                    }
                )

    def test_request_rejects_invalid_inputs(self):
        invalid = [
            {"engine": "unknown", "brief": "valid brief"},
            {"engine": "direct", "brief": " "},
            {"engine": "direct", "brief": "x", "creativity": 2.1},
            {"engine": "direct", "brief": "x", "max_repairs": 8},
            {"engine": "direct", "brief": "x", "contract_id": "chat-v2"},
            {"engine": "direct", "brief": "x", "creative_profile": "unknown"},
            {"engine": "direct", "brief": "x", "visual_repair_limit": -1},
            {"engine": "direct", "brief": "x", "visual_repair_limit": 6},
            {"engine": "direct", "brief": "x", "visual_repair_limit": 1.5},
            {"engine": "direct", "brief": "x", "visual_repair_limit": True},
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

    def test_artifact_round_trips_experimental_fields(self):
        artifact = WidgetArtifact.from_dict(
            {
                "schema_version": "1.0",
                "revision": 4,
                "stage": "motion_polish",
                "art_direction": "Animated editorial concierge",
                "body_html": '<section class="kaigo-widget"></section>',
                "css": ".kaigo-widget { color: #111; }",
                "theme_tokens": {"accent": "#ff5533"},
                "suggested_actions": ["Начать"],
                "change_summary": "Добавлено появление по прокрутке.",
                "javascript": (
                    "addEventListener('scroll', () => "
                    "document.body.dataset.y = String(scrollY))"
                ),
                "layout_contract": {
                    "desktop_panel_width": "428px",
                    "mobile_panel_height": "68dvh",
                },
            }
        )

        self.assertEqual(
            WidgetArtifact.from_dict(artifact.to_dict()),
            artifact,
        )
        self.assertEqual(
            artifact.layout_contract["desktop_panel_width"],
            "428px",
        )

    def test_old_artifact_defaults_experimental_fields(self):
        artifact = WidgetArtifact.from_dict(
            {
                "schema_version": "1.0",
                "revision": 1,
                "stage": "art_direction",
                "art_direction": "Legacy baseline",
                "body_html": '<section class="kaigo-widget"></section>',
                "css": ".kaigo-widget{}",
            }
        )

        self.assertEqual(artifact.change_summary, "")
        self.assertEqual(artifact.javascript, "")
        self.assertEqual(artifact.layout_contract, {})

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
            changes=("body_html", "css", "javascript"),
        )

        payload = event.to_dict()
        self.assertEqual(payload["sequence"], 7)
        self.assertEqual(payload["stage"], "identity")
        self.assertEqual(payload["error_code"], "provider_unavailable")
        self.assertEqual(payload["changes"], ["body_html", "css", "javascript"])
        self.assertNotIn("diagnostic", payload)
        self.assertEqual(BuilderEvent.from_dict(payload), event)


if __name__ == "__main__":
    unittest.main()
