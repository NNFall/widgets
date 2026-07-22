from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any, Protocol

from .engines.base import BuilderEngineError, DirectBuilderEngine
from .models import (
    BuilderRequest,
    DirectionProposal,
    Stage,
    TokenUsage,
    ValidationIssue,
    WidgetArtifact,
)
from .store import RunStore
from .validation import validate_artifact
from .browser_audit import BrowserAuditError
from .visual_models import VisualFinding, VisualSeverity


MAX_VISUAL_AUDITS = 3
MAX_VISUAL_REPAIRS = 2
MIN_REPAIR_CONFIDENCE = 0.75


class BrowserAuditor(Protocol):
    async def audit(self, artifact: WidgetArtifact) -> Any: ...


class VisualCritic(Protocol):
    async def critique(
        self, *, audit: Any, brief: str, art_direction: str
    ) -> Any: ...

    async def aclose(self) -> None: ...


def qualified_findings(findings: tuple[VisualFinding, ...]) -> tuple[VisualFinding, ...]:
    return tuple(
        finding
        for finding in findings
        if finding.severity in {VisualSeverity.BLOCKER, VisualSeverity.MAJOR}
        and finding.confidence >= MIN_REPAIR_CONFIDENCE
    )


def visual_fingerprint(findings: tuple[VisualFinding, ...]) -> str:
    def normalized_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().casefold()

    normalized = []
    for finding in findings:
        region = finding.region
        normalized.append(
            {
                "category": finding.category.value,
                "screenshot_id": finding.screenshot_id,
                "evidence": normalized_text(finding.evidence),
                "region": {
                    "x": round(region.x, 3),
                    "y": round(region.y, 3),
                    "width": round(region.width, 3),
                    "height": round(region.height, 3),
                    "semantic_region": region.semantic_region,
                },
                "artifact_fields": sorted(finding.artifact_fields),
                "repair_instruction": normalized_text(finding.repair_instruction),
            }
        )
    payload = json.dumps(
        sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True)),
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
    return tuple(sorted(changed - allowed))


def browser_repair_issues(error: BrowserAuditError) -> tuple[ValidationIssue, ...]:
    if error.error_code != "browser_gate_failed" or not error.failures:
        return ()
    return tuple(
        ValidationIssue(
            code="browser_gate_failed",
            field="body_html/css",
            message=failure[:500],
        )
        for failure in error.failures[:12]
    )


def browser_repair_fingerprint(issues: tuple[ValidationIssue, ...]) -> str:
    normalized = "\n".join(
        re.sub(r"\s+", " ", issue.message).strip().casefold() for issue in issues
    )
    return "browser:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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
    return tuple(sorted(changed - {"body_html", "css", "suggested_actions"}))


class VisualRepairGate:
    """Keep revision 5 private until browser and Gemini visual checks pass."""

    _cleanup_orphans: set[asyncio.Task[Any]] = set()

    def __init__(
        self,
        *,
        store: RunStore,
        audit_factory: Callable[[], BrowserAuditor],
        critic_factory: Callable[[], VisualCritic],
        critic_close_timeout_seconds: float = 5.0,
    ) -> None:
        if not 0 < critic_close_timeout_seconds <= 30:
            raise ValueError("critic_close_timeout_seconds is invalid")
        self._store = store
        self._audit_factory = audit_factory
        self._critic_factory = critic_factory
        self._critic_close_timeout_seconds = critic_close_timeout_seconds

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
                f"Детерминированная проверка после visual repair нашла ошибок: {len(issues)}"
                if issues
                else "Visual repair прошёл детерминированную проверку"
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

    async def evaluate(
        self,
        *,
        run_id: str,
        request: BuilderRequest,
        engine: DirectBuilderEngine,
        candidate: WidgetArtifact,
        previous: WidgetArtifact,
        selected_direction: DirectionProposal,
    ) -> WidgetArtifact:
        if candidate.stage is not Stage.MOTION_POLISH or candidate.revision != 5:
            raise ValueError("visual gate accepts only motion_polish revision 5")
        if previous.revision != 4:
            raise ValueError("visual gate requires committed revision 4")

        await self._store.stage_visual_candidate(run_id, candidate)
        critic: VisualCritic | None = None
        seen: set[str] = set()
        repair_count = 0
        try:
            try:
                critic = self._critic_factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise self._quality_error(
                    f"visual_critic_init: {type(exc).__name__}"
                ) from exc
            for audit_attempt in range(1, MAX_VISUAL_AUDITS + 1):
                await self._checkpoint(run_id)
                await self._store.append_event(
                    run_id,
                    event_type="visual_audit.started",
                    stage=Stage.MOTION_POLISH,
                    status="running",
                    message=f"Финальный visual audit: попытка {audit_attempt}",
                    revision=candidate.revision,
                )
                try:
                    audit = await self._audit_factory().audit(candidate)
                except asyncio.CancelledError:
                    raise
                except BrowserAuditError as exc:
                    await self._store.append_event(
                        run_id,
                        event_type="visual_audit.completed",
                        stage=Stage.MOTION_POLISH,
                        status="failed",
                        message=str(exc)[:1_000],
                        revision=candidate.revision,
                    )
                    repair_issues = browser_repair_issues(exc)
                    if not repair_issues:
                        raise self._quality_error(
                            f"{exc.error_code}: {exc.diagnostic or str(exc)}"
                        ) from exc
                    fingerprint = browser_repair_fingerprint(repair_issues)
                    if fingerprint in seen:
                        raise self._quality_error(
                            "repeated_browser_gate_fingerprint"
                        ) from exc
                    seen.add(fingerprint)
                    if (
                        repair_count >= MAX_VISUAL_REPAIRS
                        or audit_attempt >= MAX_VISUAL_AUDITS
                    ):
                        raise self._quality_error(
                            "browser_gate_repair_exhausted"
                        ) from exc

                    repair_count += 1
                    await self._checkpoint(run_id)
                    await self._store.append_event(
                        run_id,
                        event_type="visual_repair.started",
                        stage=Stage.MOTION_POLISH,
                        status="running",
                        message=f"Browser gate repair: попытка {repair_count}",
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
                            message=f"Browser gate repair {repair_count} завершился ошибкой",
                            revision=candidate.revision,
                            usage=usage,
                        )
                        raise self._quality_error(
                            "browser_gate_repair_error: "
                            f"{getattr(repair_exc, 'error_code', type(repair_exc).__name__)}"
                        ) from repair_exc
                    repaired_candidate = repair.artifact
                    await self._store.append_event(
                        run_id,
                        event_type="visual_repair.completed",
                        stage=Stage.MOTION_POLISH,
                        status="completed",
                        message=f"Модель завершила browser gate repair {repair_count}",
                        revision=repaired_candidate.revision,
                        usage=repair.usage,
                        diagnostic=repair.diagnostic,
                    )
                    await self._checkpoint(run_id)
                    forbidden = forbidden_browser_repair_fields(
                        candidate, repaired_candidate
                    )
                    if forbidden:
                        raise self._quality_error(
                            "forbidden_browser_repair_fields: "
                            + ",".join(forbidden)
                        )
                    candidate = repaired_candidate
                    await self._store.stage_visual_candidate(run_id, candidate)
                    issues = validate_artifact(
                        candidate, previous_revision=previous.revision
                    )
                    await self._record_validation(run_id, candidate, issues)
                    if issues:
                        raise self._quality_error(
                            "deterministic_regression: "
                            + "; ".join(issue.code for issue in issues)
                        )
                    continue
                except Exception as exc:
                    usage = getattr(exc, "usage", TokenUsage())
                    await self._store.append_event(
                        run_id,
                        event_type="visual_audit.completed",
                        stage=Stage.MOTION_POLISH,
                        status="failed",
                        message="Visual audit завершился ошибкой",
                        revision=candidate.revision,
                        usage=usage,
                    )
                    raise self._quality_error(
                        f"{getattr(exc, 'error_code', type(exc).__name__)}: "
                        f"{getattr(exc, 'diagnostic', None) or str(exc)}"
                    ) from exc

                await self._checkpoint(run_id)
                try:
                    for screenshot in audit.screenshots:
                        await self._store.append_event(
                            run_id,
                            event_type="screenshot.captured",
                            stage=Stage.MOTION_POLISH,
                            status="completed",
                            message=(
                                "Снимок visual audit: "
                                f"{screenshot.evidence.screenshot_id} "
                                f"({screenshot.evidence.byte_count} bytes)"
                            ),
                            revision=candidate.revision,
                        )
                    result = await critic.critique(
                        audit=audit,
                        brief=request.brief,
                        art_direction=selected_direction.art_direction,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    usage = getattr(exc, "usage", TokenUsage())
                    await self._store.append_event(
                        run_id,
                        event_type="visual_audit.completed",
                        stage=Stage.MOTION_POLISH,
                        status="failed",
                        message="Visual audit завершился ошибкой",
                        revision=candidate.revision,
                        usage=usage,
                    )
                    raise self._quality_error(
                        f"{getattr(exc, 'error_code', type(exc).__name__)}: "
                        f"{getattr(exc, 'diagnostic', None) or str(exc)}"
                    ) from exc

                usage = getattr(result, "usage", TokenUsage())
                await self._store.append_event(
                    run_id,
                    event_type="visual_audit.completed",
                    stage=Stage.MOTION_POLISH,
                    status="completed",
                    message="Browser evidence проверен Gemini visual critic",
                    revision=candidate.revision,
                    usage=usage,
                    diagnostic=json.dumps(
                        result.critique.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                await self._checkpoint(run_id)
                findings = qualified_findings(tuple(result.critique.findings))
                if not findings:
                    await self._store.append_event(
                        run_id,
                        event_type="visual_audit.passed",
                        stage=Stage.MOTION_POLISH,
                        status="completed",
                        message="Финальный visual audit пройден",
                        revision=candidate.revision,
                    )
                    await self._checkpoint(run_id)
                    return candidate

                await self._store.append_event(
                    run_id,
                    event_type="visual_audit.blocked",
                    stage=Stage.MOTION_POLISH,
                    status="failed",
                    message=f"Visual critic нашёл значимых проблем: {len(findings)}",
                    revision=candidate.revision,
                )
                if any(
                    "art_direction" in finding.artifact_fields
                    for finding in findings
                ):
                    raise self._quality_error(
                        "immutable_visual_finding_target: art_direction"
                    )
                fingerprint = visual_fingerprint(findings)
                if fingerprint in seen:
                    raise self._quality_error("repeated_visual_fingerprint")
                seen.add(fingerprint)
                if repair_count >= MAX_VISUAL_REPAIRS or audit_attempt >= MAX_VISUAL_AUDITS:
                    raise self._quality_error("visual_repair_exhausted")

                repair_count += 1
                await self._checkpoint(run_id)
                await self._store.append_event(
                    run_id,
                    event_type="visual_repair.started",
                    stage=Stage.MOTION_POLISH,
                    status="running",
                    message=f"Visual repair: попытка {repair_count}",
                    revision=candidate.revision,
                )
                try:
                    repair = await engine.generate(
                        request=request,
                        stage=Stage.MOTION_POLISH,
                        revision=candidate.revision,
                        previous_artifact=candidate,
                        repair_issues=(),
                        visual_findings=findings,
                        selected_direction=selected_direction,
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
                        message=f"Visual repair {repair_count} завершился ошибкой",
                        revision=candidate.revision,
                        usage=usage,
                    )
                    raise self._quality_error(
                        f"visual_repair_error: "
                        f"{getattr(exc, 'error_code', type(exc).__name__)}"
                    ) from exc
                repaired_candidate = repair.artifact
                await self._store.append_event(
                    run_id,
                    event_type="visual_repair.completed",
                    stage=Stage.MOTION_POLISH,
                    status="completed",
                    message=f"Модель завершила visual repair {repair_count}",
                    revision=repaired_candidate.revision,
                    usage=repair.usage,
                    diagnostic=repair.diagnostic,
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
                if issues:
                    raise self._quality_error(
                        "deterministic_regression: "
                        + "; ".join(issue.code for issue in issues)
                    )

            raise self._quality_error("visual_repair_exhausted")
        finally:
            if critic is not None:
                await self._close_critic(critic)


__all__ = [
    "MAX_VISUAL_AUDITS",
    "MAX_VISUAL_REPAIRS",
    "MIN_REPAIR_CONFIDENCE",
    "VisualRepairGate",
    "forbidden_repair_fields",
    "qualified_findings",
    "visual_fingerprint",
]
