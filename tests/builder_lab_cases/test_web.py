import json
import unittest

from aiohttp.test_utils import TestClient, TestServer

from builder_lab.models import BuilderRequest, EngineName, RunStatus, Stage
from builder_lab.store import RunStore
from builder_lab.web import create_builder_lab_app
from tests.builder_lab_cases.test_validation import artifact


class FakeOrchestrator:
    def __init__(self, store):
        self.store = store
        self.cancelled = []

    async def start(self, request):
        return await self.store.create(request)

    async def cancel(self, run_id):
        self.cancelled.append(run_id)
        return await self.store.request_cancel(run_id)

    async def retry(self, run_id):
        snapshot = await self.store.snapshot(run_id)
        return await self.store.create(snapshot.request)

    async def close(self):
        return None


class BuilderLabWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = RunStore()
        self.orchestrator = FakeOrchestrator(self.store)
        self.app = create_builder_lab_app(
            store=self.store,
            orchestrator=self.orchestrator,
            enabled_engines=(EngineName.DIRECT, EngineName.ANTIGRAVITY),
        )
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def create_run(self):
        response = await self.client.post(
            "/api/runs",
            json={"engine": "direct", "brief": "Премиальный AI-консультант"},
        )
        self.assertEqual(response.status, 202)
        return await response.json()

    async def test_page_is_self_contained_secure_and_uses_opaque_iframe(self):
        response = await self.client.get("/")
        body = await response.text()
        self.assertEqual(response.status, 200)
        self.assertIn("Kaigo Builder Lab", body)
        self.assertIn('sandbox="allow-scripts"', body)
        self.assertNotIn("allow-same-origin", body)
        self.assertIn("new EventSource", body)
        self.assertIn("event.source", body)
        self.assertIn("Экспериментальный черновик", body)
        viewport_index = body.index('class="viewport"')
        empty_index = body.index('class="preview-empty"')
        iframe_index = body.index('<iframe id="preview"')
        self.assertLess(viewport_index, empty_index)
        self.assertLess(empty_index, iframe_index)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])

    async def test_create_snapshot_cancel_and_retry_routes(self):
        created = await self.create_run()
        run_id = created["run_id"]
        snapshot_response = await self.client.get(f"/api/runs/{run_id}")
        snapshot = await snapshot_response.json()
        self.assertEqual(snapshot["request"]["brief"], "Премиальный AI-консультант")

        cancel = await self.client.post(f"/api/runs/{run_id}/cancel")
        self.assertEqual(cancel.status, 202)
        self.assertTrue((await cancel.json())["cancel_requested"])

        retry = await self.client.post(f"/api/runs/{run_id}/retry")
        self.assertEqual(retry.status, 202)
        retried = await retry.json()
        self.assertNotEqual(retried["run_id"], run_id)

    async def test_invalid_requests_and_unknown_runs_have_public_json_errors(self):
        invalid = await self.client.post("/api/runs", json={"engine": "bad", "brief": ""})
        self.assertEqual(invalid.status, 400)
        self.assertEqual((await invalid.json())["error"]["code"], "invalid_request")
        unknown = await self.client.get("/api/runs/not-a-run")
        self.assertEqual(unknown.status, 404)
        self.assertEqual((await unknown.json())["error"]["code"], "run_not_found")

    async def test_capacity_returns_retryable_public_error(self):
        small_store = RunStore(max_runs=1)
        app = create_builder_lab_app(
            store=small_store,
            orchestrator=FakeOrchestrator(small_store),
            enabled_engines=(EngineName.DIRECT,),
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            payload = {"engine": "direct", "brief": "First active run"}
            self.assertEqual((await client.post("/api/runs", json=payload)).status, 202)
            response = await client.post("/api/runs", json=payload)
            self.assertEqual(response.status, 429)
            self.assertEqual((await response.json())["error"]["code"], "run_capacity")
        finally:
            await client.close()

    async def test_preview_requires_committed_artifact_then_returns_csp_document(self):
        created = await self.create_run()
        run_id = created["run_id"]
        missing = await self.client.get(f"/api/runs/{run_id}/preview")
        self.assertEqual(missing.status, 409)
        await self.store.commit_artifact(run_id, artifact(revision=1, stage=Stage.ART_DIRECTION))
        preview = await self.client.get(f"/api/runs/{run_id}/preview?revision=1")
        document = await preview.text()
        self.assertEqual(preview.status, 200)
        self.assertIn("kaigo-builder-preview", document)
        self.assertIn("connect-src &#x27;none&#x27;", document)
        self.assertEqual(preview.headers["Cache-Control"], "no-store")

    async def test_preview_can_replay_an_older_committed_revision(self):
        created = await self.create_run()
        run_id = created["run_id"]
        await self.store.commit_artifact(run_id, artifact(revision=1, stage=Stage.ART_DIRECTION))
        await self.store.commit_artifact(run_id, artifact(revision=2, stage=Stage.FOUNDATION))
        preview = await self.client.get(f"/api/runs/{run_id}/preview?revision=1")
        self.assertEqual(preview.status, 200)
        self.assertIn("revision: 1", await preview.text())

    async def test_sse_replays_after_last_event_id_and_closes_at_terminal(self):
        created = await self.create_run()
        run_id = created["run_id"]
        await self.store.append_event(
            run_id,
            event_type="run.failed",
            stage=None,
            status="failed",
            message="failed safely",
            error_code="provider_unavailable",
        )
        await self.store.mark_terminal(
            run_id, RunStatus.FAILED, error_code="provider_unavailable"
        )
        response = await self.client.get(
            f"/api/runs/{run_id}/events",
            headers={"Last-Event-ID": "1"},
        )
        body = await response.text()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Content-Type"], "text/event-stream")
        self.assertNotIn("id: 1\n", body)
        self.assertIn("id: 2\n", body)
        payload_line = next(line for line in body.splitlines() if line.startswith("data: "))
        payload = json.loads(payload_line[6:])
        self.assertEqual(payload["type"], "run.failed")


if __name__ == "__main__":
    unittest.main()
