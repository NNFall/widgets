from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from .models import TokenUsage
from .visual_critic import (
    VisualCriticResult,
    VisualCriticRole,
)
from .visual_models import (
    VisualCritique,
    VisualFinding,
    VisualSeverity,
    VisualVerdict,
)


class VisualCritic(Protocol):
    async def critique(
        self, *, audit: Any, brief: str, art_direction: str
    ) -> VisualCriticResult: ...

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


def _finding_group_key(finding: VisualFinding) -> tuple[Any, ...]:
    return (
        finding.screenshot_id,
        finding.category.value,
        finding.region.semantic_region or "root",
        tuple(sorted(finding.artifact_fields)),
    )


def _accepted_findings(
    role_results: Mapping[VisualCriticRole, VisualCriticResult],
) -> tuple[VisualFinding, ...]:
    groups: dict[
        tuple[Any, ...],
        list[tuple[VisualCriticRole, VisualFinding]],
    ] = defaultdict(list)
    for role, result in role_results.items():
        for finding in result.critique.findings:
            if (
                finding.severity
                not in {VisualSeverity.BLOCKER, VisualSeverity.MAJOR}
                or finding.confidence < 0.75
            ):
                continue
            groups[_finding_group_key(finding)].append((role, finding))

    accepted: list[tuple[int, int, float, VisualFinding]] = []
    for supported in groups.values():
        roles = {role for role, _finding in supported}
        blockers = [
            finding
            for _role, finding in supported
            if finding.severity is VisualSeverity.BLOCKER
        ]
        if blockers:
            representative = max(blockers, key=lambda item: item.confidence)
            accepted.append((2, len(roles), representative.confidence, representative))
            continue
        if len(roles) >= 2:
            representative = max(
                (finding for _role, finding in supported),
                key=lambda item: item.confidence,
            )
            accepted.append((1, len(roles), representative.confidence, representative))

    accepted.sort(
        key=lambda item: (item[0], item[1], item[2]),
        reverse=True,
    )
    return tuple(
        replace(item[3], finding_id=f"committee-{index}")
        for index, item in enumerate(accepted[:3], start=1)
    )


class VisualCriticCommittee:
    def __init__(
        self,
        factories: Mapping[
            VisualCriticRole,
            Callable[[], VisualCritic],
        ],
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
        self._critics = {
            role: factory()
            for role, factory in normalized.items()
        }
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

        findings = _accepted_findings(role_results)
        critique = VisualCritique(
            verdict=(
                VisualVerdict.REPAIR
                if findings
                else VisualVerdict.PASS
            ),
            summary=(
                f"Visual committee quorum {len(role_results)}/3; "
                f"blocking finding groups: {len(findings)}."
            ),
            findings=findings,
        )
        first = next(iter(role_results.values()))
        return VisualCommitteeResult(
            critique=critique,
            observations=tuple(first.observations),
            pixel_proof=None,
            usage=usage,
            role_results=dict(role_results),
            role_failures=dict(role_failures),
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.gather(
            *(critic.aclose() for critic in self._critics.values()),
            return_exceptions=True,
        )


__all__ = [
    "VisualCommitteeError",
    "VisualCommitteeResult",
    "VisualCriticCommittee",
]
