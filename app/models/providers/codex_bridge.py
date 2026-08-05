from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from collections.abc import Mapping
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any
from uuid import UUID

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderCapabilities,
    ProviderPermissionDenied,
    ProviderTimeout,
    ProviderUnavailable,
    UnsupportedModelRequest,
)


_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_CRITIC_ROLES = frozenset(
    {"conversation_ux", "brand_motion", "adversarial_customer"}
)


class CodexBridgeProvider:
    capabilities = ProviderCapabilities(images=True, structured_output=True)

    def __init__(
        self,
        *,
        socket_path: str,
        timeout_seconds: float = 900.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not socket_path.strip() or not (
            PurePosixPath(socket_path).is_absolute()
            or PureWindowsPath(socket_path).is_absolute()
        ):
            raise ValueError("Codex bridge socket path must be absolute")
        if timeout_seconds <= 0:
            raise ValueError("Codex bridge timeout must be positive")
        self._timeout_seconds = timeout_seconds
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url="http://codex-bridge",
            transport=httpx.AsyncHTTPTransport(uds=socket_path),
            timeout=timeout_seconds,
        )

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        run_id = _run_id(request.metadata)
        if request.response_schema is not None:
            try:
                Draft202012Validator.check_schema(request.response_schema)
            except SchemaError as error:
                raise ValueError("response schema is invalid") from error
        payload: dict[str, Any] = {
            "run_id": run_id,
            "conversation_key": conversation_key_for_request(request.metadata),
            "prompt": request.prompt,
            "images": [
                {
                    "media_type": _image_media_type(image),
                    "data": base64.b64encode(image).decode("ascii"),
                }
                for image in request.images
            ],
            "model": model,
        }
        if request.response_schema is not None:
            payload["response_schema"] = dict(request.response_schema)
        response = await self._post("/v1/turn", payload)
        _raise_for_status(response)
        return _normalize_response(
            response,
            requested_model=model,
            run_id=run_id,
            response_schema=request.response_schema,
        )

    async def finalize_run(self, run_id: str) -> tuple[str, ...]:
        _validate_uuid(run_id)
        response = await self._post(f"/v1/runs/{run_id}/complete", None)
        _raise_for_status(response)
        try:
            body = response.json()
            raw_threads = body["archived_thread_ids"]
            if not isinstance(raw_threads, list):
                raise TypeError
            threads = tuple(str(UUID(value)) for value in raw_threads)
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidModelResponse(
                "Codex bridge returned invalid archive confirmation"
            ) from error
        return threads

    async def _post(
        self,
        path: str,
        payload: Mapping[str, Any] | None,
    ) -> httpx.Response:
        try:
            return await self._client.post(
                f"http://codex-bridge{path}",
                json=payload,
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderTimeout("Codex bridge request timed out") from error
        except httpx.RequestError as error:
            raise ProviderUnavailable(
                "Codex bridge is temporarily unavailable"
            ) from error

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()


def conversation_key_for_request(metadata: Mapping[str, Any]) -> str:
    role = metadata.get("_kaigo_role")
    role = role if isinstance(role, str) else ""
    stage = _safe_segment(metadata.get("_kaigo_stage") or "unknown")
    if role == "direction_candidate":
        return "direction:" + _safe_segment(metadata.get("_kaigo_candidate_id"))
    if role == "direction_judge":
        return "direction:judge"
    if role in _CRITIC_ROLES:
        persona = metadata.get("_kaigo_persona") or role
        return "critic:" + _safe_segment(persona)
    if role == "visual_judge":
        return "visual:judge"
    if role == "repair":
        return "repair:" + stage
    if role == "code_review":
        return "repair:verify:" + stage
    if role == "reference_analyst":
        return "reference"
    return "build:" + stage


def _run_id(metadata: Mapping[str, Any]) -> str:
    value = metadata.get("_kaigo_run_id")
    if not isinstance(value, str):
        raise UnsupportedModelRequest("Codex bridge requires builder run lineage")
    try:
        _validate_uuid(value)
    except ValueError as error:
        raise UnsupportedModelRequest(
            "Codex bridge requires valid builder run lineage"
        ) from error
    return value


def _safe_segment(value: object) -> str:
    if isinstance(value, str) and _SAFE_SEGMENT.fullmatch(value):
        return value
    encoded = str(value).encode("utf-8", "replace")
    return "id-" + hashlib.sha256(encoded).hexdigest()[:16]


def _validate_uuid(value: str) -> None:
    parsed = UUID(value)
    if str(parsed) != value.lower():
        raise ValueError("UUID must be canonical")


def _image_media_type(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    raise UnsupportedModelRequest("Codex bridge received an unsupported image")


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    code = None
    try:
        body = response.json()
        if isinstance(body, Mapping):
            error = body.get("error")
            if isinstance(error, Mapping) and isinstance(error.get("code"), str):
                code = error["code"]
    except (TypeError, ValueError):
        pass
    if response.status_code in {401, 403}:
        raise ProviderPermissionDenied("Codex bridge request is not permitted")
    if code == "generation_timeout" or response.status_code == 504:
        raise ProviderTimeout("Codex bridge generation timed out")
    if code == "invalid_response" or response.status_code == 502:
        raise InvalidModelResponse("Codex bridge returned an invalid response")
    raise ProviderUnavailable("Codex bridge is temporarily unavailable")


def _normalize_response(
    response: httpx.Response,
    *,
    requested_model: str,
    run_id: str,
    response_schema: Mapping[str, Any] | None,
) -> ModelResponse:
    try:
        body = response.json()
        if not isinstance(body, Mapping):
            raise TypeError
        text = body["text"]
        parsed = body.get("parsed")
        thread_id = body["thread_id"]
        actual_model = body.get("model") or requested_model
        duration_ms = body["duration_ms"]
        usage = _normalize_usage(body["usage"])
        if not isinstance(text, str) or not text.strip():
            raise TypeError
        _validate_uuid(thread_id)
        if not isinstance(actual_model, str) or not actual_model.strip():
            raise TypeError
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
            raise TypeError
        if duration_ms < 0:
            raise TypeError
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidModelResponse("Codex bridge returned malformed data") from error
    if response_schema is not None:
        if not isinstance(parsed, (dict, list)):
            raise InvalidModelResponse(
                "Codex bridge structured response is not JSON data",
                usage=usage,
                request_id=thread_id,
                actual_provider="codex_cli",
                actual_model=actual_model,
            )
        try:
            Draft202012Validator(response_schema).validate(parsed)
        except ValidationError as error:
            raise InvalidModelResponse(
                "Codex bridge structured response does not match the schema",
                usage=usage,
                request_id=thread_id,
                actual_provider="codex_cli",
                actual_model=actual_model,
            ) from error
    else:
        parsed = None
    return ModelResponse(
        text=text,
        parsed=parsed,
        usage=usage,
        request_id=thread_id,
        raw={
            "run_id": run_id,
            "thread_id": thread_id,
            "duration_ms": duration_ms,
        },
        actual_provider="codex_cli",
        actual_model=actual_model,
    )


def _normalize_usage(raw: object) -> ModelUsage:
    if not isinstance(raw, Mapping):
        raise TypeError("usage must be an object")
    input_tokens = _token_count(raw.get("input_tokens"))
    cached = _token_count(raw.get("cached_input_tokens"))
    output = _token_count(raw.get("output_tokens"))
    if cached > input_tokens:
        raise ValueError("cached input exceeds total input")
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output,
        cache_read_tokens=cached,
    )


def _token_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("token count must be a non-negative integer")
    return value
