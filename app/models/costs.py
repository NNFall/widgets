from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from app.models.contracts import ModelUsage


class CostState(StrEnum):
    REPORTED = "reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"
    NOT_BILLED = "not_billed"


@dataclass(frozen=True, slots=True)
class ModelPricingSnapshot:
    """Frozen rate card used to reproduce an estimated model-call charge."""

    currency: str
    source: str
    effective_version: str
    input_rate_microusd_per_million: int | None
    cache_read_rate_microusd_per_million: int | None
    cache_write_rate_microusd_per_million: int | None
    output_rate_microusd_per_million: int | None
    billing_unit_tokens: int = 1_000_000

    def __post_init__(self) -> None:
        for name in ("currency", "source", "effective_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if isinstance(self.billing_unit_tokens, bool) or not isinstance(
            self.billing_unit_tokens, int
        ) or self.billing_unit_tokens < 1:
            raise ValueError("billing_unit_tokens must be a positive integer")
        for name in (
            "input_rate_microusd_per_million",
            "cache_read_rate_microusd_per_million",
            "cache_write_rate_microusd_per_million",
            "output_rate_microusd_per_million",
        ):
            rate = getattr(self, name)
            if rate is not None and (
                isinstance(rate, bool) or not isinstance(rate, int) or rate < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")

    @property
    def has_complete_rates(self) -> bool:
        return all(
            rate is not None
            for rate in (
                self.input_rate_microusd_per_million,
                self.cache_read_rate_microusd_per_million,
                self.cache_write_rate_microusd_per_million,
                self.output_rate_microusd_per_million,
            )
        )

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "currency": self.currency,
            "source": self.source,
            "effective_version": self.effective_version,
            "billing_unit_tokens": self.billing_unit_tokens,
            "input_rate_microusd_per_million": (
                self.input_rate_microusd_per_million
            ),
            "cache_read_rate_microusd_per_million": (
                self.cache_read_rate_microusd_per_million
            ),
            "cache_write_rate_microusd_per_million": (
                self.cache_write_rate_microusd_per_million
            ),
            "output_rate_microusd_per_million": (
                self.output_rate_microusd_per_million
            ),
        }


@dataclass(frozen=True, slots=True)
class ResolvedModelCost:
    state: CostState
    cost_microusd: int | None
    pricing_snapshot: ModelPricingSnapshot | None = None

    def __post_init__(self) -> None:
        if self.state is CostState.UNKNOWN:
            if self.cost_microusd is not None:
                raise ValueError("unknown model cost must not have an amount")
            return
        if self.cost_microusd is None or self.cost_microusd < 0:
            raise ValueError("known model cost requires a non-negative amount")
        if self.state is CostState.NOT_BILLED and self.cost_microusd != 0:
            raise ValueError("not-billed model cost must be zero")


def resolve_model_cost(
    *,
    provider_dispatched: bool,
    usage: ModelUsage | None,
    pricing_snapshot: ModelPricingSnapshot | None = None,
    reported_cost_microusd: int | None = None,
    no_charge_confirmed: bool = False,
) -> ResolvedModelCost:
    """Resolve cost state without treating missing billing data as a free call."""

    _validate_billing_signal(reported_cost_microusd, no_charge_confirmed)
    if reported_cost_microusd is not None:
        return ResolvedModelCost(
            state=CostState.REPORTED,
            cost_microusd=reported_cost_microusd,
            pricing_snapshot=pricing_snapshot,
        )
    if not provider_dispatched or no_charge_confirmed:
        return ResolvedModelCost(
            state=CostState.NOT_BILLED,
            cost_microusd=0,
            pricing_snapshot=pricing_snapshot,
        )
    if usage is not None and pricing_snapshot is not None and (
        pricing_snapshot.has_complete_rates
    ):
        return ResolvedModelCost(
            state=CostState.ESTIMATED,
            cost_microusd=_estimated_cost(usage, pricing_snapshot),
            pricing_snapshot=pricing_snapshot,
        )
    return ResolvedModelCost(
        state=CostState.UNKNOWN,
        cost_microusd=None,
        pricing_snapshot=pricing_snapshot,
    )


def _estimated_cost(usage: ModelUsage, pricing: ModelPricingSnapshot) -> int:
    rates = (
        pricing.input_rate_microusd_per_million,
        pricing.cache_read_rate_microusd_per_million,
        pricing.cache_write_rate_microusd_per_million,
        pricing.output_rate_microusd_per_million,
    )
    if any(rate is None for rate in rates):
        raise ValueError("a complete rate card is required for cost estimation")
    input_rate, cache_read_rate, cache_write_rate, output_rate = rates
    assert input_rate is not None
    assert cache_read_rate is not None
    assert cache_write_rate is not None
    assert output_rate is not None
    uncached_input = (
        usage.input_tokens - usage.cache_read_tokens - usage.cache_write_tokens
    )
    numerator = (
        uncached_input * input_rate
        + usage.cache_read_tokens * cache_read_rate
        + usage.cache_write_tokens * cache_write_rate
        + usage.output_tokens * output_rate
    )
    return round(Fraction(numerator, pricing.billing_unit_tokens))


def _validate_billing_signal(
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
