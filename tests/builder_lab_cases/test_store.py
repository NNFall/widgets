import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from builder_lab.models import (
    BuilderRequest,
    EngineName,
    RunStatus,
    Stage,
    TokenUsage,
)
from builder_lab.store import (
    ArtifactNotFound,
    RunCapacityExceeded,
    RunNotFound,
    RunStore,
    RunTerminal,
)
from tests.builder_lab_cases.test_validation import artifact


class RunStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = RunStore(ttl_seconds=60, max_runs=10)
        self.request = BuilderRequest(engine=EngineName.DIRECT, brief="Premium widget")

    async def test_create_uses_unguessable_ids_and_initial_event(self):
        first = await self.store.create(self.request)
        second = await self.store.create(self.request)
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertGreaterEqual(len(first.run_id), 24)
        events = await self.store.events_after(first.run_id, 0)
        self.assertEqual([event.sequence for event in events], [1])
        self.assertEqual(events[0].event_type, "run.created")

    async def test_events_are_append_only_and_usage_accumulates(self):
        run = await self.store.create(self.request)
        event = await self.store.append_event(
            run.run_id,
            event_type="stage.completed",
            stage=Stage.FOUNDATION,
            status="completed",
            message="done",
            usage=TokenUsage(prompt_tokens=10, output_tokens=5),
        )
        next_event = await self.store.append_event(
            run.run_id,
            event_type="artifact.validated",
            stage=Stage.FOUNDATION,
            status="completed",
            message="valid",
        )
        self.assertEqual((event.sequence, next_event.sequence), (2, 3))
        snapshot = await self.store.snapshot(run.run_id)
        self.assertEqual(snapshot.usage.total_tokens, 15)
        self.assertEqual([e.sequence for e in await self.store.events_after(run.run_id, 1)], [2, 3])

    async def test_update_request_persists_grounded_context_before_generation(self):
        run = await self.store.create(
            BuilderRequest(
                engine=EngineName.DIRECT,
                brief="Premium widget",
                source_url="https://example.com/",
            )
        )
        grounded = BuilderRequest(
            engine=EngineName.DIRECT,
            brief="Premium widget",
            source_url="https://example.com/",
            reference_context='{"visual_summary":"grounded context"}',
        )

        await self.store.update_request(run.run_id, grounded)

        self.assertEqual(
            (await self.store.snapshot(run.run_id)).request.reference_context,
            grounded.reference_context,
        )

    async def test_commits_only_monotonic_artifacts_and_returns_isolated_copy(self):
        run = await self.store.create(self.request)
        await self.store.commit_artifact(run.run_id, artifact(revision=2))
        with self.assertRaises(ValueError):
            await self.store.commit_artifact(run.run_id, artifact(revision=2))
        snapshot = await self.store.snapshot(run.run_id)
        snapshot.artifact.theme_tokens["accent"] = "changed"
        fresh = await self.store.snapshot(run.run_id)
        self.assertNotEqual(fresh.artifact.theme_tokens["accent"], "changed")

    async def test_create_seeded_starts_a_new_run_from_an_isolated_artifact(self):
        seed = artifact(revision=5, stage=Stage.MOTION_POLISH)

        run = await self.store.create_seeded(self.request, seed)

        snapshot = await self.store.snapshot(run.run_id)
        self.assertEqual(snapshot.artifact, seed)
        self.assertEqual(await self.store.artifact(run.run_id, 5), seed)
        events = await self.store.events_after(run.run_id, 0)
        self.assertEqual(
            [event.event_type for event in events],
            ["run.created", "artifact.seeded"],
        )
        snapshot.artifact.theme_tokens["accent"] = "changed"
        self.assertNotEqual(
            (await self.store.snapshot(run.run_id)).artifact.theme_tokens["accent"],
            "changed",
        )

    async def test_keeps_each_valid_revision_for_event_replay_preview(self):
        run = await self.store.create(self.request)
        first = artifact(revision=1, stage=Stage.ART_DIRECTION)
        second = artifact(revision=2, stage=Stage.FOUNDATION)
        await self.store.commit_artifact(run.run_id, first)
        await self.store.commit_artifact(run.run_id, second)
        self.assertEqual((await self.store.artifact(run.run_id, 1)).stage, Stage.ART_DIRECTION)
        self.assertEqual((await self.store.artifact(run.run_id, 2)).stage, Stage.FOUNDATION)
        with self.assertRaises(ArtifactNotFound):
            await self.store.artifact(run.run_id, 3)

    async def test_visual_commit_event_exposes_summary_and_changed_fields(self):
        run = await self.store.create(self.request)
        first = artifact(
            revision=1,
            stage=Stage.ART_DIRECTION,
            change_summary="Создана первая визуальная концепция.",
        )
        second = artifact(
            revision=2,
            stage=Stage.FOUNDATION,
            change_summary="Добавлены геометрия и интерактивное поведение.",
            javascript="document.documentElement.dataset.ready = 'yes';",
            layout_contract={"desktop": "panel 388px", "mobile": "safe inset 12px"},
        )
        await self.store.commit_artifact(run.run_id, first)
        await self.store.stage_visual_candidate(run.run_id, second)

        await self.store.commit_visual_candidate(run.run_id)

        committed = [
            event
            for event in await self.store.events_after(run.run_id, 0)
            if event.event_type == "artifact.committed"
        ]
        self.assertEqual(committed[-1].message, second.change_summary)
        self.assertEqual(
            committed[-1].changes,
            ("javascript", "layout_contract"),
        )

    async def test_waiter_wakes_for_new_events(self):
        run = await self.store.create(self.request)
        waiter = asyncio.create_task(self.store.wait_for_events(run.run_id, 1, timeout=1))
        await asyncio.sleep(0)
        await self.store.append_event(
            run.run_id,
            event_type="stage.started",
            stage=Stage.ART_DIRECTION,
            status="running",
            message="started",
        )
        events = await waiter
        self.assertEqual(events[0].sequence, 2)

    async def test_waiter_timeout_returns_empty_tuple(self):
        run = await self.store.create(self.request)
        self.assertEqual(await self.store.wait_for_events(run.run_id, 1, timeout=0.01), ())

    async def test_cancellation_and_terminal_state_are_sticky(self):
        run = await self.store.create(self.request)
        self.assertTrue(await self.store.request_cancel(run.run_id))
        self.assertFalse(await self.store.request_cancel(run.run_id))
        self.assertTrue((await self.store.snapshot(run.run_id)).cancel_requested)
        await self.store.mark_terminal(run.run_id, RunStatus.CANCELLED, elapsed_seconds=0.5)
        self.assertFalse(await self.store.request_cancel(run.run_id))
        with self.assertRaises(RunTerminal):
            await self.store.commit_artifact(run.run_id, artifact(revision=2))
        with self.assertRaises(RunTerminal):
            await self.store.mark_terminal(run.run_id, RunStatus.COMPLETED)

    async def test_failure_preserves_last_valid_artifact(self):
        run = await self.store.create(self.request)
        committed = artifact(revision=2)
        await self.store.commit_artifact(run.run_id, committed)
        await self.store.mark_terminal(
            run.run_id,
            RunStatus.FAILED,
            error_code="invalid_artifact",
            elapsed_seconds=1.2,
        )
        snapshot = await self.store.snapshot(run.run_id)
        self.assertEqual(snapshot.artifact, committed)
        self.assertEqual(snapshot.error_code, "invalid_artifact")

    async def test_ttl_and_capacity_pruning_only_remove_terminal_runs(self):
        small = RunStore(ttl_seconds=1, max_runs=2)
        first = await small.create(self.request)
        await small.mark_terminal(first.run_id, RunStatus.COMPLETED)
        second = await small.create(self.request)
        await small.mark_terminal(second.run_id, RunStatus.COMPLETED)
        third = await small.create(self.request)
        with self.assertRaises(RunNotFound):
            await small.snapshot(first.run_id)
        future = datetime.now(timezone.utc) + timedelta(seconds=2)
        removed = await small.prune(now=future)
        self.assertEqual(removed, 1)
        self.assertEqual((await small.snapshot(third.run_id)).status, RunStatus.CREATED)

    async def test_capacity_rejects_new_run_without_evicting_active_work(self):
        small = RunStore(ttl_seconds=1, max_runs=1)
        active = await small.create(self.request)
        with self.assertRaises(RunCapacityExceeded):
            await small.create(self.request)
        future = datetime.now(timezone.utc) + timedelta(seconds=2)
        self.assertEqual(await small.prune(now=future), 0)
        self.assertEqual((await small.snapshot(active.run_id)).status, RunStatus.CREATED)

    async def test_terminal_event_and_status_are_committed_atomically(self):
        run = await self.store.create(self.request)
        await self.store.finish(
            run.run_id,
            RunStatus.COMPLETED,
            event_type="run.completed",
            stage=Stage.MOTION_POLISH,
            message="done",
            revision=5,
            elapsed_seconds=1.0,
        )
        snapshot = await self.store.snapshot(run.run_id)
        events = await self.store.events_after(run.run_id, 0)
        self.assertEqual(snapshot.status, RunStatus.COMPLETED)
        self.assertEqual(events[-1].event_type, "run.completed")


if __name__ == "__main__":
    unittest.main()
