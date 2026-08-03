import json

import httpx
import pytest

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ProviderPermissionDenied,
    ProviderQuotaExceeded,
    ProviderTimeout,
)
from app.models.providers.openai_compatible import OpenAICompatibleProvider


@pytest.mark.asyncio
async def test_openai_compatible_provider_sends_schema_and_normalizes_usage() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "model": "deepseek-v4-flash-free-actual",
                "choices": [{"message": {"content": '{"answer":"ok"}'}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                    "prompt_tokens_details": {"cached_tokens": 3},
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            },
        )

    client = httpx.AsyncClient(
        base_url="https://zenmux.example/api/v1",
        transport=httpx.MockTransport(handler),
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://zenmux.example/api/v1",
        provider_name="zenmux",
        client=client,
    )
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }

    response = await provider.generate(
        ModelRequest(prompt="Answer", response_schema=schema),
        model="deepseek/deepseek-v4-flash-free",
    )

    assert response.parsed == {"answer": "ok"}
    assert response.request_id == "chatcmpl-1"
    assert response.actual_provider == "zenmux"
    assert response.actual_model == "deepseek-v4-flash-free-actual"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    assert response.usage.thinking_tokens == 2
    assert response.usage.cache_read_tokens == 3
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/api/v1/chat/completions"
    assert payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "response", "strict": True, "schema": schema},
    }
    assert "max_tokens" not in payload
    assert "max_completion_tokens" not in payload
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderPermissionDenied),
        (402, ProviderQuotaExceeded),
        (429, ProviderQuotaExceeded),
    ],
)
async def test_openai_compatible_provider_classifies_http_failures(status, expected) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(status)),
    )
    provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://provider.example/v1",
        provider_name="zenmux",
        client=client,
    )
    with pytest.raises(expected):
        await provider.generate(ModelRequest(prompt="Hello"), model="model")
    await client.aclose()


@pytest.mark.asyncio
async def test_openai_compatible_provider_classifies_timeout_and_malformed_body() -> None:
    async def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    timeout_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    timeout_provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://provider.example/v1",
        provider_name="agentrouter",
        client=timeout_client,
    )
    with pytest.raises(ProviderTimeout):
        await timeout_provider.generate(ModelRequest(prompt="Hello"), model="model")
    await timeout_client.aclose()

    malformed_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={}))
    )
    malformed_provider = OpenAICompatibleProvider(
        api_key="test-key",
        base_url="https://provider.example/v1",
        provider_name="agentrouter",
        client=malformed_client,
    )
    with pytest.raises(InvalidModelResponse):
        await malformed_provider.generate(ModelRequest(prompt="Hello"), model="model")
    await malformed_client.aclose()
