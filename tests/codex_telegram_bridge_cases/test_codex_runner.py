import subprocess
from pathlib import Path

import pytest

from tools.codex_telegram_bridge.codex_runner import (
    CodexRunError,
    CodexRunner,
    run_process_tree,
)


THREAD_ID = "019f9e1b-fb04-7482-b62d-cee4c051131b"


def test_runner_uses_stdin_and_output_file_without_shell(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["args"] = args
        captured.update(kwargs)
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text("Ответ Codex", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="diagnostic", stderr="403 fallback")

    runner = CodexRunner(
        codex_command="codex.cmd",
        codex_home=tmp_path / "home",
        data_dir=tmp_path / "data",
        timeout_seconds=120,
        process_runner=fake_run,
        base_environ={"PATH": "test-path", "TELEGRAM_BOT_TOKEN": "must-not-leak"},
    )

    answer = runner.run(THREAD_ID, "текст с `quotes` & symbols", request_id="42")

    args = captured["args"]
    assert args[:5] == ["codex.cmd", "exec", "resume", "--all", "--skip-git-repo-check"]
    assert args[-2:] == [THREAD_ID, "-"]
    assert captured["input"] == "текст с `quotes` & symbols"
    assert captured["shell"] is False
    assert captured["timeout"] == 120
    assert captured["env"]["CODEX_HOME"] == str(tmp_path / "home")
    assert "TELEGRAM_BOT_TOKEN" not in captured["env"]
    assert answer == "Ответ Codex"
    assert runner.response_path("42").read_text(encoding="utf-8") == "Ответ Codex"
    runner.forget("42")
    assert not runner.response_path("42").exists()


def test_runner_raises_clean_error_on_nonzero_exit(tmp_path: Path) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args,
            1,
            stdout="",
            stderr="internal failure: private prompt",
        )

    runner = CodexRunner(
        codex_command="codex.cmd",
        codex_home=tmp_path,
        data_dir=tmp_path,
        timeout_seconds=120,
        process_runner=fake_run,
    )

    with pytest.raises(CodexRunError, match="exit code 1") as caught:
        runner.run(THREAD_ID, "private prompt", request_id="43")

    assert "private prompt" not in str(caught.value)


def test_runner_reports_timeout_without_prompt_text(tmp_path: Path) -> None:
    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(args, 10)

    runner = CodexRunner(
        codex_command="codex.cmd",
        codex_home=tmp_path,
        data_dir=tmp_path,
        timeout_seconds=10,
        process_runner=fake_run,
    )

    with pytest.raises(CodexRunError) as caught:
        runner.run(THREAD_ID, "do not leak this prompt", request_id="44")

    assert "do not leak" not in str(caught.value)
    assert "10" in str(caught.value)
    assert list(tmp_path.glob("codex-response-*.txt")) == []


def test_runner_reuses_durable_response_after_bridge_restart(tmp_path: Path) -> None:
    calls = 0

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        output_path = Path(args[args.index("--output-last-message") + 1])
        output_path.write_text("durable answer", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    runner = CodexRunner(
        codex_command="codex.cmd",
        codex_home=tmp_path,
        data_dir=tmp_path,
        timeout_seconds=120,
        process_runner=fake_run,
    )

    assert runner.run(THREAD_ID, "prompt", request_id="job-9") == "durable answer"
    assert runner.run(THREAD_ID, "prompt", request_id="job-9") == "durable answer"
    assert calls == 1


def test_process_tree_terminates_children_on_timeout() -> None:
    terminated: list[int] = []

    class FakeProcess:
        pid = 987
        returncode = None

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, input: str | None = None, timeout: int | None = None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["codex.cmd"], timeout)
            self.returncode = 1
            return "", "terminated"

    process = FakeProcess()

    with pytest.raises(subprocess.TimeoutExpired):
        run_process_tree(
            ["codex.cmd"],
            input="prompt",
            timeout=1,
            env={},
            cwd=".",
            popen_factory=lambda *args, **kwargs: process,
            tree_terminator=terminated.append,
        )

    assert terminated == [987]
    assert process.calls == 2
