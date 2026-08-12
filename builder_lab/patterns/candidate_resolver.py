"""Resolve persisted atomic-pattern shortlists into stage-scoped code packs.

The selector plan is deliberately a metadata-only, exact-version contract.  This
module is the only boundary that turns those references back into implementation
assets.  Keeping the boundary small makes it possible to audit that a stage never
receives a newer registry version or an unrelated category.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Collection, Mapping

from ..models import Stage
from .atomic_models import (
    AtomicPatternCategory,
    AtomicPatternDefinition,
    AtomicPatternStatus,
    PatternCandidate,
    PatternCandidatePlan,
    PatternAssetBundle,
    PatternCandidateAssets,
    PatternCandidateVersionRef,
    PatternVersionRef,
    ExactPatternVersionRef,
    normalize_effective_approved,
)
from .atomic_registry import AtomicPatternRegistry, AtomicPatternRegistryError
from .atomic_quality import compute_atomic_quality_profile, effective_review_state_for


# The limit is intentionally generous enough for the shipped technical
# fixtures while still bounding a provider request independently per stage.
MAX_STAGE_PATTERN_PACK_BYTES = 256 * 1024
DEFAULT_STAGE_PATTERN_PACK_BYTES = MAX_STAGE_PATTERN_PACK_BYTES

STAGE_PATTERN_CATEGORIES = MappingProxyType(
    {
        Stage.FOUNDATION: (
            AtomicPatternCategory.LAUNCHER_SHAPE,
            AtomicPatternCategory.SHELL_LAYOUT,
            AtomicPatternCategory.BACKGROUND_EFFECT,
        ),
        Stage.IDENTITY: (
            AtomicPatternCategory.LAUNCHER_ATTENTION,
            AtomicPatternCategory.LAUNCHER_IDLE,
        ),
        Stage.CONVERSATION: (
            AtomicPatternCategory.ASSISTANT_MESSAGE_ENTER,
            AtomicPatternCategory.USER_MESSAGE_ENTER,
            AtomicPatternCategory.TYPING_INDICATOR,
            AtomicPatternCategory.MESSAGE_SEND,
            AtomicPatternCategory.COMPOSER_FOCUS,
        ),
        Stage.MOTION_POLISH: (
            AtomicPatternCategory.WIDGET_OPEN,
            AtomicPatternCategory.WIDGET_CLOSE,
            AtomicPatternCategory.CONTROL_HOVER,
            AtomicPatternCategory.RESPONSIVE_TRANSITION,
            AtomicPatternCategory.BACKGROUND_EFFECT,
        ),
    }
)

# A descriptive alias for callers that prefer the mapping terminology.
STAGE_TO_PATTERN_CATEGORIES = STAGE_PATTERN_CATEGORIES


class PatternCandidateResolutionError(ValueError):
    """The persisted shortlist cannot be safely exposed to a generation stage."""


@dataclass(frozen=True, slots=True)
class ResolvedPatternCandidatePack:
    """Bounded, stage-scoped exact implementation reference pack."""

    stage: Stage
    exposed_versions: tuple[PatternVersionRef, ...]
    prompt_text: str

    def __post_init__(self) -> None:
        stage = self.stage
        if not isinstance(stage, Stage):
            try:
                stage = Stage(stage)
            except (TypeError, ValueError) as exc:
                raise ValueError("stage is invalid") from exc
            object.__setattr__(self, "stage", stage)
        exposed = tuple(self.exposed_versions)
        if any(not isinstance(item, PatternVersionRef) for item in exposed):
            raise ValueError("exposed_versions contains an invalid value")
        allowed_categories = set(STAGE_PATTERN_CATEGORIES[stage])
        seen_exact: set[tuple[str, int]] = set()
        for item in exposed:
            if item.category not in allowed_categories:
                raise ValueError("exposed_versions contains an unmapped category")
            exact = (item.pattern_id, item.version)
            if exact in seen_exact:
                raise ValueError("exposed_versions contains duplicate exact versions")
            seen_exact.add(exact)
            if not item.ai_description or not item.technical_contract:
                raise ValueError("exposed_versions contains incomplete metadata")
            if not item.implementation_sha256:
                raise ValueError("exposed_versions contains an incomplete hash")
        if not isinstance(self.prompt_text, str) or "\x00" in self.prompt_text:
            raise ValueError("prompt_text is invalid")
        canonical_prompt = _prompt_for(stage, exposed)
        canonical_bytes = len(canonical_prompt.encode("utf-8"))
        if canonical_bytes > MAX_STAGE_PATTERN_PACK_BYTES:
            raise ValueError(
                "prompt_text exceeds the default UTF-8 byte budget"
            )
        if self.prompt_text != canonical_prompt:
            raise ValueError("prompt_text does not match exposed exact versions")
        object.__setattr__(self, "exposed_versions", exposed)

    @property
    def byte_length(self) -> int:
        return len(self.prompt_text.encode("utf-8"))


def _as_plan(plan: PatternCandidatePlan | Mapping[str, object]) -> PatternCandidatePlan:
    if isinstance(plan, PatternCandidatePlan):
        return plan
    if isinstance(plan, Mapping):
        try:
            return PatternCandidatePlan.from_dict(plan)
        except (TypeError, ValueError) as exc:
            raise PatternCandidateResolutionError(str(exc)) from exc
    raise PatternCandidateResolutionError("candidate plan is invalid")


def _effective_approved(
    definition: AtomicPatternDefinition,
    approved: Mapping[tuple[str, int], object] | Collection[tuple[str, int]] | None,
) -> bool:
    key = (definition.pattern_id, definition.version)
    approved_versions = normalize_effective_approved(approved)
    if approved_versions is None:
        return definition.provenance.get("review_state") == "approved"
    return key in approved_versions


def _resolve_definition(
    candidate: PatternCandidate,
    category: AtomicPatternCategory,
    registry: AtomicPatternRegistry,
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None,
) -> AtomicPatternDefinition:
    try:
        definition = registry.resolve(candidate.pattern_id, candidate.version)
    except AtomicPatternRegistryError as exc:
        raise PatternCandidateResolutionError(str(exc)) from exc
    if definition.status is not AtomicPatternStatus.ACTIVE:
        raise PatternCandidateResolutionError(
            f"candidate {candidate.pattern_id}@{candidate.version} is not active"
        )
    if definition.category is not category:
        raise PatternCandidateResolutionError(
            f"candidate {candidate.pattern_id}@{candidate.version} category mismatch"
        )
    if not _effective_approved(definition, effective_approved):
        raise PatternCandidateResolutionError(
            f"candidate {candidate.pattern_id}@{candidate.version} is not effectively approved"
        )
    quality = compute_atomic_quality_profile(
        definition,
        effective_review_state=effective_review_state_for(
            definition,
            effective_approved,
        ),
    )
    if not quality.selector_eligible:
        raise PatternCandidateResolutionError(
            f"candidate {candidate.pattern_id}@{candidate.version} is not quality-eligible"
        )
    return definition


def _ensure_compatible(definitions: tuple[AtomicPatternDefinition, ...]) -> None:
    by_id = {definition.pattern_id: definition for definition in definitions}
    for definition in definitions:
        for incompatible_id in definition.incompatible_with:
            if incompatible_id in by_id:
                raise PatternCandidateResolutionError(
                    "selected candidates are incompatible"
                )


def _prompt_for(stage: Stage, exposed: tuple[PatternVersionRef, ...]) -> str:
    sections = [
        "KAIGO_STAGE_PATTERN_REFERENCE_PACK",
        f"STAGE: {stage.value}",
        "Only the exact pattern_id@version references listed below may be used.",
        "Adapt visual expression only within each adaptation policy and preserve each technical contract.",
        "Trusted runtime owns open/close/submit/message insertion; do not replace or duplicate those actions.",
    ]
    for item in exposed:
        sections.extend(
            [
                "",
                f"CATEGORY: {item.category.value}",
                f"EXACT_REFERENCE: {item.pattern_id}@{item.version}",
                "MANIFEST_METADATA_JSON: "
                + json.dumps(
                    item.manifest_metadata(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "FULL_AI_DESCRIPTION:",
                item.ai_description,
                f"ADAPTATION_POLICY: {item.adaptation_policy.value}",
                "TECHNICAL_CONTRACT:",
                item.technical_contract,
                f"IMPLEMENTATION_SHA256: {item.implementation_sha256}",
                "BEGIN fragment.html",
                item.html,
                "END fragment.html",
                "BEGIN styles.css",
                item.css,
                "END styles.css",
                "BEGIN behavior.js",
                item.javascript,
                "END behavior.js",
            ]
        )
    return "\n".join(sections)


def resolve_pattern_candidate_pack(
    plan: PatternCandidatePlan | Mapping[str, object],
    stage: Stage,
    registry: AtomicPatternRegistry,
    *,
    effective_approved: Mapping[tuple[str, int], object]
    | Collection[tuple[str, int]]
    | None = None,
    max_bytes: int = MAX_STAGE_PATTERN_PACK_BYTES,
    byte_limit: int | None = None,
) -> ResolvedPatternCandidatePack:
    """Resolve only persisted exact shortlist versions relevant to ``stage``.

    ``registry`` is consulted by exact ``(pattern_id, version)`` key.  No
    nearest/newer version fallback is permitted.  ``effective_approved`` can be
    supplied by the persistence layer to apply review decisions made after the
    manifest was imported; omitted values use manifest provenance.
    """

    try:
        resolved_stage = stage if isinstance(stage, Stage) else Stage(stage)
    except (TypeError, ValueError) as exc:
        raise PatternCandidateResolutionError("stage is invalid") from exc
    if resolved_stage not in STAGE_PATTERN_CATEGORIES:
        raise PatternCandidateResolutionError(
            f"stage {resolved_stage.value} has no pattern category mapping"
        )
    if byte_limit is not None:
        if max_bytes != MAX_STAGE_PATTERN_PACK_BYTES and max_bytes != byte_limit:
            raise PatternCandidateResolutionError("byte limits conflict")
        max_bytes = byte_limit
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise PatternCandidateResolutionError("max_bytes must be a positive integer")

    parsed = _as_plan(plan)
    groups = {group.category: group for group in parsed.groups}
    exposed: list[PatternVersionRef] = []
    definitions: list[AtomicPatternDefinition] = []
    seen_exact: set[tuple[str, int]] = set()
    for category in STAGE_PATTERN_CATEGORIES[resolved_stage]:
        group = groups.get(category)
        if group is None:
            continue
        # PatternCandidateGroup normally validates these invariants.  Recheck
        # them here because persisted plans can be reconstructed by older code.
        for candidate in group.candidates:
            key = (candidate.pattern_id, candidate.version)
            if key in seen_exact:
                raise PatternCandidateResolutionError(
                    f"candidate {candidate.pattern_id}@{candidate.version} is duplicated"
                )
            seen_exact.add(key)
            definition = _resolve_definition(
                candidate,
                category,
                registry,
                effective_approved,
            )
            definitions.append(definition)
            exposed.append(PatternVersionRef.from_definition(definition, candidate))

    _ensure_compatible(tuple(definitions))
    exposed_versions = tuple(exposed)
    prompt_text = _prompt_for(resolved_stage, exposed_versions)
    prompt_bytes = len(prompt_text.encode("utf-8"))
    if prompt_bytes > max_bytes:
        raise PatternCandidateResolutionError(
            f"stage pattern pack exceeds UTF-8 byte budget ({prompt_bytes}>{max_bytes})"
        )
    return ResolvedPatternCandidatePack(
        stage=resolved_stage,
        exposed_versions=exposed_versions,
        prompt_text=prompt_text,
    )


__all__ = [
    "DEFAULT_STAGE_PATTERN_PACK_BYTES",
    "ExactPatternVersionRef",
    "MAX_STAGE_PATTERN_PACK_BYTES",
    "PatternAssetBundle",
    "PatternCandidateAssets",
    "PatternCandidateResolutionError",
    "PatternCandidateVersionRef",
    "PatternVersionRef",
    "ResolvedPatternCandidatePack",
    "STAGE_PATTERN_CATEGORIES",
    "STAGE_TO_PATTERN_CATEGORIES",
    "resolve_pattern_candidate_pack",
]
