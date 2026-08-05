from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from ..models import (
    BuilderRequest,
    ConceptRole,
    ConceptRoleBrief,
    DirectionJudgement,
    DirectionProposal,
    DirectionRole,
    Stage,
    TokenUsage,
    ValidationIssue,
    WidgetArtifact,
)
from ..visual_models import VisualFinding

if TYPE_CHECKING:
    from ..patterns.atomic_models import PatternCandidatePlan


@dataclass(frozen=True)
class EngineResult:
    artifact: WidgetArtifact
    usage: TokenUsage = TokenUsage()
    provider_request_id: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class DirectionProposalResult:
    proposal: DirectionProposal
    usage: TokenUsage = TokenUsage()
    provider_request_id: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class DirectionJudgeResult:
    judgement: DirectionJudgement
    usage: TokenUsage = TokenUsage()
    provider_request_id: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class ConceptRoleResult:
    brief: ConceptRoleBrief
    usage: TokenUsage = TokenUsage()
    provider_request_id: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class CompositionPlanResult:
    payload: Mapping[str, object]
    usage: TokenUsage = TokenUsage()
    provider_request_id: str | None = None
    diagnostic: str | None = None


@dataclass(frozen=True)
class PatternCandidatePlanResult:
    plan: "PatternCandidatePlan"
    usage: TokenUsage = TokenUsage()
    provider_request_id: str | None = None
    provider_request_ids: tuple[str, ...] = ()
    diagnostic: str | None = None
    used_fallback: bool = False

    @property
    def payload(self) -> Mapping[str, object]:
        """Compatibility view matching the legacy composition result shape."""

        return self.plan.to_dict()


class BuilderEngineError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        diagnostic: str | None = None,
        usage: TokenUsage | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        super().__init__(public_message)
        self.error_code = error_code
        self.public_message = public_message
        self.diagnostic = diagnostic
        self.usage = usage or TokenUsage()
        self.provider_request_id = provider_request_id


class BuilderEngine(Protocol):
    async def generate(
        self,
        *,
        request: BuilderRequest,
        stage: Stage,
        revision: int,
        previous_artifact: WidgetArtifact | None = None,
        repair_issues: tuple[ValidationIssue, ...] = (),
        visual_findings: tuple[VisualFinding, ...] = (),
        composition: Any | None = None,
    ) -> EngineResult: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...


class DirectBuilderEngine(BuilderEngine, Protocol):
    async def plan_composition(
        self,
        *,
        request: BuilderRequest,
        selected_direction: DirectionProposal,
        public_catalog: tuple[dict[str, Any], ...],
        correction: str | None = None,
    ) -> CompositionPlanResult: ...

    async def plan_pattern_candidates(
        self,
        *,
        request: BuilderRequest,
        selected_direction: DirectionProposal,
        selector_catalog: tuple[dict[str, Any], ...],
        correction: str | None = None,
        optional_categories: tuple[str, ...] = (),
    ) -> PatternCandidatePlanResult: ...

    async def develop_concept_role(
        self,
        *,
        request: BuilderRequest,
        role: ConceptRole,
        prior_briefs: tuple[ConceptRoleBrief, ...] = (),
    ) -> ConceptRoleResult: ...

    async def propose_direction(
        self,
        *,
        request: BuilderRequest,
        role: DirectionRole,
        proposal_id: str,
    ) -> DirectionProposalResult: ...

    async def judge_directions(
        self,
        *,
        request: BuilderRequest,
        proposals: tuple[DirectionProposal, ...],
    ) -> DirectionJudgeResult: ...

    async def generate(
        self,
        *,
        request: BuilderRequest,
        stage: Stage,
        revision: int,
        previous_artifact: WidgetArtifact | None = None,
        repair_issues: tuple[ValidationIssue, ...] = (),
        visual_findings: tuple[VisualFinding, ...] = (),
        selected_direction: DirectionProposal | None = None,
        composition: Any | None = None,
    ) -> EngineResult: ...
