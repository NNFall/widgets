from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ModelResponse,
    ProviderCapabilities,
    ProviderUnavailable,
)
from app.models.providers.agentrouter_qwen import (
    AgentRouterQwenProvider,
    parse_qwen_json_output,
)
from app.models.router import InMemoryModelCallAudit, ModelPolicy, ModelRouter, ProviderTarget


def _event_stream(result: str) -> bytes:
    return json.dumps(
        [
            {
                "type": "result",
                "subtype": "success",
                "session_id": "session-generated",
                "result": result,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        ]
    ).encode()


class FakeProcess:
    def __init__(self, *, stdout: bytes = b"", error: Exception | None = None):
        self.returncode = 0
        self.stdout = stdout
        self.error = error
        self.kill = Mock()
        self.wait = AsyncMock()

    async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
        if self.error is not None:
            raise self.error
        return self.stdout, b""


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
