import asyncio
from datetime import UTC, datetime
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

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
from app.models.lineage import ModelInvocationContext
from app.models.router import (
    InMemoryModelCallAudit,
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
    SqlModelCallAudit,
)
from app.saas.models import GenerationStageAttempt, ModelCall
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
from tests.builder_lab_cases.test_assistant_persona import _persona
from tests.builder_lab_cases.test_visual_critic import report as browser_report
from tests.builder_lab_cases.test_validation import artifact
from tests.saas_cases.test_trial_service import _database


def finding(
    finding_id,
    evidence,
    *,
    severity=VisualSeverity.MAJOR,
    confidence=0.93,
):
    return VisualFinding(
        finding_id=finding_id,
        severity=severity,
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
        confidence=confidence,
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
                "issue_type": "responsive_integrity",
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


def test_deterministic_violation_is_accepted_from_one_critic_with_server_fact():
    item = finding("close-visibility", "The close control is not visible.")
    source = replace(
        result(item),
        finding_issue_types={"close-visibility": "close_control_visibility"},
    )
    roles = {
        VisualCriticRole.CONVERSATION_UX: source,
        VisualCriticRole.BRAND_MOTION: VisualCriticResult(
            critique=VisualCritique(
                verdict=VisualVerdict.PASS,
                summary="No additional blocking issue.",
            ),
            observations=(),
            pixel_proof=None,
            usage=TokenUsage(),
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
    payload = judgement_payload(
        [{"role": "conversation_ux", "finding_id": "close-visibility"}]
    )
    payload["findings"][0]["issue_type"] = "close_control_visibility"
    judged = validate_visual_judgement(
        payload,
        roles,
        deterministic_facts={"close_control_visibility": True},
    )
    assert judged.supporting_roles["judge-1"] == (VisualCriticRole.CONVERSATION_UX,)


def test_deterministic_violation_still_requires_one_critic_source():
    payload = judgement_payload([])
    payload["findings"][0]["issue_type"] = "close_control_visibility"

    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(
            payload,
            role_results(),
            deterministic_facts={"close_control_visibility": True},
        )

    assert caught.value.diagnostic == "at least one critic source is required"


def test_judge_rejects_final_finding_from_unsupplied_screenshot():
    payload = judgement_payload(
        [
            {"role": "conversation_ux", "finding_id": "ux-edge"},
            {"role": "brand_motion", "finding_id": "brand-overflow"},
        ]
    )
    payload["findings"][0]["screenshot_id"] = "studio.fake"

    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(
            payload,
            role_results(),
            expected_screenshot_ids={"mobile.after_turn_2"},
        )

    assert caught.value.diagnostic == "unknown judge screenshot: 'studio.fake'"


def test_judge_enforces_finding_and_source_bounds_like_provider_schema():
    too_many_findings = judgement_payload(
        [
            {"role": "conversation_ux", "finding_id": "ux-edge"},
            {"role": "brand_motion", "finding_id": "brand-overflow"},
        ]
    )
    too_many_findings["findings"] = [
        dict(too_many_findings["findings"][0]) for _ in range(7)
    ]
    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(too_many_findings, role_results())
    assert caught.value.diagnostic == "findings exceed maxItems 6"

    too_many_sources = judgement_payload(
        [
            {"role": "conversation_ux", "finding_id": "ux-edge"},
            {"role": "brand_motion", "finding_id": "brand-overflow"},
            {"role": "adversarial_customer", "finding_id": "customer-minor"},
            {"role": "conversation_ux", "finding_id": "ux-edge"},
        ]
    )
    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(too_many_sources, role_results())
    assert caught.value.diagnostic == "sources exceed maxItems 3"


def test_static_judge_rejects_javascript_repair_without_runtime_fact():
    payload = judgement_payload(
        [
            {"role": "conversation_ux", "finding_id": "ux-edge"},
            {"role": "brand_motion", "finding_id": "brand-overflow"},
        ]
    )
    payload["findings"][0]["artifact_fields"] = ["javascript"]

    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(payload, role_results())

    assert caught.value.diagnostic == (
        "javascript artifact field requires server-owned pattern_runtime_fidelity fact"
    )


def test_runtime_direct_judge_rejects_studio_reference_and_motion_claims():
    payload = judgement_payload(
        [
            {"role": "conversation_ux", "finding_id": "ux-edge"},
            {"role": "brand_motion", "finding_id": "brand-overflow"},
        ]
    )
    payload["findings"][0]["evidence"] = (
        "Studio reference animation timing is incorrect."
    )

    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(
            payload,
            role_results(),
            expected_screenshot_ids={"mobile.after_turn_2"},
        )

    assert caught.value.diagnostic == (
        "unsupported runtime_direct evidence scope: studio,reference,motion"
    )


def test_runtime_direct_judge_rejects_unsupported_scope_in_summary():
    payload = judgement_payload(
        [
            {"role": "conversation_ux", "finding_id": "ux-edge"},
            {"role": "brand_motion", "finding_id": "brand-overflow"},
        ]
    )
    payload["summary"] = "Studio reference animation review is required."

    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(
            payload,
            role_results(),
            expected_screenshot_ids={"mobile.after_turn_2"},
        )

    assert caught.value.diagnostic == (
        "unsupported runtime_direct evidence scope: studio,reference,motion"
    )


@pytest.mark.asyncio
async def test_live_visual_judge_rejects_runtime_scope_claims() -> None:
    payload = judgement_payload(
        [
            {"role": "conversation_ux", "finding_id": "ux-edge"},
            {"role": "brand_motion", "finding_id": "brand-overflow"},
        ]
    )
    payload["findings"][0]["repair_instruction"] = (
        "Fix the Studio reference animation timing."
    )

    class Models:
        async def generate_content(self, **kwargs):
            return SimpleNamespace(
                parsed=payload,
                text="{}",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=5,
                    candidates_token_count=2,
                    thoughts_token_count=0,
                ),
            )

    client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    with pytest.raises(VisualJudgeError) as caught:
        await GeminiVisualJudge(client=client).judge(
            role_results=role_results(),
            audit=browser_report(),
            brief="",
            art_direction="Direction",
        )

    assert "unsupported runtime_direct evidence scope" in (caught.value.diagnostic or "")


def test_subjective_one_critic_finding_is_still_rejected():
    payload = judgement_payload(
        [{"role": "conversation_ux", "finding_id": "ux-edge"}]
    )
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
async def test_visual_judge_receives_full_reports_and_five_visual_frames() -> None:
    mixed_results = {
        VisualCriticRole.CONVERSATION_UX: VisualCriticResult(
            critique=VisualCritique(
                verdict=VisualVerdict.REPAIR,
                summary="One eligible and one minor finding.",
                findings=(
                    finding("ux-major", "The composer is clipped."),
                    finding(
                        "ux-minor",
                        "A decorative edge is slightly uneven.",
                        severity=VisualSeverity.MINOR,
                    ),
                ),
            ),
            observations=(),
            pixel_proof=None,
            usage=TokenUsage(),
        ),
        VisualCriticRole.BRAND_MOTION: VisualCriticResult(
            critique=VisualCritique(
                verdict=VisualVerdict.REPAIR,
                summary="One eligible and one low-confidence finding.",
                findings=(
                    finding("brand-major", "The same composer edge is clipped."),
                    finding(
                        "brand-low-confidence",
                        "The launcher may be too quiet.",
                        confidence=0.64,
                    ),
                ),
            ),
            observations=(),
            pixel_proof=None,
            usage=TokenUsage(),
        ),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: VisualCriticResult(
            critique=VisualCritique(
                verdict=VisualVerdict.PASS,
                summary="Only a non-blocking observation.",
                findings=(
                    finding(
                        "customer-minor",
                        "The avatar could be more expressive.",
                        severity=VisualSeverity.MINOR,
                    ),
                ),
            ),
            observations=(),
            pixel_proof=None,
            usage=TokenUsage(),
        ),
    }

    class Router:
        def __init__(self) -> None:
            self.request = None

        async def generate(self, **kwargs) -> ModelResponse:
            self.request = kwargs["request"]
            return ModelResponse(
                text="{}",
                parsed=judgement_payload(
                    [
                        {"role": "conversation_ux", "finding_id": "ux-major"},
                        {"role": "brand_motion", "finding_id": "brand-major"},
                    ]
                ),
                usage=ModelUsage(input_tokens=5, output_tokens=2),
            )

    router = Router()
    judge = GeminiVisualJudge(
        model_router=router,
        routing_mode="express",
        timeout_seconds=0.2,
        routing_timeout_seconds=0.2,
    )

    audit_report = browser_report()
    persona = _persona()
    result = await judge.judge(
        role_results=mixed_results,
        audit=audit_report,
        assistant_persona=persona,
        brief="Короткое пожелание клиента",
        art_direction="Финально выбранное визуальное направление",
    )

    assert result.critique.verdict is VisualVerdict.REPAIR
    prompt = router.request.prompt
    role_payload = prompt.split("UNTRUSTED CRITIC RESULTS JSON:\n", 1)[1]
    payload = __import__("json").loads(role_payload)
    assert {
        role: [item["finding_id"] for item in role_result["eligible_findings"]]
        for role, role_result in payload.items()
    } == {
        "conversation_ux": ["ux-major"],
        "brand_motion": ["brand-major"],
        "adversarial_customer": [],
    }
    assert {
        role: [item["finding_id"] for item in role_result["all_findings"]]
        for role, role_result in payload.items()
    } == {
        "conversation_ux": ["ux-major", "ux-minor"],
        "brand_motion": ["brand-major", "brand-low-confidence"],
        "adversarial_customer": ["customer-minor"],
    }
    assert "confidence >= 0.65" in prompt
    assert "primary visual contract" in prompt.lower()
    assert "secondary" in prompt.lower()
    assert "score those three criteria from 0 to 10" in prompt.lower()
    assert "Финально выбранное визуальное направление" in prompt
    assert "Короткое пожелание клиента" in prompt
    assert len(router.request.images) == 5
    system_prompt = prompt.split("PRIMARY VISUAL CONTRACT", 1)[0]
    untrusted_prompt = prompt.split("PRIMARY VISUAL CONTRACT", 1)[1]
    assert "TRUSTED_ASSISTANT_PERSONA_POLICY" in system_prompt
    assert "Never change opening_line or behavior_rules" in system_prompt
    assert persona.display_name not in system_prompt
    assert persona.opening_line not in system_prompt
    assert persona.behavior_rules[0] not in system_prompt
    assert persona.decision_rationale not in prompt
    assert "UNTRUSTED_ASSISTANT_PERSONA_DATA_JSON" in untrusted_prompt
    assert persona.display_name in untrusted_prompt
    assert persona.opening_line in untrusted_prompt
    assert persona.behavior_rules[0] in untrusted_prompt
    assert router.request.images == tuple(
        audit_report.screenshot(state).data
        for state in (
            "desktop.closed",
            "desktop.open_initial",
            "desktop.after_turn_2",
            "mobile.open_initial",
            "mobile.after_turn_2",
        )
    )


@pytest.mark.asyncio
async def test_visual_judge_accepts_empty_optional_customer_brief() -> None:
    class Models:
        async def generate_content(self, **kwargs):
            return SimpleNamespace(
                parsed=judgement_payload(
                    [
                        {"role": "conversation_ux", "finding_id": "ux-edge"},
                        {"role": "brand_motion", "finding_id": "brand-overflow"},
                    ]
                ),
                text="{}",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=5,
                    candidates_token_count=2,
                    thoughts_token_count=0,
                ),
            )

    client = SimpleNamespace(aio=SimpleNamespace(models=Models()))
    judged = await GeminiVisualJudge(client=client).judge(
        role_results=role_results(),
        audit=browser_report(),
        brief="",
        art_direction="Direction",
    )

    assert judged.critique.verdict is VisualVerdict.REPAIR


def test_judge_rejects_source_that_is_not_eligible_for_quorum():
    results = role_results()
    weak = finding(
        "weak-source",
        "The launcher may be too quiet.",
        confidence=0.64,
    )
    results[VisualCriticRole.ADVERSARIAL_CUSTOMER] = VisualCriticResult(
        critique=VisualCritique(
            verdict=VisualVerdict.PASS,
            summary="No confident issue.",
            findings=(weak,),
        ),
        observations=(),
        pixel_proof=None,
        usage=TokenUsage(),
    )

    with pytest.raises(VisualJudgeError) as caught:
        validate_visual_judgement(
            judgement_payload(
                [
                    {"role": "conversation_ux", "finding_id": "ux-edge"},
                    {
                        "role": "adversarial_customer",
                        "finding_id": "weak-source",
                    },
                ]
            ),
            results,
        )

    assert caught.value.diagnostic == (
        "ineligible critic finding: adversarial_customer:weak-source"
    )


@pytest.mark.asyncio
async def test_routed_visual_judge_reserves_time_for_fallback() -> None:
    class HangingPrimary:
        capabilities = ProviderCapabilities(images=True, structured_output=True)

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    class SuccessfulFallback:
        capabilities = ProviderCapabilities(images=True, structured_output=True)

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

    result = await asyncio.wait_for(
        judge.judge(
            role_results=role_results(),
            audit=browser_report(),
            brief="Brief",
            art_direction="Direction",
        ),
        timeout=0.5,
    )

    assert result.critique.verdict is VisualVerdict.REPAIR
    assert fallback.calls == 1
    assert [call.status for call in audit.calls] == ["timed_out", "completed"]
    assert audit.calls[0].error_code == "generation_timeout"


@pytest.mark.asyncio
async def test_routed_visual_judge_semantic_correction_shares_one_deadline() -> None:
    class Router:
        def __init__(self) -> None:
            self.timeouts: list[float] = []
            self.contexts: list[ModelInvocationContext] = []

        async def generate(self, **kwargs) -> ModelResponse:
            self.timeouts.append(kwargs["timeout_seconds"])
            self.contexts.append(kwargs["context"])
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
    stage_attempt_id = uuid4()
    judge = GeminiVisualJudge(
        model_router=router,
        routing_mode="express",
        timeout_seconds=0.2,
        routing_timeout_seconds=0.2,
        invocation_context=ModelInvocationContext(
            stage_attempt_id=stage_attempt_id,
            stage="foundation",
            operation="visual_judge",
            candidate_id="candidate-2",
            persona="brand_motion",
        ),
    )

    result = await judge.judge(
        role_results=role_results(),
        audit=browser_report(),
        brief="Brief",
        art_direction="Direction",
    )

    assert result.critique.verdict is VisualVerdict.REPAIR
    assert len(router.timeouts) == 2
    assert 0 < router.timeouts[1] < router.timeouts[0] <= 0.2
    assert [context.operation for context in router.contexts] == [
        "visual_judge",
        "visual_judge",
    ]
    assert [context.semantic_attempt for context in router.contexts] == [1, 2]
    assert all(context.stage_attempt_id == stage_attempt_id for context in router.contexts)
    assert all(context.stage == "foundation" for context in router.contexts)
    assert all(context.candidate_id == "candidate-2" for context in router.contexts)
    assert all(context.persona == "brand_motion" for context in router.contexts)


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
        await judge.judge(
            role_results=role_results(),
            audit=browser_report(),
            brief="Brief",
            art_direction="Direction",
        )

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
    stage_attempt_id = uuid4()
    async with factory() as database, database.begin():
        database.add(
            GenerationStageAttempt(
                id=stage_attempt_id,
                run_id=run_ids[0],
                stage="foundation",
                ordinal=1,
                status="running",
                started_at=datetime.now(UTC),
            )
        )

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
        invocation_context=ModelInvocationContext(
            stage_attempt_id=stage_attempt_id,
            stage="foundation",
            operation="repair_verification",
            candidate_id="candidate-2",
            persona="code_reviewer",
        ),
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
        assert [call.operation for call in calls] == [
            "repair_verification",
            "repair_verification",
        ]
        assert [call.semantic_attempt for call in calls] == [1, 2]
        assert all(call.stage_attempt_id == stage_attempt_id for call in calls)
        assert all(call.candidate_id == "candidate-2" for call in calls)
        assert all(call.persona == "code_reviewer" for call in calls)
        assert len({call.logical_invocation_id for call in calls}) == 2
    finally:
        await engine.dispose()
