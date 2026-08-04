from __future__ import annotations

import base64
import binascii
import re
from typing import Protocol, cast
from uuid import UUID

from aiohttp import web

from .config import CodexBridgeConfig
from .runner import (
    CodexBridgeError,
    CodexInvalidOutput,
    CodexTimeout,
    CodexTurnRequest,
    CodexTurnResult,
    CodexUnavailable,
)


_CONVERSATION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_IMAGE_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp"}
)
_TURN_FIELDS = frozenset(
    {
        "run_id",
        "conversation_key",
        "prompt",
        "response_schema",
        "images",
        "model",
    }
)
_CONFIG_KEY = web.AppKey("bridge_config", CodexBridgeConfig)
_RUNNER_KEY = web.AppKey("codex_runner", object)


class _Runner(Protocol):
    async def run_turn(self, request: CodexTurnRequest) -> CodexTurnResult: ...

    async def finalize_run(self, run_id: str) -> tuple[str, ...]: ...


def create_app(*, config: CodexBridgeConfig, runner: _Runner) -> web.Application:
    app = web.Application(client_max_size=config.max_request_bytes)
    app[_CONFIG_KEY] = config
    app[_RUNNER_KEY] = runner
    app.router.add_get("/health", _health)
    app.router.add_post("/v1/turn", _turn)
    app.router.add_post("/v1/runs/{run_id}/complete", _complete_run)
    return app


async def _health(request: web.Request) -> web.Response:
    config = request.app[_CONFIG_KEY]
    return web.json_response(
        {
            "status": "ok",
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "max_concurrency": config.max_concurrency,
        }
    )


async def _turn(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
        turn = _parse_turn_payload(payload, request.app[_CONFIG_KEY])
    except (ValueError, TypeError, web.HTTPBadRequest):
        return _error_response("invalid_request", status=400)
    runner = cast(_Runner, request.app[_RUNNER_KEY])
    try:
        result = await runner.run_turn(turn)
    except ValueError:
        return _error_response("invalid_request", status=400)
    except CodexTimeout:
        return _error_response("generation_timeout", status=504)
    except CodexInvalidOutput:
        return _error_response("invalid_response", status=502)
    except CodexUnavailable:
        return _error_response("provider_unavailable", status=503)
    except CodexBridgeError:
        return _error_response("provider_unavailable", status=503)
    return web.json_response(
        {
            "text": result.text,
            "parsed": result.parsed,
            "usage": {
                "input_tokens": result.usage.input_tokens,
                "cached_input_tokens": result.usage.cached_input_tokens,
                "output_tokens": result.usage.output_tokens,
            },
            "thread_id": result.thread_id,
            "model": result.model,
            "duration_ms": result.duration_ms,
        }
    )


async def _complete_run(request: web.Request) -> web.Response:
    run_id = request.match_info["run_id"]
    try:
        _canonical_uuid("run_id", run_id)
        runner = cast(_Runner, request.app[_RUNNER_KEY])
        archived = await runner.finalize_run(run_id)
    except ValueError:
        return _error_response("invalid_request", status=400)
    except CodexTimeout:
        return _error_response("generation_timeout", status=504)
    except CodexBridgeError:
        return _error_response("provider_unavailable", status=503)
    return web.json_response({"archived_thread_ids": list(archived)})


def _parse_turn_payload(
    payload: object,
    config: CodexBridgeConfig,
) -> CodexTurnRequest:
    if not isinstance(payload, dict) or not set(payload).issubset(_TURN_FIELDS):
        raise ValueError("turn payload must be an allowlisted object")
    run_id = payload.get("run_id")
    conversation_key = payload.get("conversation_key")
    prompt = payload.get("prompt")
    _canonical_uuid("run_id", run_id)
    if not isinstance(conversation_key, str) or _CONVERSATION_KEY.fullmatch(
        conversation_key
    ) is None:
        raise ValueError("conversation_key is invalid")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 1_000_000:
        raise ValueError("prompt is invalid")
    schema = payload.get("response_schema")
    if schema is not None and not isinstance(schema, dict):
        raise ValueError("response_schema must be an object")
    model = payload.get("model")
    if model is not None and (
        not isinstance(model, str) or _MODEL_NAME.fullmatch(model) is None
    ):
        raise ValueError("model is invalid")
    raw_images = payload.get("images", [])
    if not isinstance(raw_images, list) or len(raw_images) > config.max_images:
        raise ValueError("images are invalid")
    images = tuple(_decode_image(image, config) for image in raw_images)
    return CodexTurnRequest(
        run_id=run_id,
        conversation_key=conversation_key,
        prompt=prompt,
        response_schema=schema,
        images=images,
        model=model,
    )


def _decode_image(payload: object, config: CodexBridgeConfig) -> bytes:
    if not isinstance(payload, dict) or set(payload) != {"media_type", "data"}:
        raise ValueError("image must contain media_type and data")
    if payload["media_type"] not in _IMAGE_MEDIA_TYPES:
        raise ValueError("unsupported image media type")
    encoded = payload["data"]
    if not isinstance(encoded, str):
        raise ValueError("image data must be base64 text")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("image data is invalid base64") from error
    if not decoded or len(decoded) > config.max_image_bytes:
        raise ValueError("image size is invalid")
    return decoded


def _canonical_uuid(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a UUID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a UUID") from error
    if str(parsed) != value.lower():
        raise ValueError(f"{name} must be canonical")
    return value


def _error_response(code: str, *, status: int) -> web.Response:
    return web.json_response({"error": {"code": code}}, status=status)
