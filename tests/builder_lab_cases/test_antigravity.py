import asyncio
import json
import re
import subprocess
import sys
import tempfile
import types as std_types
import unittest
from pathlib import Path

from builder_lab.engines.antigravity import AntigravityEngine, VALIDATE_OUTPUT_PY
from builder_lab.engines.base import BuilderEngineError
from builder_lab.models import BuilderRequest, EngineName, Stage
from tests.builder_lab_cases.test_snapshots import valid_archive
from tests.builder_lab_cases.test_validation import artifact


class FakeInteractions:
    def __init__(self, create_responses, get_responses=()):
        self.create_responses = list(create_responses)
        self.get_responses = list(get_responses)
        self.create_calls = []
        self.get_calls = []
        self.cancel_calls = []

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        response = self.create_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, id, **kwargs):
        self.get_calls.append((id, kwargs))
        response = self.get_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def cancel(self, id, **kwargs):
        self.cancel_calls.append((id, kwargs))
        return std_types.SimpleNamespace(id=id, status="cancelled")


class BlockingCreateInteractions(FakeInteractions):
    def __init__(self):
        super().__init__([])
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        return interaction("in_progress", identifier="late-interaction", environment_id=None)


class FakeClient:
    def __init__(self, interactions):
        self.aio = std_types.SimpleNamespace(interactions=interactions)


class FakeStreamResponse:
    def __init__(self, content, *, status_error=None, chunk_size=1024):
        self.content = content
        self.status_error = status_error
        self.chunk_size = chunk_size

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    async def aiter_bytes(self):
        for index in range(0, len(self.content), self.chunk_size):
            yield self.content[index : index + self.chunk_size]


class FakeStreamContext:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_):
        return False


class FakeHTTPClient:
    def __init__(self, content):
        self.content = content
        self.calls = []
        self.closed = False

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeStreamContext(FakeStreamResponse(self.content))

    async def aclose(self):
        self.closed = True


def interaction(status, *, identifier="interaction-1", environment_id="env-123", usage=None):
    return std_types.SimpleNamespace(
        id=identifier,
        status=status,
        environment_id=environment_id,
        output_text="agent prose is diagnostic only",
        usage=usage
        or std_types.SimpleNamespace(
            total_input_tokens=500,
            total_output_tokens=100,
            total_thought_tokens=50,
        ),
        steps=[],
    )


class AntigravityEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = BuilderRequest(
            engine=EngineName.ANTIGRAVITY,
            brief="Собери необычного AI-сотрудника для школы архитектуры",
        )

    async def test_creates_remote_agent_polls_downloads_and_imports_artifact(self):
        interactions = FakeInteractions(
            [interaction("in_progress", environment_id=None)],
            [interaction("completed")],
        )
        http = FakeHTTPClient(valid_archive())
        engine = AntigravityEngine(
            api_key="secret",
            base_url="https://proxy.test/protected/v1beta",
            client=FakeClient(interactions),
            download_client=http,
            poll_interval=0,
            timeout_seconds=5,
        )

        result = await engine.generate(
            request=self.request,
            stage=Stage.AGENT_BUILD,
            revision=1,
        )

        self.assertEqual(result.artifact.stage, Stage.AGENT_BUILD)
        self.assertEqual(result.usage.prompt_tokens, 500)
        self.assertEqual(result.usage.output_tokens, 100)
        self.assertEqual(result.usage.thinking_tokens, 50)
        self.assertEqual(result.provider_request_id, "interaction-1")
        call = interactions.create_calls[0]
        self.assertEqual(call["agent"], "antigravity-preview-05-2026")
        self.assertTrue(call["background"])
        self.assertNotIn("labels", call)
        self.assertEqual(call["environment"]["type"], "remote")
        self.assertEqual(call["environment"]["network"], "disabled")
        targets = {source["target"] for source in call["environment"]["sources"]}
        self.assertIn("AGENTS.md", targets)
        self.assertIn("scripts/validate_output.py", targets)
        self.assertIn("contract/widget-artifact.schema.json", targets)
        self.assertIn("BRIEF.md", targets)
        self.assertNotIn("temperature", call)
        method, url, kwargs = http.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(
            url,
            "https://proxy.test/protected/v1beta/files/environment-env-123:download",
        )
        self.assertEqual(kwargs["params"], {"alt": "media"})
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "secret")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertTrue(kwargs["follow_redirects"])

    def test_sandbox_validator_rejects_missing_regions_and_infinite_motion(self):
        candidate = artifact(revision=1, stage=Stage.AGENT_BUILD).to_dict()
        candidate["body_html"] = re.sub(
            r'\sdata-region="[^"]+"', "", candidate["body_html"]
        )
        candidate["css"] += """
.kaigo-widget .ambient { animation: agent-glow 1s infinite; }
@keyframes agent-glow { from { opacity: .5; } to { opacity: 1; } }
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            (root / "out/widget-artifact.json").write_text(
                json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
            )
            script = root / "validate_output.py"
            script.write_text(VALIDATE_OUTPUT_PY, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(script)],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "out/build-report.json").exists())

    async def test_reuses_environment_without_remounting_sources(self):
        interactions = FakeInteractions(
            [interaction("completed", identifier="one"), interaction("completed", identifier="two")]
        )
        engine = AntigravityEngine(
            api_key="secret",
            client=FakeClient(interactions),
            download_client=FakeHTTPClient(valid_archive()),
            poll_interval=0,
        )
        await engine.generate(request=self.request, stage=Stage.AGENT_BUILD, revision=1)
        await engine.generate(request=self.request, stage=Stage.AGENT_BUILD, revision=1)
        second_environment = interactions.create_calls[1]["environment"]
        self.assertEqual(second_environment, {"type": "remote", "environment_id": "env-123", "network": "disabled"})

    async def test_cancel_calls_interactions_api(self):
        interactions = FakeInteractions([interaction("in_progress")])
        engine = AntigravityEngine(
            api_key="secret",
            client=FakeClient(interactions),
            download_client=FakeHTTPClient(valid_archive()),
        )
        engine._interaction_id = "active-interaction"
        await engine.cancel()
        self.assertEqual(interactions.cancel_calls[0][0], "active-interaction")

    async def test_rejects_download_over_limit(self):
        interactions = FakeInteractions([interaction("completed")])
        engine = AntigravityEngine(
            api_key="secret",
            client=FakeClient(interactions),
            download_client=FakeHTTPClient(valid_archive()),
            max_snapshot_bytes=100,
        )
        with self.assertRaises(BuilderEngineError) as caught:
            await engine.generate(request=self.request, stage=Stage.AGENT_BUILD, revision=1)
        self.assertEqual(caught.exception.error_code, "snapshot_rejected")

    async def test_failed_and_timed_out_interactions_use_stable_errors(self):
        failed = AntigravityEngine(
            api_key="secret",
            client=FakeClient(FakeInteractions([interaction("failed")])),
            download_client=FakeHTTPClient(valid_archive()),
        )
        with self.assertRaises(BuilderEngineError) as caught:
            await failed.generate(request=self.request, stage=Stage.AGENT_BUILD, revision=1)
        self.assertEqual(caught.exception.error_code, "agent_unavailable")

        waiting = AntigravityEngine(
            api_key="secret",
            client=FakeClient(
                FakeInteractions(
                    [interaction("in_progress", environment_id=None)],
                    [interaction("in_progress", environment_id=None)] * 20,
                )
            ),
            download_client=FakeHTTPClient(valid_archive()),
            timeout_seconds=0.01,
            poll_interval=0.01,
        )
        with self.assertRaises(BuilderEngineError) as timeout:
            await waiting.generate(request=self.request, stage=Stage.AGENT_BUILD, revision=1)
        self.assertEqual(timeout.exception.error_code, "generation_timeout")
        self.assertEqual(waiting._client.aio.interactions.cancel_calls[0][0], "interaction-1")

    async def test_cancellation_during_create_recovers_id_and_cancels_remote_job(self):
        interactions = BlockingCreateInteractions()
        engine = AntigravityEngine(
            api_key="secret",
            client=FakeClient(interactions),
            download_client=FakeHTTPClient(valid_archive()),
            timeout_seconds=5,
            poll_interval=0,
        )
        generation = asyncio.create_task(
            engine.generate(request=self.request, stage=Stage.AGENT_BUILD, revision=1)
        )
        await interactions.started.wait()
        generation.cancel()
        remote_cancel = asyncio.create_task(engine.cancel())
        interactions.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await generation
        await remote_cancel
        self.assertEqual(interactions.cancel_calls[0][0], "late-interaction")

    async def test_late_create_is_watched_and_cancelled_before_close(self):
        interactions = BlockingCreateInteractions()
        engine = AntigravityEngine(
            api_key="secret",
            client=FakeClient(interactions),
            download_client=FakeHTTPClient(valid_archive()),
            timeout_seconds=5,
            poll_interval=0,
            creation_cancel_grace_seconds=0.01,
        )
        generation = asyncio.create_task(
            engine.generate(request=self.request, stage=Stage.AGENT_BUILD, revision=1)
        )
        await interactions.started.wait()
        generation.cancel()
        await asyncio.wait_for(engine.cancel(), timeout=0.2)
        self.assertEqual(interactions.cancel_calls, [])
        close = asyncio.create_task(engine.close())
        interactions.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await generation
        await asyncio.wait_for(close, timeout=1)
        self.assertEqual(interactions.cancel_calls[0][0], "late-interaction")

    async def test_requires_agent_stage_and_api_key(self):
        with self.assertRaises(BuilderEngineError) as caught:
            AntigravityEngine(api_key=None)
        self.assertEqual(caught.exception.error_code, "missing_api_key")
        engine = AntigravityEngine(
            api_key="secret",
            client=FakeClient(FakeInteractions([interaction("completed")])),
            download_client=FakeHTTPClient(valid_archive()),
        )
        with self.assertRaises(ValueError):
            await engine.generate(request=self.request, stage=Stage.FOUNDATION, revision=1)


if __name__ == "__main__":
    unittest.main()
