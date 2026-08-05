"""Verified composition patterns used by the Kaigo builder."""

from .atomic_models import (
    AdaptationPolicy,
    AtomicPatternCategory,
    AtomicPatternDefinition,
    AtomicPatternStatus,
)
from .atomic_registry import (
    AtomicPatternRegistry,
    AtomicPatternRegistryError,
    compute_implementation_hash,
    compute_implementation_sha256,
    load_builtin_atomic_registry,
)

from .models import (
    CompositionPlan,
    CustomPatternEscape,
    PatternCategory,
    PatternSelection,
)
from .registry import (
    CssParameterBinding,
    PatternDefinition,
    PatternIntegrationMode,
    PatternRegistry,
    PatternRegistryError,
    PatternSourceContract,
    PatternStatus,
    load_builtin_registry,
)
from .source import (
    CompiledPatternSource,
    PatternBrowserAuditContract,
    PatternCompilationError,
    PatternSourceProvenance,
    canonical_anatomy_fingerprint,
    compile_runtime_source,
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
    "AdaptationPolicy",
    "AtomicPatternCategory",
    "AtomicPatternDefinition",
    "AtomicPatternRegistry",
    "AtomicPatternRegistryError",
    "AtomicPatternStatus",
    "CompositionPlan",
    "CompositionPlanningError",
    "CompiledPatternSource",
    "CustomPatternEscape",
    "CssParameterBinding",
    "PatternCategory",
    "PatternBrowserAuditContract",
    "PatternCompilationError",
    "PatternDefinition",
    "PatternIntegrationMode",
    "PatternRegistry",
    "PatternRegistryError",
    "PatternResolutionError",
    "PatternSelection",
    "PatternSourceContract",
    "PatternSourceProvenance",
    "PatternStatus",
    "PlannedComposition",
    "ResolvedComposition",
    "canonical_anatomy_fingerprint",
    "compute_implementation_hash",
    "compute_implementation_sha256",
    "compile_runtime_source",
    "load_builtin_atomic_registry",
    "load_builtin_registry",
    "plan_composition",
    "resolve_composition",
]
