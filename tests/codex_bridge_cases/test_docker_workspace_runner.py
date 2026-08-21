from __future__ import annotations

import base64
import asyncio
from builtins import BaseExceptionGroup
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from typing import Any

import pytest

from builder_lab.models import AssistantPersona, Stage, WidgetArtifact
from builder_lab.refinement_workspace import (
    CANONICAL_SCREENSHOT_NAMES,
    create_refinement_workspace,
)
import tools.kaigo_codex_bridge.docker_workspace_runner as docker_runner_module
from tools.kaigo_codex_bridge.container_workspace_entrypoint import (
    ContainerSecretLeak,
    ContainerWorkspaceError,
    SecretScanningReader,
    build_mcp_config_probe_command,
    build_mcp_codex_command,
    build_bounded_container_result,
    execute_container_workspace,
)
from tools.kaigo_codex_bridge.docker_workspace_runner import (
    DockerCommandResult,
    DockerWorkspaceEditor,
    DockerWorkspaceInvalidOutput,
    DockerWorkspaceRunResult,
    DockerWorkspaceSecretLeak,
    DockerWorkspaceTimeout,
    DockerWorkspaceUnavailable,
    assert_no_secret_fingerprints,
    build_container_create_command,
    load_verified_docker_delivery,
    secret_fingerprints,
)
from tools.kaigo_codex_bridge.workspace_runner import WorkspaceEditorReceipt
from tools.kaigo_codex_bridge.workspace_mcp_server import (
    MCP_TOOL_NAMES,
    WorkspaceMcpError,
    WorkspaceMcpService,
)


IMAGE_ID = "sha256:" + "a" * 64
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_create_command_uses_only_two_read_only_binds_and_hardening(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace with spaces"
    auth_copy = tmp_path / "private auth" / "auth.json"
    workspace.mkdir()
    auth_copy.parent.mkdir()
    auth_copy.write_text("{}", encoding="utf-8")
    docker = _fake_docker(tmp_path)

    command = build_container_create_command(
        docker_executable=str(docker),
        container_name="kaigo-refine-fixed",
        image_id=IMAGE_ID,
        workspace=workspace,
        auth_copy=auth_copy,
    )

    assert command[:4] == (
        str(docker),
        "create",
        "--name",
        "kaigo-refine-fixed",
    )
    assert command[-1] == IMAGE_ID
    assert command.count("--mount") == 2
    mounts = tuple(
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--mount"
    )
    assert mounts == (
        f"type=bind,source={workspace.resolve()},target=/seed/workspace,readonly",
        f"type=bind,source={auth_copy.resolve()},target=/codex-home/auth.json,readonly",
    )
    assert ("--read-only", "--user", "10001:10001") == command[4:7]
    assert _pair(command, "--cap-drop") == ("--cap-drop", "ALL")
    assert _pair(command, "--security-opt") == (
        "--security-opt",
        "no-new-privileges",
    )
    assert _pair(command, "--pids-limit") == ("--pids-limit", "128")
    assert _pair(command, "--memory") == ("--memory", "2g")
    assert _pair(command, "--memory-swap") == ("--memory-swap", "2g")
    assert _pair(command, "--cpus") == ("--cpus", "2")
    assert _pair(command, "--ulimit") == ("--ulimit", "nofile=1024:1024")
    assert _pair(command, "--network") == ("--network", "bridge")
    assert _pair(command, "--log-driver") == ("--log-driver", "none")
    assert _pair(command, "--stop-timeout") == ("--stop-timeout", "5")
    tmpfs = tuple(
        command[index + 1]
        for index, value in enumerate(command)
        if value == "--tmpfs"
    )
    assert tmpfs == (
        "/workspace:rw,noexec,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700",
        "/codex-home:rw,noexec,nosuid,nodev,size=16m,uid=10001,gid=10001,mode=0700",
        "/tmp:rw,noexec,nosuid,nodev,size=64m,uid=10001,gid=10001,mode=0700",
    )
    assert all("docker.sock" not in value for value in command)
    assert all("/run/secrets" not in value for value in command)


@pytest.mark.parametrize("missing", ["workspace", "auth"])
def test_create_command_rejects_missing_bind_source(
    tmp_path: Path,
    missing: str,
) -> None:
    workspace = tmp_path / "workspace"
    auth_copy = tmp_path / "auth.json"
    if missing != "workspace":
        workspace.mkdir()
    if missing != "auth":
        auth_copy.write_text("{}", encoding="utf-8")
    docker = _fake_docker(tmp_path)

    with pytest.raises(ValueError, match="existing"):
        build_container_create_command(
            docker_executable=str(docker),
            container_name="kaigo-refine-fixed",
            image_id=IMAGE_ID,
            workspace=workspace,
            auth_copy=auth_copy,
        )


def test_create_command_rejects_comma_in_bind_source(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace,unsafe"
    auth_copy = tmp_path / "auth.json"
    workspace.mkdir()
    auth_copy.write_text("{}", encoding="utf-8")
    docker = _fake_docker(tmp_path)

    with pytest.raises(ValueError, match="commas"):
        build_container_create_command(
            docker_executable=str(docker),
            container_name="kaigo-refine-fixed",
            image_id=IMAGE_ID,
            workspace=workspace,
            auth_copy=auth_copy,
        )


def test_create_command_rejects_reparse_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    auth_copy = tmp_path / "auth.json"
    workspace.mkdir()
    auth_copy.write_text("{}", encoding="utf-8")
    docker = _fake_docker(tmp_path)
    original = docker_runner_module._path_component_is_link_like
    monkeypatch.setattr(
        docker_runner_module,
        "_path_component_is_link_like",
        lambda path: path == workspace or original(path),
    )

    with pytest.raises(ValueError, match="reparse"):
        build_container_create_command(
            docker_executable=str(docker),
            container_name="kaigo-refine-fixed",
            image_id=IMAGE_ID,
            workspace=workspace,
            auth_copy=auth_copy,
        )


def test_create_command_rejects_relative_or_link_like_docker_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    auth_copy = tmp_path / "auth.json"
    workspace.mkdir()
    auth_copy.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="absolute"):
        build_container_create_command(
            docker_executable="docker",
            container_name="kaigo-refine-fixed",
            image_id=IMAGE_ID,
            workspace=workspace,
            auth_copy=auth_copy,
        )

    docker = _fake_docker(tmp_path)
    original = docker_runner_module._path_component_is_link_like
    monkeypatch.setattr(
        docker_runner_module,
        "_path_component_is_link_like",
        lambda path: path == docker or original(path),
    )
    with pytest.raises(ValueError, match="reparse"):
        build_container_create_command(
            docker_executable=str(docker),
            container_name="kaigo-refine-fixed",
            image_id=IMAGE_ID,
            workspace=workspace,
            auth_copy=auth_copy,
        )


def test_windows_support_tools_resolve_only_from_absolute_system32(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windows = tmp_path / "Windows"
    system32 = windows / "System32"
    system32.mkdir(parents=True)
    for name in ("icacls.exe", "whoami.exe"):
        (system32 / name).write_bytes(b"MZ-system-tool")
    monkeypatch.setenv("SystemRoot", str(windows))

    assert docker_runner_module._trusted_windows_system_executable(
        "icacls.exe"
    ) == (system32 / "icacls.exe").resolve(strict=True)
    assert docker_runner_module._trusted_windows_system_executable(
        "whoami.exe"
    ) == (system32 / "whoami.exe").resolve(strict=True)

    original = docker_runner_module._path_component_is_link_like
    monkeypatch.setattr(
        docker_runner_module,
        "_path_component_is_link_like",
        lambda path: path == system32 or original(path),
    )
    with pytest.raises(DockerWorkspaceUnavailable, match="trusted Windows tool"):
        docker_runner_module._trusted_windows_system_executable("icacls.exe")


@pytest.mark.parametrize("encoding", ["raw", "json", "base64", "urlbase64", "fragment"])
def test_secret_fingerprint_scan_rejects_supported_encodings(encoding: str) -> None:
    token = "sk-local-super-secret-1234567890"
    auth = json.dumps(
        {"tokens": {"access_token": token}},
        ensure_ascii=False,
    ).encode("utf-8")
    encoded = {
        "raw": token.encode("utf-8"),
        "json": json.dumps(token)[1:-1].encode("utf-8"),
        "base64": base64.b64encode(token.encode("utf-8")),
        "urlbase64": base64.urlsafe_b64encode(token.encode("utf-8")),
        "fragment": token[5:21].encode("utf-8"),
    }[encoding]

    with pytest.raises(DockerWorkspaceSecretLeak, match="secret fingerprint"):
        assert_no_secret_fingerprints(
            (b"safe-prefix:" + encoded + b":safe-suffix",),
            secret_fingerprints(auth),
        )


def test_secret_fingerprint_scan_accepts_unrelated_output() -> None:
    auth = b'{"access_token":"sk-local-super-secret-1234567890"}'

    assert_no_secret_fingerprints(
        (b'{"status":"complete","final_text":"done"}',),
        secret_fingerprints(auth),
    )


def test_auth_preflight_rejects_access_token_near_absolute_deadline() -> None:
    near_expiry = _auth_payload(exp=5_000)

    with pytest.raises(DockerWorkspaceUnavailable, match="expiry margin"):
        docker_runner_module._require_fresh_auth_for_deadline(
            near_expiry,
            timeout_seconds=600,
            current_time=1_000,
            safety_margin_seconds=3_600,
        )

    docker_runner_module._require_fresh_auth_for_deadline(
        _auth_payload(exp=5_201),
        timeout_seconds=600,
        current_time=1_000,
        safety_margin_seconds=3_600,
    )


def test_dockerfile_pins_base_codex_integrity_and_nonroot_allowlist() -> None:
    dockerfile = (
        REPOSITORY_ROOT / "Dockerfile.codex-workspace-editor"
    ).read_text(encoding="utf-8")
    dockerignore = (
        REPOSITORY_ROOT / "Dockerfile.codex-workspace-editor.dockerignore"
    ).read_text(encoding="utf-8")

    assert (
        "node:22-bookworm-slim@sha256:"
        "f32b81066cde10a75dbac96646099533316d94bac4150c55da1636e1f0ffdc46"
    ) in dockerfile
    assert (
        "python:3.12-slim@sha256:"
        "57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de"
    ) in dockerfile
    assert "@openai/codex@0.145.0" in dockerfile
    assert (
        "ln -s ../lib/node_modules/@openai/codex/bin/codex.js "
        "/usr/local/bin/codex"
    ) in dockerfile
    assert "COPY --from=codex-build /usr/local/bin/codex" not in dockerfile
    assert (
        "sha512-/PSPSFujjjmiyVFvG2yu/grOFhsWdokTH8t2KGWhXSo/"
        "M5n/dIDsnbsnO82/7bLtIoDuzQf7ATBUMWqPWQINlQ=="
    ) in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "apt-get" not in dockerfile
    assert "COPY ." not in dockerfile
    assert "workspace_runner.py" not in dockerfile
    assert "docker_workspace_runner.py" not in dockerfile
    assert "workspace_mcp_server.py" in dockerfile
    assert (
        'CMD ["python3", '
        '"/opt/kaigo/tools/kaigo_codex_bridge/container_workspace_entrypoint.py", '
        '"--idle"]'
    ) in dockerfile
    assert "tools/kaigo_codex_bridge/__init__.py" not in dockerfile
    assert dockerignore.splitlines()[0] == "**"
    assert "!tools/kaigo_codex_bridge/container_workspace_entrypoint.py" in dockerignore
    assert "!tools/kaigo_codex_bridge/workspace_mcp_server.py" in dockerignore
    assert "!tools/kaigo_codex_bridge/workspace_runner.py" not in dockerignore
    assert "!builder_lab/refinement_workspace.py" in dockerignore


@pytest.mark.parametrize(
    "leak_target",
    [
        "final_text",
        "widget.html",
        "widget.css",
        "widget.js",
        "persona.json",
        "result.json",
    ],
)
def test_container_result_scans_final_and_all_five_editable_files(
    tmp_path: Path,
    leak_target: str,
) -> None:
    editable = tmp_path / "editable"
    editable.mkdir()
    token = "sk-container-secret-1234567890"
    safe_contents = {
        "widget.html": "<section>safe</section>",
        "widget.css": ".safe{}",
        "widget.js": "",
        "persona.json": "{}",
        "result.json": "{}",
    }
    for name, content in safe_contents.items():
        (editable / name).write_text(
            token if leak_target == name else content,
            encoding="utf-8",
        )
    receipt = _editor_receipt(
        final_text=token if leak_target == "final_text" else "Готово",
    )

    with pytest.raises(ContainerSecretLeak, match="secret fingerprint"):
        build_bounded_container_result(
            receipt=receipt,
            editable=editable,
            auth_payload=json.dumps({"access_token": token}).encode("utf-8"),
        )


def test_container_result_returns_bounded_hash_manifest(tmp_path: Path) -> None:
    editable = tmp_path / "editable"
    editable.mkdir()
    contents = {
        "widget.html": b"<section>safe</section>",
        "widget.css": b".safe{}",
        "widget.js": b"",
        "persona.json": b"{}",
        "result.json": b"{}",
    }
    for name, content in contents.items():
        (editable / name).write_bytes(content)

    result = build_bounded_container_result(
        receipt=_editor_receipt(final_text="Готово"),
        editable=editable,
        auth_payload=b'{"access_token":"sk-container-secret-1234567890"}',
    )

    assert result["schema_version"] == "kaigo.codex-container-result.v1"
    assert result["editor"]["model"] == "gpt-5.6-sol"
    assert result["invocation"] == {
        "model": "gpt-5.6-sol",
        "requested_reasoning_effort": "max",
        "effective_reasoning_effort": "max",
        "permission_profile": "kaigo_mcp_read_only",
        "mcp_tools": list(MCP_TOOL_NAMES),
        "shell_tool": False,
        "unified_exec": False,
        "screenshots_attached": len(CANONICAL_SCREENSHOT_NAMES),
        "command_sha256": result["invocation"]["command_sha256"],
    }
    assert len(result["invocation"]["command_sha256"]) == 64
    assert tuple(item["path"] for item in result["editable_files"]) == tuple(
        f"editable/{name}" for name in contents
    )
    assert tuple(item["size"] for item in result["editable_files"]) == tuple(
        len(content) for content in contents.values()
    )
    assert all(len(item["sha256"]) == 64 for item in result["editable_files"])


def test_container_result_rejects_extra_editable_path(tmp_path: Path) -> None:
    editable = tmp_path / "editable"
    editable.mkdir()
    for name in ("widget.html", "widget.css", "widget.js", "persona.json", "result.json"):
        (editable / name).write_text("{}", encoding="utf-8")
    (editable / "unexpected.txt").write_text("unsafe", encoding="utf-8")

    with pytest.raises(ValueError, match="allowlist"):
        build_bounded_container_result(
            receipt=_editor_receipt(final_text="Готово"),
            editable=editable,
            auth_payload=b'{"access_token":"sk-container-secret-1234567890"}',
        )


@pytest.mark.asyncio
async def test_container_scans_raw_codex_stream_across_chunk_boundaries() -> None:
    token = "sk-stream-secret-1234567890"
    reader = SecretScanningReader(
        ChunkReader([b"safe:sk-stream-se", b"cret-1234567890:end", b""]),
        secret_fingerprints(
            json.dumps({"access_token": token}).encode("utf-8")
        ),
    )

    assert await reader.read(64) == b"safe:sk-stream-se"
    with pytest.raises(ContainerSecretLeak):
        await reader.read(64)


@pytest.mark.asyncio
async def test_container_rejects_bound_auth_mutation_on_editor_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _workspace(tmp_path / "seed")
    destination = tmp_path / "container-workspace"
    destination.mkdir()
    auth = tmp_path / "bound-auth.json"
    auth.write_bytes(_auth_payload(exp=4_102_444_800))
    import tools.kaigo_codex_bridge.container_workspace_entrypoint as entrypoint

    monkeypatch.setattr(entrypoint, "_SEED", seed.root)
    monkeypatch.setattr(entrypoint, "_WORKSPACE", destination)
    monkeypatch.setattr(entrypoint, "_AUTH_SOURCE", auth)
    monkeypatch.setattr(entrypoint, "import_refinement_workspace", lambda *args, **kwargs: None)

    async def mutate_auth(**kwargs: Any):
        del kwargs
        auth.write_bytes(b'{"changed":true}')
        return _editor_receipt(final_text="Готово")

    monkeypatch.setattr(entrypoint, "_run_mcp_codex", mutate_auth)

    with pytest.raises(ContainerWorkspaceError, match="auth changed"):
        await execute_container_workspace(timeout_seconds=30)


@pytest.mark.asyncio
async def test_docker_editor_runs_exact_lifecycle_and_atomically_applies_outputs(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    source_css = (workspace.editable / "widget.css").read_bytes()
    auth = tmp_path / "source-auth.json"
    token = "sk-host-secret-1234567890"
    _write_fresh_auth(auth, extra_secret=token)
    container_output = tmp_path / "container-output"
    shutil.copytree(workspace.editable, container_output)
    changed_css = (
        (container_output / "widget.css").read_text(encoding="utf-8")
        + "\n.kaigo-widget .kaigo-widget__launcher:hover{transform:scale(1.04)}"
    )
    (container_output / "widget.css").write_text(changed_css, encoding="utf-8")
    (container_output / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "refinement-result.v1",
                "status": "complete",
                "public_summary": "Обновил анимацию наведения.",
                "changed_files": ["editable/widget.css"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake = FakeDockerRunner(
        image_id=IMAGE_ID,
        container_output=container_output,
    )
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-fixed",
    )

    result = await editor.run(workspace=workspace, timeout_seconds=30)

    assert isinstance(result, DockerWorkspaceRunResult)
    assert result.model == "gpt-5.6-sol"
    assert result.requested_reasoning_effort == "max"
    assert result.effective_reasoning_effort == "max"
    assert workspace.editable.joinpath("widget.css").read_bytes() == source_css
    assert result.workspace.editable.joinpath("widget.css").read_text(
        encoding="utf-8"
    ) == changed_css
    assert result.proof_path == result.delivery_root / "invocation.json"
    assert len(result.proof_sha256) == 64
    assert len(result.candidate_aggregate_sha256) == 64
    reloaded = load_verified_docker_delivery(
        result.delivery_root,
        expected_proof_sha256=result.proof_sha256,
    )
    assert reloaded.candidate_aggregate_sha256 == result.candidate_aggregate_sha256
    assert reloaded.workspace.root == result.workspace.root
    verbs = tuple(command[1] for command in fake.commands)
    assert verbs == ("image", "create", "start", "exec", "cp", "rm")
    create = fake.commands[1]
    assert create[-1] == IMAGE_ID
    assert create.count("--mount") == 2
    exec_command = fake.commands[3]
    assert Path(exec_command[0]).is_absolute()
    assert exec_command[1:3] == ("exec", "-i")
    assert _docker_exec_environment(exec_command) == {
        "CODEX_HOME": "/codex-home",
        "HOME": "/codex-home",
        "TMPDIR": "/tmp",
        "TEMP": "/tmp",
        "TMP": "/tmp",
    }
    assert all(token not in argument for command in fake.commands for argument in command)
    assert not tuple(tmp_path.glob("kaigo-codex-docker-*"))


@pytest.mark.asyncio
async def test_delivery_is_published_only_after_container_and_auth_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, auth, container_output = _successful_case(tmp_path)
    fake = FakeDockerRunner(image_id=IMAGE_ID, container_output=container_output)
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-publish-order",
    )
    original = docker_runner_module._publish_delivery
    observed = False

    def publish_after_cleanup(staging: Path, final: Path) -> None:
        nonlocal observed
        observed = True
        assert fake.commands[-1][1:] == (
            "rm",
            "-f",
            "kaigo-refine-publish-order",
        )
        assert not tuple(tmp_path.glob("kaigo-codex-docker-*"))
        original(staging, final)

    monkeypatch.setattr(
        docker_runner_module,
        "_publish_delivery",
        publish_after_cleanup,
    )

    result = await editor.run(workspace=workspace, timeout_seconds=30)

    assert observed is True
    assert result.delivery_root.is_dir()


@pytest.mark.asyncio
async def test_candidate_mutation_invalidates_persisted_invocation_proof(
    tmp_path: Path,
) -> None:
    workspace, auth, container_output = _successful_case(tmp_path)
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=FakeDockerRunner(
            image_id=IMAGE_ID,
            container_output=container_output,
        ),
        container_name_factory=lambda: "kaigo-refine-proof-mutation",
    )
    result = await editor.run(workspace=workspace, timeout_seconds=30)
    (result.workspace.editable / "widget.css").write_text(
        ".tampered{}",
        encoding="utf-8",
    )

    with pytest.raises(DockerWorkspaceInvalidOutput, match="digest"):
        load_verified_docker_delivery(
            result.delivery_root,
            expected_proof_sha256=result.proof_sha256,
        )


@pytest.mark.asyncio
async def test_docker_editor_timeout_forces_exact_container_cleanup(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    auth = tmp_path / "auth.json"
    _write_fresh_auth(auth)
    fake = FakeDockerRunner(
        image_id=IMAGE_ID,
        container_output=tmp_path,
        fail_verb="exec",
        failure=TimeoutError(),
        rm_result=DockerCommandResult(1, b"", b"daemon cleanup failure"),
        inspect_result=DockerCommandResult(0, b"container-id\n", b""),
    )
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-timeout",
    )

    with pytest.raises(DockerWorkspaceTimeout) as raised:
        await editor.run(workspace=workspace, timeout_seconds=30)

    assert isinstance(raised.value.__cause__, BaseExceptionGroup)
    assert len(raised.value.__cause__.exceptions) == 1
    assert fake.commands[-2] == (
        str(_fake_docker(tmp_path)),
        "rm",
        "-f",
        "kaigo-refine-timeout",
    )
    assert fake.commands[-1][1] == "inspect"


@pytest.mark.asyncio
async def test_docker_editor_cancellation_forces_exact_container_cleanup(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    auth = tmp_path / "auth.json"
    _write_fresh_auth(auth)
    fake = BlockingDockerRunner(image_id=IMAGE_ID)
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-cancel",
    )
    task = asyncio.create_task(
        editor.run(workspace=workspace, timeout_seconds=30)
    )
    await asyncio.wait_for(fake.exec_started.wait(), timeout=2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fake.commands[-1] == (
        str(_fake_docker(tmp_path)),
        "rm",
        "-f",
        "kaigo-refine-cancel",
    )


@pytest.mark.asyncio
async def test_host_repeats_secret_scan_before_copy(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "workspace")
    auth = tmp_path / "auth.json"
    token = "sk-host-secret-1234567890"
    _write_fresh_auth(auth, extra_secret=token)
    fake = FakeDockerRunner(
        image_id=IMAGE_ID,
        container_output=tmp_path,
        exec_override=DockerCommandResult(
            returncode=0,
            stdout=_container_stdout(tmp_path, final_text=token[4:20]),
            stderr=b"",
        ),
    )
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-leak",
    )

    with pytest.raises(DockerWorkspaceSecretLeak):
        await editor.run(workspace=workspace, timeout_seconds=30)

    assert "cp" not in tuple(command[1] for command in fake.commands)
    assert fake.commands[-1][1:] == ("rm", "-f", "kaigo-refine-leak")


@pytest.mark.asyncio
async def test_unexpected_copied_path_rejects_without_partial_apply(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    original_css = (workspace.editable / "widget.css").read_bytes()
    auth = tmp_path / "auth.json"
    _write_fresh_auth(auth)
    container_output = tmp_path / "container-output"
    shutil.copytree(workspace.editable, container_output)
    (container_output / "unexpected.txt").write_text("unsafe", encoding="utf-8")
    fake = FakeDockerRunner(
        image_id=IMAGE_ID,
        container_output=container_output,
    )
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-extra",
    )

    with pytest.raises(DockerWorkspaceInvalidOutput, match="allowlist"):
        await editor.run(workspace=workspace, timeout_seconds=30)

    assert (workspace.editable / "widget.css").read_bytes() == original_css


@pytest.mark.asyncio
async def test_private_auth_staging_failure_removes_partial_run_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    auth = tmp_path / "auth.json"
    _write_fresh_auth(auth)
    calls = 0
    original = docker_runner_module._restrict_private_acl

    def fail_second_acl(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise DockerWorkspaceInvalidOutput("simulated ACL failure")
        original(path)

    monkeypatch.setattr(
        docker_runner_module,
        "_restrict_private_acl",
        fail_second_acl,
    )
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=FakeDockerRunner(
            image_id=IMAGE_ID,
            container_output=tmp_path,
        ),
        container_name_factory=lambda: "kaigo-refine-acl-failure",
    )

    with pytest.raises(DockerWorkspaceInvalidOutput, match="ACL failure"):
        await editor.run(workspace=workspace, timeout_seconds=30)

    assert not tuple(tmp_path.glob("kaigo-codex-docker-*"))


@pytest.mark.asyncio
async def test_nonzero_rm_f_fails_when_inspect_still_finds_container(
    tmp_path: Path,
) -> None:
    workspace, auth, container_output = _successful_case(tmp_path)
    fake = FakeDockerRunner(
        image_id=IMAGE_ID,
        container_output=container_output,
        rm_result=DockerCommandResult(1, b"", b"daemon cleanup failure"),
        inspect_result=DockerCommandResult(0, b"container-id\n", b""),
    )
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-rm-failed",
    )

    with pytest.raises(DockerWorkspaceUnavailable, match="cleanup failed"):
        await editor.run(workspace=workspace, timeout_seconds=30)

    assert tuple(command[1] for command in fake.commands)[-2:] == (
        "rm",
        "inspect",
    )
    assert not tuple(tmp_path.glob("kaigo-codex-delivery-*"))


@pytest.mark.asyncio
async def test_all_cleanup_failures_are_retained_without_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, auth, container_output = _successful_case(tmp_path)
    fake = FakeDockerRunner(
        image_id=IMAGE_ID,
        container_output=container_output,
        rm_result=DockerCommandResult(1, b"", b"daemon cleanup failure"),
        inspect_result=DockerCommandResult(0, b"container-id\n", b""),
    )
    original_remove = docker_runner_module._remove_private_run_root

    def fail_auth_cleanup(path: Path) -> None:
        if path.name.startswith("kaigo-codex-docker-"):
            raise DockerWorkspaceUnavailable("simulated private cleanup failure")
        original_remove(path)

    monkeypatch.setattr(
        docker_runner_module,
        "_remove_private_run_root",
        fail_auth_cleanup,
    )
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-cleanup-group",
    )

    with pytest.raises(DockerWorkspaceUnavailable) as raised:
        await editor.run(workspace=workspace, timeout_seconds=30)

    assert isinstance(raised.value.__cause__, BaseExceptionGroup)
    assert len(raised.value.__cause__.exceptions) == 2
    assert not tuple(tmp_path.glob("kaigo-codex-delivery-*"))


@pytest.mark.asyncio
async def test_nonzero_rm_f_is_accepted_only_for_exact_inspect_absence(
    tmp_path: Path,
) -> None:
    workspace, auth, container_output = _successful_case(tmp_path)
    fake = FakeDockerRunner(
        image_id=IMAGE_ID,
        container_output=container_output,
        rm_result=DockerCommandResult(1, b"", b"daemon cleanup failure"),
        inspect_result=DockerCommandResult(
            1,
            b"",
            b"Error: No such object: kaigo-refine-already-gone\n",
        ),
    )
    editor = DockerWorkspaceEditor(
        image_id=IMAGE_ID,
        auth_file=auth,
        private_temp_base=tmp_path,
        docker_executable=str(_fake_docker(tmp_path)),
        command_runner=fake,
        container_name_factory=lambda: "kaigo-refine-already-gone",
    )

    result = await editor.run(workspace=workspace, timeout_seconds=30)

    assert result.model == "gpt-5.6-sol"


def test_private_run_root_removal_fails_if_absence_cannot_be_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_root = tmp_path / "kaigo-codex-docker-stuck"
    run_root.mkdir()
    (run_root / "auth.json").write_text("secret", encoding="utf-8")
    monkeypatch.setattr(shutil, "rmtree", lambda *args, **kwargs: None)

    with pytest.raises(DockerWorkspaceUnavailable, match="could not be verified"):
        docker_runner_module._remove_private_run_root(run_root)


def test_codex_command_is_mcp_only_with_custom_read_only_profile(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")

    command = build_mcp_codex_command(workspace=workspace.root)

    assert "-s" not in command
    assert ("--disable", "shell_tool") in tuple(zip(command, command[1:]))
    assert ("--disable", "unified_exec") in tuple(zip(command, command[1:]))
    assert "--enable" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
    assert command.count("-i") == len(CANONICAL_SCREENSHOT_NAMES)
    assert all(
        str(workspace.root / "screenshots" / f"{name}.jpg") in command
        for name in CANONICAL_SCREENSHOT_NAMES
    )
    config = tuple(
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "-c"
    )
    assert 'features.shell_tool=false' in config
    assert 'features.unified_exec=false' in config
    assert 'model_reasoning_effort="max"' in config
    assert 'web_search="disabled"' in config
    assert 'tools.web_search=false' in config
    assert 'default_permissions="kaigo_mcp_read_only"' in config
    filesystem = next(
        value
        for value in config
        if value.startswith("permissions.kaigo_mcp_read_only.filesystem=")
    )
    assert '"/workspace"' not in filesystem
    assert f'"{workspace.root.as_posix()}"="read"' in filesystem
    for denied in (
        "/codex-home",
        "/run",
        "/seed",
        "/tmp",
        "/proc",
        "/sys",
        "/dev",
        "/home",
    ):
        assert f'"{denied}"="deny"' in filesystem
    assert "permissions.kaigo_mcp_read_only.network.enabled=false" in config
    assert (
        "mcp_servers.kaigo_workspace.enabled_tools="
        + json.dumps(list(MCP_TOOL_NAMES), separators=(",", ":"))
    ) in config
    assert 'mcp_servers.kaigo_workspace.required=true' in config
    assert (
        'mcp_servers.kaigo_workspace.env='
        '{"PYTHONPATH"="/opt/kaigo","PYTHONUTF8"="1"}'
    ) in config


def test_mcp_handshake_survives_codex_sanitized_environment(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    command = build_mcp_codex_command(workspace=workspace.root)
    config = tuple(
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "-c"
    )
    env_override = next(
        value.removeprefix("mcp_servers.kaigo_workspace.env=")
        for value in config
        if value.startswith("mcp_servers.kaigo_workspace.env=")
    )
    configured_env = tomllib.loads(f"value={env_override}")["value"]
    assert configured_env == {
        "PYTHONPATH": "/opt/kaigo",
        "PYTHONUTF8": "1",
    }

    sanitized_env = dict(os.environ)
    sanitized_env.pop("PYTHONPATH", None)
    sanitized_env.update(configured_env)
    sanitized_env["PYTHONPATH"] = str(REPOSITORY_ROOT)
    server_program = (
        "import sys; from pathlib import Path; "
        "from tools.kaigo_codex_bridge.workspace_mcp_server import "
        "WorkspaceMcpService, serve_stdio; "
        "serve_stdio(WorkspaceMcpService(Path(sys.argv[1])))"
    )
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "codex-cli", "version": "0.145.0"},
        },
    }
    initialized = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    list_tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    request_bytes = b"".join(
        json.dumps(item, separators=(",", ":")).encode("utf-8") + b"\n"
        for item in (initialize, initialized, list_tools)
    )

    completed = subprocess.run(
        [sys.executable, "-c", server_program, str(workspace.root)],
        cwd=workspace.root,
        env=sanitized_env,
        input=request_bytes,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode(
        "utf-8", errors="replace"
    )
    assert completed.stderr == b""
    responses = tuple(
        json.loads(line)
        for line in completed.stdout.decode("utf-8").splitlines()
    )
    assert tuple(response["id"] for response in responses) == (1, 2)
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    assert tuple(
        tool["name"] for tool in responses[1]["result"]["tools"]
    ) == MCP_TOOL_NAMES


def test_no_model_strict_config_probe_has_same_mcp_boundary(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "workspace")

    command = build_mcp_config_probe_command(workspace=workspace.root)

    assert command[0] == "/usr/local/bin/codex"
    assert command[1] == "mcp-server"
    assert "--ignore-user-config" not in command
    assert "exec" not in command
    assert "-m" not in command
    assert "-i" not in command
    assert "--strict-config" in command
    assert ("--disable", "shell_tool") in tuple(zip(command, command[1:]))
    assert ("--disable", "unified_exec") in tuple(zip(command, command[1:]))
    config = tuple(
        command[index + 1]
        for index, argument in enumerate(command)
        if argument == "-c"
    )
    assert 'model_reasoning_effort="max"' in config
    assert 'default_permissions="kaigo_mcp_read_only"' in config
    assert 'mcp_servers.kaigo_workspace.required=true' in config
    filesystem = next(
        value
        for value in config
        if value.startswith("permissions.kaigo_mcp_read_only.filesystem=")
    )
    assert f'"{workspace.root.as_posix()}"="read"' in filesystem
    assert '"/codex-home"="deny"' in filesystem


def test_mcp_lists_and_reads_only_exact_workspace_inputs(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "workspace")
    service = WorkspaceMcpService(workspace.root)

    listed = service.list_workspace_inputs({})

    paths = tuple(item["path"] for item in listed["files"])
    assert "request.md" in paths
    assert "editable/widget.css" in paths
    assert "validate.py" not in paths
    assert not any(path.startswith("validator_lib/") for path in paths)
    assert not any(path.startswith("screenshots/") for path in paths)
    assert service.read_workspace_file({"path": "request.md"})["content"]
    with pytest.raises(WorkspaceMcpError, match="allowlisted"):
        service.read_workspace_file({"path": "../codex-home/auth.json"})
    with pytest.raises(WorkspaceMcpError, match="allowlisted"):
        service.read_workspace_file({"path": "/run/secrets/codex-auth.json"})


def test_mcp_rejects_non_allowlisted_tools_paths_links_and_oversize(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    service = WorkspaceMcpService(workspace.root)

    assert tuple(tool["name"] for tool in service.tool_specs()) == MCP_TOOL_NAMES
    with pytest.raises(WorkspaceMcpError, match="unknown tool"):
        service.call_tool("exec_command", {"cmd": "id"})
    with pytest.raises(WorkspaceMcpError, match="allowlisted"):
        service.write_editable_file({"path": "../widget.css", "content": "x"})
    with pytest.raises(WorkspaceMcpError, match="size limit"):
        service.write_editable_file(
            {"path": "editable/widget.html", "content": "x" * (64 * 1024 + 1)}
        )

    target = workspace.editable / "widget.css"
    replacement = tmp_path / "replacement.css"
    replacement.write_text(".unsafe{}", encoding="utf-8")
    target.unlink()
    try:
        target.symlink_to(replacement)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(WorkspaceMcpError, match="regular file"):
        service.write_editable_file(
            {"path": "editable/widget.css", "content": ".safe{}"}
        )


def test_mcp_validation_matches_trusted_importer(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "workspace")
    service = WorkspaceMcpService(workspace.root)

    invalid = service.validate_candidate({})

    assert invalid == {
        "valid": False,
        "error": {
            "code": "result_not_complete",
            "detail": (
                "Complete editable/result.json with the exact result schema "
                "before validating again."
            ),
        },
    }
    service.write_editable_file(
        {
            "path": "editable/widget.css",
            "content": (
                service.read_workspace_file({"path": "editable/widget.css"})[
                    "content"
                ]
                + "\n/* refined */\n"
            ),
        }
    )
    service.write_editable_file(
        {
            "path": "editable/result.json",
            "content": json.dumps(
                {
                    "schema_version": "refinement-result.v1",
                    "status": "complete",
                    "public_summary": "Уточнено визуальное оформление виджета.",
                    "changed_files": ["editable/widget.css"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
    )
    trusted = docker_runner_module.workspace_contract.import_refinement_workspace(
        workspace.root,
        receipt=workspace.receipt,
    )

    validated = service.validate_candidate({})

    assert validated["valid"] is True
    assert tuple(validated["changed_paths"]) == trusted.changed_paths


def _pair(command: tuple[str, ...], name: str) -> tuple[str, str]:
    index = command.index(name)
    return command[index], command[index + 1]


def _editor_receipt(*, final_text: str) -> WorkspaceEditorReceipt:
    return WorkspaceEditorReceipt(
        model="gpt-5.6-sol",
        duration_ms=42,
        input_tokens=10,
        cached_input_tokens=2,
        output_tokens=3,
        thread_id="thread-local",
        final_text=final_text,
    )


def _workspace(root: Path):
    artifact = WidgetArtifact(
        schema_version="1.0",
        revision=5,
        stage=Stage.CONVERSATION,
        art_direction="Спокойный консультант",
        body_html=(
            '<section class="kaigo-widget" data-region="root" aria-label="Консультант RFN">'
            '<button class="kaigo-widget__launcher" data-region="launcher" '
            'aria-label="Открыть консультанта" type="button">R</button>'
            '<div class="kaigo-widget__panel" data-region="panel" role="dialog" '
            'aria-label="Диалог с консультантом">'
            '<header data-region="header"><h2>RFN</h2></header>'
            '<main data-region="messages" aria-live="polite">Диалог</main>'
            '<div data-region="suggestions"></div>'
            '<div data-region="composer" role="group" aria-label="Сообщение">'
            '<input aria-label="Введите сообщение"><button type="button">Отправить</button></div>'
            "</div></section>"
        ),
        css=".kaigo-widget { color: #18212b; position: relative; }",
        theme_tokens={"accent": "#f38b55"},
        suggested_actions=(),
        change_summary="Первая версия.",
        javascript="",
        layout_contract={"launcher": "bottom-right"},
    )
    persona = AssistantPersona(
        schema_version="kaigo.assistant-persona.v1",
        employee_type="consultant",
        display_name="Помощник RFN",
        role_summary="Консультирует клиентов RFN.",
        voice_style="professional",
        opening_line="Здравствуйте!",
        behavior_rules=("Отвечай кратко.", "Уточняй задачу клиента."),
        safeguards=("Не выдумывай факты.",),
        decision_rationale="Соответствует задаче.",
    )
    screenshots = tuple(
        (name, b"\xff\xd8\xff" + name.encode("ascii"))
        for name in CANONICAL_SCREENSHOT_NAMES
    )
    return create_refinement_workspace(
        root=root,
        trusted_private_base=root.parent,
        source_artifact=artifact,
        source_persona=persona,
        change_request="Обнови анимацию наведения",
        screenshots=screenshots,
        candidate_revision=6,
    )


def _successful_case(tmp_path: Path):
    workspace = _workspace(tmp_path / "workspace")
    auth = tmp_path / "auth.json"
    _write_fresh_auth(auth)
    container_output = tmp_path / "container-output"
    shutil.copytree(workspace.editable, container_output)
    (container_output / "widget.css").write_text(
        (container_output / "widget.css").read_text(encoding="utf-8")
        + "\n.kaigo-widget .kaigo-widget__launcher:hover{transform:scale(1.04)}",
        encoding="utf-8",
    )
    (container_output / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "refinement-result.v1",
                "status": "complete",
                "public_summary": "Обновил анимацию наведения.",
                "changed_files": ["editable/widget.css"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return workspace, auth, container_output


def _container_stdout(output: Path, *, final_text: str = "Готово") -> bytes:
    editable = output
    records = []
    for name in ("widget.html", "widget.css", "widget.js", "persona.json", "result.json"):
        path = editable / name
        payload = path.read_bytes() if path.is_file() else b""
        records.append(
            {
                "path": f"editable/{name}",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return json.dumps(
        {
            "schema_version": "kaigo.codex-container-result.v1",
            "editor": {
                "model": "gpt-5.6-sol",
                "duration_ms": 42,
                "input_tokens": 10,
                "cached_input_tokens": 2,
                "output_tokens": 3,
                "thread_id": "thread-local",
                "final_text": final_text,
            },
            "invocation": {
                "model": "gpt-5.6-sol",
                "requested_reasoning_effort": "max",
                "effective_reasoning_effort": "max",
                "permission_profile": "kaigo_mcp_read_only",
                "mcp_tools": list(MCP_TOOL_NAMES),
                "shell_tool": False,
                "unified_exec": False,
                "screenshots_attached": len(CANONICAL_SCREENSHOT_NAMES),
                "command_sha256": "b" * 64,
            },
            "editable_files": records,
        },
        ensure_ascii=False,
    ).encode("utf-8")


class FakeDockerRunner:
    def __init__(
        self,
        *,
        image_id: str,
        container_output: Path,
        exec_override: DockerCommandResult | None = None,
        fail_verb: str | None = None,
        failure: BaseException | None = None,
        rm_result: DockerCommandResult | None = None,
        inspect_result: DockerCommandResult | None = None,
    ) -> None:
        self.image_id = image_id
        self.container_output = container_output
        self.exec_override = exec_override
        self.fail_verb = fail_verb
        self.failure = failure
        self.rm_result = rm_result
        self.inspect_result = inspect_result
        self.commands: list[tuple[str, ...]] = []

    async def run(
        self,
        command: tuple[str, ...],
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        max_stderr_bytes: int,
    ) -> DockerCommandResult:
        del timeout_seconds, max_stdout_bytes, max_stderr_bytes
        self.commands.append(command)
        verb = command[1]
        if verb == self.fail_verb and self.failure is not None:
            raise self.failure
        if verb == "image":
            return DockerCommandResult(0, (self.image_id + "\n").encode(), b"")
        if verb == "create":
            return DockerCommandResult(0, b"container-id\n", b"")
        if verb == "start":
            return DockerCommandResult(0, (command[-1] + "\n").encode(), b"")
        if verb == "exec":
            if self.exec_override is not None:
                return self.exec_override
            return DockerCommandResult(
                0,
                _container_stdout(self.container_output),
                b"",
            )
        if verb == "cp":
            destination = Path(command[-1])
            for source in self.container_output.iterdir():
                if source.is_file():
                    shutil.copyfile(source, destination / source.name)
            return DockerCommandResult(0, b"", b"")
        if verb == "rm":
            return self.rm_result or DockerCommandResult(0, b"", b"")
        if verb == "inspect":
            return self.inspect_result or DockerCommandResult(
                0,
                b"container-id\n",
                b"",
            )
        raise AssertionError(f"unexpected Docker verb: {verb}")


class BlockingDockerRunner(FakeDockerRunner):
    def __init__(self, *, image_id: str) -> None:
        super().__init__(image_id=image_id, container_output=Path("."))
        self.exec_started = asyncio.Event()

    async def run(self, command: tuple[str, ...], **kwargs: Any) -> DockerCommandResult:
        self.commands.append(command)
        if command[1] == "exec":
            self.exec_started.set()
            await asyncio.Event().wait()
        if command[1] == "image":
            return DockerCommandResult(0, (self.image_id + "\n").encode(), b"")
        return DockerCommandResult(0, b"ok\n", b"")


class ChunkReader:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = iter(chunks)

    async def read(self, size: int = -1) -> bytes:
        del size
        return next(self.chunks)


def _docker_exec_environment(command: tuple[str, ...]) -> dict[str, str]:
    values: dict[str, str] = {}
    for index, argument in enumerate(command):
        if argument == "-e":
            name, value = command[index + 1].split("=", 1)
            values[name] = value
    return values


def _fake_docker(root: Path) -> Path:
    path = root / "trusted docker.exe"
    if not path.exists():
        path.write_bytes(b"MZ-fake-docker")
    return path.resolve(strict=True)


def _auth_payload(
    *,
    exp: int,
    extra_secret: str = "sk-refresh-secret-1234567890",
) -> bytes:
    def segment(value: Any) -> str:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    account_id = "account-test"
    token = ".".join(
        (
            segment({"alg": "none", "typ": "JWT"}),
            segment(
                {
                    "exp": exp,
                    "https://api.openai.com/auth": {
                        "chatgpt_account_id": account_id,
                    },
                }
            ),
            segment("signature"),
        )
    )
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "OPENAI_API_KEY": None,
            "tokens": {
                "id_token": token,
                "access_token": token,
                "refresh_token": extra_secret,
                "account_id": account_id,
            },
            "last_refresh": "2026-08-21T17:24:18Z",
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _write_fresh_auth(
    path: Path,
    *,
    extra_secret: str = "sk-refresh-secret-1234567890",
) -> None:
    path.write_bytes(
        _auth_payload(exp=4_102_444_800, extra_secret=extra_secret)
    )
