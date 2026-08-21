from __future__ import annotations

import json
import os
from dataclasses import replace
import subprocess
import sys
from types import SimpleNamespace

import pytest

import builder_lab.refinement_workspace as refinement_workspace_module
from builder_lab.models import AssistantPersona, Stage, WidgetArtifact
from builder_lab.refinement_workspace import (
    CANONICAL_SCREENSHOT_NAMES,
    FrozenWorkspaceReceipt,
    RefinementWorkspaceError,
    create_refinement_workspace,
    import_refinement_workspace,
)


SCREENSHOT_NAMES = (
    "desktop.closed",
    "desktop.open_initial",
    "desktop.after_turn_2",
    "mobile.closed",
    "mobile.open_initial",
    "mobile.after_turn_2",
)


@pytest.fixture
def artifact() -> WidgetArtifact:
    return WidgetArtifact(
        schema_version="1.0",
        revision=5,
        stage=Stage.CONVERSATION,
        art_direction="Спокойный технологичный консультант RFN",
        body_html="""
<section class="kaigo-widget" data-region="root" aria-label="Консультант RFN">
  <button class="kaigo-widget__launcher" data-region="launcher" aria-label="Открыть консультанта" type="button">R</button>
  <div class="kaigo-widget__panel" data-region="panel" role="dialog" aria-label="Диалог с консультантом">
    <header class="kaigo-widget__header" data-region="header"><h2>Помощник RFN</h2></header>
    <main class="kaigo-widget__messages" data-region="messages" aria-live="polite"><p>Чем помочь?</p></main>
    <div class="kaigo-widget__suggestions" data-region="suggestions"><button type="button">Подобрать решение</button></div>
    <div class="kaigo-widget__composer" data-region="composer" role="group" aria-label="Сообщение"><input aria-label="Введите сообщение"><button type="button">Отправить</button></div>
  </div>
</section>
""".strip(),
        css="""
.kaigo-widget { color: #18212b; position: relative; }
.kaigo-widget .kaigo-widget__panel { border-radius: 20px; }
""".strip(),
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
        (name, b"\xff\xd8\xff" + name.encode("ascii"))
        for name in SCREENSHOT_NAMES
    )


def _write_result(editable, changed_files: list[str]) -> None:
    (editable / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "refinement-result.v1",
                "status": "complete",
                "public_summary": (
                    "Переименовал помощника и обновил анимацию наведения."
                ),
                "changed_files": changed_files,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_workspace_round_trip_imports_complete_candidate(
    tmp_path, artifact, persona, screenshots
):
    workspace = create_refinement_workspace(
        root=tmp_path / "case",
        source_artifact=artifact,
        source_persona=persona,
        change_request="Переименуй помощника в RFN Assistant",
        screenshots=screenshots,
        candidate_revision=artifact.revision + 1,
    )
    (workspace.editable / "widget.css").write_text(
        artifact.css
        + "\n.kaigo-widget .kaigo-widget__launcher:hover{transform:scale(1.04)}",
        encoding="utf-8",
    )
    persona_payload = persona.to_dict()
    persona_payload["display_name"] = "RFN Assistant"
    (workspace.editable / "persona.json").write_text(
        json.dumps(persona_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_result(
        workspace.editable,
        ["editable/persona.json", "editable/widget.css"],
    )

    result = import_refinement_workspace(
        workspace.root,
        receipt=workspace.receipt,
    )

    assert result.persona.display_name == "RFN Assistant"
    assert result.artifact.revision == artifact.revision + 1
    assert result.artifact.css.endswith("transform:scale(1.04)}")
    assert result.artifact.change_summary == result.public_summary
    assert result.changed_paths == (
        "editable/persona.json",
        "editable/widget.css",
    )


def test_workspace_materializes_only_bounded_contract_files(
    tmp_path, artifact, persona, screenshots
):
    workspace = create_refinement_workspace(
        root=tmp_path / "case",
        source_artifact=artifact,
        source_persona=persona,
        change_request="Переименуй помощника в RFN Assistant",
        screenshots=screenshots,
        candidate_revision=artifact.revision + 1,
    )

    assert isinstance(workspace.receipt, FrozenWorkspaceReceipt)
    assert workspace.receipt.root == workspace.root
    manifest = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    assert manifest["validation_command"] == "python validate.py"
    assert (workspace.root / "validate.py").is_file()
    assert sorted(path.name for path in (workspace.root / "validator_lib").iterdir()) == [
        "__init__.py",
        "contracts.py",
        "css_contract.py",
        "models.py",
        "validation.py",
    ]
    assert {
        f"validator_lib/{name}"
        for name in (
            "__init__.py",
            "contracts.py",
            "css_contract.py",
            "models.py",
            "validation.py",
        )
    }.issubset(manifest["immutable_hashes"])
    assert tuple(path.stem for path in sorted((workspace.root / "screenshots").iterdir())) == tuple(
        sorted(CANONICAL_SCREENSHOT_NAMES)
    )
    assert sorted(path.name for path in workspace.editable.iterdir()) == [
        "persona.json",
        "result.json",
        "widget.css",
        "widget.html",
        "widget.js",
    ]
    assert not (workspace.editable / "widget.meta.json").exists()


def _run_preflight(workspace) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "validate.py"],
        cwd=workspace.root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_editor_preflight_accepts_a_valid_complete_edit(
    tmp_path, artifact, persona, screenshots
):
    workspace = _make_workspace(tmp_path, artifact, persona, screenshots)
    (workspace.editable / "widget.css").write_text(
        artifact.css + "\n.kaigo-widget { opacity: .99; }",
        encoding="utf-8",
    )
    _complete_result(workspace, changed_files=["editable/widget.css"])

    completed = _run_preflight(workspace)

    assert completed.returncode == 0, completed.stderr
    assert "passed" in completed.stdout.lower()


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_file", "missing_file"),
        ("non_utf8", "invalid_utf8"),
        ("invalid_json", "invalid_json"),
        ("incomplete_result", "result_not_complete"),
        ("javascript_changed", "immutable_javascript"),
        ("reserved_runtime_attribute", "reserved_runtime_attribute"),
        ("changed_files_mismatch", "changed_files_mismatch"),
        ("no_changes", "no_changes"),
        ("employee_type_changed", "immutable_persona_field"),
    ],
)
def test_editor_preflight_rejects_invalid_edit_or_result(
    tmp_path, artifact, persona, screenshots, mutation, expected_code
):
    workspace = _make_workspace(
        tmp_path,
        artifact,
        persona,
        screenshots,
        name=f"preflight-{mutation}",
    )
    if mutation == "missing_file":
        (workspace.editable / "widget.css").unlink()
    elif mutation == "non_utf8":
        (workspace.editable / "widget.css").write_bytes(b"\xff\xfe")
        _complete_result(workspace, changed_files=["editable/widget.css"])
    elif mutation == "invalid_json":
        (workspace.editable / "persona.json").write_text("{", encoding="utf-8")
        _complete_result(workspace, changed_files=["editable/persona.json"])
    elif mutation == "incomplete_result":
        pass
    else:
        _mutate_workspace(workspace, mutation, artifact, persona)

    completed = _run_preflight(workspace)

    assert completed.returncode != 0
    assert completed.stderr.startswith(f"preflight_error:{expected_code}:")


def test_import_rejects_tampered_editor_preflight(
    tmp_path, artifact, persona, screenshots
):
    workspace = _make_workspace(tmp_path, artifact, persona, screenshots)
    (workspace.root / "validate.py").write_text(
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )

    with pytest.raises(RefinementWorkspaceError) as captured:
        _import(workspace)

    assert captured.value.error_code == "immutable_hash_mismatch"


def test_import_rejects_tampered_validator_library(
    tmp_path, artifact, persona, screenshots
):
    workspace = _make_workspace(tmp_path, artifact, persona, screenshots)
    (workspace.root / "validator_lib" / "validation.py").write_text(
        "def validate_artifact(*args, **kwargs): return ()\n",
        encoding="utf-8",
    )

    with pytest.raises(RefinementWorkspaceError) as captured:
        _import(workspace)

    assert captured.value.error_code == "immutable_hash_mismatch"


def test_posix_owner_guard_rejects_a_different_uid(monkeypatch):
    monkeypatch.setattr(
        refinement_workspace_module,
        "_POSIX_OWNER_CHECK",
        True,
    )
    monkeypatch.setattr(
        refinement_workspace_module,
        "_expected_uid",
        lambda: 1000,
    )

    with pytest.raises(RefinementWorkspaceError) as captured:
        refinement_workspace_module._ensure_expected_owner(
            SimpleNamespace(st_uid=1001),
            "editable/widget.css",
        )

    assert captured.value.error_code == "unexpected_owner"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("english_summary", "invalid_public_summary"),
        ("oversize_summary", "invalid_public_summary"),
        ("persona_normalization_only", "no_changes"),
        ("unscoped_css", "invalid_artifact"),
        ("invalid_html", "invalid_artifact"),
    ],
)
def test_editor_preflight_matches_authoritative_importer_semantics(
    tmp_path,
    artifact,
    persona,
    screenshots,
    mutation,
    expected_code,
):
    workspace = _make_workspace(
        tmp_path,
        artifact,
        persona,
        screenshots,
        name=f"semantic-{mutation}",
    )
    if mutation in {"english_summary", "oversize_summary"}:
        (workspace.editable / "widget.css").write_text(
            artifact.css + "\n.kaigo-widget { opacity: .99; }",
            encoding="utf-8",
        )
        _complete_result(
            workspace,
            changed_files=["editable/widget.css"],
            public_summary=(
                "Updated the widget."
                if mutation == "english_summary"
                else "Обновил " + ("виджет " * 500)
            ),
        )
    elif mutation == "persona_normalization_only":
        _replace_persona(
            workspace,
            persona,
            display_name=f"  {persona.display_name}  ",
        )
        _complete_result(workspace, changed_files=["editable/persona.json"])
    elif mutation == "unscoped_css":
        (workspace.editable / "widget.css").write_text(
            artifact.css + "\nbody { color: red; }",
            encoding="utf-8",
        )
        _complete_result(workspace, changed_files=["editable/widget.css"])
    elif mutation == "invalid_html":
        (workspace.editable / "widget.html").write_text(
            artifact.body_html.replace(' data-region="composer"', "", 1),
            encoding="utf-8",
        )
        _complete_result(workspace, changed_files=["editable/widget.html"])

    preflight = _run_preflight(workspace)

    assert preflight.returncode != 0
    assert preflight.stderr.startswith(f"preflight_error:{expected_code}:")
    with pytest.raises(RefinementWorkspaceError) as captured:
        _import(workspace)
    assert captured.value.error_code == expected_code


def test_explicit_private_base_rejects_workspace_escape(
    tmp_path, artifact, persona, screenshots
):
    trusted_base = tmp_path / "trusted"
    trusted_base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(RefinementWorkspaceError) as captured:
        create_refinement_workspace(
            root=outside / "case",
            trusted_private_base=trusted_base,
            source_artifact=artifact,
            source_persona=persona,
            change_request="Переименуй помощника",
            screenshots=screenshots,
            candidate_revision=artifact.revision + 1,
        )

    assert captured.value.error_code == "unsafe_private_root"


def test_private_parent_mode_policy_is_portable():
    assert refinement_workspace_module._mode_is_private_or_sticky(
        0o700,
        owned_by_service=True,
    )
    assert refinement_workspace_module._mode_is_private_or_sticky(
        0o1777,
        owned_by_service=False,
    )
    assert not refinement_workspace_module._mode_is_private_or_sticky(
        0o755,
        owned_by_service=True,
    )
    assert not refinement_workspace_module._mode_is_private_or_sticky(
        0o777,
        owned_by_service=False,
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission-bit contract")
def test_posix_workspace_uses_private_directory_and_file_modes(
    tmp_path, artifact, persona, screenshots
):
    workspace = _make_workspace(tmp_path, artifact, persona, screenshots)

    directory_paths = [
        workspace.root,
        workspace.editable,
        workspace.root / "screenshots",
        workspace.root / "scratch",
        workspace.root / "validator_lib",
    ]
    file_paths = [
        path
        for path in workspace.root.rglob("*")
        if path.is_file()
    ]
    assert all((path.stat().st_mode & 0o777) == 0o700 for path in directory_paths)
    assert all((path.stat().st_mode & 0o777) == 0o600 for path in file_paths)


@pytest.mark.skipif(os.name != "nt", reason="Windows private-temp boundary")
def test_windows_default_root_is_scoped_to_localappdata_temp(
    monkeypatch, tmp_path, artifact, persona, screenshots
):
    local_app_data = tmp_path / "profile"
    private_temp = local_app_data / "Temp"
    private_temp.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    with pytest.raises(RefinementWorkspaceError) as captured:
        create_refinement_workspace(
            root=outside / "case",
            source_artifact=artifact,
            source_persona=persona,
            change_request="Переименуй помощника",
            screenshots=screenshots,
            candidate_revision=artifact.revision + 1,
        )

    assert captured.value.error_code == "unsafe_private_root"


def _complete_result(
    workspace,
    *,
    changed_files: list[str],
    public_summary: str = "Обновил виджет по запросу.",
) -> None:
    (workspace.editable / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "refinement-result.v1",
                "status": "complete",
                "public_summary": public_summary,
                "changed_files": changed_files,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _replace_persona(workspace, persona, **changes) -> None:
    payload = persona.to_dict()
    payload.update(changes)
    (workspace.editable / "persona.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _make_workspace(tmp_path, artifact, persona, screenshots, *, name="case"):
    return create_refinement_workspace(
        root=tmp_path / name,
        source_artifact=artifact,
        source_persona=persona,
        change_request="Переименуй помощника в RFN Assistant",
        screenshots=screenshots,
        candidate_revision=artifact.revision + 1,
    )


def _import(workspace):
    return import_refinement_workspace(
        workspace.root,
        receipt=workspace.receipt,
    )


def _mutate_workspace(workspace, mutation, artifact, persona) -> None:
    if mutation == "immutable_manifest_changed":
        workspace.manifest_path.write_text("{}", encoding="utf-8")
    elif mutation == "immutable_request_changed":
        (workspace.root / "request.md").write_text("другой запрос", encoding="utf-8")
    elif mutation == "immutable_screenshot_changed":
        (workspace.root / "screenshots" / "desktop.closed.jpg").write_bytes(b"changed")
    elif mutation == "symlink_in_editable":
        target = workspace.editable / "widget.css"
        target.unlink()
        try:
            target.symlink_to(workspace.root / "source-artifact.json")
        except OSError as exc:
            pytest.skip(f"symlinks are unavailable: {exc}")
    elif mutation == "hardlink_in_editable":
        target = workspace.editable / "widget.css"
        target.unlink()
        try:
            os.link(workspace.root / "source-artifact.json", target)
        except OSError as exc:
            pytest.skip(f"hardlinks are unavailable: {exc}")
    elif mutation == "unexpected_root_file":
        (workspace.root / "credentials.txt").write_text("secret", encoding="utf-8")
    elif mutation == "unexpected_editable_meta":
        (workspace.editable / "widget.meta.json").write_text("{}", encoding="utf-8")
    elif mutation == "duplicate_json_key":
        (workspace.editable / "result.json").write_text(
            '{"schema_version":"refinement-result.v1","status":"complete",'
            '"status":"complete","public_summary":"Обновил виджет.",'
            '"changed_files":[]}',
            encoding="utf-8",
        )
    elif mutation == "non_utf8_css":
        (workspace.editable / "widget.css").write_bytes(b"\xff\xfe")
    elif mutation == "non_utf8_scratch":
        (workspace.root / "scratch" / "notes.txt").write_bytes(b"\xff\xfe")
    elif mutation == "oversize_html":
        (workspace.editable / "widget.html").write_text(
            "x" * (64 * 1024 + 1), encoding="utf-8"
        )
    elif mutation == "partial_result":
        (workspace.editable / "result.json").write_text(
            '{"status":"complete"}', encoding="utf-8"
        )
    elif mutation == "changed_files_mismatch":
        (workspace.editable / "widget.css").write_text(
            artifact.css + "\n.kaigo-widget { opacity: .99; }",
            encoding="utf-8",
        )
        _complete_result(workspace, changed_files=["editable/persona.json"])
    elif mutation == "pending_result":
        return
    elif mutation == "empty_public_summary":
        (workspace.editable / "widget.css").write_text(
            artifact.css + "\n.kaigo-widget { opacity: .99; }",
            encoding="utf-8",
        )
        _complete_result(
            workspace,
            changed_files=["editable/widget.css"],
            public_summary="   ",
        )
    elif mutation == "employee_type_changed":
        _replace_persona(workspace, persona, employee_type="sales_advisor")
        _complete_result(workspace, changed_files=["editable/persona.json"])
    elif mutation == "safeguards_changed":
        _replace_persona(workspace, persona, safeguards=[])
        _complete_result(workspace, changed_files=["editable/persona.json"])
    elif mutation == "javascript_changed":
        (workspace.editable / "widget.js").write_text("alert(1)", encoding="utf-8")
        _complete_result(workspace, changed_files=["editable/widget.js"])
    elif mutation == "reserved_runtime_attribute":
        (workspace.editable / "widget.html").write_text(
            artifact.body_html.replace(
                'data-region="root"',
                'data-region="root" data-kaigo-runtime-label="owned"',
                1,
            ),
            encoding="utf-8",
        )
        _complete_result(workspace, changed_files=["editable/widget.html"])
    elif mutation == "no_changes":
        _complete_result(workspace, changed_files=[])
    else:  # pragma: no cover - protects the test helper itself
        raise AssertionError(f"unsupported mutation: {mutation}")


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("immutable_manifest_changed", "immutable_manifest_mismatch"),
        ("immutable_request_changed", "immutable_hash_mismatch"),
        ("immutable_screenshot_changed", "immutable_hash_mismatch"),
        ("symlink_in_editable", "unsafe_file_type"),
        ("hardlink_in_editable", "unsafe_link_count"),
        ("unexpected_root_file", "unexpected_path"),
        ("unexpected_editable_meta", "unexpected_path"),
        ("duplicate_json_key", "duplicate_json_key"),
        ("non_utf8_css", "invalid_utf8"),
        ("non_utf8_scratch", "invalid_utf8"),
        ("oversize_html", "file_too_large"),
        ("partial_result", "invalid_result"),
        ("changed_files_mismatch", "changed_files_mismatch"),
        ("pending_result", "result_not_complete"),
        ("empty_public_summary", "empty_public_summary"),
        ("employee_type_changed", "immutable_persona_field"),
        ("safeguards_changed", "immutable_persona_field"),
        ("javascript_changed", "immutable_javascript"),
        ("reserved_runtime_attribute", "reserved_runtime_attribute"),
        ("no_changes", "no_changes"),
    ],
)
def test_workspace_import_rejects_untrusted_mutation(
    tmp_path,
    artifact,
    persona,
    screenshots,
    mutation,
    expected_code,
):
    workspace = _make_workspace(
        tmp_path,
        artifact,
        persona,
        screenshots,
        name=mutation,
    )
    _mutate_workspace(workspace, mutation, artifact, persona)

    with pytest.raises(RefinementWorkspaceError) as captured:
        _import(workspace)

    assert captured.value.error_code == expected_code


@pytest.mark.parametrize(
    ("candidate_revision", "expected_code"),
    [(5, "invalid_revision"), (7, "invalid_revision")],
)
def test_workspace_creation_requires_exact_next_revision(
    tmp_path,
    artifact,
    persona,
    screenshots,
    candidate_revision,
    expected_code,
):
    with pytest.raises(RefinementWorkspaceError) as captured:
        create_refinement_workspace(
            root=tmp_path / str(candidate_revision),
            source_artifact=artifact,
            source_persona=persona,
            change_request="Переименуй помощника",
            screenshots=screenshots,
            candidate_revision=candidate_revision,
        )

    assert captured.value.error_code == expected_code


def test_workspace_creation_requires_exact_screenshot_names_and_order(
    tmp_path, artifact, persona, screenshots
):
    reordered = (screenshots[1], screenshots[0], *screenshots[2:])

    with pytest.raises(RefinementWorkspaceError) as captured:
        create_refinement_workspace(
            root=tmp_path / "wrong-screenshots",
            source_artifact=artifact,
            source_persona=persona,
            change_request="Переименуй помощника",
            screenshots=reordered,
            candidate_revision=artifact.revision + 1,
        )

    assert captured.value.error_code == "invalid_screenshots"


def test_import_uses_external_receipt_not_disk_manifest(
    tmp_path, artifact, persona, screenshots
):
    workspace = _make_workspace(tmp_path, artifact, persona, screenshots)
    forged_receipt = replace(
        workspace.receipt,
        manifest_bytes=b"{}",
    )

    with pytest.raises(RefinementWorkspaceError) as captured:
        import_refinement_workspace(workspace.root, receipt=forged_receipt)

    assert captured.value.error_code == "invalid_receipt"


def test_import_preserves_server_owned_artifact_fields(
    tmp_path, artifact, persona, screenshots
):
    workspace = _make_workspace(tmp_path, artifact, persona, screenshots)
    (workspace.editable / "widget.css").write_text(
        artifact.css + "\n.kaigo-widget { opacity: .99; }",
        encoding="utf-8",
    )
    _complete_result(workspace, changed_files=["editable/widget.css"])

    imported = _import(workspace)

    assert imported.artifact.schema_version == artifact.schema_version
    assert imported.artifact.stage is artifact.stage
    assert imported.artifact.revision == artifact.revision + 1
    assert imported.artifact.art_direction == artifact.art_direction
    assert imported.artifact.theme_tokens == artifact.theme_tokens
    assert imported.artifact.suggested_actions == artifact.suggested_actions
    assert imported.artifact.layout_contract == artifact.layout_contract
