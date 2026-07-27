from __future__ import annotations

import pytest

from app.models.contracts import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
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


def test_model_request_has_no_output_token_limit() -> None:
    request = ModelRequest(prompt="Generate")

    assert "max_output" not in request.__dataclass_fields__
    assert "max_tokens" not in request.__dataclass_fields__
