from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

from builder_lab.refinement_workspace import (
    CANONICAL_SCREENSHOT_NAMES,
    FrozenWorkspaceReceipt,
    RefinementWorkspace,
)
import tools.kaigo_codex_bridge.workspace_runner as workspace_runner_module
from tools.kaigo_codex_bridge.workspace_runner import (
    CodexWorkspaceEditor,
    WorkspaceEditorInvalidOutput,
    WorkspaceEditorOutputLimit,
    WorkspaceEditorTimeout,
    WorkspaceEditorUnavailable,
    _spawn_subprocess,
    _terminate_process_tree,
    build_workspace_command,
)


def _pair(command: tuple[str, ...], flag: str) -> tuple[str, str]:
    index = command.index(flag)
    return command[index], command[index + 1]


def _images(command: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        command[index + 1] for index, value in enumerate(command) if value == "--image"
    )


def test_command_is_fresh_workspace_write_and_has_no_output_schema(tmp_path):
    command = build_workspace_command(
        executable="codex",
        workspace=tmp_path,
        model="gpt-5.6-sol",
        reasoning_effort="max",
    )

    assert command[:3] == ("codex", "exec", "--ephemeral")
    assert ("-s", "workspace-write") == _pair(command, "-s")
    assert ("-C", str(tmp_path.resolve())) == _pair(command, "-C")
    assert "resume" not in command
    assert "--output-schema" not in command
    assert "--json" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--strict-config" in command
    assert _images(command) == tuple(
        str((tmp_path / "screenshots" / f"{name}.jpg").resolve())
        for name in CANONICAL_SCREENSHOT_NAMES
    )


def test_command_rejects_unapproved_model_and_effort(tmp_path):
    with pytest.raises(ValueError, match="model"):
        build_workspace_command(
            executable="codex",
            workspace=tmp_path,
            model="gpt-5.6-luna",
            reasoning_effort="max",
        )
    with pytest.raises(ValueError, match="effort"):
        build_workspace_command(
            executable="codex",
            workspace=tmp_path,
            model="gpt-5.6-sol",
            reasoning_effort="unbounded",
        )


def test_command_enables_only_shell_and_disables_integrations(tmp_path):
    command = build_workspace_command(
        executable="codex",
        workspace=tmp_path,
        model="gpt-5.6-sol",
        reasoning_effort="max",
    )
    adjacent = tuple(zip(command, command[1:]))

    assert ("--enable", "shell_tool") in adjacent
    for feature in (
        "unified_exec",
        "apps",
        "multi_agent",
        "browser_use",
        "browser_use_external",
        "browser_use_full_cdp_access",
        "computer_use",
        "image_generation",
        "in_app_browser",
        "hooks",
        "plugins",
        "remote_plugin",
        "skill_search",
        "skill_mcp_dependency_install",
        "goals",
        "workspace_dependencies",
    ):
        assert ("--disable", feature) in adjacent
    assert 'approval_policy="never"' in command
    assert 'web_search="disabled"' in command
    assert "tools.web_search=false" in command
    assert "sandbox_workspace_write.network_access=false" in command
    assert command[-1] == "-"


def _event_stream(
    thread_id: str = "thread-test",
    *,
    final_texts: tuple[str, ...] = ("Готово.",),
    terminal: str = "turn.completed",
) -> bytes:
    events: list[dict[str, object]] = [
        {"type": "thread.started", "thread_id": thread_id},
    ]
    events.extend(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": text},
        }
        for text in final_texts
    )
    terminal_event: dict[str, object] = {"type": terminal}
    if terminal == "turn.completed":
        terminal_event["usage"] = {
            "input_tokens": 101,
            "cached_input_tokens": 23,
            "output_tokens": 47,
        }
    events.append(terminal_event)
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


class _FakeStdin:
    def __init__(self) -> None:
        self.payload = bytearray()
        self.closed = False

    def write(self, payload: bytes) -> None:
        self.payload.extend(payload)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _reader(payload: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


class _FakeProcess:
    def __init__(
        self,
        stdout: bytes,
        *,
        stderr: bytes = b"",
        returncode: int = 0,
        blocker: asyncio.Event | None = None,
    ) -> None:
        self.pid = 4242
        self.stdin = _FakeStdin()
        self.stdout = _reader(stdout)
        self.stderr = _reader(stderr)
        self.returncode: int | None = None
        self._final_returncode = returncode
        self._blocker = blocker
        self.wait_started = asyncio.Event()
        self.reaped = False

    async def wait(self) -> int:
        self.wait_started.set()
        if self._blocker is not None:
            await self._blocker.wait()
        self.returncode = self._final_returncode
        self.reaped = True
        return self.returncode


def _prepare_workspace(
    tmp_path: Path,
    *,
    status: str = "complete",
) -> RefinementWorkspace:
    root = tmp_path / "workspace"
    (root / "editable").mkdir(parents=True)
    (root / "screenshots").mkdir()
    (root / "validator_lib").mkdir()
    (root / "scratch").mkdir()
    for filename in (
        "CONTRACT.md",
        "request.md",
        "source-artifact.json",
        "source-persona.json",
        "manifest.json",
        "validate.py",
    ):
        (root / filename).write_text("fixture", encoding="utf-8")
    for name in CANONICAL_SCREENSHOT_NAMES:
        (root / "screenshots" / f"{name}.jpg").write_bytes(b"\xff\xd8\xfffixture")
    for filename in ("widget.html", "widget.css", "widget.js", "persona.json"):
        (root / "editable" / filename).write_text("fixture", encoding="utf-8")
    (root / "editable" / "result.json").write_text(
        json.dumps(
            {
                "schema_version": "refinement-result.v1",
                "status": status,
                "public_summary": "Обновил имя и анимацию.",
                "changed_files": ["editable/widget.css"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    resolved_root = root.resolve()
    manifest_path = (resolved_root / "manifest.json").resolve()
    manifest_bytes = manifest_path.read_bytes()
    receipt = FrozenWorkspaceReceipt(
        root=resolved_root,
        manifest_bytes=manifest_bytes,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        immutable_hashes=(),
    )
    return RefinementWorkspace(
        root=resolved_root,
        editable=(resolved_root / "editable").resolve(),
        manifest_path=manifest_path,
        receipt=receipt,
    )


def _editor(tmp_path: Path, **kwargs) -> CodexWorkspaceEditor:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir(exist_ok=True)
    return CodexWorkspaceEditor(codex_home=codex_home, **kwargs)


def _pid_is_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetExitCodeProcess(
            process,
            ctypes.byref(exit_code),
        ):
            return False
        return exit_code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def _force_kill_pid(pid: int) -> None:
    if not _pid_is_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ("taskkill", "/PID", str(pid), "/T", "/F"),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    os.kill(pid, signal.SIGKILL)


async def _wait_until_pid_exits(pid: int, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while _pid_is_alive(pid) and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object semantics")
@pytest.mark.asyncio
async def test_windows_job_wrapper_preserves_child_exit_code(tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (tmp_path / "scratch").mkdir()
    process = await _spawn_subprocess(
        (sys.executable, "-I", "-c", "raise SystemExit(7)"),
        tmp_path,
        workspace_runner_module._build_child_environment(codex_home, tmp_path),
    )
    if process.stdin is not None:
        process.stdin.close()
        await process.stdin.wait_closed()
    try:
        assert await asyncio.wait_for(process.wait(), timeout=5) == 7
    finally:
        await _terminate_process_tree(process)


@pytest.mark.asyncio
async def test_cleanup_kills_child_after_process_root_already_exited(tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (tmp_path / "scratch").mkdir()
    child_pid_path = tmp_path / "child.pid"
    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import pathlib, subprocess, sys; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[2]],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
        "stderr=subprocess.DEVNULL,close_fds=True); "
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid),encoding='ascii')"
    )
    process = await _spawn_subprocess(
        (
            sys.executable,
            "-c",
            parent_code,
            str(child_pid_path),
            child_code,
        ),
        tmp_path,
        workspace_runner_module._build_child_environment(codex_home, tmp_path),
    )
    child_pid = 0
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
        child_pid = int(child_pid_path.read_text(encoding="ascii"))
        if os.name != "nt":
            assert _pid_is_alive(child_pid)

        await _terminate_process_tree(process)
        await _wait_until_pid_exits(child_pid)

        assert not _pid_is_alive(child_pid)
    finally:
        if child_pid:
            _force_kill_pid(child_pid)


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal semantics")
@pytest.mark.asyncio
async def test_posix_cleanup_kills_process_that_ignores_sigterm(tmp_path):
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (tmp_path / "scratch").mkdir()
    ready_path = tmp_path / "ready.pid"
    code = (
        "import os, pathlib, signal, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid()),encoding='ascii'); "
        "time.sleep(60)"
    )
    process = await _spawn_subprocess(
        (sys.executable, "-c", code, str(ready_path)),
        tmp_path,
        workspace_runner_module._build_child_environment(codex_home, tmp_path),
    )
    ignored_pid = process.pid
    try:
        deadline = asyncio.get_running_loop().time() + 5
        while not ready_path.exists() and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.02)
        assert ready_path.exists()

        await _terminate_process_tree(process)
        await _wait_until_pid_exits(ignored_pid)

        assert not _pid_is_alive(ignored_pid)
    finally:
        _force_kill_pid(ignored_pid)


@pytest.mark.asyncio
async def test_child_environment_is_explicit_and_excludes_ambient_secrets(
    tmp_path,
    monkeypatch,
):
    workspace = _prepare_workspace(tmp_path)
    codex_home = tmp_path / "dedicated-codex-home"
    codex_home.mkdir()
    sentinels = {
        "DATABASE_URL": "postgresql://must-not-leak",
        "KAIGO_BEARER_TOKEN": "must-not-leak",
        "SSH_AUTH_SOCK": "must-not-leak",
        "HTTPS_PROXY": "http://must-not-leak",
        "KAIGO_SECRET": "must-not-leak",
    }
    for name, value in sentinels.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TEMP", str(tmp_path / "ambient-temp"))
    monkeypatch.setenv("TMP", str(tmp_path / "ambient-tmp"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "ambient-tmpdir"))
    process = _FakeProcess(_event_stream())
    captured_environment: dict[str, str] = {}

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        captured_environment.update(env)
        return process

    await CodexWorkspaceEditor(codex_home=codex_home, spawn=spawn).run(
        workspace=workspace,
        timeout_seconds=1,
    )

    assert captured_environment["CODEX_HOME"] == str(codex_home.resolve())
    assert captured_environment["HOME"] == str(codex_home.resolve())
    assert captured_environment["PATH"]
    expected_temp = str((workspace.root / "scratch" / "process-tmp").resolve())
    assert captured_environment["TEMP"] == expected_temp
    assert captured_environment["TMP"] == expected_temp
    assert captured_environment["TMPDIR"] == expected_temp
    assert sentinels.keys().isdisjoint(captured_environment)


@pytest.mark.asyncio
async def test_runner_rejects_bare_workspace_path(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    process = _FakeProcess(_event_stream())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    with pytest.raises(TypeError, match="RefinementWorkspace"):
        await _editor(tmp_path, spawn=spawn).run(
            workspace=workspace.root,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_runner_rejects_workspace_with_mismatched_receipt_root(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    mismatched = RefinementWorkspace(
        root=workspace.root,
        editable=workspace.editable,
        manifest_path=workspace.manifest_path,
        receipt=FrozenWorkspaceReceipt(
            root=tmp_path / "different-workspace",
            manifest_bytes=workspace.receipt.manifest_bytes,
            manifest_sha256=workspace.receipt.manifest_sha256,
            immutable_hashes=workspace.receipt.immutable_hashes,
        ),
    )

    with pytest.raises(ValueError, match="receipt"):
        await _editor(tmp_path).run(
            workspace=mismatched,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_runner_rejects_symlinked_workspace_root(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    alias = tmp_path / "workspace-alias"
    try:
        alias.symlink_to(workspace.root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")
    linked = RefinementWorkspace(
        root=alias,
        editable=alias / "editable",
        manifest_path=alias / "manifest.json",
        receipt=FrozenWorkspaceReceipt(
            root=alias,
            manifest_bytes=workspace.receipt.manifest_bytes,
            manifest_sha256=workspace.receipt.manifest_sha256,
            immutable_hashes=workspace.receipt.immutable_hashes,
        ),
    )

    with pytest.raises(ValueError, match="symlink"):
        await _editor(tmp_path).run(
            workspace=linked,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_timeout_is_not_masked_when_cleanup_fails(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    process = _FakeProcess(_event_stream(), blocker=asyncio.Event())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        raise RuntimeError("cleanup exploded")

    with pytest.raises(WorkspaceEditorTimeout):
        await _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_cleanup_failure_rejects_an_otherwise_successful_turn(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    process = _FakeProcess(_event_stream())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        raise RuntimeError("cleanup exploded")

    with pytest.raises(WorkspaceEditorUnavailable, match="cleanup"):
        await _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_cancellation_is_not_masked_when_cleanup_fails(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    process = _FakeProcess(_event_stream(), blocker=asyncio.Event())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        raise RuntimeError("cleanup exploded")

    task = asyncio.create_task(
        _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=10,
        )
    )
    await process.wait_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancellation_during_failing_cleanup_wins_over_cleanup_error(tmp_path):
    workspace = _prepare_workspace(tmp_path, status="pending")
    process = _FakeProcess(_event_stream())
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        cleanup_started.set()
        await cleanup_release.wait()
        raise RuntimeError("cleanup exploded after cancellation")

    task = asyncio.create_task(
        _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=10,
        )
    )
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    cleanup_release.set()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_runner_reaps_tree_before_reading_result(tmp_path):
    workspace = _prepare_workspace(tmp_path, status="pending")
    process = _FakeProcess(_event_stream())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        (workspace.root / "editable/result.json").write_text(
            json.dumps(
                {
                    "schema_version": "refinement-result.v1",
                    "status": "complete",
                    "public_summary": "Завершено после остановки процесса.",
                    "changed_files": ["editable/widget.css"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        await candidate.wait()

    receipt = await _editor(tmp_path, spawn=spawn, terminate=terminate).run(
        workspace=workspace,
        timeout_seconds=1,
    )

    assert receipt.thread_id == "thread-test"


@pytest.mark.asyncio
async def test_actual_child_receives_only_explicit_environment(tmp_path, monkeypatch):
    codex_home = tmp_path / "dedicated-codex-home"
    codex_home.mkdir()
    (tmp_path / "scratch").mkdir()
    monkeypatch.setenv("DATABASE_URL", "must-not-leak")
    monkeypatch.setenv("HTTPS_PROXY", "must-not-leak")
    capture_path = tmp_path / "child-env.json"
    capture_code = (
        "import json, os, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text("
        "json.dumps(dict(os.environ),sort_keys=True),encoding='utf-8')"
    )
    environment = workspace_runner_module._build_child_environment(
        codex_home,
        tmp_path,
    )
    process = await _spawn_subprocess(
        (sys.executable, "-I", "-c", capture_code, str(capture_path)),
        tmp_path,
        environment,
    )
    if process.stdin is not None:
        process.stdin.close()
        await process.stdin.wait_closed()
    try:
        assert await asyncio.wait_for(process.wait(), timeout=5) == 0
    finally:
        await _terminate_process_tree(process)

    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert captured["CODEX_HOME"] == str(codex_home.resolve())
    assert captured["HOME"] == str(codex_home.resolve())
    assert captured["PATH"]
    expected_temp = str((tmp_path / "scratch" / "process-tmp").resolve())
    assert captured["TEMP"] == expected_temp
    assert captured["TMP"] == expected_temp
    assert captured["TMPDIR"] == expected_temp
    assert "DATABASE_URL" not in captured
    assert "HTTPS_PROXY" not in captured


@pytest.mark.asyncio
async def test_runner_sends_static_prompt_only_on_stdin_and_returns_last_message(
    tmp_path,
):
    workspace = _prepare_workspace(tmp_path)
    secret = "SECRET-CUSTOMER-TEXT-MUST-STAY-IN-REQUEST-FILE"
    (workspace.root / "request.md").write_text(secret, encoding="utf-8")
    process = _FakeProcess(
        _event_stream(final_texts=("Промежуточно", "Финальная справка"))
    )
    commands: list[tuple[str, ...]] = []

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        commands.append(command)
        assert cwd == workspace.root
        return process

    editor = _editor(tmp_path, spawn=spawn)
    receipt = await editor.run(workspace=workspace, timeout_seconds=1)

    assert len(commands) == 1
    assert secret not in "\0".join(commands[0])
    assert secret.encode() not in process.stdin.payload
    prompt = process.stdin.payload.decode("utf-8")
    assert "CONTRACT.md" in prompt
    assert "request.md" in prompt
    assert "python validate.py" in prompt
    assert "files are authoritative" in prompt.lower()
    assert process.stdin.closed
    assert receipt.thread_id == "thread-test"
    assert receipt.final_text == "Финальная справка"
    assert receipt.input_tokens == 101
    assert receipt.cached_input_tokens == 23
    assert receipt.output_tokens == 47


@pytest.mark.asyncio
async def test_runner_treats_completed_files_as_authoritative(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    process = _FakeProcess(
        _event_stream(final_texts=("Я ошибочно утверждаю, что result pending",))
    )

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    receipt = await _editor(tmp_path, spawn=spawn).run(
        workspace=workspace,
        timeout_seconds=1,
    )

    assert receipt.final_text.startswith("Я ошибочно")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stdout", "returncode", "error_type"),
    (
        (_event_stream(), 9, WorkspaceEditorUnavailable),
        (_event_stream(terminal="turn.failed"), 0, WorkspaceEditorUnavailable),
        (b"not-json\n", 0, WorkspaceEditorInvalidOutput),
        (
            _event_stream(terminal="turn.started"),
            0,
            WorkspaceEditorInvalidOutput,
        ),
    ),
)
async def test_runner_rejects_failed_or_incomplete_event_streams(
    tmp_path,
    stdout,
    returncode,
    error_type,
):
    workspace = _prepare_workspace(tmp_path)
    process = _FakeProcess(stdout, returncode=returncode)

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    with pytest.raises(error_type):
        await _editor(tmp_path, spawn=spawn).run(
            workspace=workspace,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_runner_rejects_missing_thread_and_missing_agent_message(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    streams = (
        b'{"type":"item.completed","item":{"type":"agent_message","text":"x"}}\n'
        b'{"type":"turn.completed"}\n',
        b'{"type":"thread.started","thread_id":"thread"}\n{"type":"turn.completed"}\n',
    )
    for stream in streams:
        process = _FakeProcess(stream)

        async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
            return process

        with pytest.raises(WorkspaceEditorInvalidOutput):
            await _editor(tmp_path, spawn=spawn).run(
                workspace=workspace,
                timeout_seconds=1,
            )


@pytest.mark.asyncio
async def test_runner_rejects_pending_result_after_completed_turn(tmp_path):
    workspace = _prepare_workspace(tmp_path, status="pending")
    process = _FakeProcess(_event_stream())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    with pytest.raises(WorkspaceEditorInvalidOutput, match="result"):
        await _editor(tmp_path, spawn=spawn).run(
            workspace=workspace,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_runner_rejects_symlinked_result_without_following_it(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    result_path = workspace.root / "editable/result.json"
    target_path = workspace.root / "scratch/result-target.json"
    target_path.write_bytes(result_path.read_bytes())
    probe_path = workspace.root / "scratch/symlink-probe"
    try:
        probe_path.symlink_to(target_path)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    probe_path.unlink()
    process = _FakeProcess(_event_stream())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        result_path.unlink()
        result_path.symlink_to(target_path)
        await candidate.wait()

    with pytest.raises(WorkspaceEditorInvalidOutput, match="result"):
        await _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_runner_rejects_hardlinked_result_after_cleanup(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    result_path = workspace.root / "editable/result.json"
    target_path = workspace.root / "scratch/result-target.json"
    target_path.write_bytes(result_path.read_bytes())
    probe_path = workspace.root / "scratch/hardlink-probe"
    try:
        os.link(target_path, probe_path)
    except OSError as error:
        pytest.skip(f"hardlinks unavailable: {error}")
    probe_path.unlink()
    process = _FakeProcess(_event_stream())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        result_path.unlink()
        os.link(target_path, result_path)
        await candidate.wait()

    with pytest.raises(WorkspaceEditorInvalidOutput, match="result"):
        await _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_runner_bounds_result_read_to_16_kib(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    result_path = workspace.root / "editable/result.json"
    process = _FakeProcess(_event_stream())

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        result_path.write_bytes(b"x" * (16 * 1024 + 1))
        await candidate.wait()

    with pytest.raises(WorkspaceEditorInvalidOutput, match="size limit"):
        await _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_runner_caps_stdout(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    process = _FakeProcess(_event_stream(final_texts=("x" * 500,)))

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    with pytest.raises(WorkspaceEditorOutputLimit):
        await _editor(tmp_path, spawn=spawn, max_stdout_bytes=128).run(
            workspace=workspace,
            timeout_seconds=1,
        )


@pytest.mark.asyncio
async def test_timeout_terminates_and_reaps_process(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    blocker = asyncio.Event()
    process = _FakeProcess(_event_stream(), blocker=blocker)
    terminated: list[int] = []

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        terminated.append(candidate.pid)
        blocker.set()
        await candidate.wait()

    with pytest.raises(WorkspaceEditorTimeout):
        await _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=0.01,
        )

    assert terminated == [process.pid]
    assert process.reaped


@pytest.mark.asyncio
async def test_cancellation_terminates_and_reaps_process(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    blocker = asyncio.Event()
    process = _FakeProcess(_event_stream(), blocker=blocker)
    terminated: list[int] = []
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        return process

    async def terminate(candidate) -> None:
        terminated.append(candidate.pid)
        cleanup_started.set()
        await cleanup_release.wait()
        blocker.set()
        await candidate.wait()

    task = asyncio.create_task(
        _editor(tmp_path, spawn=spawn, terminate=terminate).run(
            workspace=workspace,
            timeout_seconds=10,
        )
    )
    await process.wait_started.wait()
    task.cancel()
    await cleanup_started.wait()
    task.cancel()
    cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert terminated == [process.pid]
    assert process.reaped


@pytest.mark.asyncio
async def test_every_run_starts_a_fresh_ephemeral_process(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    commands: list[tuple[str, ...]] = []

    async def spawn(command: tuple[str, ...], cwd: Path, env: dict[str, str]):
        commands.append(command)
        return _FakeProcess(_event_stream(thread_id=f"thread-{len(commands)}"))

    editor = _editor(tmp_path, spawn=spawn)
    first = await editor.run(workspace=workspace, timeout_seconds=1)
    second = await editor.run(workspace=workspace, timeout_seconds=1)

    assert first.thread_id != second.thread_id
    assert len(commands) == 2
    assert all("--ephemeral" in command for command in commands)
    assert all("resume" not in command for command in commands)


def test_editor_requires_existing_dedicated_codex_home(tmp_path):
    with pytest.raises(ValueError, match="codex_home"):
        CodexWorkspaceEditor(codex_home=tmp_path / "missing")


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable fixture")
@pytest.mark.asyncio
async def test_runner_executes_a_real_fake_cli(tmp_path):
    workspace = _prepare_workspace(tmp_path)
    executable = tmp_path / "fake-codex"
    executable.write_text(
        "#!"
        + sys.executable
        + "\n"
        + "import json, sys\n"
        + "prompt = sys.stdin.read()\n"
        + "assert 'CONTRACT.md' in prompt\n"
        + "events = [\n"
        + " {'type':'thread.started','thread_id':'real-fake'},\n"
        + " {'type':'item.completed','item':{'type':'agent_message','text':'ok'}},\n"
        + " {'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':2}},\n"
        + "]\n"
        + "print('\\n'.join(json.dumps(event) for event in events))\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)

    receipt = await _editor(tmp_path, executable=str(executable)).run(
        workspace=workspace,
        timeout_seconds=5,
    )

    assert receipt.thread_id == "real-fake"
