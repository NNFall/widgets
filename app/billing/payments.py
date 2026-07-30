from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.service import record_funnel_event
from app.billing.catalog import PLAN_CATALOG, BillingPlan
from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentProvider,
    PaymentStatus,
    ProviderPayment,
    validate_provider_idempotency_key,
)
from app.db.models import User
from app.saas.models import (
    BillingPaymentMethod,
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


class MerchantAccountMismatch(BillingError):
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
_MERCHANT_ACCOUNT_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LEGACY_UNKNOWN_MERCHANT = "!" * 64
LEGACY_MIGRATED_PLAN_CODE = "legacy_migrated"
_CHECKOUT_CREATION_LEASE = timedelta(seconds=30)
_PROVIDER_IDEMPOTENCY_WINDOW = timedelta(hours=24)
_PROVIDER_DISPATCH_SAFETY_MARGIN = timedelta(minutes=5)
_AUTO_RENEW_CONSENT_VERSION = "yookassa-auto-renew-v1"


class BillingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        merchant_account_fingerprint = provider.merchant_account_fingerprint
        if not _MERCHANT_ACCOUNT_FINGERPRINT_PATTERN.fullmatch(
            merchant_account_fingerprint
        ):
            raise ValueError("provider merchant account fingerprint is invalid")
        self._merchant_account_fingerprint = merchant_account_fingerprint
        self._checkout_locks: WeakValueDictionary[tuple[int, str], asyncio.Lock] = (
            WeakValueDictionary()
        )
        self._fulfillment_locks: WeakValueDictionary[int, asyncio.Lock] = (
            WeakValueDictionary()
        )

    async def close(self) -> None:
        await self._provider.aclose()

    def uses_provider(self, provider: PaymentProvider) -> bool:
        return self._provider is provider

    @property
    def merchant_account_fingerprint(self) -> str:
        return self._merchant_account_fingerprint

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
        return f"kaigo-{attempt_id}"

    @staticmethod
    def _payload_timestamp(payload: dict, field_name: str) -> datetime:
        value = payload.get(field_name)
        if not isinstance(value, str):
            raise BillingError("payment attempt requires manual recovery")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise BillingError("payment attempt requires manual recovery") from error
        if parsed.tzinfo is None:
            raise BillingError("payment attempt requires manual recovery")
        return parsed.astimezone(UTC)

    @classmethod
    def _stored_provider_dispatch(
        cls,
        attempt: PaymentAttempt,
        *,
        now: datetime,
    ) -> tuple[str, datetime]:
        payload = attempt.payload
        if not isinstance(payload, dict):
            raise BillingError("payment attempt requires manual recovery")
        key = payload.get("provider_idempotency_key")
        try:
            key = validate_provider_idempotency_key(key)
        except ValueError as error:
            raise BillingError("payment attempt requires manual recovery") from error
        if key != cls._provider_idempotency_key(attempt.id):
            raise BillingError("payment attempt requires manual recovery")
        first_dispatched_at = cls._payload_timestamp(payload, "first_dispatched_at")
        expires_at = cls._payload_timestamp(payload, "provider_idempotency_expires_at")
        if expires_at != first_dispatched_at + _PROVIDER_IDEMPOTENCY_WINDOW:
            raise BillingError("payment attempt requires manual recovery")
        if now >= expires_at - _PROVIDER_DISPATCH_SAFETY_MARGIN:
            raise BillingError("provider idempotency window expired")
        return key, expires_at

    def _assert_merchant_account(self, attempt: PaymentAttempt) -> None:
        if not self.is_current_merchant_account(attempt):
            raise MerchantAccountMismatch(
                "payment attempt merchant account does not match configured provider"
            )

    def _assert_verifiable_merchant_account(self, attempt: PaymentAttempt) -> None:
        if not (
            self.is_current_merchant_account(attempt)
            or self.is_recoverable_legacy_attempt(attempt)
        ):
            raise MerchantAccountMismatch(
                "payment attempt merchant account does not match configured provider"
            )

    def is_current_merchant_account(self, attempt: PaymentAttempt) -> bool:
        return (
            attempt.provider == self._provider.name
            and attempt.merchant_account_fingerprint
            == self._merchant_account_fingerprint
        )

    def is_recoverable_legacy_attempt(self, attempt: PaymentAttempt) -> bool:
        return (
            attempt.provider == self._provider.name
            and attempt.merchant_account_fingerprint == LEGACY_UNKNOWN_MERCHANT
            and attempt.plan_code == LEGACY_MIGRATED_PLAN_CODE
            and attempt.provider_payment_id is not None
        )

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
        auto_renew: bool,
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
                self._assert_merchant_account(row)
                if row.plan_fingerprint != plan_fingerprint:
                    raise CheckoutIdempotencyConflict(
                        "idempotency key is already used for another plan"
                    )
                if row.auto_renew_requested is not auto_renew:
                    raise CheckoutIdempotencyConflict(
                        "idempotency key is already used for another renewal intent"
                    )
                if row.checkout_url:
                    return self._checkout_result(row, created=False)
                if row.status in {"failed", "dispatch_unknown"}:
                    raise BillingError("previous checkout outcome is unresolved")
        raise BillingError("checkout creation is still in progress")

    async def create_checkout(
        self,
        user_id: int,
        plan_code: str,
        idempotency_key: str,
        *,
        auto_renew: bool = False,
    ) -> CheckoutResult:
        if not isinstance(auto_renew, bool):
            raise ValueError("auto_renew must be boolean")
        plan = self._plan(plan_code)
        return await self._create_checkout_with_plan(
            user_id,
            plan,
            idempotency_key,
            auto_renew=auto_renew,
        )

    async def _create_checkout_with_plan(
        self,
        user_id: int,
        plan: BillingPlan,
        idempotency_key: str,
        *,
        auto_renew: bool,
    ) -> CheckoutResult:
        if not _IDEMPOTENCY_PATTERN.fullmatch(idempotency_key):
            raise ValueError("invalid checkout idempotency key")
        lock = self._checkout_locks.setdefault(
            (user_id, idempotency_key), asyncio.Lock()
        )
        async with lock:
            created = False
            dispatch = False
            lease_token: str | None = None
            provider_idempotency_key: str | None = None
            provider_idempotency_expires_at: datetime | None = None
            try:
                async with self._sessions() as database, database.begin():
                    # Locking the user serializes checkout decisions across
                    # idempotency keys. This makes merchant cutover a drain:
                    # no process may create a payment under the new account
                    # while an old account still has an ambiguous attempt.
                    user = await database.scalar(
                        select(User).where(User.id == user_id).with_for_update()
                    )
                    if user is None:
                        raise PaymentNotFound("user not found")
                    cutover_blocker = await database.scalar(
                        select(PaymentAttempt)
                        .where(
                            PaymentAttempt.user_id == user_id,
                            PaymentAttempt.status.in_(
                                ("creating", "pending", "failed", "dispatch_unknown")
                            ),
                            or_(
                                PaymentAttempt.provider != self._provider.name,
                                PaymentAttempt.merchant_account_fingerprint
                                != self._merchant_account_fingerprint,
                            ),
                        )
                        .order_by(
                            PaymentAttempt.updated_at.desc(),
                            PaymentAttempt.created_at.desc(),
                        )
                        .with_for_update()
                        .limit(1)
                    )
                    if cutover_blocker is not None:
                        raise MerchantAccountMismatch(
                            "payment attempt merchant account does not match "
                            "configured provider"
                        )
                    unresolved_attempt = await database.scalar(
                        select(PaymentAttempt)
                        .where(
                            PaymentAttempt.user_id == user_id,
                            PaymentAttempt.idempotency_key != idempotency_key,
                            PaymentAttempt.status.in_(
                                (
                                    "creating",
                                    "pending",
                                    "failed",
                                    "dispatch_unknown",
                                )
                            ),
                        )
                        .order_by(
                            PaymentAttempt.updated_at.desc(),
                            PaymentAttempt.created_at.desc(),
                        )
                        .with_for_update()
                        .limit(1)
                    )
                    if unresolved_attempt is not None:
                        raise BillingError(
                            "unresolved payment attempt blocks new checkout"
                        )
                    attempt = await database.scalar(
                        select(PaymentAttempt)
                        .where(
                            PaymentAttempt.user_id == user_id,
                            PaymentAttempt.idempotency_key == idempotency_key,
                        )
                        .with_for_update()
                    )
                    if attempt is not None:
                        self._assert_merchant_account(attempt)
                        if attempt.plan_fingerprint != plan.fingerprint():
                            raise CheckoutIdempotencyConflict(
                                "idempotency key is already used for another plan"
                            )
                        if attempt.auto_renew_requested is not auto_renew:
                            raise CheckoutIdempotencyConflict(
                                "idempotency key is already used for another renewal intent"
                            )
                        if attempt.checkout_url:
                            await record_funnel_event(
                                database,
                                event_type="upgrade_started",
                                event_key=(
                                    f"upgrade_started:payment_attempt:{attempt.id}"
                                ),
                                user_id=attempt.user_id,
                                payment_attempt_id=attempt.id,
                            )
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
                            (
                                provider_idempotency_key,
                                provider_idempotency_expires_at,
                            ) = self._stored_provider_dispatch(attempt, now=now)
                            lease_token = uuid4().hex
                            attempt.updated_at = now
                            payload = dict(attempt.payload)
                            payload["checkout_lease_token"] = lease_token
                            attempt.payload = payload
                            dispatch = True
                        elif attempt.status in {"failed", "dispatch_unknown"}:
                            # The provider request may have been accepted even
                            # when our process observed a transport failure.
                            # Retrying the same attempt with the deterministic
                            # provider key is the only safe way to reconcile it.
                            (
                                provider_idempotency_key,
                                provider_idempotency_expires_at,
                            ) = self._stored_provider_dispatch(attempt, now=now)
                            lease_token = uuid4().hex
                            attempt.status = "creating"
                            attempt.updated_at = now
                            payload = dict(attempt.payload)
                            payload["checkout_lease_token"] = lease_token
                            attempt.payload = payload
                            dispatch = True
                    else:
                        attempt = PaymentAttempt(
                            user_id=user_id,
                            provider=self._provider.name,
                            merchant_account_fingerprint=(
                                self._merchant_account_fingerprint
                            ),
                            idempotency_key=idempotency_key,
                            plan_code=plan.code,
                            plan_snapshot=plan.snapshot(),
                            plan_fingerprint=plan.fingerprint(),
                            amount_minor=plan.amount.amount_minor,
                            currency=plan.amount.currency,
                            status="creating",
                            purpose="initial",
                            auto_renew_requested=auto_renew,
                            save_payment_method_requested=(
                                True if auto_renew else None
                            ),
                            consent_version=(
                                _AUTO_RENEW_CONSENT_VERSION if auto_renew else None
                            ),
                            consented_at=(datetime.now(UTC) if auto_renew else None),
                            payload={},
                        )
                        database.add(attempt)
                        await database.flush()
                        first_dispatched_at = datetime.now(UTC)
                        provider_idempotency_key = self._provider_idempotency_key(
                            attempt.id
                        )
                        provider_idempotency_expires_at = (
                            first_dispatched_at + _PROVIDER_IDEMPOTENCY_WINDOW
                        )
                        lease_token = uuid4().hex
                        attempt.payload = {
                            "checkout_lease_token": lease_token,
                            "provider_idempotency_key": provider_idempotency_key,
                            "first_dispatched_at": first_dispatched_at.isoformat(),
                            "provider_idempotency_expires_at": (
                                provider_idempotency_expires_at.isoformat()
                            ),
                        }
                        created = True
                        dispatch = True
                    await record_funnel_event(
                        database,
                        event_type="upgrade_started",
                        event_key=f"upgrade_started:payment_attempt:{attempt.id}",
                        user_id=attempt.user_id,
                        payment_attempt_id=attempt.id,
                    )
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
                    self._assert_merchant_account(attempt)
                    if attempt.plan_fingerprint != plan.fingerprint():
                        raise CheckoutIdempotencyConflict(
                            "idempotency key is already used for another plan"
                        )
                    if attempt.auto_renew_requested is not auto_renew:
                        raise CheckoutIdempotencyConflict(
                            "idempotency key is already used for another renewal intent"
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
                    auto_renew=auto_renew,
                )

            try:
                if lease_token is None:
                    raise BillingError("checkout creation lease is missing")
                if (
                    provider_idempotency_key is None
                    or provider_idempotency_expires_at is None
                ):
                    raise BillingError("payment attempt requires manual recovery")
                if (
                    datetime.now(UTC)
                    >= provider_idempotency_expires_at
                    - _PROVIDER_DISPATCH_SAFETY_MARGIN
                ):
                    raise BillingError("provider idempotency window expired")
                checkout = await self._provider.create_checkout(
                    CheckoutCommand(
                        idempotency_key=provider_idempotency_key,
                        amount=plan.amount,
                        description=plan.title,
                        metadata=metadata,
                        save_payment_method=(True if auto_renew else None),
                    )
                )
                if (
                    checkout.amount != plan.amount
                    or dict(checkout.metadata) != metadata
                    or checkout.status is not PaymentStatus.PENDING
                    or checkout.paid
                ):
                    raise BillingError(
                        "provider checkout response does not match command"
                    )
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
                        failed.status = "dispatch_unknown"
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
                    attempt.next_reconcile_at = datetime.now(UTC)
                    attempt.payload = {"test_mode": checkout.test_mode}
                    await database.flush()
                    result = self._checkout_result(attempt, created=created)
            if lost_lease:
                return await self._wait_for_checkout(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    plan_fingerprint=plan.fingerprint(),
                    auto_renew=auto_renew,
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
            self._assert_merchant_account(attempt)
            if attempt.status not in {
                "creating",
                "pending",
                "failed",
                "dispatch_unknown",
            }:
                raise BillingError("payment attempt cannot be resumed")
            plan = self._stored_plan(attempt)
            idempotency_key = attempt.idempotency_key
        return await self._create_checkout_with_plan(
            user_id,
            plan,
            idempotency_key,
            auto_renew=attempt.auto_renew_requested,
        )

    async def handle_notification(self, payload: object) -> FulfillmentResult:
        notification = self._provider.parse_notification(payload)
        async with self._sessions() as database:
            attempt = await database.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.provider == self._provider.name,
                    PaymentAttempt.provider_payment_id
                    == notification.provider_payment_id,
                )
            )
            if attempt is None:
                raise PaymentNotFound("payment attempt not found")
            self._assert_verifiable_merchant_account(attempt)
            expected_amount = Money(attempt.amount_minor, attempt.currency)
            expected_metadata = self._metadata(attempt)
            attempt_id = attempt.id

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
        return await self.apply_verified_payment(
            attempt_id,
            payment,
            source="webhook",
        )

    def verification_expectations(
        self,
        attempt: PaymentAttempt,
    ) -> tuple[Money, dict[str, str]]:
        self._assert_verifiable_merchant_account(attempt)
        return Money(attempt.amount_minor, attempt.currency), self._metadata(attempt)

    def _validate_verified_payment(
        self,
        attempt: PaymentAttempt,
        payment: ProviderPayment,
        *,
        allow_legacy_merchant: bool,
    ) -> None:
        if allow_legacy_merchant:
            self._assert_verifiable_merchant_account(attempt)
        else:
            self._assert_merchant_account(attempt)
        expected_amount = Money(attempt.amount_minor, attempt.currency)
        expected_metadata = self._metadata(attempt)
        expected_test_mode = attempt.payload.get("test_mode")
        if (
            attempt.provider_payment_id is None
            or payment.provider_payment_id != attempt.provider_payment_id
            or payment.amount != expected_amount
            or dict(payment.metadata) != expected_metadata
            or (
                isinstance(expected_test_mode, bool)
                and payment.test_mode is not expected_test_mode
            )
            or (payment.status is PaymentStatus.SUCCEEDED and payment.paid is not True)
            or (payment.status is PaymentStatus.CANCELLED and payment.paid is not False)
            or payment.status not in {PaymentStatus.SUCCEEDED, PaymentStatus.CANCELLED}
        ):
            raise BillingError("verified payment does not match payment attempt")

    @staticmethod
    def _event_name(payment: ProviderPayment) -> str:
        if payment.status is PaymentStatus.SUCCEEDED:
            return "payment.succeeded"
        if payment.status is PaymentStatus.CANCELLED:
            return "payment.canceled"
        raise BillingError("verified payment is not terminal")

    async def apply_verified_payment(
        self,
        attempt_id: UUID,
        payment: ProviderPayment,
        *,
        source: Literal["webhook", "reconciliation", "dispatch"],
    ) -> FulfillmentResult:
        if source not in {"webhook", "reconciliation", "dispatch"}:
            raise ValueError("verified payment source is invalid")
        allow_legacy_merchant = source in {"webhook", "reconciliation"}
        async with self._sessions() as database:
            attempt = await database.get(PaymentAttempt, attempt_id)
            if attempt is None:
                raise PaymentNotFound("payment attempt not found")
            self._validate_verified_payment(
                attempt,
                payment,
                allow_legacy_merchant=allow_legacy_merchant,
            )
            user_id = attempt.user_id

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
                .where(PaymentAttempt.id == attempt_id)
                .with_for_update()
            )
            if attempt is None:
                raise PaymentNotFound("payment attempt not found")
            self._validate_verified_payment(
                attempt,
                payment,
                allow_legacy_merchant=allow_legacy_merchant,
            )
            if self.is_recoverable_legacy_attempt(attempt):
                attempt.merchant_account_fingerprint = (
                    self._merchant_account_fingerprint
                )
            subscription = await database.scalar(
                select(Subscription)
                .where(Subscription.user_id == user_id, Subscription.status == "active")
                .with_for_update()
            )
            event_name = self._event_name(payment)
            event_key = f"{event_name}:{payment.provider_payment_id}"
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

            now = datetime.now(UTC)
            event_payload = {
                "provider_payment_id": payment.provider_payment_id,
                "status": (
                    "canceled"
                    if payment.status is PaymentStatus.CANCELLED
                    else payment.status.value
                ),
                "paid": payment.paid,
                "amount_minor": payment.amount.amount_minor,
                "currency": payment.amount.currency,
                "test_mode": payment.test_mode,
                "source": source,
            }
            if payment.status is PaymentStatus.CANCELLED:
                attempt.status = "cancelled"
                database.add(
                    PaymentWebhookEvent(
                        provider=self._provider.name,
                        provider_event_id=event_key,
                        merchant_account_fingerprint=(
                            attempt.merchant_account_fingerprint
                        ),
                        payment_attempt_id=attempt.id,
                        payload=event_payload,
                        processed_at=now,
                    )
                )
                await database.flush()
                return FulfillmentResult(attempt.id, None, True)

            plan = self._stored_plan(attempt)
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
                subscription.current_period_end = start + timedelta(
                    days=plan.period_days
                )
                if subscription.auto_renew:
                    subscription.next_renewal_at = subscription.current_period_end

            provider_method = payment.payment_method
            if (
                attempt.purpose == "initial"
                and attempt.auto_renew_requested is True
                and attempt.save_payment_method_requested is True
                and attempt.consent_version == _AUTO_RENEW_CONSENT_VERSION
                and attempt.consented_at is not None
                and provider_method is not None
                and provider_method.saved is True
            ):
                saved_method = await database.scalar(
                    select(BillingPaymentMethod)
                    .where(
                        BillingPaymentMethod.provider == self._provider.name,
                        BillingPaymentMethod.merchant_account_fingerprint
                        == attempt.merchant_account_fingerprint,
                        BillingPaymentMethod.provider_payment_method_id
                        == provider_method.provider_payment_method_id,
                    )
                    .with_for_update()
                )
                if saved_method is not None and saved_method.user_id != user_id:
                    raise BillingError("saved payment method belongs to another user")
                if saved_method is None:
                    saved_method = BillingPaymentMethod(
                        user_id=user_id,
                        provider=self._provider.name,
                        merchant_account_fingerprint=(
                            attempt.merchant_account_fingerprint
                        ),
                        provider_payment_method_id=(
                            provider_method.provider_payment_method_id
                        ),
                        source_payment_attempt_id=attempt.id,
                        status="active",
                        consent_version=attempt.consent_version,
                        consented_at=attempt.consented_at,
                        saved_at=now,
                    )
                    database.add(saved_method)
                    await database.flush()
                else:
                    saved_method.source_payment_attempt_id = attempt.id
                    saved_method.status = "active"
                    saved_method.consent_version = attempt.consent_version
                    saved_method.consented_at = attempt.consented_at
                    saved_method.saved_at = now
                    saved_method.disabled_at = None

                subscription.merchant_account_fingerprint = (
                    attempt.merchant_account_fingerprint
                )
                attempt.payment_method_id = saved_method.id
                subscription.payment_method_id = saved_method.id
                subscription.auto_renew = True
                subscription.next_renewal_at = subscription.current_period_end
                subscription.auto_renew_enabled_at = now
                subscription.auto_renew_disabled_at = None

            database.add(
                PaymentWebhookEvent(
                    provider=self._provider.name,
                    provider_event_id=event_key,
                    merchant_account_fingerprint=(attempt.merchant_account_fingerprint),
                    payment_attempt_id=attempt.id,
                    payload=event_payload,
                    processed_at=now,
                )
            )
            await database.flush()
            await record_funnel_event(
                database,
                event_type="payment_completed",
                event_key=f"payment_completed:payment_attempt:{attempt.id}",
                user_id=user_id,
                payment_attempt_id=attempt.id,
            )
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

    async def disable_auto_renew(
        self,
        user_id: int,
        subscription_id: UUID,
    ) -> Subscription:
        async with self._sessions() as database, database.begin():
            user = await database.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is None:
                raise PaymentNotFound("user not found")
            subscription = await database.scalar(
                select(Subscription)
                .where(
                    Subscription.id == subscription_id,
                    Subscription.user_id == user_id,
                )
                .with_for_update()
            )
            if subscription is None:
                raise PaymentNotFound("subscription not found")
            if subscription.auto_renew:
                subscription.auto_renew = False
                subscription.next_renewal_at = None
                subscription.auto_renew_disabled_at = datetime.now(UTC)
            elif subscription.auto_renew_disabled_at is None:
                subscription.auto_renew_disabled_at = datetime.now(UTC)
            await database.flush()
            return subscription
