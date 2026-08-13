from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.catalog import PLAN_CATALOG, BillingPlan
from app.billing.contracts import (
    PaymentProvider,
    PaymentStatus,
    RecurringPaymentCommand,
)
from app.billing.payments import BillingError, BillingService, _request_fingerprint
from app.billing.receipts import (
    BillingReceiptSettings,
    ReceiptCustomerUnavailable,
    resolve_payment_receipt,
)
from app.db.models import User
from app.saas.models import BillingPaymentMethod, PaymentAttempt, Subscription


RENEWAL_DISPATCH_LEASE = timedelta(seconds=30)
RENEWAL_RETRY_DELAY = timedelta(days=1)
PROVIDER_IDEMPOTENCY_WINDOW = timedelta(hours=24)
PROVIDER_DISPATCH_SAFETY_MARGIN = timedelta(minutes=5)
RETRYABLE_RENEWAL_CANCELLATIONS = frozenset(
    {
        "insufficient_funds",
        "general_decline",
        "issuer_unavailable",
        "payment_method_limit_exceeded",
    }
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def renewal_idempotency_key(
    subscription_id: UUID,
    period_start: datetime,
    attempt_number: int,
) -> str:
    start = _aware_utc(period_start).isoformat()
    return f"renewal:{subscription_id}:{start}:{attempt_number}"


@dataclass(frozen=True, slots=True)
class _ClaimedRenewal:
    attempt_id: UUID
    lease_token: str
    command: RecurringPaymentCommand


class RenewalScheduler:
    """Create and dispatch one immutable renewal cycle at a time.

    This class is inert until explicitly called by the billing worker. It does
    not own a timer and therefore cannot silently enable recurring charges.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
        *,
        payment_service: BillingService,
        receipt_settings: BillingReceiptSettings,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not payment_service.uses_provider(provider):
            raise ValueError("renewal scheduler and billing service must share provider")
        self._sessions = session_factory
        self._provider = provider
        self._payments = payment_service
        self._receipt_settings = receipt_settings
        self._clock = clock

    def _now(self) -> datetime:
        return _aware_utc(self._clock())

    async def _disable_locked(
        self,
        subscription: Subscription,
        method: BillingPaymentMethod | None,
        *,
        now: datetime,
        invalid_method: bool = False,
    ) -> None:
        subscription.auto_renew = False
        subscription.next_renewal_at = None
        subscription.auto_renew_disabled_at = now
        if method is not None:
            method.status = "invalid" if invalid_method else "disabled"
            method.disabled_at = now

    @staticmethod
    def _plan(subscription: Subscription) -> BillingPlan:
        try:
            plan = BillingPlan.from_snapshot(subscription.plan_snapshot)
        except (TypeError, ValueError) as error:
            raise BillingError("stored subscription plan snapshot is invalid") from error
        if (
            plan.code != subscription.plan_code
            or plan.fingerprint() != subscription.plan_fingerprint
        ):
            raise BillingError("stored subscription plan snapshot was modified")
        return plan

    @classmethod
    def _renewal_plan(cls, subscription: Subscription) -> BillingPlan:
        current = cls._plan(subscription)
        if current.renewal_plan_code is None:
            return current
        try:
            next_plan = PLAN_CATALOG[current.renewal_plan_code]
        except KeyError as error:
            raise BillingError("stored subscription renewal plan is unknown") from error
        # Introductory billing may have one transparent balance step
        # (500 ₽ for 15 days -> 1 500 ₽ for the remaining 15 days) before the
        # regular 2 000 ₽ monthly cycle. Every step is an immutable snapshot.
        return next_plan

    def _binding_valid(
        self,
        subscription: Subscription,
        method: BillingPaymentMethod | None,
    ) -> bool:
        return bool(
            method is not None
            and method.status == "active"
            and method.user_id == subscription.user_id
            and subscription.payment_method_id == method.id
            and method.provider == subscription.provider == self._provider.name
            and subscription.merchant_account_fingerprint
            == method.merchant_account_fingerprint
            == self._payments.merchant_account_fingerprint
        )

    async def _due_subscription_ids(self, *, now: datetime, limit: int) -> list[UUID]:
        async with self._sessions() as database:
            return list(
                await database.scalars(
                    select(Subscription.id)
                    .where(
                        Subscription.status == "active",
                        Subscription.auto_renew.is_(True),
                        Subscription.next_renewal_at.is_not(None),
                        Subscription.next_renewal_at <= now,
                    )
                    .order_by(Subscription.next_renewal_at, Subscription.id)
                    .limit(limit)
                )
            )

    async def _ensure_primary(
        self,
        subscription_id: UUID,
        *,
        now: datetime,
    ) -> UUID | None:
        period_start: datetime | None = None
        try:
            async with self._sessions() as database, database.begin():
                subscription = await database.scalar(
                    select(Subscription)
                    .where(Subscription.id == subscription_id)
                    .with_for_update()
                )
                if (
                    subscription is None
                    or subscription.status != "active"
                    or not subscription.auto_renew
                    or subscription.next_renewal_at is None
                    or _aware_utc(subscription.next_renewal_at) > now
                    or subscription.current_period_end is None
                ):
                    return None
                await database.scalar(
                    select(User)
                    .where(User.id == subscription.user_id)
                    .with_for_update()
                )
                method = (
                    await database.scalar(
                        select(BillingPaymentMethod)
                        .where(BillingPaymentMethod.id == subscription.payment_method_id)
                        .with_for_update()
                    )
                    if subscription.payment_method_id is not None
                    else None
                )
                if not self._binding_valid(subscription, method):
                    await self._disable_locked(subscription, method, now=now)
                    return None

                period_start = _aware_utc(subscription.current_period_end)
                existing = await database.scalar(
                    select(PaymentAttempt).where(
                        PaymentAttempt.subscription_id == subscription.id,
                        PaymentAttempt.billing_period_start == period_start,
                        PaymentAttempt.renewal_attempt_number == 1,
                    )
                )
                if existing is not None:
                    return existing.id

                plan = self._renewal_plan(subscription)
                try:
                    receipt = await resolve_payment_receipt(
                        database,
                        user_id=subscription.user_id,
                        plan=plan,
                        settings=self._receipt_settings,
                    )
                except ReceiptCustomerUnavailable:
                    await self._disable_locked(subscription, method, now=now)
                    return None
                period_end = period_start + timedelta(days=plan.period_days)
                attempt = PaymentAttempt(
                    user_id=subscription.user_id,
                    provider=subscription.provider,
                    merchant_account_fingerprint=str(
                        subscription.merchant_account_fingerprint
                    ),
                    purpose="renewal",
                    subscription_id=subscription.id,
                    payment_method_id=method.id,
                    billing_period_start=period_start,
                    billing_period_end=period_end,
                    renewal_attempt_number=1,
                    retry_of_payment_attempt_id=None,
                    retry_of_renewal_attempt_number=None,
                    next_dispatch_at=None,
                    auto_renew_requested=True,
                    save_payment_method_requested=False,
                    consent_version=method.consent_version,
                    consented_at=method.consented_at,
                    idempotency_key=renewal_idempotency_key(
                        subscription.id, period_start, 1
                    ),
                    plan_code=plan.code,
                    plan_snapshot=plan.snapshot(),
                    plan_fingerprint=plan.fingerprint(),
                    amount_minor=plan.amount.amount_minor,
                    currency=plan.amount.currency,
                    status="creating",
                    payload={},
                )
                database.add(attempt)
                await database.flush()
                provider_key = self._payments._provider_idempotency_key(attempt.id)
                expires_at = now + PROVIDER_IDEMPOTENCY_WINDOW
                command = RecurringPaymentCommand(
                    idempotency_key=provider_key,
                    payment_method_id=method.provider_payment_method_id,
                    amount=plan.amount,
                    description=plan.title,
                    metadata=self._payments._metadata(attempt),
                    receipt=receipt,
                )
                attempt.first_dispatched_at = now
                attempt.provider_idempotency_expires_at = expires_at
                attempt.request_fingerprint = _request_fingerprint(command)
                attempt.payload = {
                    "provider_idempotency_key": provider_key,
                    "first_dispatched_at": now.isoformat(),
                    "provider_idempotency_expires_at": expires_at.isoformat(),
                }
                return attempt.id
        except IntegrityError:
            if period_start is None:
                return None
            async with self._sessions() as database:
                return await database.scalar(
                    select(PaymentAttempt.id).where(
                        PaymentAttempt.subscription_id == subscription_id,
                        PaymentAttempt.billing_period_start == period_start,
                        PaymentAttempt.renewal_attempt_number == 1,
                    )
                )

    async def _claim(self, attempt_id: UUID, *, now: datetime) -> _ClaimedRenewal | None:
        lease_token = uuid4().hex
        async with self._sessions() as database, database.begin():
            attempt = await database.scalar(
                select(PaymentAttempt)
                .where(PaymentAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None or attempt.purpose != "renewal":
                return None
            if attempt.status == "scheduled":
                if attempt.next_dispatch_at is None or _aware_utc(attempt.next_dispatch_at) > now:
                    return None
            elif attempt.status == "dispatching":
                lease_expires = attempt.payload.get("dispatch_lease_expires_at")
                if not isinstance(lease_expires, str):
                    return None
                if datetime.fromisoformat(lease_expires).astimezone(UTC) > now:
                    return None
            elif attempt.status == "dispatch_unknown":
                if attempt.provider_idempotency_expires_at is None:
                    return None
            elif attempt.status != "creating":
                return None

            subscription = await database.scalar(
                select(Subscription)
                .where(Subscription.id == attempt.subscription_id)
                .with_for_update()
            )
            method = (
                await database.scalar(
                    select(BillingPaymentMethod)
                    .where(BillingPaymentMethod.id == attempt.payment_method_id)
                    .with_for_update()
                )
                if attempt.payment_method_id is not None
                else None
            )
            if subscription is None or not subscription.auto_renew or not self._binding_valid(subscription, method):
                if subscription is not None:
                    await self._disable_locked(subscription, method, now=now)
                attempt.status = "cancelled"
                return None
            plan = self._renewal_plan(subscription)
            if (
                attempt.plan_code != plan.code
                or attempt.plan_fingerprint != plan.fingerprint()
            ):
                await self._disable_locked(subscription, method, now=now)
                attempt.status = "dispatch_unknown"
                return None
            try:
                receipt = await resolve_payment_receipt(
                    database,
                    user_id=attempt.user_id,
                    plan=plan,
                    settings=self._receipt_settings,
                )
            except ReceiptCustomerUnavailable:
                await self._disable_locked(subscription, method, now=now)
                attempt.status = "cancelled"
                return None

            provider_key = self._payments._provider_idempotency_key(attempt.id)
            first_dispatched_at = attempt.first_dispatched_at or now
            provider_idempotency_expires_at = (
                attempt.provider_idempotency_expires_at
                or now + PROVIDER_IDEMPOTENCY_WINDOW
            )
            if (
                now
                >= _aware_utc(provider_idempotency_expires_at)
                - PROVIDER_DISPATCH_SAFETY_MARGIN
            ):
                await self._disable_locked(subscription, method, now=now)
                attempt.status = "dispatch_unknown"
                return None
            command = RecurringPaymentCommand(
                idempotency_key=provider_key,
                payment_method_id=method.provider_payment_method_id,
                amount=plan.amount,
                description=plan.title,
                metadata=self._payments._metadata(attempt),
                receipt=receipt,
            )
            fingerprint = _request_fingerprint(command)
            request_fingerprint = attempt.request_fingerprint or fingerprint
            if attempt.request_fingerprint not in {None, fingerprint}:
                await self._disable_locked(subscription, method, now=now)
                attempt.status = "dispatch_unknown"
                return None
            payload = dict(attempt.payload)
            payload.update(
                {
                    "provider_idempotency_key": provider_key,
                    "first_dispatched_at": _aware_utc(
                        first_dispatched_at
                    ).isoformat(),
                    "provider_idempotency_expires_at": _aware_utc(
                        provider_idempotency_expires_at
                    ).isoformat(),
                    "dispatch_lease_token": lease_token,
                    "dispatch_lease_expires_at": (
                        now + RENEWAL_DISPATCH_LEASE
                    ).isoformat(),
                }
            )
            expected_status = attempt.status
            original_updated_at = attempt.updated_at
            conditions = [
                PaymentAttempt.id == attempt.id,
                PaymentAttempt.status == expected_status,
            ]
            if expected_status == "dispatching" and original_updated_at is not None:
                conditions.append(PaymentAttempt.updated_at == original_updated_at)
            claimed = await database.execute(
                update(PaymentAttempt)
                .where(*conditions)
                .values(
                    status="dispatching",
                    first_dispatched_at=first_dispatched_at,
                    provider_idempotency_expires_at=provider_idempotency_expires_at,
                    request_fingerprint=request_fingerprint,
                    payload=payload,
                    updated_at=now,
                )
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                return None
            return _ClaimedRenewal(attempt.id, lease_token, command)

    async def _mark_transport_unknown(self, claim: _ClaimedRenewal) -> None:
        async with self._sessions() as database, database.begin():
            attempt = await database.scalar(
                select(PaymentAttempt)
                .where(PaymentAttempt.id == claim.attempt_id)
                .with_for_update()
            )
            if attempt is not None and attempt.payload.get("dispatch_lease_token") == claim.lease_token:
                attempt.status = "dispatch_unknown"
                attempt.next_reconcile_at = self._now() + timedelta(seconds=60)

    async def _persist_response(self, claim: _ClaimedRenewal, payment) -> bool:
        expected_paid = payment.status is PaymentStatus.SUCCEEDED
        if (
            payment.status not in {
                PaymentStatus.PENDING,
                PaymentStatus.SUCCEEDED,
                PaymentStatus.CANCELLED,
            }
            or payment.amount != claim.command.amount
            or dict(payment.metadata) != dict(claim.command.metadata)
            or payment.paid is not expected_paid
        ):
            raise BillingError("provider recurring response does not match command")
        async with self._sessions() as database, database.begin():
            attempt = await database.scalar(
                select(PaymentAttempt)
                .where(PaymentAttempt.id == claim.attempt_id)
                .with_for_update()
            )
            if attempt is None or attempt.payload.get("dispatch_lease_token") != claim.lease_token:
                return False
            attempt.provider_payment_id = payment.provider_payment_id
            attempt.status = "pending"
            attempt.next_reconcile_at = self._now()
            attempt.payload = {"test_mode": payment.test_mode}
            return True

    async def _schedule_retry_or_disable(self, attempt_id: UUID, *, now: datetime) -> None:
        async with self._sessions() as database, database.begin():
            attempt = await database.scalar(
                select(PaymentAttempt)
                .where(PaymentAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None or attempt.purpose != "renewal" or attempt.status != "cancelled":
                return
            subscription = await database.scalar(
                select(Subscription)
                .where(Subscription.id == attempt.subscription_id)
                .with_for_update()
            )
            method = (
                await database.scalar(
                    select(BillingPaymentMethod)
                    .where(BillingPaymentMethod.id == attempt.payment_method_id)
                    .with_for_update()
                )
                if attempt.payment_method_id is not None
                else None
            )
            if subscription is None or not subscription.auto_renew:
                return
            reason = attempt.payload.get("cancellation_reason", "unknown")
            if (
                attempt.renewal_attempt_number == 1
                and reason in RETRYABLE_RENEWAL_CANCELLATIONS
            ):
                existing = await database.scalar(
                    select(PaymentAttempt).where(
                        PaymentAttempt.subscription_id == attempt.subscription_id,
                        PaymentAttempt.billing_period_start == attempt.billing_period_start,
                        PaymentAttempt.renewal_attempt_number == 2,
                    )
                )
                if existing is None:
                    retry_at = now + RENEWAL_RETRY_DELAY
                    retry = PaymentAttempt(
                        user_id=attempt.user_id,
                        provider=attempt.provider,
                        merchant_account_fingerprint=attempt.merchant_account_fingerprint,
                        purpose="renewal",
                        subscription_id=attempt.subscription_id,
                        payment_method_id=attempt.payment_method_id,
                        billing_period_start=attempt.billing_period_start,
                        billing_period_end=attempt.billing_period_end,
                        renewal_attempt_number=2,
                        retry_of_payment_attempt_id=attempt.id,
                        retry_of_renewal_attempt_number=1,
                        next_dispatch_at=retry_at,
                        auto_renew_requested=True,
                        save_payment_method_requested=False,
                        consent_version=attempt.consent_version,
                        consented_at=attempt.consented_at,
                        idempotency_key=renewal_idempotency_key(
                            attempt.subscription_id,
                            attempt.billing_period_start,
                            2,
                        ),
                        plan_code=attempt.plan_code,
                        plan_snapshot=dict(attempt.plan_snapshot),
                        plan_fingerprint=attempt.plan_fingerprint,
                        amount_minor=attempt.amount_minor,
                        currency=attempt.currency,
                        status="scheduled",
                        payload={},
                    )
                    database.add(retry)
                    subscription.next_renewal_at = retry_at
                return
            await self._disable_locked(
                subscription,
                method,
                now=now,
                invalid_method=reason == "permission_revoked",
            )

    async def _terminal_cancelled_ids(self, *, limit: int) -> list[UUID]:
        async with self._sessions() as database:
            return list(
                await database.scalars(
                    select(PaymentAttempt.id)
                    .join(Subscription, Subscription.id == PaymentAttempt.subscription_id)
                    .where(
                        PaymentAttempt.purpose == "renewal",
                        PaymentAttempt.status == "cancelled",
                        Subscription.auto_renew.is_(True),
                    )
                    .order_by(PaymentAttempt.updated_at, PaymentAttempt.id)
                    .limit(limit)
                )
            )

    async def _dispatch(self, attempt_id: UUID, *, now: datetime) -> str | None:
        claim = await self._claim(attempt_id, now=now)
        if claim is None:
            return None
        try:
            payment = await self._provider.create_recurring_payment(claim.command)
        except Exception:
            await self._mark_transport_unknown(claim)
            return "dispatch_unknown"
        if not await self._persist_response(claim, payment):
            return "lost_lease"
        if payment.status is PaymentStatus.PENDING:
            return "pending"
        await self._payments.apply_verified_payment(
            claim.attempt_id,
            payment,
            source="dispatch",
        )
        if payment.status is PaymentStatus.CANCELLED:
            await self._schedule_retry_or_disable(claim.attempt_id, now=self._now())
        return payment.status.value

    async def run_once(self, *, limit: int = 100) -> dict[UUID, str]:
        if not 1 <= limit <= 1000:
            raise ValueError("renewal limit must be between 1 and 1000")
        now = self._now()
        results: dict[UUID, str] = {}

        for attempt_id in await self._terminal_cancelled_ids(limit=limit):
            await self._schedule_retry_or_disable(attempt_id, now=now)

        retry_ids: list[UUID]
        async with self._sessions() as database:
            retry_ids = list(
                await database.scalars(
                    select(PaymentAttempt.id)
                    .where(
                        PaymentAttempt.purpose == "renewal",
                        or_(
                            (
                                (PaymentAttempt.status == "scheduled")
                                & (PaymentAttempt.next_dispatch_at <= now)
                            ),
                            PaymentAttempt.status == "creating",
                            (
                                (PaymentAttempt.status == "dispatch_unknown")
                                & (PaymentAttempt.next_reconcile_at <= now)
                            ),
                        ),
                    )
                    .order_by(PaymentAttempt.created_at, PaymentAttempt.id)
                    .limit(limit)
                )
            )

        seen = set(retry_ids)
        for subscription_id in await self._due_subscription_ids(now=now, limit=limit):
            attempt_id = await self._ensure_primary(subscription_id, now=now)
            if attempt_id is not None and attempt_id not in seen:
                retry_ids.append(attempt_id)
                seen.add(attempt_id)
        for attempt_id in retry_ids[:limit]:
            outcome = await self._dispatch(attempt_id, now=self._now())
            if outcome is not None:
                results[attempt_id] = outcome
        return results


__all__ = [
    "PROVIDER_IDEMPOTENCY_WINDOW",
    "RENEWAL_DISPATCH_LEASE",
    "RENEWAL_RETRY_DELAY",
    "RETRYABLE_RENEWAL_CANCELLATIONS",
    "RenewalScheduler",
    "renewal_idempotency_key",
]
