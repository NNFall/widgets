from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping, Protocol

from app.models.contracts import (
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
)


@dataclass(frozen=True, slots=True)
class ProviderTarget:
    provider: str
    model: str
    input_price_microusd_per_million: int
    output_price_microusd_per_million: int


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    prompt_version: str
    targets: tuple[ProviderTarget, ...]

    def __post_init__(self) -> None:
        if not self.prompt_version or not self.targets:
            raise ValueError("model policy requires a prompt version and target")


@dataclass(frozen=True, slots=True)
class ModelCallAuditRecord:
    provider: str
    model: str
    role: str
    mode: str
    prompt_version: str
    attempt: int
    status: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    latency_ms: int
    cost_microusd: int
    request_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class ModelCallAudit(Protocol):
    async def record(self, call: ModelCallAuditRecord) -> None: ...


class InMemoryModelCallAudit:
    def __init__(self) -> None:
        self.calls: list[ModelCallAuditRecord] = []

    async def record(self, call: ModelCallAuditRecord) -> None:
        self.calls.append(call)


class ModelRouter:
    def __init__(
        self,
        *,
        providers: Mapping[str, ModelProvider],
        policies: Mapping[tuple[str, str], ModelPolicy],
        audit: ModelCallAudit,
    ) -> None:
        self._providers = dict(providers)
        self._policies = dict(policies)
        self._audit = audit

    async def generate(
        self,
        *,
        role: str,
        mode: str,
        request: ModelRequest,
    ) -> ModelResponse:
        try:
            policy = self._policies[(role, mode)]
        except KeyError as error:
            raise ValueError(f"no model policy for {role}:{mode}") from error

        last_error: ModelProviderError | None = None
        for attempt, target in enumerate(policy.targets, start=1):
            try:
                provider = self._providers[target.provider]
            except KeyError as error:
                raise ValueError(f"provider is not configured: {target.provider}") from error
            started = time.perf_counter()
            try:
                response = await provider.generate(request, model=target.model)
            except ModelProviderError as error:
                last_error = error
                await self._audit.record(
                    ModelCallAuditRecord(
                        provider=target.provider,
                        model=target.model,
                        role=role,
                        mode=mode,
                        prompt_version=policy.prompt_version,
                        attempt=attempt,
                        status="failed",
                        input_tokens=0,
                        output_tokens=0,
                        thinking_tokens=0,
                        latency_ms=_elapsed_ms(started),
                        cost_microusd=0,
                        error_code=error.error_code,
                        error_message=str(error)[:1000],
                    )
                )
                continue

            usage = response.usage
            cost = _cost_microusd(target, usage.input_tokens, usage.output_tokens)
            await self._audit.record(
                ModelCallAuditRecord(
                    provider=target.provider,
                    model=target.model,
                    role=role,
                    mode=mode,
                    prompt_version=policy.prompt_version,
                    attempt=attempt,
                    status="completed",
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    thinking_tokens=usage.thinking_tokens,
                    latency_ms=_elapsed_ms(started),
                    cost_microusd=cost,
                    request_id=response.request_id,
                )
            )
            return response

        if last_error is not None:
            raise last_error
        raise RuntimeError("model policy contained no executable targets")


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _cost_microusd(
    target: ProviderTarget,
    input_tokens: int,
    output_tokens: int,
) -> int:
    numerator = (
        input_tokens * target.input_price_microusd_per_million
        + output_tokens * target.output_price_microusd_per_million
    )
    return round(numerator / 1_000_000)
