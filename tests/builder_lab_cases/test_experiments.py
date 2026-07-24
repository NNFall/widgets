import asyncio
import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from builder_lab import experiments as experiment_module
from builder_lab.experiments import (
    ABC_PROFILES,
    AbcExperimentManifest,
    ExperimentEvidence,
    ExperimentFailureEvidence,
    ExperimentPricingSnapshot,
    ExperimentRoleEvent,
    ExperimentVariant,
    ExperimentVariantContext,
    VariantExecutionError,
    canonical_common_input_digest,
    run_abc_experiment,
    write_experiment_package,
)
from builder_lab.models import (
    BuilderRequest,
    ConceptRole,
    ConceptRoleBrief,
    CreativeProfile,
    EngineName,
    TokenUsage,
)
from builder_lab.engines.base import BuilderEngineError, ConceptRoleResult
from tests.builder_lab_cases.test_experiment_review import visual_critique
from tests.builder_lab_cases.test_validation import artifact
from tests.builder_lab_cases.test_visual_critic import report
from scripts import run_abc_comparison as abc_script
from scripts.run_abc_comparison import (
    DirectVariantExecutor,
    resolve_single_pricing_policy,
    run_concept_roles_with_events,
)


def evidence(*, suffix: str) -> ExperimentEvidence:
    candidate = replace(
        artifact(revision=5),
        css=artifact(revision=5).css + f"\n.kaigo-widget {{ --variant: {suffix}; }}",
    )
    return ExperimentEvidence(
        artifact=candidate,
        audit=report(),
        critique=visual_critique(repair=False),
    )


def pricing() -> ExperimentPricingSnapshot:
    return ExperimentPricingSnapshot(
        currency="USD",
        prompt_per_million=0.50,
        output_per_million=3.00,
        thinking_per_million=3.00,
        captured_at="2026-07-24T00:00:00+00:00",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    )


def variant(
    profile: CreativeProfile,
    *,
    status: str = "completed",
    rejected_final: bool = False,
    pricing_snapshot: ExperimentPricingSnapshot | None = None,
) -> ExperimentVariant:
    raw = evidence(suffix=f"{profile.value}-raw")
    final = evidence(suffix=f"{profile.value}-final")
    return ExperimentVariant.create(
        profile=profile,
        public_slug=profile.value.replace("_", "-"),
        run_id=f"run-{profile.value}",
        model="gemini-3.6-flash",
        thinking="high",
        status=status,
        raw=raw if status == "completed" or rejected_final else None,
        final=final if status == "completed" or rejected_final else None,
        usage=TokenUsage(
            prompt_tokens=1_000,
            output_tokens=200,
            thinking_tokens=300,
        ),
        elapsed_seconds=12.5,
        pricing=pricing_snapshot or pricing(),
        role_events=(
            ExperimentRoleEvent(
                role="site_brand_analyst",
                status="completed",
                summary="Brand evidence extracted.",
                decisions=("Use the observed editorial grid.",),
                safeguards=("Keep the chat subordinate to the page.",),
                usage=TokenUsage(prompt_tokens=10, output_tokens=4),
                provider_request_id="provider-role-1",
                diagnostic="model=gemini-3.6-flash",
            ),
        ),
        error_code="provider_unavailable" if status == "failed" else None,
        error_message="Provider failed." if status == "failed" else None,
    )


def request(profile: CreativeProfile = CreativeProfile.PRODUCT_CHAT) -> BuilderRequest:
    return BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Build one compact RAW BUREAU AI chat widget.",
        reference_context="Frozen RAW BUREAU visual and content evidence.",
        contract_id="chat-v1",
        creative_profile=profile,
        visual_repair_limit=1,
    )


def test_experiment_manifest_requires_three_unique_profiles_and_common_inputs():
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )

    assert [item.profile.value for item in manifest.variants] == [
        "product_chat",
        "brand_motion",
        "ai_character",
    ]
    assert manifest.source_digest == "a" * 64
    assert manifest.common_input_digest == "b" * 64
    assert len({item.public_slug for item in manifest.variants}) == 3
    assert manifest.total_cost_usd > 0


def test_manifest_rejects_missing_duplicate_or_different_model_variants():
    items = tuple(variant(profile) for profile in ABC_PROFILES)
    with pytest.raises(ValueError, match="exactly A/B/C"):
        AbcExperimentManifest.create(
            source_digest="a" * 64,
            common_input_digest="b" * 64,
            contract_id="chat-v1",
            variants=items[:2],
        )
    with pytest.raises(ValueError, match="same model and thinking"):
        AbcExperimentManifest.create(
            source_digest="a" * 64,
            common_input_digest="b" * 64,
            contract_id="chat-v1",
            variants=(
                items[0],
                replace(items[1], model="gemini-other"),
                items[2],
            ),
        )
    changed_pricing = ExperimentPricingSnapshot(
        currency="USD",
        prompt_per_million=0.51,
        output_per_million=3.00,
        thinking_per_million=3.00,
        captured_at="2026-07-24T00:00:00+00:00",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    )
    with pytest.raises(ValueError, match="same pricing snapshot"):
        AbcExperimentManifest.create(
            source_digest="a" * 64,
            common_input_digest="b" * 64,
            contract_id="chat-v1",
            variants=(
                items[0],
                variant(
                    CreativeProfile.BRAND_MOTION,
                    pricing_snapshot=changed_pricing,
                ),
                items[2],
            ),
        )


def test_variant_keeps_raw_and_final_separate_and_records_exact_cost():
    item = variant(CreativeProfile.BRAND_MOTION)

    assert item.raw is not None and item.final is not None
    assert item.raw.artifact.revision == item.final.artifact.revision == 5
    assert item.raw.artifact != item.final.artifact
    assert item.cost_usd == pytest.approx(0.002)
    payload = item.to_dict()
    assert payload["pricing"]["captured_at"] == "2026-07-24T00:00:00+00:00"
    assert payload["usage"]["thinking_tokens"] == 300
    assert payload["cost_usd"] == pytest.approx(0.002)


def test_role_event_uses_the_same_secret_redactor_for_operator_fields():
    event = ExperimentRoleEvent(
        role="site_brand_analyst",
        status="failed",
        summary="Role failed.",
        provider_request_id="token=provider-request-secret",
        diagnostic='"client_secret": "role-diagnostic-secret"',
    )

    payload = event.to_dict()
    serialized = json.dumps(payload)
    assert "provider-request-secret" not in serialized
    assert "role-diagnostic-secret" not in serialized
    assert "[REDACTED]" in serialized


def test_completed_variant_requires_a_passing_final_critique():
    raw = evidence(suffix="raw")
    rejected = ExperimentEvidence(
        artifact=evidence(suffix="rejected").artifact,
        audit=report(),
        critique=visual_critique(repair=True),
    )

    with pytest.raises(ValueError, match="final critique must pass"):
        ExperimentVariant.create(
            profile=CreativeProfile.PRODUCT_CHAT,
            public_slug="product-chat",
            run_id="run-rejected-as-completed",
            model="gemini-3.6-flash",
            thinking="high",
            status="completed",
            raw=raw,
            final=rejected,
            usage=TokenUsage(prompt_tokens=100),
            elapsed_seconds=1,
            pricing=pricing(),
        )


def test_failed_variant_is_kept_without_fabricated_evidence():
    item = variant(CreativeProfile.AI_CHARACTER, status="failed")

    assert item.raw is None and item.final is None
    assert item.error_code == "provider_unavailable"
    assert item.to_dict()["status"] == "failed"


def test_failed_variant_can_preserve_raw_and_rejected_final_evidence():
    item = variant(
        CreativeProfile.BRAND_MOTION,
        status="failed",
        rejected_final=True,
    )

    assert item.raw is not None and item.final is not None
    assert item.raw.artifact != item.final.artifact
    assert item.error_code == "provider_unavailable"


def test_failure_evidence_enforces_closed_phase_kind_audit_matrix():
    candidate = artifact(revision=5)
    complete_audit = report()

    with pytest.raises(ValueError, match="kind is invalid"):
        ExperimentFailureEvidence(
            phase="raw",
            kind="unknown_failure",
            artifact=candidate,
        )
    with pytest.raises(ValueError, match="phase is invalid"):
        ExperimentFailureEvidence(
            phase="raw",
            kind="rejected_revision",
            artifact=candidate,
        )
    with pytest.raises(ValueError, match="requires a complete audit"):
        ExperimentFailureEvidence(
            phase="raw",
            kind="strict_visual_critic_failure",
            artifact=candidate,
        )
    with pytest.raises(ValueError, match="cannot contain an audit"):
        ExperimentFailureEvidence(
            phase="final",
            kind="rejected_revision",
            artifact=candidate,
            audit=complete_audit,
        )

    assert ExperimentFailureEvidence(
        phase="raw",
        kind="browser_audit_failure",
        artifact=candidate,
    ).audit is None
    assert ExperimentFailureEvidence(
        phase="final",
        kind="strict_visual_critic_failure",
        artifact=candidate,
        audit=complete_audit,
    ).audit is complete_audit


def test_final_phase_failure_evidence_requires_preserved_raw_evidence():
    rejected = ExperimentFailureEvidence(
        phase="final",
        kind="rejected_revision",
        artifact=artifact(revision=5),
        failure_details=("changed_field:art_direction",),
    )

    with pytest.raises(ValueError, match="final failure evidence requires"):
        ExperimentVariant.create(
            profile=CreativeProfile.PRODUCT_CHAT,
            public_slug="product-chat",
            run_id="run-missing-raw",
            model="gemini-3.6-flash",
            thinking="high",
            status="failed",
            raw=None,
            final=None,
            failure_evidence=rejected,
            usage=TokenUsage(),
            elapsed_seconds=1,
            pricing=pricing(),
            error_code="unrelated_visual_revision",
            error_message="Revision rejected.",
        )


def test_common_digest_excludes_only_profile():
    source = "a" * 64
    product = request(CreativeProfile.PRODUCT_CHAT)
    motion = replace(product, creative_profile=CreativeProfile.BRAND_MOTION)

    assert canonical_common_input_digest(
        source_digest=source,
        request=product,
        model="gemini-3.6-flash",
        thinking="high",
    ) == canonical_common_input_digest(
        source_digest=source,
        request=motion,
        model="gemini-3.6-flash",
        thinking="high",
    )
    assert canonical_common_input_digest(
        source_digest=source,
        request=replace(product, brief="Different brief"),
        model="gemini-3.6-flash",
        thinking="high",
    ) != canonical_common_input_digest(
        source_digest=source,
        request=product,
        model="gemini-3.6-flash",
        thinking="high",
    )


def test_single_pricing_runner_defaults_critic_to_main_and_rejects_mixed_models():
    assert resolve_single_pricing_policy(
        model="gemini-3.6-flash",
        thinking="high",
        critic_model=None,
        critic_thinking=None,
    ) == ("gemini-3.6-flash", "high")
    with pytest.raises(ValueError, match="single pricing snapshot"):
        resolve_single_pricing_policy(
            model="gemini-3.6-flash",
            thinking="high",
            critic_model="gemini-3.5-flash",
            critic_thinking="high",
        )


@pytest.mark.asyncio
async def test_role_pipeline_preserves_full_briefs_and_real_usage_without_double_count():
    calls = []
    role_usage = {
        ConceptRole.SITE_BRAND_ANALYST: TokenUsage(prompt_tokens=11, output_tokens=3),
        ConceptRole.CONVERSATION_DESIGNER: TokenUsage(
            prompt_tokens=13,
            output_tokens=5,
        ),
        ConceptRole.ART_DIRECTOR_FRONTEND_DEVELOPER: TokenUsage(
            prompt_tokens=17,
            output_tokens=7,
            thinking_tokens=2,
        ),
    }

    class FakeEngine:
        async def develop_concept_role(self, *, request, role, prior_briefs=()):
            calls.append((role, tuple(item.role for item in prior_briefs)))
            return ConceptRoleResult(
                brief=ConceptRoleBrief(
                    role=role,
                    summary=f"{role.value} summary",
                    decisions=(f"{role.value} decision",),
                    safeguards=(f"{role.value} safeguard",),
                ),
                usage=role_usage[role],
                provider_request_id=f"request-{role.value}",
                diagnostic=f"diagnostic-{role.value}",
            )

    result, events = await run_concept_roles_with_events(
        engine=FakeEngine(),
        request=request(),
    )

    assert calls == [
        (ConceptRole.SITE_BRAND_ANALYST, ()),
        (
            ConceptRole.CONVERSATION_DESIGNER,
            (ConceptRole.SITE_BRAND_ANALYST,),
        ),
        (
            ConceptRole.ART_DIRECTOR_FRONTEND_DEVELOPER,
            (
                ConceptRole.SITE_BRAND_ANALYST,
                ConceptRole.CONVERSATION_DESIGNER,
            ),
        ),
    ]
    assert tuple(event.usage for event in events) == tuple(
        role_usage[role] for role in role_usage
    )
    assert result.usage == sum(role_usage.values(), TokenUsage())
    assert sum((event.usage for event in events), TokenUsage()) == result.usage
    assert events[-1].decisions == (
        "art_director_frontend_developer decision",
    )
    assert events[-1].safeguards == (
        "art_director_frontend_developer safeguard",
    )
    assert (
        events[-1].provider_request_id
        == "request-art_director_frontend_developer"
    )
    assert (
        events[-1].diagnostic
        == "diagnostic-art_director_frontend_developer"
    )


@pytest.mark.asyncio
async def test_role_pipeline_converts_partial_failure_to_variant_events_once():
    completed_usage = TokenUsage(prompt_tokens=11, output_tokens=3)
    failed_usage = TokenUsage(
        prompt_tokens=13,
        output_tokens=5,
        thinking_tokens=2,
    )

    class FailingEngine:
        async def develop_concept_role(self, *, request, role, prior_briefs=()):
            if role is ConceptRole.CONVERSATION_DESIGNER:
                failure = BuilderEngineError(
                    "invalid_artifact",
                    "Conversation role returned malformed JSON.",
                    usage=failed_usage,
                    diagnostic="schema validation failed at decisions",
                )
                failure.provider_request_id = "provider-failed-conversation"
                raise failure
            return ConceptRoleResult(
                brief=ConceptRoleBrief(
                    role=role,
                    summary="Brand evidence extracted.",
                    decisions=("Keep the observed editorial grid.",),
                    safeguards=("Do not invent services.",),
                ),
                usage=completed_usage,
                provider_request_id="provider-completed-brand",
                diagnostic="brand role complete",
            )

    with pytest.raises(VariantExecutionError) as caught:
        await run_concept_roles_with_events(
            engine=FailingEngine(),
            request=request(),
        )

    error = caught.value
    assert error.error_code == "invalid_artifact"
    assert error.usage == completed_usage + failed_usage
    assert [event.status for event in error.role_events] == [
        "completed",
        "failed",
    ]
    assert [event.role for event in error.role_events] == [
        "site_brand_analyst",
        "conversation_designer",
    ]
    assert error.role_events[0].decisions == (
        "Keep the observed editorial grid.",
    )
    assert error.role_events[0].provider_request_id == "provider-completed-brand"
    assert error.role_events[1].usage == failed_usage
    assert (
        error.role_events[1].provider_request_id
        == "provider-failed-conversation"
    )
    assert (
        error.role_events[1].diagnostic
        == "schema validation failed at decisions"
    )
    assert (
        sum((event.usage for event in error.role_events), TokenUsage())
        == error.usage
    )


@pytest.mark.asyncio
async def test_partial_role_failure_sanitizes_untrusted_provider_diagnostics():
    class FailingEngine:
        async def develop_concept_role(self, *, request, role, prior_briefs=()):
            if role is ConceptRole.CONVERSATION_DESIGNER:
                failure = BuilderEngineError(
                    "invalid_artifact",
                    "bad\x00response" * 300,
                    usage=TokenUsage(prompt_tokens=7),
                    diagnostic=("diagnostic\x00" * 500),
                )
                failure.provider_request_id = "request\x00id" * 100
                raise failure
            return ConceptRoleResult(
                brief=ConceptRoleBrief(
                    role=role,
                    summary="Brand evidence extracted.",
                    decisions=("Keep the observed editorial grid.",),
                    safeguards=(),
                ),
                usage=TokenUsage(prompt_tokens=5),
            )

    with pytest.raises(VariantExecutionError) as caught:
        await run_concept_roles_with_events(
            engine=FailingEngine(),
            request=request(),
        )

    error = caught.value
    failed = error.role_events[-1]
    assert failed.status == "failed"
    assert "\x00" not in error.public_message
    assert "\x00" not in failed.summary
    assert "\x00" not in failed.provider_request_id
    assert "\x00" not in failed.diagnostic
    assert len(error.public_message) <= 2000
    assert len(failed.summary) <= 1000
    assert len(failed.provider_request_id) <= 256
    assert len(failed.diagnostic) <= 2000


@pytest.mark.asyncio
async def test_direct_executor_records_elapsed_time_for_partial_role_failure():
    class FailingEngine:
        def __init__(self):
            self.closed = False

        async def develop_concept_role(self, *, request, role, prior_briefs=()):
            if role is ConceptRole.CONVERSATION_DESIGNER:
                raise BuilderEngineError(
                    "provider_unavailable",
                    "Conversation role failed.",
                    usage=TokenUsage(prompt_tokens=7),
                )
            return ConceptRoleResult(
                brief=ConceptRoleBrief(
                    role=role,
                    summary="Brand evidence extracted.",
                    decisions=("Keep the observed editorial grid.",),
                    safeguards=(),
                ),
                usage=TokenUsage(prompt_tokens=5),
            )

        async def close(self):
            self.closed = True

    engine = FailingEngine()
    executor = DirectVariantExecutor(
        api_key="test-key",
        base_url="https://generativelanguage.googleapis.com",
        critic_model="gemini-3.6-flash",
        critic_thinking="high",
        critic_timeout_seconds=90,
        browser_timeout_ms=10_000,
        browser_total_timeout_seconds=120,
    )
    context = ExperimentVariantContext(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        request=request(),
        run_id="run-partial-role-failure",
        model="gemini-3.6-flash",
        thinking="high",
        pricing=pricing(),
        audit_semaphore=asyncio.Semaphore(1),
    )

    with (
        patch(
            "scripts.run_abc_comparison.GeminiDirectEngine",
            return_value=engine,
        ),
        patch(
            "scripts.run_abc_comparison.time.perf_counter",
            side_effect=(100.0, 104.25),
        ),
        pytest.raises(VariantExecutionError) as caught,
    ):
        await executor(context)

    error = caught.value
    assert error.elapsed_seconds == pytest.approx(4.25)
    assert error.usage == TokenUsage(prompt_tokens=12)
    assert [event.status for event in error.role_events] == [
        "completed",
        "failed",
    ]
    assert engine.closed


@pytest.mark.asyncio
async def test_runner_starts_three_independent_variants_and_serializes_audits():
    started: set[CreativeProfile] = set()
    all_started = asyncio.Event()
    active_audits = 0
    maximum_audits = 0
    run_ids: set[str] = set()
    semaphore_ids: set[int] = set()

    async def execute(context):
        nonlocal active_audits, maximum_audits
        started.add(context.request.creative_profile)
        run_ids.add(context.run_id)
        semaphore_ids.add(id(context.audit_semaphore))
        if len(started) == 3:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=1)

        async def audited():
            nonlocal active_audits, maximum_audits
            active_audits += 1
            maximum_audits = max(maximum_audits, active_audits)
            await asyncio.sleep(0)
            active_audits -= 1
            return True

        assert await context.run_browser_audit(audited)
        return replace(
            variant(context.request.creative_profile),
            run_id=context.run_id,
        )

    manifest = await run_abc_experiment(
        source_digest="a" * 64,
        base_request=request(),
        model="gemini-3.6-flash",
        thinking="high",
        pricing=pricing(),
        execute_variant=execute,
        run_id_factory=lambda profile: f"independent-{profile.value}",
    )

    assert started == set(ABC_PROFILES)
    assert len(run_ids) == 3
    assert len(semaphore_ids) == 1
    assert maximum_audits == 1
    assert all(item.status == "completed" for item in manifest.variants)


@pytest.mark.asyncio
async def test_runner_records_one_failed_profile_honestly():
    partial_events = (
        ExperimentRoleEvent(
            role="site_brand_analyst",
            status="completed",
            summary="Brand evidence extracted.",
            decisions=("Keep the observed editorial grid.",),
            usage=TokenUsage(prompt_tokens=40),
        ),
        ExperimentRoleEvent(
            role="conversation_designer",
            status="failed",
            summary="Conversation role failed.",
            usage=TokenUsage(prompt_tokens=37),
            provider_request_id="provider-failed-conversation",
            diagnostic="schema validation failed",
        ),
    )

    async def execute(context):
        if context.request.creative_profile is CreativeProfile.BRAND_MOTION:
            raise VariantExecutionError(
                "provider_unavailable",
                "Gemini did not complete this profile.",
                usage=TokenUsage(prompt_tokens=77),
                elapsed_seconds=2.5,
                role_events=partial_events,
            )
        return replace(
            variant(context.request.creative_profile),
            run_id=context.run_id,
        )

    manifest = await run_abc_experiment(
        source_digest="a" * 64,
        base_request=request(),
        model="gemini-3.6-flash",
        thinking="high",
        pricing=pricing(),
        execute_variant=execute,
    )

    failed = manifest.variant(CreativeProfile.BRAND_MOTION)
    assert failed.status == "failed"
    assert failed.raw is None and failed.final is None
    assert failed.usage.prompt_tokens == 77
    assert failed.error_code == "provider_unavailable"
    assert failed.role_events == partial_events
    assert failed.to_dict()["role_events"][-1]["status"] == "failed"
    assert (
        sum((event.usage for event in failed.role_events), TokenUsage())
        == failed.usage
    )


@pytest.mark.asyncio
async def test_runner_sanitizes_diagnostic_and_preserves_incomplete_failure_evidence():
    candidate = artifact(revision=5)
    incomplete = ExperimentFailureEvidence(
        phase="raw",
        kind="browser_audit_failure",
        artifact=candidate,
        audit=None,
        failure_details=(
            "desktop.open_initial: panel outside viewport",
            "Authorization: Bearer top-secret-token",
        ),
    )

    async def execute(context):
        if context.request.creative_profile is CreativeProfile.PRODUCT_CHAT:
            raise VariantExecutionError(
                "initial_deterministic_failure",
                "Browser audit failed; token=public-message-secret",
                diagnostic=(
                    "api_key=AIzaSyDefinitelySecret1234567890\x00 "
                    "\"client_secret\": \"quoted-secret-value\" "
                    "https://operator:gateway-password@example.test/path "
                    "https://example.test/api?key=query-secret-value "
                    "key=plain-secret-value "
                    "GEMINI_API_KEY=environment-secret-value "
                    "\"chat_system_prompt\": \"private system instruction\" "
                    "C:\\Users\\Operator\\private\\trace.json "
                    "/root/ai_project/private/trace.json "
                    "desktop.open_initial failed"
                ),
                failure_evidence=incomplete,
            )
        return replace(
            variant(context.request.creative_profile),
            run_id=context.run_id,
        )

    manifest = await run_abc_experiment(
        source_digest="a" * 64,
        base_request=request(),
        model="gemini-3.6-flash",
        thinking="high",
        pricing=pricing(),
        execute_variant=execute,
    )

    failed = manifest.variant(CreativeProfile.PRODUCT_CHAT)
    assert failed.failure_evidence is not None
    assert failed.failure_evidence.artifact == candidate
    assert failed.diagnostic is not None
    assert "desktop.open_initial failed" in failed.diagnostic
    serialized = json.dumps(failed.to_dict())
    assert "DefinitelySecret" not in serialized
    assert "top-secret-token" not in serialized
    assert "quoted-secret-value" not in serialized
    assert "gateway-password" not in serialized
    assert "query-secret-value" not in serialized
    assert "plain-secret-value" not in serialized
    assert "environment-secret-value" not in serialized
    assert "private system instruction" not in serialized
    assert "Operator" not in serialized
    assert "/root/ai_project/private" not in serialized
    assert "public-message-secret" not in serialized
    assert "\u0000" not in serialized
    assert "[REDACTED]" in serialized


@pytest.mark.asyncio
async def test_runner_rejects_result_with_a_different_pricing_snapshot():
    changed_pricing = ExperimentPricingSnapshot(
        currency="USD",
        prompt_per_million=0.75,
        output_per_million=3.00,
        thinking_per_million=3.00,
        captured_at="2026-07-24T00:00:00+00:00",
        source="https://ai.google.dev/gemini-api/docs/pricing",
    )

    async def execute(context):
        return replace(
            variant(
                context.request.creative_profile,
                pricing_snapshot=changed_pricing,
            ),
            run_id=context.run_id,
        )

    with pytest.raises(ValueError, match="immutable run context"):
        await run_abc_experiment(
            source_digest="a" * 64,
            base_request=request(),
            model="gemini-3.6-flash",
            thinking="high",
            pricing=pricing(),
            execute_variant=execute,
        )


def test_package_writer_is_atomic_bounded_and_never_overwrites_existing_routes(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    sibling = tmp_path / "direct-chat-v2"
    sibling.mkdir()
    (sibling / "sentinel.txt").write_text("keep", encoding="utf-8")
    output = tmp_path / "direct-abc-v1"

    write_experiment_package(output, manifest)

    payload = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert payload["common_input_digest"] == "b" * 64
    assert (output / "product-chat" / "final" / "index.html").is_file()
    viewer = (
        output / "product-chat" / "final" / "viewer.html"
    ).read_text(encoding="utf-8")
    assert 'src="index.html"' in viewer
    assert 'sandbox="allow-scripts"' in viewer
    assert "allow-same-origin" not in viewer
    assert 'referrerpolicy="no-referrer"' in viewer
    assert "<script" not in viewer.lower()
    assert (output / "product-chat" / "raw" / "critique.json").is_file()
    role_payload = json.loads(
        (output / "product-chat" / "role-events.json").read_text(
            encoding="utf-8"
        )
    )
    assert role_payload["events"][0]["decisions"] == [
        "Use the observed editorial grid."
    ]
    assert role_payload["events"][0]["usage"]["prompt_tokens"] == 10
    assert (sibling / "sentinel.txt").read_text(encoding="utf-8") == "keep"
    with pytest.raises(FileExistsError):
        write_experiment_package(output, manifest)


def test_package_writer_explicitly_writes_incomplete_failure_evidence(tmp_path: Path):
    failure_evidence = ExperimentFailureEvidence(
        phase="raw",
        kind="strict_visual_critic_failure",
        artifact=artifact(revision=5),
        audit=report(),
        failure_details=("critic response did not match the schema",),
    )
    failed = ExperimentVariant.create(
        profile=CreativeProfile.PRODUCT_CHAT,
        public_slug="product-chat",
        run_id="run-product-chat-failed",
        model="gemini-3.6-flash",
        thinking="high",
        status="failed",
        raw=None,
        final=None,
        failure_evidence=failure_evidence,
        usage=TokenUsage(prompt_tokens=100),
        elapsed_seconds=3.5,
        pricing=pricing(),
        error_code="strict_visual_critic_failed",
        error_message="Strict visual critic failed.",
        diagnostic="schema mismatch at observations",
    )
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=(
            failed,
            variant(CreativeProfile.BRAND_MOTION),
            variant(CreativeProfile.AI_CHARACTER),
        ),
    )
    output = tmp_path / "direct-abc-failure-evidence"

    write_experiment_package(output, manifest)

    evidence_root = (
        output
        / "product-chat"
        / "failure-evidence"
        / "raw-strict_visual_critic_failure"
    )
    assert (evidence_root / "artifact.json").is_file()
    assert (evidence_root / "audit.json").is_file()
    assert not (evidence_root / "critique.json").exists()
    assert not (evidence_root / "index.html").exists()
    assert not (evidence_root / "viewer.html").exists()
    failure_payload = json.loads(
        (evidence_root / "failure.json").read_text(encoding="utf-8")
    )
    assert failure_payload["state"] == "incomplete"
    assert failure_payload["failure_details"] == [
        "critic response did not match the schema"
    ]
    report_payload = json.loads(
        (output / "product-chat" / "report.json").read_text(encoding="utf-8")
    )
    assert report_payload["diagnostic"] == "schema mismatch at observations"
    assert report_payload["failure_evidence"]["kind"] == (
        "strict_visual_critic_failure"
    )
    failure_page = (
        output / "product-chat" / "index.html"
    ).read_text(encoding="utf-8")
    assert (
        "failure-evidence/raw-strict_visual_critic_failure/artifact.json"
        in failure_page
    )
    assert "incomplete" in failure_page


def test_package_writer_never_executes_unaudited_rejected_candidate(tmp_path: Path):
    rejected = ExperimentFailureEvidence(
        phase="final",
        kind="rejected_revision",
        artifact=artifact(revision=5),
        audit=None,
        failure_details=("changed_field:art_direction",),
    )
    failed = ExperimentVariant.create(
        profile=CreativeProfile.PRODUCT_CHAT,
        public_slug="product-chat",
        run_id="run-product-chat-rejected",
        model="gemini-3.6-flash",
        thinking="high",
        status="failed",
        raw=evidence(suffix="product-chat-raw-before-rejected"),
        final=None,
        failure_evidence=rejected,
        usage=TokenUsage(prompt_tokens=100),
        elapsed_seconds=3.5,
        pricing=pricing(),
        error_code="unrelated_visual_revision",
        error_message="Revision changed an unrelated field.",
        diagnostic="art_direction",
    )
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=(
            failed,
            variant(CreativeProfile.BRAND_MOTION),
            variant(CreativeProfile.AI_CHARACTER),
        ),
    )
    output = tmp_path / "direct-abc-rejected"

    write_experiment_package(output, manifest)

    evidence_root = (
        output
        / "product-chat"
        / "failure-evidence"
        / "final-rejected_revision"
    )
    assert (evidence_root / "artifact.json").is_file()
    assert not (evidence_root / "index.html").exists()
    assert not (evidence_root / "viewer.html").exists()
    failure_page = (
        output / "product-chat" / "index.html"
    ).read_text(encoding="utf-8")
    assert "failure-evidence/final-rejected_revision/artifact.json" in failure_page


def test_package_writer_reserves_destination_exclusively_under_a_race(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    output = tmp_path / "direct-abc-race"
    barrier = threading.Barrier(2)

    def attempt():
        barrier.wait(timeout=3)
        try:
            return ("ok", write_experiment_package(output, manifest))
        except FileExistsError as exc:
            return ("exists", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: attempt(), range(2)))

    assert sorted(result[0] for result in results) == ["exists", "ok"]
    payload = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert payload["source_digest"] == "a" * 64
    assert not tuple(tmp_path.glob(".direct-abc-race*"))


def test_package_final_directory_is_invisible_until_atomic_publish(tmp_path: Path):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    output = tmp_path / "direct-abc-invisible"
    entered = threading.Event()
    release = threading.Event()
    from builder_lab import experiments as experiment_module

    original = experiment_module._write_evidence

    def delayed(root, item):
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=5)
        return original(root, item)

    with patch("builder_lab.experiments._write_evidence", side_effect=delayed):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write_experiment_package, output, manifest)
            assert entered.wait(timeout=5)
            assert not output.exists()
            release.set()
            assert future.result(timeout=15) == output.resolve()

    assert (output / "manifest.json").is_file()
    assert not tuple(tmp_path.glob(".direct-abc-invisible*"))


def test_package_manifest_is_the_last_file_written_before_publish(tmp_path: Path):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    output = tmp_path / "direct-abc-manifest-last"
    from builder_lab import experiments as experiment_module

    original = experiment_module._write
    written: list[str] = []

    def recording(path, data):
        written.append(path.name)
        return original(path, data)

    with patch("builder_lab.experiments._write", side_effect=recording):
        write_experiment_package(output, manifest)

    assert written[-1] == "manifest.json"
    assert (output / "manifest.json").is_file()


def test_package_failure_cleans_reservation_and_does_not_remove_foreign_final(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    output = tmp_path / "direct-abc-foreign"
    entered = threading.Event()
    release = threading.Event()
    from builder_lab import experiments as experiment_module

    original = experiment_module._write_evidence

    def delayed(root, item):
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=5)
        return original(root, item)

    with patch("builder_lab.experiments._write_evidence", side_effect=delayed):
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(write_experiment_package, output, manifest)
            assert entered.wait(timeout=5)
            output.mkdir()
            foreign_inode = output.stat().st_ino
            release.set()
            with pytest.raises(FileExistsError):
                future.result(timeout=15)

    assert output.is_dir()
    assert output.stat().st_ino == foreign_inode
    assert not tuple(output.iterdir())
    assert not tuple(tmp_path.glob(".direct-abc-foreign*"))


def test_failed_revision_is_packaged_as_rejected_evidence_not_accepted_final(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=(
            variant(CreativeProfile.PRODUCT_CHAT),
            variant(
                CreativeProfile.BRAND_MOTION,
                status="failed",
                rejected_final=True,
            ),
            variant(CreativeProfile.AI_CHARACTER),
        ),
    )
    output = tmp_path / "direct-abc-rejected"

    write_experiment_package(output, manifest)

    rejected = output / "brand-motion" / "rejected-final"
    assert (rejected / "index.html").is_file()
    assert (rejected / "critique.json").is_file()
    assert not (output / "brand-motion" / "final").exists()
    page = (output / "index.html").read_text(encoding="utf-8")
    assert "Rejected final" in page
    assert 'href="brand-motion/rejected-final/viewer.html"' in page
    assert 'href="brand-motion/rejected-final/"' not in page


def test_private_demo_registry_exports_only_strictly_accepted_final_artifacts(
    tmp_path: Path,
):
    failed = variant(
        CreativeProfile.BRAND_MOTION,
        status="failed",
        rejected_final=True,
    )
    base_request = request(CreativeProfile.BRAND_MOTION)
    accepted_variants = (
        variant(CreativeProfile.PRODUCT_CHAT),
        failed,
        variant(CreativeProfile.AI_CHARACTER),
    )
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest=canonical_common_input_digest(
            source_digest="a" * 64,
            request=base_request,
            model=accepted_variants[0].model,
            thinking=accepted_variants[0].thinking,
        ),
        contract_id="chat-v1",
        variants=accepted_variants,
    )
    output = tmp_path / "private-abc"
    chat_prompt = "Answer only from verified RAW BUREAU evidence."

    experiment_module.write_private_demo_registry(
        output,
        manifest,
        base_request=base_request,
        source_url="https://rawbureau.ru/",
        chat_system_prompt=chat_prompt,
    )

    product_path = output / "product-chat.json"
    character_path = output / "ai-character.json"
    assert product_path.is_file()
    assert character_path.is_file()
    assert not (output / "brand-motion.json").exists()
    assert set(path.name for path in output.iterdir()) == {
        "product-chat.json",
        "ai-character.json",
    }
    product = json.loads(product_path.read_text(encoding="utf-8"))
    accepted = manifest.variant(CreativeProfile.PRODUCT_CHAT)
    assert product["schema_version"] == 2
    assert product["model"] == accepted.model
    assert product["elapsed_seconds"] == accepted.elapsed_seconds
    assert product["usage"] == accepted.usage.to_dict()
    assert product["artifact"] == accepted.final.artifact.to_dict()
    assert product["request"]["creative_profile"] == "product_chat"
    assert product["chat_system_prompt"] == chat_prompt
    assert product["source_url"] == "https://rawbureau.ru/"
    if os.name != "nt":
        assert stat.S_IMODE(product_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(character_path.stat().st_mode) == 0o600
    assert not tuple(output.rglob("*raw*"))
    assert not tuple(output.rglob("*rejected*"))

    with pytest.raises(FileExistsError):
        experiment_module.write_private_demo_registry(
            output,
            manifest,
            base_request=request(),
            source_url="https://rawbureau.ru/",
            chat_system_prompt=chat_prompt,
        )


def test_private_demo_registry_rejects_a_mismatched_base_request(tmp_path: Path):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest=canonical_common_input_digest(
            source_digest="a" * 64,
            request=request(),
            model="gemini-3.6-flash",
            thinking="high",
        ),
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    output = tmp_path / "private-mismatch"

    with pytest.raises(ValueError, match="common input"):
        experiment_module.write_private_demo_registry(
            output,
            manifest,
            base_request=replace(request(), brief="Different brief"),
            source_url="https://rawbureau.ru/",
            chat_system_prompt="Verified prompt.",
        )

    assert not output.exists()


def test_private_demo_registry_rejects_a_mismatched_contract_before_writing(
    tmp_path: Path,
):
    base_request = request()
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest=canonical_common_input_digest(
            source_digest="a" * 64,
            request=base_request,
            model="gemini-3.6-flash",
            thinking="high",
        ),
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    output = tmp_path / "private-contract-mismatch"
    mismatched_request = request()
    object.__setattr__(
        mismatched_request,
        "contract_id",
        "corrupted-contract",
    )

    with pytest.raises(ValueError, match="contract"):
        experiment_module.write_private_demo_registry(
            output,
            manifest,
            base_request=mismatched_request,
            source_url="https://rawbureau.ru/",
            chat_system_prompt="Verified prompt.",
        )

    assert not output.exists()


def test_public_package_links_accepted_profiles_to_trusted_live_wrappers(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    output = tmp_path / "direct-abc-live"

    write_experiment_package(
        output,
        manifest,
        live_base_path="/builder-comparison/direct-abc-v1",
    )

    page = (output / "index.html").read_text(encoding="utf-8")
    assert (
        'href="/builder-comparison/direct-abc-v1/product-chat"' in page
    )
    assert (
        'src="/builder-comparison/direct-abc-v1/product-chat"' in page
    )
    assert 'href="product-chat/raw/viewer.html"' in page
    assert 'href="product-chat/final/viewer.html"' in page
    assert 'href="product-chat/raw/"' not in page
    assert 'href="product-chat/final/"' not in page
    assert page.count('sandbox="allow-scripts allow-same-origin"') == 3
    assert page.count('referrerpolicy="no-referrer"') == 6


def test_private_demo_cli_options_are_all_or_none_and_prompt_is_loaded_once(
    tmp_path: Path,
):
    prompt_file = tmp_path / "chat-system-prompt.txt"
    prompt_file.write_text("Verified RAW BUREAU chat prompt.", encoding="utf-8")
    partial = SimpleNamespace(
        private_demo_output_dir=tmp_path / "private",
        chat_system_prompt_file=None,
        source_url=None,
        live_demo_base_path=None,
    )
    with pytest.raises(SystemExit, match="must be supplied together"):
        abc_script.resolve_private_demo_options(partial)

    complete = SimpleNamespace(
        private_demo_output_dir=tmp_path / "private",
        chat_system_prompt_file=prompt_file,
        source_url="https://rawbureau.ru",
        live_demo_base_path="/builder-comparison/direct-abc-v1",
    )
    with patch(
        "scripts.run_abc_comparison._read_bounded_text",
        return_value="Verified RAW BUREAU chat prompt.",
    ) as read_prompt:
        options = abc_script.resolve_private_demo_options(complete)

    read_prompt.assert_called_once_with(prompt_file, limit=16_000)
    assert options.output_dir == tmp_path / "private"
    assert options.source_url == "https://rawbureau.ru/"
    assert options.chat_system_prompt == "Verified RAW BUREAU chat prompt."
    assert options.live_demo_base_path == "/builder-comparison/direct-abc-v1"


def test_publish_outputs_makes_private_registry_before_public_live_links(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    private = abc_script.PrivateDemoOptions(
        output_dir=tmp_path / "private",
        source_url="https://rawbureau.ru/",
        chat_system_prompt="Verified prompt.",
        live_demo_base_path="/builder-comparison/direct-abc-v1",
    )
    calls = []

    def write_private(path, manifest_value, **kwargs):
        calls.append(("private", path, kwargs))
        Path(path).mkdir()
        return Path(path)

    def write_public(path, manifest_value, **kwargs):
        calls.append(("public", path, kwargs))
        assert calls[0][0] == "private"
        return Path(path)

    with (
        patch(
            "scripts.run_abc_comparison.write_private_demo_registry",
            side_effect=write_private,
        ),
        patch(
            "scripts.run_abc_comparison.write_experiment_package",
            side_effect=write_public,
        ),
    ):
        public_output, private_output = abc_script.publish_experiment_outputs(
            public_output=tmp_path / "public",
            manifest=manifest,
            base_request=request(),
            private_options=private,
        )

    assert public_output == tmp_path / "public"
    assert private_output == tmp_path / "private"
    assert [call[0] for call in calls] == ["private", "public"]
    assert (
        calls[1][2]["live_base_path"]
        == "/builder-comparison/direct-abc-v1"
    )


def test_publish_outputs_rejects_equal_or_nested_public_private_paths(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    with patch(
        "scripts.run_abc_comparison.write_private_demo_registry"
    ) as write_private:
        for public_path, private_path in (
            (tmp_path / "same", tmp_path / "same"),
            (tmp_path / "public", tmp_path / "public" / "private"),
            (tmp_path / "private" / "public", tmp_path / "private"),
        ):
            with pytest.raises(ValueError, match="disjoint"):
                abc_script.publish_experiment_outputs(
                    public_output=public_path,
                    manifest=manifest,
                    base_request=request(),
                    private_options=abc_script.PrivateDemoOptions(
                        output_dir=private_path,
                        source_url="https://rawbureau.ru/",
                        chat_system_prompt="Verified.",
                        live_demo_base_path="/builder-comparison/direct-abc-v1",
                    ),
                )
        write_private.assert_not_called()


def test_publish_outputs_rolls_back_new_private_registry_when_public_fails(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    private_path = tmp_path / "private"

    def create_private(path, *args, **kwargs):
        Path(path).mkdir()
        (Path(path) / "product-chat.json").write_text("private", encoding="utf-8")
        return Path(path).resolve()

    with (
        patch(
            "scripts.run_abc_comparison.write_private_demo_registry",
            side_effect=create_private,
        ),
        patch(
            "scripts.run_abc_comparison.write_experiment_package",
            side_effect=RuntimeError("public failed"),
        ),
        pytest.raises(RuntimeError, match="public failed"),
    ):
        abc_script.publish_experiment_outputs(
            public_output=tmp_path / "public",
            manifest=manifest,
            base_request=request(),
            private_options=abc_script.PrivateDemoOptions(
                output_dir=private_path,
                source_url="https://rawbureau.ru/",
                chat_system_prompt="Verified.",
                live_demo_base_path="/builder-comparison/direct-abc-v1",
            ),
        )

    assert not private_path.exists()


def test_publish_rollback_does_not_remove_a_replaced_foreign_directory(
    tmp_path: Path,
):
    manifest = AbcExperimentManifest.create(
        source_digest="a" * 64,
        common_input_digest="b" * 64,
        contract_id="chat-v1",
        variants=tuple(variant(profile) for profile in ABC_PROFILES),
    )
    private_path = tmp_path / "private"

    def create_private(path, *args, **kwargs):
        Path(path).mkdir()
        return Path(path).resolve()

    def replace_then_fail(*args, **kwargs):
        private_path.rmdir()
        private_path.mkdir()
        (private_path / "foreign.txt").write_text("keep", encoding="utf-8")
        raise RuntimeError("public failed after replacement")

    with (
        patch(
            "scripts.run_abc_comparison.write_private_demo_registry",
            side_effect=create_private,
        ),
        patch(
            "scripts.run_abc_comparison.write_experiment_package",
            side_effect=replace_then_fail,
        ),
        pytest.raises(RuntimeError, match="after replacement"),
    ):
        abc_script.publish_experiment_outputs(
            public_output=tmp_path / "public",
            manifest=manifest,
            base_request=request(),
            private_options=abc_script.PrivateDemoOptions(
                output_dir=private_path,
                source_url="https://rawbureau.ru/",
                chat_system_prompt="Verified.",
                live_demo_base_path="/builder-comparison/direct-abc-v1",
            ),
        )

    assert (private_path / "foreign.txt").read_text(encoding="utf-8") == "keep"


def test_abc_cli_parser_exposes_opt_in_private_demo_group():
    args = abc_script.parser().parse_args(
        [
            "--bundle",
            "bundle",
            "--output",
            "public",
            "--prompt-price",
            "1.5",
            "--output-price",
            "7.5",
            "--thinking-price",
            "7.5",
            "--pricing-captured-at",
            "2026-07-24T00:00:00+00:00",
            "--private-demo-output-dir",
            "private",
            "--chat-system-prompt-file",
            "chat-prompt.txt",
            "--source-url",
            "https://rawbureau.ru/",
            "--live-demo-base-path",
            "/builder-comparison/direct-abc-v1",
        ]
    )

    assert args.private_demo_output_dir == Path("private")
    assert args.chat_system_prompt_file == Path("chat-prompt.txt")
    assert args.source_url == "https://rawbureau.ru/"
    assert (
        args.live_demo_base_path == "/builder-comparison/direct-abc-v1"
    )
