from __future__ import annotations

import asyncio
import json
import types as std_types
from unittest.mock import patch

import pytest

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    ProviderCapabilities,
    ProviderPermissionDenied,
    ProviderQuotaExceeded,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.models.providers.gemini import GeminiModelProvider
from app.models.router import InMemoryModelCallAudit, ModelPolicy, ModelRouter, ProviderTarget
from builder_lab.prompts import ARTIFACT_JSON_SCHEMA


class FakeModels:
    def __init__(self, *, response: object = None, error: BaseException | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, *, response: object = None, error: BaseException | None = None):
        self.models = FakeModels(response=response, error=error)
        self.aio = std_types.SimpleNamespace(models=self.models)


class FallbackProvider:
    capabilities = ProviderCapabilities(images=True, structured_output=True)

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        return ModelResponse(text='{"ok":true}', parsed={"ok": True})


def fake_response(
    *,
    text: str = '{"artifact":"ok"}',
    parsed: object = None,
) -> object:
    return std_types.SimpleNamespace(
        text=text,
        parsed=parsed,
        response_id="gemini-response-1",
        model_version="gemini-3.5-flash",
        usage_metadata=std_types.SimpleNamespace(
            prompt_token_count=120,
            candidates_token_count=40,
            thoughts_token_count=10,
            total_token_count=170,
        ),
    )


def test_provider_preserves_proxy_path_and_api_version() -> None:
    with patch("app.models.providers.gemini.genai.Client") as client_factory:
        GeminiModelProvider(
            api_key=" secret ",
            base_url="https://proxy.test/protected/v1",
        )

    call = client_factory.call_args
    assert call.kwargs["api_key"] == "secret"
    assert call.kwargs["http_options"].base_url == "https://proxy.test/protected"
    assert call.kwargs["http_options"].api_version == "v1"


@pytest.mark.asyncio
async def test_structured_request_preserves_images_and_normalizes_billable_usage() -> None:
    response = fake_response()
    client = FakeClient(response=response)
    provider = GeminiModelProvider(api_key="secret", client=client)
    png = b"\x89PNG\r\n\x1a\nexact-png"
    jpeg = b"\xff\xd8\xffexact-jpeg"
    schema = {"type": "object", "properties": {"artifact": {"type": "string"}}}

    result = await provider.generate(
        ModelRequest(
            prompt="Return the artifact",
            images=(png, jpeg),
            response_schema=schema,
            temperature=0.7,
        ),
        model="gemini-3.5-flash",
    )

    assert result.text == '{"artifact":"ok"}'
    assert result.parsed == {"artifact": "ok"}
    assert result.request_id == "gemini-response-1"
    assert result.usage.input_tokens == 120
    # Gemini reports visible candidates and hidden thoughts separately. The
    # provider-neutral contract bills their sum once and keeps thoughts as a
    # diagnostic subset of that output total.
    assert result.usage.output_tokens == 50
    assert result.usage.thinking_tokens == 10

    call = client.models.calls[0]
    assert call["model"] == "gemini-3.5-flash"
    contents = call["contents"]
    assert contents[0].text == "Return the artifact"
    assert contents[1].inline_data.data == png
    assert contents[1].inline_data.mime_type == "image/png"
    assert contents[2].inline_data.data == jpeg
    assert contents[2].inline_data.mime_type == "image/jpeg"
    config = call["config"]
    assert config.temperature == 0.7
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == schema
    assert config.max_output_tokens is None
    assert "max_output_tokens" not in config.model_dump(exclude_none=True)


@pytest.mark.asyncio
async def test_routed_gemini_36_keeps_high_thinking_and_omits_sampling() -> None:
    client = FakeClient(response=fake_response())
    provider = GeminiModelProvider(api_key="secret", client=client)

    await provider.generate(
        ModelRequest(
            prompt="Generate",
            temperature=0.9,
            metadata={"thinking_level": "high"},
        ),
        model="gemini-3.6-flash",
    )

    config = client.models.calls[0]["config"]
    assert config.thinking_config.thinking_level.value == "HIGH"
    assert config.temperature is None
    assert config.top_p is None


@pytest.mark.asyncio
async def test_sdk_parsed_json_is_preserved_without_rewriting_exact_text() -> None:
    parsed = [{"artifact": "one"}, {"artifact": "two"}]
    text = json.dumps(parsed, separators=(",", ":"))
    provider = GeminiModelProvider(
        api_key="secret",
        client=FakeClient(response=fake_response(text=text, parsed=parsed)),
    )

    result = await provider.generate(
        ModelRequest(prompt="Return artifacts", response_schema={"type": "array"}),
        model="gemini-3.5-flash",
    )

    assert result.text == text
    assert result.parsed == parsed


@pytest.mark.asyncio
async def test_plain_text_response_does_not_require_json() -> None:
    provider = GeminiModelProvider(
        api_key="secret",
        client=FakeClient(response=fake_response(text="  exact prose  ")),
    )

    result = await provider.generate(
        ModelRequest(prompt="Write prose"),
        model="gemini-3.5-flash",
    )

    assert result.text == "  exact prose  "
    assert result.parsed is None


@pytest.mark.asyncio
async def test_invalid_structured_response_is_normalized() -> None:
    provider = GeminiModelProvider(
        api_key="secret",
        client=FakeClient(response=fake_response(text="not json")),
    )

    with pytest.raises(InvalidModelResponse):
        await provider.generate(
            ModelRequest(prompt="Return JSON", response_schema={"type": "object"}),
            model="gemini-3.5-flash",
        )


@pytest.mark.asyncio
async def test_invalid_structured_response_audits_billed_usage_before_fallback() -> None:
    primary = GeminiModelProvider(
        api_key="unit-test-key",
        client=FakeClient(response=fake_response(text="not json")),
    )
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"gemini": primary, "fallback": FallbackProvider()},
        policies={
            ("artifact", "standard"): ModelPolicy(
                prompt_version="artifact-v1",
                targets=(
                    ProviderTarget("gemini", "gemini-3.5-flash", 1_000_000, 2_000_000),
                    ProviderTarget("fallback", "fallback-model", 0, 0),
                ),
            )
        },
        audit=audit,
    )

    response = await router.generate(
        role="artifact",
        mode="standard",
        request=ModelRequest(prompt="Return JSON", response_schema={"type": "object"}),
    )

    assert response.parsed == {"ok": True}
    failure = audit.calls[0]
    assert failure.status == "failed"
    assert failure.error_code == "invalid_response"
    assert failure.input_tokens == 120
    assert failure.output_tokens == 50
    assert failure.thinking_tokens == 10
    assert failure.cost_microusd == 220
    assert failure.request_id == "gemini-response-1"


@pytest.mark.asyncio
async def test_routed_gemini_uses_model_specific_provider_schema() -> None:
    clients = {
        model: FakeClient(response=fake_response())
        for model in ("gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.6-flash")
    }

    for model, client in clients.items():
        provider = GeminiModelProvider(api_key="unit-test-key", client=client)
        await provider.generate(
            ModelRequest(prompt="Return JSON", response_schema=ARTIFACT_JSON_SCHEMA),
            model=model,
        )

    schema_25 = clients["gemini-2.5-flash"].models.calls[0]["config"].response_json_schema
    schema_35 = clients["gemini-3.5-flash"].models.calls[0]["config"].response_json_schema
    schema_36 = clients["gemini-3.6-flash"].models.calls[0]["config"].response_json_schema
    assert "minimum" not in schema_25["properties"]["revision"]
    assert "maxItems" not in schema_25["properties"]["suggested_actions"]
    assert "enum" not in schema_25["properties"]["schema_version"]
    assert schema_25["additionalProperties"] is False
    assert "minimum" not in schema_35["properties"]["revision"]
    assert "additionalProperties" not in schema_35
    assert "maxItems" not in schema_35["properties"]["suggested_actions"]
    assert schema_35["properties"]["schema_version"]["enum"] == ["1.0"]
    assert schema_36["properties"]["revision"]["minimum"] == 1
    assert "additionalProperties" not in schema_36
    assert "maxItems" not in schema_36["properties"]["suggested_actions"]
    assert schema_36["properties"]["schema_version"]["enum"] == ["1.0"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("429 RESOURCE_EXHAUSTED secret route"), ProviderQuotaExceeded),
        (RuntimeError("404 model gemini-nope not found"), ModelUnavailable),
        (
            RuntimeError("404 NOT_FOUND: models/gemini-nope is not found"),
            ModelUnavailable,
        ),
        (TimeoutError("upstream timeout"), ProviderTimeout),
        (RuntimeError("connection refused https://private-route"), ProviderUnavailable),
    ],
)
async def test_provider_errors_are_classified_and_sanitized(
    error: Exception,
    expected: type[Exception],
) -> None:
    provider = GeminiModelProvider(api_key="secret", client=FakeClient(error=error))

    with pytest.raises(expected) as caught:
        await provider.generate(ModelRequest(prompt="Generate"), model="gemini-3.5-flash")

    assert "secret route" not in str(caught.value)
    assert "private-route" not in str(caught.value)


@pytest.mark.asyncio
async def test_billing_permission_denial_is_terminal_and_sanitized() -> None:
    error = RuntimeError(
        "403 PERMISSION_DENIED. Lightning dunning decision is deny for "
        "project: projects/671587661095"
    )
    provider = GeminiModelProvider(api_key="secret", client=FakeClient(error=error))

    with pytest.raises(ProviderPermissionDenied) as caught:
        await provider.generate(ModelRequest(prompt="Generate"), model="gemini-3.5-flash")

    assert caught.value.error_code == "provider_permission_denied"
    assert "671587661095" not in str(caught.value)
    assert "dunning" not in str(caught.value).lower()


@pytest.mark.asyncio
async def test_cancellation_is_not_wrapped() -> None:
    provider = GeminiModelProvider(
        api_key="secret",
        client=FakeClient(error=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await provider.generate(ModelRequest(prompt="Generate"), model="gemini-3.5-flash")
