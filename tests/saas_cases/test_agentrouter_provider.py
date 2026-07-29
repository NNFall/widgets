from __future__ import annotations

import asyncio
import json
import subprocess
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderUnavailable,
)
from app.models.providers import agentrouter_qwen
from app.models.providers.agentrouter_qwen import (
    AgentRouterQwenProvider,
    parse_qwen_json_output,
)
from app.models.router import InMemoryModelCallAudit, ModelPolicy, ModelRouter, ProviderTarget


def _event_stream(
    result: str,
    *,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> bytes:
    return json.dumps(
        [
            {
                "type": "result",
                "subtype": "success",
                "session_id": "session-generated",
                "result": result,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            }
        ]
    ).encode()


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = 0,
        error: BaseException | None = None,
        pid: int = 4242,
    ):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.pid = pid
        self.kill = Mock()
        self.wait = AsyncMock()

    async def communicate(self, payload: bytes = b"") -> tuple[bytes, bytes]:
        if self.error is not None:
            raise self.error
        return self.stdout, self.stderr


class StructuredFallback:
    capabilities = ProviderCapabilities(images=True, structured_output=True)

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(text='{"ok":true}', parsed={"ok": True})


def test_qwen_event_stream_result_is_normalized() -> None:
    payload = [
        {"type": "system", "subtype": "init", "session_id": "session-1", "model": "glm-5.2"},
        {
            "type": "result",
            "subtype": "success",
            "session_id": "session-1",
            "result": '{"artifact":"ok"}',
            "usage": {
                "input_tokens": 8013,
                "output_tokens": 122,
                "cache_read_input_tokens": 200,
                "total_tokens": 8135,
            },
            "stats": {
                "models": {"glm-5.2": {"tokens": {"thoughts": 41}}},
            },
        },
    ]

    response = parse_qwen_json_output(json.dumps(payload), model="glm-5.2")

    assert response.text == '{"artifact":"ok"}'
    assert response.parsed == {"artifact": "ok"}
    assert response.request_id == "session-1"
    assert response.usage.input_tokens == 8013
    assert response.usage.output_tokens == 122
    assert response.usage.thinking_tokens == 41


def test_markdown_fenced_json_is_parsed_without_losing_text() -> None:
    payload = [{
        "type": "result",
        "subtype": "success",
        "session_id": "session-2",
        "result": "```json\n{\"ok\": true}\n```",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }]

    response = parse_qwen_json_output(json.dumps(payload), model="gpt-5.5")

    assert response.parsed == {"ok": True}
    assert response.text.startswith("```json")


def test_agentrouter_declares_that_binary_images_are_unsupported() -> None:
    assert AgentRouterQwenProvider.capabilities.images is False
    assert AgentRouterQwenProvider.capabilities.structured_output is True


@pytest.mark.asyncio
async def test_structured_generate_rejects_non_json_result() -> None:
    provider = AgentRouterQwenProvider(api_key="secret", executable="qwen")
    process = FakeProcess(stdout=_event_stream("not json"))

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=process),
    ):
        with pytest.raises(InvalidModelResponse):
            await provider.generate(
                ModelRequest(prompt="Return JSON", response_schema={"type": "object"}),
                model="glm-5.2",
            )


@pytest.mark.asyncio
async def test_structured_failure_is_audited_before_router_fallback() -> None:
    primary = AgentRouterQwenProvider(api_key="secret", executable="qwen")
    fallback = StructuredFallback()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"agentrouter": primary, "gemini": fallback},
        policies={
            ("code_review", "standard"): ModelPolicy(
                prompt_version="review-v2",
                targets=(
                    ProviderTarget("agentrouter", "glm-5.2", 6_000_000, 6_000_000),
                    ProviderTarget("gemini", "gemini-3.5-flash", 1_000_000, 2_000_000),
                ),
            )
        },
        audit=audit,
    )
    request = ModelRequest(prompt="Return JSON", response_schema={"type": "object"})

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=FakeProcess(stdout=_event_stream("not json"))),
    ):
        response = await router.generate(
            role="code_review",
            mode="standard",
            request=request,
        )

    assert response.parsed == {"ok": True}
    assert fallback.requests == [request]
    assert [call.status for call in audit.calls] == ["failed", "completed"]
    assert audit.calls[0].error_code == "invalid_response"
    assert audit.calls[0].input_tokens == 10
    assert audit.calls[0].output_tokens == 5
    assert audit.calls[0].cost_microusd == 90
    assert audit.calls[0].request_id == "session-generated"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        "[]",
        "{}",
        '{"ok":true,"extra":1}',
        "Here is the result: {\"ok\":true}",
        "```json\n{\"ok\":true}\n```",
    ],
)
async def test_structured_generate_requires_exact_schema_valid_json(result: str) -> None:
    provider = AgentRouterQwenProvider(api_key="unit-test-key", executable="qwen")
    schema = {
        "type": "object",
        "required": ["ok"],
        "additionalProperties": False,
        "properties": {"ok": {"type": "boolean"}},
    }

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=FakeProcess(stdout=_event_stream(result))),
    ):
        with pytest.raises(InvalidModelResponse):
            await provider.generate(
                ModelRequest(prompt="Return JSON", response_schema=schema),
                model="glm-5.2",
            )


@pytest.mark.asyncio
async def test_structured_generate_validates_relevant_nested_constraints() -> None:
    provider = AgentRouterQwenProvider(api_key="unit-test-key", executable="qwen")
    schema = {
        "type": "object",
        "required": ["items"],
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "required": ["label"],
                    "additionalProperties": False,
                    "properties": {"label": {"type": "string", "minLength": 3}},
                },
            }
        },
    }
    invalid = '{"items":[{"label":"x"}]}'

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=FakeProcess(stdout=_event_stream(invalid))),
    ):
        with pytest.raises(InvalidModelResponse):
            await provider.generate(
                ModelRequest(prompt="Return JSON", response_schema=schema),
                model="glm-5.2",
            )


@pytest.mark.asyncio
async def test_subprocess_launch_error_is_provider_unavailable() -> None:
    provider = AgentRouterQwenProvider(api_key="secret", executable="missing-qwen")

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=AsyncMock(side_effect=FileNotFoundError("missing executable")),
    ):
        with pytest.raises(ProviderUnavailable):
            await provider.generate(ModelRequest(prompt="Generate"), model="glm-5.2")


@pytest.mark.asyncio
async def test_subprocess_io_error_is_provider_unavailable_and_process_is_stopped() -> None:
    provider = AgentRouterQwenProvider(api_key="secret", executable="qwen")
    process = FakeProcess(error=BrokenPipeError("stdin closed"))

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=process),
    ):
        with pytest.raises(ProviderUnavailable):
            await provider.generate(ModelRequest(prompt="Generate"), model="glm-5.2")

    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cli_version_is_pinned_and_environment_is_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    provider = AgentRouterQwenProvider(api_key="unit-test-key")
    process = FakeProcess(stdout=_event_stream("plain text"))
    launch = AsyncMock(return_value=process)

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=launch,
    ):
        await provider.generate(ModelRequest(prompt="Generate"), model="glm-5.2")

    assert agentrouter_qwen.QWEN_CODE_PACKAGE == "@qwen-code/qwen-code@0.21.0"
    assert agentrouter_qwen.QWEN_CODE_PACKAGE in launch.await_args.args
    environment = launch.await_args.kwargs["env"]
    assert "UNRELATED_SECRET" not in environment
    assert environment["OPENAI_API_KEY"] == "unit-test-key"
    assert environment["OPENAI_BASE_URL"] == "https://agentrouter.org/v1"
    assert environment["OPENAI_MODEL"] == "glm-5.2"
    assert environment["NO_COLOR"] == "1"


@pytest.mark.asyncio
async def test_preinstalled_qwen_executable_skips_runtime_npx_download() -> None:
    provider = AgentRouterQwenProvider(
        api_key="unit-test-key",
        executable="/usr/local/bin/qwen",
        working_directory="/tmp",
    )
    process = FakeProcess(stdout=_event_stream("plain text"))
    launch = AsyncMock(return_value=process)

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=launch,
    ):
        await provider.generate(ModelRequest(prompt="Generate"), model="gpt-5.5")

    assert launch.await_args.args[:2] == (
        "/usr/local/bin/qwen",
        "--safe-mode",
    )
    assert agentrouter_qwen.QWEN_CODE_PACKAGE not in launch.await_args.args
    assert launch.await_args.kwargs["cwd"] == "/tmp"
    assert launch.await_args.kwargs["env"]["HOME"] == "/tmp"
    assert launch.await_args.kwargs["env"]["TMPDIR"] == "/tmp"


def test_processes_start_in_an_isolated_platform_group() -> None:
    windows = agentrouter_qwen._process_start_options("nt")
    posix = agentrouter_qwen._process_start_options("posix")

    assert windows["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    assert posix == {"start_new_session": True}


@pytest.mark.asyncio
async def test_windows_tree_termination_uses_taskkill() -> None:
    process = FakeProcess(returncode=None, pid=4242)
    taskkill = FakeProcess()
    launch = AsyncMock(return_value=taskkill)

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=launch,
    ):
        await agentrouter_qwen._terminate_process_tree(process, platform="nt")

    assert launch.await_args.args[:6] == (
        "taskkill.exe",
        "/PID",
        "4242",
        "/T",
        "/F",
    )
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_posix_tree_termination_kills_process_group() -> None:
    process = FakeProcess(returncode=None, pid=4242)

    with patch(
        "app.models.providers.agentrouter_qwen.os.killpg",
        create=True,
    ) as killpg:
        await agentrouter_qwen._terminate_process_tree(process, platform="posix")

    killpg.assert_called_once_with(4242, agentrouter_qwen._SIGKILL)
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (asyncio.CancelledError(), asyncio.CancelledError),
        (TimeoutError("timeout"), ProviderUnavailable),
    ],
)
async def test_cancellation_and_timeout_terminate_the_process_tree(
    error: BaseException,
    expected: type[BaseException],
) -> None:
    provider = AgentRouterQwenProvider(api_key="unit-test-key", executable="qwen")
    process = FakeProcess(error=error)
    terminate_tree = AsyncMock()

    with (
        patch(
            "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=process),
        ),
        patch(
            "app.models.providers.agentrouter_qwen._terminate_process_tree",
            new=terminate_tree,
            create=True,
        ),
    ):
        with pytest.raises(expected):
            await provider.generate(ModelRequest(prompt="Generate"), model="glm-5.2")

    terminate_tree.assert_awaited_once_with(process)


@pytest.mark.asyncio
async def test_unknown_cli_error_does_not_leak_prompt_key_or_private_url() -> None:
    prompt_marker = "private-prompt-marker"
    key_marker = "unit-test-secret-marker"
    url_marker = "https://private-tunnel.invalid/path"
    provider = AgentRouterQwenProvider(api_key=key_marker, executable="qwen")
    process = FakeProcess(
        returncode=1,
        stderr=f"failed {prompt_marker} {key_marker} {url_marker}".encode(),
    )

    with patch(
        "app.models.providers.agentrouter_qwen.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=process),
    ):
        with pytest.raises(ProviderUnavailable) as caught:
            await provider.generate(ModelRequest(prompt=prompt_marker), model="glm-5.2")

    message = str(caught.value)
    assert prompt_marker not in message
    assert key_marker not in message
    assert url_marker not in message
