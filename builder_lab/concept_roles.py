from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from .engines.base import BuilderEngineError, ConceptRoleResult
from .models import (
    BuilderRequest,
    ConceptRole,
    ConceptRoleBrief,
    DirectionProposal,
    TokenUsage,
)


CONCEPT_ROLES = (
    ConceptRole.SITE_BRAND_ANALYST,
    ConceptRole.CONVERSATION_DESIGNER,
    ConceptRole.ART_DIRECTOR_FRONTEND_DEVELOPER,
)


class ConceptRoleEngine(Protocol):
    async def develop_concept_role(
        self,
        *,
        request: BuilderRequest,
        role: ConceptRole,
        prior_briefs: tuple[ConceptRoleBrief, ...] = (),
    ) -> ConceptRoleResult: ...


class ConceptRolesError(BuilderEngineError):
    pass


@dataclass(frozen=True)
class ConceptRolesResult:
    briefs: tuple[ConceptRoleBrief, ...]
    selected_direction: DirectionProposal
    usage: TokenUsage


def _direction_from_briefs(
    briefs: tuple[ConceptRoleBrief, ...],
) -> DirectionProposal:
    conversation = briefs[1]
    final = briefs[2]
    return DirectionProposal(
        proposal_id="candidate-1",
        role=final.role,
        title=final.summary,
        art_direction="\n".join((final.summary, *final.decisions)),
        interaction_model="\n".join(
            (conversation.summary, *conversation.decisions)
        ),
        safeguards=final.safeguards,
    )


async def run_concept_roles(
    *,
    engine: ConceptRoleEngine,
    request: BuilderRequest,
) -> ConceptRolesResult:
    briefs: list[ConceptRoleBrief] = []
    usage = TokenUsage()
    for role in CONCEPT_ROLES:
        try:
            result = await engine.develop_concept_role(
                request=request,
                role=role,
                prior_briefs=tuple(briefs),
            )
        except asyncio.CancelledError:
            raise
        except BuilderEngineError as exc:
            raise ConceptRolesError(
                exc.error_code,
                exc.public_message,
                diagnostic=exc.diagnostic,
                usage=usage + exc.usage,
            ) from exc
        except Exception as exc:
            raise ConceptRolesError(
                "invalid_artifact",
                "Gemini вернул некорректный контракт концептуальной роли",
                diagnostic=f"{type(exc).__name__}: {exc}",
                usage=usage,
            ) from exc
        usage = usage + result.usage
        if result.brief.role is not role:
            raise ConceptRolesError(
                "invalid_artifact",
                "Gemini вернул некорректную последовательность концептуальных ролей",
                usage=usage,
            )
        briefs.append(result.brief)

    bounded_briefs = tuple(briefs)
    return ConceptRolesResult(
        briefs=bounded_briefs,
        selected_direction=_direction_from_briefs(bounded_briefs),
        usage=usage,
    )
