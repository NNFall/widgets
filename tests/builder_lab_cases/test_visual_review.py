import asyncio

import pytest
from sqlalchemy import select

from app.models.contracts import (
    ModelRequest,
    ModelResponse,
    ModelRouteAttempt,
    ModelRouteExhausted,
    ModelUsage,
    ProviderCapabilities,
)
from app.models.router import (
    InMemoryModelCallAudit,
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
    SqlModelCallAudit,
)
from app.saas.models import ModelCall
from builder_lab.models import TokenUsage
from builder_lab.visual_critic import VisualCriticResult, VisualCriticRole
from builder_lab.visual_models import (
    NormalizedRegion,
    VisualCategory,
    VisualCritique,
    VisualFinding,
    VisualSeverity,
    VisualVerdict,
)
from builder_lab.visual_review import VisualJudgeError, validate_visual_judgement
from builder_lab.visual_review import (
    GeminiVisualJudge,
    GeminiRepairVerifier,
    RepairVerificationError,
    REPAIR_VERIFICATION_SCHEMA,
    validate_repair_verification,
)
from tests.builder_lab_cases.test_validation import artifact
from tests.saas_cases.test_trial_service import _database


def finding(finding_id, evidence):
    return VisualFinding(
        finding_id=finding_id,
        severity=VisualSeverity.MAJOR,
        category=VisualCategory.RESPONSIVE_INTEGRITY,
        screenshot_id="mobile.after_turn_2",
        evidence=evidence,
        region=NormalizedRegion(
            x=0.68,
            y=0.79,
            width=0.26,
            height=0.12,
            semantic_region="composer",
        ),
        artifact_fields=("css",),
        repair_instruction="Keep the composer fully inside the panel.",
        confidence=0.93,
    )


def result(item):
    return VisualCriticResult(
        critique=VisualCritique(
            verdict=VisualVerdict.REPAIR,
            summary="Strict review completed.",
            findings=(item,),
        ),
        observations=(),
        pixel_proof=None,
        usage=TokenUsage(),
    )


def role_results():
    return {
        VisualCriticRole.CONVERSATION_UX: result(
            finding("ux-edge", "The send control crosses the panel edge.")
        ),
        VisualCriticRole.BRAND_MOTION: result(
            finding(
                "brand-overflow",
                "On mobile the composer action is visibly clipped on the right.",
            )
        ),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: VisualCriticResult(
            critique=VisualCritique(
                verdict=VisualVerdict.PASS,
                summary="No additional blocking issue.",
            ),
            observations=(),
            pixel_proof=None,
            usage=TokenUsage(),
        ),
    }


def judgement_payload(sources):
    return {
        "verdict": "repair",
        "summary": "Two critics independently found the same mobile composer defect.",
        "findings": [
            {
                "finding_id": "judge-1",
                "severity": "major",
                "category": "responsive_integrity",
                "screenshot_id": "mobile.after_turn_2",
                "evidence": "The mobile composer send control is clipped by the panel edge.",
                "region": {
                    "x": 0.68,
                    "y": 0.79,
                    "width": 0.26,
                    "height": 0.12,
                    "semantic_region": "composer",
                },
                "artifact_fields": ["css"],
                "repair_instruction": "Keep the composer fully inside the mobile panel.",
                "confidence": 0.94,
                "sources": sources,
            }
        ],
    }


def test_judge_accepts_semantic_consensus_via_two_source_references():
    judged = validate_visual_judgement(
        judgement_payload(
            [
                {"role": "conversation_ux", "finding_id": "ux-edge"},
                {"role": "brand_motion", "finding_id": "brand-overflow"},
            ]
        ),
        role_results(),
    )

    assert judged.critique.verdict is VisualVerdict.REPAIR
    assert judged.supporting_roles["judge-1"] == (
        VisualCriticRole.CONVERSATION_UX,
        VisualCriticRole.BRAND_MOTION,
    )


def test_judge_rejects_even_a_blocker_when_only_one_role_supports_it():
    payload = judgement_payload(
        [{"role": "conversation_ux", "finding_id": "ux-edge"}]
    )
    payload["findings"][0]["severity"] = "blocker"

    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(payload, role_results())
    assert caught.value.diagnostic == "two distinct critic roles are required"


def test_judge_rejects_fabricated_source_ids():
    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(
            judgement_payload(
                [
                    {"role": "conversation_ux", "finding_id": "missing"},
                    {"role": "brand_motion", "finding_id": "brand-overflow"},
                ]
            ),
            role_results(),
        )
    assert "unknown critic finding" in caught.value.diagnostic


@pytest.mark.asyncio
async def test_routed_visual_judge_reserves_time_for_fallback() -> None:
    class HangingPrimary:
        capabilities = ProviderCapabilities(structured_output=True)

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    class SuccessfulFallback:
        capabilities = ProviderCapabilities(structured_output=True)

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                text="{}",
                parsed=judgement_payload(
                    [
                        {"role": "conversation_ux", "finding_id": "ux-edge"},
                        {"role": "brand_motion", "finding_id": "brand-overflow"},
                    ]
                ),
                usage=ModelUsage(input_tokens=21, output_tokens=8, thinking_tokens=2),
                request_id="fallback-judge",
            )

    fallback = SuccessfulFallback()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": HangingPrimary(), "fallback": fallback},
        policies={
            ("visual_judge", "express"): ModelPolicy(
                prompt_version="visual-judge-v2",
                targets=(
                    ProviderTarget("primary", "gpt-primary", 1, 1),
                    ProviderTarget("fallback", "gemini-fallback", 1, 1),
                ),
            )
        },
        audit=audit,
    )
    judge = GeminiVisualJudge(
        model_router=router,
        routing_mode="express",
        timeout_seconds=0.1,
        routing_timeout_seconds=0.1,
    )

    result = await asyncio.wait_for(judge.judge(role_results=role_results()), timeout=0.5)

    assert result.critique.verdict is VisualVerdict.REPAIR
    assert fallback.calls == 1
    assert [call.status for call in audit.calls] == ["failed", "completed"]
    assert audit.calls[0].error_code == "generation_timeout"


@pytest.mark.asyncio
async def test_routed_visual_judge_semantic_correction_shares_one_deadline() -> None:
    class Router:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        async def generate(self, **kwargs) -> ModelResponse:
            self.timeouts.append(kwargs["timeout_seconds"])
            sources = (
                [{"role": "conversation_ux", "finding_id": "ux-edge"}]
                if len(self.timeouts) == 1
                else [
                    {"role": "conversation_ux", "finding_id": "ux-edge"},
                    {"role": "brand_motion", "finding_id": "brand-overflow"},
                ]
            )
            return ModelResponse(
                text="{}",
                parsed=judgement_payload(sources),
                usage=ModelUsage(input_tokens=5, output_tokens=2),
            )

    router = Router()
    judge = GeminiVisualJudge(
        model_router=router,
        routing_mode="express",
        timeout_seconds=0.2,
        routing_timeout_seconds=0.2,
    )

    result = await judge.judge(role_results=role_results())

    assert result.critique.verdict is VisualVerdict.REPAIR
    assert len(router.timeouts) == 2
    assert 0 < router.timeouts[1] < router.timeouts[0] <= 0.2


@pytest.mark.asyncio
async def test_routed_visual_judge_preserves_terminal_route_usage_and_provenance() -> None:
    route_error = ModelRouteExhausted(
        attempts=(
            ModelRouteAttempt(
                provider="agentrouter",
                model="gpt-5.5",
                outcome="failed",
                latency_ms=100,
                usage=ModelUsage(input_tokens=12, output_tokens=5, thinking_tokens=2),
                cost_microusd=10,
                cost_state="reported",
                error_code="generation_timeout",
            ),
            ModelRouteAttempt(
                provider="gemini",
                model="gemini-fallback",
                outcome="failed",
                latency_ms=80,
                usage=ModelUsage(input_tokens=7, output_tokens=3, thinking_tokens=1),
                cost_microusd=4,
                cost_state="reported",
                error_code="provider_unavailable",
            ),
        ),
        usage=ModelUsage(input_tokens=19, output_tokens=8, thinking_tokens=3),
    )

    class Router:
        def __init__(self) -> None:
            self.timeout = 0.0

        async def generate(self, **kwargs) -> ModelResponse:
            self.timeout = kwargs["timeout_seconds"]
            raise route_error

    router = Router()
    judge = GeminiVisualJudge(
        model_router=router,
        timeout_seconds=0.01,
        routing_timeout_seconds=0.2,
    )

    with pytest.raises(VisualJudgeError) as caught:
        await judge.judge(role_results=role_results())

    assert caught.value.error_code == "route_exhausted"
    assert caught.value.diagnostic == route_error.diagnostic
    assert caught.value.usage == TokenUsage(
        prompt_tokens=19,
        output_tokens=5,
        thinking_tokens=3,
    )
    assert router.timeout > 0.1


def test_repair_verifier_requires_one_explicit_check_per_judge_finding():
    original = finding("judge-1", "The mobile composer is clipped.")
    verified = validate_repair_verification(
        {
            "summary": "The requested mobile correction is present.",
            "checks": [
                {
                    "finding_id": "judge-1",
                    "status": "fixed",
                    "evidence": "The composer now stays within the mobile panel.",
                }
            ],
        },
        (original,),
    )

    assert verified.checks["judge-1"].status == "fixed"


def test_repair_verifier_rejects_missing_or_fabricated_finding_ids():
    original = finding("judge-1", "The mobile composer is clipped.")

    with pytest.raises(RepairVerificationError):
        validate_repair_verification(
            {
                "summary": "Incorrect result.",
                "checks": [
                    {
                        "finding_id": "invented",
                        "status": "fixed",
                        "evidence": "No source.",
                    }
                ],
            },
            (original,),
        )


@pytest.mark.asyncio
async def test_repair_verifier_routes_every_semantic_attempt_as_code_review(
    tmp_path,
) -> None:
    engine, factory, _, run_ids = await _database(tmp_path)
    original = finding("judge-1", "The mobile composer is clipped.")

    class Provider:
        capabilities = ProviderCapabilities(structured_output=True)

        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            assert model == "review-model"
            self.requests.append(request)
            finding_id = "invented" if len(self.requests) == 1 else "judge-1"
            return ModelResponse(
                text="{}",
                parsed={
                    "summary": "Проверка исправления завершена.",
                    "checks": [
                        {
                            "finding_id": finding_id,
                            "status": "fixed",
                            "evidence": "Поле сообщения остаётся внутри панели.",
                        }
                    ],
                },
                usage=ModelUsage(input_tokens=20, output_tokens=5, thinking_tokens=2),
                request_id=f"review-{len(self.requests)}",
            )

    provider = Provider()
    router = ModelRouter(
        providers={"provider": provider},
        policies={
            ("code_review", "express"): ModelPolicy(
                prompt_version="code-review-v1",
                targets=(ProviderTarget("provider", "review-model", 10, 20),),
            )
        },
        audit=SqlModelCallAudit(factory),
    )
    verifier = GeminiRepairVerifier(
        model_router=router,
        routing_mode="express",
        routing_role="code_review",
        run_id=run_ids[0],
        timeout_seconds=5,
    )
    try:
        result = await verifier.verify(
            findings=(original,),
            before=artifact(revision=4),
            after=artifact(revision=4, css=artifact().css + "\n.fixed {}"),
        )

        assert result.unresolved == ()
        assert len(provider.requests) == 2
        assert all(
            request.response_schema == REPAIR_VERIFICATION_SCHEMA
            for request in provider.requests
        )
        assert "Previous verifier response failed local validation" in provider.requests[1].prompt
        async with factory() as database:
            calls = (
                await database.execute(
                    select(ModelCall).where(ModelCall.run_id == run_ids[0]).order_by(ModelCall.attempt)
                )
            ).scalars().all()
        assert [(call.role, call.mode) for call in calls] == [
            ("code_review", "express"),
            ("code_review", "express"),
        ]
        assert [call.request_id for call in calls] == ["review-1", "review-2"]
    finally:
        await engine.dispose()
