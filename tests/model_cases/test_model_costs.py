from __future__ import annotations

from uuid import uuid4

import pytest

from app.models.contracts import ModelRouteAttempt, ModelUsage
from app.models.costs import (
    CostState,
    ModelPricingSnapshot,
    resolve_model_cost,
)
from app.models.lineage import ModelCallStatus, ModelInvocationContext


def test_invocation_context_requires_a_nonempty_operation() -> None:
    with pytest.raises(ValueError, match="operation must be non-empty"):
        ModelInvocationContext(
            stage_attempt_id=None,
            stage=None,
            operation="  ",
        )


@pytest.mark.parametrize("semantic_attempt", [0, -1, True])
def test_invocation_context_requires_positive_integer_semantic_attempt(
    semantic_attempt: int,
) -> None:
    with pytest.raises(ValueError, match="semantic_attempt must be a positive integer"):
        ModelInvocationContext(
            stage_attempt_id=None,
            stage=None,
            operation="visual_critique",
            semantic_attempt=semantic_attempt,
        )


def test_invocation_context_keeps_explicit_nullable_candidate_and_persona() -> None:
    attempt_id = uuid4()
    context = ModelInvocationContext(
        stage_attempt_id=attempt_id,
        stage="motion_polish",
        operation="visual_critique",
    )

    assert context.stage_attempt_id == attempt_id
    assert context.candidate_id is None
    assert context.persona is None
    assert "metadata" not in context.__dataclass_fields__


def test_one_invocation_context_is_reusable_across_fallback_attempts() -> None:
    context = ModelInvocationContext(
        stage_attempt_id=uuid4(),
        stage="art_direction",
        operation="direction_candidate",
        candidate_id="candidate-1",
        persona="brand_strategist",
    )

    fallback_attempts = [(context, fallback_index) for fallback_index in (1, 2)]

    assert fallback_attempts[0][0] is fallback_attempts[1][0]
    assert [item[1] for item in fallback_attempts] == [1, 2]
    assert "fallback_index" not in context.__dataclass_fields__


def test_model_call_status_distinguishes_every_terminal_outcome() -> None:
    assert {status.value for status in ModelCallStatus} == {
        "completed",
        "failed",
        "timed_out",
        "cancelled",
    }


@pytest.mark.parametrize(
    "usage",
    [
        ModelUsage(input_tokens=10, output_tokens=3, cache_read_tokens=4),
        ModelUsage(input_tokens=10, output_tokens=3, cache_write_tokens=4),
    ],
)
def test_model_usage_accepts_individual_cache_buckets(usage: ModelUsage) -> None:
    assert usage.input_tokens == 10


def test_model_usage_rejects_cache_buckets_larger_than_input() -> None:
    with pytest.raises(ValueError, match="cache token buckets cannot exceed input_tokens"):
        ModelUsage(
            input_tokens=10,
            output_tokens=3,
            cache_read_tokens=7,
            cache_write_tokens=4,
        )


@pytest.mark.parametrize(
    "field",
    [
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    ],
)
def test_model_usage_rejects_every_negative_bucket(field: str) -> None:
    values = {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    values[field] = -1

    with pytest.raises(ValueError, match="model usage cannot be negative"):
        ModelUsage(**values)


def test_route_attempt_diagnostic_preserves_cache_usage_buckets() -> None:
    attempt = ModelRouteAttempt(
        provider="agentrouter",
        model="glm-5.2",
        outcome="completed",
        latency_ms=5,
        usage=ModelUsage(
            input_tokens=10,
            output_tokens=3,
            cache_read_tokens=4,
            cache_write_tokens=2,
        ),
        cost_microusd=12,
        cost_state="estimated",
    )

    assert attempt.to_dict()["usage"] == {
        "input_tokens": 10,
        "output_tokens": 3,
        "thinking_tokens": 0,
        "cache_read_tokens": 4,
        "cache_write_tokens": 2,
    }


def test_rate_card_cost_is_estimated_without_double_counting_cached_input() -> None:
    pricing = ModelPricingSnapshot(
        currency="USD",
        source="agentrouter-console",
        effective_version="2026-07-28",
        input_rate_microusd_per_million=6_000_000,
        cache_read_rate_microusd_per_million=1_000_000,
        cache_write_rate_microusd_per_million=2_000_000,
        output_rate_microusd_per_million=9_000_000,
    )
    usage = ModelUsage(
        input_tokens=100,
        output_tokens=20,
        thinking_tokens=5,
        cache_read_tokens=30,
        cache_write_tokens=10,
    )

    resolution = resolve_model_cost(
        provider_dispatched=True,
        usage=usage,
        pricing_snapshot=pricing,
    )

    # (60 * 6) + (30 * 1) + (10 * 2) + (20 * 9) microusd.
    assert resolution.state is CostState.ESTIMATED
    assert resolution.cost_microusd == 590
    assert resolution.pricing_snapshot is pricing


def test_provider_reported_charge_takes_precedence_over_rate_card() -> None:
    pricing = ModelPricingSnapshot(
        currency="USD",
        source="provider-docs",
        effective_version="v1",
        input_rate_microusd_per_million=99_000_000,
        cache_read_rate_microusd_per_million=99_000_000,
        cache_write_rate_microusd_per_million=99_000_000,
        output_rate_microusd_per_million=99_000_000,
    )

    resolution = resolve_model_cost(
        provider_dispatched=True,
        usage=ModelUsage(input_tokens=100, output_tokens=20),
        pricing_snapshot=pricing,
        reported_cost_microusd=123,
    )

    assert resolution.state is CostState.REPORTED
    assert resolution.cost_microusd == 123


def test_pre_dispatch_rejection_is_not_billed() -> None:
    resolution = resolve_model_cost(
        provider_dispatched=False,
        usage=None,
    )

    assert resolution.state is CostState.NOT_BILLED
    assert resolution.cost_microusd == 0


def test_confirmed_no_charge_is_not_billed_after_dispatch() -> None:
    resolution = resolve_model_cost(
        provider_dispatched=True,
        usage=None,
        no_charge_confirmed=True,
    )

    assert resolution.state is CostState.NOT_BILLED
    assert resolution.cost_microusd == 0


def test_dispatched_timeout_without_usage_or_rate_card_has_unknown_cost() -> None:
    resolution = resolve_model_cost(
        provider_dispatched=True,
        usage=None,
    )

    assert resolution.state is CostState.UNKNOWN
    assert resolution.cost_microusd is None


def test_incomplete_rate_card_cannot_produce_an_estimate() -> None:
    pricing = ModelPricingSnapshot(
        currency="USD",
        source="provider-docs",
        effective_version="v1",
        input_rate_microusd_per_million=6_000_000,
        cache_read_rate_microusd_per_million=None,
        cache_write_rate_microusd_per_million=None,
        output_rate_microusd_per_million=9_000_000,
    )

    resolution = resolve_model_cost(
        provider_dispatched=True,
        usage=ModelUsage(input_tokens=100, output_tokens=20),
        pricing_snapshot=pricing,
    )

    assert resolution.state is CostState.UNKNOWN
    assert resolution.cost_microusd is None
