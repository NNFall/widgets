"""AI shortlist selection and server-side validation for atomic patterns.

The selector is deliberately untrusted.  It receives metadata only; every
exact version and every relationship is resolved and checked by this module
before the resulting plan can be handed to a generation stage.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
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
        eligible.append(item)
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
            if definition.pattern_id not in other.incompatible_with:
                raise PatternCandidateValidationError(
                    "incompatibilities must be symmetric"
                )
    return parsed


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
    grouped: dict[AtomicPatternCategory, list[Mapping[str, Any]]] = {}
    for item in eligible:
        category = AtomicPatternCategory(item["category"])
        grouped.setdefault(category, []).append(item)
    for category in optional:
        grouped.setdefault(category, [])

    groups: list[PatternCandidateGroup] = []
    for category in sorted(grouped, key=lambda value: value.value):
        entries = sorted(
            grouped[category],
            key=lambda item: (str(item["category"]), str(item["pattern_id"]), int(item["version"])),
        )[:5]
        groups.append(
            PatternCandidateGroup(
                category=category,
                candidates=tuple(
                    PatternCandidate(
                        pattern_id=str(item["pattern_id"]),
                        version=int(item["version"]),
                        rank=rank,
                        reason="Deterministic server fallback ordered by category and exact version.",
                    )
                    for rank, item in enumerate(entries, start=1)
                ),
            )
        )
    if not groups:
        # The schema requires at least one group.  An empty optional category is
        # the only safe representation when the effective catalog is empty.
        category = next(iter(optional), AtomicPatternCategory.BACKGROUND_EFFECT)
        groups.append(PatternCandidateGroup(category=category, candidates=()))
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

    catalog = tuple(selector_catalog) if selector_catalog is not None else registry.selector_catalog()
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
                optional_categories=optional_categories,
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
        optional_categories=optional_categories,
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
