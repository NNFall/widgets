from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.contracts import Money, PaymentProvider, PaymentStatus
from app.billing.payments import BillingService
from app.saas.models import PaymentAttempt


RECONCILE_INTERVAL = timedelta(seconds=60)
RECONCILE_LEASE = timedelta(seconds=30)

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _ClaimedPayment:
    attempt_id: UUID
    lease_token: str
    provider_payment_id: str
    expected_amount: Money
    expected_metadata: dict[str, str]


class PaymentReconciler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
        *,
        payment_service: BillingService,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not payment_service.uses_provider(provider):
            raise ValueError(
                "payment reconciler and billing service must share one provider"
            )
        self._sessions = session_factory
        self._provider = provider
        self._payments = payment_service
        self._clock = clock

    def _due_predicate(self, now: datetime):
        return self._due_predicate_for(
            now=now,
            provider_name=self._provider.name,
            merchant_account_fingerprint=(self._payments.merchant_account_fingerprint),
        )

    @staticmethod
    def _due_predicate_for(
        *,
        now: datetime,
        provider_name: str,
        merchant_account_fingerprint: str,
    ):
        return (
            PaymentAttempt.provider == provider_name,
            PaymentAttempt.merchant_account_fingerprint == merchant_account_fingerprint,
            PaymentAttempt.provider_payment_id.is_not(None),
            PaymentAttempt.status.in_(
                ("pending", "creating", "failed", "dispatch_unknown")
            ),
            or_(
                PaymentAttempt.next_reconcile_at.is_(None),
                PaymentAttempt.next_reconcile_at <= now,
            ),
            or_(
                PaymentAttempt.reconcile_lease_token.is_(None),
                PaymentAttempt.reconcile_lease_expires_at <= now,
            ),
        )

    @classmethod
    def _candidate_statement(
        cls,
        *,
        now: datetime,
        limit: int,
        provider_name: str,
        merchant_account_fingerprint: str,
    ):
        return (
            select(PaymentAttempt.id)
            .where(
                *cls._due_predicate_for(
                    now=now,
                    provider_name=provider_name,
                    merchant_account_fingerprint=merchant_account_fingerprint,
                )
            )
            .order_by(
                PaymentAttempt.next_reconcile_at.asc().nulls_first(),
                PaymentAttempt.created_at.asc(),
            )
            .limit(limit)
        )

    async def _due_attempt_ids(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> list[UUID]:
        async with self._sessions() as database:
            return list(
                await database.scalars(
                    self._candidate_statement(
                        now=now,
                        limit=limit,
                        provider_name=self._provider.name,
                        merchant_account_fingerprint=(
                            self._payments.merchant_account_fingerprint
                        ),
                    )
                )
            )

    async def _claim_attempt(
        self,
        attempt_id: UUID,
        *,
        now: datetime,
    ) -> _ClaimedPayment | None:
        lease_token = uuid4().hex
        async with self._sessions() as database, database.begin():
            claim = await database.execute(
                update(PaymentAttempt)
                .where(
                    PaymentAttempt.id == attempt_id,
                    *self._due_predicate(now),
                )
                .values(
                    reconcile_lease_token=lease_token,
                    reconcile_lease_expires_at=now + RECONCILE_LEASE,
                    last_reconciled_at=now,
                    next_reconcile_at=now + RECONCILE_INTERVAL,
                )
                .returning(PaymentAttempt.id)
            )
            if claim.scalar_one_or_none() is None:
                return None
            attempt = await database.get(PaymentAttempt, attempt_id)
            if attempt is None or attempt.provider_payment_id is None:
                return None
            expected_amount, expected_metadata = (
                self._payments.verification_expectations(attempt)
            )
            return _ClaimedPayment(
                attempt_id=attempt.id,
                lease_token=lease_token,
                provider_payment_id=attempt.provider_payment_id,
                expected_amount=expected_amount,
                expected_metadata=expected_metadata,
            )

    async def _release_lease(self, claim: _ClaimedPayment) -> None:
        async with self._sessions() as database, database.begin():
            await database.execute(
                update(PaymentAttempt)
                .where(
                    PaymentAttempt.id == claim.attempt_id,
                    PaymentAttempt.reconcile_lease_token == claim.lease_token,
                )
                .values(
                    reconcile_lease_token=None,
                    reconcile_lease_expires_at=None,
                )
            )

    async def run_once(self, *, limit: int = 100) -> dict[UUID, str]:
        if not 1 <= limit <= 1000:
            raise ValueError("reconciliation limit must be between 1 and 1000")
        now = _aware_utc(self._clock())
        candidate_ids = await self._due_attempt_ids(now=now, limit=limit)
        results: dict[UUID, str] = {}
        for attempt_id in candidate_ids:
            claim = await self._claim_attempt(
                attempt_id,
                now=_aware_utc(self._clock()),
            )
            if claim is None:
                continue
            try:
                try:
                    payment = await self._provider.get_payment(
                        claim.provider_payment_id,
                        expected_amount=claim.expected_amount,
                        expected_metadata=claim.expected_metadata,
                    )
                except Exception:
                    logger.warning("billing reconciliation provider request failed")
                    results[claim.attempt_id] = "provider_error"
                    continue
                if payment.status is PaymentStatus.PENDING:
                    results[claim.attempt_id] = "pending"
                    continue
                await self._payments.apply_verified_payment(
                    claim.attempt_id,
                    payment,
                    source="reconciliation",
                )
                results[claim.attempt_id] = payment.status.value
            finally:
                await self._release_lease(claim)
        return results


__all__ = [
    "PaymentReconciler",
    "RECONCILE_INTERVAL",
    "RECONCILE_LEASE",
]
