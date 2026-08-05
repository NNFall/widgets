from __future__ import annotations

import base64
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tools.kaigo_codex_bridge.config import CodexBridgeConfig
from tools.kaigo_codex_bridge.runner import (
    CodexInvalidOutput,
    CodexTimeout,
    CodexTurnResult,
    CodexUsage,
    CodexUnavailable,
)
from tools.kaigo_codex_bridge.service import create_app


class FakeRunner:
    def __init__(self) -> None:
        self.requests = []
        self.completed_runs: list[str] = []
        self.error: Exception | None = None

    async def run_turn(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return CodexTurnResult(
            text='{"ok":true}',
            parsed={"ok": True},
            usage=CodexUsage(
                input_tokens=120,
                cached_input_tokens=20,
                output_tokens=30,
            ),
            thread_id=str(uuid4()),
            model="gpt-5.6-luna",
            duration_ms=1500,
        )

    async def finalize_run(self, run_id: str):
        self.completed_runs.append(run_id)
        if self.error is not None:
            raise self.error
        return (str(uuid4()), str(uuid4()))


def _config(tmp_path) -> CodexBridgeConfig:
    return CodexBridgeConfig(
        executable="codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_concurrency=3,
        timeout_seconds=30,
        state_root=tmp_path / "state",
        work_root=tmp_path / "work",
        socket_path=tmp_path / "bridge.sock",
    )


async def _client(tmp_path, runner: FakeRunner) -> TestClient:
    app = create_app(config=_config(tmp_path), runner=runner)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


@pytest.mark.asyncio
async def test_health_exposes_no_secrets(tmp_path) -> None:
    client = await _client(tmp_path, FakeRunner())
    try:
        response = await client.get("/health")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert payload == {
        "status": "ok",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "max_concurrency": 3,
    }


@pytest.mark.asyncio
async def test_turn_decodes_images_and_returns_normalized_result(tmp_path) -> None:
    runner = FakeRunner()
    client = await _client(tmp_path, runner)
    run_id = str(uuid4())
    image = b"\x89PNG\r\n\x1a\nvisual"
    try:
        response = await client.post(
            "/v1/turn",
            json={
                "run_id": run_id,
                "conversation_key": "visual:judge",
                "prompt": "Проверь виджет",
                "response_schema": {"type": "object"},
                "images": [
                    {
                        "media_type": "image/png",
                        "data": base64.b64encode(image).decode("ascii"),
                    }
                ],
            },
        )
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert runner.requests[0].run_id == run_id
    assert runner.requests[0].conversation_key == "visual:judge"
    assert runner.requests[0].images == (image,)
    assert runner.requests[0].response_schema == {"type": "object"}
    assert payload["parsed"] == {"ok": True}
    assert payload["usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 20,
        "output_tokens": 30,
    }
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["duration_ms"] == 1500


@pytest.mark.asyncio
async def test_turn_accepts_complete_visual_critic_evidence_set(tmp_path) -> None:
    runner = FakeRunner()
    client = await _client(tmp_path, runner)
    image = b"\x89PNG\r\n\x1a\nvisual"
    encoded = base64.b64encode(image).decode("ascii")
    try:
        response = await client.post(
            "/v1/turn",
            json={
                "run_id": str(uuid4()),
                "conversation_key": "critic:conversation_ux",
                "prompt": "Review six states and three detail crops",
                "images": [
                    {"media_type": "image/png", "data": encoded}
                    for _ in range(9)
                ],
            },
        )
    finally:
        await client.close()

    assert response.status == 200
    assert runner.requests[0].images == (image,) * 9


@pytest.mark.asyncio
async def test_turn_rejects_invalid_base64_without_running_codex(tmp_path) -> None:
    runner = FakeRunner()
    client = await _client(tmp_path, runner)
    try:
        response = await client.post(
            "/v1/turn",
            json={
                "run_id": str(uuid4()),
                "conversation_key": "build",
                "prompt": "Build",
                "images": [{"media_type": "image/png", "data": "***"}],
            },
        )
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 400
    assert payload == {"error": {"code": "invalid_request"}}
    assert runner.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (CodexTimeout("sensitive-detail-a"), 504, "generation_timeout"),
        (CodexInvalidOutput("sensitive-detail-b"), 502, "invalid_response"),
        (CodexUnavailable("sensitive-detail-c"), 503, "provider_unavailable"),
    ],
)
async def test_turn_maps_runner_errors_without_leaking_message(
    tmp_path, error, status, code
) -> None:
    runner = FakeRunner()
    runner.error = error
    client = await _client(tmp_path, runner)
    try:
        response = await client.post(
            "/v1/turn",
            json={
                "run_id": str(uuid4()),
                "conversation_key": "build",
                "prompt": "Private prompt",
            },
        )
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == status
    assert payload == {"error": {"code": code}}
    assert str(error) not in str(payload)


@pytest.mark.asyncio
async def test_complete_archives_all_run_threads(tmp_path) -> None:
    runner = FakeRunner()
    client = await _client(tmp_path, runner)
    run_id = str(uuid4())
    try:
        response = await client.post(f"/v1/runs/{run_id}/complete")
        payload = await response.json()
    finally:
        await client.close()

    assert response.status == 200
    assert runner.completed_runs == [run_id]
    assert len(payload["archived_thread_ids"]) == 2
