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
class ConceptRoleExecution:
    brief: ConceptRoleBrief
    usage: TokenUsage
    provider_request_id: str | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.brief, ConceptRoleBrief):
            raise ValueError("execution brief must be ConceptRoleBrief")
        if not isinstance(self.usage, TokenUsage):
            raise ValueError("execution usage must be TokenUsage")
        for field_name, limit in (
            ("provider_request_id", 256),
            ("diagnostic", 2000),
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if (
                not isinstance(value, str)
                or not value.strip()
                or len(value) > limit
                or "\x00" in value
            ):
                raise ValueError(f"execution {field_name} is invalid")
            object.__setattr__(self, field_name, value.strip())


@dataclass(frozen=True)
class ConceptRolesResult:
    briefs: tuple[ConceptRoleBrief, ...]
    selected_direction: DirectionProposal
    usage: TokenUsage
    executions: tuple[ConceptRoleExecution, ...] = ()


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
    executions: list[ConceptRoleExecution] = []
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
        executions.append(
            ConceptRoleExecution(
                brief=result.brief,
                usage=result.usage,
                provider_request_id=result.provider_request_id,
                diagnostic=result.diagnostic,
            )
        )

    bounded_briefs = tuple(briefs)
    return ConceptRolesResult(
        briefs=bounded_briefs,
        selected_direction=_direction_from_briefs(bounded_briefs),
        usage=usage,
        executions=tuple(executions),
    )
