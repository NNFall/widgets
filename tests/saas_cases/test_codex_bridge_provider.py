from __future__ import annotations

import base64
import json
from uuid import uuid4

import httpx
import pytest

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedModelRequest,
)
from app.models.providers.codex_bridge import (
    CodexBridgeProvider,
    conversation_key_for_request,
)


def _metadata(**changes):
    values = {
        "_kaigo_run_id": str(uuid4()),
        "_kaigo_role": "widget_generator",
        "_kaigo_mode": "express",
        "_kaigo_stage": "foundation",
        "_kaigo_operation": "widget_generation",
        "_kaigo_semantic_attempt": 1,
        "_kaigo_candidate_id": None,
        "_kaigo_persona": None,
        "thinking_level": "high",
    }
    values.update(changes)
    return values


@pytest.mark.asyncio
async def test_provider_serializes_schema_images_and_safe_lineage() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "text": '{"answer":"ok"}',
                "parsed": {"answer": "ok"},
                "usage": {
                    "input_tokens": 120,
                    "cached_input_tokens": 20,
                    "output_tokens": 30,
                },
                "thread_id": str(uuid4()),
                "model": "gpt-5.6-luna",
                "duration_ms": 1400,
            },
        )

    client = httpx.AsyncClient(
        base_url="http://codex-bridge",
        transport=httpx.MockTransport(handler),
    )
    provider = CodexBridgeProvider(
        socket_path="/run/kaigo-codex/bridge.sock",
        client=client,
    )
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    image = b"\x89PNG\r\n\x1a\nvisual"

    response = await provider.generate(
        ModelRequest(
            prompt="Generate",
            images=(image,),
            response_schema=schema,
            metadata=_metadata(),
        ),
        model="gpt-5.6-luna",
    )

    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/turn"
    assert payload == {
        "run_id": response.raw["run_id"],
        "conversation_key": "build:foundation",
        "prompt": "Generate",
        "response_schema": schema,
        "images": [
            {
                "media_type": "image/png",
                "data": base64.b64encode(image).decode("ascii"),
            }
        ],
        "model": "gpt-5.6-luna",
    }
    assert response.parsed == {"answer": "ok"}
    assert response.actual_provider == "codex_cli"
    assert response.actual_model == "gpt-5.6-luna"
    assert response.usage.input_tokens == 120
    assert response.usage.cache_read_tokens == 20
    assert response.usage.output_tokens == 30
    assert response.no_charge_confirmed is False
    await client.aclose()


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            _metadata(
                _kaigo_role="direction_candidate",
                _kaigo_candidate_id="candidate-c",
            ),
            "direction:candidate-c",
        ),
        (_metadata(_kaigo_role="direction_judge"), "direction:judge"),
        (
            _metadata(
                _kaigo_role="conversation_ux",
                _kaigo_persona="customer",
            ),
            "critic:customer",
        ),
        (_metadata(_kaigo_role="visual_judge"), "visual:judge"),
        (
            _metadata(_kaigo_role="repair", _kaigo_stage="foundation"),
            "repair:foundation",
        ),
        (
            _metadata(_kaigo_role="repair", _kaigo_stage="motion_polish"),
            "repair:motion_polish",
        ),
        (
            _metadata(_kaigo_role="code_review", _kaigo_stage="identity"),
            "repair:verify:identity",
        ),
        (_metadata(_kaigo_role="reference_analyst"), "reference"),
        (
            _metadata(_kaigo_role="widget_generator", _kaigo_stage="foundation"),
            "build:foundation",
        ),
        (
            _metadata(_kaigo_role="brand_designer", _kaigo_stage="identity"),
            "build:identity",
        ),
        (
            _metadata(
                _kaigo_role="conversation_designer",
                _kaigo_stage="conversation",
            ),
            "build:conversation",
        ),
        (
            _metadata(_kaigo_role="motion_designer", _kaigo_stage="motion_polish"),
            "build:motion_polish",
        ),
    ],
)
def test_conversation_key_matches_builder_role(metadata, expected) -> None:
    assert conversation_key_for_request(metadata) == expected


@pytest.mark.asyncio
async def test_provider_requires_router_run_lineage() -> None:
    provider = CodexBridgeProvider(
        socket_path="/run/kaigo-codex/bridge.sock",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)),
    )
    try:
        with pytest.raises(UnsupportedModelRequest, match="run lineage"):
            await provider.generate(ModelRequest(prompt="Build"), model="gpt-5.6-luna")
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_provider_rejects_structured_response_that_misses_schema() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "text": "{}",
                    "parsed": {},
                    "usage": {
                        "input_tokens": 1,
                        "cached_input_tokens": 0,
                        "output_tokens": 1,
                    },
                    "thread_id": str(uuid4()),
                    "model": "gpt-5.6-luna",
                    "duration_ms": 1,
                },
            )
        )
    )
    provider = CodexBridgeProvider(socket_path="/tmp/bridge.sock", client=client)

    with pytest.raises(InvalidModelResponse, match="schema"):
        await provider.generate(
            ModelRequest(
                prompt="Build",
                response_schema={
                    "type": "object",
                    "required": ["answer"],
                    "properties": {"answer": {"type": "string"}},
                },
                metadata=_metadata(),
            ),
            model="gpt-5.6-luna",
        )
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [
        (504, "generation_timeout", ProviderTimeout),
        (502, "invalid_response", InvalidModelResponse),
        (503, "provider_unavailable", ProviderUnavailable),
    ],
)
async def test_provider_maps_bridge_errors(status, code, expected) -> None:
    private = "private bridge diagnostic"
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                status,
                json={"error": {"code": code, "message": private}},
            )
        )
    )
    provider = CodexBridgeProvider(socket_path="/tmp/bridge.sock", client=client)

    with pytest.raises(expected) as raised:
        await provider.generate(
            ModelRequest(prompt="Build", metadata=_metadata()),
            model="gpt-5.6-luna",
        )

    assert private not in str(raised.value)
    await client.aclose()


@pytest.mark.asyncio
async def test_provider_finalizes_run_through_bridge() -> None:
    run_id = str(uuid4())
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"archived_thread_ids": [str(uuid4())]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CodexBridgeProvider(socket_path="/tmp/bridge.sock", client=client)

    archived = await provider.finalize_run(run_id)

    assert len(archived) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"/v1/runs/{run_id}/complete"
    await client.aclose()
