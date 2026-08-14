from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import re

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentStatus,
    ProviderCheckout,
    ProviderNotification,
    ProviderPayment,
    ProviderPaymentMethod,
)
from app.billing.catalog import PLAN_CATALOG, BillingPlan
from app.billing.offers import (
    FOUNDER_GENERATION_TOKENS,
    FounderAccessService,
    FounderOfferUnavailable,
)
from app.billing.payments import (
    BillingError,
    BillingService,
    CheckoutIdempotencyConflict,
    IntroOfferUnavailable,
    UnknownPlan,
)
from app.billing.reconciliation import PaymentReconciler
from app.billing.service import GenerationCreditService, UsageBalanceService
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    BillingPaymentMethod,
    FounderAccessGrant,
    GenerationRun,
    Project,
    PaymentAttempt,
    PaymentWebhookEvent,
    Subscription,
    UsageLedger,
)


class FakeProvider:
    name = "fakepay"

    def __init__(
        self,
        merchant_account_fingerprint: str = "a" * 64,
        *,
        reconciliation_status: PaymentStatus = PaymentStatus.SUCCEEDED,
    ) -> None:
        self.merchant_account_fingerprint = merchant_account_fingerprint
        self.reconciliation_status = reconciliation_status
        self.checkout_calls: list[CheckoutCommand] = []
        self.verify_calls: list[
            tuple[ProviderNotification, Money | None, dict | None]
        ] = []
        self.get_calls: list[tuple[str, Money | None, dict | None]] = []
        self._gate = asyncio.Event()
        self._gate.set()

    async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
        self.checkout_calls.append(command)
        await self._gate.wait()
        return ProviderCheckout(
            provider_payment_id=f"pay-{command.metadata['payment_attempt_id']}",
            checkout_url=f"https://pay.example/{command.metadata['payment_attempt_id']}",
            status=PaymentStatus.PENDING,
            amount=command.amount,
            paid=False,
            metadata=command.metadata,
            test_mode=True,
        )

    def parse_notification(self, payload: object) -> ProviderNotification:
        assert isinstance(payload, dict)
        return ProviderNotification(
            str(payload["payment_id"]),
            str(payload.get("event", "payment.succeeded")),
        )

    async def verify_notification(
        self,
        notification: ProviderNotification,
        *,
        expected_amount: Money | None = None,
        expected_metadata=None,
    ) -> ProviderPayment:
        metadata = dict(expected_metadata or {})
        self.verify_calls.append((notification, expected_amount, metadata))
        assert expected_amount is not None
        cancelled = notification.event == "payment.canceled"
        return ProviderPayment(
            provider_payment_id=notification.provider_payment_id,
            status=PaymentStatus.CANCELLED if cancelled else PaymentStatus.SUCCEEDED,
            amount=expected_amount,
            paid=not cancelled,
            metadata=metadata,
            test_mode=True,
        )

    async def get_payment(
        self,
        provider_payment_id: str,
        *,
        expected_amount: Money | None = None,
        expected_metadata=None,
    ) -> ProviderPayment:
        metadata = dict(expected_metadata or {})
        self.get_calls.append((provider_payment_id, expected_amount, metadata))
        assert expected_amount is not None
        return ProviderPayment(
            provider_payment_id=provider_payment_id,
            status=self.reconciliation_status,
            amount=expected_amount,
            paid=self.reconciliation_status is PaymentStatus.SUCCEEDED,
            metadata=metadata,
            test_mode=True,
        )

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture
async def billing_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'billing.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add(User(id=10, tenant_id=1, email="owner@example.com"))
        database.add(User(id=11, tenant_id=1, email="other@example.com"))
    try:
        yield engine, factory
    finally:
        await engine.dispose()


async def _seed_pending_intro_attempt(
    factory,
    payments: BillingService,
    provider: FakeProvider,
    *,
    idempotency_key: str,
):
    intro = PLAN_CATALOG["starter_intro_15d"]
    async with factory() as database, database.begin():
        attempt = PaymentAttempt(
            user_id=10,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            purpose="initial",
            idempotency_key=idempotency_key,
            plan_code=intro.code,
            plan_snapshot=intro.snapshot(),
            plan_fingerprint=intro.fingerprint(),
            amount_minor=intro.amount.amount_minor,
            currency=intro.amount.currency,
            status="pending",
            checkout_url=f"https://pay.example/{idempotency_key}",
            provider_payment_id=f"{idempotency_key}-payment",
            payload={"test_mode": True},
            auto_renew_requested=False,
        )
        database.add(attempt)
        await database.flush()
        payment = ProviderPayment(
            provider_payment_id=str(attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(attempt.amount_minor, attempt.currency),
            paid=True,
            metadata=payments._metadata(attempt),
            test_mode=True,
        )
    return attempt, payment, intro


@pytest.mark.asyncio
async def test_checkout_is_server_priced_and_idempotent_per_user(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)

    first = await service.create_checkout(10, "starter_monthly", "checkout-key")
    replay = await service.create_checkout(10, "starter_monthly", "checkout-key")

    assert first.created is True
    assert replay.created is False
    assert replay.payment_id == first.payment_id
    assert replay.checkout_url == first.checkout_url
    assert len(provider.checkout_calls) == 1
    assert provider.checkout_calls[0].amount == Money(200_000, "RUB")
    assert provider.checkout_calls[0].metadata["user_id"] == "10"
    async with factory() as database:
        attempt = (await database.execute(select(PaymentAttempt))).scalar_one()
        assert attempt.plan_code == "starter_monthly"
        assert attempt.plan_snapshot["amount_minor"] == 200_000
        assert len(attempt.plan_fingerprint) == 64


@pytest.mark.asyncio
async def test_hidden_renewal_plan_cannot_be_started_as_public_checkout(
    billing_db,
) -> None:
    _engine, factory = billing_db
    service = BillingService(factory, FakeProvider())

    with pytest.raises(UnknownPlan):
        await service.create_checkout(
            10,
            "starter_intro_balance_15d",
            "hidden-renewal-plan",
            auto_renew=True,
        )


@pytest.mark.asyncio
async def test_checkout_persists_explicit_auto_renew_consent_and_replays_exact_intent(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)

    checkout = await service.create_checkout(
        10,
        "starter_monthly",
        "auto-renew-consent-key",
        auto_renew=True,
    )

    assert provider.checkout_calls[0].save_payment_method is True
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        assert attempt is not None
        assert attempt.purpose == "initial"
        assert attempt.auto_renew_requested is True
        assert attempt.save_payment_method_requested is True
        assert attempt.consent_version == "yookassa-auto-renew-v1"
        assert attempt.consented_at is not None

    with pytest.raises(CheckoutIdempotencyConflict):
        await service.create_checkout(
            10,
            "starter_monthly",
            "auto-renew-consent-key",
            auto_renew=False,
        )


@pytest.mark.asyncio
async def test_verified_saved_method_enables_only_explicitly_consented_subscription(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)

    opted_in = await service.create_checkout(
        10,
        "starter_monthly",
        "saved-method-opt-in",
        auto_renew=True,
    )
    opted_out = await service.create_checkout(
        11,
        "starter_monthly",
        "saved-method-opt-out",
        auto_renew=False,
    )

    async with factory() as database:
        opted_in_attempt = await database.get(PaymentAttempt, opted_in.payment_id)
        opted_out_attempt = await database.get(PaymentAttempt, opted_out.payment_id)
        assert opted_in_attempt is not None
        assert opted_out_attempt is not None
        opted_in_payment = ProviderPayment(
            provider_payment_id=str(opted_in_attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(opted_in_attempt.amount_minor, opted_in_attempt.currency),
            paid=True,
            metadata=service._metadata(opted_in_attempt),
            test_mode=True,
            payment_method=ProviderPaymentMethod(
                provider_payment_method_id="saved-method-owner",
                saved=True,
                method_type="bank_card",
            ),
        )
        opted_out_payment = ProviderPayment(
            provider_payment_id=str(opted_out_attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(opted_out_attempt.amount_minor, opted_out_attempt.currency),
            paid=True,
            metadata=service._metadata(opted_out_attempt),
            test_mode=True,
            payment_method=ProviderPaymentMethod(
                provider_payment_method_id="saved-method-without-consent",
                saved=True,
                method_type="bank_card",
            ),
        )

    await service.apply_verified_payment(
        opted_in.payment_id,
        opted_in_payment,
        source="webhook",
    )
    await service.apply_verified_payment(
        opted_out.payment_id,
        opted_out_payment,
        source="webhook",
    )

    async with factory() as database:
        methods = list(
            await database.scalars(
                select(BillingPaymentMethod).order_by(BillingPaymentMethod.user_id)
            )
        )
        subscriptions = list(
            await database.scalars(select(Subscription).order_by(Subscription.user_id))
        )
        stored_opted_in_attempt = await database.get(
            PaymentAttempt,
            opted_in.payment_id,
        )

    assert len(methods) == 1
    assert methods[0].user_id == 10
    assert (
        methods[0].merchant_account_fingerprint == provider.merchant_account_fingerprint
    )
    assert methods[0].provider_payment_method_id == "saved-method-owner"
    assert methods[0].status == "active"
    assert stored_opted_in_attempt is not None
    assert stored_opted_in_attempt.payment_method_id == methods[0].id
    opted_in_subscription, opted_out_subscription = subscriptions
    assert opted_in_subscription.auto_renew is True
    assert (
        opted_in_subscription.merchant_account_fingerprint
        == provider.merchant_account_fingerprint
    )
    assert opted_in_subscription.payment_method_id == methods[0].id
    assert (
        opted_in_subscription.next_renewal_at
        == opted_in_subscription.current_period_end
    )
    assert opted_out_subscription.auto_renew is False
    assert opted_out_subscription.payment_method_id is None
    assert opted_out_subscription.next_renewal_at is None


@pytest.mark.asyncio
async def test_unsaved_method_never_enables_auto_renew(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    checkout = await service.create_checkout(
        10,
        "starter_monthly",
        "unsaved-method-opt-in",
        auto_renew=True,
    )

    async with factory() as database:
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        assert attempt is not None
        payment = ProviderPayment(
            provider_payment_id=str(attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(attempt.amount_minor, attempt.currency),
            paid=True,
            metadata=service._metadata(attempt),
            test_mode=True,
            payment_method=None,
        )

    await service.apply_verified_payment(
        checkout.payment_id,
        payment,
        source="reconciliation",
    )

    async with factory() as database:
        assert (
            await database.scalar(
                select(func.count()).select_from(BillingPaymentMethod)
            )
        ) == 0
        subscription = await database.scalar(select(Subscription))
        assert subscription is not None
        assert subscription.auto_renew is False
        assert subscription.payment_method_id is None
        assert subscription.next_renewal_at is None


@pytest.mark.asyncio
async def test_one_time_intro_fulfills_without_saving_or_scheduling_renewal(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    checkout = await service.create_checkout(
        10,
        "starter_intro_15d",
        "one-time-intro",
        auto_renew=False,
    )

    async with factory() as database:
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        assert attempt is not None
        assert attempt.save_payment_method_requested is None
        payment = ProviderPayment(
            provider_payment_id=str(attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(attempt.amount_minor, attempt.currency),
            paid=True,
            metadata=service._metadata(attempt),
            test_mode=True,
            payment_method=ProviderPaymentMethod(
                provider_payment_method_id="provider-returned-but-not-consented",
                saved=True,
                method_type="bank_card",
            ),
        )

    await service.apply_verified_payment(
        checkout.payment_id,
        payment,
        source="webhook",
    )

    async with factory() as database:
        subscription = await database.scalar(select(Subscription))
        methods = await database.scalar(
            select(func.count()).select_from(BillingPaymentMethod)
        )
    assert subscription is not None
    assert subscription.plan_code == "starter_intro_15d"
    assert subscription.auto_renew is False
    assert subscription.payment_method_id is None
    assert subscription.next_renewal_at is None
    assert methods == 0


@pytest.mark.asyncio
async def test_intro_offer_cannot_be_bought_twice_with_a_new_key(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    first = await service.create_checkout(
        10,
        "starter_intro_15d",
        "first-intro-purchase",
        auto_renew=False,
    )
    await service.handle_notification(
        {"payment_id": f"pay-{first.payment_id}"}
    )

    async with factory() as database:
        subscription = await database.scalar(select(Subscription))
        credit_before = await database.scalar(
            select(func.sum(UsageLedger.amount)).where(
                UsageLedger.entry_type == "subscription.credit"
            )
        )
    assert subscription is not None
    period_end_before = subscription.current_period_end

    replay = await service.create_checkout(
        10,
        "starter_intro_15d",
        "first-intro-purchase",
        auto_renew=False,
    )
    assert replay.created is False
    assert replay.payment_id == first.payment_id

    with pytest.raises(IntroOfferUnavailable):
        await service.create_checkout(
            10,
            "starter_intro_15d",
            "second-intro-purchase",
            auto_renew=False,
        )

    async with factory() as database:
        subscription = await database.scalar(select(Subscription))
        credit_after = await database.scalar(
            select(func.sum(UsageLedger.amount)).where(
                UsageLedger.entry_type == "subscription.credit"
            )
        )
    assert subscription is not None
    assert subscription.current_period_end == period_end_before
    assert credit_before == credit_after == 500_000
    assert len(provider.checkout_calls) == 1


@pytest.mark.asyncio
async def test_intro_cannot_replace_an_active_recurring_subscription(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    monthly = await service.create_checkout(
        10,
        "starter_monthly",
        "monthly-before-intro",
        auto_renew=True,
    )
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, monthly.payment_id)
        assert attempt is not None
        payment = ProviderPayment(
            provider_payment_id=str(attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(attempt.amount_minor, attempt.currency),
            paid=True,
            metadata=service._metadata(attempt),
            test_mode=True,
            payment_method=ProviderPaymentMethod(
                provider_payment_method_id="monthly-method",
                saved=True,
            ),
        )
    await service.apply_verified_payment(monthly.payment_id, payment, source="webhook")

    with pytest.raises(IntroOfferUnavailable):
        await service.create_checkout(
            10,
            "starter_intro_15d",
            "intro-after-monthly",
            auto_renew=False,
        )

    async with factory() as database:
        subscription = await database.scalar(select(Subscription))
    assert subscription is not None
    assert subscription.plan_code == "starter_monthly"
    assert subscription.auto_renew is True
    assert subscription.payment_method_id is not None
    assert subscription.next_renewal_at == subscription.current_period_end
    assert len(provider.checkout_calls) == 1


@pytest.mark.asyncio
async def test_founder_claim_rejects_pending_paid_intro_checkout(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    await payments.create_checkout(
        10,
        "starter_intro_15d",
        "pending-intro-before-founder",
        auto_renew=False,
    )

    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://pending-intro.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id

    offer = await FounderAccessService(factory).offer(10, project_id)
    assert offer.eligible is False
    assert offer.reason == "pending_paid_intro"

    with pytest.raises(FounderOfferUnavailable, match="pending_paid_intro"):
        await FounderAccessService(factory).claim(10, project_id)


@pytest.mark.asyncio
async def test_intro_webhook_cannot_replace_founder_subscription(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://founder-lineage.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    founder = await FounderAccessService(factory).claim(10, project_id)

    intro = PLAN_CATALOG["starter_intro_15d"]
    async with factory() as database, database.begin():
        attempt = PaymentAttempt(
            user_id=10,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            purpose="initial",
            idempotency_key="stale-intro-founder-conflict",
            plan_code=intro.code,
            plan_snapshot=intro.snapshot(),
            plan_fingerprint=intro.fingerprint(),
            amount_minor=intro.amount.amount_minor,
            currency=intro.amount.currency,
            status="pending",
            checkout_url="https://pay.example/stale-intro-founder-conflict",
            provider_payment_id="stale-intro-founder-conflict-payment",
            payload={"test_mode": True},
            auto_renew_requested=False,
        )
        database.add(attempt)
        await database.flush()
        payment = ProviderPayment(
            provider_payment_id=str(attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(attempt.amount_minor, attempt.currency),
            paid=True,
            metadata=payments._metadata(attempt),
            test_mode=True,
        )

    replay = await FounderAccessService(factory).claim(10, project_id)
    assert replay.created is False
    assert replay.grant.id == founder.grant.id

    first_result = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert first_result.processed is True

    async with factory() as database:
        founder_subscription = await database.get(
            Subscription, founder.grant.subscription_id
        )
        paid_subscription = await database.scalar(
            select(Subscription).where(
                Subscription.payment_attempt_id == attempt.id
            )
        )
        grant = await database.get(FounderAccessGrant, founder.grant.id)
        attempt = await database.get(PaymentAttempt, attempt.id)
        credit_total = await database.scalar(
            select(func.sum(UsageLedger.amount)).where(UsageLedger.user_id == 10)
        )
    assert founder_subscription is not None
    assert founder_subscription.provider == "founder"
    assert founder_subscription.plan_code == "founder_14d"
    assert founder_subscription.status == "expired"
    assert paid_subscription is not None
    assert paid_subscription.provider == provider.name
    assert paid_subscription.plan_code == "starter_intro_15d"
    assert paid_subscription.status == "active"
    assert grant is not None
    assert grant.subscription_id == founder_subscription.id
    grant_end = grant.ends_at
    if grant_end.tzinfo is None:
        grant_end = grant_end.replace(tzinfo=UTC)
    assert grant_end <= datetime.now(UTC)
    assert attempt is not None
    assert attempt.status == "succeeded"
    transition = attempt.payload["founder_access_transition"]
    assert transition == {
        "resolution": "paid_intro_wins",
        "grant_id": str(grant.id),
        "subscription_id": str(founder_subscription.id),
    }
    assert credit_total == FOUNDER_GENERATION_TOKENS + intro.generation_tokens

    second_result = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert second_result.processed is False
    assert second_result.subscription_id == paid_subscription.id

    async with factory() as database:
        assert (
            await database.scalar(
                select(func.count()).select_from(UsageLedger).where(
                    UsageLedger.user_id == 10,
                    UsageLedger.entry_type == "subscription.credit",
                )
        )
        == 2
        )


@pytest.mark.asyncio
async def test_intro_webhook_does_not_extend_active_founder_with_expired_end(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://expired-active-founder.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    founder = await FounderAccessService(factory).claim(10, project_id)

    old_start = datetime.now(UTC) - timedelta(days=10)
    old_end = datetime.now(UTC) - timedelta(days=1)
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, founder.grant.subscription_id)
        assert subscription is not None
        subscription.status = "active"
        subscription.current_period_start = old_start
        subscription.current_period_end = old_end

    attempt, payment, _intro = await _seed_pending_intro_attempt(
        factory,
        payments,
        provider,
        idempotency_key="expired-active-founder-intro",
    )
    result = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert result.processed is True

    async with factory() as database:
        subscription = await database.get(Subscription, founder.grant.subscription_id)
    assert subscription is not None
    assert subscription.status == "expired"
    actual_end = subscription.current_period_end
    if actual_end.tzinfo is None:
        actual_end = actual_end.replace(tzinfo=UTC)
    assert actual_end == old_end


@pytest.mark.asyncio
async def test_intro_webhook_after_expired_founder_subscription_still_fulfills(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://expired-founder-lineage.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    founder = await FounderAccessService(factory).claim(10, project_id)

    async with factory() as database, database.begin():
        founder_subscription = await database.get(
            Subscription, founder.grant.subscription_id
        )
        assert founder_subscription is not None
        founder_subscription.status = "expired"
        expired_end = datetime.now(UTC) - timedelta(days=1)
        founder_subscription.current_period_end = expired_end
        grant = await database.get(FounderAccessGrant, founder.grant.id)
        assert grant is not None
        expired_grant_end = datetime.now(UTC) - timedelta(days=2)
        grant.ends_at = expired_grant_end

    attempt, payment, intro = await _seed_pending_intro_attempt(
        factory,
        payments,
        provider,
        idempotency_key="expired-intro-founder-conflict",
    )

    result = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert result.processed is True

    async with factory() as database:
        founder_subscription = await database.get(
            Subscription, founder.grant.subscription_id
        )
        paid_subscription = await database.scalar(
            select(Subscription).where(Subscription.payment_attempt_id == attempt.id)
        )
        grant = await database.get(FounderAccessGrant, founder.grant.id)
        attempt = await database.get(PaymentAttempt, attempt.id)
        credit_total = await database.scalar(
            select(func.sum(UsageLedger.amount)).where(UsageLedger.user_id == 10)
        )
    assert founder_subscription is not None
    assert founder_subscription.status == "expired"
    actual_expired_end = founder_subscription.current_period_end
    if actual_expired_end.tzinfo is None:
        actual_expired_end = actual_expired_end.replace(tzinfo=UTC)
    assert actual_expired_end == expired_end
    assert paid_subscription is not None
    assert paid_subscription.status == "active"
    assert grant is not None
    assert grant.subscription_id == founder_subscription.id
    actual_grant_end = grant.ends_at
    if actual_grant_end.tzinfo is None:
        actual_grant_end = actual_grant_end.replace(tzinfo=UTC)
    assert actual_grant_end == expired_grant_end
    assert attempt is not None
    assert attempt.status == "succeeded"
    assert attempt.payload["founder_access_transition"]["grant_id"] == str(grant.id)
    assert (
        credit_total
        == FOUNDER_GENERATION_TOKENS + intro.generation_tokens
    )

    replay = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert replay.processed is False
    async with factory() as database:
        assert (
            await database.scalar(
                select(func.count()).select_from(UsageLedger).where(
                    UsageLedger.user_id == 10,
                    UsageLedger.entry_type == "subscription.credit",
                )
            )
            == 2
        )


@pytest.mark.asyncio
async def test_intro_webhook_compensates_same_row_converted_paid_subscription(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://same-row-paid-lineage.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    founder = await FounderAccessService(factory).claim(10, project_id)

    monthly = PLAN_CATALOG["starter_monthly"]
    baseline_start = datetime.now(UTC) - timedelta(days=1)
    baseline_end = datetime.now(UTC) + timedelta(days=4)
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, founder.grant.subscription_id)
        assert subscription is not None
        monthly_attempt = PaymentAttempt(
            user_id=10,
            subscription_id=subscription.id,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            purpose="initial",
            idempotency_key="same-row-monthly-initial",
            plan_code=monthly.code,
            plan_snapshot=monthly.snapshot(),
            plan_fingerprint=monthly.fingerprint(),
            amount_minor=monthly.amount.amount_minor,
            currency=monthly.amount.currency,
            status="succeeded",
            checkout_url="https://pay.example/same-row-monthly-initial",
            provider_payment_id="same-row-monthly-initial-payment",
            payload={"test_mode": True},
            auto_renew_requested=False,
        )
        database.add(monthly_attempt)
        await database.flush()
        database.add(
            UsageLedger(
                user_id=10,
                payment_attempt_id=monthly_attempt.id,
                bucket="tokens",
                entry_type="subscription.credit",
                amount=monthly.generation_tokens,
                idempotency_key="payment:same-row-monthly-initial:tokens",
                payload={"plan_code": monthly.code},
            )
        )
        method = BillingPaymentMethod(
            user_id=10,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            provider_payment_method_id="same-row-monthly-method",
            status="active",
            consent_version="test-consent",
            consented_at=baseline_start,
            saved_at=baseline_start,
        )
        database.add(method)
        await database.flush()
        subscription.provider = provider.name
        subscription.payment_method_id = method.id
        subscription.merchant_account_fingerprint = (
            provider.merchant_account_fingerprint
        )
        subscription.plan_code = monthly.code
        subscription.plan_snapshot = monthly.snapshot()
        subscription.plan_fingerprint = monthly.fingerprint()
        subscription.payment_attempt_id = monthly_attempt.id
        subscription.status = "active"
        subscription.current_period_start = baseline_start
        subscription.current_period_end = baseline_end
        subscription.auto_renew = True
        subscription.next_renewal_at = baseline_end

    credits = GenerationCreditService(factory)
    assert await credits.available_tokens(10) == monthly.generation_tokens

    attempt, payment, intro = await _seed_pending_intro_attempt(
        factory,
        payments,
        provider,
        idempotency_key="same-row-converted-intro",
    )
    result = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert result.processed is True

    async with factory() as database:
        subscription = await database.get(Subscription, founder.grant.subscription_id)
        attempt = await database.get(PaymentAttempt, attempt.id)
        grant = await database.get(FounderAccessGrant, founder.grant.id)
        subscription_credits = list(
            await database.scalars(
                select(UsageLedger).where(
                    UsageLedger.payment_attempt_id == monthly_attempt.id,
                    UsageLedger.entry_type == "subscription.credit",
                )
            )
        )
    assert subscription is not None
    assert subscription.status == "active"
    assert subscription.provider == provider.name
    assert subscription.plan_code == monthly.code
    assert subscription.payment_method_id == method.id
    assert subscription.auto_renew is True
    actual_end = subscription.current_period_end
    if actual_end.tzinfo is None:
        actual_end = actual_end.replace(tzinfo=UTC)
    assert actual_end == baseline_end + timedelta(days=intro.period_days)
    actual_next_renewal = subscription.next_renewal_at
    if actual_next_renewal.tzinfo is None:
        actual_next_renewal = actual_next_renewal.replace(tzinfo=UTC)
    assert actual_next_renewal == actual_end
    assert grant is not None
    assert grant.subscription_id == subscription.id
    assert attempt is not None
    assert attempt.status == "succeeded"
    transition = attempt.payload["founder_access_transition"]
    assert transition["resolution"] == "paid_intro_compensated_on_active_paid"
    assert transition["grant_id"] == str(grant.id)
    assert transition["subscription_id"] == str(subscription.id)
    assert transition["paid_subscription_id"] == str(subscription.id)
    compensation_credit = next(
        row
        for row in subscription_credits
        if row.payload.get("source_payment_attempt_id") == str(attempt.id)
    )
    assert compensation_credit.payment_attempt_id == monthly_attempt.id
    assert compensation_credit.payload["entitlement_payment_attempt_id"] == str(
        monthly_attempt.id
    )
    assert (
        compensation_credit.payload["compensation_resolution"]
        == "paid_intro_compensated_on_active_paid"
    )
    assert await credits.available_tokens(10) == (
        monthly.generation_tokens + intro.generation_tokens
    )

    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=project_id,
            mode="express",
            state="queued",
            progress=0,
            next_event_sequence=1,
            idempotency_key="same-row-compensation-run",
        )
        database.add(run)
        await database.flush()
        reservation = await credits.reserve_in_session(
            database,
            user_id=10,
            project_id=project_id,
            run_id=run.id,
            amount=100_000,
        )
        assert reservation.payment_attempt_id == monthly_attempt.id
    assert await credits.available_tokens(10) == (
        monthly.generation_tokens + intro.generation_tokens - 100_000
    )

    replay = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert replay.processed is False
    assert await credits.available_tokens(10) == (
        monthly.generation_tokens + intro.generation_tokens - 100_000
    )


@pytest.mark.asyncio
async def test_intro_webhook_compensates_active_paid_with_separate_expired_founder(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://separate-paid-lineage.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    founder = await FounderAccessService(factory).claim(10, project_id)

    old_founder_end = datetime.now(UTC) - timedelta(days=3)
    old_grant_end = datetime.now(UTC) - timedelta(days=2)
    async with factory() as database, database.begin():
        founder_subscription = await database.get(
            Subscription, founder.grant.subscription_id
        )
        grant = await database.get(FounderAccessGrant, founder.grant.id)
        assert founder_subscription is not None
        assert grant is not None
        founder_subscription.status = "expired"
        founder_subscription.current_period_end = old_founder_end
        grant.ends_at = old_grant_end

    monthly_checkout = await payments.create_checkout(
        10,
        "starter_monthly",
        "separate-active-monthly",
        auto_renew=False,
    )
    await payments.handle_notification(
        {"payment_id": f"pay-{monthly_checkout.payment_id}"}
    )
    async with factory() as database:
        monthly_subscription = await database.scalar(
            select(Subscription).where(
                Subscription.user_id == 10,
                Subscription.status == "active",
            )
        )
        assert monthly_subscription is not None
        monthly_end = monthly_subscription.current_period_end
        monthly_payment_attempt_id = monthly_subscription.payment_attempt_id
        monthly_provider = monthly_subscription.provider
        monthly_plan_code = monthly_subscription.plan_code
        monthly_auto_renew = monthly_subscription.auto_renew
        monthly_method_id = monthly_subscription.payment_method_id
    monthly = PLAN_CATALOG["starter_monthly"]
    credits = GenerationCreditService(factory)
    assert await credits.available_tokens(10) == monthly.generation_tokens

    attempt, payment, intro = await _seed_pending_intro_attempt(
        factory,
        payments,
        provider,
        idempotency_key="separate-expired-founder-intro",
    )
    result = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert result.processed is True

    async with factory() as database:
        founder_subscription = await database.get(
            Subscription, founder.grant.subscription_id
        )
        grant = await database.get(FounderAccessGrant, founder.grant.id)
        monthly_subscription = await database.get(
            Subscription, monthly_subscription.id
        )
        attempt = await database.get(PaymentAttempt, attempt.id)
        subscription_credits = list(
            await database.scalars(
                select(UsageLedger).where(
                    UsageLedger.payment_attempt_id == monthly_payment_attempt_id,
                    UsageLedger.entry_type == "subscription.credit",
                )
            )
        )
        active_count = await database.scalar(
            select(func.count()).select_from(Subscription).where(
                Subscription.user_id == 10,
                Subscription.status == "active",
            )
        )
    assert founder_subscription is not None
    actual_founder_end = founder_subscription.current_period_end
    if actual_founder_end.tzinfo is None:
        actual_founder_end = actual_founder_end.replace(tzinfo=UTC)
    assert actual_founder_end == old_founder_end
    assert grant is not None
    actual_grant_end = grant.ends_at
    if actual_grant_end.tzinfo is None:
        actual_grant_end = actual_grant_end.replace(tzinfo=UTC)
    assert actual_grant_end == old_grant_end
    assert monthly_subscription is not None
    assert monthly_subscription.status == "active"
    assert monthly_subscription.provider == monthly_provider
    assert monthly_subscription.plan_code == monthly_plan_code
    assert monthly_subscription.payment_attempt_id == monthly_payment_attempt_id
    assert monthly_subscription.auto_renew is monthly_auto_renew
    assert monthly_subscription.payment_method_id == monthly_method_id
    actual_monthly_end = monthly_subscription.current_period_end
    if actual_monthly_end.tzinfo is None:
        actual_monthly_end = actual_monthly_end.replace(tzinfo=UTC)
    expected_monthly_end = monthly_end
    if expected_monthly_end.tzinfo is None:
        expected_monthly_end = expected_monthly_end.replace(tzinfo=UTC)
    assert actual_monthly_end == expected_monthly_end + timedelta(
        days=intro.period_days
    )
    assert active_count == 1
    assert attempt is not None
    assert attempt.status == "succeeded"
    assert (
        attempt.payload["founder_access_transition"]["resolution"]
        == "paid_intro_compensated_on_active_paid"
    )
    compensation_credit = next(
        row
        for row in subscription_credits
        if row.payload.get("source_payment_attempt_id") == str(attempt.id)
    )
    assert compensation_credit.payment_attempt_id == monthly_payment_attempt_id
    assert compensation_credit.payload["entitlement_payment_attempt_id"] == str(
        monthly_payment_attempt_id
    )
    assert (
        compensation_credit.payload["compensation_resolution"]
        == "paid_intro_compensated_on_active_paid"
    )
    assert await credits.available_tokens(10) == (
        monthly.generation_tokens + intro.generation_tokens
    )

    replay = await payments.apply_verified_payment(
        attempt.id,
        payment,
        source="webhook",
    )
    assert replay.processed is False
    assert await credits.available_tokens(10) == (
        monthly.generation_tokens + intro.generation_tokens
    )


@pytest.mark.asyncio
async def test_compensated_intro_credit_rolls_off_after_successful_renewal(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://renewal-compensation-lineage.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    founder = await FounderAccessService(factory).claim(10, project_id)

    async with factory() as database, database.begin():
        founder_subscription = await database.get(
            Subscription, founder.grant.subscription_id
        )
        grant = await database.get(FounderAccessGrant, founder.grant.id)
        assert founder_subscription is not None
        assert grant is not None
        founder_subscription.status = "expired"
        founder_subscription.current_period_end = datetime.now(UTC) - timedelta(
            days=2
        )
        grant.ends_at = datetime.now(UTC) - timedelta(days=1)

    monthly = PLAN_CATALOG["starter_monthly"]
    monthly_checkout = await payments.create_checkout(
        10,
        monthly.code,
        "renewal-compensation-monthly",
        auto_renew=False,
    )
    await payments.handle_notification(
        {"payment_id": f"pay-{monthly_checkout.payment_id}"}
    )
    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(
                Subscription.user_id == 10,
                Subscription.status == "active",
            )
        )
        assert subscription is not None
        method = BillingPaymentMethod(
            user_id=10,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            provider_payment_method_id="renewal-compensation-method",
            status="active",
            consent_version="test-consent",
            consented_at=datetime.now(UTC),
            saved_at=datetime.now(UTC),
        )
        database.add(method)
        await database.flush()
        subscription.payment_method_id = method.id
        subscription.merchant_account_fingerprint = (
            provider.merchant_account_fingerprint
        )
        subscription.auto_renew = True
        subscription.next_renewal_at = subscription.current_period_end
        monthly_attempt_id = subscription.payment_attempt_id
        assert monthly_attempt_id is not None

    credits = GenerationCreditService(factory)
    attempt, payment, intro = await _seed_pending_intro_attempt(
        factory,
        payments,
        provider,
        idempotency_key="renewal-compensation-intro",
    )
    await payments.apply_verified_payment(attempt.id, payment, source="webhook")
    assert await credits.available_tokens(10) == (
        monthly.generation_tokens + intro.generation_tokens
    )

    async with factory() as database, database.begin():
        run = GenerationRun(
            project_id=project_id,
            mode="express",
            state="queued",
            progress=0,
            next_event_sequence=1,
            idempotency_key="renewal-compensation-run",
        )
        database.add(run)
        await database.flush()
        reservation = await credits.reserve_in_session(
            database,
            user_id=10,
            project_id=project_id,
            run_id=run.id,
            amount=1_400_000,
        )
        assert reservation.payment_attempt_id == monthly_attempt_id
    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(
                Subscription.user_id == 10,
                Subscription.status == "active",
            )
        )
        assert subscription is not None
        renew_start = datetime.now(UTC)
        subscription.current_period_start = renew_start - timedelta(days=30)
        subscription.current_period_end = renew_start
        subscription.next_renewal_at = renew_start

    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(
                Subscription.user_id == 10,
                Subscription.status == "active",
            )
        )
        assert subscription is not None
        method = await database.get(BillingPaymentMethod, subscription.payment_method_id)
        assert method is not None
        renewal_start = subscription.current_period_end
        renewal_end = renewal_start + timedelta(days=monthly.period_days)
        renewal_attempt = PaymentAttempt(
            user_id=10,
            provider=provider.name,
            merchant_account_fingerprint=provider.merchant_account_fingerprint,
            purpose="renewal",
            subscription_id=subscription.id,
            payment_method_id=method.id,
            billing_period_start=renewal_start,
            billing_period_end=renewal_end,
            renewal_attempt_number=1,
            retry_of_payment_attempt_id=None,
            retry_of_renewal_attempt_number=None,
            next_dispatch_at=None,
            auto_renew_requested=True,
            save_payment_method_requested=False,
            consent_version=method.consent_version,
            consented_at=method.consented_at,
            idempotency_key="renewal-compensation-primary",
            plan_code=monthly.code,
            plan_snapshot=monthly.snapshot(),
            plan_fingerprint=monthly.fingerprint(),
            amount_minor=monthly.amount.amount_minor,
            currency=monthly.amount.currency,
            status="pending",
            checkout_url=None,
            provider_payment_id="renewal-compensation-payment",
            payload={"test_mode": True},
        )
        database.add(renewal_attempt)
        await database.flush()
        renewal_payment = ProviderPayment(
            provider_payment_id=str(renewal_attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(renewal_attempt.amount_minor, renewal_attempt.currency),
            paid=True,
            metadata=payments._metadata(renewal_attempt),
            test_mode=True,
        )
        renewal_attempt_id = renewal_attempt.id

    result = await payments.apply_verified_payment(
        renewal_attempt_id,
        renewal_payment,
        source="webhook",
    )
    assert result.processed is True
    async with factory() as database:
        subscription = await database.scalar(
            select(Subscription).where(
                Subscription.user_id == 10,
                Subscription.status == "active",
            )
        )
        assert subscription is not None
        assert subscription.payment_attempt_id == renewal_attempt_id
        available = await GenerationCreditService.available_for_subscription_in_session(
            database,
            subscription,
        )
    assert available == monthly.generation_tokens


@pytest.mark.asyncio
async def test_founder_grant_consumes_intro_offer_after_subscription_expires(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://expired-founder.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id
    founder = await FounderAccessService(factory).claim(10, project_id)

    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, founder.grant.subscription_id)
        assert subscription is not None
        subscription.status = "expired"
        subscription.current_period_end = datetime.now(UTC) - timedelta(days=1)

    assert await payments.intro_offer_available(10) is False


@pytest.mark.asyncio
async def test_founder_offer_reports_active_paid_subscription_as_unavailable(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    checkout = await payments.create_checkout(
        10,
        "starter_monthly",
        "active-paid-before-founder",
        auto_renew=False,
    )
    await payments.handle_notification({"payment_id": f"pay-{checkout.payment_id}"})

    async with factory() as database, database.begin():
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://active-paid.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id

    offer = await FounderAccessService(factory).offer(10, project_id)

    assert offer.eligible is False
    assert offer.reason == "paid_access_used"


@pytest.mark.asyncio
async def test_founder_offer_and_claim_stay_blocked_after_paid_access_expires(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    payments = BillingService(factory, provider)
    checkout = await payments.create_checkout(
        10,
        "starter_monthly",
        "expired-paid-before-founder",
        auto_renew=False,
    )
    await payments.handle_notification({"payment_id": f"pay-{checkout.payment_id}"})

    async with factory() as database, database.begin():
        subscription = await database.scalar(select(Subscription))
        assert subscription is not None
        subscription.status = "expired"
        subscription.current_period_end = datetime.now(UTC) - timedelta(days=1)
        project = Project(
            tenant_id=1,
            owner_user_id=10,
            source_url="https://expired-paid.example/services",
            status="free_result_ready",
        )
        database.add(project)
        await database.flush()
        project_id = project.id

    founder = FounderAccessService(factory)
    offer = await founder.offer(10, project_id)
    assert offer.eligible is False
    assert offer.reason == "paid_access_used"
    with pytest.raises(FounderOfferUnavailable, match="paid_access_used"):
        await founder.claim(10, project_id)


@pytest.mark.asyncio
async def test_one_time_purchase_does_not_revoke_existing_auto_renew_consent(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)

    opted_in = await service.create_checkout(
        10,
        "starter_monthly",
        "existing-auto-renew-opt-in",
        auto_renew=True,
    )
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, opted_in.payment_id)
        assert attempt is not None
        first_payment = ProviderPayment(
            provider_payment_id=str(attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(attempt.amount_minor, attempt.currency),
            paid=True,
            metadata=service._metadata(attempt),
            test_mode=True,
            payment_method=ProviderPaymentMethod(
                provider_payment_method_id="existing-auto-renew-method",
                saved=True,
            ),
        )
    await service.apply_verified_payment(
        opted_in.payment_id,
        first_payment,
        source="webhook",
    )

    one_time = await service.create_checkout(
        10,
        "starter_monthly",
        "one-time-does-not-revoke",
        auto_renew=False,
    )
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, one_time.payment_id)
        subscription_before = await database.scalar(select(Subscription))
        assert attempt is not None
        assert subscription_before is not None
        method_id = subscription_before.payment_method_id
        second_payment = ProviderPayment(
            provider_payment_id=str(attempt.provider_payment_id),
            status=PaymentStatus.SUCCEEDED,
            amount=Money(attempt.amount_minor, attempt.currency),
            paid=True,
            metadata=service._metadata(attempt),
            test_mode=True,
            payment_method=None,
        )
    await service.apply_verified_payment(
        one_time.payment_id,
        second_payment,
        source="webhook",
    )

    async with factory() as database:
        subscription = await database.scalar(select(Subscription))
        assert subscription is not None
        assert subscription.auto_renew is True
        assert subscription.payment_method_id == method_id
        assert subscription.next_renewal_at == subscription.current_period_end


@pytest.mark.asyncio
async def test_saved_method_identity_cannot_be_claimed_by_another_user(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)

    first = await service.create_checkout(
        10,
        "starter_monthly",
        "saved-method-first-owner",
        auto_renew=True,
    )
    second = await service.create_checkout(
        11,
        "starter_monthly",
        "saved-method-second-owner",
        auto_renew=True,
    )

    async def verified_payment(attempt_id) -> ProviderPayment:
        async with factory() as database:
            attempt = await database.get(PaymentAttempt, attempt_id)
            assert attempt is not None
            return ProviderPayment(
                provider_payment_id=str(attempt.provider_payment_id),
                status=PaymentStatus.SUCCEEDED,
                amount=Money(attempt.amount_minor, attempt.currency),
                paid=True,
                metadata=service._metadata(attempt),
                test_mode=True,
                payment_method=ProviderPaymentMethod(
                    provider_payment_method_id="same-opaque-method-id",
                    saved=True,
                ),
            )

    await service.apply_verified_payment(
        first.payment_id,
        await verified_payment(first.payment_id),
        source="webhook",
    )
    result = await service.apply_verified_payment(
        second.payment_id,
        await verified_payment(second.payment_id),
        source="webhook",
    )

    async with factory() as database:
        methods = list(await database.scalars(select(BillingPaymentMethod)))
        subscriptions = list(
            await database.scalars(
                select(Subscription).order_by(Subscription.user_id)
            )
        )
        credits = list(
            await database.scalars(
                select(UsageLedger).where(
                    UsageLedger.entry_type == "subscription.credit"
                )
            )
        )
    assert result.processed is True
    assert [method.user_id for method in methods] == [10]
    assert [subscription.user_id for subscription in subscriptions] == [10, 11]
    assert subscriptions[1].auto_renew is False
    assert subscriptions[1].payment_method_id is None
    assert len(credits) == 2


@pytest.mark.asyncio
async def test_checkout_same_key_different_plan_conflicts(billing_db) -> None:
    _engine, factory = billing_db
    service = BillingService(factory, FakeProvider())
    await service.create_checkout(10, "starter_monthly", "same-key")

    with pytest.raises(CheckoutIdempotencyConflict):
        await service.create_checkout(10, "starter_quarterly", "same-key")


@pytest.mark.asyncio
async def test_provider_idempotency_is_global_even_when_public_keys_match(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)

    first = await service.create_checkout(10, "starter_monthly", "shared-public-key")
    second = await service.create_checkout(11, "starter_monthly", "shared-public-key")

    assert first.payment_id != second.payment_id
    provider_keys = [call.idempotency_key for call in provider.checkout_calls]
    assert provider_keys == [f"kaigo-{first.payment_id}", f"kaigo-{second.payment_id}"]
    assert len(set(provider_keys)) == 2
    assert all(re.fullmatch(r"[0-9A-Za-z+_.-]{1,64}", key) for key in provider_keys)


@pytest.mark.asyncio
async def test_stale_creating_checkout_is_recovered_with_same_provider_key(
    billing_db,
) -> None:
    _engine, factory = billing_db
    plan = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        stuck = PaymentAttempt(
            user_id=10,
            provider="fakepay",
            merchant_account_fingerprint="a" * 64,
            # Public idempotency keys historically allowed a colon. Existing
            # attempts must remain resumable while the provider header uses
            # the canonical validated key stored before its first dispatch.
            idempotency_key="legacy:checkout:key",
            plan_code=plan.code,
            plan_snapshot=plan.snapshot(),
            plan_fingerprint=plan.fingerprint(),
            amount_minor=plan.amount.amount_minor,
            currency=plan.amount.currency,
            status="creating",
            payload={},
            updated_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        database.add(stuck)
        await database.flush()
        stuck_id = stuck.id
        first_dispatched_at = datetime.now(UTC) - timedelta(minutes=1)
        stuck.payload = {
            "provider_idempotency_key": f"kaigo-{stuck_id}",
            "first_dispatched_at": first_dispatched_at.isoformat(),
            "provider_idempotency_expires_at": (
                first_dispatched_at + timedelta(hours=24)
            ).isoformat(),
        }
        stuck.updated_at = datetime.now(UTC) - timedelta(minutes=1)

    provider = FakeProvider()
    result = await BillingService(factory, provider).create_checkout(
        10, "starter_monthly", "legacy:checkout:key"
    )

    assert result.payment_id == stuck_id
    assert result.created is False
    assert provider.checkout_calls[0].idempotency_key == f"kaigo-{stuck_id}"


@pytest.mark.asyncio
async def test_legacy_ambiguous_attempt_without_provider_key_fails_closed(
    billing_db,
) -> None:
    _engine, factory = billing_db
    plan = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        attempt = PaymentAttempt(
            user_id=10,
            provider="fakepay",
            merchant_account_fingerprint="a" * 64,
            idempotency_key="legacy:ambiguous:key",
            plan_code=plan.code,
            plan_snapshot=plan.snapshot(),
            plan_fingerprint=plan.fingerprint(),
            amount_minor=plan.amount.amount_minor,
            currency=plan.amount.currency,
            status="failed",
            payload={},
        )
        database.add(attempt)

    provider = FakeProvider()
    with pytest.raises(BillingError, match="manual recovery"):
        await BillingService(factory, provider).create_checkout(
            10, "starter_monthly", "legacy:ambiguous:key"
        )

    assert provider.checkout_calls == []


@pytest.mark.asyncio
async def test_provider_post_is_blocked_at_or_after_idempotency_expiry(
    billing_db,
) -> None:
    _engine, factory = billing_db
    plan = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        attempt = PaymentAttempt(
            user_id=10,
            provider="fakepay",
            merchant_account_fingerprint="a" * 64,
            idempotency_key="expired-provider-window",
            plan_code=plan.code,
            plan_snapshot=plan.snapshot(),
            plan_fingerprint=plan.fingerprint(),
            amount_minor=plan.amount.amount_minor,
            currency=plan.amount.currency,
            status="dispatch_unknown",
            payload={},
        )
        database.add(attempt)
        await database.flush()
        first_dispatched_at = datetime.now(UTC) - timedelta(hours=24, seconds=1)
        attempt.payload = {
            "provider_idempotency_key": f"kaigo-{attempt.id}",
            "first_dispatched_at": first_dispatched_at.isoformat(),
            "provider_idempotency_expires_at": (
                first_dispatched_at + timedelta(hours=24)
            ).isoformat(),
        }

    provider = FakeProvider()
    with pytest.raises(BillingError, match="idempotency window expired"):
        await BillingService(factory, provider).create_checkout(
            10, "starter_monthly", "expired-provider-window"
        )

    assert provider.checkout_calls == []
    async with factory() as database:
        attempt = await database.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.idempotency_key == "expired-provider-window"
            )
        )
        assert attempt is not None
        assert attempt.status == "dispatch_unknown"


@pytest.mark.asyncio
async def test_provider_post_is_blocked_inside_dispatch_safety_margin(
    billing_db,
) -> None:
    _engine, factory = billing_db
    plan = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        attempt = PaymentAttempt(
            user_id=10,
            provider="fakepay",
            merchant_account_fingerprint="a" * 64,
            idempotency_key="provider-window-safety-margin",
            plan_code=plan.code,
            plan_snapshot=plan.snapshot(),
            plan_fingerprint=plan.fingerprint(),
            amount_minor=plan.amount.amount_minor,
            currency=plan.amount.currency,
            status="failed",
            payload={},
        )
        database.add(attempt)
        await database.flush()
        first_dispatched_at = datetime.now(UTC) - timedelta(hours=23, minutes=59)
        attempt.payload = {
            "provider_idempotency_key": f"kaigo-{attempt.id}",
            "first_dispatched_at": first_dispatched_at.isoformat(),
            "provider_idempotency_expires_at": (
                first_dispatched_at + timedelta(hours=24)
            ).isoformat(),
        }

    provider = FakeProvider()
    with pytest.raises(BillingError, match="idempotency window"):
        await BillingService(factory, provider).create_checkout(
            10, "starter_monthly", "provider-window-safety-margin"
        )

    assert provider.checkout_calls == []


@pytest.mark.asyncio
async def test_old_checkout_lease_cannot_clobber_recovered_payment(
    billing_db,
) -> None:
    _engine, factory = billing_db

    class LeaseProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__()
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
            self.checkout_calls.append(command)
            if len(self.checkout_calls) == 1:
                self.first_started.set()
                await self.release_first.wait()
                raise RuntimeError("old worker failed after losing its lease")
            return ProviderCheckout(
                provider_payment_id=f"pay-{command.metadata['payment_attempt_id']}",
                checkout_url=f"https://pay.example/{command.metadata['payment_attempt_id']}",
                status=PaymentStatus.PENDING,
                amount=command.amount,
                paid=False,
                metadata=command.metadata,
                test_mode=True,
            )

    provider = LeaseProvider()
    old_service = BillingService(factory, provider)
    new_service = BillingService(factory, provider)
    old_request = asyncio.create_task(
        old_service.create_checkout(10, "starter_monthly", "lease-fence-key")
    )
    await provider.first_started.wait()
    async with factory() as database, database.begin():
        attempt = await database.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.user_id == 10,
                PaymentAttempt.idempotency_key == "lease-fence-key",
            )
        )
        attempt.updated_at = datetime.now(UTC) - timedelta(minutes=1)
        attempt_id = attempt.id

    recovered = await new_service.resume_checkout(10, attempt_id)
    provider.release_first.set()
    with pytest.raises(RuntimeError, match="old worker"):
        await old_request

    assert recovered.payment_id == attempt_id
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, attempt_id)
        assert attempt.status == "pending"
        assert attempt.checkout_url == f"https://pay.example/{attempt_id}"
    assert len(provider.checkout_calls) == 2
    assert (
        provider.checkout_calls[0].idempotency_key
        == provider.checkout_calls[1].idempotency_key
    )


@pytest.mark.asyncio
async def test_failed_provider_call_retries_same_attempt_and_provider_key(
    billing_db,
) -> None:
    _engine, factory = billing_db

    class FlakyProvider(FakeProvider):
        async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
            self.checkout_calls.append(command)
            if len(self.checkout_calls) == 1:
                raise RuntimeError(
                    "connection dropped after provider may have accepted"
                )
            return ProviderCheckout(
                provider_payment_id=f"pay-{command.metadata['payment_attempt_id']}",
                checkout_url=f"https://pay.example/{command.metadata['payment_attempt_id']}",
                status=PaymentStatus.PENDING,
                amount=command.amount,
                paid=False,
                metadata=command.metadata,
                test_mode=True,
            )

    provider = FlakyProvider()
    service = BillingService(factory, provider)
    with pytest.raises(RuntimeError, match="connection dropped"):
        await service.create_checkout(10, "starter_monthly", "flaky-same-key")

    async with factory() as database:
        attempt = await database.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.idempotency_key == "flaky-same-key"
            )
        )
        assert attempt.payload["provider_idempotency_key"] == (f"kaigo-{attempt.id}")
        first_dispatched_at = datetime.fromisoformat(
            attempt.payload["first_dispatched_at"]
        )
        expires_at = datetime.fromisoformat(
            attempt.payload["provider_idempotency_expires_at"]
        )
        assert expires_at - first_dispatched_at == timedelta(hours=24)

    recovered = await service.create_checkout(10, "starter_monthly", "flaky-same-key")

    assert recovered.created is False
    assert len(provider.checkout_calls) == 2
    assert (
        provider.checkout_calls[0].idempotency_key
        == provider.checkout_calls[1].idempotency_key
    )
    assert provider.checkout_calls[0].metadata == provider.checkout_calls[1].metadata


@pytest.mark.asyncio
async def test_unknown_provider_outcome_blocks_different_public_key(
    billing_db,
) -> None:
    _engine, factory = billing_db

    class LostResponseProvider(FakeProvider):
        async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
            self.checkout_calls.append(command)
            raise RuntimeError("response lost after provider may have accepted payment")

    provider = LostResponseProvider()
    service = BillingService(factory, provider)
    with pytest.raises(RuntimeError, match="response lost"):
        await service.create_checkout(
            10, "starter_monthly", "ambiguous-first-public-key"
        )

    with pytest.raises(BillingError, match="unresolved payment attempt"):
        await service.create_checkout(10, "starter_monthly", "different-public-key")

    assert len(provider.checkout_calls) == 1
    async with factory() as database:
        attempt = await database.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.idempotency_key == "ambiguous-first-public-key"
            )
        )
        assert attempt.status == "dispatch_unknown"


@pytest.mark.asyncio
async def test_failed_checkout_cannot_retry_under_a_different_merchant_account(
    billing_db,
) -> None:
    _engine, factory = billing_db

    class LostResponseProvider(FakeProvider):
        async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
            self.checkout_calls.append(command)
            raise RuntimeError("response lost after merchant may have accepted payment")

    merchant_a = LostResponseProvider("a" * 64)
    with pytest.raises(RuntimeError, match="response lost"):
        await BillingService(factory, merchant_a).create_checkout(
            10, "starter_monthly", "merchant-bound-retry"
        )

    merchant_b = FakeProvider("b" * 64)
    with pytest.raises(
        BillingError,
        match="merchant account does not match",
    ):
        await BillingService(factory, merchant_b).create_checkout(
            10, "starter_monthly", "merchant-bound-retry"
        )

    assert merchant_b.checkout_calls == []
    async with factory() as database:
        attempt = await database.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.idempotency_key == "merchant-bound-retry"
            )
        )
        assert attempt.merchant_account_fingerprint == "a" * 64


@pytest.mark.asyncio
async def test_failed_checkout_retries_under_same_merchant_after_restart(
    billing_db,
) -> None:
    _engine, factory = billing_db

    class LostResponseProvider(FakeProvider):
        async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
            self.checkout_calls.append(command)
            raise RuntimeError("response lost")

    with pytest.raises(RuntimeError, match="response lost"):
        await BillingService(factory, LostResponseProvider("a" * 64)).create_checkout(
            10, "starter_monthly", "same-merchant-retry"
        )

    replacement = FakeProvider("a" * 64)
    result = await BillingService(factory, replacement).create_checkout(
        10, "starter_monthly", "same-merchant-retry"
    )

    assert result.created is False
    assert len(replacement.checkout_calls) == 1


@pytest.mark.asyncio
async def test_webhook_cannot_cross_a_known_merchant_account(billing_db) -> None:
    _engine, factory = billing_db
    merchant_a = FakeProvider("a" * 64)
    checkout = await BillingService(factory, merchant_a).create_checkout(
        10, "starter_monthly", "merchant-bound-webhook"
    )

    merchant_b = FakeProvider("b" * 64)
    with pytest.raises(BillingError, match="merchant account does not match"):
        await BillingService(factory, merchant_b).handle_notification(
            {"payment_id": f"pay-{checkout.payment_id}"}
        )

    assert merchant_b.verify_calls == []


@pytest.mark.asyncio
async def test_webhook_unknown_merchant_fails_before_provider_call(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider("a" * 64)
    checkout = await BillingService(factory, provider).create_checkout(
        10,
        "starter_monthly",
        "unknown-merchant-webhook",
    )
    async with factory() as database, database.begin():
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        assert attempt is not None
        attempt.merchant_account_fingerprint = "!" * 64

    with pytest.raises(BillingError, match="merchant account does not match"):
        await BillingService(factory, provider).handle_notification(
            {"payment_id": f"pay-{checkout.payment_id}"}
        )

    assert provider.verify_calls == []


@pytest.mark.asyncio
async def test_resume_uses_stored_plan_after_catalog_entry_is_removed(
    billing_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, factory = billing_db

    class FlakyProvider(FakeProvider):
        async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
            self.checkout_calls.append(command)
            if len(self.checkout_calls) == 1:
                raise RuntimeError("deploy boundary failure")
            return ProviderCheckout(
                provider_payment_id=f"pay-{command.metadata['payment_attempt_id']}",
                checkout_url=f"https://pay.example/{command.metadata['payment_attempt_id']}",
                status=PaymentStatus.PENDING,
                amount=command.amount,
                paid=False,
                metadata=command.metadata,
                test_mode=True,
            )

    provider = FlakyProvider()
    service = BillingService(factory, provider)
    with pytest.raises(RuntimeError, match="deploy boundary"):
        await service.create_checkout(10, "starter_monthly", "immutable-resume-key")
    async with factory() as database:
        attempt = await database.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.idempotency_key == "immutable-resume-key"
            )
        )
        attempt_id = attempt.id
        stored_snapshot = dict(attempt.plan_snapshot)
    monkeypatch.delitem(PLAN_CATALOG, "starter_monthly")

    result = await service.resume_checkout(10, attempt_id)

    assert result.payment_id == attempt_id
    assert result.amount_minor == stored_snapshot["amount_minor"]
    assert provider.checkout_calls[1].description == stored_snapshot["title"]
    assert provider.checkout_calls[1].amount == Money(
        stored_snapshot["amount_minor"], stored_snapshot["currency"]
    )


@pytest.mark.asyncio
async def test_concurrent_checkout_calls_provider_once(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    provider._gate.clear()
    service = BillingService(factory, provider)

    first = asyncio.create_task(
        service.create_checkout(10, "starter_monthly", "race-key")
    )
    await asyncio.sleep(0.05)
    second = asyncio.create_task(
        service.create_checkout(10, "starter_monthly", "race-key")
    )
    await asyncio.sleep(0.05)
    provider._gate.set()
    one, two = await asyncio.gather(first, second)

    assert one.payment_id == two.payment_id
    assert sorted((one.created, two.created)) == [False, True]
    assert len(provider.checkout_calls) == 1


@pytest.mark.asyncio
async def test_concurrent_checkout_across_service_instances_calls_provider_once(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    provider._gate.clear()
    first_service = BillingService(factory, provider)
    second_service = BillingService(factory, provider)

    first = asyncio.create_task(
        first_service.create_checkout(10, "starter_monthly", "cross-worker-key")
    )
    await asyncio.sleep(0.05)
    second = asyncio.create_task(
        second_service.create_checkout(10, "starter_monthly", "cross-worker-key")
    )
    await asyncio.sleep(0.05)
    provider._gate.set()
    one, two = await asyncio.gather(first, second)

    assert one.payment_id == two.payment_id
    assert len(provider.checkout_calls) == 1


@pytest.mark.asyncio
async def test_verified_webhook_fulfills_once_and_extends_subscription(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    checkout = await service.create_checkout(10, "starter_monthly", "webhook-key")
    payment_id = f"pay-{checkout.payment_id}"

    first = await service.handle_notification({"payment_id": payment_id})
    replay = await service.handle_notification({"payment_id": payment_id})

    assert first.processed is True
    assert replay.processed is False
    assert first.subscription_id == replay.subscription_id
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        subscription = (await database.execute(select(Subscription))).scalar_one()
        webhook = (await database.execute(select(PaymentWebhookEvent))).scalar_one()
        ledger = (await database.execute(select(UsageLedger))).scalar_one()
        assert attempt.status == "succeeded"
        assert subscription.status == "active"
        assert subscription.current_period_end > subscription.current_period_start
        assert webhook.provider_event_id == f"payment.succeeded:{payment_id}"
        assert webhook.payment_attempt_id == checkout.payment_id
        assert webhook.payload["source"] == "webhook"
        assert ledger.payment_attempt_id == checkout.payment_id
        assert ledger.bucket == "tokens"
        assert ledger.amount == 1_000_000
        assert ledger.idempotency_key == f"payment:{checkout.payment_id}:tokens"
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 1
    assert await UsageBalanceService(factory).token_balance(10) == 1_000_000
    assert len(provider.verify_calls) == 2


@pytest.mark.asyncio
async def test_usage_balance_includes_legacy_and_canonical_token_buckets(
    billing_db,
) -> None:
    _engine, factory = billing_db
    async with factory() as database, database.begin():
        database.add_all(
            [
                UsageLedger(
                    user_id=10,
                    bucket="generation_tokens",
                    entry_type="subscription.credit",
                    amount=125,
                    idempotency_key="legacy-balance-credit",
                    payload={},
                ),
                UsageLedger(
                    user_id=10,
                    bucket="tokens",
                    entry_type="subscription.credit",
                    amount=275,
                    idempotency_key="canonical-balance-credit",
                    payload={},
                ),
            ]
        )

    assert await UsageBalanceService(factory).token_balance(10) == 400


@pytest.mark.asyncio
async def test_succeeded_webhook_replay_is_accepted_after_subscription_ends(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    checkout = await service.create_checkout(10, "starter_monthly", "ended-replay-key")
    payload = {"payment_id": f"pay-{checkout.payment_id}"}
    first = await service.handle_notification(payload)
    async with factory() as database, database.begin():
        subscription = await database.get(Subscription, first.subscription_id)
        subscription.status = "expired"

    replay = await service.handle_notification(payload)

    assert replay.processed is False
    assert replay.payment_id == checkout.payment_id
    assert replay.subscription_id is None
    async with factory() as database:
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert (
            await database.scalar(select(func.count()).select_from(PaymentWebhookEvent))
            == 1
        )


@pytest.mark.asyncio
async def test_ledger_idempotency_is_namespaced_by_provider(billing_db) -> None:
    _engine, factory = billing_db
    plan = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        attempts = []
        for user_id, provider_name in ((10, "provider_one"), (11, "provider_two")):
            attempt = PaymentAttempt(
                user_id=user_id,
                provider=provider_name,
                merchant_account_fingerprint="a" * 64,
                provider_payment_id="shared-provider-payment-id",
                idempotency_key=f"provider-key-{user_id}",
                plan_code=plan.code,
                plan_snapshot=plan.snapshot(),
                plan_fingerprint=plan.fingerprint(),
                amount_minor=plan.amount.amount_minor,
                currency=plan.amount.currency,
                status="pending",
                checkout_url=f"https://pay.example/{user_id}",
                payload={},
            )
            database.add(attempt)
            attempts.append(attempt)
        await database.flush()

    for provider_name in ("provider_one", "provider_two"):
        provider = FakeProvider()
        provider.name = provider_name
        result = await BillingService(factory, provider).handle_notification(
            {"payment_id": "shared-provider-payment-id"}
        )
        assert result.processed is True

    async with factory() as database:
        keys = list(
            await database.scalars(
                select(UsageLedger.idempotency_key).order_by(
                    UsageLedger.idempotency_key
                )
            )
        )
    assert keys == sorted(
        [
            f"payment:{attempts[0].id}:tokens",
            f"payment:{attempts[1].id}:tokens",
        ]
    )


@pytest.mark.asyncio
async def test_concurrent_webhooks_fulfill_once(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    checkout = await service.create_checkout(10, "starter_monthly", "webhook-race")
    payload = {"payment_id": f"pay-{checkout.payment_id}"}

    results = await asyncio.gather(
        *[service.handle_notification(payload) for _ in range(5)]
    )

    assert sum(result.processed for result in results) == 1
    async with factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(Subscription)) == 1
        )
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 1
        event = (await database.execute(select(PaymentWebhookEvent))).scalar_one()
        assert event.payload["source"] == "webhook"


@pytest.mark.asyncio
async def test_verified_cancellation_releases_pending_checkout_idempotently(
    billing_db,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    checkout = await service.create_checkout(10, "starter_monthly", "cancel-key")
    payload = {
        "payment_id": f"pay-{checkout.payment_id}",
        "event": "payment.canceled",
    }

    first = await service.handle_notification(payload)
    replay = await service.handle_notification(payload)

    assert first.processed is True
    assert first.subscription_id is None
    assert replay.processed is False
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        event = (await database.execute(select(PaymentWebhookEvent))).scalar_one()
        assert attempt.status == "cancelled"
        assert event.provider_event_id == f"payment.canceled:pay-{checkout.payment_id}"
        assert (
            await database.scalar(select(func.count()).select_from(Subscription)) == 0
        )
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 0


@pytest.mark.asyncio
async def test_paid_checkout_uses_immutable_snapshot_after_catalog_change(
    billing_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    checkout = await service.create_checkout(10, "starter_monthly", "snapshot-key")
    monkeypatch.setitem(
        PLAN_CATALOG,
        "starter_monthly",
        BillingPlan(
            code="starter_monthly",
            title="Changed future plan",
            amount=Money(299_000, "RUB"),
            period_days=15,
            generation_tokens=10,
        ),
    )

    result = await service.handle_notification(
        {"payment_id": f"pay-{checkout.payment_id}"}
    )

    assert result.processed is True
    async with factory() as database:
        subscription = (await database.execute(select(Subscription))).scalar_one()
        ledger = (await database.execute(select(UsageLedger))).scalar_one()
        assert subscription.plan_snapshot["amount_minor"] == 200_000
        assert ledger.amount == 1_000_000


def test_subscription_period_is_utc_finite() -> None:
    assert datetime.now(UTC).tzinfo is not None


@pytest.mark.asyncio
async def test_billing_service_closes_provider(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    provider.closed = False

    async def close() -> None:
        provider.closed = True

    provider.aclose = close
    service = BillingService(factory, provider)
    await service.close()
    assert provider.closed is True


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def test_reconciliation_prioritizes_unscheduled_legacy_rows_on_postgresql() -> None:
    statement = PaymentReconciler._candidate_statement(
        now=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
        limit=100,
        provider_name="fakepay",
        merchant_account_fingerprint="a" * 64,
    )

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "next_reconcile_at ASC NULLS FIRST" in compiled


async def _checkout_due_for_reconciliation(
    factory,
    service: BillingService,
    *,
    idempotency_key: str,
    due_at: datetime,
    user_id: int = 10,
):
    checkout = await service.create_checkout(
        user_id,
        "starter_monthly",
        idempotency_key,
    )
    async with factory() as database, database.begin():
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        assert attempt is not None
        attempt.next_reconcile_at = due_at
    return checkout


@pytest.mark.asyncio
async def test_lost_webhook_is_reconciled_and_fulfilled_once(billing_db) -> None:
    _engine, factory = billing_db
    clock = FakeClock(datetime(2026, 7, 30, 12, 0, tzinfo=UTC))
    provider = FakeProvider(reconciliation_status=PaymentStatus.SUCCEEDED)
    service = BillingService(factory, provider)
    checkout = await _checkout_due_for_reconciliation(
        factory,
        service,
        idempotency_key="lost-webhook-reconciliation",
        due_at=clock(),
    )

    results = await PaymentReconciler(
        factory,
        provider,
        payment_service=service,
        clock=clock,
    ).run_once()

    assert results == {checkout.payment_id: "succeeded"}
    assert len(provider.get_calls) == 1
    async with factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(Subscription)) == 1
        )
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 1
        event = (await database.execute(select(PaymentWebhookEvent))).scalar_one()
        assert event.payload["source"] == "reconciliation"


@pytest.mark.asyncio
async def test_webhook_and_reconciler_race_share_one_fulfillment(billing_db) -> None:
    _engine, factory = billing_db
    clock = FakeClock(datetime(2026, 7, 30, 12, 0, tzinfo=UTC))
    provider = FakeProvider(reconciliation_status=PaymentStatus.SUCCEEDED)
    service = BillingService(factory, provider)
    checkout = await _checkout_due_for_reconciliation(
        factory,
        service,
        idempotency_key="webhook-reconciliation-race",
        due_at=clock(),
    )
    reconciler = PaymentReconciler(
        factory,
        provider,
        payment_service=service,
        clock=clock,
    )

    reconciliation, webhook = await asyncio.gather(
        reconciler.run_once(),
        service.handle_notification({"payment_id": f"pay-{checkout.payment_id}"}),
    )

    assert reconciliation[checkout.payment_id] == "succeeded"
    assert webhook.payment_id == checkout.payment_id
    async with factory() as database:
        assert (
            await database.scalar(select(func.count()).select_from(Subscription)) == 1
        )
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert (
            await database.scalar(select(func.count()).select_from(PaymentWebhookEvent))
            == 1
        )


@pytest.mark.asyncio
async def test_multiworker_reconciliation_gets_once_per_sixty_seconds(
    billing_db,
) -> None:
    _engine, factory = billing_db
    clock = FakeClock(datetime(2026, 7, 30, 12, 0, tzinfo=UTC))
    provider = FakeProvider(reconciliation_status=PaymentStatus.PENDING)
    service = BillingService(factory, provider)
    await _checkout_due_for_reconciliation(
        factory,
        service,
        idempotency_key="multiworker-reconciliation-cadence",
        due_at=clock(),
    )
    first = PaymentReconciler(
        factory,
        provider,
        payment_service=service,
        clock=clock,
    )
    second = PaymentReconciler(
        factory,
        provider,
        payment_service=service,
        clock=clock,
    )

    await asyncio.gather(first.run_once(), second.run_once())
    clock.advance(timedelta(seconds=59))
    await first.run_once()
    assert len(provider.get_calls) == 1

    clock.advance(timedelta(seconds=1))
    await second.run_once()
    assert len(provider.get_calls) == 2


@pytest.mark.asyncio
async def test_reconciler_claims_each_attempt_only_when_its_get_can_start(
    billing_db,
) -> None:
    _engine, factory = billing_db
    clock = FakeClock(datetime(2026, 7, 30, 12, 0, tzinfo=UTC))

    class GatedProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__(reconciliation_status=PaymentStatus.PENDING)
            self.first_get_started = asyncio.Event()
            self.release_first_get = asyncio.Event()

        async def get_payment(self, *args, **kwargs) -> ProviderPayment:
            if not self.get_calls:
                self.first_get_started.set()
                await self.release_first_get.wait()
            return await super().get_payment(*args, **kwargs)

    provider = GatedProvider()
    service = BillingService(factory, provider)
    await _checkout_due_for_reconciliation(
        factory,
        service,
        idempotency_key="claim-just-in-time-first",
        due_at=clock() - timedelta(seconds=1),
        user_id=10,
    )
    second = await _checkout_due_for_reconciliation(
        factory,
        service,
        idempotency_key="claim-just-in-time-second",
        due_at=clock(),
        user_id=11,
    )
    task = asyncio.create_task(
        PaymentReconciler(
            factory,
            provider,
            payment_service=service,
            clock=clock,
        ).run_once(limit=2)
    )

    await provider.first_get_started.wait()
    async with factory() as database:
        second_attempt = await database.get(PaymentAttempt, second.payment_id)
        assert second_attempt is not None
        assert second_attempt.reconcile_lease_token is None
    provider.release_first_get.set()
    await task

    assert len(provider.get_calls) == 2


@pytest.mark.parametrize("local_status", ["dispatch_unknown", "failed"])
@pytest.mark.asyncio
async def test_known_provider_payment_is_reconciled_regardless_of_attempt_age(
    billing_db,
    local_status: str,
) -> None:
    _engine, factory = billing_db
    clock = FakeClock(datetime(2026, 7, 30, 12, 0, tzinfo=UTC))
    provider = FakeProvider(reconciliation_status=PaymentStatus.PENDING)
    service = BillingService(factory, provider)
    checkout = await _checkout_due_for_reconciliation(
        factory,
        service,
        idempotency_key="old-known-provider-payment",
        due_at=clock(),
    )
    async with factory() as database, database.begin():
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        assert attempt is not None
        attempt.created_at = clock() - timedelta(days=90)
        attempt.first_dispatched_at = clock() - timedelta(days=90)
        attempt.provider_idempotency_expires_at = clock() - timedelta(days=89)
        attempt.status = local_status

    await PaymentReconciler(
        factory,
        provider,
        payment_service=service,
        clock=clock,
    ).run_once()

    assert [call[0] for call in provider.get_calls] == [f"pay-{checkout.payment_id}"]


@pytest.mark.asyncio
async def test_dispatch_unknown_without_provider_id_is_not_reposted_or_fetched(
    billing_db,
) -> None:
    _engine, factory = billing_db
    clock = FakeClock(datetime(2026, 7, 30, 12, 0, tzinfo=UTC))
    plan = PLAN_CATALOG["starter_monthly"]
    async with factory() as database, database.begin():
        database.add(
            PaymentAttempt(
                user_id=10,
                provider="fakepay",
                merchant_account_fingerprint="a" * 64,
                idempotency_key="dispatch-unknown-without-provider-id",
                plan_code=plan.code,
                plan_snapshot=plan.snapshot(),
                plan_fingerprint=plan.fingerprint(),
                amount_minor=plan.amount.amount_minor,
                currency=plan.amount.currency,
                status="dispatch_unknown",
                next_reconcile_at=clock(),
                payload={},
            )
        )
    provider = FakeProvider()
    service = BillingService(factory, provider)

    results = await PaymentReconciler(
        factory,
        provider,
        payment_service=service,
        clock=clock,
    ).run_once()

    assert results == {}
    assert provider.get_calls == []
    assert provider.checkout_calls == []


@pytest.mark.asyncio
async def test_reconciliation_merchant_mismatch_fails_before_provider_get(
    billing_db,
) -> None:
    _engine, factory = billing_db
    clock = FakeClock(datetime(2026, 7, 30, 12, 0, tzinfo=UTC))
    original_provider = FakeProvider("a" * 64)
    checkout = await _checkout_due_for_reconciliation(
        factory,
        BillingService(factory, original_provider),
        idempotency_key="reconciliation-merchant-mismatch",
        due_at=clock(),
    )
    replacement_provider = FakeProvider("b" * 64)
    replacement_service = BillingService(factory, replacement_provider)

    results = await PaymentReconciler(
        factory,
        replacement_provider,
        payment_service=replacement_service,
        clock=clock,
    ).run_once()

    assert results == {}
    assert replacement_provider.get_calls == []
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, checkout.payment_id)
        assert attempt is not None
        assert attempt.merchant_account_fingerprint == "a" * 64
