"""Verified composition patterns used by the Kaigo builder."""

from .models import (
    CompositionPlan,
    CustomPatternEscape,
    PatternCategory,
    PatternSelection,
)
from .registry import (
    PatternDefinition,
    PatternRegistry,
    PatternRegistryError,
    PatternStatus,
    load_builtin_registry,
)
from .resolver import (
    PatternResolutionError,
    ResolvedComposition,
    resolve_composition,
)
from .planner import (
    CompositionPlanningError,
    PlannedComposition,
    plan_composition,
)

__all__ = [
    "CompositionPlan",
    "CompositionPlanningError",
    "CustomPatternEscape",
    "PatternCategory",
    "PatternDefinition",
    "PatternRegistry",
    "PatternRegistryError",
    "PatternResolutionError",
    "PatternSelection",
    "PatternStatus",
    "PlannedComposition",
    "ResolvedComposition",
    "load_builtin_registry",
    "plan_composition",
    "resolve_composition",
]
