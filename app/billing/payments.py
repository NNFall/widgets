from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.analytics.service import record_funnel_event
from app.billing.catalog import PLAN_CATALOG, BillingPlan
from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentReceipt,
    PaymentProvider,
    PaymentStatus,
    ProviderPayment,
    ProviderPaymentMethod,
    RecurringPaymentCommand,
    validate_provider_idempotency_key,
)
from app.billing.receipts import BillingReceiptSettings, resolve_payment_receipt
from app.db.models import User
from app.saas.models import (
    BillingPaymentMethod,
    FounderAccessGrant,
    PaymentAttempt,
    PaymentWebhookEvent,
    Project,
    Subscription,
    UsageLedger,
)


class BillingError(RuntimeError):
    pass


class UnknownPlan(BillingError):
    pass


class CheckoutIdempotencyConflict(BillingError):
    pass


class IntroOfferUnavailable(BillingError):
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


def _canonical_money(money: Money) -> dict[str, object]:
    return {
        "amount_minor": money.amount_minor,
        "currency": money.currency,
    }


def _canonical_receipt(receipt: PaymentReceipt | None) -> dict[str, object] | None:
    if receipt is None:
        return None
    return {
        "customer_email": receipt.customer_email,
        "tax_system_code": receipt.tax_system_code,
        "items": [
            {
                "description": item.description,
                "quantity": format(item.quantity, "f"),
                "amount": _canonical_money(item.amount),
                "vat_code": item.vat_code,
                "measure": item.measure,
                "payment_mode": item.payment_mode,
                "payment_subject": item.payment_subject,
            }
            for item in receipt.items
        ],
    }


def _canonical_provider_request(
    command: CheckoutCommand | RecurringPaymentCommand,
) -> dict[str, object]:
    document: dict[str, object] = {
        "amount": _canonical_money(command.amount),
        "capture": True,
        "description": command.description,
        "metadata": dict(command.metadata),
        "receipt": _canonical_receipt(command.receipt),
    }
    if isinstance(command, CheckoutCommand):
        document.update(
            {
                "confirmation": True,
                "payment_method_id": None,
                # None deliberately represents the omitted provider field and
                # remains distinct from an explicit false value.
                "save_payment_method": command.save_payment_method,
            }
        )
    else:
        document.update(
            {
                "confirmation": False,
                "payment_method_id": command.payment_method_id,
                "save_payment_method": None,
            }
        )
    return document


def _request_fingerprint(
    command: CheckoutCommand | RecurringPaymentCommand,
) -> str:
    return hashlib.sha256(
        json.dumps(
            _canonical_provider_request(command),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _effective_renewal_period(
    attempt: PaymentAttempt,
) -> tuple[datetime, datetime]:
    """Resolve an immutable renewal's effective entitlement period."""
    if attempt.billing_period_start is None or attempt.billing_period_end is None:
        raise BillingError("renewal payment target is invalid")
    start = attempt.billing_period_start
    end = attempt.billing_period_end
    payload = attempt.payload if isinstance(attempt.payload, dict) else {}
    effective_start = payload.get("effective_entitlement_start")
    effective_end = payload.get("effective_entitlement_end")
    if isinstance(effective_start, str) or isinstance(effective_end, str):
        if not isinstance(effective_start, str) or not isinstance(effective_end, str):
            raise BillingError("renewal payment effective period is invalid")
        try:
            start = datetime.fromisoformat(effective_start)
            end = datetime.fromisoformat(effective_end)
        except ValueError as error:
            raise BillingError("renewal payment effective period is invalid") from error
        if start.tzinfo is None or end.tzinfo is None:
            raise BillingError("renewal payment effective period is invalid")
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    else:
        start = start.astimezone(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    else:
        end = end.astimezone(UTC)
    if end <= start:
        raise BillingError("renewal payment effective period is invalid")
    return start, end


class BillingService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
        *,
        receipt_settings: BillingReceiptSettings = BillingReceiptSettings(),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = session_factory
        self._provider = provider
        self._receipt_settings = receipt_settings
        self._clock = clock or (lambda: datetime.now(UTC))
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

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

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
            plan = PLAN_CATALOG[code]
        except KeyError as error:
            raise UnknownPlan("unknown billing plan") from error
        if not plan.public:
            raise UnknownPlan("unknown billing plan")
        return plan

    @staticmethod
    def _metadata(attempt: PaymentAttempt) -> dict[str, str]:
        metadata = {
            "payment_attempt_id": str(attempt.id),
            "user_id": str(attempt.user_id),
            "plan_code": attempt.plan_code,
            "plan_fingerprint": attempt.plan_fingerprint,
        }
        if attempt.purpose == "renewal":
            if (
                attempt.subscription_id is None
                or attempt.billing_period_start is None
                or attempt.billing_period_end is None
            ):
                raise BillingError("renewal payment target is invalid")
            start = attempt.billing_period_start
            end = attempt.billing_period_end
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)
            metadata.update(
                {
                    "purpose": "renewal",
                    "subscription_id": str(attempt.subscription_id),
                    "billing_period_start": start.astimezone(UTC).isoformat(),
                    "billing_period_end": end.astimezone(UTC).isoformat(),
                }
            )
        return metadata

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @classmethod
    def _shift_renewal_attempt_for_intro(
        cls,
        attempt: PaymentAttempt,
        *,
        new_start: datetime,
        source_payment_attempt_id: UUID | None = None,
    ) -> None:
        if attempt.billing_period_start is None or attempt.billing_period_end is None:
            return
        original_start = cls._utc(attempt.billing_period_start)
        original_end = cls._utc(attempt.billing_period_end)
        duration = original_end - original_start
        if duration <= timedelta(0):
            return
        new_start = cls._utc(new_start)
        new_end = new_start + duration
        safe_undispatched = attempt.status in {"creating", "scheduled"}
        payload = dict(attempt.payload)
        payload["billing_period_shift"] = {
            "reason": "intro_compensation",
            "original_billing_period_start": original_start.isoformat(),
            "original_billing_period_end": original_end.isoformat(),
            "new_billing_period_start": new_start.isoformat(),
            "new_billing_period_end": new_end.isoformat(),
            "source_intro_payment_attempt_id": (
                str(source_payment_attempt_id)
                if source_payment_attempt_id is not None
                else None
            ),
        }
        # Billing period columns remain immutable; only payload records the
        # compensated entitlement period.
        payload["effective_entitlement_start"] = new_start.isoformat()
        payload["effective_entitlement_end"] = new_end.isoformat()
        if safe_undispatched:
            payload["invalidated_by_intro"] = True
            attempt.status = "canceled"
            if attempt.renewal_attempt_number == 1:
                attempt.next_dispatch_at = None
            elif (
                attempt.next_dispatch_at is None
                or cls._utc(attempt.next_dispatch_at) < new_start
            ):
                # Retry rows require a non-null dispatch timestamp even while
                # terminal; retain that invariant and keep it at the shifted
                # boundary for audit/recovery tooling.
                attempt.next_dispatch_at = new_start
        attempt.payload = payload

    @staticmethod
    async def _carry_compensated_renewal_balance(
        database: AsyncSession,
        *,
        subscription: Subscription,
        renewal_attempt: PaymentAttempt,
    ) -> None:
        """Move the net old-primary balance to a compensated renewal once."""
        old_primary_id = subscription.payment_attempt_id
        if (
            old_primary_id is None
            or old_primary_id == renewal_attempt.id
            or not isinstance(renewal_attempt.payload, dict)
            or not renewal_attempt.payload.get("effective_entitlement_start")
        ):
            return
        bucket = await database.scalar(
            select(UsageLedger.bucket)
            .where(
                UsageLedger.user_id == subscription.user_id,
                UsageLedger.payment_attempt_id == old_primary_id,
                UsageLedger.bucket.in_(("tokens", "generation_tokens")),
                UsageLedger.entry_type == "subscription.credit",
            )
            .order_by(
                (UsageLedger.bucket == "tokens").desc(),
                UsageLedger.created_at,
            )
            .limit(1)
        )
        if bucket is None:
            return
        remaining = await database.scalar(
            select(func.coalesce(func.sum(UsageLedger.amount), 0)).where(
                UsageLedger.user_id == subscription.user_id,
                UsageLedger.payment_attempt_id == old_primary_id,
                UsageLedger.bucket == bucket,
            )
        )
        amount = int(remaining or 0)
        if amount <= 0:
            return
        debit_key = f"renewal-carry:{renewal_attempt.id}:debit"
        credit_key = f"renewal-carry:{renewal_attempt.id}:credit"
        payload = {
            "source_payment_attempt_id": str(old_primary_id),
            "target_payment_attempt_id": str(renewal_attempt.id),
            "amount": amount,
            "reason": "intro_compensation",
        }
        debit = await database.scalar(
            select(UsageLedger).where(UsageLedger.idempotency_key == debit_key)
        )
        if debit is None:
            database.add(
                UsageLedger(
                    user_id=subscription.user_id,
                    payment_attempt_id=old_primary_id,
                    bucket=bucket,
                    entry_type="subscription.credit.transfer",
                    amount=-amount,
                    idempotency_key=debit_key,
                    payload=payload,
                )
            )
        credit = await database.scalar(
            select(UsageLedger).where(UsageLedger.idempotency_key == credit_key)
        )
        if credit is None:
            database.add(
                UsageLedger(
                    user_id=subscription.user_id,
                    payment_attempt_id=renewal_attempt.id,
                    bucket=bucket,
                    entry_type="subscription.credit.transfer",
                    amount=amount,
                    idempotency_key=credit_key,
                    payload=payload,
                )
            )

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
        project_id: UUID | None,
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
                if row.project_id != project_id:
                    raise CheckoutIdempotencyConflict(
                        "idempotency key is already used for another project"
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
        project_id: UUID | None = None,
        auto_renew: bool = False,
    ) -> CheckoutResult:
        if not isinstance(auto_renew, bool):
            raise ValueError("auto_renew must be boolean")
        plan = self._plan(plan_code)
        return await self._create_checkout_with_plan(
            user_id,
            plan,
            idempotency_key,
            project_id=project_id,
            auto_renew=auto_renew,
        )

    async def intro_offer_available(self, user_id: int) -> bool:
        async with self._sessions() as database:
            return await self._intro_offer_available_in_session(
                database,
                user_id,
                now=self._now(),
            )

    @staticmethod
    async def _intro_offer_available_in_session(
        database: AsyncSession,
        user_id: int,
        *,
        now: datetime,
    ) -> bool:
        active_subscription = await database.scalar(
            select(Subscription.id)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.current_period_end > now,
            )
            .limit(1)
        )
        if active_subscription is not None:
            return False
        founder_grant = await database.scalar(
            select(FounderAccessGrant.id)
            .where(FounderAccessGrant.user_id == user_id)
            .limit(1)
        )
        if founder_grant is not None:
            return False
        previous_paid_period = await database.scalar(
            select(PaymentAttempt.id)
            .where(
                PaymentAttempt.user_id == user_id,
                PaymentAttempt.purpose == "initial",
                PaymentAttempt.status == "succeeded",
            )
            .limit(1)
        )
        return previous_paid_period is None

    async def _create_checkout_with_plan(
        self,
        user_id: int,
        plan: BillingPlan,
        idempotency_key: str,
        *,
        project_id: UUID | None,
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
            provider_command: CheckoutCommand | None = None
            receipt: PaymentReceipt | None = None
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
                    project = None
                    if project_id is not None:
                        project = await database.scalar(
                            select(Project)
                            .where(
                                Project.id == project_id,
                                Project.owner_user_id == user_id,
                            )
                            .with_for_update()
                        )
                        if project is None:
                            raise ValueError("project is not owned by user")
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
                        if attempt.project_id != project_id:
                            raise CheckoutIdempotencyConflict(
                                "idempotency key is already used for another project"
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
                                journey_id=attempt.journey_id,
                                user_id=attempt.user_id,
                                project_id=attempt.project_id,
                                payment_attempt_id=attempt.id,
                            )
                            return self._checkout_result(attempt, created=False)
                        updated_at = attempt.updated_at
                        if updated_at is not None and updated_at.tzinfo is None:
                            updated_at = updated_at.replace(tzinfo=UTC)
                        now = self._now()
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
                        if (
                            plan.code == "starter_intro_15d"
                            and not await self._intro_offer_available_in_session(
                                database,
                                user_id,
                                now=self._now(),
                            )
                        ):
                            raise IntroOfferUnavailable(
                                "introductory offer was already used"
                            )
                        receipt = await resolve_payment_receipt(
                            database,
                            user_id=user_id,
                            plan=plan,
                            settings=self._receipt_settings,
                        )
                        attempt = PaymentAttempt(
                            user_id=user_id,
                            project_id=project_id,
                            journey_id=(
                                project.journey_id if project is not None else None
                            ),
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
                            consented_at=(self._now() if auto_renew else None),
                            payload={},
                        )
                        database.add(attempt)
                        await database.flush()
                        first_dispatched_at = self._now()
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
                        journey_id=attempt.journey_id,
                        user_id=attempt.user_id,
                        project_id=attempt.project_id,
                        payment_attempt_id=attempt.id,
                    )
                    attempt_id = attempt.id
                    metadata = self._metadata(attempt)
                    if dispatch:
                        if not created:
                            receipt = await resolve_payment_receipt(
                                database,
                                user_id=user_id,
                                plan=plan,
                                settings=self._receipt_settings,
                            )
                        if provider_idempotency_key is None:
                            raise BillingError("payment attempt requires manual recovery")
                        provider_command = CheckoutCommand(
                            idempotency_key=provider_idempotency_key,
                            amount=plan.amount,
                            description=plan.title,
                            metadata=metadata,
                            save_payment_method=(
                                attempt.save_payment_method_requested
                            ),
                            receipt=receipt,
                        )
                        request_fingerprint = _request_fingerprint(provider_command)
                        if created:
                            attempt.request_fingerprint = request_fingerprint
                        elif attempt.request_fingerprint is None:
                            # Legacy attempts predate immutable receipt
                            # snapshots. They remain replayable only under the
                            # disabled receipt policy that preserves their old
                            # request shape.
                            if receipt is not None:
                                raise BillingError(
                                    "payment attempt requires manual recovery"
                                )
                        elif attempt.request_fingerprint != request_fingerprint:
                            raise BillingError("stored provider request was modified")
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
                    if attempt.project_id != project_id:
                        raise CheckoutIdempotencyConflict(
                            "idempotency key is already used for another project"
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
                    project_id=project_id,
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
                    self._now()
                    >= provider_idempotency_expires_at
                    - _PROVIDER_DISPATCH_SAFETY_MARGIN
                ):
                    raise BillingError("provider idempotency window expired")
                if provider_command is None:
                    raise BillingError("payment attempt requires manual recovery")
                checkout = await self._provider.create_checkout(provider_command)
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
                    attempt.next_reconcile_at = self._now()
                    attempt.payload = {"test_mode": checkout.test_mode}
                    await database.flush()
                    result = self._checkout_result(attempt, created=created)
            if lost_lease:
                return await self._wait_for_checkout(
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    plan_fingerprint=plan.fingerprint(),
                    project_id=project_id,
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
            project_id=attempt.project_id,
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

    async def _activate_verified_method(
        self,
        database: AsyncSession,
        *,
        attempt: PaymentAttempt,
        provider_method: ProviderPaymentMethod,
        now: datetime,
    ) -> BillingPaymentMethod | None:
        """Activate a verified method without risking paid-period rollback.

        Provider method identifiers are globally unique only inside the
        configured merchant account. A collision with another Kaigo user is
        therefore treated as an auto-renew refusal, never as a reason to undo
        an already verified payment.
        """

        identity = (
            BillingPaymentMethod.provider == self._provider.name,
            BillingPaymentMethod.merchant_account_fingerprint
            == attempt.merchant_account_fingerprint,
            BillingPaymentMethod.provider_payment_method_id
            == provider_method.provider_payment_method_id,
        )
        saved_method = await database.scalar(
            select(BillingPaymentMethod).where(*identity).with_for_update()
        )
        if saved_method is not None and saved_method.user_id != attempt.user_id:
            payload = dict(attempt.payload)
            payload["auto_renew_error"] = "payment_method_identity_collision"
            attempt.payload = payload
            return None

        if saved_method is None:
            candidate = BillingPaymentMethod(
                user_id=attempt.user_id,
                provider=self._provider.name,
                merchant_account_fingerprint=attempt.merchant_account_fingerprint,
                provider_payment_method_id=(
                    provider_method.provider_payment_method_id
                ),
                source_payment_attempt_id=attempt.id,
                status="active",
                consent_version=str(attempt.consent_version),
                consented_at=attempt.consented_at,
                saved_at=now,
            )
            try:
                async with database.begin_nested():
                    database.add(candidate)
                    await database.flush()
                saved_method = candidate
            except IntegrityError:
                # A concurrent transaction may have claimed the same opaque
                # provider identity. The SAVEPOINT keeps fulfillment intact.
                saved_method = await database.scalar(
                    select(BillingPaymentMethod).where(*identity).with_for_update()
                )
                if (
                    saved_method is None
                    or saved_method.user_id != attempt.user_id
                ):
                    payload = dict(attempt.payload)
                    payload["auto_renew_error"] = (
                        "payment_method_identity_collision"
                    )
                    attempt.payload = payload
                    return None

        saved_method.source_payment_attempt_id = attempt.id
        saved_method.status = "active"
        saved_method.consent_version = str(attempt.consent_version)
        saved_method.consented_at = attempt.consented_at
        saved_method.saved_at = now
        saved_method.disabled_at = None
        return saved_method

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
            subscription = None
            renewal_attempts: list[PaymentAttempt] = []
            if attempt.purpose == "renewal":
                if attempt.subscription_id is None:
                    raise BillingError("renewal payment has no subscription")
                subscription = await database.scalar(
                    select(Subscription)
                    .where(
                        Subscription.id == attempt.subscription_id,
                        Subscription.user_id == user_id,
                        Subscription.status == "active",
                    )
                    .with_for_update()
                )
            else:
                active_subscription_id = await database.scalar(
                    select(Subscription.id).where(
                        Subscription.user_id == user_id,
                        Subscription.status == "active",
                    )
                )
                if active_subscription_id is not None:
                    renewal_attempts = list(
                        await database.scalars(
                            select(PaymentAttempt)
                            .where(
                                PaymentAttempt.user_id == user_id,
                                PaymentAttempt.subscription_id == active_subscription_id,
                                PaymentAttempt.purpose == "renewal",
                                PaymentAttempt.status.not_in(
                                    ("succeeded", "cancelled", "canceled")
                                ),
                            )
                            .with_for_update()
                        )
                    )
                    subscription = await database.scalar(
                        select(Subscription)
                        .where(
                            Subscription.id == active_subscription_id,
                            Subscription.user_id == user_id,
                            Subscription.status == "active",
                        )
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

            now = self._now()
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
                if attempt.purpose == "renewal":
                    attempt_payload = dict(attempt.payload)
                    attempt_payload["cancellation_reason"] = (
                        payment.cancellation_reason.value
                        if payment.cancellation_reason is not None
                        else "unknown"
                    )
                    attempt.payload = attempt_payload
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
            compensated_paid_subscription = False
            compensation_entitlement_payment_attempt_id: UUID | None = None
            if attempt.purpose == "initial" and plan.code == "starter_intro_15d":
                founder_grant = await database.scalar(
                    select(FounderAccessGrant)
                    .where(FounderAccessGrant.user_id == user_id)
                    .with_for_update()
                )
                linked_founder_subscription = None
                if founder_grant is not None:
                    linked_founder_subscription = await database.scalar(
                        select(Subscription)
                        .where(Subscription.id == founder_grant.subscription_id)
                        .with_for_update()
                    )
                true_founder_subscription = (
                    subscription is not None
                    and subscription.provider == "founder"
                    and founder_grant is not None
                    and linked_founder_subscription is not None
                    and linked_founder_subscription.id == subscription.id
                )
                active_paid_subscription = (
                    subscription
                    if subscription is not None and not true_founder_subscription
                    else None
                )
                transition_payload = dict(attempt.payload)
                if true_founder_subscription:
                    period_start = linked_founder_subscription.current_period_start
                    if period_start is not None and period_start.tzinfo is None:
                        period_start = period_start.replace(tzinfo=UTC)
                    period_end = linked_founder_subscription.current_period_end
                    if period_end is not None and period_end.tzinfo is None:
                        period_end = period_end.replace(tzinfo=UTC)
                    linked_founder_subscription.status = "expired"
                    linked_founder_subscription.auto_renew = False
                    linked_founder_subscription.next_renewal_at = None
                    if period_end is None:
                        truncated_end = (
                            max(now, period_start)
                            if period_start is not None
                            else now
                        )
                    else:
                        truncated_end = min(period_end, now)
                        if period_start is not None:
                            truncated_end = max(truncated_end, period_start)
                    linked_founder_subscription.current_period_end = truncated_end
                    grant_end = founder_grant.ends_at
                    if grant_end.tzinfo is None:
                        grant_end = grant_end.replace(tzinfo=UTC)
                    if grant_end > now:
                        founder_grant.ends_at = now
                    transition_payload["founder_access_transition"] = {
                        "resolution": "paid_intro_wins",
                        "grant_id": str(founder_grant.id),
                        "subscription_id": str(linked_founder_subscription.id),
                    }
                    attempt.payload = transition_payload
                    # Release the active-founder partial index before creating
                    # the paid intro subscription below.
                    await database.flush()
                    subscription = None
                elif active_paid_subscription is not None:
                    compensation_entitlement_payment_attempt_id = (
                        active_paid_subscription.payment_attempt_id
                    )
                    period_end = active_paid_subscription.current_period_end
                    if period_end is not None and period_end.tzinfo is None:
                        period_end = period_end.replace(tzinfo=UTC)
                    start = period_end if period_end and period_end > now else now
                    compensated_end = start + timedelta(days=plan.period_days)
                    if period_end is not None:
                        for renewal_attempt in renewal_attempts:
                            if renewal_attempt.billing_period_start is None:
                                continue
                            if self._utc(renewal_attempt.billing_period_start) != period_end:
                                continue
                            self._shift_renewal_attempt_for_intro(
                                renewal_attempt,
                                new_start=compensated_end,
                                source_payment_attempt_id=attempt.id,
                            )
                    active_paid_subscription.current_period_end = compensated_end
                    if active_paid_subscription.auto_renew:
                        active_paid_subscription.next_renewal_at = (
                            active_paid_subscription.current_period_end
                        )
                    if founder_grant is not None:
                        grant_end = founder_grant.ends_at
                        if grant_end.tzinfo is None:
                            grant_end = grant_end.replace(tzinfo=UTC)
                        if grant_end > now:
                            founder_grant.ends_at = now
                    attempt.subscription_id = active_paid_subscription.id
                    entitlement_primary_recovered = (
                        compensation_entitlement_payment_attempt_id is None
                    )
                    if entitlement_primary_recovered:
                        compensation_entitlement_payment_attempt_id = attempt.id
                        active_paid_subscription.payment_attempt_id = attempt.id
                    transition_payload["founder_access_transition"] = {
                        "resolution": "paid_intro_compensated_on_active_paid",
                        "grant_id": (
                            str(founder_grant.id) if founder_grant is not None else None
                        ),
                        "subscription_id": (
                            str(linked_founder_subscription.id)
                            if linked_founder_subscription is not None
                            else None
                        ),
                        "paid_subscription_id": str(active_paid_subscription.id),
                    }
                    if entitlement_primary_recovered:
                        transition_payload["founder_access_transition"][
                            "entitlement_primary_recovered_from_null"
                        ] = True
                    attempt.payload = transition_payload
                    compensated_paid_subscription = True
                elif founder_grant is not None:
                    grant_end = founder_grant.ends_at
                    if grant_end.tzinfo is None:
                        grant_end = grant_end.replace(tzinfo=UTC)
                    if grant_end > now:
                        founder_grant.ends_at = now
                    transition_payload["founder_access_transition"] = {
                        "resolution": "paid_intro_wins",
                        "grant_id": str(founder_grant.id),
                        "subscription_id": (
                            str(linked_founder_subscription.id)
                            if linked_founder_subscription is not None
                            else None
                        ),
                    }
                    attempt.payload = transition_payload
            if attempt.purpose == "renewal":
                if (
                    subscription is None
                    or attempt.billing_period_start is None
                    or attempt.billing_period_end is None
                ):
                    raise BillingError("renewal payment target is invalid")
                raw_period_start = self._utc(attempt.billing_period_start)
                period_start, period_end = _effective_renewal_period(attempt)
                current_end = subscription.current_period_end
                if current_end is not None and current_end.tzinfo is None:
                    current_end = current_end.replace(tzinfo=UTC)
                if current_end is None or current_end < period_start:
                    raise BillingError("renewal payment period is inconsistent")
                if current_end < period_end:
                    if current_end != period_start:
                        raise BillingError("renewal payment period overlaps")
                    # A compensated in-flight renewal grants from the already
                    # active period; do not move access start into the future.
                    if period_start <= now:
                        subscription.current_period_start = period_start
                    subscription.current_period_end = period_end
                if period_start != raw_period_start:
                    await self._carry_compensated_renewal_balance(
                        database,
                        subscription=subscription,
                        renewal_attempt=attempt,
                    )
                subscription.payment_attempt_id = attempt.id
                subscription.provider = self._provider.name
                subscription.plan_code = plan.code
                subscription.plan_snapshot = attempt.plan_snapshot
                subscription.plan_fingerprint = attempt.plan_fingerprint
                if subscription.auto_renew:
                    subscription.next_renewal_at = subscription.current_period_end
            elif subscription is None:
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
            elif not compensated_paid_subscription:
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
                and not compensated_paid_subscription
            ):
                saved_method = await self._activate_verified_method(
                    database,
                    attempt=attempt,
                    provider_method=provider_method,
                    now=now,
                )
                if saved_method is not None:
                    subscription.merchant_account_fingerprint = (
                        attempt.merchant_account_fingerprint
                    )
                    attempt.payment_method_id = saved_method.id
                    subscription.payment_method_id = saved_method.id
                    subscription.auto_renew = True
                    subscription.next_renewal_at = subscription.current_period_end
                    subscription.auto_renew_enabled_at = now
                    subscription.auto_renew_disabled_at = None
                else:
                    subscription.payment_method_id = None
                    subscription.auto_renew = False
                    subscription.next_renewal_at = None
                    subscription.auto_renew_disabled_at = now

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
                journey_id=attempt.journey_id,
                user_id=user_id,
                project_id=attempt.project_id,
                payment_attempt_id=attempt.id,
            )
            if attempt.purpose == "renewal":
                if attempt.subscription_id is None or attempt.billing_period_start is None:
                    raise BillingError("renewal payment target is invalid")
                period_start, _ = _effective_renewal_period(attempt)
                credit_key = (
                    f"renewal:{attempt.subscription_id}:"
                    f"{period_start.isoformat()}:tokens"
                )
            else:
                credit_key = f"payment:{attempt.id}:tokens"
            credit_payment_attempt_id = attempt.id
            credit_payload = {
                "plan_code": plan.code,
                "plan_fingerprint": plan.fingerprint(),
                "purpose": attempt.purpose,
            }
            if compensated_paid_subscription:
                if compensation_entitlement_payment_attempt_id is None:
                    raise BillingError(
                        "paid introductory compensation requires an entitlement"
                    )
                credit_payment_attempt_id = (
                    compensation_entitlement_payment_attempt_id
                )
                credit_payload.update(
                    {
                        "source_payment_attempt_id": str(attempt.id),
                        "entitlement_payment_attempt_id": str(
                            compensation_entitlement_payment_attempt_id
                        ),
                        "compensation_resolution": (
                            "paid_intro_compensated_on_active_paid"
                        ),
                    }
                )
            existing_credit = await database.scalar(
                select(UsageLedger).where(
                    UsageLedger.idempotency_key == credit_key
                )
            )
            if existing_credit is None:
                database.add(
                    UsageLedger(
                        user_id=user_id,
                        payment_attempt_id=credit_payment_attempt_id,
                        bucket="tokens",
                        entry_type="subscription.credit",
                        amount=plan.generation_tokens,
                        idempotency_key=credit_key,
                        payload=credit_payload,
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
            # Ownership checks are intentionally unlocked. They prevent a
            # caller from making us lock another user's subscription before we
            # acquire the canonical User lock.
            known_user_id = await database.scalar(
                select(User.id).where(User.id == user_id)
            )
            if known_user_id is None:
                raise PaymentNotFound("user not found")
            subscription_owner_id = await database.scalar(
                select(Subscription.user_id).where(Subscription.id == subscription_id)
            )
            if subscription_owner_id != user_id:
                raise PaymentNotFound("subscription not found")

            user = await database.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
            if user is None:
                raise PaymentNotFound("user not found")
            renewal_attempts = list(
                await database.scalars(
                    select(PaymentAttempt)
                    .where(
                        PaymentAttempt.user_id == user_id,
                        PaymentAttempt.subscription_id == subscription_id,
                        PaymentAttempt.purpose == "renewal",
                        PaymentAttempt.status.not_in(
                            ("succeeded", "cancelled", "canceled")
                        ),
                    )
                    .with_for_update()
                )
            )
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
            if any(
                attempt.status in {"dispatching", "pending", "dispatch_unknown"}
                for attempt in renewal_attempts
            ):
                raise BillingError("renewal_in_progress")
            for attempt in renewal_attempts:
                if attempt.status in {"scheduled", "creating"}:
                    attempt.status = "cancelled"
            method = (
                await database.scalar(
                    select(BillingPaymentMethod)
                    .where(BillingPaymentMethod.id == subscription.payment_method_id)
                    .with_for_update()
                )
                if subscription.payment_method_id is not None
                else None
            )
            now = self._now()
            if method is not None and method.user_id == user_id:
                method.status = "disabled"
                method.disabled_at = now
            if subscription.auto_renew:
                subscription.auto_renew = False
                subscription.next_renewal_at = None
                subscription.auto_renew_disabled_at = now
            elif subscription.auto_renew_disabled_at is None:
                subscription.auto_renew_disabled_at = now
            await database.flush()
            return subscription
