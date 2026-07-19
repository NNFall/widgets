import asyncio
import unittest

from builder_lab.engines.base import (
    BuilderEngineError,
    DirectionJudgeResult,
    DirectionProposalResult,
    EngineResult,
)
from builder_lab.models import (
    BuilderRequest,
    DirectionJudgement,
    DirectionProposal,
    DirectionRole,
    EngineName,
    RunStatus,
    Stage,
    TokenUsage,
)
from builder_lab.orchestrator import BuilderOrchestrator, DIRECT_STAGES
from builder_lab.store import RunCapacityExceeded, RunStore
from tests.builder_lab_cases.test_validation import artifact


class ScriptedEngine:
    def __init__(self, handler=None):
        self.handler = handler
        self.calls = []
        self.cancelled = False
        self.closed = False

    async def propose_direction(self, *, request, role, proposal_id):
        self.calls.append({
            "kind": "proposal",
            "request": request,
            "role": role,
            "proposal_id": proposal_id,
        })
        return DirectionProposalResult(
            proposal=DirectionProposal(
                proposal_id=proposal_id,
                role=role,
                title=f"Proposal {proposal_id}",
                art_direction="Compact editorial project note with sharp geometry.",
                interaction_model="A bounded note opens without blocking the page.",
                safeguards=("No fake actions",),
            ),
            usage=TokenUsage(prompt_tokens=2, output_tokens=1),
        )

    async def judge_directions(self, *, request, proposals):
        self.calls.append({"kind": "judge", "request": request, "proposals": proposals})
        return DirectionJudgeResult(
            judgement=DirectionJudgement(
                selected_proposal_id="candidate-2",
                rationale="Best matrix score.",
            ),
            usage=TokenUsage(prompt_tokens=3, output_tokens=1),
        )

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.handler:
            value = self.handler(kwargs, len(self.calls))
            if isinstance(value, Exception):
                raise value
            if value is not None:
                return value
        return EngineResult(
            artifact=artifact(
                revision=kwargs["revision"],
                stage=kwargs["stage"],
            ),
            usage=TokenUsage(prompt_tokens=10, output_tokens=5),
            provider_request_id=f"request-{len(self.calls)}",
        )

    async def cancel(self):
        self.cancelled = True

    async def close(self):
        self.closed = True


class BlockingEngine(ScriptedEngine):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        return EngineResult(artifact=artifact(revision=kwargs["revision"], stage=kwargs["stage"]))


class BuilderOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = RunStore()

    async def run_direct(self, engine):
        orchestrator = BuilderOrchestrator(
            store=self.store,
            engine_factories={EngineName.DIRECT: lambda: engine},
        )
        snapshot = await orchestrator.start(
            BuilderRequest(engine=EngineName.DIRECT, brief="Premium AI employee")
        )
        await orchestrator.wait(snapshot.run_id)
        return orchestrator, await self.store.snapshot(snapshot.run_id)

    async def test_direct_run_commits_each_real_stage_in_order(self):
        engine = ScriptedEngine()
        _, snapshot = await self.run_direct(engine)

        self.assertEqual(snapshot.status, RunStatus.COMPLETED)
        generation_calls = [call for call in engine.calls if "stage" in call]
        self.assertEqual([call["stage"] for call in generation_calls], list(DIRECT_STAGES))
        self.assertEqual([call["revision"] for call in generation_calls], [1, 2, 3, 4, 5])
        self.assertTrue(all(
            call["selected_direction"].proposal_id == "candidate-2"
            for call in generation_calls
        ))
        self.assertEqual(snapshot.artifact.revision, 5)
        self.assertEqual(snapshot.artifact.stage, Stage.MOTION_POLISH)
        self.assertEqual(snapshot.usage.prompt_tokens, 59)
        events = await self.store.events_after(snapshot.run_id, 0)
        committed = [event for event in events if event.event_type == "artifact.committed"]
        self.assertEqual([event.revision for event in committed], [1, 2, 3, 4, 5])
        self.assertEqual(events[-1].event_type, "run.completed")
        self.assertTrue(engine.closed)

    async def test_direction_board_event_precedes_art_direction_and_aggregates_usage(self):
        engine = ScriptedEngine()
        _, snapshot = await self.run_direct(engine)
        events = await self.store.events_after(snapshot.run_id, 0)
        event_types = [event.event_type for event in events]
        judged_index = event_types.index("direction.judged")
        art_started_index = next(
            index for index, event in enumerate(events)
            if event.event_type == "stage.started" and event.stage is Stage.ART_DIRECTION
        )
        self.assertLess(judged_index, art_started_index)
        judged = events[judged_index]
        self.assertEqual(judged.usage.prompt_tokens, 9)
        self.assertEqual(judged.usage.output_tokens, 4)
        self.assertEqual(snapshot.usage.prompt_tokens, 59)

    async def test_failed_judge_records_completed_proposal_usage(self):
        class FailedJudgeEngine(ScriptedEngine):
            async def judge_directions(self, **_kwargs):
                raise BuilderEngineError(
                    "provider_unavailable",
                    "judge offline",
                    usage=TokenUsage(prompt_tokens=4, output_tokens=2),
                )

        engine = FailedJudgeEngine()
        _, snapshot = await self.run_direct(engine)
        self.assertEqual(snapshot.status, RunStatus.FAILED)
        self.assertEqual(snapshot.error_code, "provider_unavailable")
        self.assertEqual(snapshot.usage.prompt_tokens, 10)
        events = await self.store.events_after(snapshot.run_id, 0)
        failed = [event for event in events if event.event_type == "direction.failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].usage.output_tokens, 5)

    async def test_invalid_stage_is_repaired_and_only_valid_candidate_commits(self):
        def handler(kwargs, _):
            if kwargs["stage"] is Stage.FOUNDATION and not kwargs.get("repair_issues"):
                return EngineResult(
                    artifact=artifact(
                        revision=kwargs["revision"],
                        stage=Stage.FOUNDATION,
                        css="body { color: red; }",
                    )
                )
            return None

        engine = ScriptedEngine(handler)
        _, snapshot = await self.run_direct(engine)
        self.assertEqual(snapshot.status, RunStatus.COMPLETED)
        generation_calls = [call for call in engine.calls if "stage" in call]
        stages = [call["stage"] for call in generation_calls]
        self.assertEqual(stages[:3], [Stage.ART_DIRECTION, Stage.FOUNDATION, Stage.FOUNDATION])
        repair = generation_calls[2]
        self.assertTrue(repair["repair_issues"])
        self.assertEqual(repair["revision"], 2)
        self.assertEqual(repair["stage"], Stage.FOUNDATION)
        self.assertEqual(
            (await self.store.artifact(snapshot.run_id, 2)).stage,
            Stage.FOUNDATION,
        )
        events = await self.store.events_after(snapshot.run_id, 0)
        self.assertIn("repair.started", [event.event_type for event in events])
        self.assertIn("repair.completed", [event.event_type for event in events])

    async def test_repeated_invalid_fingerprint_fails_and_preserves_last_valid(self):
        def handler(kwargs, _):
            if kwargs["stage"] in {Stage.FOUNDATION, Stage.VALIDATION}:
                return EngineResult(
                    artifact=artifact(
                        revision=kwargs["revision"],
                        stage=kwargs["stage"],
                        css="body { color: red; }",
                    )
                )
            return None

        engine = ScriptedEngine(handler)
        _, snapshot = await self.run_direct(engine)
        self.assertEqual(snapshot.status, RunStatus.FAILED)
        self.assertEqual(snapshot.error_code, "invalid_artifact")
        self.assertEqual(snapshot.artifact.revision, 1)
        generation_calls = [call for call in engine.calls if "stage" in call]
        self.assertEqual([call["stage"] for call in generation_calls], [
            Stage.ART_DIRECTION,
            Stage.FOUNDATION,
            Stage.FOUNDATION,
        ])

    async def test_provider_error_becomes_stable_failed_run(self):
        engine = ScriptedEngine(
            lambda _kwargs, _count: BuilderEngineError(
                "quota_exceeded", "Квота исчерпана", diagnostic="secret endpoint"
            )
        )
        _, snapshot = await self.run_direct(engine)
        self.assertEqual(snapshot.status, RunStatus.FAILED)
        self.assertEqual(snapshot.error_code, "quota_exceeded")
        events = await self.store.events_after(snapshot.run_id, 0)
        self.assertNotIn("secret endpoint", " ".join(event.message for event in events))

    async def test_cancellation_stops_task_and_calls_engine(self):
        engine = BlockingEngine()
        orchestrator = BuilderOrchestrator(
            store=self.store,
            engine_factories={EngineName.DIRECT: lambda: engine},
        )
        run = await orchestrator.start(
            BuilderRequest(engine=EngineName.DIRECT, brief="Cancel me")
        )
        await asyncio.wait_for(engine.started.wait(), timeout=1)
        self.assertTrue(await orchestrator.cancel(run.run_id))
        self.assertFalse(await orchestrator.cancel(run.run_id))
        await orchestrator.wait(run.run_id)
        snapshot = await self.store.snapshot(run.run_id)
        self.assertEqual(snapshot.status, RunStatus.CANCELLED)
        self.assertTrue(engine.cancelled)
        self.assertTrue(engine.closed)

    async def test_antigravity_uses_one_agent_build_stage(self):
        engine = ScriptedEngine()
        orchestrator = BuilderOrchestrator(
            store=self.store,
            engine_factories={EngineName.ANTIGRAVITY: lambda: engine},
        )
        run = await orchestrator.start(
            BuilderRequest(engine=EngineName.ANTIGRAVITY, brief="Agent build")
        )
        await orchestrator.wait(run.run_id)
        snapshot = await self.store.snapshot(run.run_id)
        self.assertEqual(snapshot.status, RunStatus.COMPLETED)
        self.assertEqual(len(engine.calls), 1)
        self.assertEqual(engine.calls[0]["stage"], Stage.AGENT_BUILD)

    async def test_retry_copies_failed_request_into_new_run(self):
        engines = [
            ScriptedEngine(lambda *_: BuilderEngineError("provider_unavailable", "offline")),
            ScriptedEngine(),
        ]
        orchestrator = BuilderOrchestrator(
            store=self.store,
            engine_factories={EngineName.DIRECT: lambda: engines.pop(0)},
        )
        first = await orchestrator.start(
            BuilderRequest(engine=EngineName.DIRECT, brief="Retry this")
        )
        await orchestrator.wait(first.run_id)
        second = await orchestrator.retry(first.run_id)
        await orchestrator.wait(second.run_id)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual((await self.store.snapshot(second.run_id)).status, RunStatus.COMPLETED)

    async def test_capacity_rejection_closes_unadmitted_engine(self):
        store = RunStore(max_runs=1)
        await store.create(BuilderRequest(engine=EngineName.DIRECT, brief="active"))
        engine = ScriptedEngine()
        orchestrator = BuilderOrchestrator(
            store=store,
            engine_factories={EngineName.DIRECT: lambda: engine},
        )
        with self.assertRaises(RunCapacityExceeded):
            await orchestrator.start(
                BuilderRequest(engine=EngineName.DIRECT, brief="rejected")
            )
        self.assertTrue(engine.closed)


if __name__ == "__main__":
    unittest.main()
