from __future__ import annotations

import asyncio
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
        responses = await asyncio.gather(
            *(
                self._critics[role].critique(
                    audit=audit,
                    brief=brief,
                    art_direction=art_direction,
                )
                for role in roles
            ),
            return_exceptions=True,
        )
        role_results: dict[VisualCriticRole, VisualCriticResult] = {}
        role_failures: dict[VisualCriticRole, str] = {}
        usage = TokenUsage()
        for role, response in zip(roles, responses):
            if isinstance(response, BaseException):
                response_usage = getattr(response, "usage", TokenUsage())
                if isinstance(response_usage, TokenUsage):
                    usage = usage + response_usage
                role_failures[role] = str(
                    getattr(response, "error_code", type(response).__name__)
                )
                if isinstance(response, asyncio.CancelledError):
                    raise response
                continue
            role_results[role] = response
            usage = usage + response.usage

        if len(role_results) < 2:
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
            raise VisualCommitteeError(
                "visual_review_inconclusive",
                "Независимый визуальный судья не смог завершить проверку",
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
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
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
