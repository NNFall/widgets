from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import AsyncIterator
from uuid import UUID
from weakref import WeakValueDictionary

from sqlalchemy import exists, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.saas.models import (
    GenerationArtifact,
    GenerationEvent,
    GenerationRun,
    ModelCall,
    Project,
    TrialEntitlement,
    UsageLedger,
    UserIdentity,
)
from builder_lab.models import WidgetArtifact
from builder_lab.validation import validate_artifact


logger = logging.getLogger(__name__)


class TrialUnavailable(RuntimeError):
    """The verified user no longer has an available complete trial."""


class UnverifiedTrialUser(RuntimeError):
    """A trial may only be reserved by a verified OAuth identity."""


class TrialCompensationDenied(RuntimeError):
    """The reservation outcome is not eligible for a trial compensation."""


class TrialFailureKind(str, Enum):
    PLATFORM = "platform"
    INFRASTRUCTURE = "infrastructure"
    PROVIDER = "provider"
    MODEL_INVALID_OUTPUT = "model_invalid_output"
    USER = "user"
    CONTENT = "content"
    VALIDATION = "validation"


COMPENSATABLE_FAILURES = frozenset(
    {
        TrialFailureKind.INFRASTRUCTURE,
        TrialFailureKind.PLATFORM,
        TrialFailureKind.PROVIDER,
        TrialFailureKind.MODEL_INVALID_OUTPUT,
    }
)


@dataclass(frozen=True, slots=True)
class TrialReservation:
    user_id: int
    run_id: UUID
    key: str


_SQLITE_TRIAL_LOCKS: WeakValueDictionary[tuple[int, int], asyncio.Lock] = (
    WeakValueDictionary()
)
_SQLITE_MODEL_LOCKS: WeakValueDictionary[tuple[int, UUID], asyncio.Lock] = (
    WeakValueDictionary()
)


class TrialService:
    """Transactional trial entitlement and immutable usage accounting."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._sessions = session_factory
        bind = getattr(session_factory, "kw", {}).get("bind")
        self._bind = bind
        self._dialect = bind.dialect.name if bind is not None else ""

    @asynccontextmanager
    async def _user_guard(self, user_id: int) -> AsyncIterator[None]:
        if self._dialect != "sqlite":
            yield
            return
        key = (id(self._bind), user_id)
        lock = _SQLITE_TRIAL_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SQLITE_TRIAL_LOCKS[key] = lock
        async with lock:
            yield

    @staticmethod
    def _reservation_key(
        user_id: int,
        run_id: UUID,
        request_id: str | None,
        epoch: int,
    ) -> str:
        request = (request_id or "default").strip()
        if not request or "\x00" in request:
            raise ValueError("trial request_id is invalid")
        request_hash = hashlib.sha256(request.encode("utf-8")).hexdigest()[:20]
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 1:
            raise ValueError("trial reservation epoch is invalid")
        return f"trial:{user_id}:{run_id.hex}:{request_hash}:{epoch}"

    async def _validate_verified_owned_run(
        self,
        database: AsyncSession,
        *,
        user_id: int,
        run_id: UUID,
    ) -> None:
        verified = await database.scalar(
            select(
                exists().where(
                    UserIdentity.user_id == user_id,
                    UserIdentity.email_verified.is_(True),
                )
            )
        )
        if not verified:
            raise UnverifiedTrialUser("Для пробной сборки нужна подтверждённая почта")
        owned = await database.scalar(
            select(
                exists().where(
                    GenerationRun.id == run_id,
                    GenerationRun.project_id == Project.id,
                    Project.owner_user_id == user_id,
                )
            )
        )
        if not owned:
            raise ValueError("trial run does not belong to user")

    async def _ensure_entitlement(
        self,
        database: AsyncSession,
        *,
        user_id: int,
    ) -> None:
        values = {
            "user_id": user_id,
            "state": "available",
            "granted_units": 1,
            "reserved_units": 0,
            "consumed_units": 0,
            "reservation_epoch": 0,
        }
        if database.get_bind().dialect.name == "postgresql":
            statement = postgresql_insert(TrialEntitlement).values(**values)
            statement = statement.on_conflict_do_nothing(index_elements=["user_id"])
        elif database.get_bind().dialect.name == "sqlite":
            statement = sqlite_insert(TrialEntitlement).values(**values)
            statement = statement.on_conflict_do_nothing(index_elements=["user_id"])
        else:
            existing = await database.scalar(
                select(TrialEntitlement.id).where(
                    TrialEntitlement.user_id == user_id
                )
            )
            if existing is None:
                database.add(TrialEntitlement(**values))
                await database.flush()
            return
        await database.execute(statement)

    @staticmethod
    async def _locked_entitlement(
        database: AsyncSession,
        user_id: int,
    ) -> TrialEntitlement:
        entitlement = (
            await database.execute(
                select(TrialEntitlement)
                .where(TrialEntitlement.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one()
        return entitlement

    @staticmethod
    async def _locked_run(
        database: AsyncSession,
        run_id: UUID,
    ) -> GenerationRun:
        run = (
            await database.execute(
                select(GenerationRun)
                .where(GenerationRun.id == run_id)
                .with_for_update()
            )
        ).scalar_one()
        return run

    @staticmethod
    def _add_transition(
        database: AsyncSession,
        *,
        reservation: TrialReservation,
        transition: str,
        entries: tuple[tuple[str, int], ...],
        payload: dict | None = None,
    ) -> None:
        transition_key = f"{reservation.key}:{transition}"
        for bucket, amount in entries:
            database.add(
                UsageLedger(
                    user_id=reservation.user_id,
                    run_id=reservation.run_id,
                    bucket=bucket,
                    entry_type=f"trial.{transition}",
                    amount=amount,
                    idempotency_key=f"{transition_key}:{bucket}",
                    payload={
                        "transition_key": transition_key,
                        "trial_units": 1,
                        **(payload or {}),
                    },
                )
            )

    async def reserve_trial(
        self,
        user_id: int,
        run_id: UUID,
        *,
        request_id: str | None = None,
    ) -> TrialReservation:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("trial user_id is invalid")
        if not isinstance(run_id, UUID):
            raise ValueError("trial run_id is invalid")
        async with self._user_guard(user_id):
            async with self._sessions() as database, database.begin():
                await self._validate_verified_owned_run(
                    database,
                    user_id=user_id,
                    run_id=run_id,
                )
                await self._ensure_entitlement(database, user_id=user_id)
                entitlement = await self._locked_entitlement(database, user_id)
                base_key = self._reservation_key(
                    user_id,
                    run_id,
                    request_id,
                    max(1, entitlement.reservation_epoch),
                ).rsplit(":", 1)[0]
                if entitlement.state == "reserved":
                    current_key = entitlement.reservation_key or ""
                    if current_key.rsplit(":", 1)[0] == base_key:
                        return TrialReservation(
                            user_id=user_id,
                            run_id=run_id,
                            key=current_key,
                        )
                if entitlement.state != "available":
                    raise TrialUnavailable("Пробная полная сборка уже использована")
                if (
                    entitlement.granted_units
                    - entitlement.reserved_units
                    - entitlement.consumed_units
                    < 1
                ):
                    raise TrialUnavailable("Пробная полная сборка уже использована")
                entitlement.reservation_epoch += 1
                reservation = TrialReservation(
                    user_id=user_id,
                    run_id=run_id,
                    key=self._reservation_key(
                        user_id,
                        run_id,
                        request_id,
                        entitlement.reservation_epoch,
                    ),
                )
                entitlement.state = "reserved"
                entitlement.reserved_units = 1
                entitlement.reservation_key = reservation.key
                self._add_transition(
                    database,
                    reservation=reservation,
                    transition="reserve",
                    entries=(
                        ("trial_available", -1),
                        ("trial_reserved", 1),
                    ),
                )
        return reservation

    async def reserve_trial_in_session(
        self,
        database: AsyncSession,
        user_id: int,
        run_id: UUID,
        *,
        request_id: str | None = None,
    ) -> TrialReservation:
        """Reserve a trial as part of the caller's durable enqueue transaction."""

        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise ValueError("trial user_id is invalid")
        if not isinstance(run_id, UUID):
            raise ValueError("trial run_id is invalid")
        await self._validate_verified_owned_run(
            database,
            user_id=user_id,
            run_id=run_id,
        )
        await self._ensure_entitlement(database, user_id=user_id)
        entitlement = await self._locked_entitlement(database, user_id)
        base_key = self._reservation_key(
            user_id,
            run_id,
            request_id,
            max(1, entitlement.reservation_epoch),
        ).rsplit(":", 1)[0]
        if entitlement.state == "reserved":
            current_key = entitlement.reservation_key or ""
            if current_key.rsplit(":", 1)[0] == base_key:
                return TrialReservation(user_id=user_id, run_id=run_id, key=current_key)
        available = (
            entitlement.granted_units
            - entitlement.reserved_units
            - entitlement.consumed_units
        )
        if entitlement.state != "available" or available < 1:
            raise TrialUnavailable("Пробная полная сборка уже использована")
        entitlement.reservation_epoch += 1
        reservation = TrialReservation(
            user_id=user_id,
            run_id=run_id,
            key=self._reservation_key(
                user_id,
                run_id,
                request_id,
                entitlement.reservation_epoch,
            ),
        )
        entitlement.state = "reserved"
        entitlement.reserved_units = 1
        entitlement.reservation_key = reservation.key
        self._add_transition(
            database,
            reservation=reservation,
            transition="reserve",
            entries=(("trial_available", -1), ("trial_reserved", 1)),
        )
        return reservation

    async def can_start_trial(self, user_id: int) -> bool:
        async with self._sessions() as database:
            verified = await database.scalar(
                select(
                    exists().where(
                        UserIdentity.user_id == user_id,
                        UserIdentity.email_verified.is_(True),
                    )
                )
            )
            if not verified:
                return False
            entitlement = await database.scalar(
                select(TrialEntitlement).where(TrialEntitlement.user_id == user_id)
            )
            return entitlement is None or (
                entitlement.state == "available"
                and entitlement.granted_units
                - entitlement.reserved_units
                - entitlement.consumed_units
                >= 1
            )

    async def consume_trial(
        self,
        reservation: TrialReservation,
        *,
        reason: str,
    ) -> bool:
        reason = reason.strip()
        if not reason or len(reason) > 128 or "\x00" in reason:
            raise ValueError("trial consume reason is invalid")
        async with self._user_guard(reservation.user_id):
            async with self._sessions() as database, database.begin():
                entitlement = await self._locked_entitlement(
                    database, reservation.user_id
                )
                if (
                    entitlement.state == "consumed"
                    and entitlement.reservation_key == reservation.key
                ):
                    return True
                if (
                    entitlement.state != "reserved"
                    or entitlement.reservation_key != reservation.key
                ):
                    raise TrialUnavailable("Пробная сборка не зарезервирована")
                entitlement.state = "consumed"
                entitlement.reserved_units = 0
                entitlement.consumed_units = 1
                self._add_transition(
                    database,
                    reservation=reservation,
                    transition="debit",
                    entries=(
                        ("trial_reserved", -1),
                        ("trial_consumed", 1),
                    ),
                    payload={"reason": reason},
                )
        return True

    @staticmethod
    async def _usable_result_exists(
        database: AsyncSession,
        run_id: UUID,
    ) -> bool:
        accepted_artifacts = (
            await database.execute(
                select(GenerationArtifact)
                .where(
                    GenerationArtifact.run_id == run_id,
                    GenerationArtifact.quality_status.in_(("accepted", "verified")),
                )
                .order_by(GenerationArtifact.revision.desc())
            )
        ).scalars().all()
        for record in accepted_artifacts:
            payload = record.config.get("artifact") if isinstance(record.config, dict) else None
            if not isinstance(payload, dict):
                continue
            try:
                candidate = WidgetArtifact.from_dict(payload)
            except (KeyError, TypeError, ValueError):
                continue
            if not validate_artifact(
                candidate,
                previous_revision=max(0, candidate.revision - 1),
            ):
                return True
        draft_payloads = (
            await database.execute(
                select(GenerationEvent.payload)
                .where(
                    GenerationEvent.run_id == run_id,
                    GenerationEvent.event_type == "artifact.draft_staged",
                )
                .order_by(GenerationEvent.sequence.desc())
                .limit(50)
            )
        ).scalars().all()
        for payload in draft_payloads:
            artifact = payload.get("artifact") if isinstance(payload, dict) else None
            if not isinstance(artifact, dict):
                continue
            try:
                candidate = WidgetArtifact.from_dict(artifact)
            except (KeyError, TypeError, ValueError):
                continue
            previous_revision = max(0, candidate.revision - 1)
            if not validate_artifact(
                candidate,
                previous_revision=previous_revision,
            ):
                return True
        return False

    async def compensate_if_eligible(
        self,
        reservation: TrialReservation,
        *,
        failure_kind: TrialFailureKind,
    ) -> bool:
        if not isinstance(failure_kind, TrialFailureKind):
            raise ValueError("trial failure kind is invalid")
        if failure_kind not in COMPENSATABLE_FAILURES:
            raise TrialCompensationDenied(
                "Ошибка пользователя, контента или проверки расходует пробную сборку"
            )
        async with self._user_guard(reservation.user_id):
            async with self._sessions() as database, database.begin():
                # Any transaction needing both rows locks run -> entitlement.
                # Worker finalization holds this run lock while materializing,
                # so result eligibility and compensation are one decision.
                await self._locked_run(database, reservation.run_id)
                entitlement = await self._locked_entitlement(
                    database, reservation.user_id
                )
                compensation_key = f"{reservation.key}:compensation:trial_available"
                existing = await database.scalar(
                    select(UsageLedger.id).where(
                        UsageLedger.idempotency_key == compensation_key
                    )
                )
                if existing is not None:
                    return True
                if (
                    entitlement.state != "reserved"
                    or entitlement.reservation_key != reservation.key
                ):
                    raise TrialCompensationDenied(
                        "Пробная сборка уже завершена и не может быть возвращена"
                    )
                if await self._usable_result_exists(database, reservation.run_id):
                    raise TrialCompensationDenied(
                        "У пробной сборки уже есть полезный результат, вернуть её нельзя"
                    )
                entitlement.state = "available"
                entitlement.reserved_units = 0
                entitlement.reservation_key = None
                self._add_transition(
                    database,
                    reservation=reservation,
                    transition="compensation",
                    entries=(
                        ("trial_reserved", -1),
                        ("trial_available", 1),
                    ),
                    payload={"failure_kind": failure_kind.value},
                )
        return True

    async def settle_failure(
        self,
        reservation: TrialReservation,
        *,
        failure_kind: TrialFailureKind,
    ) -> str:
        """Settle a failed reserved run exactly once.

        User/content/validation failures consume the trial. Platform/provider/
        infrastructure/model-output failures compensate only when no accepted or
        restorable result exists; otherwise the useful result consumes the trial.
        """

        if not isinstance(failure_kind, TrialFailureKind):
            raise ValueError("trial failure kind is invalid")
        if failure_kind not in COMPENSATABLE_FAILURES:
            await self.consume_trial(reservation, reason=failure_kind.value)
            return "consumed"
        try:
            await self.compensate_if_eligible(
                reservation,
                failure_kind=failure_kind,
            )
            return "compensated"
        except TrialCompensationDenied:
            async with self._sessions() as database:
                usable_result = await self._usable_result_exists(
                    database, reservation.run_id
                )
            if not usable_result:
                raise
            await self.consume_trial(
                reservation,
                reason=f"{failure_kind.value}:usable_result",
            )
            return "consumed"

    @asynccontextmanager
    async def _model_guard(self, model_call_id: UUID) -> AsyncIterator[None]:
        if self._dialect != "sqlite":
            yield
            return
        key = (id(self._bind), model_call_id)
        lock = _SQLITE_MODEL_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SQLITE_MODEL_LOCKS[key] = lock
        async with lock:
            yield

    async def record_model_call_usage(
        self,
        user_id: int,
        model_call_id: UUID,
    ) -> tuple[UsageLedger, ...]:
        async with self._model_guard(model_call_id):
            async with self._sessions() as database, database.begin():
                row = (
                    await database.execute(
                        select(ModelCall, GenerationRun.project_id)
                        .join(GenerationRun, ModelCall.run_id == GenerationRun.id)
                        .join(Project, GenerationRun.project_id == Project.id)
                        .where(
                            ModelCall.id == model_call_id,
                            Project.owner_user_id == user_id,
                        )
                    )
                ).one_or_none()
                if row is None:
                    raise ValueError("model call does not belong to user")
                call, project_id = row
                billable_tokens = call.input_tokens + call.output_tokens
                payload = {
                    "provider": call.provider,
                    "model": call.model,
                    "role": call.role,
                    "request_id": call.request_id,
                    "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens,
                    "thinking_tokens": call.thinking_tokens,
                    "billable_tokens": billable_tokens,
                    "cost_microusd": call.cost_microusd,
                    "pricing_snapshot": call.pricing_snapshot,
                }
                definitions = []
                if billable_tokens > 0:
                    definitions.append(("tokens", -billable_tokens))
                if call.cost_microusd > 0:
                    definitions.append(("cost_microusd", -call.cost_microusd))
                entries = []
                for bucket, amount in definitions:
                    idempotency_key = f"model-call:{call.id}:{bucket}"
                    values = {
                        "user_id": user_id,
                        "project_id": project_id,
                        "run_id": call.run_id,
                        "model_call_id": call.id,
                        "bucket": bucket,
                        "entry_type": "model.usage",
                        "amount": amount,
                        "idempotency_key": idempotency_key,
                        "payload": payload,
                    }
                    if database.get_bind().dialect.name == "postgresql":
                        statement = postgresql_insert(UsageLedger).values(**values)
                        statement = statement.on_conflict_do_nothing(
                            index_elements=["idempotency_key"]
                        )
                        await database.execute(statement)
                    elif database.get_bind().dialect.name == "sqlite":
                        statement = sqlite_insert(UsageLedger).values(**values)
                        statement = statement.on_conflict_do_nothing(
                            index_elements=["idempotency_key"]
                        )
                        await database.execute(statement)
                    else:
                        existing = await database.scalar(
                            select(UsageLedger.id).where(
                                UsageLedger.idempotency_key == idempotency_key
                            )
                        )
                        if existing is None:
                            database.add(UsageLedger(**values))
                            await database.flush()
                    entry = await database.scalar(
                        select(UsageLedger).where(
                            UsageLedger.idempotency_key == idempotency_key
                        )
                    )
                    if entry is None:
                        raise RuntimeError("model usage ledger entry was not persisted")
                    entries.append(entry)
                return tuple(entries)


class TrialSettlementReconciler:
    """Idempotently settle terminal trial runs and recover interrupted hooks."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory
        self._trials = TrialService(session_factory)

    async def _reservation(self, run_id: UUID) -> TrialReservation | None:
        async with self._sessions() as database:
            row = (
                await database.execute(
                    select(UsageLedger.user_id, UsageLedger.payload)
                    .where(
                        UsageLedger.run_id == run_id,
                        UsageLedger.entry_type == "trial.reserve",
                    )
                    .order_by(UsageLedger.created_at, UsageLedger.id)
                    .limit(1)
                )
            ).one_or_none()
        if row is None:
            return None
        user_id, payload = row
        transition_key = payload.get("transition_key") if isinstance(payload, dict) else None
        if not isinstance(transition_key, str) or not transition_key.endswith(":reserve"):
            raise RuntimeError(f"run {run_id} has an invalid trial reservation ledger")
        return TrialReservation(
            user_id=user_id,
            run_id=run_id,
            key=transition_key.removesuffix(":reserve"),
        )

    async def _record_model_usage(self, run_id: UUID, user_id: int) -> bool:
        async with self._sessions() as database:
            calls = list(
                (
                    await database.execute(
                        select(ModelCall).where(ModelCall.run_id == run_id)
                    )
                ).scalars()
            )
        spent = False
        for call in calls:
            if (
                call.provider_dispatched
                or call.input_tokens + call.output_tokens + call.thinking_tokens > 0
                or call.cost_microusd > 0
            ):
                spent = True
            await self._trials.record_model_call_usage(user_id, call.id)
        return spent

    async def settle_run(self, run_id: UUID) -> str | None:
        async with self._sessions() as database:
            run = await database.get(GenerationRun, run_id)
            if run is None or run.state not in {"completed", "failed", "cancelled"}:
                return None
            if run.trial_settlement is not None:
                return run.trial_settlement
            state = run.state
            failure_category = run.failure_category
        try:
            reservation = await self._reservation(run_id)
        except RuntimeError:
            logger.exception("quarantining invalid trial reservation for run %s", run_id)
            return await self._mark_settlement(run_id, "quarantined")
        if reservation is None:
            return await self._mark_settlement(run_id, "not_applicable")
        model_spent = await self._record_model_usage(run_id, reservation.user_id)
        if state == "completed":
            await self._trials.consume_trial(reservation, reason="completed")
            outcome = "consumed"
        elif state == "cancelled" and model_spent:
            await self._trials.consume_trial(
                reservation,
                reason="cancelled_after_model_spend",
            )
            outcome = "consumed"
        elif state == "cancelled":
            await self._trials.compensate_if_eligible(
                reservation,
                failure_kind=TrialFailureKind.PLATFORM,
            )
            outcome = "compensated"
        else:
            try:
                failure_kind = TrialFailureKind(failure_category)
            except (TypeError, ValueError):
                # Missing structured metadata is itself a platform failure. Never
                # infer billing from a mutable public error string.
                failure_kind = TrialFailureKind.PLATFORM
            outcome = await self._trials.settle_failure(
                reservation,
                failure_kind=failure_kind,
            )
        return await self._mark_settlement(run_id, outcome)

    async def _mark_settlement(self, run_id: UUID, outcome: str) -> str:
        async with self._sessions() as database, database.begin():
            run = (
                await database.execute(
                    select(GenerationRun)
                    .where(GenerationRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one()
            if run.trial_settlement is None:
                run.trial_settlement = outcome
                run.trial_settled_at = datetime.now(UTC)
            return run.trial_settlement

    async def reconcile(self, *, limit: int = 100) -> dict[UUID, str]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("settlement reconcile limit must be positive")
        async with self._sessions() as database:
            run_ids = list(
                (
                    await database.execute(
                        select(GenerationRun.id)
                        .where(
                            GenerationRun.state.in_(("completed", "failed", "cancelled")),
                            GenerationRun.trial_settled_at.is_(None),
                            exists().where(
                                UsageLedger.run_id == GenerationRun.id,
                                UsageLedger.entry_type == "trial.reserve",
                            ),
                        )
                        .order_by(GenerationRun.created_at, GenerationRun.id)
                        .limit(limit)
                    )
                ).scalars()
            )
        outcomes: dict[UUID, str] = {}
        for run_id in run_ids:
            try:
                outcome = await self.settle_run(run_id)
            except Exception:  # noqa: BLE001
                logger.exception("trial settlement recovery failed for run %s", run_id)
                continue
            if outcome is not None:
                outcomes[run_id] = outcome
        return outcomes


__all__ = [
    "TrialCompensationDenied",
    "TrialFailureKind",
    "TrialReservation",
    "TrialSettlementReconciler",
    "TrialService",
    "TrialUnavailable",
    "UnverifiedTrialUser",
]
