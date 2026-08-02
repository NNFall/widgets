from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import Update

import app.models.router as router_module
from app.models.contracts import (
    BilledModelProviderError,
    InvalidModelResponse,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelRouteExhausted,
    ModelUsage,
    ProviderCapabilities,
    ProviderPermissionDenied,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.models.lineage import ModelInvocationContext
from app.models.router import (
    InMemoryModelCallAudit,
    ModelAccountingError,
    ModelCallAuditRecord,
    ModelPolicy,
    ModelRouter,
    ProviderTarget,
    SqlModelCallAudit,
)
from app.saas.models import ModelCall
from tests.saas_cases.test_trial_service import _database


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


def _context(operation: str) -> ModelInvocationContext:
    return ModelInvocationContext(
        stage_attempt_id=None,
        stage=None,
        operation=operation,
    )


def _ledger_record(
    *,
    status: str = "dispatched",
    call_id=None,
    logical_invocation_id=None,
    operation: str = "chat",
) -> ModelCallAuditRecord:
    return ModelCallAuditRecord(
        call_id=call_id or uuid4(),
        run_id=None,
        stage_attempt_id=None,
        logical_invocation_id=logical_invocation_id or uuid4(),
        operation=operation,
        semantic_attempt=1,
        candidate_id=None,
        persona=None,
        fallback_index=1,
        provider="provider",
        model="model",
        role="chat_visitor",
        mode="express",
        prompt_version="chat-v1",
        attempt=1,
        provider_dispatched=status != "dispatched",
        status=status,
        input_tokens=0,
        output_tokens=0,
        thinking_tokens=0,
        latency_ms=0,
        cost_microusd=0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "timed_out", "cancelled"])
async def test_in_memory_audit_rejects_terminal_first_write(status: str) -> None:
    audit = InMemoryModelCallAudit()

    with pytest.raises(ModelAccountingError, match="first write must be dispatched"):
        await audit.record(_ledger_record(status=status))

    assert audit.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "timed_out", "cancelled"])
async def test_sql_audit_rejects_terminal_first_write(tmp_path, status: str) -> None:
    engine, factory, _, _ = await _database(tmp_path)
    audit = SqlModelCallAudit(factory)
    try:
        with pytest.raises(ModelAccountingError, match="first write must be dispatched"):
            await audit.record(_ledger_record(status=status))

        async with factory() as database:
            assert (await database.execute(select(ModelCall))).scalars().all() == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_audit_finalizes_dispatched_row_with_cas(tmp_path) -> None:
    engine, factory, _, _ = await _database(tmp_path)
    audit = SqlModelCallAudit(factory)
    dispatched = _ledger_record()
    terminal = replace(
        dispatched,
        status="completed",
        provider_dispatched=True,
        input_tokens=7,
        output_tokens=3,
        latency_ms=11,
    )
    try:
        await audit.record(dispatched)
        await audit.record(terminal)

        async with factory() as database:
            persisted = await database.get(ModelCall, dispatched.call_id)
        assert persisted is not None
        assert persisted.status == "completed"
        assert persisted.provider_dispatched is True
        assert (persisted.input_tokens, persisted.output_tokens) == (7, 3)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_audit_duplicate_terminal_finalization_is_idempotent(tmp_path) -> None:
    engine, factory, _, _ = await _database(tmp_path)
    audit = SqlModelCallAudit(factory)
    dispatched = _ledger_record()
    terminal = replace(dispatched, status="failed", error_code="invalid_response")
    try:
        await audit.record(dispatched)
        await audit.record(terminal)
        await audit.record(terminal)

        async with factory() as database:
            calls = (await database.execute(select(ModelCall))).scalars().all()
        assert len(calls) == 1
        assert calls[0].status == "failed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_audit_concurrent_identical_terminal_cas_loser_is_idempotent(
    tmp_path,
) -> None:
    engine, setup_factory, _, _ = await _database(tmp_path)
    both_read = asyncio.Event()
    allow_loser_update = asyncio.Event()
    reader_count = 0

    class BarrierSession(AsyncSession):
        async def get(self, entity, ident, **kwargs):
            nonlocal reader_count
            persisted = await super().get(entity, ident, **kwargs)
            task = asyncio.current_task()
            if entity is ModelCall and task is not None and task.get_name() in {
                "terminal-winner",
                "terminal-loser",
            }:
                reader_count += 1
                if reader_count == 2:
                    both_read.set()
                await both_read.wait()
            return persisted

        async def execute(self, statement, *args, **kwargs):
            task = asyncio.current_task()
            if (
                isinstance(statement, Update)
                and task is not None
                and task.get_name() == "terminal-loser"
            ):
                await allow_loser_update.wait()
            return await super().execute(statement, *args, **kwargs)

    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=BarrierSession,
    )
    audit = SqlModelCallAudit(factory)
    dispatched = _ledger_record()
    terminal = replace(
        dispatched,
        status="completed",
        provider_dispatched=True,
        input_tokens=5,
        output_tokens=2,
    )
    try:
        await SqlModelCallAudit(setup_factory).record(dispatched)
        winner = asyncio.create_task(
            audit.record(terminal),
            name="terminal-winner",
        )
        loser = asyncio.create_task(
            audit.record(terminal),
            name="terminal-loser",
        )
        await asyncio.wait_for(winner, timeout=1)
        allow_loser_update.set()
        await asyncio.wait_for(loser, timeout=1)

        async with setup_factory() as database:
            calls = (await database.execute(select(ModelCall))).scalars().all()
        assert len(calls) == 1
        assert calls[0].status == "completed"
        assert (calls[0].input_tokens, calls[0].output_tokens) == (5, 2)
    finally:
        allow_loser_update.set()
        await engine.dispose()


@pytest.mark.asyncio
async def test_sql_audit_rejects_immutable_lineage_conflict(tmp_path) -> None:
    engine, factory, _, _ = await _database(tmp_path)
    audit = SqlModelCallAudit(factory)
    dispatched = _ledger_record()
    try:
        await audit.record(dispatched)
        with pytest.raises(ModelAccountingError, match="immutable model-call lineage"):
            await audit.record(
                replace(dispatched, status="failed", operation="not-chat")
            )

        async with factory() as database:
            persisted = await database.get(ModelCall, dispatched.call_id)
        assert persisted is not None
        assert (persisted.operation, persisted.status) == ("chat", "dispatched")
    finally:
        await engine.dispose()


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
        context=_context("visual_critic"),
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
        role="code_review",
        mode="express",
        request=ModelRequest(prompt="Review"),
        context=_context("code_review"),
    )

    assert [call.attempt for call in audit.calls] == [1, 2]
    assert [call.status for call in audit.calls] == ["failed", "completed"]
    assert audit.calls[0].error_code == "provider_unavailable"
    assert audit.calls[1].provider == "fallback"


@pytest.mark.asyncio
async def test_fallback_attempts_share_explicit_invocation_lineage() -> None:
    primary = FakeProvider(fail=True)
    fallback = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("code_review", "express"): ModelPolicy(
                prompt_version="review-v1",
                targets=(
                    ProviderTarget("primary", "gpt-5.5", 1, 1),
                    ProviderTarget("fallback", "glm-5.2", 1, 1),
                ),
            )
        },
        audit=audit,
    )
    stage_attempt_id = uuid4()
    context = ModelInvocationContext(
        stage_attempt_id=stage_attempt_id,
        stage="visual_review",
        operation="code_review",
        semantic_attempt=2,
        candidate_id="candidate-2",
        persona="skeptic",
    )

    await router.generate(
        role="code_review",
        mode="express",
        request=ModelRequest(prompt="Review"),
        context=context,
    )

    assert len({call.call_id for call in audit.calls}) == 2
    assert len({call.logical_invocation_id for call in audit.calls}) == 1
    assert [call.fallback_index for call in audit.calls] == [1, 2]
    assert [call.attempt for call in audit.calls] == [1, 2]
    assert all(call.stage_attempt_id == stage_attempt_id for call in audit.calls)
    assert all(call.operation == "code_review" for call in audit.calls)
    assert all(call.semantic_attempt == 2 for call in audit.calls)
    assert all(call.candidate_id == "candidate-2" for call in audit.calls)
    assert all(call.persona == "skeptic" for call in audit.calls)


@pytest.mark.asyncio
async def test_in_memory_audit_rejects_conflicting_immutable_lineage() -> None:
    provider = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"provider": provider},
        policies={
            ("chat_visitor", "express"): ModelPolicy(
                prompt_version="chat-v1",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=audit,
    )
    await router.generate(
        role="chat_visitor",
        mode="express",
        request=ModelRequest(prompt="Hello"),
        context=ModelInvocationContext(
            stage_attempt_id=None,
            stage=None,
            operation="chat",
        ),
    )
    completed = audit.calls[0]

    with pytest.raises(RuntimeError, match="immutable model-call lineage"):
        await audit.record(replace(completed, operation="not-chat"))

    assert audit.calls == [completed]


@pytest.mark.asyncio
async def test_permission_denial_is_preserved_and_never_falls_back() -> None:
    class PermissionDeniedProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            raise ProviderPermissionDenied("provider access is not permitted")

    primary = PermissionDeniedProvider()
    fallback = FakeProvider()
    audit = InMemoryModelCallAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("reference_analyzer", "standard"): ModelPolicy(
                prompt_version="reference-v1",
                targets=(
                    ProviderTarget("primary", "gemini-3.6-flash", 1, 1),
                    ProviderTarget("fallback", "must-not-run", 1, 1),
                ),
            )
        },
        audit=audit,
    )

    with pytest.raises(ProviderPermissionDenied) as caught:
        await router.generate(
            role="reference_analyzer",
            mode="standard",
            request=ModelRequest(prompt="Analyze"),
            context=_context("reference_analysis"),
        )

    assert caught.value.error_code == "provider_permission_denied"
    assert len(primary.requests) == 1
    assert fallback.requests == []
    assert [call.error_code for call in audit.calls] == ["provider_permission_denied"]


@pytest.mark.asyncio
async def test_image_request_skips_incapable_primary_and_audits_failure() -> None:
    class RecordingAudit(InMemoryModelCallAudit):
        def __init__(self) -> None:
            super().__init__()
            self.history: list[ModelCallAuditRecord] = []

        async def record(self, call: ModelCallAuditRecord) -> None:
            self.history.append(call)
            await super().record(call)

    primary = FakeProvider()
    primary.capabilities = ProviderCapabilities(images=False, structured_output=True)
    fallback = FakeProvider()
    fallback.capabilities = ProviderCapabilities(images=True, structured_output=True)
    audit = RecordingAudit()
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
        context=_context("visual_critic"),
    )

    assert response.request_id == "request-gemini-3.5-flash"
    assert primary.requests == []
    assert fallback.requests == [request]
    assert [call.status for call in audit.calls] == ["failed", "completed"]
    assert audit.calls[0].error_code == "unsupported_request"
    assert [call.status for call in audit.history[:2]] == ["dispatched", "failed"]
    assert [call.provider_dispatched for call in audit.history[:2]] == [False, False]


def test_model_request_has_no_output_token_limit() -> None:
    request = ModelRequest(prompt="Generate")

    assert "max_output" not in request.__dataclass_fields__
    assert "max_tokens" not in request.__dataclass_fields__


def test_model_usage_rejects_thinking_greater_than_billed_output() -> None:
    with pytest.raises(ValueError, match="thinking_tokens cannot exceed output_tokens"):
        ModelUsage(input_tokens=10, output_tokens=3, thinking_tokens=4)


@pytest.mark.parametrize("factory", [ModelResponse, BilledModelProviderError])
def test_provider_contract_rejects_conflicting_billing_signals(factory) -> None:
    kwargs = {
        "reported_cost_microusd": 17,
        "no_charge_confirmed": True,
    }
    if factory is ModelResponse:
        with pytest.raises(ValueError, match="billing signals are mutually exclusive"):
            factory(text="{}", **kwargs)
    else:
        with pytest.raises(ValueError, match="billing signals are mutually exclusive"):
            factory("provider failed", **kwargs)


@pytest.mark.parametrize("factory", [ModelResponse, BilledModelProviderError])
def test_provider_contract_requires_paired_actual_identity(factory) -> None:
    kwargs = {"actual_provider": "agentrouter", "actual_model": None}
    if factory is ModelResponse:
        with pytest.raises(ValueError, match="actual provider and model must be paired"):
            factory(text="{}", **kwargs)
    else:
        with pytest.raises(ValueError, match="actual provider and model must be paired"):
            factory("provider failed", **kwargs)


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
            context=ModelInvocationContext(
                stage_attempt_id=None,
                stage="reference_analysis",
                operation="reference_analysis",
            ),
            timeout_seconds=0.1,
        ),
        timeout=1,
    )

    assert response.request_id == "request-working"
    assert [call.status for call in audit.calls] == ["timed_out", "completed"]
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
        context=_context("direction_candidate"),
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
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
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
            context=_context("direction_candidate"),
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
            context=_context("visual_critic"),
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
            context=_context("visual_critic"),
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
                context=_context("direction_candidate"),
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
            context=_context("direction_candidate"),
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
        "timed_out",
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
                    context=_context("direction_candidate"),
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
                context=_context("direction_candidate"),
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
            context=_context("direction_candidate"),
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
                context=_context("direction_candidate"),
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
            context=_context("direction_candidate"),
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
            context=_context("direction_candidate"),
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
            context=_context("direction_candidate"),
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
                context=_context("direction_candidate"),
                timeout_seconds=0.03,
            ),
            timeout=0.1,
        )

    assert provider.requests
    assert [call.status for call in audit.calls] == ["dispatched"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "blocked_status"),
    [
        (ProviderUnavailable("offline"), "failed"),
        (ProviderTimeout("slow"), "timed_out"),
    ],
)
async def test_cancellation_during_failed_or_timed_out_audit_preserves_lineage(
    provider_error: ModelProviderError,
    blocked_status: str,
) -> None:
    class FailingProvider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            self.requests.append(request)
            raise provider_error

    class BlockingTerminalAudit(InMemoryModelCallAudit):
        def __init__(self) -> None:
            super().__init__()
            self.terminal_started = asyncio.Event()
            self.prepared_terminal: ModelCallAuditRecord | None = None

        async def record(self, call: ModelCallAuditRecord) -> None:
            if call.status == blocked_status:
                self.prepared_terminal = call
                self.terminal_started.set()
                await asyncio.Event().wait()
            await super().record(call)

    stage_attempt_id = uuid4()
    context = ModelInvocationContext(
        stage_attempt_id=stage_attempt_id,
        stage="visual_review",
        operation="code_review",
        semantic_attempt=3,
        candidate_id="candidate-2",
        persona="skeptic",
    )
    primary = FailingProvider()
    fallback = FakeProvider()
    audit = BlockingTerminalAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("code_review", "express"): ModelPolicy(
                prompt_version="review-v2",
                targets=(
                    ProviderTarget("primary", "model", 1, 1),
                    ProviderTarget("fallback", "must-not-run", 1, 1),
                ),
            )
        },
        audit=audit,
    )

    route = asyncio.create_task(
        router.generate(
            role="code_review",
            mode="express",
            request=ModelRequest(prompt="Cancel blocked terminal accounting"),
            context=context,
            timeout_seconds=120,
        )
    )
    await asyncio.wait_for(audit.terminal_started.wait(), timeout=0.05)
    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(route, timeout=0.1)

    prepared = audit.prepared_terminal
    assert prepared is not None
    assert len(audit.calls) == 1
    cancelled = audit.calls[0]
    assert cancelled.status == "cancelled"
    assert cancelled.error_code == "cancelled"
    assert cancelled.provider_dispatched is True
    assert (
        cancelled.call_id,
        cancelled.stage_attempt_id,
        cancelled.logical_invocation_id,
        cancelled.operation,
        cancelled.semantic_attempt,
        cancelled.candidate_id,
        cancelled.persona,
        cancelled.fallback_index,
    ) == (
        prepared.call_id,
        prepared.stage_attempt_id,
        prepared.logical_invocation_id,
        prepared.operation,
        prepared.semantic_attempt,
        prepared.candidate_id,
        prepared.persona,
        prepared.fallback_index,
    )
    assert cancelled.stage_attempt_id == stage_attempt_id
    assert fallback.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "blocked_status"),
    [
        (None, "completed"),
        (ProviderUnavailable("offline"), "failed"),
        (ProviderTimeout("slow"), "timed_out"),
    ],
)
async def test_cancellation_waits_for_terminal_audit_lock_cleanup_before_compensation(
    provider_error: ModelProviderError | None,
    blocked_status: str,
) -> None:
    class Provider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            if provider_error is not None:
                self.requests.append(request)
                raise provider_error
            return await super().generate(request, model=model)

    class LockHoldingTerminalAudit(InMemoryModelCallAudit):
        def __init__(self) -> None:
            super().__init__()
            self._lock = asyncio.Lock()
            self.terminal_started = asyncio.Event()
            self.cleanup_finished = asyncio.Event()

        async def record(self, call: ModelCallAuditRecord) -> None:
            async with self._lock:
                if call.status == blocked_status:
                    self.terminal_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        await asyncio.sleep(0.05)
                        self.cleanup_finished.set()
                await super().record(call)

    audit = LockHoldingTerminalAudit()
    router = ModelRouter(
        providers={"provider": Provider()},
        policies={
            ("code_review", "express"): ModelPolicy(
                prompt_version="review-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=audit,
    )
    route = asyncio.create_task(
        router.generate(
            role="code_review",
            mode="express",
            request=ModelRequest(prompt="Cancel while terminal audit cleans up"),
            context=_context("code_review"),
            timeout_seconds=120,
        )
    )
    await asyncio.wait_for(audit.terminal_started.wait(), timeout=0.1)

    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(route, timeout=0.3)

    assert audit.cleanup_finished.is_set()
    assert [call.status for call in audit.calls] == ["cancelled"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "blocked_status"),
    [
        (None, "completed"),
        (ProviderUnavailable("offline"), "failed"),
        (ProviderTimeout("slow"), "timed_out"),
    ],
)
async def test_repeated_cancellation_waits_for_initial_audit_cleanup(
    provider_error: ModelProviderError | None,
    blocked_status: str,
) -> None:
    class Provider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            if provider_error is not None:
                self.requests.append(request)
                raise provider_error
            return await super().generate(request, model=model)

    class RepeatedCleanupCancellationAudit(InMemoryModelCallAudit):
        def __init__(self) -> None:
            super().__init__()
            self._lock = asyncio.Lock()
            self.terminal_started = asyncio.Event()
            self.cleanup_started = asyncio.Event()
            self.cleanup_finished = asyncio.Event()
            self.compensation_invoked = asyncio.Event()
            self.compensation_saw_finished_cleanup: bool | None = None

        async def record(self, call: ModelCallAuditRecord) -> None:
            if call.status == "cancelled":
                self.compensation_saw_finished_cleanup = (
                    self.cleanup_finished.is_set()
                )
                self.compensation_invoked.set()
            async with self._lock:
                if call.status == blocked_status:
                    self.terminal_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        self.cleanup_started.set()
                        await asyncio.sleep(0.05)
                        self.cleanup_finished.set()
                await super().record(call)

    audit = RepeatedCleanupCancellationAudit()
    router = ModelRouter(
        providers={"provider": Provider()},
        policies={
            ("code_review", "express"): ModelPolicy(
                prompt_version="review-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=audit,
    )
    route = asyncio.create_task(
        router.generate(
            role="code_review",
            mode="express",
            request=ModelRequest(prompt="Cancel initial audit cleanup twice"),
            context=_context("code_review"),
            timeout_seconds=120,
        )
    )
    await asyncio.wait_for(audit.terminal_started.wait(), timeout=0.1)
    route.cancel()
    await asyncio.wait_for(audit.cleanup_started.wait(), timeout=0.1)

    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(route, timeout=0.3)

    assert audit.compensation_invoked.is_set()
    assert audit.compensation_saw_finished_cleanup is True
    assert [call.status for call in audit.calls] == ["cancelled"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "blocked_status"),
    [
        (None, "completed"),
        (ProviderUnavailable("offline"), "failed"),
        (ProviderTimeout("slow"), "timed_out"),
    ],
)
async def test_repeated_cancellation_does_not_cancel_terminal_compensation(
    provider_error: ModelProviderError | None,
    blocked_status: str,
) -> None:
    class Provider(FakeProvider):
        async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
            if provider_error is not None:
                self.requests.append(request)
                raise provider_error
            return await super().generate(request, model=model)

    class RepeatedCancellationAudit(InMemoryModelCallAudit):
        def __init__(self) -> None:
            super().__init__()
            self._lock = asyncio.Lock()
            self.terminal_started = asyncio.Event()
            self.compensation_started = asyncio.Event()

        async def record(self, call: ModelCallAuditRecord) -> None:
            async with self._lock:
                if call.status == blocked_status:
                    self.terminal_started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        await asyncio.sleep(0.05)
                if call.status == "cancelled":
                    self.compensation_started.set()
                    await asyncio.sleep(0.05)
                await super().record(call)

    audit = RepeatedCancellationAudit()
    router = ModelRouter(
        providers={"provider": Provider()},
        policies={
            ("code_review", "express"): ModelPolicy(
                prompt_version="review-v2",
                targets=(ProviderTarget("provider", "model", 1, 1),),
            )
        },
        audit=audit,
    )
    route = asyncio.create_task(
        router.generate(
            role="code_review",
            mode="express",
            request=ModelRequest(prompt="Cancel terminal compensation twice"),
            context=_context("code_review"),
            timeout_seconds=120,
        )
    )
    await asyncio.wait_for(audit.terminal_started.wait(), timeout=0.1)
    route.cancel()
    await asyncio.wait_for(audit.compensation_started.wait(), timeout=0.2)

    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(route, timeout=0.3)

    assert [call.status for call in audit.calls] == ["cancelled"]


@pytest.mark.asyncio
async def test_cancellation_during_completed_audit_never_leaves_dispatched() -> None:
    class BlockingCompletedAudit(InMemoryModelCallAudit):
        def __init__(self) -> None:
            super().__init__()
            self.completed_started = asyncio.Event()

        async def record(self, call: ModelCallAuditRecord) -> None:
            if call.status == "completed":
                self.completed_started.set()
                await asyncio.Event().wait()
            await super().record(call)

    primary = FakeProvider()
    fallback = FakeProvider()
    audit = BlockingCompletedAudit()
    router = ModelRouter(
        providers={"primary": primary, "fallback": fallback},
        policies={
            ("direction_candidate", "express"): ModelPolicy(
                prompt_version="direction-v2",
                targets=(
                    ProviderTarget("primary", "model", 1, 1),
                    ProviderTarget("fallback", "must-not-run", 1, 1),
                ),
            )
        },
        audit=audit,
    )

    route = asyncio.create_task(
        router.generate(
            role="direction_candidate",
            mode="express",
            request=ModelRequest(prompt="Cancel during completed accounting"),
            context=_context("direction_candidate"),
            timeout_seconds=120,
        )
    )
    await asyncio.wait_for(audit.completed_started.wait(), timeout=0.05)
    started = time.monotonic()
    route.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(route, timeout=0.1)

    assert time.monotonic() - started < 0.05
    assert [call.status for call in audit.calls] in (["cancelled"], ["completed"])
    assert fallback.requests == []


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
            context=_context("direction_candidate"),
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
            context=_context("direction_candidate"),
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
        context=_context("direction_candidate"),
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
            context=_context("direction_candidate"),
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
