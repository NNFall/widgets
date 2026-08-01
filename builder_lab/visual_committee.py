from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .models import TokenUsage
from .visual_critic import (
    VisualCriticResult,
    VisualCriticRole,
)
from .visual_models import VisualCritique
from .visual_review import VisualJudgeError, VisualJudgeResult


_TERMINAL_ROUTE_CODES = frozenset({"route_exhausted", "invalid_response"})


def _safe_route_label(value: object) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return None
    if not all(character.isalnum() or character in "._:/-" for character in value):
        return None
    return value


def _safe_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, (1 << 63) - 1)


def _safe_route_diagnostic(value: object) -> dict[str, object]:
    try:
        payload = json.loads(value) if isinstance(value, str) else None
    except (json.JSONDecodeError, ValueError, TypeError):
        payload = None
    if not isinstance(payload, dict):
        return {"terminal_reason": "unavailable", "route_attempts": []}
    terminal_reason = _safe_route_label(payload.get("terminal_reason")) or "unavailable"
    attempts: list[dict[str, object]] = []
    raw_attempts = payload.get("route_attempts")
    if isinstance(raw_attempts, list):
        for raw_attempt in raw_attempts[:8]:
            if not isinstance(raw_attempt, dict):
                continue
            usage_source = raw_attempt.get("usage")
            usage: dict[str, int] = {}
            if isinstance(usage_source, dict):
                for key in ("input_tokens", "output_tokens", "thinking_tokens"):
                    safe_value = _safe_nonnegative_int(usage_source.get(key))
                    if safe_value is not None:
                        usage[key] = safe_value
            attempt: dict[str, object] = {"usage": usage}
            for key in (
                "provider",
                "model",
                "outcome",
                "cost_state",
                "error_code",
            ):
                safe_value = _safe_route_label(raw_attempt.get(key))
                if safe_value is not None:
                    attempt[key] = safe_value
            for key in ("latency_ms", "cost_microusd"):
                safe_value = _safe_nonnegative_int(raw_attempt.get(key))
                if safe_value is not None:
                    attempt[key] = safe_value
            attempts.append(attempt)
    return {"terminal_reason": terminal_reason, "route_attempts": attempts}


class VisualCritic(Protocol):
    async def critique(
        self, *, audit: Any, brief: str, art_direction: str
    ) -> VisualCriticResult: ...

    async def aclose(self) -> None: ...


class VisualJudge(Protocol):
    async def judge(
        self,
        *,
        role_results: Mapping[VisualCriticRole, VisualCriticResult],
    ) -> VisualJudgeResult: ...

    async def aclose(self) -> None: ...


class VisualCommitteeError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        diagnostic: str | None = None,
        usage: TokenUsage | None = None,
    ) -> None:
        super().__init__(public_message)
        self.error_code = error_code
        self.public_message = public_message
        self.diagnostic = diagnostic
        self.usage = usage or TokenUsage()


@dataclass(frozen=True)
class VisualCommitteeResult:
    critique: VisualCritique
    observations: tuple[Any, ...]
    pixel_proof: None
    usage: TokenUsage
    role_results: Mapping[VisualCriticRole, VisualCriticResult]
    role_failures: Mapping[VisualCriticRole, str]
    supporting_roles: Mapping[str, tuple[VisualCriticRole, ...]]
    reused_roles: tuple[VisualCriticRole, ...]


class VisualCriticCommittee:
    def __init__(
        self,
        factories: Mapping[
            VisualCriticRole,
            Callable[[], VisualCritic],
        ],
        *,
        judge_factory: Callable[[], VisualJudge],
    ) -> None:
        normalized = {
            VisualCriticRole(role): factory
            for role, factory in factories.items()
        }
        expected = set(VisualCriticRole)
        if set(normalized) != expected:
            raise ValueError("visual committee requires exactly three critic roles")
        if any(not callable(factory) for factory in normalized.values()):
            raise TypeError("visual critic factories must be callable")
        if not callable(judge_factory):
            raise TypeError("visual judge factory must be callable")
        self._critics = {
            role: factory()
            for role, factory in normalized.items()
        }
        self._judge = judge_factory()
        self._closed = False
        self._cached_audit: Any | None = None
        self._cached_brief = ""
        self._cached_art_direction = ""
        self._cached_role_results: dict[
            VisualCriticRole,
            VisualCriticResult,
        ] = {}

    async def critique(
        self,
        *,
        audit: Any,
        brief: str,
        art_direction: str,
    ) -> VisualCommitteeResult:
        if self._closed:
            raise RuntimeError("visual committee is closed")
        roles = tuple(VisualCriticRole)
        same_input = (
            audit is self._cached_audit
            and brief == self._cached_brief
            and art_direction == self._cached_art_direction
        )
        if not same_input:
            self._cached_audit = audit
            self._cached_brief = brief
            self._cached_art_direction = art_direction
            self._cached_role_results.clear()
        reused_role_results = dict(self._cached_role_results)
        roles_to_run = tuple(
            role for role in roles if role not in reused_role_results
        )
        responses = await asyncio.gather(
            *(
                self._critics[role].critique(
                    audit=audit,
                    brief=brief,
                    art_direction=art_direction,
                )
                for role in roles_to_run
            ),
            return_exceptions=True,
        )
        role_results: dict[VisualCriticRole, VisualCriticResult] = dict(
            reused_role_results
        )
        role_failures: dict[VisualCriticRole, str] = {}
        role_errors: dict[VisualCriticRole, BaseException] = {}
        usage = TokenUsage()
        for role, response in zip(roles_to_run, responses):
            if isinstance(response, BaseException):
                response_usage = getattr(response, "usage", TokenUsage())
                if isinstance(response_usage, TokenUsage):
                    usage = usage + response_usage
                role_failures[role] = str(
                    getattr(response, "error_code", type(response).__name__)
                )
                role_errors[role] = response
                if isinstance(response, asyncio.CancelledError):
                    raise response
                continue
            role_results[role] = response
            self._cached_role_results[role] = response
            usage = usage + response.usage

        if len(role_results) < 2:
            terminal_failures = [
                (role, role_errors[role])
                for role in roles
                if getattr(role_errors.get(role), "error_code", None)
                in _TERMINAL_ROUTE_CODES
            ]
            if terminal_failures:
                terminal_codes = {
                    str(getattr(error, "error_code"))
                    for _, error in terminal_failures
                }
                error_code = (
                    "invalid_response"
                    if terminal_codes == {"invalid_response"}
                    else "route_exhausted"
                )
                diagnostic = json.dumps(
                    {
                        "terminal_reason": "committee_terminal_failures",
                        "role_failures": [
                            {
                                "role": role.value,
                                "error_code": str(
                                    getattr(error, "error_code", "route_exhausted")
                                ),
                                "route": _safe_route_diagnostic(
                                    getattr(error, "diagnostic", None)
                                ),
                            }
                            for role, error in terminal_failures
                        ],
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                terminal_cause = terminal_failures[0][1]
                raise VisualCommitteeError(
                    error_code,
                    (
                        "Сервис визуальной проверки вернул некорректный ответ"
                        if error_code == "invalid_response"
                        else "Сервис визуальной проверки не смог завершить запрос"
                    ),
                    diagnostic=diagnostic,
                    usage=usage,
                ) from terminal_cause
            diagnostic = ",".join(
                f"{role.value}:{role_failures.get(role, 'missing')}"
                for role in roles
                if role not in role_results
            )
            raise VisualCommitteeError(
                "visual_review_inconclusive",
                "Визуальные критики не смогли завершить проверку",
                diagnostic=diagnostic,
                usage=usage,
            )

        try:
            judgement = await self._judge.judge(role_results=role_results)
        except asyncio.CancelledError:
            raise
        except VisualJudgeError as exc:
            usage = usage + exc.usage
            terminal_route_error = exc.error_code in {
                "route_exhausted",
                "invalid_response",
            }
            raise VisualCommitteeError(
                (
                    exc.error_code
                    if terminal_route_error
                    else "visual_review_inconclusive"
                ),
                (
                    "Сервис визуальной проверки не смог завершить запрос"
                    if terminal_route_error
                    else "Независимый визуальный судья не смог завершить проверку"
                ),
                diagnostic=exc.diagnostic or exc.error_code,
                usage=usage,
            ) from exc
        usage = usage + judgement.usage
        first = next(iter(role_results.values()))
        return VisualCommitteeResult(
            critique=judgement.critique,
            observations=tuple(first.observations),
            pixel_proof=None,
            usage=usage,
            role_results=dict(role_results),
            role_failures=dict(role_failures),
            supporting_roles=dict(judgement.supporting_roles),
            reused_roles=tuple(
                role for role in roles if role in reused_role_results
            ),
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._cached_audit = None
        self._cached_role_results.clear()
        await asyncio.gather(
            *(critic.aclose() for critic in self._critics.values()),
            self._judge.aclose(),
            return_exceptions=True,
        )


__all__ = [
    "VisualCommitteeError",
    "VisualCommitteeResult",
    "VisualCriticCommittee",
]
