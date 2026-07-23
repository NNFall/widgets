from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Protocol

from .browser_audit import BrowserAuditError, BrowserAuditReport
from .engines.base import BuilderEngineError, EngineResult
from .models import (
    BuilderRequest,
    DirectionProposal,
    Stage,
    TokenUsage,
    WidgetArtifact,
)
from .strict_visual_critic import (
    StrictVisualCriticError,
    StrictVisualCriticResult,
)
from .strict_visual_models import (
    StrictVisualCritique,
    StrictVisualDimension,
    StrictVisualSeverity,
    StrictVisualVerdict,
)
from .validation import validate_artifact
from .visual_models import (
    VisualCategory,
    VisualFinding,
    VisualSeverity,
)


_REVISION_FIELDS = (
    "art_direction",
    "body_html",
    "change_summary",
    "css",
    "javascript",
    "layout_contract",
    "suggested_actions",
    "theme_tokens",
)


class ExperimentAuditor(Protocol):
    async def audit(self, candidate: WidgetArtifact) -> BrowserAuditReport: ...


class ExperimentCritic(Protocol):
    async def critique(
        self,
        *,
        audit: BrowserAuditReport,
        brief: str,
        art_direction: str,
        locale: str,
        phase: str,
    ) -> StrictVisualCriticResult: ...


class ExperimentRevisionEngine(Protocol):
    async def generate(
        self,
        *,
        request: BuilderRequest,
        stage: Stage,
        revision: int,
        previous_artifact: WidgetArtifact,
        repair_issues: tuple[Any, ...],
        visual_findings: tuple[VisualFinding, ...],
        selected_direction: DirectionProposal | None,
    ) -> EngineResult: ...


@dataclass(frozen=True)
class ExperimentReviewEvidence:
    artifact: WidgetArtifact
    audit: BrowserAuditReport
    critique: StrictVisualCritique


@dataclass(frozen=True)
class ExperimentReviewResult:
    raw: ExperimentReviewEvidence
    final: ExperimentReviewEvidence
    usage: TokenUsage
    elapsed_seconds: float


class ExperimentVisualQualityError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        diagnostic: str = "",
        raw: ExperimentReviewEvidence | None = None,
        usage: TokenUsage | None = None,
        elapsed_seconds: float = 0,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.diagnostic = diagnostic
        self.raw = raw
        self.usage = usage or TokenUsage()
        self.elapsed_seconds = elapsed_seconds


def _snapshot_artifact(artifact: WidgetArtifact) -> WidgetArtifact:
    return WidgetArtifact.from_dict(artifact.to_dict())


def _changed_revision_fields(
    before: WidgetArtifact,
    after: WidgetArtifact,
) -> frozenset[str]:
    return frozenset(
        field_name
        for field_name in _REVISION_FIELDS
        if getattr(before, field_name) != getattr(after, field_name)
    )


_CATEGORY_BY_DIMENSION = {
    StrictVisualDimension.DIRECTION_FIDELITY: VisualCategory.SITE_FIT,
    StrictVisualDimension.PAGE_SUBORDINATION: VisualCategory.PAGE_SUBORDINATION,
    StrictVisualDimension.VISUAL_HIERARCHY: VisualCategory.SITE_FIT,
    StrictVisualDimension.CONVERSATION_CLARITY: VisualCategory.FUNCTIONAL_TRUTH,
    StrictVisualDimension.TYPOGRAPHY_LEGIBILITY: VisualCategory.ACCESSIBILITY,
    StrictVisualDimension.SPACING_ALIGNMENT: VisualCategory.RESPONSIVE_INTEGRITY,
    StrictVisualDimension.SYSTEM_COHERENCE: VisualCategory.SITE_FIT,
    StrictVisualDimension.RESPONSIVE_COMPOSITION: VisualCategory.RESPONSIVE_INTEGRITY,
    StrictVisualDimension.CRAFT_POLISH: VisualCategory.SITE_FIT,
    StrictVisualDimension.DISTINCTIVENESS: VisualCategory.SITE_FIT,
}
_SEVERITY_MAP = {
    StrictVisualSeverity.BLOCKER: VisualSeverity.BLOCKER,
    StrictVisualSeverity.MAJOR: VisualSeverity.MAJOR,
    StrictVisualSeverity.MINOR: VisualSeverity.MINOR,
}


def _repair_findings(
    critique: StrictVisualCritique,
) -> tuple[VisualFinding, ...]:
    assessments = {
        item.dimension: item for item in critique.assessments
    }
    actions_by_finding = {
        finding_id: action
        for action in critique.revision_actions
        for finding_id in action.finding_ids
    }
    values: list[VisualFinding] = []
    for finding in critique.findings:
        assessment = assessments[finding.dimension]
        severity = assessment.severity
        action = actions_by_finding.get(finding.finding_id)
        if severity is None or action is None:
            continue
        values.append(
            VisualFinding(
                finding_id=finding.finding_id,
                severity=_SEVERITY_MAP[severity],
                category=_CATEGORY_BY_DIMENSION[finding.dimension],
                screenshot_id=finding.screenshot_id,
                evidence=finding.evidence,
                region=finding.region,
                artifact_fields=action.artifact_fields,
                repair_instruction=action.instruction,
                confidence=finding.confidence,
            )
        )
    return tuple(values)


class ExperimentReview:
    def __init__(
        self,
        *,
        request: BuilderRequest,
        auditor: ExperimentAuditor,
        critic: ExperimentCritic,
        engine: ExperimentRevisionEngine,
        selected_direction: DirectionProposal | None = None,
        clock: Any = time.perf_counter,
    ) -> None:
        self.request = request
        self.auditor = auditor
        self.critic = critic
        self.engine = engine
        self.selected_direction = selected_direction
        self._clock = clock

    def _error(
        self,
        error_code: str,
        message: str,
        *,
        started_at: float,
        diagnostic: str = "",
        raw: ExperimentReviewEvidence | None = None,
        usage: TokenUsage | None = None,
    ) -> ExperimentVisualQualityError:
        return ExperimentVisualQualityError(
            error_code,
            message,
            diagnostic=diagnostic,
            raw=raw,
            usage=usage,
            elapsed_seconds=max(0, self._clock() - started_at),
        )

    async def review(self, raw_artifact: WidgetArtifact) -> ExperimentReviewResult:
        started_at = self._clock()
        raw_snapshot = _snapshot_artifact(raw_artifact)
        usage = TokenUsage()
        try:
            raw_audit = await self.auditor.audit(raw_snapshot)
        except BrowserAuditError as exc:
            raise self._error(
                "initial_deterministic_failure",
                "Исходный кандидат не прошёл детерминированную браузерную проверку",
                started_at=started_at,
                diagnostic=exc.diagnostic or "; ".join(exc.failures),
            ) from exc

        try:
            raw_critic_result = await self.critic.critique(
                audit=raw_audit,
                brief=self.request.brief,
                art_direction=raw_snapshot.art_direction,
                locale=self.request.locale,
                phase="raw",
            )
        except asyncio.CancelledError:
            raise
        except StrictVisualCriticError as exc:
            usage = usage + exc.usage
            raise self._error(
                "strict_visual_critic_failed",
                "Строгий визуальный критик не завершил проверку raw",
                started_at=started_at,
                diagnostic=exc.diagnostic or exc.error_code,
                usage=usage,
            ) from exc
        usage = usage + raw_critic_result.usage
        raw_evidence = ExperimentReviewEvidence(
            artifact=raw_snapshot,
            audit=raw_audit,
            critique=raw_critic_result.critique,
        )
        if raw_critic_result.critique.verdict is StrictVisualVerdict.PASS:
            final_evidence = ExperimentReviewEvidence(
                artifact=_snapshot_artifact(raw_snapshot),
                audit=raw_audit,
                critique=raw_critic_result.critique,
            )
            return ExperimentReviewResult(
                raw=raw_evidence,
                final=final_evidence,
                usage=usage,
                elapsed_seconds=max(0, self._clock() - started_at),
            )
        if self.request.visual_repair_limit < 1:
            raise self._error(
                "strict_visual_revision_disabled",
                "Строгая визуальная проверка требует ревизию, но она отключена",
                started_at=started_at,
                raw=raw_evidence,
                usage=usage,
            )

        repair_findings = _repair_findings(raw_critic_result.critique)
        if not repair_findings:
            raise self._error(
                "strict_visual_revision_plan_invalid",
                "Критик не сформировал применимый точечный план ревизии",
                started_at=started_at,
                raw=raw_evidence,
                usage=usage,
            )
        revision_input = _snapshot_artifact(raw_snapshot)
        try:
            revision_result = await self.engine.generate(
                request=self.request,
                stage=Stage.MOTION_POLISH,
                revision=raw_snapshot.revision,
                previous_artifact=revision_input,
                repair_issues=(),
                visual_findings=repair_findings,
                selected_direction=self.selected_direction,
            )
        except asyncio.CancelledError:
            raise
        except BuilderEngineError as exc:
            usage = usage + exc.usage
            raise self._error(
                "strict_visual_revision_unavailable",
                "Модель не смогла выполнить единственную визуальную ревизию",
                started_at=started_at,
                diagnostic=exc.diagnostic or exc.error_code,
                raw=raw_evidence,
                usage=usage,
            ) from exc
        usage = usage + revision_result.usage
        final_snapshot = _snapshot_artifact(revision_result.artifact)
        invariant_changes = []
        if final_snapshot.schema_version != raw_snapshot.schema_version:
            invariant_changes.append("schema_version")
        if final_snapshot.revision != raw_snapshot.revision:
            invariant_changes.append("revision")
        if final_snapshot.stage is not raw_snapshot.stage:
            invariant_changes.append("stage")
        changed_fields = _changed_revision_fields(raw_snapshot, final_snapshot)
        unrelated = (
            changed_fields - raw_critic_result.critique.allowed_artifact_fields
        )
        if invariant_changes or unrelated:
            diagnostic = ",".join(
                sorted(set(invariant_changes) | set(unrelated))
            )
            raise self._error(
                "unrelated_visual_revision",
                "Модель изменила поля вне точечного плана визуальной ревизии",
                started_at=started_at,
                diagnostic=diagnostic,
                raw=raw_evidence,
                usage=usage,
            )
        deterministic_issues = validate_artifact(
            final_snapshot,
            previous_revision=max(0, raw_snapshot.revision - 1),
        )
        if deterministic_issues:
            raise self._error(
                "deterministic_regression",
                "Единственная визуальная ревизия нарушила контракт артефакта",
                started_at=started_at,
                diagnostic="; ".join(
                    f"{item.code}:{item.field}" for item in deterministic_issues
                ),
                raw=raw_evidence,
                usage=usage,
            )
        try:
            final_audit = await self.auditor.audit(final_snapshot)
        except BrowserAuditError as exc:
            raise self._error(
                "deterministic_regression",
                "Единственная визуальная ревизия провалила браузерную проверку",
                started_at=started_at,
                diagnostic=exc.diagnostic or "; ".join(exc.failures),
                raw=raw_evidence,
                usage=usage,
            ) from exc
        try:
            final_critic_result = await self.critic.critique(
                audit=final_audit,
                brief=self.request.brief,
                art_direction=final_snapshot.art_direction,
                locale=self.request.locale,
                phase="final",
            )
        except asyncio.CancelledError:
            raise
        except StrictVisualCriticError as exc:
            usage = usage + exc.usage
            raise self._error(
                "strict_visual_critic_failed",
                "Строгий визуальный критик не завершил проверку final",
                started_at=started_at,
                diagnostic=exc.diagnostic or exc.error_code,
                raw=raw_evidence,
                usage=usage,
            ) from exc
        usage = usage + final_critic_result.usage
        final_evidence = ExperimentReviewEvidence(
            artifact=final_snapshot,
            audit=final_audit,
            critique=final_critic_result.critique,
        )
        if final_critic_result.critique.verdict is StrictVisualVerdict.REPAIR:
            raise self._error(
                "strict_visual_revision_failed",
                "Единственная визуальная ревизия не достигла release bar",
                started_at=started_at,
                diagnostic=(
                    f"weighted_score="
                    f"{final_critic_result.critique.weighted_score:.3f}"
                ),
                raw=raw_evidence,
                usage=usage,
            )
        return ExperimentReviewResult(
            raw=raw_evidence,
            final=final_evidence,
            usage=usage,
            elapsed_seconds=max(0, self._clock() - started_at),
        )


__all__ = [
    "ExperimentReview",
    "ExperimentReviewEvidence",
    "ExperimentReviewResult",
    "ExperimentVisualQualityError",
]
