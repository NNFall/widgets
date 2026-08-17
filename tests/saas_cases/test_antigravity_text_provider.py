import asyncio
import json

import httpx
import pytest

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ModelUnavailable,
    ProviderPermissionDenied,
    ProviderQuotaExceeded,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedModelRequest,
)
from app.models.providers.antigravity_text import AntigravityTextProvider


MODEL = "gemini-3.7-flash-high"
SCHEMA = {
    "type": "object",
    "properties": {"direction": {"type": "string"}},
    "required": ["direction"],
    "additionalProperties": False,
}


def _success_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "request_id": "agt-request-1",
        "model": MODEL,
        "actual_provider": "gemini",
        "actual_model": "gemini-3.7-flash",
        "reasoning_effort": "high",
        "output_text": '{"direction":"bold"}',
        "tool_calls": [],
        "native_tool_events": [],
        "provider_warnings": [],
        "usage": {
            "input_tokens": 11,
            "output_tokens": 7,
            "thinking_tokens": 2,
            "cache_read_tokens": 3,
            "total_tokens": 18,
        },
        "duration_ms": 123,
        "finish_reason": "stop",
        "conversation_deleted": True,
    }
    body.update(overrides)
    return body


def _provider(client: httpx.AsyncClient) -> AntigravityTextProvider:
    return AntigravityTextProvider(
        api_key="test-integration-key",
        base_url="https://kaigo.space/antigravity-api/",
        reasoning_effort="high",
        timeout_seconds=180,
        client=client,
    )


@pytest.mark.asyncio
async def test_provider_sends_text_only_structured_request_and_normalizes_result() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_success_body())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _provider(client)

    response = await provider.generate(
        ModelRequest(
            prompt="Create a direction",
            response_schema=SCHEMA,
            temperature=0.9,
        ),
        model=MODEL,
    )

    assert response.text == '{"direction":"bold"}'
    assert response.parsed == {"direction": "bold"}
    assert response.request_id == "agt-request-1"
    assert response.actual_provider == "gemini"
    assert response.actual_model == "gemini-3.7-flash"
    assert response.reported_cost_microusd is None
    assert response.no_charge_confirmed is False
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    assert response.usage.thinking_tokens == 2
    assert response.usage.cache_read_tokens == 3
    assert requests[0].url.path == "/antigravity-api/v1/respond"
    assert requests[0].headers["authorization"] == "Bearer test-integration-key"
    payload = json.loads(requests[0].content)
    assert payload == {
        "prompt": "Create a direction",
        "model": MODEL,
        "reasoning_effort": "high",
        "native_tools": "none",
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "kaigo_response",
                "schema": SCHEMA,
                "strict": True,
            },
        },
    }
    assert response.raw == {
        "duration_ms": 123,
        "conversation_deleted": True,
        "provider_warnings": [],
    }
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_returns_plain_text_without_structured_format() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_success_body(output_text="Plain answer"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = await _provider(client).generate(ModelRequest(prompt="Answer"), model=MODEL)

    assert response.text == "Plain answer"
    assert response.parsed is None
    assert "response_format" not in json.loads(requests[0].content)
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_accepts_recovered_result_with_total_only_usage() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_success_body(
                    output_text="Recovered answer",
                    usage={"total_tokens": 10},
                ),
            )
        )
    )

    response = await _provider(client).generate(
        ModelRequest(prompt="Answer"),
        model=MODEL,
    )

    assert response.text == "Recovered answer"
    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.usage.thinking_tokens == 0
    assert response.usage.cache_read_tokens == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_accepts_upstream_usage_defaults() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_success_body(output_text="Answer", usage={}),
            )
        )
    )

    response = await _provider(client).generate(
        ModelRequest(prompt="Answer"),
        model=MODEL,
    )

    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.usage.thinking_tokens == 0
    assert response.usage.cache_read_tokens == 0
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_preserves_confirmed_cli_backend_identity() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_success_body(
                    actual_provider="antigravity_cli",
                    actual_model=MODEL,
                ),
            )
        )
    )

    response = await _provider(client).generate(
        ModelRequest(prompt="Answer"),
        model=MODEL,
    )

    assert response.actual_provider == "antigravity_cli"
    assert response.actual_model == MODEL
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "identity",
    [
        {"actual_provider": None},
        {"actual_model": None},
        {"actual_provider": ""},
        {"actual_model": ""},
        {"actual_provider": "untrusted_gateway"},
    ],
)
async def test_provider_rejects_missing_or_unknown_backend_identity(
    identity: dict[str, object],
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_success_body(**identity),
            )
        )
    )

    with pytest.raises(InvalidModelResponse):
        await _provider(client).generate(ModelRequest(prompt="Answer"), model=MODEL)

    await client.aclose()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"api_key": ""}, "API key"),
        ({"base_url": "http://127.0.0.1:8793"}, "HTTPS"),
        ({"reasoning_effort": "max"}, "reasoning effort"),
        ({"timeout_seconds": 0}, "timeout"),
    ],
)
def test_provider_rejects_invalid_configuration(kwargs: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "api_key": "test-key",
        "base_url": "https://provider.example/api",
        "reasoning_effort": "high",
        "timeout_seconds": 10,
    }
    values.update(kwargs)

    with pytest.raises(ValueError, match=message):
        AntigravityTextProvider(**values)


@pytest.mark.asyncio
async def test_provider_rejects_images_and_model_effort_mismatch_before_dispatch() -> None:
    dispatched = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json=_success_body())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _provider(client)

    with pytest.raises(UnsupportedModelRequest, match="images"):
        await provider.generate(ModelRequest(prompt="Look", images=(b"image",)), model=MODEL)
    with pytest.raises(UnsupportedModelRequest, match="reasoning effort"):
        await provider.generate(
            ModelRequest(prompt="Answer"),
            model="gemini-3.7-flash-medium",
        )

    assert dispatched is False
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_rejects_invalid_json_schema_before_dispatch() -> None:
    dispatched = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal dispatched
        dispatched = True
        return httpx.Response(200, json=_success_body())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = _provider(client)

    with pytest.raises(ValueError, match="response schema"):
        await provider.generate(
            ModelRequest(prompt="Answer", response_schema={"type": "unknown"}),
            model=MODEL,
        )

    assert dispatched is False
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderPermissionDenied),
        (403, ProviderPermissionDenied),
        (404, ModelUnavailable),
        (429, ProviderQuotaExceeded),
        (500, ProviderUnavailable),
        (502, ProviderUnavailable),
        (504, ProviderTimeout),
    ],
)
async def test_provider_classifies_http_failures_without_leaking_body(
    status: int,
    expected: type[Exception],
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                status,
                json={"error": {"message": "secret-response-body"}},
            )
        )
    )

    with pytest.raises(expected) as captured:
        await _provider(client).generate(ModelRequest(prompt="Hello"), model=MODEL)

    assert "secret-response-body" not in str(captured.value)
    assert "test-integration-key" not in str(captured.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_classifies_timeout_and_transport_failure() -> None:
    async def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow secret response")

    timeout_client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    with pytest.raises(ProviderTimeout, match="timed out"):
        await _provider(timeout_client).generate(ModelRequest(prompt="Hello"), model=MODEL)
    await timeout_client.aclose()

    async def unavailable(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret host detail")

    unavailable_client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    with pytest.raises(ProviderUnavailable, match="unavailable"):
        await _provider(unavailable_client).generate(
            ModelRequest(prompt="Hello"), model=MODEL
        )
    await unavailable_client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        _success_body(output_text=""),
        _success_body(model="gemini-3.7-flash-medium"),
        _success_body(reasoning_effort="medium"),
        _success_body(conversation_deleted=False),
        _success_body(finish_reason="tool_calls", output_text=None),
        _success_body(
            usage={
                "input_tokens": 2,
                "output_tokens": 1,
                "thinking_tokens": 1,
                "cache_read_tokens": 0,
                "total_tokens": -1,
            }
        ),
    ],
)
async def test_provider_rejects_malformed_or_non_ephemeral_response(
    body: dict[str, object],
) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=body))
    )

    with pytest.raises(InvalidModelResponse):
        await _provider(client).generate(ModelRequest(prompt="Hello"), model=MODEL)

    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output_text",
    [
        "not-json",
        '{"direction": 42}',
        '["not", "an", "object"]',
    ],
)
async def test_provider_rejects_invalid_structured_output(output_text: str) -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=_success_body(output_text=output_text),
            )
        )
    )

    with pytest.raises(InvalidModelResponse) as captured:
        await _provider(client).generate(
            ModelRequest(prompt="Hello", response_schema=SCHEMA),
            model=MODEL,
        )

    assert captured.value.actual_provider == "gemini"
    assert captured.value.actual_model == "gemini-3.7-flash"

    await client.aclose()


@pytest.mark.asyncio
async def test_provider_propagates_cancellation_to_http_request() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_request: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    task = asyncio.create_task(
        _provider(client).generate(ModelRequest(prompt="Hello"), model=MODEL)
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    await client.aclose()
