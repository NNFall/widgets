import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from builder_lab.browser_audit import BrowserAuditError
from builder_lab.engines.base import BuilderEngineError, EngineResult
from builder_lab.experiment_review import (
    ExperimentReview,
    ExperimentVisualQualityError,
)
from builder_lab.models import BuilderRequest, EngineName, TokenUsage
from builder_lab.strict_visual_critic import (
    StrictVisualCriticError,
    StrictVisualCriticResult,
)
from builder_lab.strict_visual_models import (
    STRICT_VISUAL_DIMENSIONS,
    StrictVisualAssessment,
    StrictVisualCritique,
    StrictVisualDimension,
    StrictVisualFinding,
    StrictVisualObservation,
    StrictVisualRevisionAction,
    StrictVisualVerdict,
)
from builder_lab.visual_models import NormalizedRegion
from tests.builder_lab_cases.test_validation import artifact


def visual_critique(*, repair: bool) -> StrictVisualCritique:
    findings = ()
    actions = ()
    values = []
    for dimension in STRICT_VISUAL_DIMENSIONS:
        score = (
            3
            if repair and dimension is StrictVisualDimension.CONVERSATION_CLARITY
            else 5
        )
        values.append(
            StrictVisualAssessment(
                dimension=dimension,
                score=score,
                confidence=0.94,
                finding_ids=("conversation-flat",) if score == 3 else (),
            )
        )
    if repair:
        findings = (
            StrictVisualFinding(
                finding_id="conversation-flat",
                dimension=StrictVisualDimension.CONVERSATION_CLARITY,
                screenshot_id="desktop.after_turn_2",
                evidence=(
                    "The desktop after-turn frame uses one undifferentiated gray "
                    "surface for both message authors."
                ),
                region=NormalizedRegion(
                    x=0.65,
                    y=0.42,
                    width=0.2,
                    height=0.16,
                    semantic_region="messages",
                ),
                confidence=0.94,
            ),
        )
        actions = (
            StrictVisualRevisionAction(
                action_id="separate-authors",
                finding_ids=("conversation-flat",),
                artifact_fields=("css",),
                instruction=(
                    "Separate the two authors using alignment and surface tone; "
                    "leave all other design decisions unchanged."
                ),
            ),
        )
    return StrictVisualCritique(
        observations=(
            StrictVisualObservation(
                screenshot_id="desktop.after_turn_2",
                observation="Two message rows remain visible above the composer.",
            ),
        ),
        assessments=tuple(values),
        findings=findings,
        revision_actions=actions,
        summary="Strict screenshot-specific inspection.",
    )


class FakeAuditor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def audit(self, candidate):
        self.calls.append(candidate)
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeCritic:
    def __init__(self, critiques):
        self.critiques = list(critiques)
        self.calls = []

    async def critique(self, **kwargs):
        self.calls.append(kwargs)
        value = self.critiques.pop(0)
        if isinstance(value, BaseException):
            raise value
        return StrictVisualCriticResult(
            critique=value,
            usage=TokenUsage(prompt_tokens=7, output_tokens=3),
        )


class FakeEngine:
    def __init__(self, revised, *, error=None):
        self.revised = revised
        self.error = error
        self.visual_revision_calls = 0
        self.calls = []

    async def generate(self, **kwargs):
        self.visual_revision_calls += 1
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return EngineResult(
            artifact=self.revised,
            usage=TokenUsage(prompt_tokens=11, output_tokens=5),
        )


def reviewer(auditor, critic, engine):
    return ExperimentReview(
        request=BuilderRequest(
            engine=EngineName.DIRECT,
            brief="Build a compact RAW BUREAU AI chat widget.",
            visual_repair_limit=1,
        ),
        auditor=auditor,
        critic=critic,
        engine=engine,
    )


@pytest.mark.asyncio
async def test_repair_then_pass_uses_exactly_one_generation():
    raw = artifact(revision=5)
    revised = replace(raw, css=raw.css + "\n.kaigo-widget { --author-gap: 8px; }")
    audit_one = SimpleNamespace(name="raw-audit")
    audit_two = SimpleNamespace(name="final-audit")
    auditor = FakeAuditor([audit_one, audit_two])
    critic = FakeCritic(
        [visual_critique(repair=True), visual_critique(repair=False)]
    )
    engine = FakeEngine(revised)

    result = await reviewer(auditor, critic, engine).review(raw)

    assert engine.visual_revision_calls == 1
    assert len(auditor.calls) == 2
    assert len(critic.calls) == 2
    assert result.raw.artifact == raw
    assert result.raw.artifact is not raw
    assert result.final.artifact == revised
    assert result.final.critique.verdict is StrictVisualVerdict.PASS
    assert result.usage == TokenUsage(prompt_tokens=25, output_tokens=11)
    assert result.elapsed_seconds >= 0


@pytest.mark.asyncio
async def test_second_failure_is_terminal_without_another_generation():
    raw = artifact(revision=5)
    revised = replace(raw, css=raw.css + "\n.kaigo-widget { --author-gap: 8px; }")
    auditor = FakeAuditor([object(), object()])
    critic = FakeCritic(
        [visual_critique(repair=True), visual_critique(repair=True)]
    )
    engine = FakeEngine(revised)

    with pytest.raises(ExperimentVisualQualityError) as caught:
        await reviewer(auditor, critic, engine).review(raw)

    assert caught.value.error_code == "strict_visual_revision_failed"
    assert engine.visual_revision_calls == 1
    assert len(auditor.calls) == 2
    assert len(critic.calls) == 2


@pytest.mark.asyncio
async def test_initial_deterministic_failure_calls_neither_critic_nor_generator():
    raw = artifact(revision=5)
    failure = BrowserAuditError(
        "browser_gate_failed",
        "Panel leaves the viewport.",
        failures=("desktop.open_initial: panel outside viewport",),
    )
    auditor = FakeAuditor([failure])
    critic = FakeCritic([])
    engine = FakeEngine(raw)

    with pytest.raises(ExperimentVisualQualityError) as caught:
        await reviewer(auditor, critic, engine).review(raw)

    assert caught.value.error_code == "initial_deterministic_failure"
    assert len(critic.calls) == 0
    assert engine.visual_revision_calls == 0


@pytest.mark.asyncio
async def test_post_revision_deterministic_failure_is_terminal():
    raw = artifact(revision=5)
    revised = replace(raw, css=raw.css + "\n.kaigo-widget { --author-gap: 8px; }")
    failure = BrowserAuditError(
        "browser_gate_failed",
        "Composer now overlaps messages.",
        failures=("desktop.after_turn_2: composer overlaps messages",),
    )
    auditor = FakeAuditor([object(), failure])
    critic = FakeCritic([visual_critique(repair=True)])
    engine = FakeEngine(revised)

    with pytest.raises(ExperimentVisualQualityError) as caught:
        await reviewer(auditor, critic, engine).review(raw)

    assert caught.value.error_code == "deterministic_regression"
    assert engine.visual_revision_calls == 1
    assert len(critic.calls) == 1


@pytest.mark.asyncio
async def test_unrelated_revision_field_is_rejected():
    raw = artifact(revision=5)
    revised = replace(
        raw,
        css=raw.css + "\n.kaigo-widget { --author-gap: 8px; }",
        art_direction="Unrelated full redesign.",
    )
    auditor = FakeAuditor([object()])
    critic = FakeCritic([visual_critique(repair=True)])
    engine = FakeEngine(revised)

    with pytest.raises(ExperimentVisualQualityError) as caught:
        await reviewer(auditor, critic, engine).review(raw)

    assert caught.value.error_code == "unrelated_visual_revision"
    assert "art_direction" in caught.value.diagnostic
    assert len(auditor.calls) == 1


@pytest.mark.asyncio
async def test_change_summary_cannot_be_changed_by_visual_revision():
    raw = artifact(revision=5)
    revised = replace(
        raw,
        css=raw.css + "\n.kaigo-widget { --author-gap: 8px; }",
        change_summary="Model rewrote provenance for an unrelated redesign.",
    )
    auditor = FakeAuditor([object(), object()])
    critic = FakeCritic(
        [visual_critique(repair=True), visual_critique(repair=False)]
    )
    engine = FakeEngine(revised)

    with pytest.raises(ExperimentVisualQualityError) as caught:
        await reviewer(auditor, critic, engine).review(raw)

    assert caught.value.error_code == "unrelated_visual_revision"
    assert "change_summary" in caught.value.diagnostic
    assert len(auditor.calls) == 1


@pytest.mark.asyncio
async def test_initial_pass_keeps_raw_and_final_evidence_without_generation():
    raw = artifact(revision=5)
    audit = SimpleNamespace(name="raw-audit")
    auditor = FakeAuditor([audit])
    critic = FakeCritic([visual_critique(repair=False)])
    engine = FakeEngine(raw)

    result = await reviewer(auditor, critic, engine).review(raw)

    assert result.raw.artifact == result.final.artifact == raw
    assert result.raw is not result.final
    assert engine.visual_revision_calls == 0
    assert len(auditor.calls) == len(critic.calls) == 1


@pytest.mark.asyncio
async def test_final_critic_failure_aggregates_usage_and_preserves_raw_evidence():
    raw = artifact(revision=5)
    revised = replace(raw, css=raw.css + "\n.kaigo-widget { --author-gap: 8px; }")
    failure = StrictVisualCriticError(
        "strict_visual_critic_unavailable",
        "critic unavailable",
        diagnostic="provider request failed",
        usage=TokenUsage(prompt_tokens=13, output_tokens=2),
    )
    auditor = FakeAuditor([object(), object()])
    critic = FakeCritic([visual_critique(repair=True), failure])
    engine = FakeEngine(revised)

    with pytest.raises(ExperimentVisualQualityError) as caught:
        await reviewer(auditor, critic, engine).review(raw)

    assert caught.value.error_code == "strict_visual_critic_failed"
    assert caught.value.raw is not None
    assert caught.value.raw.artifact == raw
    assert caught.value.raw.critique.verdict is StrictVisualVerdict.REPAIR
    assert caught.value.usage == TokenUsage(prompt_tokens=31, output_tokens=10)
    assert "provider request failed" in caught.value.diagnostic


@pytest.mark.asyncio
async def test_revision_engine_failure_aggregates_usage_and_preserves_raw_evidence():
    raw = artifact(revision=5)
    failure = BuilderEngineError(
        "provider_unavailable",
        "revision unavailable",
        diagnostic="upstream reset",
        usage=TokenUsage(prompt_tokens=17, output_tokens=4),
    )
    auditor = FakeAuditor([object()])
    critic = FakeCritic([visual_critique(repair=True)])
    engine = FakeEngine(raw, error=failure)

    with pytest.raises(ExperimentVisualQualityError) as caught:
        await reviewer(auditor, critic, engine).review(raw)

    assert caught.value.error_code == "strict_visual_revision_unavailable"
    assert caught.value.raw is not None
    assert caught.value.raw.artifact == raw
    assert caught.value.usage == TokenUsage(prompt_tokens=24, output_tokens=7)
    assert "upstream reset" in caught.value.diagnostic


@pytest.mark.asyncio
async def test_cancellation_from_final_critic_propagates_unchanged():
    raw = artifact(revision=5)
    revised = replace(raw, css=raw.css + "\n.kaigo-widget { --author-gap: 8px; }")
    cancellation = asyncio.CancelledError()
    auditor = FakeAuditor([object(), object()])
    critic = FakeCritic([visual_critique(repair=True), cancellation])
    engine = FakeEngine(revised)

    with pytest.raises(asyncio.CancelledError) as caught:
        await reviewer(auditor, critic, engine).review(raw)

    assert caught.value is cancellation


@pytest.mark.asyncio
async def test_revision_engine_cannot_mutate_evidence_owned_raw_snapshot():
    raw = artifact(revision=5)
    original_tokens = dict(raw.theme_tokens)
    revised = replace(raw, css=raw.css + "\n.kaigo-widget { --author-gap: 8px; }")

    class MutatingEngine(FakeEngine):
        async def generate(self, **kwargs):
            kwargs["previous_artifact"].theme_tokens["malicious"] = "#ff00ff"
            return await super().generate(**kwargs)

    auditor = FakeAuditor([object(), object()])
    critic = FakeCritic(
        [visual_critique(repair=True), visual_critique(repair=False)]
    )
    engine = MutatingEngine(revised)

    result = await reviewer(auditor, critic, engine).review(raw)

    assert raw.theme_tokens == original_tokens
    assert result.raw.artifact.theme_tokens == original_tokens
    assert "malicious" not in result.raw.artifact.theme_tokens
