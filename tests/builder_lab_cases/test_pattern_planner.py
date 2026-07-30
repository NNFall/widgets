from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

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
from builder_lab.patterns.registry import PatternRegistry, load_builtin_registry
from builder_lab.prompts import (
    COMPOSITION_PLAN_JSON_SCHEMA,
    build_composition_plan_prompt,
)

from ._pattern_source_fixtures import source_registry_and_plan


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


def source_registry_and_payload(tmp_path) -> tuple[PatternRegistry, dict[str, object]]:
    registry, plan = source_registry_and_plan(tmp_path)
    return registry, replace(plan, direction_id="candidate-2").to_dict()


def legacy_payload() -> dict[str, object]:
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
                "reason": "Историческая версия",
            }
            for category, pattern_id in pattern_ids.items()
        ],
        "custom_escape": None,
        "summary": "Историческая prompt-only композиция",
    }


@dataclass
class FakeEngine:
    plan_results: list[dict[str, object]]
    plan_attempts: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def plan_composition(self, **kwargs) -> CompositionPlanResult:
        self.calls.append(kwargs)
        payload = self.plan_results[self.plan_attempts]
        self.plan_attempts += 1
        return CompositionPlanResult(
            payload=payload,
            usage=TokenUsage(prompt_tokens=10, output_tokens=4),
            provider_request_id=f"plan-{self.plan_attempts}",
        )


@pytest.mark.asyncio
async def test_planner_repairs_invalid_model_contract_once(tmp_path) -> None:
    registry, payload = source_registry_and_payload(tmp_path)
    engine = FakeEngine([{"selections": []}, payload])

    result = await plan_composition(
        engine,
        request(),
        direction(),
        registry,
    )

    assert result.plan.to_dict() == payload
    assert result.resolved.source is not None
    assert result.usage == TokenUsage(prompt_tokens=20, output_tokens=8)
    assert result.provider_request_ids == ("plan-1", "plan-2")
    assert engine.plan_attempts == 2


@pytest.mark.asyncio
async def test_planner_fails_closed_after_one_contract_repair(tmp_path) -> None:
    registry, _ = source_registry_and_payload(tmp_path)
    engine = FakeEngine([{"selections": []}, {"selections": []}])

    with pytest.raises(ValueError, match="composition plan"):
        await plan_composition(
            engine,
            request(),
            direction(),
            registry,
        )

    assert engine.plan_attempts == 2


@pytest.mark.asyncio
async def test_planner_receives_only_selectable_runtime_source_catalog(tmp_path) -> None:
    registry, payload = source_registry_and_payload(tmp_path)
    engine = FakeEngine([payload])

    await plan_composition(engine, request(), direction(), registry)

    assert registry.public_catalog() == ()
    assert engine.calls[0]["public_catalog"] == registry.planner_catalog()
    assert len(engine.calls[0]["public_catalog"]) == 5


@pytest.mark.asyncio
async def test_planner_never_enables_explicit_legacy_resume(tmp_path) -> None:
    source_registry, _ = source_registry_and_payload(tmp_path)
    registry = PatternRegistry(
        source_registry.definitions + load_builtin_registry().definitions
    )
    engine = FakeEngine([legacy_payload(), legacy_payload()])

    with pytest.raises(ValueError, match="composition plan"):
        await plan_composition(engine, request(), direction(), registry)

    assert engine.calls[0]["public_catalog"] == source_registry.planner_catalog()
    assert "legacy_reference" in engine.calls[1]["correction"]


def test_builtin_planner_catalog_is_empty_until_safe_assets_exist() -> None:
    assert load_builtin_registry().planner_catalog() == ()


def test_planner_prompt_exposes_source_catalog_metadata_but_not_assets(tmp_path) -> None:
    registry, _ = source_registry_and_payload(tmp_path)

    prompt = build_composition_plan_prompt(
        request=request(),
        selected_direction=direction(),
        public_catalog=registry.planner_catalog(),
        correction="required slots are missing",
    )

    assert "candidate-2" in prompt
    assert "test-launcher" in prompt
    assert "required slots are missing" in prompt
    assert "Каталог уже отфильтрован сервером" in prompt
    assert "только `runtime_source`" in prompt
    assert "reason и summary" in prompt
    assert "fragment.html" not in prompt
    assert set(COMPOSITION_PLAN_JSON_SCHEMA["required"]) == {
        "schema_version",
        "direction_id",
        "selections",
        "custom_escape",
        "summary",
    }
    assert COMPOSITION_PLAN_JSON_SCHEMA["properties"]["selections"]["minItems"] == 5
    assert COMPOSITION_PLAN_JSON_SCHEMA["properties"]["selections"]["maxItems"] == 5
    assert COMPOSITION_PLAN_JSON_SCHEMA["properties"]["custom_escape"] == {
        "type": "null"
    }
