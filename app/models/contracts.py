from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    images: bool = False
    structured_output: bool = False


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Normalized billing usage; thinking_tokens is a diagnostic output subset."""

    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.thinking_tokens) < 0:
            raise ValueError("model usage cannot be negative")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    prompt: str
    images: tuple[bytes, ...] = ()
    response_schema: Mapping[str, Any] | None = None
    temperature: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    parsed: Mapping[str, Any] | list[Any] | None = None
    usage: ModelUsage = ModelUsage()
    request_id: str | None = None
    raw: Mapping[str, Any] | None = None


class ModelProviderError(RuntimeError):
    error_code = "provider_error"


class ProviderUnavailable(ModelProviderError):
    error_code = "provider_unavailable"


class ModelUnavailable(ModelProviderError):
    error_code = "model_unavailable"


class ProviderQuotaExceeded(ModelProviderError):
    error_code = "quota_exceeded"


class BilledModelProviderError(ModelProviderError):
    def __init__(
        self,
        message: str,
        *,
        usage: ModelUsage = ModelUsage(),
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage
        self.request_id = request_id


class InvalidModelResponse(BilledModelProviderError):
    error_code = "invalid_response"


class UnsupportedModelRequest(ModelProviderError):
    error_code = "unsupported_request"


class ModelProvider(Protocol):
    capabilities: ProviderCapabilities

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse: ...
