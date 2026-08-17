from __future__ import annotations

import asyncio
import json
import math
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


_REASONING_EFFORTS = frozenset({"low", "medium", "high"})


class AntigravityTextProvider:
    """Text-only adapter for the private Kaigo AntiGravity HTTP contract."""

    capabilities = ProviderCapabilities(images=False, structured_output=True)

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        reasoning_effort: str,
        timeout_seconds: float = 180.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("AntiGravity API key is required")
        normalized_url = base_url.strip().rstrip("/")
        try:
            parsed_url = httpx.URL(normalized_url)
        except (TypeError, ValueError) as error:
            raise ValueError("AntiGravity API base URL must use HTTPS") from error
        if (
            parsed_url.scheme != "https"
            or not parsed_url.host
            or bool(parsed_url.username)
            or bool(parsed_url.password)
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("AntiGravity API base URL must use HTTPS")
        normalized_effort = reasoning_effort.strip().lower()
        if normalized_effort not in _REASONING_EFFORTS:
            raise ValueError("AntiGravity reasoning effort is unsupported")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("AntiGravity timeout must be positive and finite")

        self._api_key = api_key.strip()
        self._base_url = normalized_url
        self._reasoning_effort = normalized_effort
        self._timeout_seconds = float(timeout_seconds)
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(timeout=self._timeout_seconds)

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        if request.images:
            raise UnsupportedModelRequest(
                "AntiGravity text provider does not accept images"
            )
        model = model.strip()
        if not model:
            raise UnsupportedModelRequest("AntiGravity model must be non-empty")
        encoded_effort = _encoded_effort(model)
        if encoded_effort is not None and encoded_effort != self._reasoning_effort:
            raise UnsupportedModelRequest(
                "AntiGravity model and reasoning effort do not match"
            )
        if request.response_schema is not None:
            try:
                Draft202012Validator.check_schema(request.response_schema)
            except SchemaError as error:
                raise ValueError("response schema is invalid") from error

        payload: dict[str, Any] = {
            "prompt": request.prompt,
            "model": model,
            "reasoning_effort": self._reasoning_effort,
            "native_tools": "none",
        }
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "kaigo_response",
                    "schema": dict(request.response_schema),
                    "strict": True,
                },
            }

        try:
            response = await self._client.post(
                f"{self._base_url}/v1/respond",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderTimeout("AntiGravity generation timed out") from error
        except httpx.RequestError as error:
            raise ProviderUnavailable(
                "AntiGravity is temporarily unavailable"
            ) from error

        _raise_for_status(response)
        try:
            body = response.json()
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise InvalidModelResponse(
                "AntiGravity returned an invalid response"
            ) from error
        return _normalize_response(
            body,
            requested_model=model,
            reasoning_effort=self._reasoning_effort,
            response_schema=request.response_schema,
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()


def _encoded_effort(model: str) -> str | None:
    return next(
        (effort for effort in _REASONING_EFFORTS if model.endswith(f"-{effort}")),
        None,
    )


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    if status in {401, 403}:
        raise ProviderPermissionDenied("AntiGravity request is not permitted")
    if status == 404:
        raise ModelUnavailable("AntiGravity model is unavailable")
    if status == 429:
        raise ProviderQuotaExceeded("AntiGravity request capacity is unavailable")
    if status == 504:
        raise ProviderTimeout("AntiGravity generation timed out")
    raise ProviderUnavailable("AntiGravity is temporarily unavailable")


def _normalize_response(
    body: Any,
    *,
    requested_model: str,
    reasoning_effort: str,
    response_schema: Mapping[str, Any] | None,
) -> ModelResponse:
    if not isinstance(body, Mapping):
        raise InvalidModelResponse("AntiGravity returned a non-object response")
    try:
        request_id = _required_string(body.get("request_id"))
        actual_model = _required_string(body.get("model"))
        actual_effort = _required_string(body.get("reasoning_effort"))
        text = _required_string(body.get("output_text"))
        duration_ms = _non_negative_int(body.get("duration_ms"))
        if actual_model != requested_model or actual_effort != reasoning_effort:
            raise ValueError("provider identity mismatch")
        if body.get("conversation_deleted") is not True:
            raise ValueError("conversation was not deleted")
        if body.get("finish_reason") != "stop":
            raise ValueError("unexpected finish reason")
        if body.get("tool_calls") != [] or body.get("native_tool_events") != []:
            raise ValueError("unexpected tool calls")
        warnings = body.get("provider_warnings")
        if not isinstance(warnings, list) or not all(
            isinstance(item, str) for item in warnings
        ):
            raise ValueError("provider warnings are invalid")
        usage = _normalize_usage(body.get("usage"))
    except (TypeError, ValueError) as error:
        raise InvalidModelResponse(
            "AntiGravity returned malformed data"
        ) from error

    parsed: Mapping[str, Any] | list[Any] | None = None
    if response_schema is not None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise InvalidModelResponse(
                "AntiGravity returned invalid structured JSON",
                usage=usage,
                request_id=request_id,
                actual_provider="antigravity_cli",
                actual_model=actual_model,
            ) from error
        if not isinstance(parsed, (dict, list)):
            raise InvalidModelResponse(
                "AntiGravity structured response is not JSON data",
                usage=usage,
                request_id=request_id,
                actual_provider="antigravity_cli",
                actual_model=actual_model,
            )
        try:
            Draft202012Validator(response_schema).validate(parsed)
        except ValidationError as error:
            raise InvalidModelResponse(
                "AntiGravity structured response does not match the schema",
                usage=usage,
                request_id=request_id,
                actual_provider="antigravity_cli",
                actual_model=actual_model,
            ) from error

    return ModelResponse(
        text=text,
        parsed=parsed,
        usage=usage,
        request_id=request_id,
        raw={
            "duration_ms": duration_ms,
            "conversation_deleted": True,
            "provider_warnings": list(warnings),
        },
        actual_provider="antigravity_cli",
        actual_model=actual_model,
    )


def _normalize_usage(raw: object) -> ModelUsage:
    if not isinstance(raw, Mapping):
        raise ValueError("usage must be an object")
    input_tokens = _non_negative_int(raw.get("input_tokens", 0))
    output_tokens = _non_negative_int(raw.get("output_tokens", 0))
    thinking_tokens = _non_negative_int(raw.get("thinking_tokens", 0))
    cache_read_tokens = _non_negative_int(raw.get("cache_read_tokens", 0))
    # The provider can recover a successful streamed response with only the
    # aggregate counter populated. Keep validating it, but do not derive or
    # reject the independently reported component buckets from that total.
    _non_negative_int(raw.get("total_tokens", 0))
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        thinking_tokens=thinking_tokens,
        cache_read_tokens=cache_read_tokens,
    )


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty string")
    return value.strip()


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("value must be a non-negative integer")
    return value
