import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from builder_lab.experiments import (
    ABC_PROFILES,
    AbcExperimentManifest,
    ExperimentEvidence,
    ExperimentPricingSnapshot,
    ExperimentRoleEvent,
    ExperimentVariant,
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
from scripts.run_abc_comparison import (
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
    assert 'href="brand-motion/rejected-final/"' in page
