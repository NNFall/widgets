from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.catalog import PLAN_CATALOG, BillingPlan
from app.billing.contracts import CheckoutCommand, Money, PaymentProvider, PaymentStatus
from app.db.models import User
from app.saas.models import (
    PaymentAttempt,
    PaymentWebhookEvent,
    Subscription,
    UsageLedger,
)


class BillingError(RuntimeError):
    pass


class UnknownPlan(BillingError):
    pass


class CheckoutIdempotencyConflict(BillingError):
    pass


class PaymentNotFound(BillingError):
    pass


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    payment_id: UUID
    plan_code: str
    status: str
    amount_minor: int
    currency: str
    checkout_url: str
    created_at: datetime
    created: bool


@dataclass(frozen=True, slots=True)
class FulfillmentResult:
    payment_id: UUID
    subscription_id: UUID | None
    processed: bool


_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_CHECKOUT_CREATION_LEASE = timedelta(seconds=30)


class BillingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        self._checkout_locks: WeakValueDictionary[tuple[int, str], asyncio.Lock] = (
            WeakValueDictionary()
        )
        self._fulfillment_locks: WeakValueDictionary[int, asyncio.Lock] = (
            WeakValueDictionary()
        )

    async def close(self) -> None:
        await self._provider.aclose()

    @staticmethod
    def _plan(code: str) -> BillingPlan:
        try:
            return PLAN_CATALOG[code]
        except KeyError as error:
            raise UnknownPlan("unknown billing plan") from error

    @staticmethod
    def _metadata(attempt: PaymentAttempt) -> dict[str, str]:
        return {
            "payment_attempt_id": str(attempt.id),
            "user_id": str(attempt.user_id),
            "plan_code": attempt.plan_code,
            "plan_fingerprint": attempt.plan_fingerprint,
        }

    @staticmethod
    def _provider_idempotency_key(attempt_id: UUID) -> str:
        # YooKassa idempotency is scoped to the merchant account, while the
        # public key is scoped per Kaigo user. Deriving the provider key from
        # our globally unique attempt prevents two users choosing the same
        # public key from sharing one provider payment. It also stays below
        # YooKassa's 64-character limit and is stable for crash recovery.
        return f"kaigo:{attempt_id}"

    @staticmethod
    def _stored_plan(attempt: PaymentAttempt) -> BillingPlan:
        try:
            plan = BillingPlan.from_snapshot(attempt.plan_snapshot)
        except (TypeError, ValueError) as error:
            raise BillingError("stored billing plan snapshot is invalid") from error
        if (
            attempt.plan_code != plan.code
            or attempt.plan_fingerprint != plan.fingerprint()
            or attempt.amount_minor != plan.amount.amount_minor
            or attempt.currency != plan.amount.currency
        ):
            raise BillingError("stored billing plan snapshot was modified")
        return plan

    @staticmethod
    def _checkout_result(attempt: PaymentAttempt, *, created: bool) -> CheckoutResult:
        if not attempt.checkout_url:
            raise BillingError("checkout is not ready")
        return CheckoutResult(
            payment_id=attempt.id,
            plan_code=attempt.plan_code,
            status=attempt.status,
            amount_minor=attempt.amount_minor,
            currency=attempt.currency,
            checkout_url=attempt.checkout_url,
            created_at=attempt.created_at,
            created=created,
        )

    async def _wait_for_checkout(
        self,
        *,
        user_id: int,
        idempotency_key: str,
        plan_fingerprint: str,
    ) -> CheckoutResult:
        for _attempt in range(200):
            await asyncio.sleep(0.05)
            async with self._sessions() as database:
                row = await database.scalar(
                    select(PaymentAttempt).where(
                        PaymentAttempt.user_id == user_id,
                        PaymentAttempt.idempotency_key == idempotency_key,
                    )
                )
                if row is None:
                    continue
                if row.plan_fingerprint != plan_fingerprint:
                    raise CheckoutIdempotencyConflict(
                        "idempotency key is already used for another plan"
                    )
                if row.checkout_url:
                    return self._checkout_result(row, created=False)
                if row.status == "failed":
                    raise BillingError("previous checkout creation failed")
        raise BillingError("checkout creation is still in progress")

    async def create_checkout(
        self,
        user_id: int,
        plan_code: str,
        idempotency_key: str,
    ) -> CheckoutResult:
        plan = self._plan(plan_code)
        return await self._create_checkout_with_plan(user_id, plan, idempotency_key)

    async def _create_checkout_with_plan(
        self,
        user_id: int,
        plan: BillingPlan,
        idempotency_key: str,
    ) -> CheckoutResult:
        if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise ValueError("invalid checkout idempotency key")
        lock = self._checkout_locks.setdefault((user_id, idempotency_key), asyncio.Lock())
        async with lock:
            created = False
            dispatch = False
            lease_token: str | None = None
            try:
                async with self._sessions() as database, database.begin():
                    user = await database.get(User, user_id)
                    if user is None:
                        raise PaymentNotFound("user not found")
                    attempt = await database.scalar(
                        select(PaymentAttempt)
                        .where(
                            PaymentAttempt.user_id == user_id,
                            PaymentAttempt.idempotency_key == idempotency_key,
                        )
                        .with_for_update()
                    )
                    if attempt is not None:
                        if attempt.plan_fingerprint != plan.fingerprint():
                            raise CheckoutIdempotencyConflict(
                                "idempotency key is already used for another plan"
                            )
                        if attempt.checkout_url:
                            return self._checkout_result(attempt, created=False)
                        updated_at = attempt.updated_at
                        if updated_at is not None and updated_at.tzinfo is None:
                            updated_at = updated_at.replace(tzinfo=UTC)
                        now = datetime.now(UTC)
                        if (
                            attempt.status == "creating"
                            and updated_at is not None
                            and updated_at <= now - _CHECKOUT_CREATION_LEASE
                        ):
                            lease_token = uuid4().hex
                            attempt.updated_at = now
                            attempt.payload = {"checkout_lease_token": lease_token}
                            dispatch = True
                        elif attempt.status == "failed":
                            # The provider request may have been accepted even
                            # when our process observed a transport failure.
                            # Retrying the same attempt with the deterministic
                            # provider key is the only safe way to reconcile it.
                            lease_token = uuid4().hex
                            attempt.status = "creating"
                            attempt.updated_at = now
                            attempt.payload = {"checkout_lease_token": lease_token}
                            dispatch = True
                    else:
                        attempt = PaymentAttempt(
                            user_id=user_id,
                            provider=self._provider.name,
                            idempotency_key=idempotency_key,
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
                        lease_token = uuid4().hex
                        attempt.payload = {"checkout_lease_token": lease_token}
                        created = True
                        dispatch = True
                    attempt_id = attempt.id
                    metadata = self._metadata(attempt)
            except IntegrityError:
                # Another process may have committed the same per-user key
                # between our SELECT and INSERT. The winner owns the provider
                # call; this request becomes an ordinary replay.
                async with self._sessions() as database:
                    attempt = await database.scalar(
                        select(PaymentAttempt).where(
                            PaymentAttempt.user_id == user_id,
                            PaymentAttempt.idempotency_key == idempotency_key,
                        )
                    )
                    if attempt is None:
                        raise
                    if attempt.plan_fingerprint != plan.fingerprint():
                        raise CheckoutIdempotencyConflict(
                            "idempotency key is already used for another plan"
                        )
                    if attempt.checkout_url:
                        return self._checkout_result(attempt, created=False)
                    attempt_id = attempt.id
                    metadata = self._metadata(attempt)

            if not dispatch and attempt.status == "creating":
                return await self._wait_for_checkout(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    plan_fingerprint=plan.fingerprint(),
                )

            try:
                if lease_token is None:
                    raise BillingError("checkout creation lease is missing")
                checkout = await self._provider.create_checkout(
                    CheckoutCommand(
                        idempotency_key=self._provider_idempotency_key(attempt_id),
                        amount=plan.amount,
                        description=plan.title,
                        metadata=metadata,
                    )
                )
                if (
                    checkout.amount != plan.amount
                    or dict(checkout.metadata) != metadata
                    or checkout.status is not PaymentStatus.PENDING
                    or checkout.paid
                ):
                    raise BillingError("provider checkout response does not match command")
            except Exception:
                async with self._sessions() as database, database.begin():
                    failed = await database.scalar(
                        select(PaymentAttempt)
                        .where(PaymentAttempt.id == attempt_id)
                        .with_for_update()
                    )
                    if (
                        failed is not None
                        and failed.checkout_url is None
                        and failed.payload.get("checkout_lease_token") == lease_token
                    ):
                        failed.status = "failed"
                raise

            lost_lease = False
            async with self._sessions() as database, database.begin():
                attempt = await database.scalar(
                    select(PaymentAttempt)
                    .where(PaymentAttempt.id == attempt_id)
                    .with_for_update()
                )
                if attempt is None:
                    raise PaymentNotFound("payment attempt disappeared")
                if attempt.payload.get("checkout_lease_token") != lease_token:
                    lost_lease = True
                    result = None
                else:
                    attempt.provider_payment_id = checkout.provider_payment_id
                    attempt.checkout_url = checkout.checkout_url
                    attempt.status = checkout.status.value
                    attempt.payload = {"test_mode": checkout.test_mode}
                    await database.flush()
                    result = self._checkout_result(attempt, created=created)
            if lost_lease:
                return await self._wait_for_checkout(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    plan_fingerprint=plan.fingerprint(),
                )
            if result is None:  # pragma: no cover - guarded by lost_lease
                raise BillingError("checkout result is unavailable")
            return result

    async def resume_checkout(
        self,
        user_id: int,
        payment_id: UUID,
    ) -> CheckoutResult:
        async with self._sessions() as database:
            attempt = await database.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.id == payment_id,
                    PaymentAttempt.user_id == user_id,
                )
            )
            if attempt is None:
                raise PaymentNotFound("payment attempt not found")
            if attempt.status not in {"creating", "pending", "failed"}:
                raise BillingError("payment attempt cannot be resumed")
            plan = self._stored_plan(attempt)
            idempotency_key = attempt.idempotency_key
        return await self._create_checkout_with_plan(user_id, plan, idempotency_key)

    async def handle_notification(self, payload: object) -> FulfillmentResult:
        notification = self._provider.parse_notification(payload)
        async with self._sessions() as database:
            attempt = await database.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.provider == self._provider.name,
                    PaymentAttempt.provider_payment_id == notification.provider_payment_id,
                )
            )
            if attempt is None:
                raise PaymentNotFound("payment attempt not found")
            expected_amount = Money(attempt.amount_minor, attempt.currency)
            expected_metadata = self._metadata(attempt)
            user_id = attempt.user_id

        payment = await self._provider.verify_notification(
            notification,
            expected_amount=expected_amount,
            expected_metadata=expected_metadata,
        )
        expected_status = (
            PaymentStatus.SUCCEEDED
            if notification.event == "payment.succeeded"
            else PaymentStatus.CANCELLED
        )
        expected_paid = notification.event == "payment.succeeded"
        if (
            notification.event not in {"payment.succeeded", "payment.canceled"}
            or payment.provider_payment_id != notification.provider_payment_id
            or payment.status is not expected_status
            or payment.paid is not expected_paid
            or payment.amount != expected_amount
            or dict(payment.metadata) != expected_metadata
        ):
            raise BillingError("verified payment does not match payment attempt")

        if notification.event == "payment.canceled":
            lock = self._fulfillment_locks.setdefault(user_id, asyncio.Lock())
            async with lock, self._sessions() as database, database.begin():
                user = await database.scalar(
                    select(User).where(User.id == user_id).with_for_update()
                )
                if user is None:
                    raise PaymentNotFound("payment user not found")
                attempt = await database.scalar(
                    select(PaymentAttempt)
                    .where(
                        PaymentAttempt.provider == self._provider.name,
                        PaymentAttempt.provider_payment_id
                        == payment.provider_payment_id,
                    )
                    .with_for_update()
                )
                if attempt is None:
                    raise PaymentNotFound("payment attempt not found")
                event_key = f"payment.canceled:{payment.provider_payment_id}"
                existing_event = await database.scalar(
                    select(PaymentWebhookEvent)
                    .where(
                        PaymentWebhookEvent.provider == self._provider.name,
                        PaymentWebhookEvent.provider_event_id == event_key,
                    )
                    .with_for_update()
                )
                if existing_event is not None:
                    return FulfillmentResult(attempt.id, None, False)
                attempt.status = "cancelled"
                database.add(
                    PaymentWebhookEvent(
                        provider=self._provider.name,
                        provider_event_id=event_key,
                        payment_attempt_id=attempt.id,
                        payload={
                            "provider_payment_id": payment.provider_payment_id,
                            "status": "canceled",
                            "paid": payment.paid,
                            "amount_minor": payment.amount.amount_minor,
                            "currency": payment.amount.currency,
                            "test_mode": payment.test_mode,
                        },
                        processed_at=datetime.now(UTC),
                    )
                )
                await database.flush()
                return FulfillmentResult(attempt.id, None, True)

        lock = self._fulfillment_locks.setdefault(user_id, asyncio.Lock())
        async with lock, self._sessions() as database, database.begin():
            # Global lock order: User -> PaymentAttempt -> Subscription ->
            # PaymentWebhookEvent -> UsageLedger.
            user = await database.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is None:
                raise PaymentNotFound("payment user not found")
            attempt = await database.scalar(
                select(PaymentAttempt)
                .where(
                    PaymentAttempt.provider == self._provider.name,
                    PaymentAttempt.provider_payment_id == payment.provider_payment_id,
                )
                .with_for_update()
            )
            if attempt is None:
                raise PaymentNotFound("payment attempt not found")
            subscription = await database.scalar(
                select(Subscription)
                .where(Subscription.user_id == user_id, Subscription.status == "active")
                .with_for_update()
            )
            event_key = f"payment.succeeded:{payment.provider_payment_id}"
            existing_event = await database.scalar(
                select(PaymentWebhookEvent)
                .where(
                    PaymentWebhookEvent.provider == self._provider.name,
                    PaymentWebhookEvent.provider_event_id == event_key,
                )
                .with_for_update()
            )
            if existing_event is not None:
                return FulfillmentResult(
                    attempt.id,
                    subscription.id if subscription is not None else None,
                    False,
                )

            plan = self._stored_plan(attempt)
            now = datetime.now(UTC)
            if subscription is None:
                subscription = Subscription(
                    user_id=user_id,
                    provider=self._provider.name,
                    payment_attempt_id=attempt.id,
                    plan_code=plan.code,
                    plan_snapshot=attempt.plan_snapshot,
                    plan_fingerprint=attempt.plan_fingerprint,
                    status="active",
                    current_period_start=now,
                    current_period_end=now + timedelta(days=plan.period_days),
                )
                database.add(subscription)
                await database.flush()
            else:
                period_end = subscription.current_period_end
                if period_end is not None and period_end.tzinfo is None:
                    period_end = period_end.replace(tzinfo=UTC)
                start = period_end if period_end and period_end > now else now
                subscription.payment_attempt_id = attempt.id
                subscription.provider = self._provider.name
                subscription.plan_code = plan.code
                subscription.plan_snapshot = attempt.plan_snapshot
                subscription.plan_fingerprint = attempt.plan_fingerprint
                subscription.current_period_start = now
                subscription.current_period_end = start + timedelta(days=plan.period_days)

            webhook = PaymentWebhookEvent(
                provider=self._provider.name,
                provider_event_id=event_key,
                payment_attempt_id=attempt.id,
                payload={
                    "provider_payment_id": payment.provider_payment_id,
                    "status": payment.status.value,
                    "paid": payment.paid,
                    "amount_minor": payment.amount.amount_minor,
                    "currency": payment.amount.currency,
                    "test_mode": payment.test_mode,
                },
                processed_at=now,
            )
            database.add(webhook)
            await database.flush()
            database.add(
                UsageLedger(
                    user_id=user_id,
                    payment_attempt_id=attempt.id,
                    bucket="generation_tokens",
                    entry_type="subscription.credit",
                    amount=plan.generation_tokens,
                    idempotency_key=(
                        f"{self._provider.name}:{event_key}:generation_tokens"
                    ),
                    payload={
                        "plan_code": plan.code,
                        "plan_fingerprint": plan.fingerprint(),
                    },
                )
            )
            attempt.status = "succeeded"
            await database.flush()
            return FulfillmentResult(attempt.id, subscription.id, True)
