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

__all__ = [
    "CompositionPlan",
    "CustomPatternEscape",
    "PatternCategory",
    "PatternDefinition",
    "PatternRegistry",
    "PatternRegistryError",
    "PatternResolutionError",
    "PatternSelection",
    "PatternStatus",
    "ResolvedComposition",
    "load_builtin_registry",
    "resolve_composition",
]
