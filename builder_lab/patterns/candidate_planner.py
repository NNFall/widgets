"""AI shortlist selection and server-side validation for atomic patterns.

The selector is deliberately untrusted.  It receives metadata only; every
exact version and every relationship is resolved and checked by this module
before the resulting plan can be handed to a generation stage.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from itertools import combinations
from typing import Any

from ..engines.base import PatternCandidatePlanResult
from ..models import BuilderRequest, DirectionProposal, TokenUsage
from ..prompts import build_pattern_candidate_plan_prompt
from .atomic_models import (
    AtomicPatternCategory,
    AtomicPatternDefinition,
    AtomicPatternStatus,
    PatternCandidate,
    PatternCandidateGroup,
    PatternCandidatePlan,
)
from .atomic_registry import AtomicPatternRegistry, AtomicPatternRegistryError


class PatternCandidateValidationError(ValueError):
    """A selector response violates the server-owned shortlist contract."""


_MAX_CORRECTION_DIAGNOSTIC = 1_200
_MAX_FALLBACK_SEARCH_NODES = 4_096
_MAX_FALLBACK_COMBINATIONS_PER_CATEGORY = 4_096


def _bounded_diagnostic(value: object) -> str:
    text = " ".join(str(value).split())
    return text[:_MAX_CORRECTION_DIAGNOSTIC] or "selector response was invalid"


def _candidate_plan(value: PatternCandidatePlan | Mapping[str, object]) -> PatternCandidatePlan:
    if isinstance(value, PatternCandidatePlan):
        return value
    if isinstance(value, Mapping):
        try:
            return PatternCandidatePlan.from_dict(value)
        except (TypeError, ValueError) as exc:
            raise PatternCandidateValidationError(str(exc)) from exc
    raise PatternCandidateValidationError("selector did not return a candidate plan")


def _normalise_optional_categories(
    categories: Iterable[AtomicPatternCategory | str],
) -> frozenset[AtomicPatternCategory]:
    result: set[AtomicPatternCategory] = set()
    for category in categories:
        try:
            result.add(
                category
                if isinstance(category, AtomicPatternCategory)
                else AtomicPatternCategory(category)
            )
        except (TypeError, ValueError) as exc:
            raise PatternCandidateValidationError("category is invalid") from exc
    return frozenset(result)


def _normalise_effective_approved(
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None,
) -> frozenset[tuple[str, int]] | None:
    if effective_approved is None:
        return None
    if isinstance(effective_approved, Mapping):
        return frozenset(
            key
            for key, state in effective_approved.items()
            if state is True or state == "approved"
        )
    return frozenset(effective_approved)


def _catalog_metadata(
    selector_catalog: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, int], Mapping[str, Any]]:
    metadata: dict[tuple[str, int], Mapping[str, Any]] = {}
    for item in selector_catalog:
        if not isinstance(item, Mapping):
            raise PatternCandidateValidationError("selector catalog contains an invalid item")
        pattern_id = item.get("pattern_id")
        version = item.get("version")
        if not isinstance(pattern_id, str) or isinstance(version, bool) or not isinstance(version, int):
            raise PatternCandidateValidationError("selector catalog contains an invalid exact version")
        key = (pattern_id, version)
        if key in metadata:
            raise PatternCandidateValidationError("selector catalog contains duplicate versions")
        metadata[key] = item
    return metadata


def _metadata_review_state(item: Mapping[str, Any]) -> object:
    provenance = item.get("provenance")
    if isinstance(provenance, Mapping):
        return provenance.get("review_state")
    return item.get("review_state")


def _filter_selector_catalog(
    *,
    registry: AtomicPatternRegistry,
    selector_catalog: Sequence[Mapping[str, Any]],
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None,
) -> tuple[Mapping[str, Any], ...]:
    """Keep only exact active/effectively-approved metadata for the selector."""

    return _eligible_catalog(
        registry=registry,
        selector_catalog=selector_catalog,
        effective_approved=effective_approved,
    )


def _eligible_catalog(
    *,
    registry: AtomicPatternRegistry,
    selector_catalog: Sequence[Mapping[str, Any]],
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None,
) -> tuple[Mapping[str, Any], ...]:
    metadata = _catalog_metadata(selector_catalog)
    approved_override = _normalise_effective_approved(effective_approved)
    eligible: list[Mapping[str, Any]] = []
    for key, item in metadata.items():
        if approved_override is None:
            if item.get("status") != AtomicPatternStatus.ACTIVE.value:
                continue
            if _metadata_review_state(item) != "approved":
                continue
        elif key not in approved_override:
            continue
        try:
            definition = registry.resolve(*key)
        except AtomicPatternRegistryError:
            continue
        if definition.status is not AtomicPatternStatus.ACTIVE:
            continue
        category = item.get("category")
        if category != definition.category.value:
            continue
        # The caller-owned catalog is only an exact-version allowlist plus
        # effective review state.  Never forward its mutable metadata: a
        # stale or compromised caller could otherwise inject assets, secrets,
        # or a forged selector description into the provider prompt.
        eligible.append(definition.selector_dict())
    return tuple(
        sorted(
            eligible,
            key=lambda item: (
                str(item.get("category", "")),
                str(item.get("pattern_id", "")),
                int(item.get("version", 0)),
            ),
        )
    )


def _expected_direction_id(
    *,
    expected_direction_id: str | None,
    selected_direction: DirectionProposal | str | None,
) -> str | None:
    if expected_direction_id is not None:
        return expected_direction_id
    if isinstance(selected_direction, DirectionProposal):
        return selected_direction.proposal_id
    return selected_direction


def validate_pattern_candidate_plan(
    plan: PatternCandidatePlan | Mapping[str, object],
    *,
    registry: AtomicPatternRegistry,
    selector_catalog: Sequence[Mapping[str, Any]],
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None = None,
    optional_categories: Iterable[AtomicPatternCategory | str] = (),
    expected_direction_id: str | None = None,
    selected_direction: DirectionProposal | str | None = None,
) -> PatternCandidatePlan:
    """Resolve and validate a selector response against the immutable catalog."""

    parsed = _candidate_plan(plan)
    expected = _expected_direction_id(
        expected_direction_id=expected_direction_id,
        selected_direction=selected_direction,
    )
    if expected is not None and parsed.direction_id != expected:
        raise PatternCandidateValidationError("direction_id does not match selected direction")

    optional = _normalise_optional_categories(optional_categories)
    metadata = _catalog_metadata(selector_catalog)
    approved_override = _normalise_effective_approved(effective_approved)
    eligible = _eligible_catalog(
        registry=registry,
        selector_catalog=selector_catalog,
        effective_approved=effective_approved,
    )
    eligible_by_category: dict[AtomicPatternCategory, tuple[Mapping[str, Any], ...]] = {}
    for item in eligible:
        try:
            category = AtomicPatternCategory(item["category"])
        except (TypeError, ValueError) as exc:
            raise PatternCandidateValidationError("selector catalog contains an unknown category") from exc
        eligible_by_category.setdefault(category, tuple())
        eligible_by_category[category] = eligible_by_category[category] + (item,)

    selected_definitions: dict[tuple[str, int], AtomicPatternDefinition] = {}
    for group in parsed.groups:
        # Resolve the submitted exact versions before cardinality checks.  This
        # keeps diagnostics actionable for unknown versions, stale lifecycle
        # records, and review drift even when the category has no currently
        # eligible entries.
        seen: set[tuple[str, int]] = set()
        for candidate in group.candidates:
            key = (candidate.pattern_id, candidate.version)
            if key in seen:
                raise PatternCandidateValidationError("candidate group contains duplicate candidates")
            seen.add(key)
            try:
                definition = registry.resolve(*key)
            except AtomicPatternRegistryError as exc:
                raise PatternCandidateValidationError(str(exc)) from exc
            if definition.status is not AtomicPatternStatus.ACTIVE:
                raise PatternCandidateValidationError("candidate lifecycle is not active")
            if definition.category is not group.category:
                raise PatternCandidateValidationError("candidate category does not match its group")
            item = metadata.get(key)
            if item is None:
                raise PatternCandidateValidationError(
                    f"candidate {candidate.pattern_id}@{candidate.version} is not in the approved selector catalog"
                )
            if item.get("status") != AtomicPatternStatus.ACTIVE.value:
                raise PatternCandidateValidationError("candidate lifecycle is not active")
            if approved_override is None and _metadata_review_state(item) != "approved":
                raise PatternCandidateValidationError("candidate is not effectively approved")
            if approved_override is not None and key not in approved_override:
                raise PatternCandidateValidationError("candidate is not effectively approved")
            selected_definitions[key] = definition

        available = eligible_by_category.get(group.category, ())
        if not available:
            if group.candidates:
                raise PatternCandidateValidationError(
                    f"category {group.category.value} has no eligible candidates"
                )
            if group.category not in optional:
                raise PatternCandidateValidationError(
                    f"category {group.category.value} is not optional"
                )
            continue

        expected_count = 1 if len(available) == 1 else min(5, len(available))
        if len(available) >= 2:
            if not 2 <= len(group.candidates) <= 5:
                raise PatternCandidateValidationError(
                    f"category {group.category.value} requires two to five candidates"
                )
        elif len(group.candidates) != expected_count:
            raise PatternCandidateValidationError(
                f"category {group.category.value} requires exactly one candidate"
            )

        for candidate in group.candidates:
            key = (candidate.pattern_id, candidate.version)
            if key not in selected_definitions:
                raise PatternCandidateValidationError("candidate could not be resolved")

    declared_categories = {group.category for group in parsed.groups}
    expected_categories = set(eligible_by_category) | set(optional)
    if declared_categories != expected_categories:
        missing = sorted(
            category.value for category in expected_categories - declared_categories
        )
        extra = sorted(
            category.value for category in declared_categories - expected_categories
        )
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("unexpected=" + ",".join(extra))
        raise PatternCandidateValidationError(
            "plan categories do not match eligible categories (" + "; ".join(details) + ")"
        )

    # The model enforces canonical ranks, but retaining this explicit check
    # protects callers that pass a hand-built object from a different version.
    for group in parsed.groups:
        if tuple(item.rank for item in group.candidates) != tuple(
            range(1, len(group.candidates) + 1)
        ):
            raise PatternCandidateValidationError("candidate ranks must be canonical")

    selected_by_pattern = {
        definition.pattern_id: definition
        for definition in selected_definitions.values()
    }
    for definition in selected_definitions.values():
        for incompatible_id in definition.incompatible_with:
            other = selected_by_pattern.get(incompatible_id)
            if other is None:
                continue
            raise PatternCandidateValidationError(
                "selected candidates are incompatible"
            )
    return parsed


def _definitions_are_compatible(
    candidates: Sequence[AtomicPatternDefinition],
) -> bool:
    """Return whether a set of definitions has no declared incompatibility."""

    for index, definition in enumerate(candidates):
        for other in candidates[index + 1 :]:
            if (
                other.pattern_id in definition.incompatible_with
                or definition.pattern_id in other.incompatible_with
            ):
                return False
    return True


def _fallback_options(
    entries: Sequence[AtomicPatternDefinition],
) -> tuple[tuple[AtomicPatternDefinition, ...], ...]:
    """Enumerate stable, bounded compatible minimum subsets for one category.

    Feasibility is established at the contract minimum first: one candidate
    when exactly one definition is eligible, otherwise two.  Optional
    maximization happens only after every category has a valid minimum set, so
    a large catalog cannot spend the search budget on five-way combinations
    and hide a feasible pair.
    """

    if not entries:
        return ((),)
    if len(entries) == 1:
        return ((entries[0],),)

    ordered = tuple(
        sorted(entries, key=lambda item: (item.pattern_id, item.version))
    )
    minimum = 2
    options: list[tuple[AtomicPatternDefinition, ...]] = []
    for inspected, indexes in enumerate(
        combinations(range(len(ordered)), minimum),
        start=1,
    ):
        if inspected > _MAX_FALLBACK_COMBINATIONS_PER_CATEGORY:
            break
        candidate_set = tuple(ordered[index] for index in indexes)
        if _definitions_are_compatible(candidate_set):
            options.append(candidate_set)
    return tuple(options)


def _solve_fallback_assignment(
    grouped: Mapping[
        AtomicPatternCategory,
        Sequence[AtomicPatternDefinition],
    ],
) -> dict[AtomicPatternCategory, tuple[AtomicPatternDefinition, ...]] | None:
    """Find a deterministic compatible assignment across all categories.

    Greedily filling one category can consume the only compatible candidates
    for a later required category.  This bounded depth-first search keeps the
    stable sorted preference while backtracking across category boundaries.
    """

    categories = tuple(sorted(grouped, key=lambda value: value.value))
    options = {
        category: _fallback_options(grouped[category])
        for category in categories
    }
    if any(not options[category] for category in categories):
        return None

    nodes = 0
    assignment: dict[AtomicPatternCategory, tuple[AtomicPatternDefinition, ...]] = {}

    def search(
        index: int,
        selected: tuple[AtomicPatternDefinition, ...],
    ) -> dict[AtomicPatternCategory, tuple[AtomicPatternDefinition, ...]] | None:
        nonlocal nodes
        if nodes >= _MAX_FALLBACK_SEARCH_NODES:
            return None
        if index == len(categories):
            return dict(assignment)

        category = categories[index]
        for candidate_set in options[category]:
            nodes += 1
            if nodes > _MAX_FALLBACK_SEARCH_NODES:
                return None
            if not _definitions_are_compatible(selected + candidate_set):
                continue
            assignment[category] = candidate_set
            result = search(index + 1, selected + candidate_set)
            if result is not None:
                return result
            assignment.pop(category, None)
        return None

    return search(0, ())


def _maximize_fallback_assignment(
    grouped: Mapping[
        AtomicPatternCategory,
        Sequence[AtomicPatternDefinition],
    ],
    assignment: Mapping[
        AtomicPatternCategory,
        Sequence[AtomicPatternDefinition],
    ],
) -> dict[AtomicPatternCategory, tuple[AtomicPatternDefinition, ...]]:
    """Deterministically add compatible candidates after minimum feasibility."""

    categories = tuple(sorted(grouped, key=lambda value: value.value))
    selected = [
        definition
        for category in categories
        for definition in assignment[category]
    ]
    expanded = {
        category: list(assignment[category])
        for category in categories
    }
    for category in categories:
        entries = tuple(
            sorted(
                grouped[category],
                key=lambda item: (item.pattern_id, item.version),
            )
        )
        maximum = 1 if len(entries) == 1 else min(5, len(entries))
        for definition in entries:
            if len(expanded[category]) >= maximum:
                break
            if definition in expanded[category]:
                continue
            if not _definitions_are_compatible(tuple(selected) + (definition,)):
                continue
            expanded[category].append(definition)
            selected.append(definition)
    return {
        category: tuple(expanded[category])
        for category in categories
    }


def _fallback_plan(
    *,
    direction_id: str,
    registry: AtomicPatternRegistry,
    selector_catalog: Sequence[Mapping[str, Any]],
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None,
    optional_categories: Iterable[AtomicPatternCategory | str],
) -> PatternCandidatePlan:
    optional = _normalise_optional_categories(optional_categories)
    eligible = _eligible_catalog(
        registry=registry,
        selector_catalog=selector_catalog,
        effective_approved=effective_approved,
    )
    grouped: dict[AtomicPatternCategory, list[AtomicPatternDefinition]] = {}
    for item in eligible:
        category = AtomicPatternCategory(item["category"])
        grouped.setdefault(category, []).append(
            registry.resolve(str(item["pattern_id"]), int(item["version"]))
        )
    for category in optional:
        grouped.setdefault(category, [])

    assignment = _solve_fallback_assignment(grouped)
    if assignment is None:
        raise PatternCandidateValidationError(
            "deterministic fallback could not satisfy candidate compatibility constraints"
        )
    assignment = _maximize_fallback_assignment(grouped, assignment)

    groups: list[PatternCandidateGroup] = []
    for category in sorted(grouped, key=lambda value: value.value):
        entries = assignment[category]
        chosen: list[PatternCandidate] = []
        for definition in entries:
            chosen.append(
                PatternCandidate(
                    pattern_id=definition.pattern_id,
                    version=definition.version,
                    rank=len(chosen) + 1,
                    reason="Deterministic server fallback ordered by category and exact version.",
                )
            )
        groups.append(
            PatternCandidateGroup(
                category=category,
                candidates=tuple(chosen),
            )
        )
    return PatternCandidatePlan(
        schema_version=2,
        direction_id=direction_id,
        groups=tuple(groups),
        summary="Deterministic fallback shortlist ordered by category, pattern_id, and version.",
    )


def _result_parts(value: object) -> tuple[object, TokenUsage, tuple[str, ...]]:
    if isinstance(value, PatternCandidatePlanResult):
        ids = tuple(value.provider_request_ids)
        if value.provider_request_id and value.provider_request_id not in ids:
            ids += (value.provider_request_id,)
        return value.plan, value.usage, ids
    if hasattr(value, "plan") or hasattr(value, "payload"):
        raw_plan = getattr(value, "plan", None)
        if raw_plan is None:
            raw_plan = getattr(value, "payload", None)
        usage = getattr(value, "usage", TokenUsage())
        request_id = getattr(value, "provider_request_id", None)
        request_ids = tuple(getattr(value, "provider_request_ids", ()) or ())
        if request_id and request_id not in request_ids:
            request_ids += (request_id,)
        return raw_plan, usage, request_ids
    if isinstance(value, (PatternCandidatePlan, Mapping)):
        return value, TokenUsage(), ()
    raise PatternCandidateValidationError("selector returned an unsupported result")


async def plan_pattern_candidates(
    engine: Any,
    request: BuilderRequest,
    selected_direction: DirectionProposal,
    registry: AtomicPatternRegistry,
    *,
    selector_catalog: Sequence[Mapping[str, Any]] | None = None,
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None = None,
    optional_categories: Iterable[AtomicPatternCategory | str] = (),
    correction: str | None = None,
) -> PatternCandidatePlanResult:
    """Call the untrusted selector once, retry once with diagnostics, then fallback."""

    raw_catalog = (
        tuple(selector_catalog)
        if selector_catalog is not None
        else registry.selector_catalog()
    )
    optional = _normalise_optional_categories(optional_categories)
    optional_values = tuple(category.value for category in sorted(optional, key=lambda item: item.value))
    catalog = _filter_selector_catalog(
        registry=registry,
        selector_catalog=raw_catalog,
        effective_approved=effective_approved,
    )
    total_usage = TokenUsage()
    request_ids: list[str] = []
    diagnostics: list[str] = []
    current_correction = correction[:_MAX_CORRECTION_DIAGNOSTIC] if correction else None

    for attempt in range(2):
        try:
            raw_result = await engine.plan_pattern_candidates(
                request=request,
                selected_direction=selected_direction,
                selector_catalog=tuple(catalog),
                correction=current_correction,
                optional_categories=optional_values,
            )
            raw_plan, usage, ids = _result_parts(raw_result)
            if isinstance(usage, TokenUsage):
                total_usage += usage
            for request_id in ids:
                if request_id and request_id not in request_ids:
                    request_ids.append(request_id)
            parsed = validate_pattern_candidate_plan(
                raw_plan,
                registry=registry,
                selector_catalog=catalog,
                effective_approved=effective_approved,
                optional_categories=optional,
                selected_direction=selected_direction,
            )
            return PatternCandidatePlanResult(
                plan=parsed,
                usage=total_usage,
                provider_request_id=request_ids[-1] if request_ids else None,
                provider_request_ids=tuple(request_ids),
                diagnostic="; ".join(diagnostics) or None,
                used_fallback=False,
            )
        except Exception as exc:  # selector output and provider errors are untrusted
            usage = getattr(exc, "usage", None)
            if isinstance(usage, TokenUsage):
                total_usage += usage
            provider_id = getattr(exc, "provider_request_id", None)
            if provider_id and provider_id not in request_ids:
                request_ids.append(str(provider_id))
            diagnostic = _bounded_diagnostic(exc)
            diagnostics.append(diagnostic)
            current_correction = (
                "The previous selector response failed server validation: " + diagnostic
            )[:_MAX_CORRECTION_DIAGNOSTIC]

    fallback = _fallback_plan(
        direction_id=selected_direction.proposal_id,
        registry=registry,
        selector_catalog=catalog,
        effective_approved=effective_approved,
        optional_categories=optional,
    )
    fallback = validate_pattern_candidate_plan(
        fallback,
        registry=registry,
        selector_catalog=catalog,
        effective_approved=effective_approved,
        optional_categories=optional,
        selected_direction=selected_direction,
    )
    return PatternCandidatePlanResult(
        plan=fallback,
        usage=total_usage,
        provider_request_id=request_ids[-1] if request_ids else None,
        provider_request_ids=tuple(request_ids),
        diagnostic="; ".join(diagnostics)[:_MAX_CORRECTION_DIAGNOSTIC] or None,
        used_fallback=True,
    )


__all__ = [
    "PatternCandidate",
    "PatternCandidateGroup",
    "PatternCandidatePlan",
    "PatternCandidatePlanResult",
    "PatternCandidateValidationError",
    "build_pattern_candidate_plan_prompt",
    "plan_pattern_candidates",
    "validate_pattern_candidate_plan",
]
