import asyncio
import types
import unittest

from builder_lab.engines.base import BuilderEngineError, EngineResult
from builder_lab.models import (
    BuilderRequest,
    DirectionProposal,
    DirectionRole,
    EngineName,
    RunStatus,
    Stage,
    TokenUsage,
)
from builder_lab.orchestrator import BuilderOrchestrator
from builder_lab.store import RunStore, RunTerminal
from builder_lab.browser_audit import BrowserAuditError
from builder_lab.visual_gate import VisualRepairGate
from builder_lab.visual_models import (
    NormalizedRegion,
    VisualCategory,
    VisualCritique,
    VisualFinding,
    VisualSeverity,
    VisualVerdict,
)
from tests.builder_lab_cases.test_validation import artifact
from tests.builder_lab_cases.test_orchestrator import ScriptedEngine


def finding(
    finding_id="visual-1",
    *,
    severity=VisualSeverity.MAJOR,
    confidence=0.9,
    instruction="Keep the composer inside the panel edge.",
    artifact_fields=("css",),
):
    return VisualFinding(
        finding_id=finding_id,
        severity=severity,
        category=VisualCategory.RESPONSIVE_INTEGRITY,
        screenshot_id="mobile.after_turn_2",
        evidence="The send control crosses the visible right edge.",
        region=NormalizedRegion(x=0.7, y=0.8, width=0.2, height=0.1),
        artifact_fields=artifact_fields,
        repair_instruction=instruction,
        confidence=confidence,
    )


def critique(*findings):
    items = tuple(findings)
    return VisualCritique(
        verdict=VisualVerdict.REPAIR if any(
            item.severity in {VisualSeverity.BLOCKER, VisualSeverity.MAJOR}
            and item.confidence >= 0.75
            for item in items
        ) else VisualVerdict.PASS,
        summary="Six visual states checked.",
        findings=items,
    )


class FakeAuditor:
    def __init__(self, *, blocker=None, error=None):
        self.calls = []
        self.blocker = blocker
        self.error = error

    async def audit(self, candidate):
        self.calls.append(candidate)
        if self.error is not None:
            raise self.error
        if self.blocker is not None:
            await self.blocker.wait()
        screenshots = tuple(
            types.SimpleNamespace(
                evidence=types.SimpleNamespace(
                    screenshot_id=f"shot-{index}", byte_count=100 + index
                )
            )
            for index in range(6)
        )
        return types.SimpleNamespace(screenshots=screenshots)


class FakeCritic:
    def __init__(self, critiques, *, error=None, blocker=None):
        self.critiques = list(critiques)
        self.error = error
        self.blocker = blocker
        self.calls = []
        self.closed = False

    async def critique(self, **kwargs):
        self.calls.append(kwargs)
        if self.blocker is not None:
            await self.blocker.wait()
        if self.error is not None:
            raise self.error
        return types.SimpleNamespace(
            critique=self.critiques.pop(0),
            usage=TokenUsage(prompt_tokens=7, output_tokens=3),
        )

    async def aclose(self):
        self.closed = True


class FakeEngine:
    def __init__(self, repairs=(), *, error=None):
        self.repairs = list(repairs)
        self.error = error
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return EngineResult(
            artifact=self.repairs.pop(0),
            usage=TokenUsage(prompt_tokens=11, output_tokens=5),
        )


class VisualRepairGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = RunStore()
        self.request = BuilderRequest(
            engine=EngineName.DIRECT,
            brief="RAW editorial AI consultant",
            creativity=1.2,
            max_repairs=0,
        )
        run = await self.store.create(self.request)
        self.run_id = run.run_id
        await self.store.set_running(self.run_id)
        self.previous = artifact(revision=4, stage=Stage.CONVERSATION)
        await self.store.commit_artifact(self.run_id, self.previous)
        self.candidate = artifact(revision=5, stage=Stage.MOTION_POLISH)
        self.direction = DirectionProposal(
            proposal_id="candidate-2",
            role=DirectionRole.INTERACTION_INVENTOR,
            title="Floating note",
            art_direction="Compact black and white editorial note.",
            interaction_model="Opens on demand without covering the page.",
        )

    async def evaluate(self, auditor, critic, engine=None):
        gate = VisualRepairGate(
            store=self.store,
            audit_factory=lambda: auditor,
            critic_factory=lambda: critic,
        )
        return await gate.evaluate(
            run_id=self.run_id,
            request=self.request,
            engine=engine or FakeEngine(),
            candidate=self.candidate,
            previous=self.previous,
            selected_direction=self.direction,
        )

    async def test_initial_pass_and_minor_findings_pass_without_repair(self):
        minor = finding(
            finding_id="minor-ignored-report",
            severity=VisualSeverity.MINOR,
            confidence=1.0,
        )
        auditor = FakeAuditor()
        critic = FakeCritic([critique(minor)])

        result = await self.evaluate(auditor, critic)

        self.assertEqual(result, self.candidate)
        self.assertEqual(len(auditor.calls), 1)
        self.assertTrue(critic.closed)
        events = await self.store.events_after(self.run_id, 0)
        visual = [event for event in events if event.event_type.startswith(("visual_", "screenshot."))]
        self.assertEqual(
            [event.event_type for event in visual],
            ["visual_audit.started"]
            + ["screenshot.captured"] * 6
            + ["visual_audit.completed", "visual_audit.passed"],
        )
        self.assertEqual(sum(event.usage.total_tokens for event in visual), 10)
        completed = next(
            event for event in visual if event.event_type == "visual_audit.completed"
        )
        self.assertIn("minor-ignored-report", completed.diagnostic)

    async def test_major_finding_repairs_full_candidate_then_revalidates_and_reaudits(self):
        repaired = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            css=self.candidate.css + "\n.kaigo-widget { overflow: clip; }",
        )
        auditor = FakeAuditor()
        minor = finding(
            finding_id="minor-ignored",
            severity=VisualSeverity.MINOR,
            confidence=1.0,
        )
        critic = FakeCritic([critique(finding(), minor), critique()])
        engine = FakeEngine([repaired])

        result = await self.evaluate(auditor, critic, engine)

        self.assertEqual(result, repaired)
        self.assertEqual(len(auditor.calls), 2)
        self.assertEqual(len(engine.calls), 1)
        call = engine.calls[0]
        self.assertEqual(call["previous_artifact"], self.candidate)
        self.assertEqual(call["selected_direction"], self.direction)
        self.assertEqual(call["repair_issues"], ())
        self.assertEqual(call["visual_findings"], (finding(),))
        self.assertEqual((await self.store.visual_candidate(self.run_id)), repaired)

    async def test_structured_browser_gate_failure_repairs_then_reaudits_before_critic(self):
        repaired = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            css=self.candidate.css + "\n.kaigo-widget { overflow: clip; }",
        )
        gate_error = BrowserAuditError(
            "browser_gate_failed",
            "Виджет не прошёл проверку: desktop panel width must be 372px",
            diagnostic="private browser internals",
            failures=("desktop panel width must be 372px",),
        )

        class RepairableAuditor(FakeAuditor):
            async def audit(self, candidate):
                self.calls.append(candidate)
                if len(self.calls) == 1:
                    raise gate_error
                return await FakeAuditor().audit(candidate)

        auditor = RepairableAuditor()
        critic = FakeCritic([critique()])
        engine = FakeEngine([repaired])

        result = await self.evaluate(auditor, critic, engine)

        self.assertEqual(result, repaired)
        self.assertEqual(len(auditor.calls), 2)
        self.assertEqual(len(critic.calls), 1)
        self.assertEqual(len(engine.calls), 1)
        call = engine.calls[0]
        self.assertEqual(call["visual_findings"], ())
        self.assertEqual(call["repair_issues"][0].code, "browser_gate_failed")
        self.assertIn("desktop panel width", call["repair_issues"][0].message)
        self.assertNotIn("private browser internals", call["repair_issues"][0].message)

    async def test_repeated_browser_issue_can_repair_again_after_artifact_changes(self):
        gate_error = BrowserAuditError(
            "browser_gate_failed",
            "Widget failed: first-open transcript must not scroll",
            failures=("first-open transcript must not scroll",),
        )
        first = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            css=self.candidate.css + "\n.kaigo-widget__messages { min-height: 120px; }",
        )
        second = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            css=self.candidate.css + "\n.kaigo-widget__messages { min-height: 180px; }",
        )

        class TwiceFailingAuditor(FakeAuditor):
            async def audit(self, candidate):
                self.calls.append(candidate)
                if len(self.calls) <= 2:
                    raise gate_error
                return await FakeAuditor().audit(candidate)

        auditor = TwiceFailingAuditor()
        engine = FakeEngine([first, second])

        result = await self.evaluate(auditor, FakeCritic([critique()]), engine)

        self.assertEqual(result, second)
        self.assertEqual(len(auditor.calls), 3)
        self.assertEqual(len(engine.calls), 2)

    async def test_repeated_browser_issue_stops_when_repair_does_not_change_artifact(self):
        gate_error = BrowserAuditError(
            "browser_gate_failed",
            "Widget failed: first-open transcript must not scroll",
            failures=("first-open transcript must not scroll",),
        )

        with self.assertLogs("builder_lab.visual_gate", level="WARNING") as logs:
            with self.assertRaises(BuilderEngineError):
                await self.evaluate(
                    FakeAuditor(error=gate_error),
                    FakeCritic([critique()]),
                    FakeEngine([self.candidate]),
                )
        self.assertTrue(any("candidate=" in message for message in logs.output))

    async def test_browser_repair_error_logs_private_diagnostic_for_operator(self):
        gate_error = BrowserAuditError(
            "browser_gate_failed",
            "Widget failed: too many actions",
            failures=("too many actions",),
        )
        repair_error = BuilderEngineError(
            "provider_unavailable",
            "repair unavailable",
            diagnostic="private browser repair diagnostic",
        )

        with self.assertLogs("builder_lab.visual_gate", level="WARNING") as logs:
            with self.assertRaises(BuilderEngineError):
                await self.evaluate(
                    FakeAuditor(error=gate_error),
                    FakeCritic([critique()]),
                    FakeEngine(error=repair_error),
                )

        events = await self.store.events_after(self.run_id, 0)
        failed = [
            event
            for event in events
            if event.event_type == "visual_repair.completed"
        ]
        self.assertNotIn("private browser repair diagnostic", failed[0].message)
        self.assertTrue(
            any("private browser repair diagnostic" in message for message in logs.output)
        )

    async def test_browser_repair_discards_changes_outside_safe_patch_fields(self):
        proposed = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            art_direction="MODEL TRIED TO REPLACE THE DIRECTION",
            theme_tokens={"surface": "#ff00ff"},
            css=self.candidate.css + "\n.kaigo-widget { overflow: clip; }",
        )
        gate_error = BrowserAuditError(
            "browser_gate_failed",
            "Widget failed: desktop panel width must be 372px",
            failures=("desktop panel width must be 372px",),
        )

        class RepairableAuditor(FakeAuditor):
            async def audit(self, candidate):
                self.calls.append(candidate)
                if len(self.calls) == 1:
                    raise gate_error
                return await FakeAuditor().audit(candidate)

        auditor = RepairableAuditor()
        result = await self.evaluate(
            auditor,
            FakeCritic([critique()]),
            FakeEngine([proposed]),
        )

        self.assertEqual(result.art_direction, self.candidate.art_direction)
        self.assertEqual(result.theme_tokens, self.candidate.theme_tokens)
        self.assertEqual(result.css, proposed.css)
        self.assertEqual(auditor.calls[1], result)
        self.assertEqual((await self.store.visual_candidate(self.run_id)), result)

    async def test_browser_repair_regression_is_repaired_before_reaudit(self):
        invalid_first = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            body_html=self.candidate.body_html.replace(
                'data-region="composer" role="group" aria-label=',
                'data-region="composer" role="group" data-label=',
            ),
            css=self.candidate.css + "\n.kaigo-widget { overflow: clip; }",
        )
        invalid_second = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            body_html=invalid_first.body_html,
            css=invalid_first.css + "\n/* changed but still invalid */",
        )
        fixed = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            css=invalid_second.css,
        )
        gate_error = BrowserAuditError(
            "browser_gate_failed",
            "Widget failed: desktop panel width must be 372px",
            failures=("desktop panel width must be 372px",),
        )

        class RepairableAuditor(FakeAuditor):
            async def audit(self, candidate):
                self.calls.append(candidate)
                if len(self.calls) == 1:
                    raise gate_error
                return await FakeAuditor().audit(candidate)

        auditor = RepairableAuditor()
        engine = FakeEngine([invalid_first, invalid_second, fixed])
        result = await self.evaluate(auditor, FakeCritic([critique()]), engine)

        self.assertEqual(result, fixed)
        self.assertEqual(len(engine.calls), 3)
        self.assertIn(
            "missing_accessible_label",
            {issue.code for issue in engine.calls[1]["repair_issues"]},
        )
        self.assertIn(
            "missing_accessible_label",
            {issue.code for issue in engine.calls[2]["repair_issues"]},
        )
        self.assertEqual(len(auditor.calls), 2)

    async def test_repeated_normalized_fingerprint_stops_without_second_repair(self):
        first = finding(artifact_fields=("css", "body_html"), confidence=0.90)
        same_semantics_new_id = finding(
            finding_id="visual-2",
            artifact_fields=("body_html", "css"),
            confidence=0.91,
        )
        engine = FakeEngine([self.candidate])
        critic = FakeCritic([critique(first), critique(same_semantics_new_id)])

        with self.assertRaises(BuilderEngineError) as caught:
            await self.evaluate(FakeAuditor(), critic, engine)

        self.assertEqual(caught.exception.error_code, "visual_quality_failed")
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(len(critic.calls), 2)

    async def test_hard_limits_are_six_audits_and_five_repairs_independent_of_request_limit(self):
        findings = [
            finding(finding_id=f"visual-{index}", instruction=f"Repair instruction {index}.")
            for index in range(1, 7)
        ]
        repairs = [
            artifact(revision=5, stage=Stage.MOTION_POLISH, css=self.candidate.css + f"\n/* {i} */")
            for i in range(5)
        ]
        auditor = FakeAuditor()
        critic = FakeCritic([critique(item) for item in findings])
        engine = FakeEngine(repairs)

        with self.assertRaises(BuilderEngineError):
            await self.evaluate(auditor, critic, engine)

        self.assertEqual(len(auditor.calls), 6)
        self.assertEqual(len(critic.calls), 6)
        self.assertEqual(len(engine.calls), 5)
        self.assertEqual((await self.store.snapshot(self.run_id)).artifact.revision, 4)
        self.assertEqual((await self.store.visual_candidate(self.run_id)).revision, 5)

    async def test_deterministic_regression_fails_without_ordinary_repair(self):
        invalid = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            css="body { color: red; }",
        )
        engine = FakeEngine([invalid])

        with self.assertRaises(BuilderEngineError) as caught:
            await self.evaluate(FakeAuditor(), FakeCritic([critique(finding())]), engine)

        self.assertEqual(caught.exception.error_code, "visual_quality_failed")
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual((await self.store.snapshot(self.run_id)).artifact.revision, 4)
        self.assertEqual((await self.store.visual_candidate(self.run_id)), invalid)

    async def test_visual_repair_cannot_change_direction_or_unlisted_fields(self):
        hostile = artifact(
            revision=5,
            stage=Stage.MOTION_POLISH,
            art_direction="COMPLETELY DIFFERENT DIRECTION",
            css=self.candidate.css + "\n.kaigo-widget { overflow: clip; }",
            body_html=self.candidate.body_html.replace("AI", "UNLISTED CHANGE", 1),
        )
        engine = FakeEngine([hostile])

        with self.assertRaises(BuilderEngineError) as caught:
            await self.evaluate(
                FakeAuditor(),
                FakeCritic([critique(finding(artifact_fields=("css",)))]),
                engine,
            )

        self.assertEqual(caught.exception.error_code, "visual_quality_failed")
        self.assertIn("forbidden_visual_repair_fields", caught.exception.diagnostic)
        self.assertEqual((await self.store.visual_candidate(self.run_id)), self.candidate)

    async def test_art_direction_target_is_rejected_before_model_repair(self):
        engine = FakeEngine([self.candidate])
        direction_finding = finding(artifact_fields=("art_direction",))

        with self.assertRaises(BuilderEngineError) as caught:
            await self.evaluate(
                FakeAuditor(), FakeCritic([critique(direction_finding)]), engine
            )

        self.assertEqual(caught.exception.error_code, "visual_quality_failed")
        self.assertIn("immutable_visual_finding_target", caught.exception.diagnostic)
        self.assertEqual(engine.calls, [])

    async def test_critic_error_accounts_usage_once_and_closes_resource(self):
        error = BuilderEngineError(
            "provider_unavailable",
            "critic unavailable",
            usage=TokenUsage(prompt_tokens=13, output_tokens=2),
        )
        critic = FakeCritic([], error=error)

        with self.assertRaises(BuilderEngineError) as caught:
            await self.evaluate(FakeAuditor(), critic)

        self.assertEqual(caught.exception.error_code, "visual_quality_failed")
        self.assertTrue(critic.closed)
        events = await self.store.events_after(self.run_id, 0)
        completed = [event for event in events if event.event_type == "visual_audit.completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].usage.total_tokens, 15)

    async def test_critic_error_logs_private_diagnostic_for_server_operator(self):
        error = BuilderEngineError(
            "provider_unavailable",
            "critic unavailable",
            diagnostic="private critic diagnostic",
        )

        with self.assertLogs("builder_lab.visual_gate", level="WARNING") as logs:
            with self.assertRaises(BuilderEngineError):
                await self.evaluate(FakeAuditor(), FakeCritic([], error=error))

        events = await self.store.events_after(self.run_id, 0)
        completed = [event for event in events if event.event_type == "visual_audit.completed"]
        self.assertNotIn("private critic diagnostic", completed[0].message)
        self.assertTrue(
            any("private critic diagnostic" in message for message in logs.output)
        )

    async def test_unexpected_audit_failure_is_sanitized_as_visual_failure(self):
        critic = FakeCritic([critique()])
        with self.assertRaises(BuilderEngineError) as caught:
            await self.evaluate(
                FakeAuditor(error=RuntimeError("private browser detail")), critic
            )
        self.assertEqual(caught.exception.error_code, "visual_quality_failed")
        self.assertNotIn("private browser detail", caught.exception.public_message)
        self.assertTrue(critic.closed)
        events = await self.store.events_after(self.run_id, 0)
        completed = [event for event in events if event.event_type == "visual_audit.completed"]
        self.assertNotIn("private browser detail", completed[0].message)

    async def test_typed_browser_audit_failure_exposes_only_public_gate_message(self):
        error = BrowserAuditError(
            "browser_gate_failed",
            "Виджет не прошёл проверку: desktop panel width must be 372px",
            diagnostic="private internal browser diagnostic",
        )

        with self.assertLogs("builder_lab.visual_gate", level="WARNING") as logs:
            with self.assertRaises(BuilderEngineError):
                await self.evaluate(FakeAuditor(error=error), FakeCritic([critique()]))

        events = await self.store.events_after(self.run_id, 0)
        completed = [event for event in events if event.event_type == "visual_audit.completed"]
        self.assertIn("desktop panel width must be 372px", completed[0].message)
        self.assertNotIn("private internal browser diagnostic", completed[0].message)
        self.assertTrue(
            any("private internal browser diagnostic" in message for message in logs.output)
        )

    async def test_unstructured_browser_gate_failure_retries_without_model_repair(self):
        transient = BrowserAuditError(
            "browser_gate_failed",
            "Виджет не прошёл детерминированную браузерную проверку",
            diagnostic="private Playwright timeout detail",
        )

        class FlakyAuditor(FakeAuditor):
            async def audit(self, candidate):
                self.calls.append(candidate)
                if len(self.calls) == 1:
                    raise transient
                return await FakeAuditor().audit(candidate)

        auditor = FlakyAuditor()
        critic = FakeCritic([critique()])
        engine = FakeEngine()

        result = await self.evaluate(auditor, critic, engine)

        self.assertEqual(result, self.candidate)
        self.assertEqual(len(auditor.calls), 2)
        self.assertEqual(len(engine.calls), 0)
        self.assertEqual(len(critic.calls), 1)
        events = await self.store.events_after(self.run_id, 0)
        completed = [event for event in events if event.event_type == "visual_audit.completed"]
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[0].status, "failed")
        self.assertNotIn("private Playwright timeout detail", completed[0].message)

    async def test_unproven_critic_response_retries_without_model_repair(self):
        transient = BuilderEngineError(
            "visual_evidence_unproven",
            "critic response missed one required state marker",
            diagnostic="private state-marker diagnostic",
            usage=TokenUsage(prompt_tokens=13, output_tokens=5),
        )

        class FlakyCritic(FakeCritic):
            async def critique(self, **kwargs):
                self.calls.append(kwargs)
                if len(self.calls) == 1:
                    raise transient
                return types.SimpleNamespace(
                    critique=critique(),
                    usage=TokenUsage(prompt_tokens=7, output_tokens=3),
                )

        auditor = FakeAuditor()
        critic = FlakyCritic([])
        engine = FakeEngine()

        result = await self.evaluate(auditor, critic, engine)

        self.assertEqual(result, self.candidate)
        self.assertEqual(len(auditor.calls), 2)
        self.assertEqual(len(critic.calls), 2)
        self.assertEqual(len(engine.calls), 0)
        events = await self.store.events_after(self.run_id, 0)
        completed = [event for event in events if event.event_type == "visual_audit.completed"]
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[0].status, "failed")
        self.assertNotIn("private state-marker diagnostic", completed[0].message)

    async def test_critic_factory_failure_is_sanitized_as_visual_failure(self):
        gate = VisualRepairGate(
            store=self.store,
            audit_factory=FakeAuditor,
            critic_factory=lambda: (_ for _ in ()).throw(
                RuntimeError("private constructor detail")
            ),
        )
        with self.assertRaises(BuilderEngineError) as caught:
            await gate.evaluate(
                run_id=self.run_id,
                request=self.request,
                engine=FakeEngine(),
                candidate=self.candidate,
                previous=self.previous,
                selected_direction=self.direction,
            )
        self.assertEqual(caught.exception.error_code, "visual_quality_failed")
        self.assertNotIn("private constructor detail", caught.exception.public_message)

    async def test_hanging_critic_close_is_bounded(self):
        class HangingCloseCritic(FakeCritic):
            def __init__(self):
                super().__init__([critique()])
                self.close_cancelled = False

            async def aclose(self):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.close_cancelled = True
                    await asyncio.sleep(0.2)
                finally:
                    self.close_cancelled = True

        critic = HangingCloseCritic()
        gate = VisualRepairGate(
            store=self.store,
            audit_factory=FakeAuditor,
            critic_factory=lambda: critic,
            critic_close_timeout_seconds=0.01,
        )
        result = await asyncio.wait_for(
            gate.evaluate(
                run_id=self.run_id,
                request=self.request,
                engine=FakeEngine(),
                candidate=self.candidate,
                previous=self.previous,
                selected_direction=self.direction,
            ),
            timeout=0.05,
        )
        self.assertEqual(result, self.candidate)
        await asyncio.sleep(0)
        self.assertTrue(critic.close_cancelled)

    async def test_visual_repair_error_accounts_usage_once_and_is_visual_failure(self):
        error = BuilderEngineError(
            "quota_exceeded",
            "repair unavailable",
            usage=TokenUsage(prompt_tokens=17, output_tokens=4),
        )
        engine = FakeEngine(error=error)

        with self.assertRaises(BuilderEngineError) as caught:
            await self.evaluate(
                FakeAuditor(), FakeCritic([critique(finding())]), engine
            )

        self.assertEqual(caught.exception.error_code, "visual_quality_failed")
        events = await self.store.events_after(self.run_id, 0)
        repairs = [event for event in events if event.event_type == "visual_repair.completed"]
        self.assertEqual(len(repairs), 1)
        self.assertEqual(repairs[0].status, "failed")
        self.assertEqual(repairs[0].usage.total_tokens, 21)

    async def test_task_cancellation_during_critic_closes_resource_and_never_passes(self):
        blocker = asyncio.Event()
        critic = FakeCritic([critique()], blocker=blocker)
        task = asyncio.create_task(self.evaluate(FakeAuditor(), critic))
        while not critic.calls:
            await asyncio.sleep(0)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(critic.closed)
        events = await self.store.events_after(self.run_id, 0)
        self.assertNotIn("visual_audit.passed", [event.event_type for event in events])

    async def test_completed_critic_usage_is_recorded_before_cancel_checkpoint(self):
        store = self.store

        class CancelOnReturnCritic(FakeCritic):
            async def critique(self, **kwargs):
                result = await super().critique(**kwargs)
                await store.request_cancel(self_run_id)
                return result

        self_run_id = self.run_id
        critic = CancelOnReturnCritic([critique()])

        with self.assertRaises(asyncio.CancelledError):
            await self.evaluate(FakeAuditor(), critic)

        events = await self.store.events_after(self.run_id, 0)
        completed = [event for event in events if event.event_type == "visual_audit.completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].usage.total_tokens, 10)
        self.assertNotIn("visual_audit.passed", [event.event_type for event in events])

    async def test_completed_visual_repair_usage_is_recorded_before_cancel_checkpoint(self):
        store = self.store
        self_run_id = self.run_id

        class CancelOnReturnEngine(FakeEngine):
            async def generate(self, **kwargs):
                result = await super().generate(**kwargs)
                await store.request_cancel(self_run_id)
                return result

        repaired = artifact(revision=5, stage=Stage.MOTION_POLISH)
        engine = CancelOnReturnEngine([repaired])

        with self.assertRaises(asyncio.CancelledError):
            await self.evaluate(
                FakeAuditor(), FakeCritic([critique(finding())]), engine
            )

        events = await self.store.events_after(self.run_id, 0)
        completed = [event for event in events if event.event_type == "visual_repair.completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].usage.total_tokens, 16)
        self.assertEqual((await self.store.visual_candidate(self.run_id)), self.candidate)

    async def test_task_cancellation_during_browser_closes_per_run_critic(self):
        blocker = asyncio.Event()
        critic = FakeCritic([critique()])
        task = asyncio.create_task(
            self.evaluate(FakeAuditor(blocker=blocker), critic)
        )
        await asyncio.sleep(0)
        task.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await task

        self.assertTrue(critic.closed)

    async def test_parallel_runs_use_isolated_critic_instances(self):
        second = await self.store.create(self.request)
        await self.store.set_running(second.run_id)
        await self.store.commit_artifact(second.run_id, self.previous)
        critics = []

        def make_critic():
            value = FakeCritic([critique()])
            critics.append(value)
            return value

        gate = VisualRepairGate(
            store=self.store,
            audit_factory=FakeAuditor,
            critic_factory=make_critic,
        )
        await asyncio.gather(
            gate.evaluate(
                run_id=self.run_id,
                request=self.request,
                engine=FakeEngine(),
                candidate=self.candidate,
                previous=self.previous,
                selected_direction=self.direction,
            ),
            gate.evaluate(
                run_id=second.run_id,
                request=self.request,
                engine=FakeEngine(),
                candidate=self.candidate,
                previous=self.previous,
                selected_direction=self.direction,
            ),
        )
        self.assertEqual(len(critics), 2)
        self.assertIsNot(critics[0], critics[1])
        self.assertTrue(all(value.closed for value in critics))
        self.assertTrue(all(len(value.calls) == 1 for value in critics))


class VisualGateStoreAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_deterministic_repair_restores_motion_stage_for_terminal_event(self):
        def handler(kwargs, _count):
            if (
                kwargs["stage"] is Stage.MOTION_POLISH
                and not kwargs.get("repair_issues")
                and not kwargs.get("visual_findings")
            ):
                return EngineResult(
                    artifact=artifact(
                        revision=5,
                        stage=Stage.MOTION_POLISH,
                        css="body { color: red; }",
                    )
                )
            return None

        store = RunStore()
        orchestrator = BuilderOrchestrator(
            store=store,
            engine_factories={EngineName.DIRECT: lambda: ScriptedEngine(handler)},
            visual_audit_factory=FakeAuditor,
            visual_critic_factory=lambda: FakeCritic([critique()]),
        )
        run = await orchestrator.start(
            BuilderRequest(engine=EngineName.DIRECT, brief="motion repair stage")
        )
        await orchestrator.wait(run.run_id)
        events = await store.events_after(run.run_id, 0)
        completed = [event for event in events if event.event_type == "run.completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].stage, Stage.MOTION_POLISH)

    async def test_candidate_is_private_and_commit_is_cancel_safe(self):
        store = RunStore()
        run = await store.create(BuilderRequest(engine=EngineName.DIRECT, brief="private"))
        await store.set_running(run.run_id)
        await store.commit_artifact(run.run_id, artifact(revision=4, stage=Stage.CONVERSATION))
        candidate = artifact(revision=5, stage=Stage.MOTION_POLISH)
        await store.stage_visual_candidate(run.run_id, candidate)
        self.assertEqual((await store.snapshot(run.run_id)).artifact.revision, 4)
        self.assertEqual((await store.visual_candidate(run.run_id)).revision, 5)
        await store.request_cancel(run.run_id)
        with self.assertRaises(RunTerminal):
            await store.commit_visual_candidate(run.run_id)
        self.assertEqual((await store.snapshot(run.run_id)).artifact.revision, 4)

    async def test_append_event_rejects_terminal_run(self):
        store = RunStore()
        run = await store.create(BuilderRequest(engine=EngineName.DIRECT, brief="terminal"))
        await store.mark_terminal(run.run_id, RunStatus.FAILED, error_code="visual_quality_failed")
        with self.assertRaises(RunTerminal):
            await store.append_event(
                run.run_id,
                event_type="late.event",
                stage=None,
                status="failed",
                message="late",
            )

    async def test_cancellation_wins_atomic_race_after_visual_pass_before_commit(self):
        class CancelAtCommitStore(RunStore):
            async def commit_visual_candidate(self, run_id):
                await self.request_cancel(run_id)
                return await super().commit_visual_candidate(run_id)

        store = CancelAtCommitStore()
        engine = ScriptedEngine()
        critics = []

        def make_critic():
            value = FakeCritic([critique()])
            critics.append(value)
            return value

        orchestrator = BuilderOrchestrator(
            store=store,
            engine_factories={EngineName.DIRECT: lambda: engine},
            visual_audit_factory=FakeAuditor,
            visual_critic_factory=make_critic,
        )
        run = await orchestrator.start(
            BuilderRequest(engine=EngineName.DIRECT, brief="cancel at visual commit")
        )
        await orchestrator.wait(run.run_id)

        snapshot = await store.snapshot(run.run_id)
        self.assertEqual(snapshot.status, RunStatus.CANCELLED)
        self.assertEqual(snapshot.artifact.revision, 4)
        self.assertTrue(critics[0].closed)
        events = await store.events_after(run.run_id, 0)
        self.assertNotIn(
            5,
            [event.revision for event in events if event.event_type == "artifact.committed"],
        )

    async def test_publish_event_is_atomic_when_cancel_arrives_after_revision_five_commit(self):
        class PauseAfterPublishStore(RunStore):
            def __init__(self):
                super().__init__()
                self.published = asyncio.Event()
                self.release = asyncio.Event()

            async def commit_visual_candidate(self, run_id):
                result = await super().commit_visual_candidate(run_id)
                self.published.set()
                await self.release.wait()
                return result

        store = PauseAfterPublishStore()
        orchestrator = BuilderOrchestrator(
            store=store,
            engine_factories={EngineName.DIRECT: ScriptedEngine},
            visual_audit_factory=FakeAuditor,
            visual_critic_factory=lambda: FakeCritic([critique()]),
        )
        run = await orchestrator.start(
            BuilderRequest(engine=EngineName.DIRECT, brief="cancel after publish")
        )
        await asyncio.wait_for(store.published.wait(), timeout=2)
        self.assertTrue(await orchestrator.cancel(run.run_id))
        await orchestrator.wait(run.run_id)

        snapshot = await store.snapshot(run.run_id)
        events = await store.events_after(run.run_id, 0)
        revision_five_commits = [
            event
            for event in events
            if event.event_type == "artifact.committed" and event.revision == 5
        ]
        self.assertEqual(snapshot.artifact.revision, 5)
        self.assertEqual(len(revision_five_commits), 1)
        self.assertEqual(snapshot.status, RunStatus.CANCELLED)


if __name__ == "__main__":
    unittest.main()
