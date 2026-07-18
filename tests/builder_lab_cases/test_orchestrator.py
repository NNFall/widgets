import asyncio
import unittest

from builder_lab.engines.base import BuilderEngineError, EngineResult
from builder_lab.models import (
    BuilderRequest,
    EngineName,
    RunStatus,
    Stage,
    TokenUsage,
)
from builder_lab.orchestrator import BuilderOrchestrator, DIRECT_STAGES
from builder_lab.store import RunStore
from tests.builder_lab_cases.test_validation import artifact


class ScriptedEngine:
    def __init__(self, handler=None):
        self.handler = handler
        self.calls = []
        self.cancelled = False
        self.closed = False

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
        self.assertEqual([call["stage"] for call in engine.calls], list(DIRECT_STAGES))
        self.assertEqual([call["revision"] for call in engine.calls], [1, 2, 3, 4, 5])
        self.assertEqual(snapshot.artifact.revision, 5)
        self.assertEqual(snapshot.artifact.stage, Stage.MOTION_POLISH)
        self.assertEqual(snapshot.usage.prompt_tokens, 50)
        events = await self.store.events_after(snapshot.run_id, 0)
        committed = [event for event in events if event.event_type == "artifact.committed"]
        self.assertEqual([event.revision for event in committed], [1, 2, 3, 4, 5])
        self.assertEqual(events[-1].event_type, "run.completed")
        self.assertTrue(engine.closed)

    async def test_invalid_stage_is_repaired_and_only_valid_candidate_commits(self):
        def handler(kwargs, _):
            if kwargs["stage"] is Stage.FOUNDATION:
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
        stages = [call["stage"] for call in engine.calls]
        self.assertEqual(stages[:3], [Stage.ART_DIRECTION, Stage.FOUNDATION, Stage.VALIDATION])
        repair = engine.calls[2]
        self.assertTrue(repair["repair_issues"])
        self.assertEqual(repair["revision"], 2)
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
        self.assertEqual([call["stage"] for call in engine.calls], [
            Stage.ART_DIRECTION,
            Stage.FOUNDATION,
            Stage.VALIDATION,
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


if __name__ == "__main__":
    unittest.main()
