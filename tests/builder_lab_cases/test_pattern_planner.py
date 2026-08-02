from __future__ import annotations

from dataclasses import dataclass

import pytest

from builder_lab.engines.base import CompositionPlanResult
from builder_lab.models import (
    BuilderRequest,
    DirectionProposal,
    DirectionRole,
    EngineName,
    TokenUsage,
)
from builder_lab.patterns.models import PatternCategory
from builder_lab.patterns.planner import plan_composition
from builder_lab.patterns.registry import load_builtin_registry
from builder_lab.prompts import (
    COMPOSITION_PLAN_JSON_SCHEMA,
    build_composition_plan_prompt,
)


def request() -> BuilderRequest:
    return BuilderRequest(engine=EngineName.DIRECT, brief="Собери AI-консультанта")


def direction() -> DirectionProposal:
    return DirectionProposal(
        proposal_id="candidate-2",
        role=DirectionRole.INTERACTION_INVENTOR,
        title="Спокойный эксперт",
        art_direction="Тёплая компактная карточка с ясным разговором.",
        interaction_model="Кнопка раскрывает короткий двухсторонний чат.",
        safeguards=("Не перекрывать контент сайта",),
    )


def complete_payload() -> dict[str, object]:
    pattern_ids = {
        PatternCategory.LAUNCHER: "orb-pulse",
        PatternCategory.SHELL: "compact-chat",
        PatternCategory.MESSAGES: "paired-bubbles",
        PatternCategory.COMPOSER: "single-line-pill",
        PatternCategory.MOTION: "spring-reveal",
    }
    return {
        "schema_version": 1,
        "direction_id": "candidate-2",
        "selections": [
            {
                "slot": category.value,
                "pattern_id": pattern_id,
                "version": 1,
                "parameters": {},
                "reason": "Поддерживает выбранное направление",
            }
            for category, pattern_id in pattern_ids.items()
        ],
        "custom_escape": None,
        "summary": "Компактный брендовый консультант",
    }


@dataclass
class FakeEngine:
    plan_results: list[dict[str, object]]
    plan_attempts: int = 0

    async def plan_composition(self, **kwargs) -> CompositionPlanResult:
        payload = self.plan_results[self.plan_attempts]
        self.plan_attempts += 1
        return CompositionPlanResult(
            payload=payload,
            usage=TokenUsage(prompt_tokens=10, output_tokens=4),
            provider_request_id=f"plan-{self.plan_attempts}",
        )


@pytest.mark.asyncio
async def test_planner_repairs_invalid_model_contract_once() -> None:
    engine = FakeEngine([{"selections": []}, complete_payload()])

    result = await plan_composition(
        engine,
        request(),
        direction(),
        load_builtin_registry(),
    )

    assert result.plan.to_dict() == complete_payload()
    assert result.resolved.pattern_ids[0] == "orb-pulse"
    assert result.usage == TokenUsage(prompt_tokens=20, output_tokens=8)
    assert result.provider_request_ids == ("plan-1", "plan-2")
    assert engine.plan_attempts == 2


@pytest.mark.asyncio
async def test_planner_uses_verified_fallback_after_one_contract_repair() -> None:
    engine = FakeEngine([{"selections": []}, {"selections": []}])

    result = await plan_composition(
        engine,
        request(),
        direction(),
        load_builtin_registry(),
    )

    assert engine.plan_attempts == 2
    assert result.resolved.pattern_ids == (
        "orb-pulse",
        "compact-chat",
        "paired-bubbles",
        "single-line-pill",
        "spring-reveal",
    )
    assert result.plan.direction_id == "candidate-2"
    assert result.usage == TokenUsage(prompt_tokens=20, output_tokens=8)
    assert result.provider_request_ids == ("plan-1", "plan-2")
    assert "резерв" in result.plan.summary.lower()


def test_planner_prompt_exposes_catalog_metadata_but_not_assets() -> None:
    registry = load_builtin_registry()

    prompt = build_composition_plan_prompt(
        request=request(),
        selected_direction=direction(),
        public_catalog=registry.public_catalog(),
        correction="required slots are missing",
    )

    assert "candidate-2" in prompt
    assert "orb-pulse" in prompt
    assert "required slots are missing" in prompt
    assert "fragment.html" not in prompt
    assert set(COMPOSITION_PLAN_JSON_SCHEMA["required"]) == {
        "schema_version",
        "direction_id",
        "selections",
        "custom_escape",
        "summary",
    }
