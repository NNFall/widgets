from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import scripts.run_refinement_workspace_benchmark as benchmark_module
from builder_lab.browser_audit import BrowserAuditReport, CapturedScreenshot
from builder_lab.comparison import verify_bundle
from builder_lab.models import AssistantPersona, Stage, WidgetArtifact
from builder_lab.refinement_motion import (
    ActionMotionEvidence,
    ReducedMotionEvidence,
    RefinementMotionReport,
    ViewportMotionEvidence,
)
from builder_lab.refinement_workspace import (
    CANONICAL_SCREENSHOT_NAMES,
    RefinementWorkspace,
)
from builder_lab.visual_models import (
    LayoutEvidence,
    LayoutState,
    ScreenshotEvidence,
    ScreenshotState,
)
from scripts.run_refinement_workspace_benchmark import (
    ArmSpec,
    BenchmarkError,
    EditorMetadata,
    ExternalAttestation,
    ExternalInvocationRecord,
    finalize_benchmark_bundle,
    freeze_finalized_benchmark,
    load_execution_attestations,
    load_finalized_benchmark,
    load_prepared_benchmark,
    parser,
    prepare_benchmark_bundle,
)


@pytest.fixture
def artifact() -> WidgetArtifact:
    return WidgetArtifact(
        schema_version="1.0",
        revision=5,
        stage=Stage.CONVERSATION,
        art_direction="Спокойный технологичный консультант RFN",
        body_html=(
            '<section class="kaigo-widget" data-region="root" '
            'aria-label="Консультант RFN">'
            '<button class="kaigo-widget__launcher" data-region="launcher" '
            'aria-label="Открыть консультанта" type="button">R</button>'
            '<div class="kaigo-widget__panel" data-region="panel" role="dialog" '
            'aria-label="Диалог с консультантом">'
            '<header class="kaigo-widget__header" data-region="header">'
            '<h2>Помощник RFN</h2></header>'
            '<main class="kaigo-widget__messages" data-region="messages" '
            'aria-live="polite"><p>Чем помочь?</p></main>'
            '<div class="kaigo-widget__suggestions" data-region="suggestions">'
            '<button type="button">Подобрать решение</button></div>'
            '<div class="kaigo-widget__composer" data-region="composer" '
            'role="group" aria-label="Сообщение">'
            '<input aria-label="Введите сообщение">'
            '<button type="button">Отправить</button></div></div></section>'
        ),
        css=(
            ".kaigo-widget { color: #18212b; position: relative; }\n"
            ".kaigo-widget .kaigo-widget__panel { border-radius: 20px; }"
        ),
        theme_tokens={"accent": "#f38b55"},
        suggested_actions=("Подобрать решение",),
        change_summary="Первая версия готова.",
        javascript="",
        layout_contract={"launcher": "bottom-right"},
    )


@pytest.fixture
def persona() -> AssistantPersona:
    return AssistantPersona(
        schema_version="kaigo.assistant-persona.v1",
        employee_type="consultant",
        display_name="Помощник RFN",
        role_summary="Консультирует клиентов RFN по услугам компании.",
        voice_style="professional",
        opening_line="Здравствуйте! Чем помочь?",
        behavior_rules=(
            "Отвечай кратко и по существу.",
            "Предлагай связаться со специалистом при сложном вопросе.",
        ),
        safeguards=("Не обещай неподтверждённые условия.",),
        decision_rationale="Соответствует деловому стилю RFN.",
    )


@pytest.fixture
def screenshots() -> tuple[tuple[str, bytes], ...]:
    return tuple(
        (name, b"\xff\xd8\xff" + name.encode("ascii") + b"\xff\xd9")
        for name in CANONICAL_SCREENSHOT_NAMES
    )


REQUEST = (
    "пусть будет название RFN Assistant а также анимацию при наведении "
    "на закрытый виджет поменяй, и сделай анимацию интересную закрытие виджета"
)


@lru_cache(maxsize=1)
def _browser_report() -> BrowserAuditReport:
    captured: list[CapturedScreenshot] = []
    for index, state in enumerate(ScreenshotState):
        width, height = (
            (1920, 1080)
            if state.value.startswith("desktop")
            else (390, 844)
        )
        output = io.BytesIO()
        Image.new("RGB", (width, height), (235 - index, 235, 235)).save(
            output,
            "JPEG",
            quality=80,
        )
        data = output.getvalue()
        captured.append(
            CapturedScreenshot(
                evidence=ScreenshotEvidence(
                    screenshot_id=state.value,
                    state=state,
                    sha256=hashlib.sha256(data).hexdigest(),
                    mime_type="image/jpeg",
                    byte_count=len(data),
                    width=width,
                    height=height,
                ),
                data=data,
            )
        )
    screenshot_ids = {
        item.evidence.state.value: item.evidence.screenshot_id for item in captured
    }
    layouts = tuple(
        LayoutEvidence(
            evidence_id=f"layout-{state.value}",
            state=state,
            screenshot_id=(
                None
                if state.value.endswith("after_turn_1")
                else screenshot_ids[state.value]
            ),
            viewport_width=(
                1920 if state.value.startswith("desktop") else 390
            ),
            viewport_height=(
                1080 if state.value.startswith("desktop") else 844
            ),
            panel_inside_viewport=True,
        )
        for state in LayoutState
    )
    return BrowserAuditReport(screenshots=tuple(captured), layouts=layouts)


@lru_cache(maxsize=1)
def _motion_report() -> RefinementMotionReport:
    close = ActionMotionEvidence(
        action="close",
        duration_ms=220,
        distinct_frame_count=3,
        frames=(),
        terminal_closed=True,
        reopens_with_transcript=True,
        reopens_with_draft=True,
    )
    hover = ActionMotionEvidence(
        action="hover",
        duration_ms=160,
        distinct_frame_count=3,
        frames=(),
        restored_baseline=True,
    )
    reduced = ReducedMotionEvidence(
        continuing_animation_count=0,
        checked_actions=("hover", "close"),
    )
    return RefinementMotionReport(
        assistant_label="RFN Assistant",
        viewports=(
            ViewportMotionEvidence(
                name="desktop",
                width=1920,
                height=1080,
                hover=hover,
                close=close,
                reduced_motion=reduced,
            ),
            ViewportMotionEvidence(
                name="mobile",
                width=390,
                height=844,
                hover=None,
                close=close,
                reduced_motion=reduced,
            ),
        ),
    )


async def _async_browser_gate(*_) -> BrowserAuditReport:
    return _browser_report()


async def _async_motion_gate(*_) -> RefinementMotionReport:
    return _motion_report()


def _invocation(spec: ArmSpec) -> ExternalInvocationRecord:
    return ExternalInvocationRecord(
        provider=spec.provider,
        model=spec.model,
        effort=spec.effort,
        invocation_id="00000000-0000-4000-8000-000000000001",
        event_log=b'{"event":"completed","scope":"workspace"}',
        tool_paths=("editable/widget.css", "editable/persona.json"),
        network_accesses=(),
        fallback_used=False,
        duration_ms=987,
        input_tokens=500,
        cached_input_tokens=200,
        output_tokens=120,
    )


def _controller_attestation(spec: ArmSpec, bundle):
    assert spec.execution == "docker-codex"
    loaded = load_execution_attestations(bundle)
    if spec.name not in loaded:
        benchmark_module._record_controller_attestation(
            bundle,
            bundle.arms[0],
            _invocation(spec),
        )
        loaded = load_execution_attestations(bundle)
    return loaded[spec.name]


def _prepare(tmp_path, artifact, persona, screenshots, *, arms=None):
    return prepare_benchmark_bundle(
        output=tmp_path / "evidence",
        source_artifact=artifact,
        source_persona=persona,
        request=REQUEST,
        screenshots=screenshots,
        arm_specs=arms,
        trusted_private_base=tmp_path,
    )


def _complete_candidate(arm, artifact, persona, *, summary=None) -> None:
    root = arm.root if isinstance(arm, RefinementWorkspace) else arm.workspace
    css = artifact.css + "\n.kaigo-widget__launcher:hover{transform:scale(1.06)}"
    (root / "editable" / "widget.css").write_text(css, encoding="utf-8")
    payload = persona.to_dict()
    payload["display_name"] = "RFN Assistant"
    (root / "editable" / "persona.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "editable" / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "refinement-result.v1",
                "status": "complete",
                "public_summary": summary
                or "Переименовал помощника и обновил анимацию наведения.",
                "changed_files": [
                    "editable/persona.json",
                    "editable/widget.css",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_benchmark_arms_share_inputs_and_never_import_project_mutations(
    tmp_path, artifact, persona, screenshots
):
    bundle = _prepare(tmp_path, artifact, persona, screenshots)

    assert {arm.input_digest for arm in bundle.arms} == {bundle.source_digest}
    assert all(
        (arm.workspace / "request.md").read_text(encoding="utf-8") == REQUEST
        for arm in bundle.arms
    )
    comparable = tuple(
        path.relative_to(bundle.arms[0].workspace).as_posix()
        for path in sorted(bundle.arms[0].workspace.rglob("*"))
        if path.is_file()
    )
    assert "manifest.json" in comparable
    for relative in comparable:
        assert len(
            {
                hashlib.sha256((arm.workspace / relative).read_bytes()).hexdigest()
                for arm in bundle.arms
            }
        ) == 1

    source = Path("scripts/run_refinement_workspace_benchmark.py").read_text(
        encoding="utf-8"
    )
    assert "from app." not in source
    assert "import app." not in source
    assert "project_id" not in source


def test_cli_rejects_project_ids_urls_and_tokens(tmp_path):
    command = [
        "--source-artifact",
        str(tmp_path / "artifact.json"),
        "--source-persona",
        str(tmp_path / "persona.json"),
        "--request",
        str(tmp_path / "request.txt"),
        "--screenshots",
        str(tmp_path / "screenshots"),
        "--output",
        str(tmp_path / "output"),
    ]
    with pytest.raises(SystemExit):
        parser().parse_args([*command, "--project-id", "46c6a329"])
    with pytest.raises(SystemExit):
        parser().parse_args(
            [*command[:1], "https://kaigo.space/api/projects/46c6a329", *command[2:]]
        )
    with pytest.raises(SystemExit):
        parser().parse_args([*command, "--token", "secret"])
    parsed = parser().parse_args([*command, "--mode", "finalize"])
    assert parsed.mode == "finalize"


def test_prepared_bundle_round_trips_external_receipts(
    tmp_path, artifact, persona, screenshots
):
    prepared = _prepare(tmp_path, artifact, persona, screenshots)

    loaded = load_prepared_benchmark(
        prepared.output,
        trusted_private_base=tmp_path,
    )

    assert loaded.source_digest == prepared.source_digest
    assert tuple(arm.spec for arm in loaded.arms) == tuple(
        arm.spec for arm in prepared.arms
    )
    assert tuple(arm.receipt for arm in loaded.arms) == tuple(
        arm.receipt for arm in prepared.arms
    )


@dataclass
class _FakeEditor:
    artifact: WidgetArtifact
    persona: AssistantPersona
    calls: int = 0
    summary: str | None = None

    async def run(self, *, workspace, timeout_seconds):
        assert timeout_seconds == 300
        assert isinstance(workspace, RefinementWorkspace)
        self.calls += 1
        _complete_candidate(
            workspace,
            self.artifact,
            self.persona,
            summary=self.summary,
        )
        return EditorMetadata(
            duration_ms=1234,
            input_tokens=800,
            cached_input_tokens=300,
            output_tokens=140,
        )


@dataclass
class _NoopEditor:
    calls: int = 0

    async def run(self, *, workspace, timeout_seconds):
        assert isinstance(workspace, RefinementWorkspace)
        assert timeout_seconds == 300
        self.calls += 1
        return EditorMetadata(duration_ms=10)


@pytest.mark.asyncio
async def test_provider_neutral_editor_is_one_shot_and_gates_authoritative_files(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="external-test",
        title="External test",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    editor = _FakeEditor(artifact, persona)
    browser_seen = []
    motion_seen = []

    async def browser_gate(candidate, candidate_persona):
        browser_seen.append((candidate, candidate_persona))
        return _browser_report()

    async def motion_gate(candidate, candidate_persona):
        motion_seen.append((candidate, candidate_persona))
        return _motion_report()

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: editor},
        browser_gate=browser_gate,
        motion_gate=motion_gate,
    )

    assert editor.calls == 1
    assert len(browser_seen) == len(motion_seen) == 1
    assert browser_seen[0][0].css.endswith("transform:scale(1.06)}")
    assert browser_seen[0][1].display_name == "RFN Assistant"
    receipt = report.results[0].receipt
    assert receipt == json.loads(report.results[0].receipt_path.read_text("utf-8"))
    assert receipt["status"] == "completed"
    assert receipt["provider"] == "test-provider"
    assert receipt["duration_ms"] == 1234
    assert receipt["usage"] == {
        "cached_input_tokens": 300,
        "input_tokens": 800,
        "output_tokens": 140,
    }
    assert receipt["changed_paths"] == [
        "editable/persona.json",
        "editable/widget.css",
    ]
    assert receipt["validation"] == {"status": "passed"}
    assert receipt["browser"] == {"status": "passed"}
    assert receipt["motion"] == {"status": "passed"}
    assert receipt["gate_evidence"]["mode"] == "injected"
    assert receipt["gate_evidence"]["candidate_digest"] == receipt[
        "candidate_digest"
    ]
    assert all(
        len(receipt["gate_evidence"][key]) == 64
        for key in ("browser_sha256", "motion_sha256")
    )
    assert receipt["attestation"] is None
    serialized = report.results[0].receipt_path.read_text("utf-8")
    assert "raw_model_output" not in serialized
    assert "final_text" not in serialized
    assert REQUEST not in serialized


@pytest.mark.asyncio
async def test_failed_arm_has_exact_receipt_and_visible_sandboxed_failure_card(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="external-failure",
        title="External failure",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: _NoopEditor()},
        browser_gate=_async_browser_gate,
        motion_gate=_async_motion_gate,
    )

    receipt = report.results[0].receipt
    assert receipt["status"] == "failed"
    assert receipt["failure"] == {
        "code": "result_not_complete",
        "public_message": "Кандидат не завершил локальную подготовку.",
    }
    assert receipt["candidate_digest"] is None
    assert receipt["changed_paths"] == []
    assert receipt["validation"] == {"status": "not_run"}
    assert receipt["browser"] == {"status": "not_run"}
    assert receipt["motion"] == {"status": "not_run"}
    page = report.comparison_index.read_text(encoding="utf-8")
    assert "External failure" in page
    assert "result_not_complete" in page
    assert 'sandbox="allow-scripts"' in page
    assert "allow-same-origin" not in page
    failure_card = (
        report.comparison_index.parent / "variants" / spec.name / "index.html"
    ).read_text(encoding="utf-8")
    assert "result_not_complete" in failure_card


@pytest.mark.asyncio
async def test_browser_failure_preserves_exact_gate_progress_without_running_motion(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="browser-failure",
        title="Browser failure",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    motion_calls = []

    async def browser_gate(*_):
        raise RuntimeError("private diagnostic that must not be copied")

    async def motion_gate(*_):
        motion_calls.append(True)
        return _motion_report()

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: _FakeEditor(artifact, persona)},
        browser_gate=browser_gate,
        motion_gate=motion_gate,
    )

    receipt = report.results[0].receipt
    assert receipt["failure"]["code"] == "browser_gate_failed"
    assert receipt["validation"] == {"status": "passed"}
    assert receipt["browser"] == {"status": "failed"}
    assert receipt["motion"] == {"status": "not_run"}
    assert receipt["candidate_digest"] is not None
    assert receipt["changed_paths"] == [
        "editable/persona.json",
        "editable/widget.css",
    ]
    assert receipt["public_summary"] == (
        "Переименовал помощника и обновил анимацию наведения."
    )
    assert "browser_gate_failed" in report.comparison_index.read_text("utf-8")
    assert motion_calls == []
    assert "private diagnostic" not in report.results[0].receipt_path.read_text("utf-8")


@pytest.mark.asyncio
async def test_codex_arm_rejects_non_docker_editor_without_calling_it(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="codex-test",
        title="Codex test",
        provider="openai-codex-cli",
        model="gpt-5.6-sol",
        effort="max",
        execution="docker-codex",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    editor = _FakeEditor(artifact, persona)

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: editor},
    )

    assert editor.calls == 0
    assert report.results[0].receipt["failure"]["code"] in {
        "unsafe_codex_editor",
        "docker_editor_unavailable",
    }


@pytest.mark.asyncio
async def test_codex_arm_never_imports_an_externally_completed_workspace(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="codex-unproven",
        title="Codex unproven",
        provider="openai-codex-cli",
        model="gpt-5.6-sol",
        effort="max",
        execution="docker-codex",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    _complete_candidate(bundle.arms[0], artifact, persona)

    report = await finalize_benchmark_bundle(
        bundle,
    )

    assert report.results[0].receipt["status"] == "failed"
    assert report.results[0].receipt["failure"]["code"] == "docker_editor_required"


@pytest.mark.asyncio
async def test_secret_like_public_output_fails_closed_without_copying_it_to_receipt(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="unsafe-output",
        title="Unsafe output",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    secret = "sk-proj-this-must-never-appear"
    report = await finalize_benchmark_bundle(
        bundle,
        editors={
            spec.name: _FakeEditor(
                artifact,
                persona,
                summary=f"Обновил виджет. {secret}",
            )
        },
    )

    receipt_bytes = report.results[0].receipt_path.read_bytes()
    assert secret.encode() not in receipt_bytes
    assert report.results[0].receipt["failure"]["code"] == "unsafe_public_output"


@pytest.mark.asyncio
async def test_model_cannot_copy_the_private_request_into_public_receipt(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="prompt-copy",
        title="Prompt copy",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: _FakeEditor(artifact, persona, summary=REQUEST)},
    )

    receipt_bytes = report.results[0].receipt_path.read_bytes()
    assert REQUEST.encode("utf-8") not in receipt_bytes
    assert report.results[0].receipt["failure"]["code"] == "unsafe_public_output"


def test_receipt_json_is_canonical_bounded_and_does_not_store_capabilities(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="canonical",
        title="Canonical",
        provider="test-provider",
        model="test-model",
        effort="medium",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    first = asyncio.run(
        finalize_benchmark_bundle(
            bundle,
            editors={spec.name: _FakeEditor(artifact, persona)},
            browser_gate=_async_browser_gate,
            motion_gate=_async_motion_gate,
        )
    )
    payload = first.results[0].receipt_path.read_bytes()

    assert len(payload) <= 64 * 1024
    assert first.results[0].receipt["attestation"] is None
    assert payload == (
        json.dumps(
            first.results[0].receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    lowered = payload.lower()
    assert b"authorization" not in lowered
    assert b"access_token" not in lowered
    assert b"api_key" not in lowered
    assert str(bundle.output).encode("utf-8") not in payload


def test_provider_identity_is_bound_to_its_execution_boundary():
    with pytest.raises(ValueError, match="Codex"):
        ArmSpec(
            name="codex-bypass",
            title="Codex bypass",
            provider="openai-codex-cli",
            model="gpt-5.6-sol",
            effort="max",
            execution="external",
        )
    with pytest.raises(ValueError, match="Antigravity"):
        ArmSpec(
            name="antigravity-bypass",
            title="Antigravity bypass",
            provider="antigravity-cli",
            model="gemini-3.7-flash-high",
            effort="high",
            execution="injected",
        )


def test_external_arm_cannot_mint_a_controller_proof_from_caller_data(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="unverified-external",
        title="Unverified external",
        provider="antigravity-cli",
        model="gemini-3.7-flash-high",
        effort="high",
        execution="external",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    _complete_candidate(bundle.arms[0], artifact, persona)

    with pytest.raises(BenchmarkError) as captured:
        benchmark_module._record_controller_attestation(
            bundle,
            bundle.arms[0],
            _invocation(spec),
        )

    assert captured.value.error_code == "external_attestation_unsupported"
    assert not (bundle.output / "control" / "execution-proofs").exists()


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "C:/Users/User/.codex/secrets/key",
        "C:relative-secret.txt",
        "https://kaigo.space/private",
        "file:///C:/private.txt",
    ),
)
def test_invocation_scope_rejects_windows_and_uri_paths(unsafe_path):
    with pytest.raises(BenchmarkError) as captured:
        benchmark_module._validate_invocation_scope(
            tool_paths=(unsafe_path,),
            network_accesses=(),
        )

    assert captured.value.error_code == "external_scope_violation"


@pytest.mark.asyncio
async def test_external_arm_requires_controller_attestation_and_never_falls_back(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="unattested-external",
        title="Unattested external",
        provider="antigravity-cli",
        model="gemini-3.7-flash-high",
        effort="high",
        execution="external",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    _complete_candidate(bundle.arms[0], artifact, persona)

    report = await finalize_benchmark_bundle(
        bundle,
    )

    receipt = report.results[0].receipt
    assert receipt["status"] == "failed"
    assert receipt["failure"]["code"] == "external_attestation_unsupported"
    assert receipt["duration_ms"] is None
    assert all(value is None for value in receipt["usage"].values())


@pytest.mark.asyncio
async def test_self_signed_external_proof_is_rejected_fail_closed(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="self-signed-external",
        title="Self-signed external",
        provider="antigravity-cli",
        model="gemini-3.7-flash-high",
        effort="high",
        execution="external",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    arm = bundle.arms[0]
    _complete_candidate(arm, artifact, persona)
    imported = benchmark_module.import_refinement_workspace(
        arm.workspace,
        receipt=arm.receipt,
    )
    candidate_digest, editable_hashes = benchmark_module._candidate_binding(
        arm,
        imported,
    )
    invocation = _invocation(spec)
    invocation_record = benchmark_module._invocation_record_payload(invocation)
    attestation = ExternalAttestation(
        arm_name=spec.name,
        execution="external",
        provider=spec.provider,
        model=spec.model,
        effort=spec.effort,
        input_digest=bundle.source_digest,
        challenge=arm.challenge,
        workspace_receipt_sha256=benchmark_module._workspace_receipt_digest(
            arm.receipt
        ),
        candidate_digest=candidate_digest,
        editable_hashes=editable_hashes,
        invocation_id=invocation.invocation_id,
        invocation_sha256=hashlib.sha256(
            benchmark_module._canonical_json_bytes(invocation_record)
        ).hexdigest(),
        duration_ms=invocation.duration_ms,
        input_tokens=invocation.input_tokens,
        cached_input_tokens=invocation.cached_input_tokens,
        output_tokens=invocation.output_tokens,
        model_verified=True,
        workspace_scope_verified=True,
        fallback_used=False,
    )
    key = b"x" * 32
    signed = benchmark_module._sign_external_attestation(attestation, key=key)
    proof_root = bundle.output / "control" / "execution-proofs" / spec.name
    proof_root.mkdir(parents=True)
    (proof_root / "verification.key").write_bytes(key)
    (proof_root / "invocation.json").write_bytes(
        benchmark_module._canonical_json_bytes(invocation_record) + b"\n"
    )
    (proof_root / "attestation.json").write_bytes(
        benchmark_module._canonical_json_bytes(
            benchmark_module._signed_attestation_dict(signed)
        )
        + b"\n"
    )

    report = await finalize_benchmark_bundle(bundle)

    assert report.results[0].receipt["status"] == "failed"
    assert report.results[0].receipt["failure"]["code"] == (
        "external_attestation_unsupported"
    )


def test_signed_docker_controller_proof_round_trips_and_tampering_is_detected(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="signed-docker",
        title="Signed Docker",
        provider="openai-codex-cli",
        model="gpt-5.6-sol",
        effort="max",
        execution="docker-codex",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    proof_root = bundle.output / "control" / "execution-proofs" / spec.name
    assert not proof_root.exists()
    _complete_candidate(bundle.arms[0], artifact, persona)
    signed = _controller_attestation(spec, bundle)
    path = (
        bundle.output
        / "control"
        / "execution-proofs"
        / spec.name
        / "attestation.json"
    )

    assert path.is_file()
    invocation_path = path.parent / "invocation.json"
    invocation_payload = json.loads(invocation_path.read_text("utf-8"))
    assert "event_log" not in invocation_payload
    assert invocation_payload["event_log_sha256"] == hashlib.sha256(
        _invocation(spec).event_log
    ).hexdigest()
    assert signed.attestation.invocation_sha256 == hashlib.sha256(
        json.dumps(
            invocation_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    loaded = load_execution_attestations(bundle)
    assert loaded == {spec.name: signed}
    invocation_bytes = invocation_path.read_bytes()
    invocation_payload["event_log_sha256"] = "0" * 64
    invocation_path.write_text(
        json.dumps(
            invocation_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkError) as captured:
        load_execution_attestations(bundle)
    assert captured.value.error_code == "external_attestation_mismatch"
    invocation_path.write_bytes(invocation_bytes)
    tampered = replace(signed, signature_sha256="0" * 64)
    report = asyncio.run(
        finalize_benchmark_bundle(
            bundle,
            external_attestations={spec.name: tampered},
        )
    )
    assert report.results[0].receipt["failure"]["code"] == (
        "external_attestation_invalid"
    )


@pytest.mark.asyncio
async def test_docker_controller_proof_cannot_cross_benchmark_roots(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="root-bound",
        title="Root bound",
        provider="openai-codex-cli",
        model="gpt-5.6-sol",
        effort="max",
        execution="docker-codex",
    )
    first = prepare_benchmark_bundle(
        output=tmp_path / "first",
        source_artifact=artifact,
        source_persona=persona,
        request=REQUEST,
        screenshots=screenshots,
        arm_specs=(spec,),
        trusted_private_base=tmp_path,
    )
    second = prepare_benchmark_bundle(
        output=tmp_path / "second",
        source_artifact=artifact,
        source_persona=persona,
        request=REQUEST,
        screenshots=screenshots,
        arm_specs=(spec,),
        trusted_private_base=tmp_path,
    )
    _complete_candidate(first.arms[0], artifact, persona)
    _complete_candidate(second.arms[0], artifact, persona)
    signed = _controller_attestation(spec, first)

    report = await finalize_benchmark_bundle(
        second,
        external_attestations={spec.name: signed},
    )

    assert report.results[0].receipt["failure"]["code"] == (
        "external_attestation_mismatch"
    )


@pytest.mark.asyncio
async def test_injected_gate_must_return_explicit_matching_evidence(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="invalid-gate",
        title="Invalid gate",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    _complete_candidate(bundle.arms[0], artifact, persona)
    editor = _FakeEditor(artifact, persona)

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: editor},
        browser_gate=lambda *_: SimpleNamespace(status="passed"),
        motion_gate=lambda *_: _motion_report(),
    )

    assert report.results[0].receipt["failure"]["code"] == "browser_gate_failed"
    assert report.results[0].receipt["browser"] == {"status": "failed"}


@dataclass
class _HangingEditor:
    cancelled: bool = False

    async def run(self, *, workspace, timeout_seconds):
        del workspace, timeout_seconds
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


@pytest.mark.asyncio
async def test_hanging_editor_is_cancelled_by_harness_deadline(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="hanging-editor",
        title="Hanging editor",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    editor = _HangingEditor()

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: editor},
        browser_gate=lambda *_: _browser_report(),
        motion_gate=lambda *_: _motion_report(),
        editor_timeout_seconds=0.02,
    )

    assert editor.cancelled is True
    assert report.results[0].receipt["failure"]["code"] == "editor_timeout"


@dataclass
class _HangingGate:
    cancelled: bool = False

    async def __call__(self, *_):
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled = True


@pytest.mark.asyncio
async def test_hanging_browser_gate_is_cancelled_by_harness_deadline(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="hanging-gate",
        title="Hanging gate",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    browser_gate = _HangingGate()

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: _FakeEditor(artifact, persona)},
        browser_gate=browser_gate,
        motion_gate=lambda *_: _motion_report(),
        gate_timeout_seconds=0.02,
    )

    assert browser_gate.cancelled is True
    assert report.results[0].receipt["failure"]["code"] == "browser_gate_failed"
    assert report.results[0].receipt["browser"] == {"status": "failed"}


@pytest.mark.asyncio
async def test_secret_fingerprint_anywhere_in_candidate_fails_before_gates(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="candidate-secret",
        title="Candidate secret",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    _complete_candidate(bundle.arms[0], artifact, persona)
    secret = "sk-proj-secret-in-css-must-not-escape"
    css_path = bundle.arms[0].workspace / "editable" / "widget.css"
    css_path.write_text(css_path.read_text("utf-8") + f"\n/* {secret} */", "utf-8")
    gate_calls = []

    async def browser_gate(*_):
        gate_calls.append("browser")
        return _browser_report()

    async def motion_gate(*_):
        gate_calls.append("motion")
        return _motion_report()

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: _NoopEditor()},
        browser_gate=browser_gate,
        motion_gate=motion_gate,
    )

    assert gate_calls == []
    assert report.results[0].receipt["failure"]["code"] == "unsafe_public_output"
    assert secret not in report.results[0].receipt_path.read_text("utf-8")


def test_persisted_control_hardlink_is_rejected(
    tmp_path, artifact, persona, screenshots
):
    bundle = _prepare(tmp_path, artifact, persona, screenshots)
    control_manifest = bundle.output / "control" / "bundle.json"
    extra_link = bundle.output / "control" / "bundle-copy.json"
    try:
        os.link(control_manifest, extra_link)
    except OSError as exc:  # pragma: no cover - filesystem capability dependent
        pytest.skip(f"hardlinks unavailable: {exc}")
    try:
        with pytest.raises(BenchmarkError) as captured:
            load_prepared_benchmark(bundle.output, trusted_private_base=tmp_path)
        assert captured.value.error_code == "unsafe_input"
    finally:
        extra_link.unlink()


@pytest.mark.asyncio
async def test_receipt_directory_symlink_is_rejected_before_any_write(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="symlink-output",
        title="Symlink output",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    outside = tmp_path / "outside-receipts"
    outside.mkdir()
    receipt_link = bundle.output / "evidence"
    try:
        receipt_link.symlink_to(outside, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows developer-mode dependent
        pytest.skip(f"directory symlinks unavailable: {exc}")
    try:
        with pytest.raises(BenchmarkError) as captured:
            await finalize_benchmark_bundle(
                bundle,
                editors={spec.name: _FakeEditor(artifact, persona)},
                browser_gate=_async_browser_gate,
                motion_gate=_async_motion_gate,
            )
        assert captured.value.error_code == "unsafe_output"
        assert list(outside.iterdir()) == []
    finally:
        receipt_link.unlink()


def test_single_handle_read_rejects_post_open_identity_change(tmp_path, monkeypatch):
    path = tmp_path / "input.json"
    path.write_text("{}", encoding="utf-8")
    real_lstat = Path.lstat

    def changed_identity(candidate: Path):
        info = real_lstat(candidate)
        if candidate == path:
            return SimpleNamespace(
                st_dev=info.st_dev,
                st_ino=info.st_ino + 1,
                st_mode=info.st_mode,
                st_nlink=info.st_nlink,
                st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns,
            )
        return info

    monkeypatch.setattr(Path, "lstat", changed_identity)

    with pytest.raises(BenchmarkError) as captured:
        benchmark_module._read_regular_bytes(path, limit=128)

    assert captured.value.error_code == "input_changed_during_read"


def test_cli_input_symlink_is_not_resolved_away(
    tmp_path, artifact, persona, screenshots
):
    real_artifact = tmp_path / "artifact.json"
    real_artifact.write_text(
        json.dumps(artifact.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    artifact_link = tmp_path / "artifact-link.json"
    try:
        artifact_link.symlink_to(real_artifact)
    except OSError as exc:  # pragma: no cover - Windows developer-mode dependent
        pytest.skip(f"file symlinks unavailable: {exc}")
    persona_path = tmp_path / "persona.json"
    persona_path.write_text(
        json.dumps(persona.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    request_path = tmp_path / "request.md"
    request_path.write_text(REQUEST, encoding="utf-8")
    screenshots_root = tmp_path / "screenshots"
    screenshots_root.mkdir()
    for name, payload in screenshots:
        (screenshots_root / f"{name}.jpg").write_bytes(payload)

    args = SimpleNamespace(
        source_artifact=artifact_link,
        source_persona=persona_path,
        request=request_path,
        screenshots=screenshots_root,
    )
    with pytest.raises(BenchmarkError) as captured:
        benchmark_module._load_cli_inputs(args)

    assert captured.value.error_code in {"unsafe_input", "unsafe_output"}


def test_existing_output_symlink_is_rejected(
    tmp_path, artifact, persona, screenshots
):
    real_output = tmp_path / "real-evidence"
    prepare_benchmark_bundle(
        output=real_output,
        source_artifact=artifact,
        source_persona=persona,
        request=REQUEST,
        screenshots=screenshots,
        trusted_private_base=tmp_path,
    )
    linked_output = tmp_path / "linked-evidence"
    try:
        linked_output.symlink_to(real_output, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - Windows developer-mode dependent
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(BenchmarkError) as captured:
        load_prepared_benchmark(linked_output, trusted_private_base=tmp_path)

    assert captured.value.error_code == "unsafe_output"


@pytest.mark.asyncio
async def test_refinalize_uses_a_persisted_docker_controller_proof(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="docker-refinalize",
        title="Docker refinalize",
        provider="openai-codex-cli",
        model="gpt-5.6-sol",
        effort="max",
        execution="docker-codex",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    first = await finalize_benchmark_bundle(
        bundle,
    )
    assert first.results[0].receipt["failure"]["code"] == (
        "docker_editor_required"
    )

    _complete_candidate(bundle.arms[0], artifact, persona)
    attestation = {spec.name: _controller_attestation(spec, bundle)}
    second = await finalize_benchmark_bundle(
        bundle,
        external_attestations=attestation,
        browser_gate=_async_browser_gate,
        motion_gate=_async_motion_gate,
        allow_injected_gates=True,
    )

    assert second.results[0].receipt["status"] == "completed"
    assert second.comparison_index != first.comparison_index
    assert second.comparison_index.is_file()


@pytest.mark.asyncio
async def test_docker_controller_proof_cannot_be_replayed_after_candidate_mutation(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="docker-replay",
        title="Docker replay",
        provider="openai-codex-cli",
        model="gpt-5.6-sol",
        effort="max",
        execution="docker-codex",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    _complete_candidate(bundle.arms[0], artifact, persona)
    signed = _controller_attestation(spec, bundle)
    css_path = bundle.arms[0].workspace / "editable" / "widget.css"
    css_path.write_text(
        css_path.read_text("utf-8") + "\n.kaigo-widget{outline:0}",
        "utf-8",
    )

    report = await finalize_benchmark_bundle(
        bundle,
        external_attestations={spec.name: signed},
        browser_gate=_async_browser_gate,
        motion_gate=_async_motion_gate,
        allow_injected_gates=True,
    )

    assert report.results[0].receipt["status"] == "failed"
    assert report.results[0].receipt["failure"]["code"] == (
        "external_attestation_mismatch"
    )


@pytest.mark.asyncio
async def test_frozen_state_preserves_receipts_and_top_level_manifest(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="frozen-evidence",
        title="Frozen evidence",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    first = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: _FakeEditor(artifact, persona)},
        browser_gate=_async_browser_gate,
        motion_gate=_async_motion_gate,
    )
    first_receipt_path = first.results[0].receipt_path
    first_receipt = first_receipt_path.read_bytes()

    second = await finalize_benchmark_bundle(
        bundle,
        editors={
            spec.name: _FakeEditor(
                artifact,
                persona,
                summary="Второй локальный evidence state.",
            )
        },
        browser_gate=_async_browser_gate,
        motion_gate=_async_motion_gate,
    )

    assert first_receipt_path.is_file()
    assert first_receipt_path.read_bytes() == first_receipt
    assert first_receipt_path != second.results[0].receipt_path
    assert verify_bundle(first.comparison_index.parent)
    assert (
        first.comparison_index.parent / "receipts" / f"{spec.name}.json"
    ).read_bytes() == first_receipt


def test_final_component_symlink_is_rejected_before_open(
    tmp_path, monkeypatch
):
    target = tmp_path / "target.txt"
    target.write_text("private-target", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:  # pragma: no cover - Windows developer-mode dependent
        pytest.skip(f"file symlinks unavailable: {exc}")
    real_open = os.open
    opened: list[Path] = []

    def observed_open(path, *args, **kwargs):
        opened.append(Path(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", observed_open)
    with pytest.raises(BenchmarkError):
        benchmark_module._read_regular_bytes(link, limit=128)

    assert opened == []


@pytest.mark.asyncio
async def test_synchronous_gate_is_rejected_without_invocation(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="sync-gate",
        title="Sync gate",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    called: list[bool] = []

    def synchronous_gate(*_):
        called.append(True)
        return _browser_report()

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: _FakeEditor(artifact, persona)},
        browser_gate=synchronous_gate,
        motion_gate=_async_motion_gate,
    )

    assert called == []
    assert report.results[0].receipt["failure"]["code"] == "browser_gate_failed"


@pytest.mark.asyncio
async def test_synchronous_editor_is_rejected_without_invocation(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="sync-editor",
        title="Sync editor",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    called: list[bool] = []

    class SynchronousEditor:
        def run(self, **_):
            called.append(True)
            return EditorMetadata()

    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: SynchronousEditor()},
        browser_gate=_async_browser_gate,
        motion_gate=_async_motion_gate,
    )

    assert called == []
    assert report.results[0].receipt["failure"]["code"] == "invalid_async_editor"


@pytest.mark.asyncio
async def test_finalize_and_freeze_are_separate_reusable_phases(
    tmp_path, artifact, persona, screenshots
):
    spec = ArmSpec(
        name="phase-separation",
        title="Phase separation",
        provider="test-provider",
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )
    report = await finalize_benchmark_bundle(
        bundle,
        editors={spec.name: _FakeEditor(artifact, persona)},
        browser_gate=_async_browser_gate,
        motion_gate=_async_motion_gate,
        freeze_comparison=False,
    )

    assert report.comparison_index is None
    assert verify_bundle(report.evidence_root)
    evidence_root, evidence_results = load_finalized_benchmark(bundle)
    frozen_candidate_digest = evidence_results[0].receipt["candidate_digest"]
    live_css = bundle.arms[0].workspace / "editable" / "widget.css"
    live_css.write_text(live_css.read_text("utf-8") + "\n.changed-after-finalize{}", "utf-8")

    comparison_index = freeze_finalized_benchmark(bundle)

    assert evidence_root == report.evidence_root
    assert verify_bundle(comparison_index.parent)
    assert evidence_results[0].receipt["candidate_digest"] == frozen_candidate_digest
    variant = (
        comparison_index.parent / "variants" / spec.name / "index.html"
    ).read_text("utf-8")
    assert "scale(1.06)" in variant
    assert "changed-after-finalize" not in variant


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_existing_evidence_root_must_be_private(
    tmp_path, artifact, persona, screenshots
):
    bundle = _prepare(tmp_path, artifact, persona, screenshots)
    bundle.output.chmod(0o755)

    with pytest.raises(BenchmarkError) as captured:
        load_prepared_benchmark(bundle.output, trusted_private_base=tmp_path)

    assert captured.value.error_code == "unsafe_output"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission contract")
def test_nonsticky_world_writable_parent_is_rejected(
    tmp_path, artifact, persona, screenshots
):
    unsafe_parent = tmp_path / "unsafe-parent"
    unsafe_parent.mkdir(mode=0o777)
    unsafe_parent.chmod(0o777)

    with pytest.raises(BenchmarkError) as captured:
        prepare_benchmark_bundle(
            output=unsafe_parent / "evidence",
            source_artifact=artifact,
            source_persona=persona,
            request=REQUEST,
            screenshots=screenshots,
            trusted_private_base=tmp_path,
        )

    assert captured.value.error_code == "unsafe_output"


@pytest.mark.asyncio
async def test_forbidden_fingerprint_in_receipt_identity_is_never_written(
    tmp_path, artifact, persona, screenshots
):
    fingerprint = b"controller-private-capability"
    spec = ArmSpec(
        name="unsafe-identity",
        title="Unsafe identity",
        provider=fingerprint.decode("ascii"),
        model="test-model",
        effort="high",
        execution="injected",
    )
    bundle = _prepare(
        tmp_path,
        artifact,
        persona,
        screenshots,
        arms=(spec,),
    )

    with pytest.raises(BenchmarkError) as captured:
        await finalize_benchmark_bundle(
            bundle,
            editors={spec.name: _FakeEditor(artifact, persona)},
            browser_gate=lambda *_: _browser_report(),
            motion_gate=lambda *_: _motion_report(),
            forbidden_fingerprints=(fingerprint,),
        )

    assert captured.value.error_code == "unsafe_public_output"
    for root_name in ("receipts", "comparison"):
        root = bundle.output / root_name
        if root.exists():
            assert all(
                fingerprint not in path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            )
