from __future__ import annotations

import json
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
        if self.thinking_tokens > self.output_tokens:
            raise ValueError("thinking_tokens cannot exceed output_tokens")


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


class ProviderTimeout(ModelProviderError):
    error_code = "generation_timeout"


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


@dataclass(frozen=True, slots=True)
class ModelRouteAttempt:
    provider: str
    model: str
    outcome: str
    latency_ms: int
    usage: ModelUsage
    cost_microusd: int
    cost_state: str
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "failed"}:
            raise ValueError("route attempt outcome must be completed or failed")
        if self.cost_state not in {
            "reported",
            "estimated",
            "unknown",
            "not_billed",
        }:
            raise ValueError("invalid route attempt cost state")
        if self.latency_ms < 0 or self.cost_microusd < 0:
            raise ValueError("route attempt latency and cost cannot be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "outcome": self.outcome,
            "latency_ms": self.latency_ms,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "thinking_tokens": self.usage.thinking_tokens,
            },
            "cost_microusd": self.cost_microusd,
            "cost_state": self.cost_state,
            "error_code": self.error_code,
        }


class ModelRouteExhausted(BilledModelProviderError):
    error_code = "route_exhausted"

    def __init__(
        self,
        *,
        attempts: tuple[ModelRouteAttempt, ...],
        usage: ModelUsage,
    ) -> None:
        error_codes = {attempt.error_code for attempt in attempts}
        if error_codes == {"invalid_response"}:
            self.error_code = "invalid_response"
            self.terminal_reason = "all_invalid_response"
        elif len(error_codes) == 1:
            only_code = next(iter(error_codes))
            self.terminal_reason = f"all_{only_code or 'unknown'}"
        else:
            self.terminal_reason = "mixed_provider_failures"
        super().__init__(
            f"model route exhausted after {len(attempts)} attempt(s)",
            usage=usage,
        )
        self.attempts = attempts
        # Deliberately exclude exception messages, prompts, URLs and credentials.
        self.diagnostic = json.dumps(
            {
                "terminal_reason": self.terminal_reason,
                "route_attempts": [attempt.to_dict() for attempt in attempts],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        )


class InvalidModelResponse(BilledModelProviderError):
    error_code = "invalid_response"


class UnsupportedModelRequest(ModelProviderError):
    error_code = "unsupported_request"


class ModelProvider(Protocol):
    capabilities: ProviderCapabilities

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse: ...
