from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from builder_lab.models import (
    BuilderRequest,
    DirectionProposal,
    DirectionRole,
    EngineName,
    TokenUsage,
)
from builder_lab.patterns.atomic_models import AtomicPatternCategory
from builder_lab.patterns.atomic_registry import (
    AtomicPatternRegistry,
    load_builtin_atomic_registry,
)
from builder_lab.patterns.candidate_planner import (
    PatternCandidate,
    PatternCandidateGroup,
    PatternCandidatePlan,
    PatternCandidatePlanResult,
    PatternCandidateValidationError,
    plan_pattern_candidates,
    validate_pattern_candidate_plan,
)
from builder_lab.prompts import (
    PATTERN_CANDIDATE_PLAN_JSON_SCHEMA,
    build_pattern_candidate_plan_prompt,
)


def builder_request(*, reference_context: str = "") -> BuilderRequest:
    return BuilderRequest(
        engine=EngineName.DIRECT,
        brief="Compact assistant widget for an architecture studio.",
        locale="en",
        reference_context=reference_context,
    )


def direction() -> DirectionProposal:
    return DirectionProposal(
        proposal_id="candidate-1",
        role=DirectionRole.INTERACTION_INVENTOR,
        title="Quiet technical note",
        art_direction="A restrained compact widget with a precise editorial rhythm.",
        interaction_model="The panel opens on demand and keeps the conversation legible.",
        safeguards=("No fake controls",),
    )


def approved_registry() -> AtomicPatternRegistry:
    return load_builtin_atomic_registry()


def catalog(registry: AtomicPatternRegistry | None = None):
    return (registry or approved_registry()).selector_catalog()


def plan_for(*groups: PatternCandidateGroup) -> PatternCandidatePlan:
    return PatternCandidatePlan(
        schema_version=2,
        direction_id="candidate-1",
        groups=tuple(groups),
        summary="Shortlist for the selected direction.",
    )


def candidate(category: AtomicPatternCategory, *, rank: int = 1) -> PatternCandidateGroup:
    pattern_id = f"{category.value.replace('_', '-')}-technical"
    return PatternCandidateGroup(
        category=category,
        candidates=(
            PatternCandidate(
                pattern_id=pattern_id,
                version=1,
                rank=rank,
                reason="Matches the requested direction and runtime contract.",
            ),
        ),
    )


def test_selector_prompt_contains_full_ai_description_but_no_assets() -> None:
    registry = approved_registry()
    prompt = build_pattern_candidate_plan_prompt(
        request=builder_request(),
        selected_direction=direction(),
        selector_catalog=catalog(registry),
        correction=None,
    )

    full_description = registry.resolve("widget-open-technical", 1).ai_description
    assert full_description in prompt
    assert "BEGIN fragment.html" not in prompt
    assert "<div class=" not in prompt
    assert "styles.css" not in prompt
    assert "behavior.js" not in prompt
    assert "implementation code" in prompt.lower()


def test_selector_prompt_includes_bounded_untrusted_reference_and_optional_categories() -> None:
    reference = "site-context-marker " + ("reference " * 700)
    prompt = build_pattern_candidate_plan_prompt(
        request=builder_request(reference_context=reference),
        selected_direction=direction(),
        selector_catalog=catalog(),
        optional_categories=(AtomicPatternCategory.WIDGET_OPEN,),
    )

    assert "UNTRUSTED_GROUNDED_REFERENCE_JSON" in prompt
    assert "site-context-marker" in prompt
    assert "widget_open" in prompt
    assert reference not in prompt


def test_schema_and_models_pin_schema_version_two() -> None:
    assert PATTERN_CANDIDATE_PLAN_JSON_SCHEMA["properties"]["schema_version"] == {
        "type": "integer",
        "enum": [2],
    }
    group = candidate(AtomicPatternCategory.WIDGET_OPEN)
    plan = plan_for(group)
    assert plan.schema_version == 2
    assert group.candidates[0].rank == 1

    with pytest.raises(ValueError, match="schema_version"):
        PatternCandidatePlan(
            schema_version=1,
            direction_id="candidate-1",
            groups=(group,),
            summary="invalid",
        )


def test_validation_rejects_unknown_version_and_category() -> None:
    registry = approved_registry()
    unknown_version = PatternCandidateGroup(
        category=AtomicPatternCategory.WIDGET_OPEN,
        candidates=(
            PatternCandidate(
                pattern_id="widget-open-technical",
                version=99,
                rank=1,
                reason="unknown",
            ),
        ),
    )
    with pytest.raises(PatternCandidateValidationError, match="unknown pattern"):
        validate_pattern_candidate_plan(
            plan_for(unknown_version),
            registry=registry,
            selector_catalog=tuple(
                item
                for item in catalog(registry)
                if item["category"] == "widget_open"
            ),
        )

    wrong_category = PatternCandidateGroup(
        category=AtomicPatternCategory.WIDGET_CLOSE,
        candidates=(
            PatternCandidate(
                pattern_id="widget-open-technical",
                version=1,
                rank=1,
                reason="wrong category",
            ),
        ),
    )
    with pytest.raises(PatternCandidateValidationError, match="category"):
        validate_pattern_candidate_plan(
            plan_for(wrong_category),
            registry=registry,
            selector_catalog=catalog(registry),
        )


def test_validation_rejects_lifecycle_and_review_state() -> None:
    registry = approved_registry()
    definition = registry.resolve("widget-open-technical", 1)
    draft_registry = AtomicPatternRegistry((replace(definition, status=type(definition.status).DRAFT),))
    group = candidate(AtomicPatternCategory.WIDGET_OPEN)
    with pytest.raises(PatternCandidateValidationError, match="active"):
        validate_pattern_candidate_plan(
            plan_for(group),
            registry=draft_registry,
            selector_catalog=(definition.selector_dict(),),
        )

    rejected_catalog = (replace(definition, provenance={"origin": "test", "review_state": "rejected"}).selector_dict(),)
    with pytest.raises(PatternCandidateValidationError, match="approved"):
        validate_pattern_candidate_plan(
            plan_for(group),
            registry=registry,
            selector_catalog=rejected_catalog,
        )


def test_validation_rejects_duplicates_and_noncanonical_rank() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        PatternCandidateGroup(
            category=AtomicPatternCategory.WIDGET_OPEN,
            candidates=(
                PatternCandidate("widget-open-technical", 1, 1, "first"),
                PatternCandidate("widget-open-technical", 1, 2, "duplicate"),
            ),
        )


def test_validation_requires_two_to_five_candidates_when_two_are_eligible() -> None:
    original = approved_registry()
    definition = original.resolve("widget-open-technical", 1)
    second = replace(definition, version=2)
    registry = AtomicPatternRegistry(tuple(original.definitions) + (second,))
    metadata = tuple(
        item
        for item in registry.selector_catalog()
        if item["category"] == AtomicPatternCategory.WIDGET_OPEN.value
    )
    accepted = plan_for(
        PatternCandidateGroup(
            category=AtomicPatternCategory.WIDGET_OPEN,
            candidates=(
                PatternCandidate(definition.pattern_id, 1, 1, "first"),
                PatternCandidate(second.pattern_id, 2, 2, "second"),
            ),
        )
    )
    assert validate_pattern_candidate_plan(
        accepted,
        registry=registry,
        selector_catalog=metadata,
    ) == accepted

    with pytest.raises(PatternCandidateValidationError, match="two to five"):
        validate_pattern_candidate_plan(
            plan_for(
                PatternCandidateGroup(
                    category=AtomicPatternCategory.WIDGET_OPEN,
                    candidates=(PatternCandidate(definition.pattern_id, 1, 1, "only"),),
                )
            ),
            registry=registry,
            selector_catalog=metadata,
        )


def test_validation_rejects_direction_mismatch_and_plan_duplicate_categories() -> None:
    registry = approved_registry()
    plan = plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN))
    with pytest.raises(PatternCandidateValidationError, match="direction_id"):
        validate_pattern_candidate_plan(
            plan,
            registry=registry,
            selector_catalog=catalog(registry),
            expected_direction_id="candidate-2",
        )
    with pytest.raises(ValueError, match="duplicate categories"):
        PatternCandidatePlan(
            schema_version=2,
            direction_id="candidate-1",
            groups=(plan.groups[0], plan.groups[0]),
            summary="duplicate",
        )

    with pytest.raises(ValueError, match="rank"):
        PatternCandidateGroup(
            category=AtomicPatternCategory.WIDGET_OPEN,
            candidates=(PatternCandidate("widget-open-technical", 1, 2, "bad rank"),),
        )


def test_validation_rejects_asymmetric_incompatibility() -> None:
    registry = approved_registry()
    first = registry.resolve("widget-open-technical", 1)
    second = registry.resolve("widget-close-technical", 1)
    incompatible_registry = AtomicPatternRegistry(
        (
            replace(first, incompatible_with=(second.pattern_id,)),
            replace(second, incompatible_with=(first.pattern_id,)),
        )
    )
    plan = plan_for(
        PatternCandidateGroup(
            category=AtomicPatternCategory.WIDGET_OPEN,
            candidates=(PatternCandidate(first.pattern_id, 1, 1, "open"),),
        ),
        PatternCandidateGroup(
            category=AtomicPatternCategory.WIDGET_CLOSE,
            candidates=(PatternCandidate(second.pattern_id, 1, 1, "close"),),
        ),
    )
    with pytest.raises(PatternCandidateValidationError, match="incompatible"):
        validate_pattern_candidate_plan(
            plan,
            registry=incompatible_registry,
            selector_catalog=tuple(item.selector_dict() for item in incompatible_registry.definitions),
        )


def test_validation_rejects_omitted_eligible_and_declared_optional_categories() -> None:
    registry = approved_registry()
    subset = tuple(
        item
        for item in catalog(registry)
        if item["category"] in {"widget_open", "widget_close"}
    )
    with pytest.raises(PatternCandidateValidationError, match="categories"):
        validate_pattern_candidate_plan(
            plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN)),
            registry=registry,
            selector_catalog=subset,
        )
    with pytest.raises(PatternCandidateValidationError, match="categories"):
        validate_pattern_candidate_plan(
            plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN)),
            registry=registry,
            selector_catalog=(
                next(item for item in subset if item["category"] == "widget_open"),
            ),
            optional_categories=(AtomicPatternCategory.WIDGET_CLOSE,),
        )


class FakeSelectorEngine:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    async def plan_pattern_candidates(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return PatternCandidatePlanResult(
            plan=payload,
            usage=SimpleNamespace(),
            provider_request_id=f"selector-{len(self.calls)}",
        )


def valid_payload(registry: AtomicPatternRegistry | None = None):
    registry = registry or approved_registry()
    by_category: dict[AtomicPatternCategory, list] = {}
    for item in registry.definitions:
        by_category.setdefault(item.category, []).append(item)
    return plan_for(
        *(
            PatternCandidateGroup(
                category=category,
                candidates=tuple(
                    PatternCandidate(
                        pattern_id=item.pattern_id,
                        version=item.version,
                        rank=rank,
                        reason="Matches the requested direction and runtime contract.",
                    )
                    for rank, item in enumerate(items[:5], start=1)
                ),
            )
            for category, items in sorted(by_category.items(), key=lambda pair: pair[0].value)
        )
    )


@pytest.mark.asyncio
async def test_repeated_invalid_selector_output_uses_deterministic_fallback() -> None:
    registry = approved_registry()
    invalid = plan_for(
        PatternCandidateGroup(
            category=AtomicPatternCategory.WIDGET_OPEN,
            candidates=(PatternCandidate("does-not-exist", 1, 1, "bad"),),
        )
    )
    engine = FakeSelectorEngine([invalid, invalid])

    result = await plan_pattern_candidates(
        engine,
        request=builder_request(),
        selected_direction=direction(),
        registry=registry,
    )

    assert result.used_fallback is True
    assert result.plan.schema_version == 2
    assert all(1 <= len(group.candidates) <= 5 for group in result.plan.groups)
    assert len(engine.calls) == 2
    assert engine.calls[1]["correction"]
    assert len(engine.calls[1]["correction"]) <= 1200


@pytest.mark.asyncio
async def test_fallback_is_sorted_compatible_and_server_validated() -> None:
    original = approved_registry()
    first = original.resolve("widget-open-technical", 1)
    close = original.resolve("widget-close-technical", 1)
    open_v2 = replace(
        first,
        pattern_id="widget-open-alt-technical",
        incompatible_with=(),
    )
    open_v3 = replace(
        first,
        pattern_id="widget-open-alt-two-technical",
        incompatible_with=(),
    )
    fallback_registry = AtomicPatternRegistry(
        tuple(
            replace(item, incompatible_with=(close.pattern_id,))
            if item.pattern_id == first.pattern_id
            else replace(item, incompatible_with=(first.pattern_id,))
            if item.pattern_id == close.pattern_id
            else item
            for item in original.definitions
        )
        + (open_v2, open_v3)
    )
    engine = FakeSelectorEngine([
        plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN)),
        plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN)),
    ])

    result = await plan_pattern_candidates(
        engine,
        request=builder_request(),
        selected_direction=direction(),
        registry=fallback_registry,
    )

    assert result.used_fallback is True
    validate_pattern_candidate_plan(
        result.plan,
        registry=fallback_registry,
        selector_catalog=fallback_registry.selector_catalog(),
    )
    selected = {
        item.pattern_id
        for group in result.plan.groups
        for item in group.candidates
    }
    assert not {first.pattern_id, close.pattern_id} <= selected


@pytest.mark.asyncio
async def test_fallback_backtracks_earlier_category_to_preserve_later_required_group() -> None:
    original = approved_registry()
    open_definition = original.resolve("widget-open-technical", 1)
    close_definition = original.resolve("widget-close-technical", 1)
    retained = tuple(
        item
        for item in original.definitions
        if item.pattern_id not in {open_definition.pattern_id, close_definition.pattern_id}
    )
    close_one = replace(
        close_definition,
        pattern_id="widget-close-001",
        incompatible_with=("widget-open-001", "widget-open-002"),
    )
    close_two = replace(close_definition, pattern_id="widget-close-002", incompatible_with=())
    close_three = replace(close_definition, pattern_id="widget-close-003", incompatible_with=())
    open_one = replace(
        open_definition,
        pattern_id="widget-open-001",
        incompatible_with=("widget-close-001",),
    )
    open_two = replace(
        open_definition,
        pattern_id="widget-open-002",
        incompatible_with=("widget-close-001",),
    )
    registry = AtomicPatternRegistry(
        retained + (close_one, close_two, close_three, open_one, open_two)
    )

    result = await plan_pattern_candidates(
        FakeSelectorEngine([
            plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN)),
            plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN)),
        ]),
        request=builder_request(),
        selected_direction=direction(),
        registry=registry,
    )

    assert result.used_fallback is True
    validate_pattern_candidate_plan(
        result.plan,
        registry=registry,
        selector_catalog=registry.selector_catalog(),
    )
    groups = {group.category: group for group in result.plan.groups}
    assert [item.pattern_id for item in groups[AtomicPatternCategory.WIDGET_CLOSE].candidates] == [
        "widget-close-002",
        "widget-close-003",
    ]
    assert [item.pattern_id for item in groups[AtomicPatternCategory.WIDGET_OPEN].candidates] == [
        "widget-open-001",
        "widget-open-002",
    ]


@pytest.mark.asyncio
async def test_effective_approved_catalog_is_filtered_before_selector_call() -> None:
    registry = approved_registry()
    filtered_key = ("widget-open-technical", 1)
    engine = FakeSelectorEngine([plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN))])

    await plan_pattern_candidates(
        engine,
        request=builder_request(),
        selected_direction=direction(),
        registry=registry,
        selector_catalog=catalog(registry),
        effective_approved={filtered_key},
    )

    sent_ids = {(item["pattern_id"], item["version"]) for item in engine.calls[0]["selector_catalog"]}
    assert sent_ids == {filtered_key}


class RaisingSelectorEngine:
    def __init__(self):
        self.calls = 0

    async def plan_pattern_candidates(self, **kwargs):
        self.calls += 1
        raise RuntimeError("selector unavailable")


@pytest.mark.asyncio
async def test_selector_exception_retries_once_then_uses_fallback() -> None:
    engine = RaisingSelectorEngine()
    result = await plan_pattern_candidates(
        engine,
        request=builder_request(),
        selected_direction=direction(),
        registry=approved_registry(),
    )
    assert engine.calls == 2
    assert result.used_fallback is True
    assert result.plan.groups


@pytest.mark.asyncio
async def test_empty_catalog_fallback_does_not_invent_non_optional_category() -> None:
    invalid = plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN))
    result = await plan_pattern_candidates(
        FakeSelectorEngine([invalid, invalid]),
        request=builder_request(),
        selected_direction=direction(),
        registry=approved_registry(),
        selector_catalog=(),
        optional_categories=(),
    )
    assert result.used_fallback is True
    assert result.plan.groups == ()


@pytest.mark.asyncio
async def test_invalid_structured_response_preserves_each_provider_request_id() -> None:
    from builder_lab.engines.gemini_direct import GeminiDirectEngine

    class Router:
        def __init__(self):
            self.calls = 0

        async def generate(self, **kwargs):
            self.calls += 1
            return SimpleNamespace(
                parsed={"schema_version": 1},
                text="",
                response_id=f"invalid-{self.calls}",
                usage_metadata=None,
                model_version="gemini-3.6-flash",
            )

    router = Router()
    engine = GeminiDirectEngine(
        model_router=router,
        routing_role="selector-role",
        run_id="run-1",
    )
    result = await plan_pattern_candidates(
        engine,
        request=builder_request(),
        selected_direction=direction(),
        registry=approved_registry(),
    )
    assert result.used_fallback is True
    assert result.provider_request_ids == ("invalid-1", "invalid-2")


@pytest.mark.asyncio
async def test_retry_aggregates_usage_and_provider_request_ids() -> None:
    registry = approved_registry()
    invalid = plan_for(
        PatternCandidateGroup(
            category=AtomicPatternCategory.WIDGET_OPEN,
            candidates=(PatternCandidate("does-not-exist", 1, 1, "bad"),),
        )
    )
    valid = valid_payload(registry)

    class UsageEngine:
        def __init__(self):
            self.calls = 0

        async def plan_pattern_candidates(self, **kwargs):
            self.calls += 1
            plan = invalid if self.calls == 1 else valid
            return PatternCandidatePlanResult(
                plan=plan,
                usage=TokenUsage(prompt_tokens=10, output_tokens=4, thinking_tokens=1),
                provider_request_id=f"provider-{self.calls}",
            )

    result = await plan_pattern_candidates(
        UsageEngine(),
        request=builder_request(),
        selected_direction=direction(),
        registry=registry,
    )
    assert result.used_fallback is False
    assert result.usage == TokenUsage(prompt_tokens=20, output_tokens=8, thinking_tokens=2)
    assert result.provider_request_ids == ("provider-1", "provider-2")


@pytest.mark.asyncio
async def test_selector_returns_one_candidate_when_only_one_is_eligible() -> None:
    registry = approved_registry()
    selector_catalog = tuple(
        item for item in catalog(registry) if item["category"] == "widget_open"
    )
    plan = plan_for(candidate(AtomicPatternCategory.WIDGET_OPEN))
    engine = FakeSelectorEngine([plan])
    result = await plan_pattern_candidates(
        engine,
        request=builder_request(),
        selected_direction=direction(),
        registry=registry,
        selector_catalog=selector_catalog,
    )
    assert result.used_fallback is False
    assert len(engine.calls) == 1
    assert len(result.plan.groups) == 1
    assert len(result.plan.groups[0].candidates) == 1


@pytest.mark.asyncio
async def test_optional_category_without_eligible_candidates_may_be_empty() -> None:
    registry = approved_registry()
    plan = plan_for(
        PatternCandidateGroup(category=AtomicPatternCategory.WIDGET_OPEN, candidates=()),
    )
    result = await plan_pattern_candidates(
        FakeSelectorEngine([plan]),
        request=builder_request(),
        selected_direction=direction(),
        registry=registry,
        selector_catalog=(),
        optional_categories={AtomicPatternCategory.WIDGET_OPEN},
    )
    assert result.plan.groups[0].candidates == ()
    assert result.used_fallback is False
    assert result.plan.groups[0].category is AtomicPatternCategory.WIDGET_OPEN


@pytest.mark.asyncio
async def test_direct_engine_structured_method_uses_pattern_operation_and_router_context() -> None:
    from builder_lab.engines.gemini_direct import GeminiDirectEngine

    response = SimpleNamespace(
        parsed={
            "schema_version": 2,
            "direction_id": "candidate-1",
            "groups": [
                {
                    "category": "widget_open",
                    "candidates": [
                        {
                            "pattern_id": "widget-open-technical",
                            "version": 1,
                            "rank": 1,
                            "reason": "ok",
                        }
                    ],
                }
            ],
            "summary": "ok",
        },
        text="",
        response_id="pattern-request-1",
        usage_metadata=None,
        model_version="gemini-3.6-flash",
    )
    calls = []

    class Router:
        async def generate(self, **kwargs):
            calls.append(kwargs)
            return response

    engine = GeminiDirectEngine(
        model_router=Router(),
        routing_role="selector-role",
        run_id="run-1",
    )
    result = await engine.plan_pattern_candidates(
        request=builder_request(),
        selected_direction=direction(),
        selector_catalog=catalog(),
        optional_categories=("widget_open",),
    )
    assert result.plan.schema_version == 2
    assert calls[0]["request"].temperature == 0.2
    assert calls[0]["context"].operation == "pattern_candidate_plan"
    assert result.provider_request_id == "pattern-request-1"
    assert "widget_open" in calls[0]["request"].prompt
