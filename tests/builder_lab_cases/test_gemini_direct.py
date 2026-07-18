import asyncio
import json
import types as std_types
import unittest

from builder_lab.engines.base import BuilderEngineError
from builder_lab.engines.gemini_direct import GeminiDirectEngine, build_http_options
from builder_lab.models import (
    BuilderRequest,
    EngineName,
    Stage,
    ValidationIssue,
)
from tests.builder_lab_cases.test_validation import artifact


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.models = FakeModels(response=response, error=error)
        self.aio = std_types.SimpleNamespace(models=self.models)


def fake_response(candidate=None):
    candidate = candidate or artifact(revision=2, stage=Stage.FOUNDATION)
    usage = std_types.SimpleNamespace(
        prompt_token_count=120,
        candidates_token_count=40,
        thoughts_token_count=10,
        total_token_count=170,
    )
    return std_types.SimpleNamespace(
        text=json.dumps(candidate.to_dict(), ensure_ascii=False),
        parsed=None,
        response_id="response-123",
        usage_metadata=usage,
        model_version="gemini-3.5-flash",
    )


class GeminiDirectEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = BuilderRequest(
            engine=EngineName.DIRECT,
            brief="Создай премиального AI-консультанта для архитектурного бюро",
            locale="ru",
            creativity=1.1,
        )

    async def test_builds_structured_async_request_and_parses_usage(self):
        client = FakeClient(response=fake_response())
        engine = GeminiDirectEngine(
            api_key="secret",
            model="gemini-3.5-flash",
            base_url="https://proxy.test/protected/v1beta",
            client=client,
        )

        result = await engine.generate(
            request=self.request,
            stage=Stage.FOUNDATION,
            revision=2,
            previous_artifact=artifact(revision=1, stage=Stage.ART_DIRECTION),
        )

        self.assertEqual(result.artifact.revision, 2)
        self.assertEqual(result.usage.prompt_tokens, 120)
        self.assertEqual(result.usage.output_tokens, 40)
        self.assertEqual(result.usage.thinking_tokens, 10)
        self.assertEqual(result.provider_request_id, "response-123")
        call = client.models.calls[0]
        self.assertEqual(call["model"], "gemini-3.5-flash")
        self.assertEqual(call["config"].temperature, 1.1)
        self.assertEqual(call["config"].top_p, 1.0)
        self.assertEqual(call["config"].response_mime_type, "application/json")
        self.assertIn("schema_version", call["config"].response_json_schema["properties"])
        self.assertIn("архитектурного бюро", call["contents"])
        self.assertIn('"revision":1', call["contents"])

    async def test_prompt_demands_premium_non_generic_safe_russian_widget(self):
        client = FakeClient(
            response=fake_response(artifact(revision=3, stage=Stage.IDENTITY))
        )
        engine = GeminiDirectEngine(api_key="secret", client=client)
        await engine.generate(
            request=self.request,
            stage=Stage.IDENTITY,
            revision=3,
            previous_artifact=artifact(revision=2),
        )
        prompt = client.models.calls[0]["contents"]
        self.assertIn("русском", prompt.lower())
        self.assertIn("премиаль", prompt.lower())
        self.assertIn("фиолет", prompt.lower())
        self.assertIn("javascript", prompt.lower())
        self.assertIn("полный", prompt.lower())
        self.assertIn("бесконечные анимации запрещены", prompt.lower())
        self.assertIn("animation-iteration-count", prompt)
        self.assertIn("prefers-reduced-motion", prompt)
        self.assertIn("не более 12 повторов", prompt.lower())
        self.assertIn("svg path", prompt.lower())
        self.assertIn("circle", prompt.lower())

    async def test_repair_prompt_contains_only_concrete_issues_and_candidate(self):
        client = FakeClient(response=fake_response(artifact(revision=4, stage=Stage.IDENTITY)))
        engine = GeminiDirectEngine(api_key="secret", client=client)
        issues = (
            ValidationIssue("unscoped_css", "css", "Every selector must be scoped"),
            ValidationIssue("missing_region", "body_html", "composer is missing"),
        )
        await engine.generate(
            request=self.request,
            stage=Stage.IDENTITY,
            revision=4,
            previous_artifact=artifact(revision=3),
            repair_issues=issues,
        )
        prompt = client.models.calls[0]["contents"]
        self.assertIn("unscoped_css", prompt)
        self.assertIn("missing_region", prompt)
        self.assertIn('"revision":3', prompt)
        self.assertIn("Режим: repair", prompt)
        self.assertIn("stage строго identity", prompt)

    async def test_provider_errors_are_sanitized(self):
        cases = (
            (RuntimeError("429 RESOURCE_EXHAUSTED secret route"), "quota_exceeded"),
            (RuntimeError("404 model gemini-nope not found"), "model_unavailable"),
            (TimeoutError("upstream timeout"), "generation_timeout"),
            (RuntimeError("connection refused https://private-route"), "provider_unavailable"),
        )
        for error, code in cases:
            with self.subTest(code=code):
                engine = GeminiDirectEngine(api_key="secret", client=FakeClient(error=error))
                with self.assertRaises(BuilderEngineError) as caught:
                    await engine.generate(
                        request=self.request,
                        stage=Stage.ART_DIRECTION,
                        revision=1,
                    )
                self.assertEqual(caught.exception.error_code, code)
                self.assertNotIn("private-route", caught.exception.public_message)

    async def test_cancellation_is_not_wrapped(self):
        engine = GeminiDirectEngine(
            api_key="secret", client=FakeClient(error=asyncio.CancelledError())
        )
        with self.assertRaises(asyncio.CancelledError):
            await engine.generate(
                request=self.request,
                stage=Stage.ART_DIRECTION,
                revision=1,
            )

    def test_requires_key_and_normalizes_proxy_base_url(self):
        with self.assertRaises(BuilderEngineError) as caught:
            GeminiDirectEngine(api_key=None)
        self.assertEqual(caught.exception.error_code, "missing_api_key")
        options = build_http_options("https://proxy.test/protected/v1beta")
        self.assertEqual(options.base_url, "https://proxy.test/protected")
        self.assertEqual(options.api_version, "v1beta")


if __name__ == "__main__":
    unittest.main()
