from __future__ import annotations

import asyncio
import json
from typing import Any

from google import genai
from google.genai import types

from ..models import BuilderRequest, Stage, TokenUsage, ValidationIssue, WidgetArtifact
from ..prompts import ARTIFACT_JSON_SCHEMA, build_stage_prompt
from .base import BuilderEngineError, EngineResult


def build_http_options(base_url: str) -> types.HttpOptions:
    base = base_url.strip().rstrip("/")
    if not base:
        raise ValueError("Gemini base URL must not be empty")
    api_version = "v1beta"
    for version in ("v1beta", "v1"):
        suffix = "/" + version
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)]
            api_version = version
            break
    return types.HttpOptions(
        base_url=base,
        api_version=api_version,
        timeout=180_000,
    )


def _provider_error(exc: Exception) -> BuilderEngineError:
    diagnostic = f"{type(exc).__name__}: {exc}"
    lower = diagnostic.lower()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in lower:
        return BuilderEngineError(
            "generation_timeout",
            "Gemini не завершил этап вовремя",
            diagnostic=diagnostic,
        )
    if any(token in lower for token in ("429", "resource_exhausted", "quota")):
        return BuilderEngineError(
            "quota_exceeded",
            "Квота Gemini временно исчерпана",
            diagnostic=diagnostic,
        )
    if any(token in lower for token in ("model not found", "404 model", "model_unavailable")):
        return BuilderEngineError(
            "model_unavailable",
            "Выбранная модель Gemini недоступна проекту",
            diagnostic=diagnostic,
        )
    return BuilderEngineError(
        "provider_unavailable",
        "Gemini временно недоступен",
        diagnostic=diagnostic,
    )


def _usage(response: Any) -> TokenUsage:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=int(getattr(metadata, "prompt_token_count", 0) or 0),
        output_tokens=int(getattr(metadata, "candidates_token_count", 0) or 0),
        thinking_tokens=int(getattr(metadata, "thoughts_token_count", 0) or 0),
    )


class GeminiDirectEngine:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "gemini-3.5-flash",
        base_url: str = "https://generativelanguage.googleapis.com",
        client: Any | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise BuilderEngineError(
                "missing_api_key", "Для direct-режима не настроен ключ Gemini"
            )
        self.model = model
        self._owned_client = client is None
        self._client = client or genai.Client(
            api_key=api_key.strip(),
            http_options=build_http_options(base_url),
        )

    async def generate(
        self,
        *,
        request: BuilderRequest,
        stage: Stage,
        revision: int,
        previous_artifact: WidgetArtifact | None = None,
        repair_issues: tuple[ValidationIssue, ...] = (),
    ) -> EngineResult:
        prompt = build_stage_prompt(
            request=request,
            stage=stage,
            revision=revision,
            previous_artifact=previous_artifact,
            repair_issues=repair_issues,
        )
        config = types.GenerateContentConfig(
            temperature=request.creativity,
            top_p=1.0,
            response_mime_type="application/json",
            response_json_schema=ARTIFACT_JSON_SCHEMA,
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=self.model,
                contents=prompt,
                config=config,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _provider_error(exc) from exc

        parsed = getattr(response, "parsed", None)
        try:
            if isinstance(parsed, dict):
                payload = parsed
            else:
                text = getattr(response, "text", None)
                if not text or not str(text).strip():
                    raise ValueError("empty Gemini response")
                payload = json.loads(str(text))
            artifact = WidgetArtifact.from_dict(payload)
            if artifact.revision != revision or artifact.stage != stage:
                raise ValueError("Gemini candidate violates stage or revision invariants")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise BuilderEngineError(
                "invalid_artifact",
                "Gemini вернул некорректный формат виджета",
                diagnostic=f"{type(exc).__name__}: {exc}",
            ) from exc

        return EngineResult(
            artifact=artifact,
            usage=_usage(response),
            provider_request_id=getattr(response, "response_id", None),
            diagnostic=(
                f"model={getattr(response, 'model_version', None) or self.model}"
            ),
        )

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        if not self._owned_client:
            return
        aio = getattr(self._client, "aio", None)
        if aio is not None and hasattr(aio, "aclose"):
            await aio.aclose()
        elif hasattr(self._client, "close"):
            self._client.close()
