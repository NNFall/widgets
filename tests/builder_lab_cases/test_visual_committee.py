import asyncio
import json
import types

import pytest

from builder_lab.models import TokenUsage
from builder_lab.visual_committee import (
    VisualCommitteeError,
    VisualCriticCommittee,
)
from builder_lab.visual_critic import (
    VisualCriticError,
    VisualCriticResult,
    VisualCriticRole,
)
from builder_lab.visual_review import VisualJudgeError, VisualJudgeResult
from builder_lab.visual_models import (
    NormalizedRegion,
    VisualCategory,
    VisualCritique,
    VisualFinding,
    VisualSeverity,
    VisualVerdict,
)


def finding(
    finding_id: str,
    *,
    severity: VisualSeverity = VisualSeverity.MAJOR,
    role_region: str = "composer",
) -> VisualFinding:
    return VisualFinding(
        finding_id=finding_id,
        severity=severity,
        category=VisualCategory.RESPONSIVE_INTEGRITY,
        screenshot_id="mobile.after_turn_2",
        evidence="The composer send control crosses the visible right panel edge.",
        region=NormalizedRegion(
            x=0.72,
            y=0.82,
            width=0.18,
            height=0.1,
            semantic_region=role_region,
        ),
        artifact_fields=("css",),
        repair_instruction="Keep the composer inside the panel edge.",
        confidence=0.94,
    )


def critique(*findings: VisualFinding) -> VisualCritique:
    items = tuple(findings)
    repair = any(
        item.severity in {VisualSeverity.BLOCKER, VisualSeverity.MAJOR}
        and item.confidence >= 0.75
        for item in items
    )
    return VisualCritique(
        verdict=VisualVerdict.REPAIR if repair else VisualVerdict.PASS,
        summary="Independent visual role completed.",
        findings=items,
    )


def result(*findings: VisualFinding, tokens: int = 10) -> VisualCriticResult:
    return VisualCriticResult(
        critique=critique(*findings),
        observations=(),
        pixel_proof=None,
        usage=TokenUsage(prompt_tokens=tokens),
    )


class FakeCritic:
    def __init__(self, response=None, *, error=None, barrier=None):
        self.response = response
        self.error = error
        self.barrier = barrier
        self.calls = []
        self.closed = False

    async def critique(self, **kwargs):
        self.calls.append(kwargs)
        if self.barrier is not None:
            await self.barrier.arrive()
        if self.error is not None:
            raise self.error
        return self.response

    async def aclose(self):
        self.closed = True


class FakeJudge:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.closed = False

    async def judge(self, *, role_results):
        self.calls.append(role_results)
        return self.response

    async def aclose(self):
        self.closed = True


class Barrier:
    def __init__(self, parties):
        self.parties = parties
        self.arrived = 0
        self.ready = asyncio.Event()

    async def arrive(self):
        self.arrived += 1
        if self.arrived == self.parties:
            self.ready.set()
        await asyncio.wait_for(self.ready.wait(), timeout=0.2)


def judged_result(*findings, tokens=7, supporting_roles=None):
    return VisualJudgeResult(
        critique=critique(*findings),
        supporting_roles=supporting_roles or {},
        usage=TokenUsage(prompt_tokens=tokens),
    )


def committee(critics, judge=None):
    resolved_judge = judge or FakeJudge(judged_result())
    return VisualCriticCommittee(
        {
            role: (lambda critic=critic: critic)
            for role, critic in critics.items()
        },
        judge_factory=lambda: resolved_judge,
    )


@pytest.mark.asyncio
async def test_two_valid_roles_form_quorum_and_all_roles_run_in_parallel():
    barrier = Barrier(3)
    critics = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(result(tokens=11), barrier=barrier),
        VisualCriticRole.BRAND_MOTION: FakeCritic(result(tokens=12), barrier=barrier),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(
            error=VisualCriticError(
                "visual_critic_unavailable",
                "unavailable",
                usage=TokenUsage(prompt_tokens=13),
            ),
            barrier=barrier,
        ),
    }
    judge = FakeJudge(judged_result(tokens=17))
    visual_committee = committee(critics, judge)

    combined = await visual_committee.critique(
        audit=types.SimpleNamespace(),
        brief="Brief",
        art_direction="Direction",
    )
    await visual_committee.aclose()

    assert combined.critique.verdict is VisualVerdict.PASS
    assert combined.usage.prompt_tokens == 53
    assert set(combined.role_results) == {
        VisualCriticRole.CONVERSATION_UX,
        VisualCriticRole.BRAND_MOTION,
    }
    assert set(combined.role_failures) == {
        VisualCriticRole.ADVERSARIAL_CUSTOMER,
    }
    assert barrier.arrived == 3
    assert all(critic.closed for critic in critics.values())
    assert judge.closed
    assert len(judge.calls) == 1


@pytest.mark.asyncio
async def test_ai_judge_semantically_merges_differently_worded_findings():
    accepted = finding("judge-1")
    judge = FakeJudge(
        judged_result(
            accepted,
            supporting_roles={
                "judge-1": (
                    VisualCriticRole.CONVERSATION_UX,
                    VisualCriticRole.BRAND_MOTION,
                )
            },
        )
    )
    critics = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(
            result(finding("conversation-major"))
        ),
        VisualCriticRole.BRAND_MOTION: FakeCritic(
            result(finding("brand-major", role_region="panel"))
        ),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(result()),
    }

    combined = await committee(critics, judge).critique(
        audit=types.SimpleNamespace(),
        brief="Brief",
        art_direction="Direction",
    )

    assert combined.critique.verdict is VisualVerdict.REPAIR
    assert len(combined.critique.findings) == 1
    assert combined.critique.findings[0].finding_id == "judge-1"
    assert combined.supporting_roles["judge-1"] == (
        VisualCriticRole.CONVERSATION_UX,
        VisualCriticRole.BRAND_MOTION,
    )


@pytest.mark.asyncio
async def test_single_blocker_does_not_bypass_two_critic_consensus():
    critics = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(result(finding("major"))),
        VisualCriticRole.BRAND_MOTION: FakeCritic(result()),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(
            result(finding("blocker", severity=VisualSeverity.BLOCKER))
        ),
    }
    judge = FakeJudge(judged_result())

    combined = await committee(critics, judge).critique(
        audit=types.SimpleNamespace(),
        brief="Brief",
        art_direction="Direction",
    )
    assert combined.critique.verdict is VisualVerdict.PASS
    assert combined.critique.findings == ()


@pytest.mark.asyncio
async def test_only_one_valid_role_is_inconclusive_not_visual_quality_failure():
    failure = VisualCriticError(
        "visual_evidence_unproven",
        "invalid evidence",
        usage=TokenUsage(prompt_tokens=5),
    )
    critics = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(result(tokens=7)),
        VisualCriticRole.BRAND_MOTION: FakeCritic(error=failure),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(error=failure),
    }

    with pytest.raises(VisualCommitteeError) as caught:
        await committee(critics).critique(
            audit=types.SimpleNamespace(),
            brief="Brief",
            art_direction="Direction",
        )

    assert caught.value.error_code == "visual_review_inconclusive"
    assert caught.value.usage.prompt_tokens == 17


@pytest.mark.asyncio
async def test_terminal_critic_route_exhaustion_crosses_committee_when_quorum_is_lost():
    diagnostic = (
        '{"terminal_reason":"all_generation_timeout",'
        '"route_attempts":[{"cost_microusd":12345}]}'
    )
    terminal = VisualCriticError(
        "route_exhausted",
        "route failed",
        diagnostic=diagnostic,
        usage=TokenUsage(prompt_tokens=100, output_tokens=20),
    )
    transient = VisualCriticError(
        "visual_critic_unavailable",
        "critic unavailable",
        usage=TokenUsage(prompt_tokens=5),
    )
    critics = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(result(tokens=7)),
        VisualCriticRole.BRAND_MOTION: FakeCritic(error=terminal),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(error=transient),
    }

    with pytest.raises(VisualCommitteeError) as caught:
        await committee(critics).critique(
            audit=types.SimpleNamespace(),
            brief="Brief",
            art_direction="Direction",
        )

    assert caught.value.error_code == "route_exhausted"
    assert caught.value.usage == TokenUsage(prompt_tokens=112, output_tokens=20)
    aggregated = json.loads(caught.value.diagnostic)
    assert aggregated["terminal_reason"] == "committee_terminal_failures"
    assert aggregated["role_failures"] == [
        {
            "role": "brand_motion",
            "error_code": "route_exhausted",
            "route": {
                "terminal_reason": "all_generation_timeout",
                "route_attempts": [{"usage": {}, "cost_microusd": 12345}],
            },
        }
    ]


@pytest.mark.asyncio
async def test_mixed_terminal_critic_failures_are_order_independent_and_keep_each_cost():
    invalid = VisualCriticError(
        "invalid_response",
        "invalid output",
        diagnostic=(
            '{"terminal_reason":"all_invalid_response",'
            '"route_attempts":[{"provider":"primary","model":"model-a",'
            '"outcome":"failed","latency_ms":1,"usage":{"input_tokens":9,'
            '"output_tokens":3,"thinking_tokens":0},"cost_microusd":111,'
            '"cost_state":"estimated","error_code":"invalid_response"}]}'
        ),
        usage=TokenUsage(prompt_tokens=9, output_tokens=3),
    )
    exhausted = VisualCriticError(
        "route_exhausted",
        "route exhausted",
        diagnostic=(
            '{"terminal_reason":"all_generation_timeout",'
            '"route_attempts":[{"provider":"fallback","model":"model-b",'
            '"outcome":"failed","latency_ms":2,"usage":{"input_tokens":100,'
            '"output_tokens":20,"thinking_tokens":0},"cost_microusd":222,'
            '"cost_state":"reported","error_code":"generation_timeout"}]}'
        ),
        usage=TokenUsage(prompt_tokens=100, output_tokens=20),
    )
    critics = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(error=invalid),
        VisualCriticRole.BRAND_MOTION: FakeCritic(error=exhausted),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(result(tokens=7)),
    }

    with pytest.raises(VisualCommitteeError) as caught:
        await committee(critics).critique(
            audit=types.SimpleNamespace(),
            brief="Brief",
            art_direction="Direction",
        )

    assert caught.value.error_code == "route_exhausted"
    assert caught.value.usage == TokenUsage(prompt_tokens=116, output_tokens=23)
    diagnostic = json.loads(caught.value.diagnostic)
    assert diagnostic["terminal_reason"] == "committee_terminal_failures"
    assert [item["error_code"] for item in diagnostic["role_failures"]] == [
        "invalid_response",
        "route_exhausted",
    ]
    assert [
        item["route"]["route_attempts"][0]["cost_microusd"]
        for item in diagnostic["role_failures"]
    ] == [111, 222]


@pytest.mark.asyncio
async def test_terminal_route_exhaustion_crosses_committee_with_usage_and_provenance():
    class TerminalJudge(FakeJudge):
        async def judge(self, *, role_results):
            self.calls.append(role_results)
            raise VisualJudgeError(
                "route_exhausted",
                "route failed",
                diagnostic='{"terminal_reason":"mixed_provider_failures"}',
                usage=TokenUsage(prompt_tokens=11, output_tokens=4, thinking_tokens=2),
            )

    critics = {
        role: FakeCritic(result(tokens=10))
        for role in VisualCriticRole
    }
    judge = TerminalJudge(judged_result())

    with pytest.raises(VisualCommitteeError) as caught:
        await committee(critics, judge).critique(
            audit=types.SimpleNamespace(),
            brief="Brief",
            art_direction="Direction",
        )

    assert caught.value.error_code == "route_exhausted"
    assert caught.value.usage == TokenUsage(
        prompt_tokens=41,
        output_tokens=4,
        thinking_tokens=2,
    )
    assert caught.value.diagnostic == '{"terminal_reason":"mixed_provider_failures"}'
