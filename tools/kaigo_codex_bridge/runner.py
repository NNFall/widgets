from __future__ import annotations

import asyncio
import json
import os
import signal
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import CodexBridgeConfig
from .state import BridgeEvent, BridgeStateStore, BridgeThread


_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "multi_agent",
    "browser_use",
    "computer_use",
    "image_generation",
    "in_app_browser",
    "hooks",
    "plugins",
    "goals",
    "workspace_dependencies",
)


@dataclass(frozen=True, slots=True)
class CodexUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True, slots=True)
class CodexTurnRequest:
    run_id: str
    conversation_key: str
    prompt: str
    response_schema: Mapping[str, Any] | None = None
    images: tuple[bytes, ...] = ()
    model: str | None = None


@dataclass(frozen=True, slots=True)
class CodexTurnResult:
    text: str
    parsed: Mapping[str, Any] | list[Any] | None
    usage: CodexUsage
    thread_id: str
    model: str
    duration_ms: int


class CodexBridgeError(RuntimeError):
    error_code = "provider_unavailable"


class CodexTimeout(CodexBridgeError):
    error_code = "generation_timeout"


class CodexInvalidOutput(CodexBridgeError):
    error_code = "invalid_response"


class CodexUnavailable(CodexBridgeError):
    error_code = "provider_unavailable"


class _Process(Protocol):
    pid: int
    returncode: int | None

    async def communicate(
        self, input: bytes | None = None
    ) -> tuple[bytes, bytes]: ...


_Spawn = Callable[[tuple[str, ...], Path], Awaitable[_Process]]
_Terminate = Callable[[_Process], Awaitable[None]]


class CodexRunner:
    def __init__(
        self,
        *,
        config: CodexBridgeConfig,
        state: BridgeStateStore,
        spawn: _Spawn | None = None,
        terminate: _Terminate | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self._spawn = spawn or _spawn_subprocess
        self._terminate = terminate or _terminate_process
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._lock_guard = asyncio.Lock()
        self._conversation_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self.config.work_root.mkdir(parents=True, exist_ok=True)

    async def run_turn(self, request: CodexTurnRequest) -> CodexTurnResult:
        if not isinstance(request.prompt, str) or not request.prompt.strip():
            raise ValueError("prompt must be non-empty")
        if len(request.images) > self.config.max_images:
            raise ValueError("too many images")
        if any(len(image) > self.config.max_image_bytes for image in request.images):
            raise ValueError("image exceeds configured size")
        model = request.model or self.config.model
        lock = await self._conversation_lock(
            request.run_id,
            request.conversation_key,
        )
        async with lock:
            async with self._semaphore:
                return await self._run_locked(request, model=model)

    async def finalize_run(self, run_id: str) -> tuple[str, ...]:
        archived: list[str] = []
        for record in self.state.list_active_threads(run_id):
            lock = await self._conversation_lock(run_id, record.conversation_key)
            async with lock:
                async with self._semaphore:
                    await self._archive(record)
                archived.append(record.thread_id)
        async with self._lock_guard:
            for key in tuple(self._conversation_locks):
                if key[0] == run_id:
                    self._conversation_locks.pop(key, None)
        return tuple(archived)

    async def _run_locked(
        self,
        request: CodexTurnRequest,
        *,
        model: str,
    ) -> CodexTurnResult:
        existing = self.state.get_active_thread(
            run_id=request.run_id,
            conversation_key=request.conversation_key,
        )
        self.state.append_event(
            BridgeEvent(
                event="turn.started",
                run_id=request.run_id,
                conversation_key=request.conversation_key,
                thread_id=existing.thread_id if existing else None,
                model=model,
            )
        )
        started = time.perf_counter()
        thread_id = existing.thread_id if existing else None
        try:
            with tempfile.TemporaryDirectory(
                prefix="turn-",
                dir=self.config.work_root,
            ) as raw_directory:
                directory = Path(raw_directory)
                command = self._build_command(
                    request,
                    model=model,
                    directory=directory,
                    thread_id=thread_id,
                )
                process = await self._spawn(command, directory)
                try:
                    stdout, _stderr = await asyncio.wait_for(
                        process.communicate(input=request.prompt.encode("utf-8")),
                        timeout=self.config.timeout_seconds,
                    )
                except TimeoutError as error:
                    await self._terminate(process)
                    raise CodexTimeout("Codex turn timed out") from error
                if process.returncode != 0:
                    raise CodexUnavailable("Codex process failed")
                parsed_stream = _parse_event_stream(stdout)

            observed_thread = parsed_stream.thread_id or thread_id
            if observed_thread is None:
                raise CodexInvalidOutput("Codex did not return a thread id")
            if thread_id is not None and observed_thread != thread_id:
                raise CodexInvalidOutput("Codex resumed a different thread")
            if existing is None:
                self.state.bind_thread(
                    run_id=request.run_id,
                    conversation_key=request.conversation_key,
                    thread_id=observed_thread,
                    model=model,
                )
            result_payload: Mapping[str, Any] | list[Any] | None = None
            if request.response_schema is not None:
                try:
                    candidate = json.loads(parsed_stream.text)
                except json.JSONDecodeError as error:
                    raise CodexInvalidOutput(
                        "Codex structured response was invalid JSON"
                    ) from error
                if not isinstance(candidate, (dict, list)):
                    raise CodexInvalidOutput(
                        "Codex structured response has invalid root"
                    )
                result_payload = candidate
            duration_ms = _elapsed_ms(started)
            result = CodexTurnResult(
                text=parsed_stream.text,
                parsed=result_payload,
                usage=parsed_stream.usage,
                thread_id=observed_thread,
                model=model,
                duration_ms=duration_ms,
            )
            self.state.append_event(
                BridgeEvent(
                    event="turn.completed",
                    run_id=request.run_id,
                    conversation_key=request.conversation_key,
                    thread_id=observed_thread,
                    model=model,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                    cached_input_tokens=result.usage.cached_input_tokens,
                    duration_ms=duration_ms,
                )
            )
            return result
        except CodexBridgeError as error:
            self.state.append_event(
                BridgeEvent(
                    event="turn.failed",
                    run_id=request.run_id,
                    conversation_key=request.conversation_key,
                    thread_id=thread_id,
                    model=model,
                    duration_ms=_elapsed_ms(started),
                    error_code=error.error_code,
                )
            )
            raise

    def _build_command(
        self,
        request: CodexTurnRequest,
        *,
        model: str,
        directory: Path,
        thread_id: str | None,
    ) -> tuple[str, ...]:
        command: list[str] = [
            self.config.executable,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-C",
            str(directory),
            "-s",
            "read-only",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{self.config.reasoning_effort}"',
            "-c",
            'approval_policy="never"',
            "-c",
            'web_search="disabled"',
            "--ignore-user-config",
            "--ignore-rules",
            "--strict-config",
        ]
        for feature in _DISABLED_FEATURES:
            command.extend(("--disable", feature))
        if request.response_schema is not None:
            schema_path = directory / "response-schema.json"
            schema_path.write_text(
                json.dumps(request.response_schema, ensure_ascii=False),
                encoding="utf-8",
            )
            command.extend(("--output-schema", str(schema_path)))
        for index, image in enumerate(request.images):
            image_path = directory / f"image-{index}{_image_suffix(image)}"
            image_path.write_bytes(image)
            command.extend(("--image", str(image_path)))
        if thread_id is not None:
            command.extend(("resume", thread_id))
        command.append("-")
        return tuple(command)

    async def _conversation_lock(
        self,
        run_id: str,
        conversation_key: str,
    ) -> asyncio.Lock:
        # State access performs strict identifier validation before a subprocess
        # can be started or a filesystem path can be derived.
        self.state.get_active_thread(
            run_id=run_id,
            conversation_key=conversation_key,
        )
        key = (run_id, conversation_key)
        async with self._lock_guard:
            return self._conversation_locks.setdefault(key, asyncio.Lock())

    async def _archive(self, record: BridgeThread) -> None:
        process = await self._spawn(
            (self.config.executable, "archive", record.thread_id),
            self.config.work_root,
        )
        try:
            await asyncio.wait_for(
                process.communicate(input=b""),
                timeout=min(30.0, self.config.timeout_seconds),
            )
        except TimeoutError as error:
            await self._terminate(process)
            self._append_archive_failure(record, "generation_timeout")
            raise CodexTimeout("Codex archive timed out") from error
        if process.returncode != 0:
            self._append_archive_failure(record, "provider_unavailable")
            raise CodexUnavailable("Codex archive failed")
        self.state.mark_archived(
            run_id=record.run_id,
            conversation_key=record.conversation_key,
        )
        self.state.append_event(
            BridgeEvent(
                event="thread.archived",
                run_id=record.run_id,
                conversation_key=record.conversation_key,
                thread_id=record.thread_id,
                model=record.model,
            )
        )

    def _append_archive_failure(self, record: BridgeThread, code: str) -> None:
        self.state.append_event(
            BridgeEvent(
                event="thread.archive_failed",
                run_id=record.run_id,
                conversation_key=record.conversation_key,
                thread_id=record.thread_id,
                model=record.model,
                error_code=code,
            )
        )


@dataclass(frozen=True, slots=True)
class _ParsedStream:
    thread_id: str | None
    text: str
    usage: CodexUsage


def _parse_event_stream(stdout: bytes) -> _ParsedStream:
    thread_id: str | None = None
    final_text: str | None = None
    usage: CodexUsage | None = None
    terminal_failure = False
    try:
        lines = stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise CodexInvalidOutput("Codex JSONL was not UTF-8") from error
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CodexInvalidOutput("Codex emitted invalid JSONL") from error
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "thread.started" and isinstance(
            event.get("thread_id"), str
        ):
            thread_id = event["thread_id"]
        elif event_type == "item.completed":
            item = event.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                final_text = item["text"]
        elif event_type == "turn.completed":
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = CodexUsage(
                    input_tokens=_safe_token_count(raw_usage.get("input_tokens")),
                    cached_input_tokens=_safe_token_count(
                        raw_usage.get("cached_input_tokens")
                    ),
                    output_tokens=_safe_token_count(raw_usage.get("output_tokens")),
                )
        elif event_type in {"turn.failed", "error"}:
            terminal_failure = True
    if terminal_failure:
        raise CodexUnavailable("Codex reported a failed turn")
    if final_text is None:
        raise CodexInvalidOutput("Codex did not emit a final agent message")
    return _ParsedStream(
        thread_id=thread_id,
        text=final_text,
        usage=usage or CodexUsage(),
    )


def _safe_token_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _image_suffix(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if image.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if image.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return ".webp"
    raise ValueError("unsupported image format")


async def _spawn_subprocess(command: tuple[str, ...], cwd: Path) -> _Process:
    return await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=(os.name != "nt"),
    )


async def _terminate_process(process: _Process) -> None:
    if os.name != "nt":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        terminate = getattr(process, "terminate", None)
        if callable(terminate):
            terminate()
    wait = getattr(process, "wait", None)
    if callable(wait):
        try:
            await asyncio.wait_for(wait(), timeout=2)
            return
        except TimeoutError:
            pass
    if os.name != "nt":
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except ProcessLookupError:
            return
    else:
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))
