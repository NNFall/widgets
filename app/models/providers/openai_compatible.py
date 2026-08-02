from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    ModelUsage,
    ProviderCapabilities,
    ProviderPermissionDenied,
    ProviderQuotaExceeded,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedModelRequest,
)


class OpenAICompatibleProvider:
    """Small text-only adapter for OpenAI-compatible chat-completions APIs."""

    capabilities = ProviderCapabilities(images=False, structured_output=True)

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        provider_name: str,
        timeout_seconds: float = 180.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("provider API key is required")
        if not provider_name.strip():
            raise ValueError("provider name is required")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._provider_name = provider_name.strip()
        self._timeout_seconds = timeout_seconds
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        if request.images:
            raise UnsupportedModelRequest(
                "OpenAI-compatible text provider does not accept binary images"
            )
        if request.response_schema is not None:
            try:
                Draft202012Validator.check_schema(request.response_schema)
            except SchemaError as error:
                raise ValueError("response schema is invalid") from error

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": request.prompt}],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": dict(request.response_schema),
                },
            }

        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderTimeout(f"{self._provider_name} generation timed out") from error
        except httpx.RequestError as error:
            raise ProviderUnavailable(
                f"{self._provider_name} is temporarily unavailable"
            ) from error

        _raise_for_status(response, provider_name=self._provider_name)
        try:
            body = response.json()
            return _normalize_response(
                body,
                requested_model=model,
                provider_name=self._provider_name,
                response_schema=request.response_schema,
            )
        except InvalidModelResponse:
            raise
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
            raise InvalidModelResponse(
                f"{self._provider_name} returned an invalid response"
            ) from error

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()


def _raise_for_status(response: httpx.Response, *, provider_name: str) -> None:
    status = response.status_code
    if status < 400:
        return
    if status in {401, 403} or status == 402:
        raise ProviderPermissionDenied(f"{provider_name} request is not permitted")
    if status == 404:
        raise ModelUnavailable(f"{provider_name} model is unavailable")
    if status == 429:
        raise ProviderQuotaExceeded(f"{provider_name} quota is unavailable")
    if status >= 500:
        raise ProviderUnavailable(f"{provider_name} is temporarily unavailable")
    raise ProviderUnavailable(f"{provider_name} rejected the request")


def _normalize_response(
    body: Any,
    *,
    requested_model: str,
    provider_name: str,
    response_schema: Mapping[str, Any] | None,
) -> ModelResponse:
    if not isinstance(body, Mapping):
        raise InvalidModelResponse(f"{provider_name} returned a non-object response")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise InvalidModelResponse(f"{provider_name} response has no choices")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise InvalidModelResponse(f"{provider_name} response has no message")
    text = message.get("content")
    if not isinstance(text, str) or not text.strip():
        raise InvalidModelResponse(f"{provider_name} returned empty text")

    usage = _normalize_usage(body.get("usage"))
    request_id = _optional_string(body.get("id"))
    actual_model = _optional_string(body.get("model"))
    parsed: Mapping[str, Any] | list[Any] | None = None
    if response_schema is not None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise InvalidModelResponse(
                f"{provider_name} returned invalid structured JSON",
                usage=usage,
                request_id=request_id,
                **_identity(provider_name, actual_model),
            ) from error
        if not isinstance(parsed, (dict, list)):
            raise InvalidModelResponse(
                f"{provider_name} structured response is not JSON data",
                usage=usage,
                request_id=request_id,
                **_identity(provider_name, actual_model),
            )
        try:
            Draft202012Validator(response_schema).validate(parsed)
        except ValidationError as error:
            raise InvalidModelResponse(
                f"{provider_name} structured response does not match the schema",
                usage=usage,
                request_id=request_id,
                **_identity(provider_name, actual_model),
            ) from error

    return ModelResponse(
        text=text,
        parsed=parsed,
        usage=usage,
        request_id=request_id,
        raw=dict(body),
        **_identity(provider_name, actual_model),
    )


def _normalize_usage(raw: Any) -> ModelUsage:
    if raw is None:
        return ModelUsage()
    if not isinstance(raw, Mapping):
        raise ValueError("usage must be an object")
    prompt_details = raw.get("prompt_tokens_details")
    completion_details = raw.get("completion_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, Mapping) else {}
    completion_details = completion_details if isinstance(completion_details, Mapping) else {}
    return ModelUsage(
        input_tokens=_token_count(raw.get("prompt_tokens", 0)),
        output_tokens=_token_count(raw.get("completion_tokens", 0)),
        thinking_tokens=_token_count(completion_details.get("reasoning_tokens", 0)),
        cache_read_tokens=_token_count(prompt_details.get("cached_tokens", 0)),
        cache_write_tokens=_token_count(
            prompt_details.get("cache_creation_tokens", raw.get("cache_creation_tokens", 0))
        ),
    )


def _token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token usage must be a non-negative integer")
    return value


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _identity(provider_name: str, actual_model: str | None) -> dict[str, str]:
    if actual_model is None:
        return {}
    return {"actual_provider": provider_name, "actual_model": actual_model}
