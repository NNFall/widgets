from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from .engines.base import (
    BuilderEngineError,
    DirectionJudgeResult,
    DirectionProposalResult,
)
from .models import (
    BuilderRequest,
    DirectionJudgement,
    DirectionProposal,
    DirectionRole,
    TokenUsage,
)


DIRECTION_ROLES = (
    DirectionRole.BRAND_ARCHAEOLOGIST,
    DirectionRole.INTERACTION_INVENTOR,
    DirectionRole.HOSTILE_CONVERSION_ACCESSIBILITY_CRITIC,
)

_INFRASTRUCTURE_PUBLIC_MESSAGES = {
    "provider_permission_denied": "Сервис генерации недоступен из-за ограничений доступа или оплаты. Обратитесь в поддержку.",
    "generation_timeout": "Сервис генерации не завершил этап вовремя",
    "provider_unavailable": "Сервис генерации временно недоступен",
    "quota_exceeded": "Квота сервиса генерации временно исчерпана",
    "model_unavailable": "Выбранная модель генерации временно недоступна",
    "invalid_response": "Сервис генерации вернул некорректный ответ",
    "unsupported_request": "Сервис генерации не поддерживает этот запрос",
}


class DirectionBoardError(BuilderEngineError):
    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        usage: TokenUsage,
        diagnostic: str | None = None,
    ) -> None:
        super().__init__(
            error_code,
            public_message,
            diagnostic=diagnostic,
            usage=usage,
        )
        self.usage = usage


def _completed_usage(tasks: tuple[asyncio.Task[DirectionProposalResult], ...]) -> TokenUsage:
    usage = TokenUsage()
    for task in tasks:
        if not task.done() or task.cancelled():
            continue
        try:
            result = task.result()
        except BuilderEngineError as exc:
            usage = usage + exc.usage
            continue
        except Exception:
            continue
        usage = usage + result.usage
    return usage


def _board_error(
    exc: Exception,
    usage: TokenUsage,
    *,
    include_error_usage: bool = True,
) -> DirectionBoardError:
    if isinstance(exc, BuilderEngineError):
        if include_error_usage:
            usage = usage + exc.usage
        return DirectionBoardError(
            exc.error_code,
            _INFRASTRUCTURE_PUBLIC_MESSAGES.get(
                exc.error_code,
                exc.public_message,
            ),
            usage=usage,
            diagnostic=exc.diagnostic,
        )
    return DirectionBoardError(
        "invalid_artifact",
        "Сервис генерации вернул некорректный контракт визуального направления",
        usage=usage,
        diagnostic=f"{type(exc).__name__}: {exc}",
    )


class DirectionEngine(Protocol):
    async def propose_direction(
        self,
        *,
        request: BuilderRequest,
        role: DirectionRole,
        proposal_id: str,
    ) -> DirectionProposalResult: ...

    async def judge_directions(
        self,
        *,
        request: BuilderRequest,
        proposals: tuple[DirectionProposal, ...],
    ) -> DirectionJudgeResult: ...


@dataclass(frozen=True)
class DirectionBoardResult:
    proposals: tuple[DirectionProposal, ...]
    judgement: DirectionJudgement
    usage: TokenUsage

    @property
    def selected(self) -> DirectionProposal:
        return next(
            proposal
            for proposal in self.proposals
            if proposal.proposal_id == self.judgement.selected_proposal_id
        )


async def run_direction_board(
    *,
    engine: DirectionEngine | None = None,
    proposal_engine: DirectionEngine | None = None,
    judge_engine: DirectionEngine | None = None,
    request: BuilderRequest,
) -> DirectionBoardResult:
    proposer = proposal_engine or engine
    judge = judge_engine or engine
    if proposer is None or judge is None:
        raise ValueError("direction board requires proposal and judge engines")
    tasks = tuple(
        asyncio.create_task(
            proposer.propose_direction(
                request=request,
                role=role,
                proposal_id=f"candidate-{index}",
            ),
            name=f"kaigo-direction-{index}",
        )
        for index, role in enumerate(DIRECTION_ROLES, start=1)
    )
    try:
        proposal_results = await asyncio.gather(*tasks)
    except BaseException as exc:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise _board_error(
            exc,
            _completed_usage(tasks),
            include_error_usage=False,
        ) from exc
    proposals = tuple(result.proposal for result in proposal_results)
    proposal_usage = _completed_usage(tasks)
    if tuple(proposal.role for proposal in proposals) != DIRECTION_ROLES:
        raise DirectionBoardError(
            "invalid_artifact",
            "Сервис генерации вернул некорректные роли визуальных направлений",
            usage=proposal_usage,
        )
    if tuple(proposal.proposal_id for proposal in proposals) != (
        "candidate-1",
        "candidate-2",
        "candidate-3",
    ):
        raise DirectionBoardError(
            "invalid_artifact",
            "Сервис генерации вернул некорректные идентификаторы направлений",
            usage=proposal_usage,
        )

    try:
        judge_result = await judge.judge_directions(
            request=request,
            proposals=proposals,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise _board_error(exc, proposal_usage) from exc
    total_usage = proposal_usage + judge_result.usage
    if judge_result.judgement.selected_proposal_id not in {
        proposal.proposal_id for proposal in proposals
    }:
        raise DirectionBoardError(
            "invalid_artifact",
            "Сервис генерации выбрал отсутствующее визуальное направление",
            usage=total_usage,
        )
    return DirectionBoardResult(
        proposals=proposals,
        judgement=judge_result.judgement,
        usage=total_usage,
    )
