from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest

from tools.kaigo_codex_bridge.config import CodexBridgeConfig
from tools.kaigo_codex_bridge.runner import (
    CodexInvalidOutput,
    CodexRunner,
    CodexTimeout,
    CodexTurnRequest,
)
from tools.kaigo_codex_bridge.state import BridgeStateStore


def _event_stream(
    thread_id: str,
    *,
    text: str = '{"ok":true}',
    input_tokens: int = 120,
    output_tokens: int = 30,
) -> bytes:
    events = [
        {"type": "thread.started", "thread_id": thread_id},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "item-1", "type": "agent_message", "text": text},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": 20,
                "output_tokens": output_tokens,
            },
        },
    ]
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


class FakeProcess:
    def __init__(
        self,
        stdout: bytes,
        *,
        stderr: bytes = b"",
        returncode: int = 0,
        blocker: asyncio.Event | None = None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.blocker = blocker
        self.stdin_payload: bytes | None = None
        self.pid = 4242

    async def communicate(self, input: bytes | None = None):
        self.stdin_payload = input
        if self.blocker is not None:
            await self.blocker.wait()
        return self._stdout, self._stderr


def _config(tmp_path, **changes) -> CodexBridgeConfig:
    values = {
        "executable": "codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "max_concurrency": 3,
        "timeout_seconds": 30.0,
        "state_root": tmp_path / "state",
        "work_root": tmp_path / "work",
        "socket_path": tmp_path / "bridge.sock",
    }
    values.update(changes)
    return CodexBridgeConfig(**values)


@pytest.mark.asyncio
async def test_runner_starts_luna_max_turn_with_schema_and_image(tmp_path) -> None:
    thread_id = str(uuid4())
    process = FakeProcess(_event_stream(thread_id))
    captured: dict[str, object] = {}

    async def spawn(command: tuple[str, ...], cwd: Path):
        captured["command"] = command
        captured["cwd"] = cwd
        image_path = Path(command[command.index("--image") + 1])
        schema_path = Path(command[command.index("--output-schema") + 1])
        captured["image"] = image_path.read_bytes()
        captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        return process

    runner = CodexRunner(
        config=_config(tmp_path),
        state=BridgeStateStore(tmp_path / "state"),
        spawn=spawn,
    )
    run_id = str(uuid4())
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    result = await runner.run_turn(
        CodexTurnRequest(
            run_id=run_id,
            conversation_key="visual:judge",
            prompt="Проверь виджет",
            response_schema=schema,
            images=(b"\x89PNG\r\n\x1a\nimage",),
        )
    )

    command = captured["command"]
    assert isinstance(command, tuple)
    assert command[:2] == ("codex", "exec")
    assert "resume" not in command
    assert command[-1] == "-"
    assert ("-m", "gpt-5.6-luna") == (
        command[command.index("-m")],
        command[command.index("-m") + 1],
    )
    assert 'model_reasoning_effort="max"' in command
    assert 'approval_policy="never"' in command
    assert "read-only" in command
    for feature in (
        "shell_tool",
        "unified_exec",
        "apps",
        "multi_agent",
        "browser_use",
        "computer_use",
        "image_generation",
    ):
        assert ("--disable", feature) in tuple(zip(command, command[1:]))
    assert process.stdin_payload == "Проверь виджет".encode("utf-8")
    assert captured["image"] == b"\x89PNG\r\n\x1a\nimage"
    assert captured["schema"] == schema
    assert result.thread_id == thread_id
    assert result.parsed == {"ok": True}
    assert result.usage.input_tokens == 120
    assert result.usage.cached_input_tokens == 20
    assert result.usage.output_tokens == 30


@pytest.mark.asyncio
async def test_runner_resumes_bound_conversation(tmp_path) -> None:
    run_id = str(uuid4())
    thread_id = str(uuid4())
    state = BridgeStateStore(tmp_path / "state")
    state.bind_thread(
        run_id=run_id,
        conversation_key="repair",
        thread_id=thread_id,
        model="gpt-5.6-luna",
    )
    commands: list[tuple[str, ...]] = []

    async def spawn(command: tuple[str, ...], cwd: Path):
        commands.append(command)
        return FakeProcess(_event_stream(thread_id, text="fixed"))

    runner = CodexRunner(config=_config(tmp_path), state=state, spawn=spawn)
    result = await runner.run_turn(
        CodexTurnRequest(
            run_id=run_id,
            conversation_key="repair",
            prompt="Исправь замечания",
        )
    )

    command = commands[0]
    resume_index = command.index("resume")
    assert command[resume_index + 1 : resume_index + 3] == (thread_id, "-")
    assert result.text == "fixed"


@pytest.mark.asyncio
async def test_runner_rejects_invalid_json_for_structured_turn(tmp_path) -> None:
    process = FakeProcess(_event_stream(str(uuid4()), text="not-json"))

    async def spawn(command: tuple[str, ...], cwd: Path):
        return process

    runner = CodexRunner(
        config=_config(tmp_path),
        state=BridgeStateStore(tmp_path / "state"),
        spawn=spawn,
    )

    with pytest.raises(CodexInvalidOutput, match="structured"):
        await runner.run_turn(
            CodexTurnRequest(
                run_id=str(uuid4()),
                conversation_key="build",
                prompt="Build",
                response_schema={"type": "object"},
            )
        )


@pytest.mark.asyncio
async def test_runner_caps_global_concurrency_at_three(tmp_path) -> None:
    release = asyncio.Event()
    three_started = asyncio.Event()
    active = 0
    maximum = 0

    async def spawn(command: tuple[str, ...], cwd: Path):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 3:
            three_started.set()

        class CountingProcess(FakeProcess):
            async def communicate(self, input: bytes | None = None):
                nonlocal active
                try:
                    await release.wait()
                    return _event_stream(str(uuid4())), b""
                finally:
                    active -= 1

        return CountingProcess(b"")

    runner = CodexRunner(
        config=_config(tmp_path, max_concurrency=3),
        state=BridgeStateStore(tmp_path / "state"),
        spawn=spawn,
    )
    tasks = [
        asyncio.create_task(
            runner.run_turn(
                CodexTurnRequest(
                    run_id=str(uuid4()),
                    conversation_key="build",
                    prompt=f"Build {index}",
                )
            )
        )
        for index in range(4)
    ]

    await asyncio.wait_for(three_started.wait(), timeout=1)
    await asyncio.sleep(0.05)
    assert maximum == 3
    release.set()
    await asyncio.gather(*tasks)
    assert maximum == 3


@pytest.mark.asyncio
async def test_runner_serializes_same_conversation(tmp_path) -> None:
    first_release = asyncio.Event()
    first_started = asyncio.Event()
    spawn_count = 0
    thread_id = str(uuid4())

    async def spawn(command: tuple[str, ...], cwd: Path):
        nonlocal spawn_count
        spawn_count += 1
        if spawn_count == 1:
            first_started.set()
            return FakeProcess(_event_stream(thread_id), blocker=first_release)
        return FakeProcess(_event_stream(thread_id))

    runner = CodexRunner(
        config=_config(tmp_path),
        state=BridgeStateStore(tmp_path / "state"),
        spawn=spawn,
    )
    run_id = str(uuid4())
    first = asyncio.create_task(
        runner.run_turn(CodexTurnRequest(run_id, "build", "First"))
    )
    await first_started.wait()
    second = asyncio.create_task(
        runner.run_turn(CodexTurnRequest(run_id, "build", "Second"))
    )
    await asyncio.sleep(0.05)
    assert spawn_count == 1
    first_release.set()
    await asyncio.gather(first, second)
    assert spawn_count == 2


@pytest.mark.asyncio
async def test_runner_terminates_timed_out_process(tmp_path) -> None:
    process = FakeProcess(b"", blocker=asyncio.Event())
    terminated: list[int] = []

    async def spawn(command: tuple[str, ...], cwd: Path):
        return process

    async def terminate(candidate) -> None:
        terminated.append(candidate.pid)

    runner = CodexRunner(
        config=_config(tmp_path, timeout_seconds=0.01),
        state=BridgeStateStore(tmp_path / "state"),
        spawn=spawn,
        terminate=terminate,
    )

    with pytest.raises(CodexTimeout):
        await runner.run_turn(
            CodexTurnRequest(str(uuid4()), "build", "Never returns")
        )

    assert terminated == [4242]
