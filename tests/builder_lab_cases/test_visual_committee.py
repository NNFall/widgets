import asyncio
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


def committee(critics):
    return VisualCriticCommittee(
        {
            role: (lambda critic=critic: critic)
            for role, critic in critics.items()
        }
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
    visual_committee = committee(critics)

    combined = await visual_committee.critique(
        audit=types.SimpleNamespace(),
        brief="Brief",
        art_direction="Direction",
    )
    await visual_committee.aclose()

    assert combined.critique.verdict is VisualVerdict.PASS
    assert combined.usage.prompt_tokens == 36
    assert set(combined.role_results) == {
        VisualCriticRole.CONVERSATION_UX,
        VisualCriticRole.BRAND_MOTION,
    }
    assert set(combined.role_failures) == {
        VisualCriticRole.ADVERSARIAL_CUSTOMER,
    }
    assert barrier.arrived == 3
    assert all(critic.closed for critic in critics.values())


@pytest.mark.asyncio
async def test_matching_major_from_two_roles_triggers_one_repair_finding():
    critics = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(
            result(finding("conversation-major"))
        ),
        VisualCriticRole.BRAND_MOTION: FakeCritic(
            result(finding("brand-major"))
        ),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(result()),
    }

    combined = await committee(critics).critique(
        audit=types.SimpleNamespace(),
        brief="Brief",
        art_direction="Direction",
    )

    assert combined.critique.verdict is VisualVerdict.REPAIR
    assert len(combined.critique.findings) == 1
    assert combined.critique.findings[0].finding_id == "committee-1"


@pytest.mark.asyncio
async def test_single_major_does_not_block_but_single_blocker_does():
    one_major = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(result(finding("major"))),
        VisualCriticRole.BRAND_MOTION: FakeCritic(result()),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(result()),
    }
    major_result = await committee(one_major).critique(
        audit=types.SimpleNamespace(),
        brief="Brief",
        art_direction="Direction",
    )
    assert major_result.critique.verdict is VisualVerdict.PASS

    one_blocker = {
        VisualCriticRole.CONVERSATION_UX: FakeCritic(result()),
        VisualCriticRole.BRAND_MOTION: FakeCritic(result()),
        VisualCriticRole.ADVERSARIAL_CUSTOMER: FakeCritic(
            result(finding("blocker", severity=VisualSeverity.BLOCKER))
        ),
    }
    blocker_result = await committee(one_blocker).critique(
        audit=types.SimpleNamespace(),
        brief="Brief",
        art_direction="Direction",
    )
    assert blocker_result.critique.verdict is VisualVerdict.REPAIR
    assert len(blocker_result.critique.findings) == 1


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
