from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.base import Base
from app.models.contracts import (
    ModelRequest,
    ModelResponse,
    ModelRouteAttempt,
    ModelRouteExhausted,
    ModelUsage,
    ProviderCapabilities,
)
from app.models.router import (
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
    SqlModelCallAudit,
)
from app.models.structured_generation import RoutedStructuredGenerationBackend
from app.saas.models import ModelCall
from scripts.analyze_reference_site import (
    REFERENCE_ANALYSIS_SCHEMA,
    ReferenceAnalysisError,
    analyze_reference_site,
)
from tests.builder_lab_cases.test_analyze_reference_site import (
    REQUIRED_LABELS,
    evidence_with_manifest,
    valid_analysis,
)
from tests.saas_cases.test_trial_service import _database


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")


@pytest.mark.asyncio
async def test_routed_reference_preserves_terminal_route_usage_and_cost(tmp_path) -> None:
    attempt = ModelRouteAttempt(
        provider="agentrouter",
        model="glm-5.2-reference",
        outcome="failed",
        latency_ms=456,
        usage=ModelUsage(input_tokens=100, output_tokens=20, thinking_tokens=5),
        cost_microusd=12_345,
        cost_state="reported",
        error_code="generation_timeout",
    )
    route_error = ModelRouteExhausted(
        attempts=(attempt,),
        usage=ModelUsage(input_tokens=100, output_tokens=20, thinking_tokens=5),
    )

    class ExhaustedBackend:
        model_name = "routed-model"

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                payload = valid_analysis(REQUIRED_LABELS)
                payload["public_facts"] = []
                return ModelResponse(
                    text="{}",
                    parsed=payload,
                    usage=ModelUsage(
                        input_tokens=30,
                        output_tokens=10,
                        thinking_tokens=2,
                    ),
                )
            raise route_error

    inputs, manifest = evidence_with_manifest(tmp_path)
    backend = ExhaustedBackend()

    with pytest.raises(ReferenceAnalysisError) as caught:
        await analyze_reference_site(
            source_url="https://rawbureau.ru/",
            allowed_hosts={"rawbureau.ru"},
            screenshot_inputs=inputs,
            evidence_root=tmp_path,
            captured_at="2026-07-19T12:10:23.127441+00:00",
            coverage_status="complete",
            capture_manifest=manifest,
            api_key=None,
            structured_backend=backend,
        )

    assert caught.value.error_code == "route_exhausted"
    assert caught.value.usage == ModelUsage(
        input_tokens=130,
        output_tokens=30,
        thinking_tokens=7,
    )
    assert caught.value.diagnostic == route_error.diagnostic
    assert '"cost_microusd":12345' in caught.value.diagnostic
    assert not any(
        provider in caught.value.public_message
        for provider in ("Gemini", "GPT", "GLM", "AgentRouter")
    )
    assert backend.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("exception_type", "error_code"),
    [
        (ValueError, "invalid_semantic_output"),
        (TimeoutError, "analysis_timeout"),
        (RuntimeError, "analysis_unavailable"),
    ],
)
async def test_routed_reference_errors_are_provider_neutral(
    tmp_path,
    exception_type,
    error_code,
) -> None:
    class FailingBackend:
        model_name = "routed-model"

        async def generate(self, request: ModelRequest) -> ModelResponse:
            raise exception_type("private Gemini GPT GLM AgentRouter failure")

    inputs, manifest = evidence_with_manifest(tmp_path)

    with pytest.raises(ReferenceAnalysisError) as caught:
        await analyze_reference_site(
            source_url="https://rawbureau.ru/",
            allowed_hosts={"rawbureau.ru"},
            screenshot_inputs=inputs,
            evidence_root=tmp_path,
            captured_at="2026-07-19T12:10:23.127441+00:00",
            coverage_status="complete",
            capture_manifest=manifest,
            api_key=None,
            structured_backend=FailingBackend(),
        )

    assert caught.value.error_code == error_code
    assert not any(
        provider in caught.value.public_message
        for provider in ("Gemini", "GPT", "GLM", "AgentRouter")
    )


@pytest.mark.asyncio
async def test_routed_reference_backend_forwards_analyzer_deadline() -> None:
    class Router:
        def __init__(self) -> None:
            self.timeout_seconds = None

        async def generate(self, **kwargs):
            self.timeout_seconds = kwargs["timeout_seconds"]
            return ModelResponse(text="{}", parsed={})

    router = Router()
    backend = RoutedStructuredGenerationBackend(
        router=router,
        role="reference_analyst",
        mode="express",
        run_id=__import__("uuid").uuid4(),
        timeout_seconds=17,
    )

    await backend.generate(ModelRequest(prompt="Analyze"))

    assert router.timeout_seconds == 17


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "database_url",
    [
        None,
        pytest.param(
            POSTGRES_URL,
            marks=pytest.mark.skipif(
                not POSTGRES_URL,
                reason="KAIGO_TEST_POSTGRES_URL is not configured",
            ),
            id="postgresql",
        ),
    ],
)
async def test_reference_analysis_routes_images_schema_retries_and_sql_audit(
    tmp_path,
    database_url,
) -> None:
    engine, factory, _, run_ids = await _database(
        tmp_path,
        database_url=database_url,
    )

    class Provider:
        capabilities = ProviderCapabilities(images=True, structured_output=True)

        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            assert model == "glm-5.2-reference"
            self.requests.append(request)
            payload = valid_analysis(REQUIRED_LABELS)
            if len(self.requests) == 1:
                payload["public_facts"] = []
            return ModelResponse(
                text="{}",
                parsed=payload,
                usage=ModelUsage(
                    input_tokens=100,
                    output_tokens=20,
                    thinking_tokens=5,
                ),
                request_id=f"reference-{len(self.requests)}",
            )

    provider = Provider()
    router = ModelRouter(
        providers={"agentrouter": provider},
        policies={
            ("reference_analyst", "express"): ModelPolicy(
                prompt_version="reference-v2",
                targets=(
                    ProviderTarget(
                        "agentrouter",
                        "glm-5.2-reference",
                        6_000_000,
                        6_000_000,
                    ),
                ),
            )
        },
        audit=SqlModelCallAudit(factory),
    )
    backend = RoutedStructuredGenerationBackend(
        router=router,
        role="reference_analyst",
        mode="express",
        run_id=run_ids[0],
    )
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, manifest = evidence_with_manifest(root)
            result = await analyze_reference_site(
                source_url="https://rawbureau.ru/",
                allowed_hosts={"rawbureau.ru"},
                screenshot_inputs=inputs,
                evidence_root=root,
                captured_at="2026-07-19T12:10:23.127441+00:00",
                coverage_status="complete",
                capture_manifest=manifest,
                api_key=None,
                structured_backend=backend,
            )

        assert len(provider.requests) == 2
        assert all(
            request.response_schema == REFERENCE_ANALYSIS_SCHEMA
            for request in provider.requests
        )
        assert all(len(request.images) == 6 for request in provider.requests)
        assert provider.requests[1].metadata["image_labels"] == REQUIRED_LABELS
        assert "public_facts is outside its item budget" in provider.requests[1].prompt
        assert result["provenance"]["attempt_count"] == 2
        assert result["provenance"]["usage"] == {
            "prompt_tokens": 200,
            "output_tokens": 30,
            "thinking_tokens": 10,
            "total_tokens": 240,
        }

        async with factory() as database:
            calls = (
                await database.execute(select(ModelCall).order_by(ModelCall.created_at, ModelCall.id))
            ).scalars().all()
        assert len(calls) == 2
        assert all(call.run_id == run_ids[0] for call in calls)
        assert all((call.role, call.mode) == ("reference_analyst", "express") for call in calls)
        assert all((call.provider, call.model) == ("agentrouter", "glm-5.2-reference") for call in calls)
        assert all(
            (call.input_tokens, call.output_tokens, call.thinking_tokens)
            == (100, 20, 5)
            for call in calls
        )
        assert all(call.cost_microusd == 720 for call in calls)
    finally:
        if database_url:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
