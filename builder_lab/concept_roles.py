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


def _safe_optional_text(value: object, *, limit: int) -> str | None:
    if value is None:
        return None
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        return None
    text = text.replace("\x00", " ").strip()
    if not text:
        return None
    return text[:limit].strip() or None


@dataclass(frozen=True)
class ConceptRoleFailure:
    role: ConceptRole
    summary: str
    usage: TokenUsage
    provider_request_id: str | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.role, ConceptRole):
            raise ValueError("failed execution role must be ConceptRole")
        summary = _safe_optional_text(self.summary, limit=1000)
        if summary is None:
            raise ValueError("failed execution summary is invalid")
        if not isinstance(self.usage, TokenUsage):
            raise ValueError("failed execution usage must be TokenUsage")
        object.__setattr__(self, "summary", summary)
        object.__setattr__(
            self,
            "provider_request_id",
            _safe_optional_text(self.provider_request_id, limit=256),
        )
        object.__setattr__(
            self,
            "diagnostic",
            _safe_optional_text(self.diagnostic, limit=2000),
        )


class ConceptRolesError(BuilderEngineError):
    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        diagnostic: str | None = None,
        usage: TokenUsage | None = None,
        completed_executions: tuple[ConceptRoleExecution, ...] = (),
        failed_execution: ConceptRoleFailure | None = None,
    ) -> None:
        super().__init__(
            error_code,
            public_message,
            diagnostic=diagnostic,
            usage=usage,
        )
        completed = tuple(completed_executions)
        if len(completed) > len(CONCEPT_ROLES) or any(
            not isinstance(item, ConceptRoleExecution) for item in completed
        ):
            raise ValueError("completed concept role executions are invalid")
        if failed_execution is not None and not isinstance(
            failed_execution,
            ConceptRoleFailure,
        ):
            raise ValueError("failed concept role execution is invalid")
        self.completed_executions = completed
        self.executions = completed
        self.failed_execution = failed_execution


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
            failed_usage = (
                exc.usage if isinstance(exc.usage, TokenUsage) else TokenUsage()
            )
            diagnostic = _safe_optional_text(exc.diagnostic, limit=2000)
            public_message = _safe_optional_text(
                exc.public_message,
                limit=2000,
            ) or "Gemini не завершил концептуальную роль"
            raise ConceptRolesError(
                exc.error_code,
                public_message,
                diagnostic=diagnostic,
                usage=usage + failed_usage,
                completed_executions=tuple(executions),
                failed_execution=ConceptRoleFailure(
                    role=role,
                    summary=public_message,
                    usage=failed_usage,
                    provider_request_id=getattr(
                        exc,
                        "provider_request_id",
                        None,
                    ),
                    diagnostic=diagnostic,
                ),
            ) from exc
        except Exception as exc:
            diagnostic = _safe_optional_text(
                f"{type(exc).__name__}: {exc}",
                limit=2000,
            )
            public_message = (
                "Gemini вернул некорректный контракт концептуальной роли"
            )
            raise ConceptRolesError(
                "invalid_artifact",
                public_message,
                diagnostic=diagnostic,
                usage=usage,
                completed_executions=tuple(executions),
                failed_execution=ConceptRoleFailure(
                    role=role,
                    summary=public_message,
                    usage=TokenUsage(),
                    diagnostic=diagnostic,
                ),
            ) from exc
        usage = usage + result.usage
        if result.brief.role is not role:
            diagnostic = _safe_optional_text(
                result.diagnostic
                or (
                    f"expected role {role.value}, "
                    f"received {result.brief.role.value}"
                ),
                limit=2000,
            )
            public_message = (
                "Gemini вернул некорректную последовательность "
                "концептуальных ролей"
            )
            raise ConceptRolesError(
                "invalid_artifact",
                public_message,
                diagnostic=diagnostic,
                usage=usage,
                completed_executions=tuple(executions),
                failed_execution=ConceptRoleFailure(
                    role=role,
                    summary=public_message,
                    usage=result.usage,
                    provider_request_id=result.provider_request_id,
                    diagnostic=diagnostic,
                ),
            )
        briefs.append(result.brief)
        executions.append(
            ConceptRoleExecution(
                brief=result.brief,
                usage=result.usage,
                provider_request_id=_safe_optional_text(
                    result.provider_request_id,
                    limit=256,
                ),
                diagnostic=_safe_optional_text(
                    result.diagnostic,
                    limit=2000,
                ),
            )
        )

    bounded_briefs = tuple(briefs)
    return ConceptRolesResult(
        briefs=bounded_briefs,
        selected_direction=_direction_from_briefs(bounded_briefs),
        usage=usage,
        executions=tuple(executions),
    )
