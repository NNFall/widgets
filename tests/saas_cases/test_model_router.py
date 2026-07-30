from __future__ import annotations

import asyncio
import time

import pytest

import app.models.router as router_module
from app.models.contracts import (
    InvalidModelResponse,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelRouteExhausted,
    ModelUsage,
    ProviderCapabilities,
    ProviderUnavailable,
)
from app.models.router import InMemoryModelCallAudit, ModelPolicy, ModelRouter, ProviderTarget


class FakeProvider:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        self.requests.append(request)
        if self.fail:
            raise ProviderUnavailable("temporary outage")
        return ModelResponse(
            text='{"ok":true}',
            parsed={"ok": True},
            usage=ModelUsage(input_tokens=120, output_tokens=15, thinking_tokens=4),
            request_id=f"request-{model}",
        )


def _raising_generate(marker: str):
    async def generate(request: ModelRequest, *, model: str) -> ModelResponse:
        raise ProviderUnavailable(marker)

    return generate


@pytest.mark.asyncio
async def test_role_policy_records_exact_attempt_usage_and_cost() -> None:
    provider = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"agentrouter": provider},
        policies={
            ("visual_critic", "standard"): ModelPolicy(
                prompt_version="visual-v3",
                targets=(ProviderTarget("agentrouter", "glm-5.2", 6_000_000, 6_000_000),),
            )
        },
        audit=audit,
    )

    result = await router.generate(
        role="visual_critic",
        mode="standard",
        request=ModelRequest(prompt="Проверь скриншот"),
    )

    assert result.parsed == {"ok": True}
    call = audit.calls[0]
    assert (call.provider, call.model, call.role, call.attempt) == (
        "agentrouter", "glm-5.2", "visual_critic", 1,
    )
    assert call.prompt_version == "visual-v3"
    assert call.input_tokens == 120
    assert call.output_tokens == 15
    assert call.thinking_tokens == 4
    assert call.cost_microusd == 810
    assert call.latency_ms >= 0


@pytest.mark.asyncio
async def test_fallback_is_a_second_visible_attempt() -> None:
    primary = FakeProvider(fail=True)
    fallback = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("code_review", "express"): ModelPolicy(
                prompt_version="review-v1",
                targets=(
                    ProviderTarget("primary", "gpt-5.5", 7_000_000, 7_000_000),
                    ProviderTarget("fallback", "glm-5.2", 6_000_000, 6_000_000),
                ),
            )
        },
        audit=audit,
    )

    await router.generate(
        role="code_review", mode="express", request=ModelRequest(prompt="Review")
    )

    assert [call.attempt for call in audit.calls] == [1, 2]
    assert [call.status for call in audit.calls] == ["failed", "completed"]
    assert audit.calls[0].error_code == "provider_unavailable"
    assert audit.calls[1].provider == "fallback"


@pytest.mark.asyncio
async def test_image_request_skips_incapable_primary_and_audits_failure() -> None:
    primary = FakeProvider()
    primary.capabilities = ProviderCapabilities(images=False, structured_output=True)
    fallback = FakeProvider()
    fallback.capabilities = ProviderCapabilities(images=True, structured_output=True)
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("visual_critic", "standard"): ModelPolicy(
                prompt_version="visual-v4",
                targets=(
                    ProviderTarget("primary", "glm-5.2", 6_000_000, 6_000_000),
                    ProviderTarget("fallback", "gemini-3.5-flash", 1_000_000, 2_000_000),
                ),
            )
        },
        audit=audit,
    )
    request = ModelRequest(prompt="Inspect", images=(b"exact-image",))

    response = await router.generate(
        role="visual_critic",
        mode="standard",
        request=request,
    )

    assert response.request_id == "request-gemini-3.5-flash"
    assert primary.requests == []
    assert fallback.requests == [request]
    assert [call.status for call in audit.calls] == ["failed", "completed"]
    assert audit.calls[0].error_code == "unsupported_request"


def test_model_request_has_no_output_token_limit() -> None:
    request = ModelRequest(prompt="Generate")

    assert "max_output" not in request.__dataclass_fields__
    assert "max_tokens" not in request.__dataclass_fields__


def test_model_usage_rejects_thinking_greater_than_billed_output() -> None:
    with pytest.raises(ValueError, match="thinking_tokens cannot exceed output_tokens"):
        ModelUsage(input_tokens=10, output_tokens=3, thinking_tokens=4)


@pytest.mark.asyncio
async def test_provider_attempt_deadline_audits_timeout_then_falls_back() -> None:
    class NeverReturningProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    primary = NeverReturningProvider()
    fallback = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("reference_analyst", "express"): ModelPolicy(
                prompt_version="reference-v1",
                targets=(
                    ProviderTarget("primary", "hung", 1, 1),
                    ProviderTarget("fallback", "working", 1, 1),
                ),
            )
        },
        audit=audit,
    )

    response = await asyncio.wait_for(
        router.generate(
            role="reference_analyst",
            mode="express",
            request=ModelRequest(prompt="Bound this call"),
            timeout_seconds=0.1,
        ),
        timeout=1,
    )

    assert response.request_id == "request-working"
    assert [call.status for call in audit.calls] == ["failed", "completed"]
    assert audit.calls[0].error_code == "generation_timeout"
    assert (
        audit.calls[0].input_tokens,
        audit.calls[0].output_tokens,
        audit.calls[0].thinking_tokens,
        audit.calls[0].cost_microusd,
    ) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_fallback_success_preserves_attempt_lineage_and_all_billed_usage() -> None:
    class BilledFailure(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            raise InvalidModelResponse(
                "private upstream payload must not leak",
                usage=ModelUsage(input_tokens=11, output_tokens=7, thinking_tokens=2),
                request_id="primary-request",
            )

    primary = BilledFailure()
    fallback = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(
                    ProviderTarget("primary", "gpt-primary", 7_000_000, 8_000_000),
                    ProviderTarget("fallback", "gemini-fallback", 1_000_000, 2_000_000),
                ),
            )
        },
        audit=audit,
    )

    response = await router.generate(
        role="direction_candidate",
        mode="express",
        request=ModelRequest(prompt="Create a direction"),
        timeout_seconds=1,
    )

    assert response.usage == ModelUsage(
        input_tokens=131,
        output_tokens=22,
        thinking_tokens=6,
    )
    assert response.raw is not None
    assert response.raw["provider"] == "fallback"
    assert response.raw["model"] == "gemini-fallback"
    attempts = response.raw["route_attempts"]
    assert [attempt["provider"] for attempt in attempts] == ["primary", "fallback"]
    assert [attempt["model"] for attempt in attempts] == [
        "gpt-primary",
        "gemini-fallback",
    ]
    assert [attempt["outcome"] for attempt in attempts] == ["failed", "completed"]
    assert [attempt["cost_state"] for attempt in attempts] == ["estimated", "estimated"]
    assert attempts[0]["error_code"] == "invalid_response"
    assert attempts[0]["usage"] == {
        "input_tokens": 11,
        "output_tokens": 7,
        "thinking_tokens": 2,
    }
    assert "private upstream payload" not in str(attempts)


@pytest.mark.asyncio
async def test_all_target_exhaustion_is_terminal_safe_and_preserves_billed_usage() -> None:
    class BilledFailure(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            raise InvalidModelResponse(
                "secret response body",
                usage=ModelUsage(input_tokens=13, output_tokens=5, thinking_tokens=1),
                request_id="billed-request",
            )

    primary = BilledFailure()
    fallback = FakeProvider(fail=True)
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(
                    ProviderTarget("primary", "gpt-primary", 1, 1),
                    ProviderTarget("fallback", "gemini-fallback", 1, 1),
                ),
            )
        },
        audit=InMemoryModelCallAudit(),
    )

    with pytest.raises(ModelRouteExhausted) as caught:
        await router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Create a direction"),
            timeout_seconds=1,
        )

    error = caught.value
    assert error.error_code == "route_exhausted"
    assert error.terminal_reason == "mixed_provider_failures"
    assert error.usage == ModelUsage(input_tokens=13, output_tokens=5, thinking_tokens=1)
    assert [attempt.provider for attempt in error.attempts] == ["primary", "fallback"]
    assert [attempt.model for attempt in error.attempts] == [
        "gpt-primary",
        "gemini-fallback",
    ]
    assert [attempt.error_code for attempt in error.attempts] == [
        "invalid_response",
        "provider_unavailable",
    ]
    assert [attempt.cost_state for attempt in error.attempts] == [
        "estimated",
        "unknown",
    ]
    assert "secret response body" not in error.diagnostic
    assert "temporary outage" not in error.diagnostic
    assert "gpt-primary" in error.diagnostic
    assert "gemini-fallback" in error.diagnostic


@pytest.mark.asyncio
async def test_failed_audit_cannot_mask_billed_provider_failure() -> None:
    class BilledFailure(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            raise InvalidModelResponse(
                "private provider response",
                usage=ModelUsage(input_tokens=9, output_tokens=3),
                request_id="billed-request",
            )

    class FailedRecordAudit(InMemoryModelCallAudit):
        async def record(self, record):
            if record.status == "failed":
                raise RuntimeError("audit-down-private")
            await super().record(record)

    router = ModelRouter(
        providers={"primary": BilledFailure()},
        policies={
            ("visual_critic", "standard"): ModelPolicy(
                prompt_version="visual-v4",
                targets=(ProviderTarget("primary", "model-a", 2_000_000, 3_000_000),),
            )
        },
        audit=FailedRecordAudit(),
    )

    with pytest.raises(ModelRouteExhausted) as caught:
        await router.generate(
            role="visual_critic",
            mode="standard",
            request=ModelRequest(prompt="Inspect"),
            timeout_seconds=1,
        )

    assert caught.value.error_code == "invalid_response"
    assert caught.value.usage == ModelUsage(input_tokens=9, output_tokens=3)
    assert len(caught.value.attempts) == 1
    assert caught.value.attempts[0].cost_microusd == 27
    assert "audit-down-private" not in caught.value.diagnostic


@pytest.mark.asyncio
async def test_cancelled_provider_stays_cancelled_when_cancellation_audit_fails() -> None:
    class BlockingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.stopped.set()
            raise AssertionError("unreachable")

    class FailedCancellationAudit(InMemoryModelCallAudit):
        async def record(self, record):
            if record.status == "cancelled":
                raise RuntimeError("audit-cancel-private")
            await super().record(record)

    provider = BlockingProvider()
    router = ModelRouter(
        providers={"primary": provider},
        policies={
            ("visual_critic", "standard"): ModelPolicy(
                prompt_version="visual-v4",
                targets=(ProviderTarget("primary", "model-a", 1, 1),),
            )
        },
        audit=FailedCancellationAudit(),
    )
    task = asyncio.create_task(
        router.generate(
            role="visual_critic",
            mode="standard",
            request=ModelRequest(prompt="Inspect"),
            timeout_seconds=120,
        )
    )
    await provider.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.stopped.is_set()


@pytest.mark.asyncio
async def test_logical_deadline_is_shared_across_all_targets() -> None:
    class NeverReturningProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    router = ModelRouter(
        providers={
            "primary": NeverReturningProvider(),
            "fallback": NeverReturningProvider(),
        },
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(
                    ProviderTarget("primary", "hung-primary", 1, 1),
                    ProviderTarget("fallback", "hung-fallback", 1, 1),
                ),
            )
        },
        audit=InMemoryModelCallAudit(),
    )

    with pytest.raises(ModelRouteExhausted) as caught:
        await asyncio.wait_for(
            router.generate(
                role="direction_candidate",
                mode="express",
                request=ModelRequest(prompt="Bound the whole route"),
                timeout_seconds=0.1,
            ),
            timeout=0.15,
        )

    assert [attempt.error_code for attempt in caught.value.attempts] == [
        "generation_timeout",
        "generation_timeout",
    ]


@pytest.mark.asyncio
async def test_timed_out_provider_cleanup_preserves_fallback_and_audit_budget() -> None:
    class StubbornPrimary(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.cancellations = 0
            self.stopped = asyncio.Event()

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            while True:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancellations += 1
                    if self.cancellations >= 3:
                        self.stopped.set()
                        raise

    primary = StubbornPrimary()
    fallback = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(
                    ProviderTarget("primary", "hung-primary", 1, 1),
                    ProviderTarget("fallback", "gemini-fallback", 1, 1),
                ),
            )
        },
        audit=audit,
    )

    started = time.monotonic()
    response = await asyncio.wait_for(
        router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Keep the fallback reserve"),
            timeout_seconds=0.4,
        ),
        timeout=0.6,
    )
    elapsed = time.monotonic() - started

    assert response.raw is not None
    assert response.raw["provider"] == "fallback"
    assert [attempt["outcome"] for attempt in response.raw["route_attempts"]] == [
        "failed",
        "completed",
    ]
    assert [call.status for call in audit.calls] == [
        "failed",
        "completed",
    ]
    assert primary.stopped.is_set()
    assert len(fallback.requests) == 1
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_unsettled_timed_out_provider_fails_closed_before_fallback() -> None:
    class UnsettledPrimary(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.allow_stop = False
            self.task: asyncio.Task[ModelResponse] | None = None
            self.stopped = asyncio.Event()

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            self.task = asyncio.current_task()
            try:
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if self.allow_stop:
                            raise
            finally:
                self.stopped.set()

    primary = UnsettledPrimary()
    fallback = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(
                    ProviderTarget("primary", "unsettled-primary", 1, 1),
                    ProviderTarget("fallback", "must-not-overlap", 1, 1),
                ),
            )
        },
        audit=audit,
    )

    try:
        with pytest.raises(ModelProviderError) as caught:
            await asyncio.wait_for(
                router.generate(
                    role="direction_candidate",
                    mode="express",
                    request=ModelRequest(prompt="Never overlap provider calls"),
                    timeout_seconds=0.2,
                ),
                timeout=0.3,
            )

        assert caught.value.error_code == "provider_cleanup_incomplete"
        assert fallback.requests == []
        assert [call.status for call in audit.calls] == ["failed"]
    finally:
        primary.allow_stop = True
        await asyncio.wait_for(router.aclose(), timeout=0.2)
    assert primary.stopped.is_set()


@pytest.mark.asyncio
async def test_expired_route_deadline_never_dispatches_fallback() -> None:
    class SlowCancellationProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Model a transport cleanup that really carries the event loop
                # beyond the logical route deadline. An interruptible sleep is
                # stopped by the router's bounded second cancellation and no
                # longer proves the post-deadline dispatch guard.
                time.sleep(0.025)
                raise

    primary = SlowCancellationProvider()
    fallback = FakeProvider()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(
                    ProviderTarget("primary", "hung-primary", 1, 1),
                    ProviderTarget("fallback", "must-not-run", 1, 1),
                ),
            )
        },
        audit=InMemoryModelCallAudit(),
    )

    started = time.monotonic()
    with pytest.raises(ModelRouteExhausted):
        await asyncio.wait_for(
            router.generate(
                role="direction_candidate",
                mode="express",
                request=ModelRequest(prompt="Do not dispatch after expiry"),
                timeout_seconds=0.04,
            ),
            timeout=0.15,
        )
    elapsed = time.monotonic() - started

    assert len(primary.requests) == 1
    assert fallback.requests == []
    assert elapsed < 0.07


@pytest.mark.asyncio
async def test_deadline_expiring_inside_dispatched_audit_never_starts_provider() -> None:
    class BlockingDispatchedAudit(InMemoryModelCallAudit):
        async def record(self, call) -> None:
            if call.status == "dispatched":
                # Deliberately block the event loop so asyncio.timeout_at cannot
                # inject cancellation until after record() has returned.
                time.sleep(0.03)
            await super().record(call)

    provider = FakeProvider()
    router = ModelRouter(
        providers={"provider": provider},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "must-not-run", 1, 1),),
            )
        },
        audit=BlockingDispatchedAudit(),
    )

    with pytest.raises(ModelRouteExhausted):
        await router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Do not epsilon-dispatch"),
            timeout_seconds=0.01,
        )

    assert provider.requests == []


@pytest.mark.asyncio
async def test_route_deadline_bounds_hanging_final_audit() -> None:
    class HangingAudit(InMemoryModelCallAudit):
        async def record(self, call) -> None:
            await asyncio.sleep(0.2)

    provider = FakeProvider()
    router = ModelRouter(
        providers={"provider": provider},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=HangingAudit(),
    )

    started = time.monotonic()
    with pytest.raises(ModelRouteExhausted):
        await asyncio.wait_for(
            router.generate(
                role="direction_candidate",
                mode="express",
                request=ModelRequest(prompt="Bound audit"),
                timeout_seconds=0.02,
            ),
            timeout=0.08,
        )
    elapsed = time.monotonic() - started

    assert provider.requests == []
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_external_cancellation_uses_short_cleanup_budget_and_stops_provider() -> None:
    provider_started = asyncio.Event()
    provider_stopped = asyncio.Event()

    class SlowFirstCancellationProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            provider_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Simulate a provider adapter whose first cancellation enters a
                # long cleanup. The router must bound that cleanup independently
                # from the much longer logical route deadline.
                await asyncio.sleep(60)
                raise
            finally:
                provider_stopped.set()

    provider = SlowFirstCancellationProvider()
    router = ModelRouter(
        providers={"provider": provider},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=InMemoryModelCallAudit(),
    )

    route = asyncio.create_task(
        router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Cancel with bounded cleanup"),
            timeout_seconds=120,
        )
    )
    await asyncio.wait_for(provider_started.wait(), timeout=0.05)
    started = time.monotonic()
    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(route, timeout=0.2)
    elapsed = time.monotonic() - started

    assert elapsed < 0.08
    assert provider_stopped.is_set()


@pytest.mark.asyncio
async def test_external_cancellation_reaper_is_drained_by_router_close() -> None:
    class CancellationResistantProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.allow_stop = False
            self.started = asyncio.Event()
            self.stopped = asyncio.Event()

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            self.started.set()
            try:
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if self.allow_stop:
                            raise
            finally:
                self.stopped.set()

    provider = CancellationResistantProvider()
    router = ModelRouter(
        providers={"provider": provider},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=InMemoryModelCallAudit(),
    )
    route = asyncio.create_task(
        router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Cancel and retain cleanup ownership"),
            timeout_seconds=120,
        )
    )
    await asyncio.wait_for(provider.started.wait(), timeout=0.05)

    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(route, timeout=0.1)
    provider.allow_stop = True
    await asyncio.wait_for(router.aclose(), timeout=0.2)

    assert provider.stopped.is_set()


@pytest.mark.asyncio
async def test_external_cancellation_during_timeout_cleanup_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(router_module, "_PROVIDER_CANCELLATION_GRACE_SECONDS", 0.5)
    monkeypatch.setattr(router_module, "_ROUTE_FINALIZATION_GRACE_SECONDS", 0.5)

    class CleanupBlockingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.allow_stop = asyncio.Event()
            self.cleanup_started = asyncio.Event()
            self.stopped = asyncio.Event()

        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cleanup_started.set()
                while not self.allow_stop.is_set():
                    try:
                        await self.allow_stop.wait()
                    except asyncio.CancelledError:
                        if self.allow_stop.is_set():
                            raise
                raise
            finally:
                self.stopped.set()

    provider = CleanupBlockingProvider()
    router = ModelRouter(
        providers={"provider": provider},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=InMemoryModelCallAudit(),
    )
    route = asyncio.create_task(
        router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Cancel inside timeout cleanup"),
            timeout_seconds=0.05,
        )
    )
    await asyncio.wait_for(provider.cleanup_started.wait(), timeout=0.1)

    route.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(route, timeout=0.1)
    finally:
        provider.allow_stop.set()
        await asyncio.wait_for(router.aclose(), timeout=0.2)

    assert provider.stopped.is_set()


@pytest.mark.asyncio
async def test_success_fails_closed_when_completed_audit_cannot_be_finalized() -> None:
    class HangingCompletedAudit(InMemoryModelCallAudit):
        async def record(self, call) -> None:
            if call.status == "completed":
                await asyncio.sleep(60)
            await super().record(call)

    provider = FakeProvider()
    audit = HangingCompletedAudit()
    router = ModelRouter(
        providers={"provider": provider},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=audit,
    )

    with pytest.raises(RuntimeError, match="terminal model-call audit"):
        await asyncio.wait_for(
            router.generate(
                role="direction_candidate",
                mode="express",
                request=ModelRequest(prompt="Never return unaudited success"),
                timeout_seconds=0.03,
            ),
            timeout=0.1,
        )

    assert provider.requests
    assert [call.status for call in audit.calls] == ["dispatched"]


@pytest.mark.asyncio
async def test_cancellation_does_not_wait_for_hanging_best_effort_audit() -> None:
    provider_started = asyncio.Event()

    class NeverReturningProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            provider_started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    class HangingCancellationAudit(InMemoryModelCallAudit):
        def __init__(self) -> None:
            super().__init__()
            self.statuses: list[str] = []

        async def record(self, call) -> None:
            self.statuses.append(call.status)
            if call.status == "cancelled":
                await asyncio.sleep(0.2)
            await super().record(call)

    audit = HangingCancellationAudit()
    router = ModelRouter(
        providers={"provider": NeverReturningProvider()},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=audit,
    )

    route = asyncio.create_task(
        router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Cancel promptly"),
            timeout_seconds=120,
        )
    )
    await asyncio.wait_for(provider_started.wait(), timeout=0.05)
    started = time.monotonic()
    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(route, timeout=0.08)
    elapsed = time.monotonic() - started

    assert "cancelled" in audit.statuses
    assert elapsed < 0.05


@pytest.mark.asyncio
async def test_all_invalid_responses_classify_terminal_as_model_invalid_output() -> None:
    class InvalidProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            raise InvalidModelResponse("private invalid payload")

    router = ModelRouter(
        providers={"first": InvalidProvider(), "second": InvalidProvider()},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(
                    ProviderTarget("first", "model-a", 1, 1),
                    ProviderTarget("second", "model-b", 1, 1),
                ),
            )
        },
        audit=InMemoryModelCallAudit(),
    )

    with pytest.raises(ModelRouteExhausted) as caught:
        await router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Invalid everywhere"),
        )

    assert caught.value.error_code == "invalid_response"
    assert caught.value.terminal_reason == "all_invalid_response"
    assert '"terminal_reason":"all_invalid_response"' in caught.value.diagnostic


@pytest.mark.asyncio
async def test_success_without_usage_report_marks_cost_state_unknown() -> None:
    class NoUsageProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            return ModelResponse(text="{}", parsed={})

    router = ModelRouter(
        providers={"provider": NoUsageProvider()},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=InMemoryModelCallAudit(),
    )

    response = await router.generate(
        role="direction_candidate",
        mode="express",
        request=ModelRequest(prompt="No usage metadata"),
    )

    assert response.raw is not None
    assert response.raw["route_attempts"][0]["cost_state"] == "unknown"


@pytest.mark.asyncio
async def test_audit_failure_message_excludes_private_provider_payload() -> None:
    marker = "private-provider-payload-and-secret-url"
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"provider": FakeProvider(fail=True)},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=audit,
    )
    router._providers["provider"].generate = _raising_generate(marker)

    with pytest.raises(ModelRouteExhausted):
        await router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Private prompt"),
        )

    call = audit.calls[0]
    assert marker not in (call.error_message or "")
    assert call.error_message == "provider_unavailable"
    assert (call.provider, call.model, call.attempt, call.error_code) == (
        "provider",
        "model",
        1,
        "provider_unavailable",
    )


@pytest.mark.asyncio
async def test_router_closes_each_unique_provider_once_and_is_idempotent() -> None:
    class ClosableProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    shared = ClosableProvider()
    router = ModelRouter(
        providers={"primary": shared, "alias": shared},
        policies={
            ("widget_generator", "express"): ModelPolicy(
                prompt_version="builder-v1",
                targets=(ProviderTarget("primary", "model", 1, 1),),
            )
        },
        audit=InMemoryModelCallAudit(),
    )

    await asyncio.gather(router.aclose(), router.aclose())
    await router.aclose()

    assert shared.close_calls == 1
