from __future__ import annotations

import asyncio
import json
from typing import Any

from google import genai
from google.genai import types

from ..model_config import generation_policy, normalize_thinking_level
from ..models import (
    BuilderRequest,
    DirectionJudgement,
    DirectionProposal,
    DirectionRole,
    Stage,
    TokenUsage,
    ValidationIssue,
    WidgetArtifact,
)
from ..visual_models import VisualFinding
from ..prompts import (
    ARTIFACT_JSON_SCHEMA,
    DIRECTION_JUDGE_JSON_SCHEMA,
    DIRECTION_PROPOSAL_JSON_SCHEMA,
    build_direction_judge_prompt,
    build_direction_proposal_prompt,
    build_stage_prompt,
)
from .base import (
    BuilderEngineError,
    DirectionJudgeResult,
    DirectionProposalResult,
    EngineResult,
)


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


def build_low_thinking_config(
    model: str,
    *,
    include_thoughts: bool | None = None,
) -> types.ThinkingConfig | None:
    """Backward-compatible low-thinking policy used by older callers."""

    return generation_policy(
        model,
        "low",
        include_thoughts=include_thoughts,
    ).thinking_config


_GEMINI_25_SCHEMA_CONSTRAINTS = frozenset(
    {
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }
)


def build_provider_json_schema(schema: dict[str, Any], model: str) -> dict[str, Any]:
    """Keep strict schemas for Gemini 3.x and trim serving-state constraints for 2.5."""

    normalized = model.strip().lower().removeprefix("models/")
    if not normalized.startswith("gemini-2.5-"):
        return schema

    def simplify(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: simplify(item)
                for key, item in value.items()
                if key not in _GEMINI_25_SCHEMA_CONSTRAINTS
            }
        if isinstance(value, list):
            return [simplify(item) for item in value]
        return value

    return simplify(schema)


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


def _response_payload(response: Any) -> dict[str, Any]:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    text = getattr(response, "text", None)
    if not text or not str(text).strip():
        raise ValueError("empty Gemini response")
    payload = json.loads(str(text))
    if not isinstance(payload, dict):
        raise ValueError("Gemini JSON response must be an object")
    return payload


def _require_exact_keys(payload: dict[str, Any], expected: frozenset[str]) -> None:
    if set(payload) != expected:
        raise ValueError("Gemini JSON response fields do not match the contract")


class GeminiDirectEngine:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = "gemini-3.6-flash",
        thinking_level: str = "high",
        base_url: str = "https://generativelanguage.googleapis.com",
        client: Any | None = None,
    ) -> None:
        if not api_key or not api_key.strip():
            raise BuilderEngineError(
                "missing_api_key", "Для direct-режима не настроен ключ Gemini"
            )
        self.model = model
        self.thinking_level = normalize_thinking_level(thinking_level)
        self._owned_client = client is None
        self._client = client or genai.Client(
            api_key=api_key.strip(),
            http_options=build_http_options(base_url),
        )

    async def _generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
    ) -> Any:
        policy = generation_policy(
            self.model,
            self.thinking_level,
            temperature=temperature,
        )
        config = types.GenerateContentConfig(
            **policy.sampling_kwargs,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=build_provider_json_schema(schema, self.model),
            tools=[],
            thinking_config=policy.thinking_config,
        )
        retry_delays = (0.5, 1.5, 3.0, 5.0)
        for attempt in range(len(retry_delays) + 1):
            try:
                return await self._client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = _provider_error(exc)
                retryable = error.error_code in {
                    "provider_unavailable",
                    "generation_timeout",
                }
                if not retryable or attempt == len(retry_delays):
                    raise error from exc
                await asyncio.sleep(retry_delays[attempt])
        raise AssertionError("unreachable provider retry loop")

    async def propose_direction(
        self,
        *,
        request: BuilderRequest,
        role: DirectionRole,
        proposal_id: str,
    ) -> DirectionProposalResult:
        prompt = build_direction_proposal_prompt(request=request, role=role)
        total_usage = TokenUsage()
        for attempt in range(3):
            attempt_prompt = prompt
            if attempt:
                attempt_prompt += (
                    "\nCORRECTION: The previous JSON violated one or more field budgets. "
                    "Return a fresh complete proposal and keep every field within the exact "
                    "numeric limits above."
                )
            response = await self._generate_structured(
                prompt=attempt_prompt,
                schema=DIRECTION_PROPOSAL_JSON_SCHEMA,
                temperature=request.creativity,
                max_output_tokens=1_600,
            )
            total_usage = total_usage + _usage(response)
            try:
                payload = _response_payload(response)
                _require_exact_keys(
                    payload,
                    frozenset(
                        {"title", "art_direction", "interaction_model", "safeguards"}
                    ),
                )
                safeguards = payload["safeguards"]
                if not isinstance(safeguards, list):
                    raise ValueError("direction safeguards must be an array")
                proposal = DirectionProposal(
                    proposal_id=proposal_id,
                    role=role,
                    title=str(payload["title"]),
                    art_direction=str(payload["art_direction"]),
                    interaction_model=str(payload["interaction_model"]),
                    safeguards=tuple(str(item) for item in safeguards),
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if attempt < 2:
                    continue
                raise BuilderEngineError(
                    "invalid_artifact",
                    "Gemini вернул некорректное визуальное направление",
                    diagnostic=f"{type(exc).__name__}: {exc}",
                    usage=total_usage,
                ) from exc
            return DirectionProposalResult(
                proposal=proposal,
                usage=total_usage,
                provider_request_id=getattr(response, "response_id", None),
                diagnostic=f"model={getattr(response, 'model_version', None) or self.model}",
            )
        raise AssertionError("unreachable direction proposal loop")

    async def judge_directions(
        self,
        *,
        request: BuilderRequest,
        proposals: tuple[DirectionProposal, ...],
    ) -> DirectionJudgeResult:
        if len(proposals) != 3 or len({item.proposal_id for item in proposals}) != 3:
            raise ValueError("judge requires exactly three unique proposals")
        prompt = build_direction_judge_prompt(request=request, proposals=proposals)
        response = await self._generate_structured(
            prompt=prompt,
            schema=DIRECTION_JUDGE_JSON_SCHEMA,
            temperature=0.2,
            max_output_tokens=500,
        )
        try:
            payload = _response_payload(response)
            _require_exact_keys(
                payload,
                frozenset({"selected_proposal_id", "rationale"}),
            )
            judgement = DirectionJudgement.from_dict(payload)
            if judgement.selected_proposal_id not in {
                proposal.proposal_id for proposal in proposals
            }:
                raise ValueError("judge selected an unknown proposal")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise BuilderEngineError(
                "invalid_artifact",
                "Gemini вернул некорректное решение по направлению",
                diagnostic=f"{type(exc).__name__}: {exc}",
                usage=_usage(response),
            ) from exc
        return DirectionJudgeResult(
            judgement=judgement,
            usage=_usage(response),
            provider_request_id=getattr(response, "response_id", None),
            diagnostic=f"model={getattr(response, 'model_version', None) or self.model}",
        )

    async def generate(
        self,
        *,
        request: BuilderRequest,
        stage: Stage,
        revision: int,
        previous_artifact: WidgetArtifact | None = None,
        repair_issues: tuple[ValidationIssue, ...] = (),
        visual_findings: tuple[VisualFinding, ...] = (),
        selected_direction: DirectionProposal | None = None,
    ) -> EngineResult:
        prompt = build_stage_prompt(
            request=request,
            stage=stage,
            revision=revision,
            previous_artifact=previous_artifact,
            repair_issues=repair_issues,
            visual_findings=visual_findings,
            selected_direction=selected_direction,
        )
        temperature = (
            min(request.creativity, 0.35)
            if visual_findings
            else request.creativity
        )
        total_usage = TokenUsage()
        for attempt in range(2):
            attempt_prompt = prompt
            if attempt:
                attempt_prompt += (
                    "\nCORRECTION: The previous JSON could not be accepted as a complete "
                    "widget artifact. Return a fresh full JSON object with the exact stage "
                    f"{stage.value} and revision {revision}; preserve every schema field and "
                    "do not add commentary."
                )
            response = await self._generate_structured(
                prompt=attempt_prompt,
                schema=ARTIFACT_JSON_SCHEMA,
                temperature=temperature,
                max_output_tokens=8_192,
            )
            total_usage = total_usage + _usage(response)
            try:
                payload = {**_response_payload(response), "schema_version": "1.0"}
                artifact = WidgetArtifact.from_dict(payload)
                if artifact.revision != revision or artifact.stage != stage:
                    raise ValueError(
                        "Gemini candidate violates stage or revision invariants"
                    )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if attempt == 0:
                    continue
                raise BuilderEngineError(
                    "invalid_artifact",
                    "Gemini вернул некорректный формат виджета",
                    diagnostic=f"{type(exc).__name__}: {exc}",
                    usage=total_usage,
                ) from exc

            return EngineResult(
                artifact=artifact,
                usage=total_usage,
                provider_request_id=getattr(response, "response_id", None),
                diagnostic=(
                    f"model={getattr(response, 'model_version', None) or self.model}"
                ),
            )
        raise AssertionError("unreachable widget artifact loop")

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
