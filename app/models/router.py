from __future__ import annotations

import asyncio
import inspect
import math
import time
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Callable, Mapping, Protocol
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.contracts import (
    BilledModelProviderError,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelRouteAttempt,
    ModelRouteExhausted,
    ModelUsage,
    ProviderCapabilities,
    ProviderTimeout,
    UnsupportedModelRequest,
)
from app.models.costs import (
    ModelPricingSnapshot,
    ResolvedModelCost,
    resolve_model_cost,
)
from app.models.lineage import ModelInvocationContext
from app.saas.models import ModelCall


_FALLBACK_ERROR_CODES = frozenset(
    {
        "generation_timeout",
        "provider_unavailable",
        "quota_exceeded",
        "model_unavailable",
        "invalid_response",
        # Capability failures are target-specific, so a capable target may continue.
        "unsupported_request",
    }
)
_ROUTE_FINALIZATION_GRACE_SECONDS = 0.01
_PROVIDER_CANCELLATION_GRACE_SECONDS = 0.02
_AUDIT_CANCELLATION_SETTLEMENT_GRACE_SECONDS = 0.1
_AUDIT_CANCELLATION_COMPENSATION_GRACE_SECONDS = 0.1


class _ProviderCleanupIncomplete(ModelProviderError):
    error_code = "provider_cleanup_incomplete"


class ModelAccountingError(RuntimeError):
    """A model-call ledger write violated its append/finalize contract."""


@dataclass(frozen=True, slots=True)
class ProviderTarget:
    provider: str
    model: str
    input_price_microusd_per_million: int
    output_price_microusd_per_million: int
    capabilities: ProviderCapabilities | None = None
    cache_read_price_microusd_per_million: int | None = None
    cache_write_price_microusd_per_million: int | None = None


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    prompt_version: str
    targets: tuple[ProviderTarget, ...]

    def __post_init__(self) -> None:
        if not self.prompt_version or not self.targets:
            raise ValueError("model policy requires a prompt version and target")


@dataclass(frozen=True, slots=True)
class ModelCallAuditRecord:
    call_id: UUID
    run_id: UUID | None
    stage_attempt_id: UUID | None
    logical_invocation_id: UUID
    operation: str
    semantic_attempt: int
    candidate_id: str | None
    persona: str | None
    fallback_index: int
    provider: str
    model: str
    role: str
    mode: str
    prompt_version: str
    attempt: int
    provider_dispatched: bool
    status: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: int
    cost_state: str
    cost_microusd: int | None
    actual_provider: str | None = None
    actual_model: str | None = None
    request_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    pricing_snapshot: Mapping[str, int | str | None] | None = None


class ModelCallAudit(Protocol):
    async def record(self, call: ModelCallAuditRecord) -> None: ...


class InMemoryModelCallAudit:
    def __init__(self) -> None:
        self.calls: list[ModelCallAuditRecord] = []

    async def record(self, call: ModelCallAuditRecord) -> None:
        for index, existing in enumerate(self.calls):
            if existing.call_id == call.call_id:
                _assert_immutable_lineage(existing, call)
                if existing.status != "dispatched":
                    if existing != call:
                        raise ModelAccountingError(
                            f"conflicting terminal model-call audit for {call.call_id}"
                        )
                    return
                if call.status == "dispatched":
                    if _is_provider_dispatch_promotion(existing, call):
                        self.calls[index] = call
                        return
                    if existing != call:
                        raise ModelAccountingError(
                            f"conflicting dispatched model-call audit for {call.call_id}"
                        )
                    return
                self.calls[index] = call
                return
        if call.status != "dispatched":
            raise ModelAccountingError(
                f"model-call first write must be dispatched for {call.call_id}"
            )
        self.calls.append(call)


class SqlModelCallAudit:
    """Persist provider accounting only; prompts and secrets never enter the audit row."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record(self, call: ModelCallAuditRecord) -> None:
        async with self._sessions() as database, database.begin():
            persisted = await database.get(ModelCall, call.call_id)
            immutable_values = {
                "run_id": call.run_id,
                "stage_attempt_id": call.stage_attempt_id,
                "logical_invocation_id": call.logical_invocation_id,
                "operation": call.operation,
                "semantic_attempt": call.semantic_attempt,
                "candidate_id": call.candidate_id,
                "persona": call.persona,
                "fallback_index": call.fallback_index,
                "provider": call.provider,
                "model": call.model,
                "role": call.role,
                "mode": call.mode,
                "prompt_version": call.prompt_version,
                "attempt": call.attempt,
            }
            mutable_values = {
                "request_id": call.request_id,
                "provider_dispatched": call.provider_dispatched,
                "actual_provider": call.actual_provider,
                "actual_model": call.actual_model,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "thinking_tokens": call.thinking_tokens,
                "cache_read_tokens": call.cache_read_tokens,
                "cache_write_tokens": call.cache_write_tokens,
                "latency_ms": call.latency_ms,
                "status": call.status,
                "error_code": call.error_code,
                "error_message": call.error_message,
                "cost_state": call.cost_state,
                "cost_microusd": call.cost_microusd,
                "pricing_snapshot": dict(call.pricing_snapshot or {}),
            }
            if persisted is None:
                if call.status != "dispatched":
                    raise ModelAccountingError(
                        f"model-call first write must be dispatched for {call.call_id}"
                    )
                database.add(
                    ModelCall(
                        id=call.call_id,
                        **immutable_values,
                        **mutable_values,
                    )
                )
                return

            _assert_immutable_lineage(persisted, call)
            if persisted.status != "dispatched":
                if not _persisted_terminal_matches(persisted, mutable_values):
                    raise ModelAccountingError(
                        f"conflicting terminal model-call audit for {call.call_id}"
                    )
                return
            if call.status == "dispatched":
                if _is_provider_dispatch_promotion(persisted, call):
                    result = await database.execute(
                        update(ModelCall)
                        .where(
                            ModelCall.id == call.call_id,
                            ModelCall.status == "dispatched",
                            ModelCall.provider_dispatched.is_(False),
                        )
                        .values(provider_dispatched=True)
                        .values(cost_state="unknown", cost_microusd=None)
                    )
                    if result.rowcount != 1:
                        raise ModelAccountingError(
                            f"provider dispatch promotion race for {call.call_id}"
                        )
                    return
                if not _persisted_terminal_matches(persisted, mutable_values):
                    raise ModelAccountingError(
                        f"conflicting dispatched model-call audit for {call.call_id}"
                    )
                return

            result = await database.execute(
                update(ModelCall)
                .where(
                    ModelCall.id == call.call_id,
                    ModelCall.status == "dispatched",
                )
                .values(**mutable_values)
            )
            if result.rowcount != 1:
                await database.refresh(persisted)
                _assert_immutable_lineage(persisted, call)
                if (
                    persisted.status != "dispatched"
                    and _persisted_terminal_matches(persisted, mutable_values)
                ):
                    return
                raise ModelAccountingError(
                    f"model-call finalization race for {call.call_id}"
                )


_IMMUTABLE_AUDIT_FIELDS = (
    "run_id",
    "stage_attempt_id",
    "logical_invocation_id",
    "operation",
    "semantic_attempt",
    "candidate_id",
    "persona",
    "fallback_index",
    "provider",
    "model",
    "role",
    "mode",
    "prompt_version",
    "attempt",
)


def _assert_immutable_lineage(existing: object, call: ModelCallAuditRecord) -> None:
    conflicts = [
        field
        for field in _IMMUTABLE_AUDIT_FIELDS
        if getattr(existing, field) != getattr(call, field)
    ]
    if conflicts:
        raise ModelAccountingError(
            "immutable model-call lineage conflict for "
            f"{call.call_id}: {', '.join(conflicts)}"
        )


def _persisted_terminal_matches(
    persisted: ModelCall,
    values: Mapping[str, object],
) -> bool:
    return all(getattr(persisted, field) == value for field, value in values.items())


def _is_provider_dispatch_promotion(
    existing: object,
    call: ModelCallAuditRecord,
) -> bool:
    if (
        getattr(existing, "status") != "dispatched"
        or call.status != "dispatched"
        or getattr(existing, "provider_dispatched") is not False
        or call.provider_dispatched is not True
    ):
        return False
    mutable_fields = (
        "request_id",
        "actual_provider",
        "actual_model",
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "latency_ms",
        "error_code",
        "error_message",
        "pricing_snapshot",
    )
    return all(
        getattr(existing, field) == getattr(call, field)
        for field in mutable_fields
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
        self._close_lock = asyncio.Lock()
        self._closed = False
        self._provider_reapers: set[asyncio.Task[None]] = set()

    async def generate(
        self,
        *,
        role: str,
        mode: str,
        request: ModelRequest,
        context: ModelInvocationContext,
        run_id: UUID | None = None,
        timeout_seconds: float | None = None,
    ) -> ModelResponse:
        if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("model request timeout must be positive and finite")
        try:
            policy = self._policies[(role, mode)]
        except KeyError as error:
            raise ValueError(f"no model policy for {role}:{mode}") from error

        route_deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        finalization_deadline = (
            route_deadline + _ROUTE_FINALIZATION_GRACE_SECONDS
            if route_deadline is not None
            else None
        )
        route_attempts: list[ModelRouteAttempt] = []
        total_usage = ModelUsage()
        logical_invocation_id = uuid4()
        for attempt, target in enumerate(policy.targets, start=1):
            if route_deadline is not None and time.monotonic() >= route_deadline:
                break
            call_id = uuid4()
            provider_dispatched = False
            try:
                provider = self._providers[target.provider]
            except KeyError as error:
                raise ValueError(f"provider is not configured: {target.provider}") from error
            started = time.perf_counter()
            try:
                dispatched_record = _audit_record(
                    call_id=call_id,
                    run_id=run_id,
                    context=context,
                    logical_invocation_id=logical_invocation_id,
                    target=target,
                    role=role,
                    mode=mode,
                    prompt_version=policy.prompt_version,
                    attempt=attempt,
                    provider_dispatched=False,
                    status="dispatched",
                    started=started,
                )
                if route_deadline is None:
                    await self._audit.record(dispatched_record)
                else:
                    try:
                        async with asyncio.timeout_at(route_deadline):
                            await self._audit.record(dispatched_record)
                    except TimeoutError as error:
                        raise ProviderTimeout(
                            "model route deadline expired during audit"
                        ) from error
                _ensure_supported(provider, target, request)
                dispatched_record = replace(
                    dispatched_record,
                    provider_dispatched=True,
                    cost_state="unknown",
                    cost_microusd=None,
                )
                if route_deadline is None:
                    await self._audit.record(dispatched_record)
                else:
                    try:
                        async with asyncio.timeout_at(route_deadline):
                            await self._audit.record(dispatched_record)
                    except TimeoutError as error:
                        raise ProviderTimeout(
                            "model route deadline expired during audit"
                        ) from error
                attempt_timeout: float | None = None
                if route_deadline is not None:
                    targets_left = len(policy.targets) - attempt + 1
                    remaining = route_deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProviderTimeout(
                            "model route deadline expired before provider dispatch"
                        )
                    attempt_timeout = remaining / targets_left
                provider_dispatched = True
                try:
                    response = await _bounded_provider_generate(
                        provider,
                        request,
                        model=target.model,
                        timeout_seconds=attempt_timeout,
                        finalization_deadline=finalization_deadline,
                        reap_unsettled=self._schedule_provider_task_reaper,
                    )
                except TimeoutError as error:
                    assert attempt_timeout is not None
                    raise ProviderTimeout(
                        f"provider attempt exceeded {attempt_timeout:g} seconds"
                    ) from error
                if route_deadline is not None and time.monotonic() >= route_deadline:
                    raise ProviderTimeout("model route deadline expired")
            except asyncio.CancelledError:
                # Audit storage is best-effort on cancellation: it must never
                # replace the caller's cancellation with a storage failure.
                await _record_audit_resilient(
                    self._audit,
                    _audit_record(
                        call_id=call_id,
                        run_id=run_id,
                        context=context,
                        logical_invocation_id=logical_invocation_id,
                        target=target,
                        role=role,
                        mode=mode,
                        prompt_version=policy.prompt_version,
                        attempt=attempt,
                        provider_dispatched=provider_dispatched,
                        status="cancelled",
                        started=started,
                        error_code="cancelled",
                        error_message="provider call cancelled",
                    ),
                    deadline=(
                        time.monotonic() + _ROUTE_FINALIZATION_GRACE_SECONDS
                    ),
                )
                raise
            except ModelProviderError as error:
                usage = (
                    error.usage
                    if isinstance(error, BilledModelProviderError)
                    else ModelUsage()
                )
                total_usage = _add_usage(total_usage, usage)
                latency_ms = _elapsed_ms(started)
                cost = _resolve_cost(
                    target=target,
                    provider_dispatched=provider_dispatched,
                    usage=usage,
                    reported_cost_microusd=(
                        error.reported_cost_microusd
                        if isinstance(error, BilledModelProviderError)
                        else None
                    ),
                    no_charge_confirmed=(
                        error.no_charge_confirmed
                        if isinstance(error, BilledModelProviderError)
                        else False
                    ),
                )
                route_attempts.append(
                    ModelRouteAttempt(
                        provider=target.provider,
                        model=target.model,
                        outcome="failed",
                        latency_ms=latency_ms,
                        usage=usage,
                        cost_microusd=cost.cost_microusd or 0,
                        cost_state=cost.state.value,
                        error_code=error.error_code,
                    )
                )
                try:
                    failed_record = ModelCallAuditRecord(
                            call_id=call_id,
                            run_id=run_id,
                            stage_attempt_id=context.stage_attempt_id,
                            logical_invocation_id=logical_invocation_id,
                            operation=context.operation,
                            semantic_attempt=context.semantic_attempt,
                            candidate_id=context.candidate_id,
                            persona=context.persona,
                            fallback_index=attempt,
                            provider=target.provider,
                            model=target.model,
                            role=role,
                            mode=mode,
                            prompt_version=policy.prompt_version,
                            attempt=attempt,
                            provider_dispatched=provider_dispatched,
                            status=(
                                "timed_out"
                                if error.error_code == "generation_timeout"
                                else "failed"
                            ),
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            thinking_tokens=usage.thinking_tokens,
                            cache_read_tokens=usage.cache_read_tokens,
                            cache_write_tokens=usage.cache_write_tokens,
                            latency_ms=latency_ms,
                            cost_state=cost.state.value,
                            cost_microusd=cost.cost_microusd,
                            actual_provider=(
                                error.actual_provider
                                if isinstance(error, BilledModelProviderError)
                                else None
                            ),
                            actual_model=(
                                error.actual_model
                                if isinstance(error, BilledModelProviderError)
                                else None
                            ),
                            request_id=(
                                error.request_id
                                if isinstance(error, BilledModelProviderError)
                                else None
                            ),
                            error_code=error.error_code,
                            error_message=error.error_code,
                            pricing_snapshot=_pricing_snapshot_dict(cost),
                        )
                    failed_audit_recorded = await _record_terminal_safe(
                        self._audit,
                        failed_record,
                        deadline=finalization_deadline,
                    )
                except Exception as audit_error:
                    raise ModelRouteExhausted(
                        attempts=tuple(route_attempts),
                        usage=total_usage,
                    ) from audit_error
                if not failed_audit_recorded:
                    raise ModelRouteExhausted(
                        attempts=tuple(route_attempts),
                        usage=total_usage,
                    )
                if error.error_code not in _FALLBACK_ERROR_CODES:
                    raise
                continue

            usage = response.usage
            total_usage = _add_usage(total_usage, usage)
            cost = _resolve_cost(
                target=target,
                provider_dispatched=True,
                usage=usage,
                reported_cost_microusd=response.reported_cost_microusd,
                no_charge_confirmed=response.no_charge_confirmed,
            )
            latency_ms = _elapsed_ms(started)
            route_attempts.append(
                ModelRouteAttempt(
                    provider=target.provider,
                    model=target.model,
                    outcome="completed",
                    latency_ms=latency_ms,
                    usage=usage,
                    cost_microusd=cost.cost_microusd or 0,
                    cost_state=cost.state.value,
                )
            )
            completed_record = ModelCallAuditRecord(
                    call_id=call_id,
                    run_id=run_id,
                    stage_attempt_id=context.stage_attempt_id,
                    logical_invocation_id=logical_invocation_id,
                    operation=context.operation,
                    semantic_attempt=context.semantic_attempt,
                    candidate_id=context.candidate_id,
                    persona=context.persona,
                    fallback_index=attempt,
                    provider=target.provider,
                    model=target.model,
                    role=role,
                    mode=mode,
                    prompt_version=policy.prompt_version,
                    attempt=attempt,
                    provider_dispatched=True,
                    status="completed",
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    thinking_tokens=usage.thinking_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    latency_ms=latency_ms,
                    cost_state=cost.state.value,
                    cost_microusd=cost.cost_microusd,
                    actual_provider=response.actual_provider,
                    actual_model=response.actual_model,
                    request_id=response.request_id,
                    pricing_snapshot=_pricing_snapshot_dict(cost),
                )
            terminal_audit_recorded = await _record_terminal_safe(
                self._audit,
                completed_record,
                deadline=finalization_deadline,
            )
            if not terminal_audit_recorded:
                raise RuntimeError(
                    "terminal model-call audit could not be finalized"
                )
            return replace(
                response,
                usage=total_usage,
                raw={
                    **dict(response.raw or {}),
                    "provider": target.provider,
                    "model": target.model,
                    "route_attempts": [
                        route_attempt.to_dict() for route_attempt in route_attempts
                    ],
                },
            )

        if route_attempts:
            raise ModelRouteExhausted(
                attempts=tuple(route_attempts),
                usage=total_usage,
            )
        raise RuntimeError("model policy contained no executable targets")

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            providers = tuple({id(provider): provider for provider in self._providers.values()}.values())
            errors: list[BaseException] = []
            while self._provider_reapers:
                reapers = tuple(self._provider_reapers)
                results = await asyncio.gather(*reapers, return_exceptions=True)
                self._provider_reapers.difference_update(reapers)
                errors.extend(
                    result for result in results if isinstance(result, BaseException)
                )
            for provider in providers:
                close = getattr(provider, "aclose", None)
                if not callable(close):
                    continue
                try:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                except BaseException as error:
                    errors.append(error)
            if errors:
                raise errors[0]

    def _schedule_provider_task_reaper(
        self,
        task: asyncio.Task[ModelResponse],
    ) -> None:
        if task.done():
            _consume_task_result(task)
            return
        reaper = asyncio.create_task(_reap_provider_task(task))
        self._provider_reapers.add(reaper)
        reaper.add_done_callback(self._provider_reapers.discard)
        reaper.add_done_callback(_consume_task_result)


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


def _add_usage(left: ModelUsage, right: ModelUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        thinking_tokens=left.thinking_tokens + right.thinking_tokens,
        cache_read_tokens=left.cache_read_tokens + right.cache_read_tokens,
        cache_write_tokens=left.cache_write_tokens + right.cache_write_tokens,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _pricing_snapshot(target: ProviderTarget) -> ModelPricingSnapshot:
    return ModelPricingSnapshot(
        source="model_policy",
        effective_version="v1",
        currency="USD",
        billing_unit_tokens=1_000_000,
        input_rate_microusd_per_million=(
            target.input_price_microusd_per_million
        ),
        cache_read_rate_microusd_per_million=(
            target.cache_read_price_microusd_per_million
            if target.cache_read_price_microusd_per_million is not None
            else target.input_price_microusd_per_million
        ),
        cache_write_rate_microusd_per_million=(
            target.cache_write_price_microusd_per_million
            if target.cache_write_price_microusd_per_million is not None
            else target.input_price_microusd_per_million
        ),
        output_rate_microusd_per_million=(
            target.output_price_microusd_per_million
        ),
    )


def _resolve_cost(
    *,
    target: ProviderTarget,
    provider_dispatched: bool,
    usage: ModelUsage,
    reported_cost_microusd: int | None = None,
    no_charge_confirmed: bool = False,
) -> ResolvedModelCost:
    usage_reported = usage if any(
        (
            usage.input_tokens,
            usage.output_tokens,
            usage.thinking_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
        )
    ) else None
    return resolve_model_cost(
        provider_dispatched=provider_dispatched,
        usage=usage_reported,
        pricing_snapshot=_pricing_snapshot(target),
        reported_cost_microusd=reported_cost_microusd,
        no_charge_confirmed=no_charge_confirmed,
    )


def _pricing_snapshot_dict(
    resolution: ResolvedModelCost,
) -> dict[str, int | str | None]:
    if resolution.pricing_snapshot is None:
        return {}
    return resolution.pricing_snapshot.to_dict()


def _audit_record(
    *,
    call_id: UUID,
    run_id: UUID | None,
    context: ModelInvocationContext,
    logical_invocation_id: UUID,
    target: ProviderTarget,
    role: str,
    mode: str,
    prompt_version: str,
    attempt: int,
    provider_dispatched: bool,
    status: str,
    started: float,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ModelCallAuditRecord:
    return ModelCallAuditRecord(
        call_id=call_id,
        run_id=run_id,
        stage_attempt_id=context.stage_attempt_id,
        logical_invocation_id=logical_invocation_id,
        operation=context.operation,
        semantic_attempt=context.semantic_attempt,
        candidate_id=context.candidate_id,
        persona=context.persona,
        fallback_index=attempt,
        provider=target.provider,
        model=target.model,
        role=role,
        mode=mode,
        prompt_version=prompt_version,
        attempt=attempt,
        provider_dispatched=provider_dispatched,
        status=status,
        input_tokens=0,
        output_tokens=0,
        thinking_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        latency_ms=_elapsed_ms(started),
        cost_state="not_billed" if not provider_dispatched else "unknown",
        cost_microusd=0 if not provider_dispatched else None,
        error_code=error_code,
        error_message=error_message,
        pricing_snapshot=_pricing_snapshot(target).to_dict(),
    )


async def _record_finalization_safe(
    audit: ModelCallAudit,
    record: ModelCallAuditRecord,
    *,
    deadline: float | None = None,
) -> bool:
    finalization = asyncio.create_task(audit.record(record))
    try:
        if deadline is None:
            await asyncio.shield(finalization)
        else:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                finalization.cancel()
                finalization.add_done_callback(_consume_task_result)
                return False
            await asyncio.wait_for(
                asyncio.shield(finalization),
                timeout=remaining,
            )
        return True
    except TimeoutError:
        finalization.cancel()
        finalization.add_done_callback(_consume_task_result)
        return False
    except asyncio.CancelledError:
        await _cancel_and_settle_audit_task(finalization)
        raise


async def _record_terminal_safe(
    audit: ModelCallAudit,
    record: ModelCallAuditRecord,
    *,
    deadline: float | None,
) -> bool:
    try:
        return await _record_finalization_safe(
            audit,
            record,
            deadline=deadline,
        )
    except asyncio.CancelledError:
        await _record_audit_resilient(
            audit,
            replace(
                record,
                status="cancelled",
                error_code="cancelled",
                error_message="model-call accounting cancelled",
            ),
            deadline=(
                time.monotonic()
                + _AUDIT_CANCELLATION_COMPENSATION_GRACE_SECONDS
            ),
        )
        raise


async def _cancel_and_settle_audit_task(task: asyncio.Task[None]) -> bool:
    task.cancel()
    return await _wait_for_audit_task_resilient(
        task,
        deadline=(
            time.monotonic() + _AUDIT_CANCELLATION_SETTLEMENT_GRACE_SECONDS
        ),
    )


async def _record_audit_resilient(
    audit: ModelCallAudit,
    record: ModelCallAuditRecord,
    *,
    deadline: float,
) -> bool:
    task = asyncio.create_task(audit.record(record))
    return await _wait_for_audit_task_resilient(task, deadline=deadline)


async def _wait_for_audit_task_resilient(
    task: asyncio.Task[None],
    *,
    deadline: float,
) -> bool:
    while not task.done():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            task.add_done_callback(_consume_task_result)
            return False
        try:
            done, _ = await asyncio.wait((task,), timeout=remaining)
        except asyncio.CancelledError:
            continue
        if task in done:
            break
    _consume_task_result(task)
    return True


def _consume_task_result(task: asyncio.Task[object]) -> None:
    with suppress(BaseException):
        task.result()


async def _bounded_provider_generate(
    provider: ModelProvider,
    request: ModelRequest,
    *,
    model: str,
    timeout_seconds: float | None,
    finalization_deadline: float | None,
    reap_unsettled: Callable[[asyncio.Task[ModelResponse]], None],
) -> ModelResponse:
    task = asyncio.create_task(provider.generate(request, model=model))
    try:
        if timeout_seconds is None:
            return await asyncio.shield(task)
        return await asyncio.wait_for(
            asyncio.shield(task),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        cleanup_deadline = time.monotonic() + _PROVIDER_CANCELLATION_GRACE_SECONDS
        if finalization_deadline is not None:
            cleanup_deadline = min(cleanup_deadline, finalization_deadline)
        try:
            settled = await _cancel_and_settle_provider_task(
                task,
                deadline=cleanup_deadline,
            )
        except asyncio.CancelledError:
            if task.done():
                _consume_task_result(task)
            else:
                reap_unsettled(task)
            raise
        if not settled:
            reap_unsettled(task)
            raise _ProviderCleanupIncomplete(
                "timed-out provider task did not stop inside cleanup budget"
            )
        raise
    except asyncio.CancelledError:
        try:
            settled = await _cancel_and_settle_provider_task(
                task,
                deadline=time.monotonic() + _PROVIDER_CANCELLATION_GRACE_SECONDS,
            )
        except asyncio.CancelledError:
            if task.done():
                _consume_task_result(task)
            else:
                reap_unsettled(task)
            raise
        if not settled:
            reap_unsettled(task)
        raise


async def _cancel_and_settle_provider_task(
    task: asyncio.Task[ModelResponse],
    *,
    deadline: float | None,
) -> bool:
    if task.done():
        _consume_task_result(task)
        return True
    if deadline is None:
        task.cancel()
        if await _wait_for_provider_task(task, timeout_seconds=None):
            return True
        task.add_done_callback(_consume_task_result)
        return False
    cancellation_attempts = 3
    for cleanup_attempt in range(cancellation_attempts):
        if task.done():
            _consume_task_result(task)
            return True
        task.cancel()
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            break
        attempts_left = cancellation_attempts - cleanup_attempt
        settle_slice = remaining / attempts_left
        if await _wait_for_provider_task(task, timeout_seconds=settle_slice):
            return True
    if task.done():
        _consume_task_result(task)
        return True
    # A cancellation raised at the exact cleanup deadline can complete on the
    # next ready-queue turn after wait_for reports its own timeout. Give that
    # already-issued cancellation one non-blocking settle turn before deciding
    # that the provider remains live.
    await asyncio.sleep(0)
    if task.done():
        _consume_task_result(task)
        return True
    task.add_done_callback(_consume_task_result)
    return False


async def _wait_for_provider_task(
    task: asyncio.Task[ModelResponse],
    *,
    timeout_seconds: float | None,
) -> bool:
    try:
        if timeout_seconds is None:
            await asyncio.shield(task)
        else:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=timeout_seconds,
            )
    except TimeoutError:
        pass
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        if not task.done():
            raise
    except Exception:
        # Provider failures are consumed below once the provider task is known
        # to be terminal. They must not replace the route's timeout/cancellation.
        pass
    if task.done():
        _consume_task_result(task)
        return True
    return False


async def _reap_provider_task(task: asyncio.Task[ModelResponse]) -> None:
    try:
        while not task.done():
            task.cancel()
            await _wait_for_provider_task(
                task,
                timeout_seconds=_PROVIDER_CANCELLATION_GRACE_SECONDS,
            )
            if not task.done():
                await asyncio.sleep(0)
    finally:
        if task.done():
            _consume_task_result(task)
        else:
            task.cancel()
            task.add_done_callback(_consume_task_result)
