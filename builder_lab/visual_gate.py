from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol

from .engines.base import BuilderEngineError, DirectBuilderEngine
from .forensics.models import ForensicBlob
from .models import (
    AssistantPersona,
    BuilderRequest,
    DirectionProposal,
    Stage,
    TokenUsage,
    ValidationIssue,
    WidgetArtifact,
)
from .store import RunStore
from .validation import strip_reserved_runtime_attributes, validate_artifact
from .browser_audit import BrowserAuditError
from .visual_models import MIN_REPAIR_CONFIDENCE, VisualFinding, VisualSeverity


MAX_BROWSER_CAPTURE_ATTEMPTS = 6
MAX_AI_REVIEW_ATTEMPTS = 3
# Backwards-compatible export for callers that display the former limit.
MAX_VISUAL_AUDITS = MAX_BROWSER_CAPTURE_ATTEMPTS
MAX_BROWSER_REPAIRS = 4
MAX_VALIDATION_REPAIRS = 4
MAX_VISUAL_REPAIRS = 10
MAX_REPEATED_VISUAL_ISSUE_ROUNDS = 3
_TRANSIENT_VISUAL_RETRY_DELAYS_SECONDS = (5, 15)
LOGGER = logging.getLogger(__name__)

_CRITIC_ROLE_LABELS = {
    "conversation_ux": "диалог и удобство",
    "brand_motion": "бренд и анимация",
    "adversarial_customer": "строгий взгляд клиента",
}


def _critic_role_label(role: Any) -> str:
    value = getattr(role, "value", str(role))
    return _CRITIC_ROLE_LABELS.get(value, value)


def _critic_role_value(role: Any) -> str:
    return str(getattr(role, "value", role))


def _transient_visual_retry_delay(
    diagnostic: object,
    attempt: int,
) -> int:
    if not isinstance(diagnostic, str) or not 1 <= attempt < MAX_AI_REVIEW_ATTEMPTS:
        return 0
    try:
        payload = json.loads(diagnostic)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0
    if (
        not isinstance(payload, dict)
        or payload.get("terminal_reason") != "committee_transient_routes"
    ):
        return 0
    return _TRANSIENT_VISUAL_RETRY_DELAYS_SECONDS[attempt - 1]


def _critic_forensic_evidence(role: Any, role_result: Any) -> dict[str, Any]:
    critique = getattr(role_result, "critique", None)
    critique_payload = (
        critique.to_dict()
        if critique is not None and callable(getattr(critique, "to_dict", None))
        else {}
    )
    observations = []
    for observation in tuple(getattr(role_result, "observations", ())):
        observations.append(
            {
                "screenshot_id": getattr(observation, "screenshot_id", None),
                "observation": getattr(observation, "observation", None),
                "pixel_facts": dict(getattr(observation, "pixel_facts", {})),
            }
        )
    pixel_proof = getattr(role_result, "pixel_proof", None)
    proof_payload = None
    if pixel_proof is not None:
        proof_payload = {
            "code": getattr(pixel_proof, "code", None),
            "screenshot_id": getattr(pixel_proof, "screenshot_id", None),
            "source_sha256": getattr(pixel_proof, "source_sha256", None),
            "transmitted_sha256": getattr(
                pixel_proof,
                "transmitted_sha256",
                None,
            ),
        }
    usage = getattr(role_result, "usage", TokenUsage())
    return {
        "role": _critic_role_value(role),
        "critique": critique_payload,
        "observations": observations,
        "pixel_proof": proof_payload,
        "usage": usage.to_dict() if isinstance(usage, TokenUsage) else {},
    }


def _judge_forensic_evidence(
    critique: Any,
    supporting_roles: dict[str, tuple[Any, ...]],
) -> dict[str, Any]:
    return {
        "critique": (
            critique.to_dict()
            if callable(getattr(critique, "to_dict", None))
            else {}
        ),
        "supporting_roles": {
            finding_id: [_critic_role_value(role) for role in roles]
            for finding_id, roles in supporting_roles.items()
        },
    }


def _failure_forensic_evidence(kind: str, error: BaseException) -> dict[str, Any]:
    return {
        kind: {
            "error_code": getattr(error, "error_code", type(error).__name__),
            "message": str(error),
            "diagnostic": getattr(error, "diagnostic", None),
            "failures": list(getattr(error, "failures", ())),
        }
    }


class BrowserAuditor(Protocol):
    async def audit(self, artifact: WidgetArtifact) -> Any: ...


class VisualCritic(Protocol):
    async def critique(
        self,
        *,
        audit: Any,
        brief: str,
        art_direction: str,
        assistant_persona: AssistantPersona | None = None,
    ) -> Any: ...

    async def aclose(self) -> None: ...


class RepairVerifier(Protocol):
    async def verify(
        self,
        *,
        findings: tuple[VisualFinding, ...],
        before: WidgetArtifact,
        after: WidgetArtifact,
    ) -> Any: ...

    async def aclose(self) -> None: ...


def qualified_findings(findings: tuple[VisualFinding, ...]) -> tuple[VisualFinding, ...]:
    return tuple(
        finding
        for finding in findings
        if finding.severity in {VisualSeverity.BLOCKER, VisualSeverity.MAJOR}
        and finding.confidence >= MIN_REPAIR_CONFIDENCE
    )


def visual_finding_issues(
    findings: tuple[VisualFinding, ...],
    *,
    supporting_roles: dict[str, tuple[Any, ...]] | None = None,
) -> tuple[ValidationIssue, ...]:
    issues = []
    for finding in findings:
        roles = tuple(
            _critic_role_label(role)
            for role in (supporting_roles or {}).get(finding.finding_id, ())
        )
        support = f" Подтвердили: {', '.join(roles)}." if roles else ""
        issues.append(
            ValidationIssue(
                code=f"visual_{finding.category.value}",
                field="/".join(finding.artifact_fields),
                message=(
                    f"{finding.evidence} Исправить: "
                    f"{finding.repair_instruction}.{support}"
                )[:1_500],
            )
        )
    return tuple(issues)


def visual_fingerprint(findings: tuple[VisualFinding, ...]) -> str:
    """Fingerprint the semantic problem, not model-specific prose or coordinates."""
    normalized = []
    for finding in findings:
        normalized.append(
            {
                "category": finding.category.value,
                "screenshot_id": finding.screenshot_id,
                "semantic_region": finding.region.semantic_region,
                "artifact_fields": sorted(finding.artifact_fields),
            }
        )
    payload = json.dumps(
        sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True)),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def artifact_fingerprint(
    candidate: WidgetArtifact,
    assistant_persona: AssistantPersona | None = None,
) -> str:
    fingerprint_payload = candidate.to_dict()
    # A prose-only progress note is not an implementation change. Ignoring it
    # makes a no-op visual repair fail fast instead of consuming more AI rounds.
    fingerprint_payload.pop("change_summary", None)
    payload = json.dumps(
        {
            "artifact": fingerprint_payload,
            "assistant_persona": (
                assistant_persona.to_dict()
                if assistant_persona is not None
                else None
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def forbidden_repair_fields(
    before: WidgetArtifact,
    after: WidgetArtifact,
    findings: tuple[VisualFinding, ...],
) -> tuple[str, ...]:
    before_payload = before.to_dict()
    after_payload = after.to_dict()
    changed = {
        field_name
        for field_name in before_payload
        if before_payload[field_name] != after_payload[field_name]
    }
    allowed = {
        field_name
        for finding in findings
        for field_name in finding.artifact_fields
        if field_name != "art_direction"
    }
    allowed.add("change_summary")
    return tuple(sorted(changed - allowed))


def browser_repair_issues(
    error: BrowserAuditError,
    *,
    include_transient_runtime: bool = False,
) -> tuple[ValidationIssue, ...]:
    if error.error_code != "browser_gate_failed":
        return ()
    failures = error.failures
    if not failures:
        diagnostic = (error.diagnostic or str(error)).strip()
        normalized = diagnostic.casefold()
        repairable_runtime_failure = any(
            marker in normalized
            for marker in (
                "page crashed",
                "targetclosed",
                "target page, context or browser has been closed",
            )
        )
        if include_transient_runtime and (
            "timeout" in normalized or "timed out" in normalized
        ):
            repairable_runtime_failure = True
        if not diagnostic or not repairable_runtime_failure:
            return ()
        failures = (f"Сбой браузерной проверки: {diagnostic}",)
    return tuple(
        ValidationIssue(
            code="browser_gate_failed",
            field="body_html/css",
            message=failure[:500],
        )
        for failure in failures[:12]
    )


def browser_repair_fingerprint(issues: tuple[ValidationIssue, ...]) -> str:
    normalized = "\n".join(
        re.sub(r"\s+", " ", issue.message).strip().casefold() for issue in issues
    )
    return "browser:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validation_repair_fingerprint(issues: tuple[ValidationIssue, ...]) -> str:
    normalized = json.dumps(
        sorted(
            (issue.to_dict() for issue in issues),
            key=lambda item: json.dumps(item, sort_keys=True),
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "validation:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def forbidden_browser_repair_fields(
    before: WidgetArtifact,
    after: WidgetArtifact,
) -> tuple[str, ...]:
    before_payload = before.to_dict()
    after_payload = after.to_dict()
    changed = {
        field_name
        for field_name in before_payload
        if before_payload[field_name] != after_payload[field_name]
    }
    return tuple(
        sorted(
            changed
            - {
                "body_html",
                "css",
                "javascript",
                "suggested_actions",
                "layout_contract",
                "change_summary",
            }
        )
    )


def apply_browser_repair(
    before: WidgetArtifact,
    proposed: WidgetArtifact,
) -> WidgetArtifact:
    """Apply only the fields a deterministic browser finding can authorize."""
    return replace(
        before,
        body_html=proposed.body_html,
        css=proposed.css,
        javascript=proposed.javascript,
        suggested_actions=proposed.suggested_actions,
        layout_contract=proposed.layout_contract,
        change_summary=proposed.change_summary,
    )


class VisualRepairGate:
    """Keep a motion-polish revision private until visual checks pass."""

    _cleanup_orphans: set[asyncio.Task[Any]] = set()

    def __init__(
        self,
        *,
        store: RunStore,
        audit_factory: Callable[[], BrowserAuditor],
        critic_factory: Callable[[], VisualCritic],
        verifier_factory: Callable[[], RepairVerifier] | None = None,
        critic_close_timeout_seconds: float = 5.0,
        fail_open_on_inconclusive: bool = False,
    ) -> None:
        if not 0 < critic_close_timeout_seconds <= 30:
            raise ValueError("critic_close_timeout_seconds is invalid")
        self._store = store
        self._audit_factory = audit_factory
        self._critic_factory = critic_factory
        self._verifier_factory = verifier_factory
        self._critic_close_timeout_seconds = critic_close_timeout_seconds
        self._fail_open_on_inconclusive = fail_open_on_inconclusive

    async def _close_critic(self, critic: VisualCritic) -> None:
        cleanup = asyncio.create_task(critic.aclose())
        try:
            done, _ = await asyncio.wait(
                {cleanup}, timeout=self._critic_close_timeout_seconds
            )
        except asyncio.CancelledError:
            cleanup.cancel()
            self._track_cleanup_orphan(cleanup)
            raise
        if not done:
            cleanup.cancel()
            self._track_cleanup_orphan(cleanup)
            return
        try:
            cleanup.result()
        except BaseException:
            pass

    @classmethod
    def _track_cleanup_orphan(cls, task: asyncio.Task[Any]) -> None:
        cls._cleanup_orphans.add(task)

        def consume(completed: asyncio.Task[Any]) -> None:
            cls._cleanup_orphans.discard(completed)
            try:
                completed.exception()
            except BaseException:
                pass

        task.add_done_callback(consume)

    async def _checkpoint(self, run_id: str) -> None:
        if (await self._store.snapshot(run_id)).cancel_requested:
            raise asyncio.CancelledError

    async def _verify_visual_repair(
        self,
        *,
        run_id: str,
        findings: tuple[VisualFinding, ...],
        before: WidgetArtifact,
        after: WidgetArtifact,
    ) -> None:
        if self._verifier_factory is None:
            return
        await self._store.append_event(
            run_id,
            event_type="visual_repair.verifier_started",
            stage=Stage.MOTION_POLISH,
            status="running",
            message="Независимо проверяем, что код отвечает замечаниям судьи.",
            revision=after.revision,
            issues=visual_finding_issues(findings),
            forensic_payload={
                "repair_verifier": {
                    "findings": [finding.to_dict() for finding in findings],
                    "before_revision": before.revision,
                    "after_revision": after.revision,
                }
            },
        )
        verifier: RepairVerifier | None = None
        try:
            verifier = self._verifier_factory()
            result = await verifier.verify(
                findings=findings,
                before=before,
                after=after,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._store.append_event(
                run_id,
                event_type="visual_repair.verifier_completed",
                stage=Stage.MOTION_POLISH,
                status="failed",
                message=(
                    "Независимая проверка кода не завершилась; "
                    "продолжаем новой браузерной проверкой."
                ),
                revision=after.revision,
                usage=getattr(exc, "usage", TokenUsage()),
                forensic_payload={
                    "repair_verifier": {
                        "failure": {
                            "error_code": getattr(
                                exc,
                                "error_code",
                                type(exc).__name__,
                            ),
                            "diagnostic": getattr(exc, "diagnostic", None),
                        }
                    }
                },
            )
            return
        finally:
            if verifier is not None:
                try:
                    async with asyncio.timeout(
                        self._critic_close_timeout_seconds
                    ):
                        await verifier.aclose()
                except (Exception, TimeoutError):
                    pass
        unresolved = tuple(getattr(result, "unresolved", ()))
        issues = tuple(
            ValidationIssue(
                code="visual_repair_unresolved",
                field=check.finding_id,
                message=check.evidence,
            )
            for check in unresolved
        )
        await self._store.append_event(
            run_id,
            event_type="visual_repair.verifier_completed",
            stage=Stage.MOTION_POLISH,
            status="failed" if issues else "completed",
            message=result.summary,
            revision=after.revision,
            usage=result.usage,
            issues=issues,
            forensic_payload={
                "repair_verifier": {
                    "summary": result.summary,
                    "checks": [
                        {
                            "finding_id": check.finding_id,
                            "status": check.status,
                            "evidence": check.evidence,
                        }
                        for check in result.checks.values()
                    ],
                }
            },
        )

    async def _record_validation(
        self,
        run_id: str,
        candidate: WidgetArtifact,
        issues: tuple[ValidationIssue, ...],
    ) -> None:
        await self._store.append_event(
            run_id,
            event_type="artifact.validated",
            stage=candidate.stage,
            status="failed" if issues else "completed",
            message=(
                f"Техническая проверка после визуальной доработки нашла ошибок: {len(issues)}"
                if issues
                else "Визуальная доработка прошла техническую проверку"
            ),
            revision=candidate.revision,
            issues=issues,
        )

    @staticmethod
    def _quality_error(
        diagnostic: str,
        *,
        usage: TokenUsage | None = None,
    ) -> BuilderEngineError:
        return BuilderEngineError(
            "visual_quality_failed",
            "Финальная визуальная проверка виджета не пройдена",
            diagnostic=diagnostic,
            usage=usage,
        )

    @staticmethod
    def _inconclusive_error(diagnostic: str) -> BuilderEngineError:
        return BuilderEngineError(
            "visual_review_inconclusive",
            "Визуальные критики не смогли завершить проверку",
            diagnostic=diagnostic,
        )

    async def evaluate(
        self,
        *,
        run_id: str,
        request: BuilderRequest,
        engine: DirectBuilderEngine,
        candidate: WidgetArtifact,
        previous: WidgetArtifact,
        selected_direction: DirectionProposal,
        composition: Any | None = None,
        pattern_candidate_pack: Any | None = None,
    ) -> WidgetArtifact:
        if candidate.stage is not Stage.MOTION_POLISH:
            raise ValueError("visual gate accepts only motion_polish artifacts")
        if candidate.revision != previous.revision + 1:
            raise ValueError("visual gate requires consecutive revisions")

        await self._store.stage_visual_candidate(run_id, candidate)
        await self._store.stage_visual_draft(run_id, candidate)
        critic: VisualCritic | None = None
        seen: set[str] = set()
        browser_repair_count = 0
        validation_repair_count = 0
        visual_repair_count = 0
        visual_issue_rounds: dict[str, int] = {}
        cached_audit: Any | None = None
        screenshots_recorded = False
        browser_attempt = 0
        ai_review_attempt = 0
        try:
            try:
                critic = self._critic_factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise self._quality_error(
                    f"visual_critic_init: {type(exc).__name__}"
                ) from exc
            while True:
                is_browser_phase = cached_audit is None
                phase_attempt = (
                    browser_attempt + 1
                    if is_browser_phase
                    else ai_review_attempt + 1
                )
                await self._checkpoint(run_id)
                await self._store.append_event(
                    run_id,
                    event_type="visual_audit.started",
                    stage=Stage.MOTION_POLISH,
                    status="running",
                    message=(
                        "Браузерная визуальная проверка"
                        if is_browser_phase
                        else "Проверка визуальными AI-критиками"
                    )
                    + f": попытка {phase_attempt}",
                    revision=candidate.revision,
                )
                try:
                    if cached_audit is None:
                        browser_attempt += 1
                        audit = await self._audit_factory().audit(candidate)
                        cached_audit = audit
                        screenshots_recorded = False
                    else:
                        audit = cached_audit
                except asyncio.CancelledError:
                    raise
                except BrowserAuditError as exc:
                    LOGGER.warning(
                        "visual browser audit failed run_id=%s attempt=%s error_code=%s",
                        run_id,
                        browser_attempt,
                        exc.error_code,
                    )
                    await self._store.append_event(
                        run_id,
                        event_type="visual_audit.completed",
                        stage=Stage.MOTION_POLISH,
                        status="failed",
                        message=str(exc)[:1_000],
                        revision=candidate.revision,
                        forensic_payload=_failure_forensic_evidence(
                            "browser_audit",
                            exc,
                        ),
                    )
                    repair_issues = browser_repair_issues(
                        exc,
                        include_transient_runtime=browser_attempt > 1,
                    )
                    if not repair_issues:
                        if (
                            exc.error_code == "browser_gate_failed"
                            and browser_attempt < MAX_BROWSER_CAPTURE_ATTEMPTS
                        ):
                            continue
                        raise self._quality_error(
                            f"{exc.error_code}: {exc.diagnostic or str(exc)}"
                        ) from exc
                    fingerprint = (
                        browser_repair_fingerprint(repair_issues)
                        + ":"
                        + artifact_fingerprint(candidate, request.assistant_persona)
                    )
                    if fingerprint in seen:
                        LOGGER.warning(
                            "visual browser candidate exhausted run_id=%s fingerprint=%s",
                            run_id,
                            artifact_fingerprint(candidate, request.assistant_persona),
                        )
                        raise self._quality_error(
                            "repeated_browser_gate_fingerprint"
                        ) from exc
                    seen.add(fingerprint)
                    if browser_repair_count >= MAX_BROWSER_REPAIRS:
                        LOGGER.warning(
                            "visual browser candidate exhausted run_id=%s fingerprint=%s",
                            run_id,
                            artifact_fingerprint(candidate, request.assistant_persona),
                        )
                        raise self._quality_error(
                            "browser_gate_repair_exhausted"
                        ) from exc

                    browser_repair_count += 1
                    await self._checkpoint(run_id)
                    await self._store.append_event(
                        run_id,
                        event_type="visual_repair.started",
                        stage=Stage.MOTION_POLISH,
                        status="running",
                        message=(
                            "Исправление после браузерной проверки: попытка "
                            f"{browser_repair_count}/{MAX_BROWSER_REPAIRS}"
                        ),
                        revision=candidate.revision,
                        issues=repair_issues,
                    )
                    try:
                        repair = await engine.generate(
                            request=request,
                            stage=Stage.MOTION_POLISH,
                            revision=candidate.revision,
                            previous_artifact=candidate,
                            repair_issues=repair_issues,
                            visual_findings=(),
                            selected_direction=selected_direction,
                            composition=composition,
                            pattern_candidate_pack=pattern_candidate_pack,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as repair_exc:
                        usage = getattr(repair_exc, "usage", TokenUsage())
                        LOGGER.warning(
                            "browser gate repair failed run_id=%s attempt=%s error_code=%s",
                            run_id,
                            browser_repair_count,
                            getattr(repair_exc, "error_code", type(repair_exc).__name__),
                        )
                        await self._store.append_event(
                            run_id,
                            event_type="visual_repair.completed",
                            stage=Stage.MOTION_POLISH,
                            status="failed",
                            message=(
                                "Исправление после браузерной проверки "
                                f"{browser_repair_count} завершилось ошибкой"
                            ),
                            revision=candidate.revision,
                            usage=usage,
                            forensic_payload=_failure_forensic_evidence(
                                "browser_repair",
                                repair_exc,
                            ),
                        )
                        raise self._quality_error(
                            "browser_gate_repair_error: "
                            f"{getattr(repair_exc, 'error_code', type(repair_exc).__name__)}"
                        ) from repair_exc
                    proposed_candidate = repair.artifact
                    ignored_fields = forbidden_browser_repair_fields(
                        candidate, proposed_candidate
                    )
                    repaired_candidate = strip_reserved_runtime_attributes(
                        apply_browser_repair(candidate, proposed_candidate)
                    )
                    repair_diagnostic = repair.diagnostic
                    if ignored_fields:
                        ignored_note = (
                            "ignored_browser_repair_fields: "
                            + ",".join(ignored_fields)
                        )
                        repair_diagnostic = (
                            f"{repair_diagnostic}; {ignored_note}"
                            if repair_diagnostic
                            else ignored_note
                        )
                    await self._store.append_event(
                        run_id,
                        event_type="visual_repair.completed",
                        stage=Stage.MOTION_POLISH,
                        status="completed",
                        message=(
                            repaired_candidate.change_summary.strip()
                            or "Модель исправила проблему браузерной проверки."
                        ),
                        revision=repaired_candidate.revision,
                        usage=repair.usage,
                        diagnostic=repair_diagnostic,
                    )
                    await self._checkpoint(run_id)
                    candidate = repaired_candidate
                    await self._store.stage_visual_candidate(run_id, candidate)
                    issues = validate_artifact(
                        candidate, previous_revision=previous.revision
                    )
                    await self._record_validation(run_id, candidate, issues)
                    while issues:
                        fingerprint = (
                            validation_repair_fingerprint(issues)
                            + ":"
                            + artifact_fingerprint(candidate, request.assistant_persona)
                        )
                        if fingerprint in seen:
                            raise self._quality_error(
                                "repeated_validation_repair_fingerprint"
                            )
                        seen.add(fingerprint)
                        if validation_repair_count >= MAX_VALIDATION_REPAIRS:
                            raise self._quality_error(
                                "deterministic_regression: "
                                + "; ".join(issue.code for issue in issues)
                            )

                        validation_repair_count += 1
                        await self._checkpoint(run_id)
                        await self._store.append_event(
                            run_id,
                            event_type="visual_repair.started",
                            stage=Stage.MOTION_POLISH,
                            status="running",
                            message=(
                                "Техническое исправление после браузерной "
                                "проверки: попытка "
                                f"{validation_repair_count}/{MAX_VALIDATION_REPAIRS}"
                            ),
                            revision=candidate.revision,
                            issues=issues,
                        )
                        try:
                            repair = await engine.generate(
                                request=request,
                                stage=Stage.MOTION_POLISH,
                                revision=candidate.revision,
                                previous_artifact=candidate,
                                repair_issues=issues,
                                visual_findings=(),
                                selected_direction=selected_direction,
                                composition=composition,
                                pattern_candidate_pack=pattern_candidate_pack,
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as repair_exc:
                            usage = getattr(repair_exc, "usage", TokenUsage())
                            await self._store.append_event(
                                run_id,
                                event_type="visual_repair.completed",
                                stage=Stage.MOTION_POLISH,
                                status="failed",
                                message=(
                                    "Техническое исправление после браузерной "
                                    f"проверки {validation_repair_count} завершилось ошибкой"
                                ),
                                revision=candidate.revision,
                                usage=usage,
                                forensic_payload=_failure_forensic_evidence(
                                    "validation_repair",
                                    repair_exc,
                                ),
                            )
                            raise self._quality_error(
                                "browser_validation_repair_error: "
                                f"{getattr(repair_exc, 'error_code', type(repair_exc).__name__)}"
                            ) from repair_exc

                        proposed_candidate = repair.artifact
                        ignored_fields = forbidden_browser_repair_fields(
                            candidate, proposed_candidate
                        )
                        candidate = strip_reserved_runtime_attributes(
                            apply_browser_repair(candidate, proposed_candidate)
                        )
                        repair_diagnostic = repair.diagnostic
                        if ignored_fields:
                            ignored_note = (
                                "ignored_browser_repair_fields: "
                                + ",".join(ignored_fields)
                            )
                            repair_diagnostic = (
                                f"{repair_diagnostic}; {ignored_note}"
                                if repair_diagnostic
                                else ignored_note
                            )
                        await self._store.append_event(
                            run_id,
                            event_type="visual_repair.completed",
                            stage=Stage.MOTION_POLISH,
                            status="completed",
                            message=(
                                "Модель завершила техническое исправление "
                                "после браузерной проверки: "
                                + (
                                    candidate.change_summary.strip()
                                    or "исправлена техническая ошибка виджета"
                                )
                            ),
                            revision=candidate.revision,
                            usage=repair.usage,
                            diagnostic=repair_diagnostic,
                        )
                        await self._checkpoint(run_id)
                        await self._store.stage_visual_candidate(run_id, candidate)
                        issues = validate_artifact(
                            candidate, previous_revision=previous.revision
                        )
                        await self._record_validation(run_id, candidate, issues)
                    await self._store.stage_visual_draft(run_id, candidate)
                    cached_audit = None
                    screenshots_recorded = False
                    browser_attempt = 0
                    ai_review_attempt = 0
                    continue
                except Exception as exc:
                    usage = getattr(exc, "usage", TokenUsage())
                    await self._store.append_event(
                        run_id,
                        event_type="visual_audit.completed",
                        stage=Stage.MOTION_POLISH,
                        status="failed",
                        message="Визуальная проверка завершилась ошибкой",
                        revision=candidate.revision,
                        usage=usage,
                        forensic_payload=_failure_forensic_evidence(
                            "visual_critic",
                            exc,
                        ),
                    )
                    raise self._quality_error(
                        f"{getattr(exc, 'error_code', type(exc).__name__)}: "
                        f"{getattr(exc, 'diagnostic', None) or str(exc)}"
                    ) from exc

                await self._checkpoint(run_id)
                try:
                    if not screenshots_recorded:
                        for screenshot in audit.screenshots:
                            screenshot_blob = ForensicBlob(
                                data=screenshot.data,
                                mime_type="image/jpeg",
                                byte_count=screenshot.evidence.byte_count,
                                sha256=screenshot.evidence.sha256,
                            )
                            await self._store.append_event(
                                run_id,
                                event_type="screenshot.captured",
                                stage=Stage.MOTION_POLISH,
                                status="completed",
                                message=(
                                    "Снимок визуальной проверки: "
                                    f"{screenshot.evidence.screenshot_id} "
                                    f"({screenshot.evidence.byte_count} bytes)"
                                ),
                                revision=candidate.revision,
                                output_refs=(screenshot.evidence.screenshot_id,),
                                forensic_payload={
                                    "screenshot": screenshot.evidence.to_dict()
                                },
                                forensic_blobs=(screenshot_blob,),
                            )
                        screenshots_recorded = True
                    ai_review_attempt += 1
                    result = await critic.critique(
                        audit=audit,
                        brief=request.brief,
                        art_direction=selected_direction.art_direction,
                        assistant_persona=request.assistant_persona,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    usage = getattr(exc, "usage", TokenUsage())
                    route_error_code = getattr(exc, "error_code", None)
                    terminal_route_error = route_error_code in {
                        "route_exhausted",
                        "invalid_response",
                    }
                    LOGGER.warning(
                        "visual critic failed run_id=%s attempt=%s error_code=%s",
                        run_id,
                        ai_review_attempt,
                        getattr(exc, "error_code", type(exc).__name__),
                    )
                    if (
                        self._fail_open_on_inconclusive
                        and route_error_code
                        in {
                            "route_exhausted",
                            "invalid_response",
                            "visual_evidence_unproven",
                            "invalid_visual_critique",
                            "visual_critic_unavailable",
                            "visual_critic_timeout",
                            "visual_review_inconclusive",
                        }
                    ):
                        await self._store.append_event(
                            run_id,
                            event_type="visual_audit.completed",
                            stage=Stage.MOTION_POLISH,
                            status="completed",
                            message=(
                                "AI-критики не завершили формальный отчёт; "
                                "рабочая версия виджета сохранена"
                            ),
                            revision=candidate.revision,
                            usage=usage,
                            forensic_payload=_failure_forensic_evidence(
                                "visual_critic",
                                exc,
                            ),
                        )
                        return candidate
                    await self._store.append_event(
                        run_id,
                        event_type="visual_audit.completed",
                        stage=Stage.MOTION_POLISH,
                        status="failed",
                        message="Визуальная проверка завершилась ошибкой",
                        revision=candidate.revision,
                        # Terminal usage is persisted by worker stage.failed.
                        # Keeping it here too would double the run aggregate.
                        usage=TokenUsage() if terminal_route_error else usage,
                        forensic_payload=_failure_forensic_evidence(
                            "visual_critic",
                            exc,
                        ),
                    )
                    if terminal_route_error:
                        raise BuilderEngineError(
                            route_error_code,
                            (
                                "Сервис визуальной проверки вернул некорректный ответ"
                                if route_error_code == "invalid_response"
                                else "Сервис визуальной проверки не смог завершить запрос"
                            ),
                            diagnostic=getattr(exc, "diagnostic", None),
                            usage=usage,
                        ) from exc
                    if (
                        route_error_code
                        in {
                            "visual_evidence_unproven",
                            "invalid_visual_critique",
                            "visual_critic_unavailable",
                            "visual_critic_timeout",
                            "visual_review_inconclusive",
                        }
                        and ai_review_attempt < MAX_AI_REVIEW_ATTEMPTS
                    ):
                        retry_delay = _transient_visual_retry_delay(
                            getattr(exc, "diagnostic", None),
                            ai_review_attempt,
                        )
                        if retry_delay:
                            await asyncio.sleep(retry_delay)
                        continue
                    if getattr(exc, "error_code", None) in {
                        "visual_evidence_unproven",
                        "invalid_visual_critique",
                        "visual_critic_unavailable",
                        "visual_critic_timeout",
                        "visual_review_inconclusive",
                    }:
                        raise self._inconclusive_error(
                            f"{getattr(exc, 'error_code', type(exc).__name__)}: "
                            f"{getattr(exc, 'diagnostic', None) or str(exc)}"
                        ) from exc
                    raise self._quality_error(
                        f"{getattr(exc, 'error_code', type(exc).__name__)}: "
                        f"{getattr(exc, 'diagnostic', None) or str(exc)}"
                    ) from exc

                usage = getattr(result, "usage", TokenUsage())
                role_results = getattr(result, "role_results", {})
                role_failures = getattr(result, "role_failures", {})
                reused_roles = set(getattr(result, "reused_roles", ()))
                if isinstance(role_results, dict):
                    for role, role_result in role_results.items():
                        role_name = _critic_role_label(role)
                        role_critique = getattr(role_result, "critique", None)
                        role_findings = tuple(
                            getattr(role_critique, "findings", ())
                        )
                        reused = role in reused_roles
                        critic_evidence = _critic_forensic_evidence(
                            role,
                            role_result,
                        )
                        critic_evidence["reused"] = reused
                        await self._store.append_event(
                            run_id,
                            event_type="visual_critic.completed",
                            stage=Stage.MOTION_POLISH,
                            status="completed",
                            message=(
                                f"Критик {role_name}: "
                                + (
                                    "использован сохранённый результат предыдущей попытки; "
                                    if reused
                                    else ""
                                )
                                + str(
                                    getattr(
                                        role_critique,
                                        "summary",
                                        "проверка завершена",
                                    )
                                )
                            ),
                            revision=candidate.revision,
                            issues=visual_finding_issues(role_findings),
                            forensic_payload={
                                "critic": critic_evidence
                            },
                        )
                if isinstance(role_failures, dict):
                    for role, error_code in role_failures.items():
                        role_name = _critic_role_label(role)
                        await self._store.append_event(
                            run_id,
                            event_type="visual_critic.completed",
                            stage=Stage.MOTION_POLISH,
                            status="failed",
                            message=(
                                f"Визуальный критик {role_name} не завершил ответ; "
                                "проверяем кворум"
                            ),
                            revision=candidate.revision,
                            diagnostic=str(error_code)[:160],
                            forensic_payload={
                                "critic": {
                                    "role": _critic_role_value(role),
                                    "failure": {
                                        "error_code": str(error_code)[:160]
                                    },
                                }
                            },
                        )
                supporting_roles = getattr(result, "supporting_roles", {})
                normalized_support = (
                    dict(supporting_roles)
                    if isinstance(supporting_roles, dict)
                    else {}
                )
                findings = qualified_findings(tuple(result.critique.findings))
                await self._store.append_event(
                    run_id,
                    event_type="visual_judge.completed",
                    stage=Stage.MOTION_POLISH,
                    status="failed" if findings else "completed",
                    message=f"Судья: {result.critique.summary}",
                    revision=candidate.revision,
                    issues=visual_finding_issues(
                        findings,
                        supporting_roles=normalized_support,
                    ),
                    forensic_payload={
                        "judge": _judge_forensic_evidence(
                            result.critique,
                            normalized_support,
                        )
                    },
                )
                await self._store.append_event(
                    run_id,
                    event_type="visual_audit.completed",
                    stage=Stage.MOTION_POLISH,
                    status="completed",
                    message=result.critique.summary,
                    revision=candidate.revision,
                    usage=usage,
                    diagnostic=json.dumps(
                        result.critique.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                await self._checkpoint(run_id)
                if not findings:
                    await self._store.append_event(
                        run_id,
                        event_type="visual_audit.passed",
                        stage=Stage.MOTION_POLISH,
                        status="completed",
                        message="Финальная визуальная проверка пройдена",
                        revision=candidate.revision,
                    )
                    await self._checkpoint(run_id)
                    return candidate

                await self._store.append_event(
                    run_id,
                    event_type="visual_audit.blocked",
                    stage=Stage.MOTION_POLISH,
                    status="failed",
                    message=f"Судья подтвердил проблем: {len(findings)}",
                    revision=candidate.revision,
                    issues=visual_finding_issues(
                        findings,
                        supporting_roles=normalized_support,
                    ),
                    forensic_payload={
                        "repair_plan": {
                            "kind": "visual",
                            "findings": [
                                finding.to_dict() for finding in findings
                            ],
                            "supporting_roles": {
                                finding_id: [
                                    _critic_role_value(role) for role in roles
                                ]
                                for finding_id, roles in normalized_support.items()
                            },
                        }
                    },
                )
                if any(
                    "art_direction" in finding.artifact_fields
                    for finding in findings
                ):
                    raise self._quality_error(
                        "immutable_visual_finding_target: art_direction"
                    )
                semantic_fingerprint = visual_fingerprint(findings)
                visual_issue_rounds[semantic_fingerprint] = (
                    visual_issue_rounds.get(semantic_fingerprint, 0) + 1
                )
                if (
                    visual_issue_rounds[semantic_fingerprint]
                    > MAX_REPEATED_VISUAL_ISSUE_ROUNDS
                ):
                    raise self._quality_error(
                        "repeated_visual_issue_rounds_exhausted"
                    )
                fingerprint = (
                    semantic_fingerprint
                    + ":"
                    + artifact_fingerprint(candidate, request.assistant_persona)
                )
                if fingerprint in seen:
                    raise self._quality_error("repeated_visual_fingerprint")
                seen.add(fingerprint)
                visual_limit = min(
                    request.visual_repair_limit,
                    MAX_VISUAL_REPAIRS,
                )
                if visual_repair_count >= visual_limit:
                    raise self._quality_error("visual_repair_exhausted")

                visual_repair_count += 1
                await self._checkpoint(run_id)
                await self._store.append_event(
                    run_id,
                    event_type="visual_repair.started",
                    stage=Stage.MOTION_POLISH,
                    status="running",
                    message=(
                        "Визуальная доработка: попытка "
                        f"{visual_repair_count}/{visual_limit}"
                    ),
                    revision=candidate.revision,
                    issues=visual_finding_issues(
                        findings,
                        supporting_roles=normalized_support,
                    ),
                    forensic_payload={
                        "repair": {
                            "kind": "visual",
                            "attempt": visual_repair_count,
                            "findings": [
                                finding.to_dict() for finding in findings
                            ],
                            "supporting_roles": {
                                finding_id: [
                                    _critic_role_value(role) for role in roles
                                ]
                                for finding_id, roles in normalized_support.items()
                            },
                        }
                    },
                )
                repair_input_candidate = candidate
                try:
                    repair = await engine.generate(
                        request=request,
                        stage=Stage.MOTION_POLISH,
                        revision=candidate.revision,
                        previous_artifact=candidate,
                        repair_issues=(),
                        visual_findings=findings,
                        selected_direction=selected_direction,
                        composition=composition,
                        pattern_candidate_pack=pattern_candidate_pack,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    usage = getattr(exc, "usage", TokenUsage())
                    await self._store.append_event(
                        run_id,
                        event_type="visual_repair.completed",
                        stage=Stage.MOTION_POLISH,
                        status="failed",
                        message=(
                            f"Визуальная доработка {visual_repair_count} "
                            "завершилась ошибкой"
                        ),
                        revision=candidate.revision,
                        usage=usage,
                        forensic_payload={
                            "repair": {
                                "kind": "visual",
                                "attempt": visual_repair_count,
                                "failure": {
                                    "error_code": getattr(
                                        exc,
                                        "error_code",
                                        type(exc).__name__,
                                    ),
                                    "diagnostic": getattr(
                                        exc,
                                        "diagnostic",
                                        None,
                                    ),
                                },
                            }
                        },
                    )
                    raise self._quality_error(
                        f"visual_repair_error: "
                        f"{getattr(exc, 'error_code', type(exc).__name__)}"
                    ) from exc
                repaired_candidate = strip_reserved_runtime_attributes(
                    repair.artifact
                )
                await self._store.append_event(
                    run_id,
                    event_type="visual_repair.completed",
                    stage=Stage.MOTION_POLISH,
                    status="completed",
                    message=(
                        repaired_candidate.change_summary.strip()
                        or "Модель исправила подтверждённые визуальные проблемы."
                    ),
                    revision=repaired_candidate.revision,
                    usage=repair.usage,
                    diagnostic=repair.diagnostic,
                    forensic_payload={
                        "repair": {
                            "kind": "visual",
                            "attempt": visual_repair_count,
                            "provider_request_id": repair.provider_request_id,
                            "change_summary": repaired_candidate.change_summary,
                        }
                    },
                )
                await self._checkpoint(run_id)
                forbidden = forbidden_repair_fields(
                    candidate, repaired_candidate, findings
                )
                if forbidden:
                    raise self._quality_error(
                        "forbidden_visual_repair_fields: " + ",".join(forbidden)
                    )
                candidate = repaired_candidate
                await self._store.stage_visual_candidate(run_id, candidate)
                issues = validate_artifact(candidate, previous_revision=previous.revision)
                await self._record_validation(run_id, candidate, issues)
                while issues:
                    fingerprint = (
                        validation_repair_fingerprint(issues)
                        + ":"
                        + artifact_fingerprint(candidate, request.assistant_persona)
                    )
                    if fingerprint in seen:
                        raise self._quality_error(
                            "repeated_validation_repair_fingerprint"
                        )
                    seen.add(fingerprint)
                    if validation_repair_count >= MAX_VALIDATION_REPAIRS:
                        raise self._quality_error(
                            "deterministic_regression: "
                            + "; ".join(issue.code for issue in issues)
                        )

                    validation_repair_count += 1
                    await self._checkpoint(run_id)
                    await self._store.append_event(
                        run_id,
                        event_type="visual_repair.started",
                        stage=Stage.MOTION_POLISH,
                        status="running",
                        message=(
                            "Техническое исправление после визуальной "
                            "доработки: попытка "
                            f"{validation_repair_count}/{MAX_VALIDATION_REPAIRS}"
                        ),
                        revision=candidate.revision,
                        issues=issues,
                    )
                    try:
                        repair = await engine.generate(
                            request=request,
                            stage=Stage.MOTION_POLISH,
                            revision=candidate.revision,
                            previous_artifact=candidate,
                            repair_issues=issues,
                            visual_findings=(),
                            selected_direction=selected_direction,
                            composition=composition,
                            pattern_candidate_pack=pattern_candidate_pack,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as repair_exc:
                        usage = getattr(repair_exc, "usage", TokenUsage())
                        await self._store.append_event(
                            run_id,
                            event_type="visual_repair.completed",
                            stage=Stage.MOTION_POLISH,
                            status="failed",
                            message=(
                                "Техническое исправление после визуальной "
                                f"доработки {validation_repair_count} завершилось ошибкой"
                            ),
                            revision=candidate.revision,
                            usage=usage,
                            forensic_payload=_failure_forensic_evidence(
                                "validation_repair",
                                repair_exc,
                            ),
                        )
                        raise self._quality_error(
                            "visual_validation_repair_error: "
                            f"{getattr(repair_exc, 'error_code', type(repair_exc).__name__)}"
                        ) from repair_exc

                    proposed_candidate = repair.artifact
                    ignored_fields = forbidden_browser_repair_fields(
                        candidate, proposed_candidate
                    )
                    candidate = strip_reserved_runtime_attributes(
                        apply_browser_repair(candidate, proposed_candidate)
                    )
                    repair_diagnostic = repair.diagnostic
                    if ignored_fields:
                        ignored_note = (
                            "ignored_visual_validation_repair_fields: "
                            + ",".join(ignored_fields)
                        )
                        repair_diagnostic = (
                            f"{repair_diagnostic}; {ignored_note}"
                            if repair_diagnostic
                            else ignored_note
                        )
                    await self._store.append_event(
                        run_id,
                        event_type="visual_repair.completed",
                        stage=Stage.MOTION_POLISH,
                        status="completed",
                        message=(
                            candidate.change_summary.strip()
                            or "Модель исправила техническую ошибку после визуальной доработки."
                        ),
                        revision=candidate.revision,
                        usage=repair.usage,
                        diagnostic=repair_diagnostic,
                    )
                    await self._checkpoint(run_id)
                    await self._store.stage_visual_candidate(run_id, candidate)
                    issues = validate_artifact(
                        candidate, previous_revision=previous.revision
                    )
                    await self._record_validation(run_id, candidate, issues)

                await self._verify_visual_repair(
                    run_id=run_id,
                    findings=findings,
                    before=repair_input_candidate,
                    after=candidate,
                )
                await self._store.stage_visual_draft(run_id, candidate)
                cached_audit = None
                screenshots_recorded = False
                browser_attempt = 0
                ai_review_attempt = 0
        finally:
            if critic is not None:
                await self._close_critic(critic)


__all__ = [
    "MAX_AI_REVIEW_ATTEMPTS",
    "MAX_BROWSER_CAPTURE_ATTEMPTS",
    "MAX_BROWSER_REPAIRS",
    "MAX_VALIDATION_REPAIRS",
    "MAX_VISUAL_AUDITS",
    "MAX_VISUAL_REPAIRS",
    "MIN_REPAIR_CONFIDENCE",
    "VisualRepairGate",
    "forbidden_repair_fields",
    "qualified_findings",
    "visual_finding_issues",
    "visual_fingerprint",
]
