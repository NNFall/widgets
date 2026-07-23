import asyncio
import json
from dataclasses import replace
from pathlib import Path

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
    CreativeProfile,
    EngineName,
    TokenUsage,
)
from tests.builder_lab_cases.test_experiment_review import visual_critique
from tests.builder_lab_cases.test_validation import artifact
from tests.builder_lab_cases.test_visual_critic import report


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
        raw=raw if status == "completed" else None,
        final=final if status == "completed" else None,
        usage=TokenUsage(
            prompt_tokens=1_000,
            output_tokens=200,
            thinking_tokens=300,
        ),
        elapsed_seconds=12.5,
        pricing=pricing(),
        role_events=(
            ExperimentRoleEvent(
                role="site_brand_analyst",
                status="completed",
                summary="Brand evidence extracted.",
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


def test_failed_variant_is_kept_without_fabricated_evidence():
    item = variant(CreativeProfile.AI_CHARACTER, status="failed")

    assert item.raw is None and item.final is None
    assert item.error_code == "provider_unavailable"
    assert item.to_dict()["status"] == "failed"


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
    async def execute(context):
        if context.request.creative_profile is CreativeProfile.BRAND_MOTION:
            raise VariantExecutionError(
                "provider_unavailable",
                "Gemini did not complete this profile.",
                usage=TokenUsage(prompt_tokens=77),
                elapsed_seconds=2.5,
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
    assert (sibling / "sentinel.txt").read_text(encoding="utf-8") == "keep"
    with pytest.raises(FileExistsError):
        write_experiment_package(output, manifest)
