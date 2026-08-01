from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any, Mapping

from google import genai
from google.genai import types

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
)
from app.models.generation_policy import generation_policy


@dataclass(frozen=True, slots=True)
class GeminiUsageCounts:
    input_tokens: int = 0
    candidate_tokens: int = 0
    thinking_tokens: int = 0


def build_http_options(base_url: str) -> types.HttpOptions:
    """Build google-genai HTTP options while preserving proxy path prefixes."""

    base = base_url.strip().rstrip("/")
    if not base:
        raise ValueError("Gemini base URL must not be empty")
    api_version = "v1beta"
    for version in ("v1beta", "v1"):
        suffix = "/" + version
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            api_version = version
            break
    return types.HttpOptions(
        base_url=base,
        api_version=api_version,
        timeout=180_000,
    )


def gemini_usage_counts(response: Any) -> GeminiUsageCounts:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return GeminiUsageCounts()
    return GeminiUsageCounts(
        input_tokens=_nonnegative_integer(getattr(metadata, "prompt_token_count", 0)),
        candidate_tokens=_nonnegative_integer(
            getattr(metadata, "candidates_token_count", 0)
        ),
        thinking_tokens=_nonnegative_integer(
            getattr(metadata, "thoughts_token_count", 0)
        ),
    )


_GEMINI_25_SCHEMA_CONSTRAINTS = frozenset(
    {
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)

_GEMINI_3X_SCHEMA_CONSTRAINTS = frozenset(
    {
        "additionalProperties",
        "maxItems",
        "minItems",
    }
)

_GEMINI_35_EXTRA_SCHEMA_CONSTRAINTS = frozenset(
    {
        "maximum",
        "minimum",
    }
)


def build_provider_json_schema(
    schema: Mapping[str, Any],
    model: str,
) -> dict[str, Any]:
    """Adapt the strict local schema to each Gemini serving implementation."""

    normalized = model.strip().lower().removeprefix("models/")
    if normalized.startswith(("gemini-3.5-", "gemini-3.6-")):
        unsupported = _GEMINI_3X_SCHEMA_CONSTRAINTS
        if normalized.startswith("gemini-3.5-"):
            unsupported = unsupported | _GEMINI_35_EXTRA_SCHEMA_CONSTRAINTS

        def simplify_3x(value: Any) -> Any:
            if isinstance(value, Mapping):
                simplified = {
                    key: simplify_3x(item)
                    for key, item in value.items()
                    if key not in unsupported
                }
                declared_type = simplified.get("type")
                if (
                    isinstance(declared_type, list)
                    and len(declared_type) == 2
                    and "null" in declared_type
                ):
                    simplified["type"] = next(
                        item for item in declared_type if item != "null"
                    )
                return simplified
            if isinstance(value, list):
                return [simplify_3x(item) for item in value]
            return value

        return simplify_3x(schema)
    if not normalized.startswith("gemini-2.5-"):
        return dict(schema)

    def simplify_25(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                key: simplify_25(item)
                for key, item in value.items()
                if key not in _GEMINI_25_SCHEMA_CONSTRAINTS
            }
        if isinstance(value, list):
            return [simplify_25(item) for item in value]
        return value

    return simplify_25(schema)


def classify_gemini_error(error: Exception) -> str:
    diagnostic = f"{type(error).__name__}: {error}".lower()
    status = str(getattr(error, "status", "")).lower()
    code = str(getattr(error, "code", "")).lower()
    combined = " ".join((diagnostic, status, code))
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in combined:
        return "generation_timeout"
    if "403" in combined and any(
        token in combined
        for token in ("permission_denied", "permission denied", "dunning decision")
    ):
        return "provider_permission_denied"
    if any(token in combined for token in ("429", "resource_exhausted", "quota")):
        return "quota_exceeded"
    explicit_model_error = any(
        token in combined for token in ("model not found", "model_unavailable")
    )
    not_found_model_error = (
        "404" in combined
        and "model" in combined
        and any(token in combined for token in ("not found", "not_found"))
    )
    if explicit_model_error or not_found_model_error:
        return "model_unavailable"
    return "provider_unavailable"


class GeminiModelProvider:
    """Provider-neutral adapter for the official asynchronous google-genai client."""

    capabilities = ProviderCapabilities(images=True, structured_output=True)

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str = "https://generativelanguage.googleapis.com",
        client: Any | None = None,
    ) -> None:
        if client is None and (not api_key or not api_key.strip()):
            raise ValueError("Gemini API key is required")
        self._owned_client = client is None
        self._client = client or genai.Client(
            api_key=api_key.strip(),  # type: ignore[union-attr]
            http_options=build_http_options(base_url),
        )

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        raw_thinking_level = request.metadata.get("thinking_level")
        thinking_level = (
            raw_thinking_level
            if isinstance(raw_thinking_level, str)
            else "high"
        )
        policy = generation_policy(
            model,
            thinking_level,
            temperature=request.temperature,
        )
        config_values: dict[str, Any] = {
            **policy.sampling_kwargs,
            "thinking_config": policy.thinking_config,
        }
        if request.response_schema is not None:
            config_values.update(
                response_mime_type="application/json",
                response_json_schema=build_provider_json_schema(
                    request.response_schema,
                    model,
                ),
                tools=[],
            )
        config = types.GenerateContentConfig(**config_values)
        contents: str | list[types.Part]
        if request.images:
            contents = [types.Part.from_text(text=request.prompt)]
            labels = request.metadata.get("image_labels")
            if (
                isinstance(labels, (tuple, list))
                and len(labels) == len(request.images)
                and all(isinstance(label, str) and label.strip() for label in labels)
            ):
                for label, image in zip(labels, request.images):
                    contents.append(types.Part.from_text(text=f"EVIDENCE {label.strip()}"))
                    contents.append(
                        types.Part.from_bytes(
                            data=image,
                            mime_type=_image_mime_type(image),
                        )
                    )
            else:
                contents.extend(
                    types.Part.from_bytes(data=image, mime_type=_image_mime_type(image))
                    for image in request.images
                )
        else:
            contents = request.prompt

        try:
            response = await self._client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _provider_error(error) from error

        try:
            return _normalize_response(response, structured=request.response_schema is not None)
        except InvalidModelResponse:
            raise
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise InvalidModelResponse("Gemini returned an invalid response") from error

    async def aclose(self) -> None:
        if not self._owned_client:
            return
        close = getattr(getattr(self._client, "aio", None), "aclose", None)
        if not callable(close):
            close = getattr(self._client, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result


def _normalize_response(response: Any, *, structured: bool) -> ModelResponse:
    usage = _normalized_usage(response)
    request_id = _optional_string(getattr(response, "response_id", None))
    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise InvalidModelResponse(
            "Gemini returned an empty response",
            usage=usage,
            request_id=request_id,
        )

    parsed: Mapping[str, Any] | list[Any] | None = None
    if structured:
        sdk_parsed = getattr(response, "parsed", None)
        if isinstance(sdk_parsed, (Mapping, list)):
            parsed = sdk_parsed
        else:
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError as error:
                raise InvalidModelResponse(
                    "Gemini returned invalid structured JSON",
                    usage=usage,
                    request_id=request_id,
                ) from error
            if not isinstance(decoded, (dict, list)):
                raise InvalidModelResponse(
                    "Gemini structured response is not JSON data",
                    usage=usage,
                    request_id=request_id,
                )
            parsed = decoded

    return ModelResponse(
        text=text,
        parsed=parsed,
        usage=usage,
        request_id=request_id,
        raw=_raw_response(response),
    )


def _normalized_usage(response: Any) -> ModelUsage:
    counts = gemini_usage_counts(response)
    return ModelUsage(
        input_tokens=counts.input_tokens,
        output_tokens=counts.candidate_tokens + counts.thinking_tokens,
        thinking_tokens=counts.thinking_tokens,
    )


def _provider_error(error: Exception) -> Exception:
    category = classify_gemini_error(error)
    if category == "provider_permission_denied":
        return ProviderPermissionDenied("Gemini request is not permitted")
    if category == "quota_exceeded":
        return ProviderQuotaExceeded("Gemini quota is temporarily unavailable")
    if category == "model_unavailable":
        return ModelUnavailable("The selected Gemini model is unavailable")
    if category == "generation_timeout":
        return ProviderTimeout("Gemini generation timed out")
    return ProviderUnavailable("Gemini is temporarily unavailable")


def _raw_response(response: Any) -> Mapping[str, Any] | None:
    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json", exclude_none=True)
        if isinstance(payload, Mapping):
            return payload
    model_version = _optional_string(getattr(response, "model_version", None))
    return {"model_version": model_version} if model_version else None


def _image_mime_type(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(image) >= 12 and image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _nonnegative_integer(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _optional_string(value: object) -> str | None:
    return str(value) if value else None
