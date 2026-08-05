from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from google import genai
from google.genai import types

from app.models.providers.gemini import (
    build_http_options,
    build_provider_json_schema,
    classify_gemini_error,
    gemini_usage_counts,
)
from app.models.contracts import (
    BilledModelProviderError,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)
from app.models.lineage import ModelInvocationContext
from app.models.router import ModelRouter

from ..model_config import generation_policy, normalize_thinking_level
from ..models import (
    BuilderRequest,
    ConceptRole,
    ConceptRoleBrief,
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
    COMPOSITION_PLAN_JSON_SCHEMA,
    CONCEPT_ROLE_BRIEF_JSON_SCHEMA,
    DIRECTION_JUDGE_JSON_SCHEMA,
    DIRECTION_PROPOSAL_JSON_SCHEMA,
    PATTERN_CANDIDATE_PLAN_JSON_SCHEMA,
    build_direction_judge_prompt,
    build_direction_proposal_prompt,
    build_concept_role_prompt,
    build_composition_plan_prompt,
    build_pattern_candidate_plan_prompt,
    build_stage_prompt,
)
from .base import (
    BuilderEngineError,
    CompositionPlanResult,
    ConceptRoleResult,
    DirectionJudgeResult,
    DirectionProposalResult,
    EngineResult,
    PatternCandidatePlanResult,
)
from ..patterns.atomic_models import PatternCandidatePlan


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


def _provider_error(exc: Exception) -> BuilderEngineError:
    diagnostic = f"{type(exc).__name__}: {exc}"
    category = classify_gemini_error(exc)
    if category == "provider_permission_denied":
        return BuilderEngineError(
            "provider_permission_denied",
            "Сервис генерации недоступен из-за ограничений доступа или оплаты. Обратитесь в поддержку.",
            diagnostic=diagnostic,
        )
    if category == "generation_timeout":
        return BuilderEngineError(
            "generation_timeout",
            "Gemini не завершил этап вовремя",
            diagnostic=diagnostic,
        )
    if category == "quota_exceeded":
        return BuilderEngineError(
            "quota_exceeded",
            "Квота Gemini временно исчерпана",
            diagnostic=diagnostic,
        )
    if category == "model_unavailable":
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
    if isinstance(response, (ModelResponse, ModelUsage)):
        usage = response.usage if isinstance(response, ModelResponse) else response
        return TokenUsage(
            prompt_tokens=usage.input_tokens,
            output_tokens=max(
                0,
                usage.output_tokens - usage.thinking_tokens,
            ),
            thinking_tokens=usage.thinking_tokens,
        )
    try:
        counts = gemini_usage_counts(response)
    except BilledModelProviderError as exc:
        raise BuilderEngineError(
            exc.error_code,
            "Сервис генерации вернул некорректный ответ",
            diagnostic=f"{type(exc).__name__}: {exc}",
            usage=_usage(exc.usage),
        ) from exc
    # Builder Lab's historical TokenUsage adds thinking_tokens in total_tokens,
    # unlike ModelUsage where thinking is already a subset of output_tokens.
    return TokenUsage(
        prompt_tokens=counts.input_tokens,
        output_tokens=counts.candidate_tokens,
        thinking_tokens=counts.thinking_tokens,
    )


def _accumulate_usage(total: TokenUsage, response: Any) -> TokenUsage:
    try:
        return total + _usage(response)
    except BuilderEngineError as exc:
        exc.usage = total + exc.usage
        raise


def _response_diagnostic(response: Any, *, fallback_model: str) -> str:
    raw = getattr(response, "raw", None)
    provider = raw.get("provider") if isinstance(raw, Mapping) else None
    model = (
        raw.get("model") if isinstance(raw, Mapping) else None
    ) or getattr(response, "model_version", None) or fallback_model
    if provider:
        return f"provider={provider}; model={model}"
    return f"model={model}"


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
        api_key: str | None = None,
        model: str = "gemini-3.6-flash",
        thinking_level: str = "high",
        base_url: str = "https://generativelanguage.googleapis.com",
        client: Any | None = None,
        model_router: ModelRouter | None = None,
        routing_role: str | None = None,
        routing_mode: str = "direct",
        routing_timeout_seconds: float | None = None,
        run_id: Any | None = None,
        invocation_context: ModelInvocationContext | None = None,
    ) -> None:
        if model_router is None and (not api_key or not api_key.strip()):
            raise BuilderEngineError(
                "missing_api_key", "Для direct-режима не настроен ключ Gemini"
            )
        self.model = model
        self.thinking_level = normalize_thinking_level(thinking_level)
        if model_router is not None and not routing_role:
            raise ValueError("routing_role is required with model_router")
        self._model_router = model_router
        self._routing_role = routing_role
        self._routing_mode = routing_mode
        self._routing_timeout_seconds = routing_timeout_seconds
        self._run_id = run_id
        self._invocation_context = invocation_context
        self._owned_client = model_router is None and client is None
        self._client = None
        if model_router is None:
            self._client = client or genai.Client(
                api_key=api_key.strip(),  # type: ignore[union-attr]
                http_options=build_http_options(base_url),
            )

    async def _generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        temperature: float,
        routing_deadline: float | None = None,
        context: ModelInvocationContext | None = None,
    ) -> Any:
        if self._model_router is not None:
            routing_timeout_seconds = self._routing_timeout_seconds
            if routing_deadline is not None:
                routing_timeout_seconds = max(
                    routing_deadline - time.monotonic(),
                    1e-6,
                )
            try:
                if context is None:
                    context = self._context(
                        operation=str(self._routing_role),
                        semantic_attempt=1,
                    )
                return await self._model_router.generate(
                    role=str(self._routing_role),
                    mode=self._routing_mode,
                    run_id=self._run_id,
                    context=context,
                    request=ModelRequest(
                        prompt=prompt,
                        response_schema=schema,
                        temperature=temperature,
                        metadata={"thinking_level": self.thinking_level},
                    ),
                    timeout_seconds=routing_timeout_seconds,
                )
            except ModelProviderError as exc:
                public_messages = {
                    "provider_permission_denied": "Сервис генерации недоступен из-за ограничений доступа или оплаты. Обратитесь в поддержку.",
                    "generation_timeout": "Сервис генерации не завершил этап вовремя",
                    "quota_exceeded": "Квота сервиса генерации временно исчерпана",
                    "model_unavailable": "Выбранная модель генерации временно недоступна",
                    "invalid_response": "Сервис генерации вернул некорректный ответ",
                    "unsupported_request": "Сервис генерации не поддерживает этот запрос",
                    "route_exhausted": "Сервис генерации не смог завершить запрос доступным маршрутом",
                }
                usage = (
                    _usage(exc.usage)
                    if isinstance(exc, BilledModelProviderError)
                    else TokenUsage()
                )
                raise BuilderEngineError(
                    exc.error_code,
                    public_messages.get(
                        exc.error_code,
                        "Сервис генерации временно недоступен",
                    ),
                    diagnostic=(
                        getattr(exc, "diagnostic", None)
                        or f"{type(exc).__name__}: {exc}"
                    ),
                    usage=usage,
                ) from exc
        policy = generation_policy(
            self.model,
            self.thinking_level,
            temperature=temperature,
        )
        config = types.GenerateContentConfig(
            **policy.sampling_kwargs,
            response_mime_type="application/json",
            response_json_schema=build_provider_json_schema(schema, self.model),
            tools=[],
            thinking_config=policy.thinking_config,
        )
        retry_delays = (0.5, 1.5, 3.0, 5.0)
        for attempt in range(len(retry_delays) + 1):
            try:
                return await self._client.aio.models.generate_content(  # type: ignore[union-attr]
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

    def _new_routing_deadline(self) -> float | None:
        if self._model_router is None or self._routing_timeout_seconds is None:
            return None
        return time.monotonic() + self._routing_timeout_seconds

    def _context(
        self,
        *,
        operation: str,
        semantic_attempt: int,
        candidate_id: str | None = None,
        persona: str | None = None,
    ) -> ModelInvocationContext:
        base = self._invocation_context or ModelInvocationContext(
            stage_attempt_id=None,
            stage=None,
            operation=operation,
        )
        return replace(
            base,
            operation=operation,
            semantic_attempt=semantic_attempt,
            candidate_id=(
                base.candidate_id if candidate_id is None else candidate_id
            ),
            persona=base.persona if persona is None else persona,
        )

    async def propose_direction(
        self,
        *,
        request: BuilderRequest,
        role: DirectionRole,
        proposal_id: str,
    ) -> DirectionProposalResult:
        prompt = build_direction_proposal_prompt(request=request, role=role)
        total_usage = TokenUsage()
        routing_deadline = self._new_routing_deadline()
        semantic_attempts = 2 if self._model_router is not None else 3
        for attempt in range(semantic_attempts):
            attempt_prompt = prompt
            if attempt:
                attempt_prompt += (
                    "\nCORRECTION: The previous JSON violated one or more field budgets. "
                    "Return a fresh complete proposal and keep every field within the exact "
                    "numeric limits above."
                )
            try:
                response = await self._generate_structured(
                    prompt=attempt_prompt,
                    schema=DIRECTION_PROPOSAL_JSON_SCHEMA,
                    temperature=request.creativity,
                    routing_deadline=routing_deadline,
                    context=self._context(
                        operation="direction_candidate",
                        semantic_attempt=attempt + 1,
                        candidate_id=proposal_id,
                        persona=role.value,
                    ),
                )
            except BuilderEngineError as exc:
                exc.usage = total_usage + exc.usage
                raise
            total_usage = _accumulate_usage(total_usage, response)
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
                if attempt < semantic_attempts - 1:
                    continue
                raise BuilderEngineError(
                    "invalid_artifact",
                    "Сервис генерации вернул некорректное визуальное направление",
                    diagnostic=f"{type(exc).__name__}: {exc}",
                    usage=total_usage,
                ) from exc
            return DirectionProposalResult(
                proposal=proposal,
                usage=total_usage,
                provider_request_id=(
                    getattr(response, "request_id", None)
                    or getattr(response, "response_id", None)
                ),
                diagnostic=_response_diagnostic(response, fallback_model=self.model),
            )
        raise AssertionError("unreachable direction proposal loop")

    async def develop_concept_role(
        self,
        *,
        request: BuilderRequest,
        role: ConceptRole,
        prior_briefs: tuple[ConceptRoleBrief, ...] = (),
    ) -> ConceptRoleResult:
        prompt = build_concept_role_prompt(
            request=request,
            role=role,
            prior_briefs=prior_briefs,
        )
        total_usage = TokenUsage()
        routing_deadline = self._new_routing_deadline()
        semantic_attempts = 2 if self._model_router is not None else 3
        for attempt in range(semantic_attempts):
            attempt_prompt = prompt
            if attempt:
                attempt_prompt += (
                    "\nCORRECTION: The previous JSON violated the concept-role field "
                    "budgets. Return a fresh complete object with exactly summary, "
                    "decisions and safeguards within the numeric limits above."
                )
            try:
                response = await self._generate_structured(
                    prompt=attempt_prompt,
                    schema=CONCEPT_ROLE_BRIEF_JSON_SCHEMA,
                    temperature=request.creativity,
                    routing_deadline=routing_deadline,
                    context=self._context(
                        operation=str(self._routing_role),
                        semantic_attempt=attempt + 1,
                        persona=role.value,
                    ),
                )
            except BuilderEngineError as exc:
                exc.usage = total_usage + exc.usage
                raise
            total_usage = _accumulate_usage(total_usage, response)
            try:
                payload = _response_payload(response)
                _require_exact_keys(
                    payload,
                    frozenset({"summary", "decisions", "safeguards"}),
                )
                brief = ConceptRoleBrief.from_dict(payload, role=role)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if attempt < semantic_attempts - 1:
                    continue
                raise BuilderEngineError(
                    "invalid_artifact",
                    "Сервис генерации вернул некорректный контракт концептуальной роли",
                    diagnostic=f"{type(exc).__name__}: {exc}",
                    usage=total_usage,
                ) from exc
            return ConceptRoleResult(
                brief=brief,
                usage=total_usage,
                provider_request_id=(
                    getattr(response, "request_id", None)
                    or getattr(response, "response_id", None)
                ),
                diagnostic=_response_diagnostic(response, fallback_model=self.model),
            )
        raise AssertionError("unreachable concept role loop")

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
            routing_deadline=self._new_routing_deadline(),
            context=self._context(
                operation="direction_judge",
                semantic_attempt=1,
            ),
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
                "Сервис генерации вернул некорректное решение по направлению",
                diagnostic=f"{type(exc).__name__}: {exc}",
                usage=_usage(response),
            ) from exc
        return DirectionJudgeResult(
            judgement=judgement,
            usage=_usage(response),
            provider_request_id=(
                getattr(response, "request_id", None)
                or getattr(response, "response_id", None)
            ),
            diagnostic=_response_diagnostic(response, fallback_model=self.model),
        )

    async def plan_composition(
        self,
        *,
        request: BuilderRequest,
        selected_direction: DirectionProposal,
        public_catalog: tuple[dict[str, Any], ...],
        correction: str | None = None,
    ) -> CompositionPlanResult:
        prompt = build_composition_plan_prompt(
            request=request,
            selected_direction=selected_direction,
            public_catalog=public_catalog,
            correction=correction,
        )
        response = await self._generate_structured(
            prompt=prompt,
            schema=COMPOSITION_PLAN_JSON_SCHEMA,
            temperature=0.25,
            routing_deadline=self._new_routing_deadline(),
            context=self._context(
                operation="composition_plan",
                semantic_attempt=1,
                candidate_id=selected_direction.proposal_id,
                persona=selected_direction.role.value,
            ),
        )
        try:
            payload = _response_payload(response)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise BuilderEngineError(
                "invalid_structured_output",
                "Сервис генерации вернул некорректный план композиции",
                diagnostic=f"{type(exc).__name__}: {exc}",
                usage=_usage(response),
            ) from exc
        return CompositionPlanResult(
            payload=payload,
            usage=_usage(response),
            provider_request_id=(
                getattr(response, "request_id", None)
                or getattr(response, "response_id", None)
            ),
            diagnostic=_response_diagnostic(response, fallback_model=self.model),
        )

    async def plan_pattern_candidates(
        self,
        *,
        request: BuilderRequest,
        selected_direction: DirectionProposal,
        selector_catalog: tuple[dict[str, Any], ...],
        correction: str | None = None,
        optional_categories: tuple[str, ...] = (),
    ) -> PatternCandidatePlanResult:
        prompt = build_pattern_candidate_plan_prompt(
            request=request,
            selected_direction=selected_direction,
            selector_catalog=selector_catalog,
            correction=correction,
            optional_categories=optional_categories,
        )
        response = await self._generate_structured(
            prompt=prompt,
            schema=PATTERN_CANDIDATE_PLAN_JSON_SCHEMA,
            temperature=0.2,
            routing_deadline=self._new_routing_deadline(),
            context=self._context(
                operation="pattern_candidate_plan",
                semantic_attempt=1,
                candidate_id=selected_direction.proposal_id,
                persona=selected_direction.role.value,
            ),
        )
        try:
            payload = _response_payload(response)
            plan = PatternCandidatePlan.from_dict(payload)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            provider_request_id = (
                getattr(response, "request_id", None)
                or getattr(response, "response_id", None)
            )
            raise BuilderEngineError(
                "invalid_structured_output",
                "РЎРµСЂРІРёСЃ РіРµРЅРµСЂР°С†РёРё РІРµСЂРЅСѓР» РЅРµРєРѕСЂСЂРµРєС‚РЅС‹Р№ РїР»Р°РЅ РєР°РЅРґРёРґР°С‚РѕРІ",
                diagnostic=f"{type(exc).__name__}: {exc}",
                usage=_usage(response),
                provider_request_id=provider_request_id,
            ) from exc
        provider_request_id = (
            getattr(response, "request_id", None)
            or getattr(response, "response_id", None)
        )
        return PatternCandidatePlanResult(
            plan=plan,
            usage=_usage(response),
            provider_request_id=provider_request_id,
            provider_request_ids=(provider_request_id,) if provider_request_id else (),
            diagnostic=_response_diagnostic(response, fallback_model=self.model),
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
        composition: Any | None = None,
        pattern_candidate_pack: Any | None = None,
    ) -> EngineResult:
        prompt = build_stage_prompt(
            request=request,
            stage=stage,
            revision=revision,
            previous_artifact=previous_artifact,
            repair_issues=repair_issues,
            visual_findings=visual_findings,
            selected_direction=selected_direction,
            composition=composition,
            pattern_candidate_pack=pattern_candidate_pack,
        )
        temperature = (
            min(request.creativity, 0.35)
            if visual_findings
            else request.creativity
        )
        total_usage = TokenUsage()
        routing_deadline = self._new_routing_deadline()
        operation = (
            "visual_repair"
            if visual_findings
            else "validation_repair"
            if repair_issues
            else "artifact_generation"
        )
        candidate_id = (
            selected_direction.proposal_id if selected_direction is not None else None
        )
        persona = (
            selected_direction.role.value if selected_direction is not None else None
        )
        for attempt in range(2):
            attempt_prompt = prompt
            if attempt:
                attempt_prompt += (
                    "\nCORRECTION: The previous JSON could not be accepted as a complete "
                    "widget artifact. Return a fresh full JSON object with the exact stage "
                    f"{stage.value} and revision {revision}; preserve every schema field and "
                    "do not add commentary."
                )
            try:
                response = await self._generate_structured(
                    prompt=attempt_prompt,
                    schema=ARTIFACT_JSON_SCHEMA,
                    temperature=temperature,
                    routing_deadline=routing_deadline,
                    context=self._context(
                        operation=(
                            "validation_repair"
                            if attempt and operation == "artifact_generation"
                            else operation
                        ),
                        semantic_attempt=attempt + 1,
                        candidate_id=candidate_id,
                        persona=persona,
                    ),
                )
            except BuilderEngineError as exc:
                exc.usage = total_usage + exc.usage
                raise
            total_usage = _accumulate_usage(total_usage, response)
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
                    "Сервис генерации вернул некорректный формат виджета",
                    diagnostic=f"{type(exc).__name__}: {exc}",
                    usage=total_usage,
                ) from exc

            return EngineResult(
                artifact=artifact,
                usage=total_usage,
                provider_request_id=(
                    getattr(response, "request_id", None)
                    or getattr(response, "response_id", None)
                ),
                diagnostic=_response_diagnostic(response, fallback_model=self.model),
            )
        raise AssertionError("unreachable widget artifact loop")

    async def cancel(self) -> None:
        return None

    async def close(self) -> None:
        if not self._owned_client:
            return
        if self._client is None:
            return
        aio = getattr(self._client, "aio", None)
        if aio is not None and hasattr(aio, "aclose"):
            await aio.aclose()
        elif hasattr(self._client, "close"):
            self._client.close()
