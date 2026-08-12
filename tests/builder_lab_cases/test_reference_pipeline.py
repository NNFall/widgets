import hashlib
import json
import unittest
from datetime import datetime, timezone

from app.models.contracts import ModelUsage
from builder_lab.reference_models import (
    CrawlFailure,
    ReferenceCrawlResult,
    ReferencePageEvidence,
    ScreenshotEvidence,
)
from builder_lab.models import TokenUsage
from builder_lab.reference_pipeline import (
    GeminiReferencePipeline,
    ReferencePipelineError,
    compile_reference_context,
)


def screenshot(
    screenshot_id: str,
    page_id: str,
    viewport: str,
    position: str,
) -> ScreenshotEvidence:
    data = f"{viewport}-{position}".encode()
    return ScreenshotEvidence(
        screenshot_id=screenshot_id,
        page_id=page_id,
        viewport=viewport,
        position=position,
        mime_type="image/jpeg",
        width=1440 if viewport == "desktop" else 390,
        height=900 if viewport == "desktop" else 844,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        data=data,
    )


def crawl_result() -> ReferenceCrawlResult:
    now = datetime.now(timezone.utc)
    desktop = ReferencePageEvidence(
        page_id="home",
        category="home",
        requested_url="https://example.com/",
        final_url="https://example.com/",
        depth=0,
        screenshots=tuple(
            screenshot(f"desktop-{position}", "home", "desktop", position)
            for position in ("top", "after_top", "middle", "bottom")
        ),
        timings_ms={"navigation": 123.4, "evidence_pass": 456.7, "evidence_steps": 4},
        coverage_status="complete",
    )
    mobile = ReferencePageEvidence(
        page_id="home-mobile",
        category="home",
        requested_url="https://example.com/",
        final_url="https://example.com/",
        depth=0,
        screenshots=tuple(
            screenshot(f"mobile-{position}", "home-mobile", "mobile", position)
            for position in ("top", "middle", "bottom")
        ),
        timings_ms={"navigation": 98.7, "evidence_pass": 321.0, "evidence_steps": 5},
        coverage_status="complete",
    )
    return ReferenceCrawlResult.succeeded(
        source_url="https://example.com/",
        pages=(desktop, mobile),
        started_at=now,
        completed_at=now,
    )


def analysis_payload() -> dict:
    return {
        "schema_version": "kaigo.reference.v1",
        "source": {"url": "https://example.com/", "kind": "public website"},
        "analysis": {
            "visual_summary": "Строгая светлая сетка с крупной чёрной типографикой.",
            "public_facts": [
                {
                    "statement": "Компания показывает услуги на главной странице.",
                    "evidence": ["desktop.top"],
                }
            ],
            "visual_tokens": {
                "palette": [
                    {
                        "token": "background",
                        "value": "warm white",
                        "evidence": ["desktop.top", "mobile.top"],
                    }
                ],
                "typography": [],
                "geometry": [],
                "motion": [],
            },
        },
        "provenance": {
            "model": "gemini-3.5-flash",
            "usage": {
                "prompt_tokens": 10,
                "output_tokens": 5,
                "thinking_tokens": 3,
                "total_tokens": 18,
            },
        },
    }


class FakeCrawler:
    def __init__(self, result):
        self.result = result
        self.urls = []

    async def crawl(self, url):
        self.urls.append(url)
        return self.result


class ReferencePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_denial_crosses_pipeline_unchanged(self):
        async def analyzer(**_kwargs):
            from scripts.analyze_reference_site import ReferenceAnalysisError

            raise ReferenceAnalysisError(
                "provider_permission_denied",
                "Сервис генерации недоступен из-за ограничений доступа или оплаты. Обратитесь в поддержку.",
                diagnostic="ProviderPermissionDenied: provider access is not permitted",
            )

        pipeline = GeminiReferencePipeline(
            crawler=FakeCrawler(crawl_result()),
            analyzer=analyzer,
            api_key="direct-key-is-not-used",
            model="routed-model",
            thinking_level="high",
            base_url="https://example.invalid",
        )

        with self.assertRaises(ReferencePipelineError) as caught:
            await pipeline.analyze(
                "https://example.com/",
                structured_backend=object(),
            )

        self.assertEqual(caught.exception.error_code, "provider_permission_denied")
        self.assertIn("ограничений доступа или оплаты", caught.exception.public_message)

    async def test_terminal_routed_failure_crosses_pipeline_with_usage_and_diagnostic(self):
        diagnostic = (
            '{"terminal_reason":"all_generation_timeout",'
            '"route_attempts":[{"cost_microusd":12345}]}'
        )

        async def analyzer(**_kwargs):
            from scripts.analyze_reference_site import ReferenceAnalysisError

            raise ReferenceAnalysisError(
                "route_exhausted",
                "Сервис анализа сайта не смог завершить запрос",
                diagnostic=diagnostic,
                usage=ModelUsage(input_tokens=100, output_tokens=20),
            )

        pipeline = GeminiReferencePipeline(
            crawler=FakeCrawler(crawl_result()),
            analyzer=analyzer,
            api_key="direct-key-is-not-used",
            model="routed-model",
            thinking_level="high",
            base_url="https://example.invalid",
        )

        with self.assertRaises(ReferencePipelineError) as caught:
            await pipeline.analyze(
                "https://example.com/",
                structured_backend=object(),
            )

        self.assertEqual(caught.exception.error_code, "route_exhausted")
        self.assertEqual(
            caught.exception.usage,
            TokenUsage(prompt_tokens=100, output_tokens=20),
        )
        self.assertEqual(caught.exception.diagnostic, diagnostic)

    async def test_routed_analyzer_failure_is_provider_neutral(self):
        async def analyzer(**_kwargs):
            raise RuntimeError("private Gemini GPT GLM AgentRouter failure")

        pipeline = GeminiReferencePipeline(
            crawler=FakeCrawler(crawl_result()),
            analyzer=analyzer,
            api_key="direct-key-is-not-used",
            model="routed-model",
            thinking_level="high",
            base_url="https://example.invalid",
        )

        with self.assertRaises(ReferencePipelineError) as caught:
            await pipeline.analyze(
                "https://example.com/",
                structured_backend=object(),
            )

        self.assertEqual(caught.exception.error_code, "reference_analysis_failed")
        self.assertFalse(
            any(
                provider in caught.exception.public_message
                for provider in ("Gemini", "GPT", "GLM", "AgentRouter")
            )
        )

    async def test_captures_seven_states_and_reports_capture_before_ai_analysis(self):
        crawler = FakeCrawler(crawl_result())
        analyzer_calls = []
        progress = []

        async def analyzer(**kwargs):
            progress.append(("analyzer_called", {}))
            analyzer_calls.append(kwargs)
            self.assertTrue(kwargs["capture_manifest"].is_file())
            self.assertTrue(kwargs["evidence_root"].is_dir())
            self.assertEqual(
                [label for label, _path in kwargs["screenshot_inputs"]],
                [
                    "desktop.top",
                    "desktop.after_top",
                    "desktop.middle",
                    "desktop.bottom",
                    "mobile.top",
                    "mobile.middle",
                    "mobile.bottom",
                ],
            )
            return analysis_payload()

        async def report(phase, payload):
            progress.append((phase, payload))

        pipeline = GeminiReferencePipeline(
            crawler=crawler,
            analyzer=analyzer,
            api_key="secret",
            model="gemini-3.5-flash",
            thinking_level="high",
            base_url="https://generativelanguage.googleapis.com",
        )

        result = await pipeline.analyze(
            "https://example.com/",
            progress_callback=report,
        )

        self.assertEqual(crawler.urls, ["https://example.com/"])
        self.assertEqual(len(analyzer_calls), 1)
        self.assertIn('"visual_summary"', result.context)
        self.assertIn("Строгая светлая сетка", result.context)
        self.assertLessEqual(len(result.context), 8_000)
        self.assertEqual(result.summary, analysis_payload()["analysis"]["visual_summary"])
        self.assertEqual(result.usage.total_tokens, 18)
        self.assertEqual(
            [phase for phase, _payload in progress],
            ["capture_completed", "analysis_started", "analyzer_called"],
        )
        self.assertEqual(progress[0][1]["screenshot_count"], 7)
        self.assertEqual(
            result.capture_metrics,
            {
                "total_ms": 0.0,
                "viewports": {
                    "desktop": {
                        "navigation": 123.4,
                        "evidence_pass": 456.7,
                        "evidence_steps": 4,
                    },
                    "mobile": {
                        "navigation": 98.7,
                        "evidence_pass": 321.0,
                        "evidence_steps": 5,
                    },
                },
            },
        )

    async def test_rejects_incomplete_or_failed_capture(self):
        failed = ReferenceCrawlResult.failed(
            source_url="https://example.com/",
            failure=CrawlFailure(code="crawl_failed", message="navigation failed"),
            started_at=datetime.now(timezone.utc),
        )
        pipeline = GeminiReferencePipeline(
            crawler=FakeCrawler(failed),
            analyzer=lambda **_kwargs: None,
            api_key="secret",
            model="gemini-3.5-flash",
            thinking_level="high",
            base_url="https://generativelanguage.googleapis.com",
        )

        with self.assertRaises(ReferencePipelineError) as caught:
            await pipeline.analyze("https://example.com/")

        self.assertEqual(caught.exception.error_code, "reference_capture_failed")

    def test_compiler_prunes_oversized_model_output_but_keeps_truth(self):
        payload = analysis_payload()
        payload["analysis"]["visual_summary"] = "S" * 1_200
        payload["analysis"]["public_facts"] = [
            {"statement": f"{index}-" + "F" * 480, "evidence": ["desktop.top"]}
            for index in range(24)
        ]
        payload["analysis"]["visual_tokens"] = {
            category: [
                {
                    "token": f"{category}-{index}",
                    "value": "V" * 220,
                    "evidence": ["desktop.top", "mobile.top"],
                }
                for index in range(16)
            ]
            for category in ("palette", "typography", "geometry", "motion")
        }

        result = compile_reference_context(payload)
        decoded = json.loads(result.context)

        self.assertLessEqual(len(result.context), 8_000)
        self.assertTrue(decoded["public_facts"])
        self.assertTrue(decoded["visual_summary"])
        self.assertEqual(decoded["source_url"], "https://example.com/")


if __name__ == "__main__":
    unittest.main()
