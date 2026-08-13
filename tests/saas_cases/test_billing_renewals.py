from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.catalog import PLAN_CATALOG
from app.billing.contracts import (
    CheckoutCommand,
    PaymentCancellationReason,
    PaymentStatus,
    ProviderPayment,
    RecurringPaymentCommand,
)
from app.billing.payments import BillingService
from app.billing.receipts import BillingReceiptSettings
from app.billing.renewals import RenewalScheduler
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    BillingPaymentMethod,
    PaymentAttempt,
    Subscription,
    UsageLedger,
    UserIdentity,
)


@dataclass
class FakeClock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeRecurringProvider:
    name = "fakepay"
    merchant_account_fingerprint = "a" * 64

    def __init__(
        self,
        *,
        status: PaymentStatus = PaymentStatus.SUCCEEDED,
        cancellation_reason: PaymentCancellationReason | None = None,
    ) -> None:
        self.status = status
        self.cancellation_reason = cancellation_reason
        self.recurring_calls: list[RecurringPaymentCommand] = []
        self._gate = asyncio.Event()
        self._gate.set()

    async def create_checkout(self, command: CheckoutCommand):  # pragma: no cover
        raise AssertionError("checkout is outside renewal tests")

    async def create_recurring_payment(
        self, command: RecurringPaymentCommand
    ) -> ProviderPayment:
        self.recurring_calls.append(command)
        await self._gate.wait()
        return ProviderPayment(
            provider_payment_id=f"renewal-{command.idempotency_key}",
            status=self.status,
            amount=command.amount,
            paid=self.status is PaymentStatus.SUCCEEDED,
            metadata=command.metadata,
            test_mode=True,
            cancellation_reason=self.cancellation_reason,
        )

    async def get_payment(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("reconciliation is outside renewal tests")

    def parse_notification(self, payload):  # pragma: no cover
        raise AssertionError("webhook is outside renewal tests")

    async def verify_notification(self, *args, **kwargs):  # pragma: no cover
        raise AssertionError("webhook is outside renewal tests")

    async def aclose(self) -> None:
        return None


class AmbiguousRecurringProvider(FakeRecurringProvider):
    async def create_recurring_payment(
        self, command: RecurringPaymentCommand
    ) -> ProviderPayment:
        self.recurring_calls.append(command)
        raise RuntimeError("lost provider response")


@pytest_asyncio.fixture
async def recurring_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'renewals.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 7, 31, 12, tzinfo=UTC)
    plan = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        database.add(
            UserIdentity(
                user_id=10,
                provider="yandex",
                provider_subject="owner",
                email="owner@example.com",
                email_verified=True,
            )
        )
        method = BillingPaymentMethod(
            user_id=10,
            provider="fakepay",
            merchant_account_fingerprint="a" * 64,
            provider_payment_method_id="saved-renewal-method",
            status="active",
            consent_version="yookassa-auto-renew-v1",
            consented_at=now - timedelta(days=31),
            saved_at=now - timedelta(days=31),
        )
        database.add(method)
        await database.flush()
        subscription = Subscription(
            user_id=10,
            provider="fakepay",
            merchant_account_fingerprint="a" * 64,
            payment_method_id=method.id,
            plan_code=plan.code,
            plan_snapshot=plan.snapshot(),
            plan_fingerprint=plan.fingerprint(),
            status="active",
            current_period_start=now - timedelta(days=30),
            current_period_end=now,
            auto_renew=True,
            next_renewal_at=now,
            auto_renew_enabled_at=now - timedelta(days=31),
        )
        database.add(subscription)
        await database.flush()
        subscription_id = subscription.id
    try:
        yield factory, FakeClock(now), subscription_id
    finally:
        await engine.dispose()


def scheduler(factory, clock, provider, *, receipts=BillingReceiptSettings()):
    service = BillingService(factory, provider, receipt_settings=receipts, clock=clock)
    return RenewalScheduler(
        factory,
        provider,
        payment_service=service,
        receipt_settings=receipts,
        clock=clock,
    )


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@pytest.mark.asyncio
async def test_due_renewal_extends_exact_period_and_credits_once(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    runner = scheduler(factory, clock, provider)

    await runner.run_once()
    await runner.run_once()

    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        attempts = list(await database.scalars(select(PaymentAttempt)))
        credits = list(await database.scalars(select(UsageLedger)))
    assert len(provider.recurring_calls) == 1
    assert len(attempts) == 1
    assert aware(subscription.current_period_start) == clock.now
    assert aware(subscription.current_period_end) == clock.now + timedelta(days=30)
    assert subscription.next_renewal_at == subscription.current_period_end
    assert [(row.bucket, row.amount) for row in credits] == [("tokens", 1_000_000)]
    assert credits[0].idempotency_key == (
        f"renewal:{subscription_id}:{clock.now.isoformat()}:tokens"
    )


@pytest.mark.asyncio
async def test_intro_period_charges_only_first_month_balance(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    intro = PLAN_CATALOG["starter_intro_15d"]
    balance = PLAN_CATALOG["starter_intro_balance_15d"]
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        subscription.plan_code = intro.code
        subscription.plan_snapshot = intro.snapshot()
        subscription.plan_fingerprint = intro.fingerprint()
        subscription.current_period_start = clock.now - timedelta(days=15)
        subscription.current_period_end = clock.now
        subscription.next_renewal_at = clock.now

    provider = FakeRecurringProvider()
    await scheduler(factory, clock, provider).run_once()

    assert len(provider.recurring_calls) == 1
    command = provider.recurring_calls[0]
    assert command.amount.amount_minor == 150_000
    assert command.metadata["plan_code"] == balance.code
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        attempt = await database.scalar(select(PaymentAttempt))
        credit = await database.scalar(select(UsageLedger))
    assert attempt.plan_code == balance.code
    assert attempt.plan_snapshot == balance.snapshot()
    assert subscription.plan_code == balance.code
    assert aware(subscription.current_period_end) == clock.now + timedelta(days=15)
    assert credit.amount == 500_000


@pytest.mark.asyncio
async def test_intro_balance_then_renews_into_regular_monthly_starter(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    balance = PLAN_CATALOG["starter_intro_balance_15d"]
    monthly = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        subscription.plan_code = balance.code
        subscription.plan_snapshot = balance.snapshot()
        subscription.plan_fingerprint = balance.fingerprint()
        subscription.current_period_start = clock.now - timedelta(days=15)
        subscription.current_period_end = clock.now
        subscription.next_renewal_at = clock.now

    provider = FakeRecurringProvider()
    await scheduler(factory, clock, provider).run_once()

    assert len(provider.recurring_calls) == 1
    assert provider.recurring_calls[0].amount.amount_minor == 200_000
    assert provider.recurring_calls[0].metadata["plan_code"] == monthly.code
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
    assert subscription.plan_code == monthly.code
    assert aware(subscription.current_period_end) == clock.now + timedelta(days=30)


@pytest.mark.asyncio
async def test_due_renewal_is_claimed_once_across_workers(recurring_db) -> None:
    factory, clock, _subscription_id = recurring_db
    provider = FakeRecurringProvider(status=PaymentStatus.PENDING)
    first = scheduler(factory, clock, provider)
    second = scheduler(factory, clock, provider)

    await asyncio.gather(first.run_once(), second.run_once())

    async with factory() as database:
        count = await database.scalar(select(func.count()).select_from(PaymentAttempt))
    assert count == 1
    assert len(provider.recurring_calls) == 1


@pytest.mark.asyncio
async def test_temporary_cancellation_schedules_one_retry_next_day(recurring_db) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider(
        status=PaymentStatus.CANCELLED,
        cancellation_reason=PaymentCancellationReason.INSUFFICIENT_FUNDS,
    )
    runner = scheduler(factory, clock, provider)

    await runner.run_once()
    async with factory() as database:
        attempts = list(
            await database.scalars(
                select(PaymentAttempt).order_by(PaymentAttempt.renewal_attempt_number)
            )
        )
        subscription = await database.get(Subscription, subscription_id)
    assert [(row.renewal_attempt_number, row.status) for row in attempts] == [
        (1, "cancelled"),
        (2, "scheduled"),
    ]
    assert aware(attempts[1].next_dispatch_at) == clock.now + timedelta(days=1)
    assert aware(subscription.next_renewal_at) == aware(attempts[1].next_dispatch_at)

    await runner.run_once()
    assert len(provider.recurring_calls) == 1
    clock.advance(timedelta(days=1))
    provider.status = PaymentStatus.SUCCEEDED
    provider.cancellation_reason = None
    await runner.run_once()
    assert len(provider.recurring_calls) == 2


@pytest.mark.asyncio
async def test_disabled_subscription_and_missing_receipt_email_never_post(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        subscription.auto_renew = False
        subscription.next_renewal_at = None
    await scheduler(factory, clock, provider).run_once()
    assert provider.recurring_calls == []

    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        subscription.auto_renew = True
        subscription.next_renewal_at = clock.now
        identity = await database.scalar(select(UserIdentity))
        identity.email_verified = False
    settings = BillingReceiptSettings(enabled=True, vat_code=1)
    await scheduler(factory, clock, provider, receipts=settings).run_once()
    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
    assert provider.recurring_calls == []
    assert subscription.auto_renew is False
    assert aware(subscription.current_period_end) == clock.now


@pytest.mark.asyncio
async def test_permanent_cancellation_disables_method_without_shortening_period(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider(
        status=PaymentStatus.CANCELLED,
        cancellation_reason=PaymentCancellationReason.PERMISSION_REVOKED,
    )

    await scheduler(factory, clock, provider).run_once()

    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        method = await database.get(
            BillingPaymentMethod, subscription.payment_method_id
        )
        attempts = list(await database.scalars(select(PaymentAttempt)))
    assert len(attempts) == 1
    assert subscription.auto_renew is False
    assert subscription.next_renewal_at is None
    assert aware(subscription.current_period_end) == clock.now
    assert method.status == "invalid"


@pytest.mark.asyncio
async def test_ambiguous_post_never_creates_retry_and_expires_same_key(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = AmbiguousRecurringProvider()
    runner = scheduler(factory, clock, provider)

    await runner.run_once()
    clock.advance(timedelta(days=1))
    await runner.run_once()

    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        attempts = list(await database.scalars(select(PaymentAttempt)))
    assert len(provider.recurring_calls) == 1
    assert len(attempts) == 1
    assert attempts[0].status == "dispatch_unknown"
    assert subscription.auto_renew is False


@pytest.mark.asyncio
async def test_cross_provider_method_binding_fails_closed_before_post(
    recurring_db,
) -> None:
    factory, clock, subscription_id = recurring_db
    provider = FakeRecurringProvider()
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, subscription_id)
        method = await database.get(
            BillingPaymentMethod, subscription.payment_method_id
        )
        method.provider = "other-provider"

    await scheduler(factory, clock, provider).run_once()

    async with factory() as database:
        subscription = await database.get(Subscription, subscription_id)
        count = await database.scalar(select(func.count()).select_from(PaymentAttempt))
    assert provider.recurring_calls == []
    assert count == 0
    assert subscription.auto_renew is False
