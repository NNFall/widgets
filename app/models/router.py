from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.saas.models import ModelCall

from app.models.contracts import (
    BilledModelProviderError,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderCapabilities,
    UnsupportedModelRequest,
)


@dataclass(frozen=True, slots=True)
class ProviderTarget:
    provider: str
    model: str
    input_price_microusd_per_million: int
    output_price_microusd_per_million: int
    capabilities: ProviderCapabilities | None = None


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    prompt_version: str
    targets: tuple[ProviderTarget, ...]

    def __post_init__(self) -> None:
        if not self.prompt_version or not self.targets:
            raise ValueError("model policy requires a prompt version and target")


@dataclass(frozen=True, slots=True)
class ModelCallAuditRecord:
    run_id: UUID | None
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
    pricing_snapshot: Mapping[str, int | str] | None = None


class ModelCallAudit(Protocol):
    async def record(self, call: ModelCallAuditRecord) -> None: ...


class InMemoryModelCallAudit:
    def __init__(self) -> None:
        self.calls: list[ModelCallAuditRecord] = []

    async def record(self, call: ModelCallAuditRecord) -> None:
        self.calls.append(call)


class SqlModelCallAudit:
    """Persist provider accounting only; prompts and secrets never enter the audit row."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record(self, call: ModelCallAuditRecord) -> None:
        async with self._sessions() as database, database.begin():
            database.add(
                ModelCall(
                    run_id=call.run_id,
                    provider=call.provider,
                    model=call.model,
                    role=call.role,
                    mode=call.mode,
                    prompt_version=call.prompt_version,
                    request_id=call.request_id,
                    attempt=call.attempt,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    thinking_tokens=call.thinking_tokens,
                    latency_ms=call.latency_ms,
                    status=call.status,
                    error_code=call.error_code,
                    error_message=call.error_message,
                    cost_microusd=call.cost_microusd,
                    pricing_snapshot=dict(call.pricing_snapshot or {}),
                )
            )


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
        run_id: UUID | None = None,
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
                _ensure_supported(provider, target, request)
                response = await provider.generate(request, model=target.model)
            except ModelProviderError as error:
                last_error = error
                usage = (
                    error.usage
                    if isinstance(error, BilledModelProviderError)
                    else ModelUsage()
                )
                await self._audit.record(
                    ModelCallAuditRecord(
                        run_id=run_id,
                        provider=target.provider,
                        model=target.model,
                        role=role,
                        mode=mode,
                        prompt_version=policy.prompt_version,
                        attempt=attempt,
                        status="failed",
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        thinking_tokens=usage.thinking_tokens,
                        latency_ms=_elapsed_ms(started),
                        cost_microusd=_cost_microusd(
                            target,
                            usage.input_tokens,
                            usage.output_tokens,
                        ),
                        request_id=(
                            error.request_id
                            if isinstance(error, BilledModelProviderError)
                            else None
                        ),
                        error_code=error.error_code,
                        error_message=str(error)[:1000],
                        pricing_snapshot=_pricing_snapshot(target),
                    )
                )
                continue

            usage = response.usage
            cost = _cost_microusd(target, usage.input_tokens, usage.output_tokens)
            await self._audit.record(
                ModelCallAuditRecord(
                    run_id=run_id,
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
                    pricing_snapshot=_pricing_snapshot(target),
                )
            )
            return response

        if last_error is not None:
            raise last_error
        raise RuntimeError("model policy contained no executable targets")


def _ensure_supported(
    provider: ModelProvider,
    target: ProviderTarget,
    request: ModelRequest,
) -> None:
    capabilities = target.capabilities or getattr(
        provider,
        "capabilities",
        ProviderCapabilities(),
    )
    if request.images and not capabilities.images:
        raise UnsupportedModelRequest(
            f"{target.provider} does not support binary image inputs"
        )
    if request.response_schema is not None and not capabilities.structured_output:
        raise UnsupportedModelRequest(
            f"{target.provider} does not support structured output"
        )


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


def _pricing_snapshot(target: ProviderTarget) -> dict[str, int | str]:
    return {
        "currency": "USD",
        "billing_unit_tokens": 1_000_000,
        "input_price_microusd_per_million": (
            target.input_price_microusd_per_million
        ),
        "output_price_microusd_per_million": (
            target.output_price_microusd_per_million
        ),
    }
