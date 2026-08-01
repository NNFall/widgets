import asyncio
import json
import types as std_types
import unittest
from unittest.mock import AsyncMock, patch

from app.models.contracts import (
    BilledModelProviderError,
    ModelResponse,
    ModelRouteAttempt,
    ModelRouteExhausted,
    ModelUsage,
    ProviderPermissionDenied,
    ProviderTimeout,
)
from builder_lab.engines.base import BuilderEngineError
from builder_lab.engines.gemini_direct import (
    GeminiDirectEngine,
    build_http_options,
    build_provider_json_schema,
)
from builder_lab.models import (
    BuilderRequest,
    ConceptRole,
    ConceptRoleBrief,
    DirectionProposal,
    DirectionRole,
    EngineName,
    Stage,
    ValidationIssue,
)
from tests.builder_lab_cases.test_validation import artifact
from builder_lab.visual_models import (
    NormalizedRegion,
    VisualCategory,
    VisualFinding,
    VisualSeverity,
)
from builder_lab.patterns.registry import load_builtin_registry


class FakeModels:
    def __init__(self, response=None, error=None):
        self.responses = response if isinstance(response, list) else None
        self.response = response if self.responses is None else self.responses[-1]
        self.errors = error if isinstance(error, list) else None
        self.error = error if self.errors is None else None
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        error = self.error
        if self.errors is not None:
            error = self.errors[min(len(self.calls) - 1, len(self.errors) - 1)]
        if error:
            raise error
        if self.responses is not None:
            return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.models = FakeModels(response=response, error=error)
        self.aio = std_types.SimpleNamespace(models=self.models)


class BilledProviderUnavailable(BilledModelProviderError):
    error_code = "provider_unavailable"


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

    async def test_routed_provider_timeout_becomes_builder_generation_timeout(self):
        router = std_types.SimpleNamespace(
            generate=AsyncMock(
                side_effect=ProviderTimeout("AgentRouter GPT request timed out")
            )
        )
        engine = GeminiDirectEngine(
            model="configured-gemini-model",
            model_router=router,
            routing_role="direction_candidate",
        )

        with self.assertRaises(BuilderEngineError) as caught:
            await engine.propose_direction(
                request=self.request,
                role=DirectionRole.BRAND_ARCHAEOLOGIST,
                proposal_id="candidate-1",
            )

        self.assertEqual(caught.exception.error_code, "generation_timeout")
        for provider_name in ("Gemini", "GPT", "GLM"):
            self.assertNotIn(provider_name, caught.exception.public_message)

    async def test_routed_provider_unavailable_becomes_provider_neutral_builder_error(self):
        router = std_types.SimpleNamespace(
            generate=AsyncMock(
                side_effect=BilledProviderUnavailable(
                    "Gemini fallback after GPT and GLM was unavailable",
                    usage=ModelUsage(
                        input_tokens=19,
                        output_tokens=11,
                        thinking_tokens=4,
                    ),
                    request_id="billed-failure-1",
                )
            )
        )
        engine = GeminiDirectEngine(
            model="configured-gemini-model",
            model_router=router,
            routing_role="widget_generator",
        )

        with self.assertRaises(BuilderEngineError) as caught:
            await engine.generate(
                request=self.request,
                stage=Stage.FOUNDATION,
                revision=1,
            )

        self.assertEqual(caught.exception.error_code, "provider_unavailable")
        self.assertEqual(caught.exception.usage.prompt_tokens, 19)
        self.assertEqual(caught.exception.usage.output_tokens, 7)
        self.assertEqual(caught.exception.usage.thinking_tokens, 4)
        self.assertIn("BilledProviderUnavailable", caught.exception.diagnostic)
        for provider_name in ("Gemini", "GPT", "GLM"):
            self.assertNotIn(provider_name, caught.exception.public_message)

    async def test_direct_permission_denial_is_terminal_and_public_message_is_safe(self):
        client = FakeClient(
            error=RuntimeError(
                "403 PERMISSION_DENIED. Lightning dunning decision is deny for "
                "project: projects/671587661095"
            )
        )
        engine = GeminiDirectEngine(api_key="test-key", client=client)

        with self.assertRaises(BuilderEngineError) as caught:
            await engine.propose_direction(
                request=self.request,
                role=DirectionRole.BRAND_ARCHAEOLOGIST,
                proposal_id="candidate-1",
            )

        self.assertEqual(caught.exception.error_code, "provider_permission_denied")
        self.assertEqual(len(client.aio.models.calls), 1)
        self.assertIn("ограничений доступа или оплаты", caught.exception.public_message)
        self.assertNotIn("671587661095", caught.exception.public_message)
        self.assertNotIn("dunning", caught.exception.public_message.lower())

    async def test_routed_permission_denial_keeps_terminal_code_and_safe_message(self):
        router = std_types.SimpleNamespace(
            generate=AsyncMock(
                side_effect=ProviderPermissionDenied(
                    "provider project access is not permitted"
                )
            )
        )
        engine = GeminiDirectEngine(
            model_router=router,
            routing_role="widget_generator",
        )

        with self.assertRaises(BuilderEngineError) as caught:
            await engine.generate(
                request=self.request,
                stage=Stage.FOUNDATION,
                revision=1,
            )

        self.assertEqual(caught.exception.error_code, "provider_permission_denied")
        self.assertIn("ограничений доступа или оплаты", caught.exception.public_message)
        self.assertNotIn("project", caught.exception.public_message.lower())

    async def test_routed_result_diagnostic_uses_actual_provider_and_model(self):
        payload = artifact(revision=1, stage=Stage.FOUNDATION).to_dict()
        router = std_types.SimpleNamespace(
            generate=AsyncMock(
                return_value=ModelResponse(
                    text=json.dumps(payload),
                    parsed=payload,
                    usage=ModelUsage(input_tokens=12, output_tokens=5),
                    request_id="routed-response-1",
                    raw={"provider": "agentrouter", "model": "gpt-5.5"},
                )
            )
        )
        engine = GeminiDirectEngine(
            model="configured-gemini-model",
            model_router=router,
            routing_role="widget_generator",
        )

        result = await engine.generate(
            request=self.request,
            stage=Stage.FOUNDATION,
            revision=1,
        )

        self.assertEqual(result.diagnostic, "provider=agentrouter; model=gpt-5.5")

    async def test_route_exhaustion_preserves_safe_attempt_diagnostic_and_usage(self):
        route_error = ModelRouteExhausted(
            attempts=(
                ModelRouteAttempt(
                    provider="agentrouter",
                    model="gpt-5.5",
                    outcome="failed",
                    latency_ms=100,
                    usage=ModelUsage(input_tokens=15, output_tokens=6, thinking_tokens=2),
                    cost_microusd=123,
                    cost_state="reported",
                    error_code="invalid_response",
                ),
                ModelRouteAttempt(
                    provider="gemini",
                    model="gemini-fallback",
                    outcome="failed",
                    latency_ms=80,
                    usage=ModelUsage(),
                    cost_microusd=0,
                    cost_state="unknown",
                    error_code="provider_unavailable",
                ),
            ),
            usage=ModelUsage(input_tokens=15, output_tokens=6, thinking_tokens=2),
        )
        router = std_types.SimpleNamespace(generate=AsyncMock(side_effect=route_error))
        engine = GeminiDirectEngine(
            model_router=router,
            routing_role="direction_candidate",
            routing_timeout_seconds=180,
        )

        with self.assertRaises(BuilderEngineError) as caught:
            await engine.propose_direction(
                request=self.request,
                role=DirectionRole.BRAND_ARCHAEOLOGIST,
                proposal_id="candidate-1",
            )

        self.assertEqual(caught.exception.error_code, "route_exhausted")
        self.assertEqual(caught.exception.diagnostic, route_error.diagnostic)
        self.assertEqual(caught.exception.usage.prompt_tokens, 15)
        self.assertEqual(caught.exception.usage.output_tokens, 4)
        self.assertEqual(caught.exception.usage.thinking_tokens, 2)
        for provider_name in ("Gemini", "GPT", "GLM"):
            self.assertNotIn(provider_name, caught.exception.public_message)
        routed_timeout = router.generate.await_args.kwargs["timeout_seconds"]
        self.assertGreater(routed_timeout, 0)
        self.assertLessEqual(routed_timeout, 180)

    async def test_routed_local_validation_error_is_provider_neutral(self):
        router = std_types.SimpleNamespace(
            generate=AsyncMock(
                return_value=ModelResponse(
                    text="{}",
                    parsed={"unexpected": "payload"},
                    usage=ModelUsage(input_tokens=4, output_tokens=2),
                    raw={"provider": "agentrouter", "model": "gpt-5.5"},
                )
            )
        )
        engine = GeminiDirectEngine(
            model_router=router,
            routing_role="direction_candidate",
            routing_timeout_seconds=180,
        )

        with self.assertRaises(BuilderEngineError) as caught:
            await engine.propose_direction(
                request=self.request,
                role=DirectionRole.BRAND_ARCHAEOLOGIST,
                proposal_id="candidate-1",
            )

        self.assertEqual(caught.exception.error_code, "invalid_artifact")
        for provider_name in ("Gemini", "GPT", "GLM"):
            self.assertNotIn(provider_name, caught.exception.public_message)
        self.assertEqual(router.generate.await_count, 2)
        timeouts = [
            call.kwargs["timeout_seconds"]
            for call in router.generate.await_args_list
        ]
        self.assertGreater(timeouts[0], timeouts[1])
        self.assertLessEqual(timeouts[0], 180)
        self.assertGreater(timeouts[1], 0)

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

    async def test_gemini_36_uses_high_thinking_without_sampling(self):
        client = FakeClient(response=fake_response())
        engine = GeminiDirectEngine(
            api_key="secret",
            model="gemini-3.6-flash",
            thinking_level="high",
            client=client,
        )

        await engine.generate(
            request=self.request,
            stage=Stage.FOUNDATION,
            revision=2,
            previous_artifact=artifact(revision=1, stage=Stage.ART_DIRECTION),
        )

        config = client.models.calls[0]["config"]
        self.assertEqual(config.thinking_config.thinking_level.value, "HIGH")
        self.assertIsNone(config.temperature)
        self.assertIsNone(config.top_p)

    async def test_gemini_2_5_uses_structural_provider_schema(self):
        client = FakeClient(response=fake_response())
        engine = GeminiDirectEngine(
            api_key="secret",
            model="gemini-2.5-flash",
            client=client,
        )

        await engine.generate(
            request=self.request,
            stage=Stage.FOUNDATION,
            revision=2,
            previous_artifact=artifact(revision=1, stage=Stage.ART_DIRECTION),
        )

        provider_schema = json.dumps(
            client.models.calls[0]["config"].response_json_schema,
            sort_keys=True,
        )
        config = client.models.calls[0]["config"]
        self.assertIsNone(config.thinking_config.thinking_level)
        self.assertEqual(config.thinking_config.thinking_budget, 0)
        self.assertIsNone(config.max_output_tokens)
        self.assertEqual(config.tools, [])
        self.assertIn('"schema_version"', provider_schema)
        for unsupported in (
            '"enum"',
            '"maxItems"',
            '"minItems"',
            '"maxLength"',
            '"minLength"',
            '"pattern"',
        ):
            self.assertNotIn(unsupported, provider_schema)

    def test_gemini_35_relaxes_nested_serving_constraints_but_keeps_structure(self):
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 6,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["kind", "label"],
                        "properties": {
                            "kind": {"type": "string", "enum": ["pass", "repair"]},
                            "label": {"type": ["string", "null"]},
                            "score": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                        },
                    },
                }
            },
        }

        provider_35 = build_provider_json_schema(schema, "gemini-3.5-flash")
        provider_36 = build_provider_json_schema(schema, "gemini-3.6-flash")
        for model, provider in (
            ("gemini-3.5-flash", provider_35),
            ("gemini-3.6-flash", provider_36),
        ):
            with self.subTest(model=model):
                encoded = json.dumps(provider, sort_keys=True)
                self.assertIn('"required"', encoded)
                self.assertIn('"properties"', encoded)
                self.assertIn('"enum"', encoded)
                self.assertNotIn('"additionalProperties"', encoded)
                self.assertNotIn('"minItems"', encoded)
                self.assertNotIn('"maxItems"', encoded)
                self.assertEqual(
                    provider["properties"]["items"]["items"]["properties"]["label"][
                        "type"
                    ],
                    "string",
                )
        encoded_35 = json.dumps(provider_35, sort_keys=True)
        encoded_36 = json.dumps(provider_36, sort_keys=True)
        self.assertNotIn('"minimum"', encoded_35)
        self.assertNotIn('"maximum"', encoded_35)
        self.assertIn('"minimum"', encoded_36)
        self.assertIn('"maximum"', encoded_36)

    async def test_server_owns_protocol_schema_version_not_the_model(self):
        candidate = artifact(revision=2, stage=Stage.FOUNDATION)
        candidate = type(candidate).from_dict(
            {**candidate.to_dict(), "schema_version": "model-guessed-version"}
        )
        client = FakeClient(response=fake_response(candidate))
        engine = GeminiDirectEngine(
            api_key="secret", model="gemini-2.5-flash", client=client
        )

        result = await engine.generate(
            request=self.request,
            stage=Stage.FOUNDATION,
            revision=2,
            previous_artifact=artifact(revision=1, stage=Stage.ART_DIRECTION),
        )

        self.assertEqual(result.artifact.schema_version, "1.0")

    async def test_stage_generation_retries_one_invalid_protocol_response(self):
        invalid = fake_response(artifact(revision=99, stage=Stage.FOUNDATION))
        valid = fake_response(artifact(revision=2, stage=Stage.FOUNDATION))
        client = FakeClient(response=[invalid, valid])
        engine = GeminiDirectEngine(
            api_key="secret", model="gemini-2.5-flash", client=client
        )

        result = await engine.generate(
            request=self.request,
            stage=Stage.FOUNDATION,
            revision=2,
            previous_artifact=artifact(revision=1, stage=Stage.ART_DIRECTION),
        )

        self.assertEqual(len(client.models.calls), 2)
        self.assertIn("CORRECTION", client.models.calls[1]["contents"])
        self.assertEqual(result.artifact.revision, 2)
        self.assertEqual(result.usage.prompt_tokens, 240)
        self.assertEqual(result.usage.output_tokens, 80)
        self.assertEqual(result.usage.thinking_tokens, 20)

    async def test_prompt_demands_premium_non_generic_safe_russian_widget(self):
        client = FakeClient(
            response=fake_response(artifact(revision=3, stage=Stage.IDENTITY))
        )
        engine = GeminiDirectEngine(
            api_key="secret",
            model="gemini-3.5-flash",
            client=client,
        )
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
        self.assertIn("layout_contract", prompt)
        self.assertIn("change_summary", prompt)
        self.assertIn("полный", prompt.lower())
        self.assertIn("unrestricted javascript", prompt.lower())
        self.assertIn("infinite", prompt.lower())
        self.assertNotIn("animation-iteration-count", prompt)
        self.assertNotIn("216px", prompt)
        self.assertNotIn("372px", prompt)
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

    async def test_visual_repair_prompt_is_separate_untrusted_and_low_creativity(self):
        client = FakeClient(
            response=fake_response(artifact(revision=5, stage=Stage.MOTION_POLISH))
        )
        engine = GeminiDirectEngine(
            api_key="secret",
            model="gemini-3.5-flash",
            client=client,
        )
        visual = VisualFinding(
            finding_id="major-1",
            severity=VisualSeverity.MAJOR,
            category=VisualCategory.PAGE_SUBORDINATION,
            screenshot_id="desktop.open_initial",
            evidence="Panel covers the primary page heading.",
            region=NormalizedRegion(x=0.6, y=0.1, width=0.3, height=0.4),
            artifact_fields=("css",),
            repair_instruction="Reduce the panel footprint while preserving direction.",
            confidence=0.91,
        )

        await engine.generate(
            request=self.request,
            stage=Stage.MOTION_POLISH,
            revision=5,
            previous_artifact=artifact(revision=5, stage=Stage.MOTION_POLISH),
            visual_findings=(visual,),
        )

        call = client.models.calls[0]
        self.assertEqual(call["config"].temperature, 0.35)
        self.assertIn("visual_repair", call["contents"])
        self.assertIn("НЕДОВЕРЕННЫЕ", call["contents"])
        self.assertIn("major-1", call["contents"])
        self.assertIn('"revision":5', call["contents"])

    async def test_direction_proposal_uses_bounded_structured_output(self):
        payload = {
            "title": "Плавающая проектная заметка",
            "art_direction": "Острые углы, hairline и редакционная типографика.",
            "interaction_model": "Компактная заметка раскрывается рядом с launcher.",
            "safeguards": ["Без fake actions", "Не больше двух suggestions"],
        }
        client = FakeClient(response=std_types.SimpleNamespace(
            text=json.dumps(payload, ensure_ascii=False),
            parsed=None,
            response_id="proposal-1",
            usage_metadata=std_types.SimpleNamespace(
                prompt_token_count=20,
                candidates_token_count=8,
                thoughts_token_count=2,
            ),
            model_version="gemini-3.5-flash",
        ))
        engine = GeminiDirectEngine(
            api_key="secret", model="gemini-2.5-flash", client=client
        )

        result = await engine.propose_direction(
            request=self.request,
            role=DirectionRole.BRAND_ARCHAEOLOGIST,
            proposal_id="candidate-1",
        )

        self.assertEqual(result.proposal.proposal_id, "candidate-1")
        self.assertEqual(result.proposal.role, DirectionRole.BRAND_ARCHAEOLOGIST)
        call = client.models.calls[0]
        self.assertIsNone(call["config"].max_output_tokens)
        self.assertIsNone(call["config"].thinking_config.thinking_level)
        self.assertEqual(call["config"].thinking_config.thinking_budget, 0)
        self.assertEqual(call["config"].tools, [])
        self.assertEqual(call["config"].response_mime_type, "application/json")
        self.assertEqual(call["config"].response_json_schema["additionalProperties"], False)
        self.assertIn("brand archaeologist", call["contents"])
        self.assertIn("interaction_model: 1 to 800 characters", call["contents"])
        self.assertIn("safeguards: 0 to 8 items", call["contents"])
        self.assertIn("1 to 160 characters each", call["contents"])

        client.models.response.text = json.dumps({**payload, "unexpected": "ignored?"})
        with self.assertRaises(BuilderEngineError) as caught:
            await engine.propose_direction(
                request=self.request,
                role=DirectionRole.BRAND_ARCHAEOLOGIST,
                proposal_id="candidate-1",
            )
        self.assertEqual(caught.exception.error_code, "invalid_artifact")
        self.assertEqual(caught.exception.usage.prompt_tokens, 60)
        self.assertEqual(caught.exception.usage.output_tokens, 24)
        self.assertEqual(caught.exception.usage.thinking_tokens, 6)

    async def test_concept_role_uses_bounded_output_and_server_owned_role(self):
        payload = {
            "summary": "Редакционная система RAW",
            "decisions": ["Использовать строгую сетку", "Сохранить монохром"],
            "safeguards": ["Не придумывать услуги"],
        }
        response = std_types.SimpleNamespace(
            text=json.dumps(payload, ensure_ascii=False),
            parsed=None,
            response_id="concept-1",
            usage_metadata=std_types.SimpleNamespace(
                prompt_token_count=21,
                candidates_token_count=9,
                thoughts_token_count=3,
            ),
            model_version="gemini-3.6-flash",
        )
        client = FakeClient(response=response)
        engine = GeminiDirectEngine(api_key="secret", client=client)

        result = await engine.develop_concept_role(
            request=self.request,
            role=ConceptRole.SITE_BRAND_ANALYST,
        )

        self.assertEqual(result.brief.role, ConceptRole.SITE_BRAND_ANALYST)
        self.assertEqual(result.usage.prompt_tokens, 21)
        self.assertEqual(result.provider_request_id, "concept-1")
        call = client.models.calls[0]
        self.assertIsNone(call["config"].max_output_tokens)
        self.assertEqual(
            set(call["config"].response_json_schema["properties"]),
            {"summary", "decisions", "safeguards"},
        )
        self.assertNotIn('"role"', call["contents"])

    async def test_concept_role_retries_invalid_payload_and_aggregates_usage(self):
        invalid = {
            "summary": "x" * 81,
            "decisions": ["Valid"],
            "safeguards": [],
        }
        response = std_types.SimpleNamespace(
            text=json.dumps(invalid),
            parsed=None,
            response_id="invalid-concept",
            usage_metadata=std_types.SimpleNamespace(
                prompt_token_count=5,
                candidates_token_count=2,
                thoughts_token_count=1,
            ),
            model_version="gemini-3.6-flash",
        )
        client = FakeClient(response=response)
        engine = GeminiDirectEngine(api_key="secret", client=client)

        with self.assertRaises(BuilderEngineError) as caught:
            await engine.develop_concept_role(
                request=self.request,
                role=ConceptRole.CONVERSATION_DESIGNER,
                prior_briefs=(
                    ConceptRoleBrief(
                        role=ConceptRole.SITE_BRAND_ANALYST,
                        summary="RAW visual system",
                        decisions=("Use the grid",),
                        safeguards=(),
                    ),
                ),
            )

        self.assertEqual(len(client.models.calls), 3)
        self.assertIn("CORRECTION", client.models.calls[1]["contents"])
        self.assertEqual(caught.exception.error_code, "invalid_artifact")
        self.assertEqual(caught.exception.usage.prompt_tokens, 15)
        self.assertEqual(caught.exception.usage.output_tokens, 6)
        self.assertEqual(caught.exception.usage.thinking_tokens, 3)

    async def test_direction_proposal_allows_two_semantic_corrections(self):
        invalid = {
            "title": "Direction",
            "art_direction": "Sharp editorial grid.",
            "interaction_model": "x" * 801,
            "safeguards": ["No fake actions"],
        }
        valid = {
            **invalid,
            "interaction_model": "A compact note opens beside the launcher.",
        }

        def response(payload, response_id):
            return std_types.SimpleNamespace(
                text=json.dumps(payload),
                parsed=None,
                response_id=response_id,
                usage_metadata=std_types.SimpleNamespace(
                    prompt_token_count=20,
                    candidates_token_count=8,
                    thoughts_token_count=0,
                ),
                model_version="gemini-2.5-flash",
            )

        client = FakeClient(
            response=[
                response(invalid, "invalid-1"),
                response(invalid, "invalid-2"),
                response(valid, "valid-3"),
            ]
        )
        engine = GeminiDirectEngine(
            api_key="secret", model="gemini-2.5-flash", client=client
        )

        result = await engine.propose_direction(
            request=self.request,
            role=DirectionRole.BRAND_ARCHAEOLOGIST,
            proposal_id="candidate-1",
        )

        self.assertEqual(len(client.models.calls), 3)
        self.assertIn("CORRECTION", client.models.calls[1]["contents"])
        self.assertIn("CORRECTION", client.models.calls[2]["contents"])
        self.assertEqual(result.usage.prompt_tokens, 60)
        self.assertEqual(result.usage.output_tokens, 24)
        self.assertEqual(result.provider_request_id, "valid-3")

    async def test_direction_judge_is_blind_and_rejects_unknown_selection(self):
        proposals = tuple(
            DirectionProposal(
                proposal_id=f"candidate-{index}",
                role=role,
                title=f"Direction {index}",
                art_direction="Editorial, compact and subordinate to the page.",
                interaction_model="A bounded project note opens on demand.",
                safeguards=("No fake actions",),
            )
            for index, role in enumerate(DirectionRole, start=1)
        )
        response = std_types.SimpleNamespace(
            text=json.dumps({
                "selected_proposal_id": "candidate-2",
                "rationale": "The strongest fixed-matrix result.",
            }),
            parsed=None,
            response_id="judge-1",
            usage_metadata=None,
            model_version="gemini-3.5-flash",
        )
        client = FakeClient(response=response)
        engine = GeminiDirectEngine(api_key="secret", client=client)

        result = await engine.judge_directions(request=self.request, proposals=proposals)

        self.assertEqual(result.judgement.selected_proposal_id, "candidate-2")
        call = client.models.calls[0]
        self.assertIsNone(call["config"].max_output_tokens)
        self.assertNotIn("brand_archaeologist", call["contents"])
        self.assertNotIn("interaction_inventor", call["contents"])
        self.assertNotIn("hostile_conversion_accessibility_critic", call["contents"])
        self.assertIn("site fit", call["contents"].lower())

        client.models.response.text = json.dumps({
            "selected_proposal_id": "candidate-99",
            "rationale": "Unknown",
        })
        with self.assertRaises(BuilderEngineError) as caught:
            await engine.judge_directions(request=self.request, proposals=proposals)
        self.assertEqual(caught.exception.error_code, "invalid_artifact")

    async def test_composition_planner_uses_structured_catalog_without_assets(self):
        payload = {
            "schema_version": 1,
            "direction_id": "candidate-2",
            "selections": [],
            "custom_escape": None,
            "summary": "Черновой план",
        }
        response = std_types.SimpleNamespace(
            text=json.dumps(payload, ensure_ascii=False),
            parsed=None,
            response_id="composition-1",
            usage_metadata=None,
            model_version="gemini-3.6-flash",
        )
        client = FakeClient(response=response)
        engine = GeminiDirectEngine(api_key="secret", client=client)
        selected = DirectionProposal(
            proposal_id="candidate-2",
            role=DirectionRole.INTERACTION_INVENTOR,
            title="Плавающая проектная заметка",
            art_direction="Тёплая компактная карточка.",
            interaction_model="Открывается по запросу.",
            safeguards=("Не перекрывать страницу",),
        )

        result = await engine.plan_composition(
            request=self.request,
            selected_direction=selected,
            public_catalog=load_builtin_registry().public_catalog(),
            correction="required slots are missing",
        )

        self.assertEqual(result.payload, payload)
        self.assertEqual(result.provider_request_id, "composition-1")
        call = client.models.calls[0]
        self.assertIn("orb-pulse", call["contents"])
        self.assertIn("required slots are missing", call["contents"])
        self.assertNotIn("fragment.html", call["contents"])
        self.assertIn("custom_escape", call["config"].response_json_schema["properties"])

    async def test_stage_prompt_contains_selected_direction_and_flexible_widget_bounds(self):
        client = FakeClient(response=fake_response(artifact(revision=1, stage=Stage.ART_DIRECTION)))
        engine = GeminiDirectEngine(api_key="secret", client=client)
        selected = DirectionProposal(
            proposal_id="candidate-2",
            role=DirectionRole.INTERACTION_INVENTOR,
            title="Плавающая проектная заметка",
            art_direction="Editorial note, sharp corners and one outer border.",
            interaction_model="Compact project note that opens on demand.",
            safeguards=("No fake actions",),
        )
        await engine.generate(
            request=self.request,
            stage=Stage.ART_DIRECTION,
            revision=1,
            selected_direction=selected,
        )
        prompt = client.models.calls[0]["contents"]
        for required in (
            "Плавающая проектная заметка",
            "javascript",
            "layout_contract",
            "change_summary",
            "unrestricted JavaScript",
            "fixed runtime remains the sole owner",
            "fit entirely inside the viewport",
            "header, messages, suggestions and composer are peer panel regions",
            "box-sizing: border-box",
            "44px × 44px",
            "close, send, suggestion и retry",
            "scrollHeight <= clientHeight",
            "reset p and heading margins to 0",
            "fake actions",
            "no fullscreen",
            ".kaigo-widget__message--assistant",
            ".kaigo-widget__message--user",
            ".kaigo-widget__message-status",
            ".kaigo-widget__message-status--pending",
            ".kaigo-widget__message-status--error",
            'data-kaigo-runtime-message="assistant"',
            'data-kaigo-runtime-message="user"',
            "ai messages on the left",
            "user messages on the right",
            "chat bubbles",
            "visible author label",
            "one short assistant welcome message",
            "at most two quick replies",
            "roughly 64–78% of the viewport height",
            "hide the entire suggestions region after the first user message",
            "runtime messages and the initial assistant message must share one visual language",
            "permanent facts, prices, service menus and statistic cards",
            'data-kaigo-runtime-retry="true"',
            'data-kaigo-runtime-status="error"',
            '.kaigo-widget [data-kaigo-runtime-retry="true"]',
            "runtime status has none",
        ):
            self.assertIn(required.lower(), prompt.lower())
        for forbidden in (
            "372px",
            "216px",
            "68dvh",
            "70dvh",
            "at most three visible actions total",
            "бесконечные анимации запрещены",
            "без bubbles и avatars",
        ):
            self.assertNotIn(forbidden.lower(), prompt.lower())

    async def test_provider_errors_are_sanitized(self):
        cases = (
            (RuntimeError("429 RESOURCE_EXHAUSTED secret route"), "quota_exceeded"),
            (RuntimeError("404 model gemini-nope not found"), "model_unavailable"),
            (TimeoutError("upstream timeout"), "generation_timeout"),
            (RuntimeError("connection refused https://private-route"), "provider_unavailable"),
        )
        with patch(
            "builder_lab.engines.gemini_direct.asyncio.sleep",
            new=AsyncMock(),
        ):
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

    async def test_transient_provider_error_is_retried_with_backoff(self):
        client = FakeClient(
            response=fake_response(artifact(revision=1, stage=Stage.ART_DIRECTION)),
            error=[RuntimeError("503 upstream unavailable"), None],
        )
        engine = GeminiDirectEngine(api_key="secret", client=client)

        with patch(
            "builder_lab.engines.gemini_direct.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            result = await engine.generate(
                request=self.request,
                stage=Stage.ART_DIRECTION,
                revision=1,
            )

        self.assertEqual(result.artifact.revision, 1)
        self.assertEqual(len(client.models.calls), 2)
        sleep.assert_awaited_once_with(0.5)

    async def test_transient_provider_error_can_recover_on_fifth_attempt(self):
        client = FakeClient(
            response=fake_response(artifact(revision=1, stage=Stage.ART_DIRECTION)),
            error=[
                RuntimeError("503 upstream unavailable"),
                RuntimeError("503 upstream unavailable"),
                RuntimeError("503 upstream unavailable"),
                RuntimeError("503 upstream unavailable"),
                None,
            ],
        )
        engine = GeminiDirectEngine(api_key="secret", client=client)

        with patch(
            "builder_lab.engines.gemini_direct.asyncio.sleep",
            new=AsyncMock(),
        ) as sleep:
            result = await engine.generate(
                request=self.request,
                stage=Stage.ART_DIRECTION,
                revision=1,
            )

        self.assertEqual(result.artifact.revision, 1)
        self.assertEqual(len(client.models.calls), 5)
        self.assertEqual(
            [call.args[0] for call in sleep.await_args_list],
            [0.5, 1.5, 3.0, 5.0],
        )

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
