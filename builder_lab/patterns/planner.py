from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..engines.base import CompositionPlanResult
from ..models import BuilderRequest, DirectionProposal, TokenUsage
from .models import CompositionPlan, JSONValue, PatternCategory, PatternSelection
from .registry import PatternRegistry
from .resolver import PatternResolutionError, ResolvedComposition, resolve_composition


class CompositionPlannerEngine(Protocol):
    async def plan_composition(
        self,
        *,
        request: BuilderRequest,
        selected_direction: DirectionProposal,
        public_catalog: tuple[dict[str, JSONValue], ...],
        correction: str | None = None,
    ) -> CompositionPlanResult: ...


class CompositionPlanningError(ValueError):
    def __init__(self, message: str, *, usage: TokenUsage = TokenUsage()) -> None:
        super().__init__(message)
        self.usage = usage


@dataclass(frozen=True, slots=True)
class PlannedComposition:
    plan: CompositionPlan
    resolved: ResolvedComposition
    usage: TokenUsage = TokenUsage()
    provider_request_ids: tuple[str, ...] = ()


_VERIFIED_FALLBACK_PATTERNS = (
    (PatternCategory.LAUNCHER, "orb-pulse"),
    (PatternCategory.SHELL, "compact-chat"),
    (PatternCategory.MESSAGES, "paired-bubbles"),
    (PatternCategory.COMPOSER, "single-line-pill"),
    (PatternCategory.MOTION, "spring-reveal"),
)


def _verified_fallback_plan(selected_direction: DirectionProposal) -> CompositionPlan:
    return CompositionPlan(
        schema_version=1,
        direction_id=selected_direction.proposal_id,
        selections=tuple(
            PatternSelection(
                slot=slot,
                pattern_id=pattern_id,
                version=1,
                parameters={},
                reason="Проверенный резервный паттерн",
            )
            for slot, pattern_id in _VERIFIED_FALLBACK_PATTERNS
        ),
        custom_escape=None,
        summary="Использована резервная проверенная композиция для продолжения сборки.",
    )


async def plan_composition(
    engine: CompositionPlannerEngine,
    request: BuilderRequest,
    selected_direction: DirectionProposal,
    registry: PatternRegistry,
) -> PlannedComposition:
    total_usage = TokenUsage()
    request_ids: list[str] = []
    diagnostic = ""
    for attempt in range(2):
        result = await engine.plan_composition(
            request=request,
            selected_direction=selected_direction,
            public_catalog=registry.public_catalog(),
            correction=diagnostic or None,
        )
        total_usage = total_usage + result.usage
        if result.provider_request_id is not None:
            request_ids.append(result.provider_request_id)
        try:
            if set(result.payload) != {
                "schema_version",
                "direction_id",
                "selections",
                "custom_escape",
                "summary",
            }:
                raise ValueError("top-level fields do not match the contract")
            plan = CompositionPlan.from_dict(result.payload)
            if plan.direction_id != selected_direction.proposal_id:
                raise ValueError("direction_id does not match the selected direction")
            resolved = resolve_composition(plan, registry)
        except (PatternResolutionError, TypeError, ValueError) as exc:
            diagnostic = str(exc)[:1_000]
            if attempt == 0:
                continue
            try:
                plan = _verified_fallback_plan(selected_direction)
                resolved = resolve_composition(plan, registry)
            except (PatternResolutionError, TypeError, ValueError) as fallback_exc:
                raise CompositionPlanningError(
                    f"composition plan is invalid: {diagnostic}",
                    usage=total_usage,
                ) from fallback_exc
            return PlannedComposition(
                plan=plan,
                resolved=resolved,
                usage=total_usage,
                provider_request_ids=tuple(request_ids),
            )
        return PlannedComposition(
            plan=plan,
            resolved=resolved,
            usage=total_usage,
            provider_request_ids=tuple(request_ids),
        )
    raise AssertionError("unreachable composition planning loop")
