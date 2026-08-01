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
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if min(
            self.input_tokens,
            self.output_tokens,
            self.thinking_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        ) < 0:
            raise ValueError("model usage cannot be negative")
        if self.thinking_tokens > self.output_tokens:
            raise ValueError("thinking_tokens cannot exceed output_tokens")
        if self.cache_read_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("cache token buckets cannot exceed input_tokens")


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
    actual_provider: str | None = None
    actual_model: str | None = None
    reported_cost_microusd: int | None = None
    no_charge_confirmed: bool = False

    def __post_init__(self) -> None:
        _validate_actual_identity(self.actual_provider, self.actual_model)
        _validate_billing_signals(
            self.reported_cost_microusd,
            self.no_charge_confirmed,
        )


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
        actual_provider: str | None = None,
        actual_model: str | None = None,
        reported_cost_microusd: int | None = None,
        no_charge_confirmed: bool = False,
    ) -> None:
        _validate_actual_identity(actual_provider, actual_model)
        _validate_billing_signals(reported_cost_microusd, no_charge_confirmed)
        super().__init__(message)
        self.usage = usage
        self.request_id = request_id
        self.actual_provider = actual_provider
        self.actual_model = actual_model
        self.reported_cost_microusd = reported_cost_microusd
        self.no_charge_confirmed = no_charge_confirmed


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
                "cache_read_tokens": self.usage.cache_read_tokens,
                "cache_write_tokens": self.usage.cache_write_tokens,
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


def _validate_actual_identity(
    actual_provider: str | None,
    actual_model: str | None,
) -> None:
    if (actual_provider is None) != (actual_model is None):
        raise ValueError("actual provider and model must be paired")
    for name, value in (
        ("actual_provider", actual_provider),
        ("actual_model", actual_model),
    ):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be non-empty when provided")


def _validate_billing_signals(
    reported_cost_microusd: int | None,
    no_charge_confirmed: bool,
) -> None:
    if reported_cost_microusd is not None and (
        isinstance(reported_cost_microusd, bool)
        or not isinstance(reported_cost_microusd, int)
        or reported_cost_microusd < 0
    ):
        raise ValueError("reported_cost_microusd must be non-negative or None")
    if not isinstance(no_charge_confirmed, bool):
        raise ValueError("no_charge_confirmed must be a boolean")
    if reported_cost_microusd is not None and no_charge_confirmed:
        raise ValueError("billing signals are mutually exclusive")
