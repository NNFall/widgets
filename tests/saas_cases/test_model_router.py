from __future__ import annotations

import asyncio

import pytest

from app.models.contracts import (
    ModelRequest,
    ModelResponse,
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
            timeout_seconds=0.01,
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
