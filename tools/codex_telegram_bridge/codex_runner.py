from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Mapping

from .config import validate_thread_id


class CodexRunError(RuntimeError):
    """Raised when Codex CLI does not produce a usable final response."""


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


def _terminate_process_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
        return
    try:
        os.kill(pid, 9)
    except ProcessLookupError:
        pass


def run_process_tree(
    args: list[str],
    *,
    input: str,
    timeout: int,
    env: Mapping[str, str],
    cwd: str,
    text: bool = True,
    encoding: str = "utf-8",
    errors: str = "replace",
    stdout: int = subprocess.PIPE,
    stderr: int = subprocess.PIPE,
    shell: bool = False,
    popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    tree_terminator: Callable[[int], None] = _terminate_process_tree,
) -> subprocess.CompletedProcess[str]:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    process = popen_factory(
        args,
        stdin=subprocess.PIPE,
        stdout=stdout,
        stderr=stderr,
        text=text,
        encoding=encoding,
        errors=errors,
        env=dict(env),
        cwd=cwd,
        shell=shell,
        creationflags=creationflags,
    )
    try:
        captured_stdout, captured_stderr = process.communicate(input=input, timeout=timeout)
    except subprocess.TimeoutExpired:
        tree_terminator(process.pid)
        process.communicate()
        raise
    return subprocess.CompletedProcess(
        args,
        int(process.returncode or 0),
        stdout=captured_stdout,
        stderr=captured_stderr,
    )


class CodexRunner:
    def __init__(
        self,
        *,
        codex_command: str,
        codex_home: str | Path,
        data_dir: str | Path,
        timeout_seconds: int,
        process_runner: ProcessRunner = run_process_tree,
        base_environ: Mapping[str, str] | None = None,
    ) -> None:
        self.codex_command = codex_command
        self.codex_home = Path(codex_home)
        self.data_dir = Path(data_dir)
        self.timeout_seconds = timeout_seconds
        self.process_runner = process_runner
        self.base_environ = dict(os.environ if base_environ is None else base_environ)

    def build_command(self, thread_id: str, output_path: Path) -> list[str]:
        return [
            self.codex_command,
            "exec",
            "resume",
            "--all",
            "--skip-git-repo-check",
            "--output-last-message",
            str(output_path),
            validate_thread_id(thread_id),
            "-",
        ]

    def response_path(self, request_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", request_id):
            raise CodexRunError("Codex request ID contains unsupported characters")
        return self.data_dir / f"codex-response-{request_id}.txt"

    def forget(self, request_id: str) -> None:
        self.response_path(request_id).unlink(missing_ok=True)

    def run(self, thread_id: str, prompt: str, *, request_id: str) -> str:
        if not prompt.strip():
            raise CodexRunError("Codex prompt is empty")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.response_path(request_id)
        if output_path.exists():
            durable_response = output_path.read_text(encoding="utf-8").strip()
            if durable_response:
                return durable_response
            output_path.unlink(missing_ok=True)
        environment = dict(self.base_environ)
        environment.pop("TELEGRAM_BOT_TOKEN", None)
        environment["CODEX_HOME"] = str(self.codex_home)
        command = self.build_command(thread_id, output_path)
        try:
            completed = self.process_runner(
                    command,
                    input=prompt,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                    env=environment,
                    cwd=str(self.data_dir),
                    shell=False,
                )
        except subprocess.TimeoutExpired as exc:
            output_path.unlink(missing_ok=True)
            raise CodexRunError(
                f"Codex did not finish within {self.timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            output_path.unlink(missing_ok=True)
            raise CodexRunError(f"Codex CLI could not be started: {exc}") from exc
        if completed.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise CodexRunError(
                f"Codex CLI failed with exit code {completed.returncode}"
            )
        response = output_path.read_text(encoding="utf-8").strip()
        if not response:
            output_path.unlink(missing_ok=True)
            raise CodexRunError("Codex CLI finished without a final response")
        return response
