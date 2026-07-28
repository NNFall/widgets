from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentStatus,
    ProviderCheckout,
    ProviderNotification,
    ProviderPayment,
)
from app.billing.catalog import PLAN_CATALOG, BillingPlan
from app.billing.payments import (
    BillingService,
    CheckoutIdempotencyConflict,
)
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import (
    PaymentAttempt,
    PaymentWebhookEvent,
    Subscription,
    UsageLedger,
)


class FakeProvider:
    name = "fakepay"

    def __init__(self) -> None:
        self.checkout_calls: list[CheckoutCommand] = []
        self.verify_calls: list[tuple[ProviderNotification, Money | None, dict | None]] = []
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
    assert provider.checkout_calls[0].amount == Money(199_000, "RUB")
    assert provider.checkout_calls[0].metadata["user_id"] == "10"
    async with factory() as database:
        attempt = (await database.execute(select(PaymentAttempt))).scalar_one()
        assert attempt.plan_code == "starter_monthly"
        assert attempt.plan_snapshot["amount_minor"] == 199_000
        assert len(attempt.plan_fingerprint) == 64


@pytest.mark.asyncio
async def test_checkout_same_key_different_plan_conflicts(billing_db) -> None:
    _engine, factory = billing_db
    service = BillingService(factory, FakeProvider())
    await service.create_checkout(10, "starter_monthly", "same-key")

    with pytest.raises(CheckoutIdempotencyConflict):
        await service.create_checkout(10, "pro_monthly", "same-key")


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
    assert provider_keys == [f"kaigo:{first.payment_id}", f"kaigo:{second.payment_id}"]
    assert len(set(provider_keys)) == 2
    assert all(len(key) <= 64 for key in provider_keys)


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
            idempotency_key="stale-checkout-key",
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

    provider = FakeProvider()
    result = await BillingService(factory, provider).create_checkout(
        10, "starter_monthly", "stale-checkout-key"
    )

    assert result.payment_id == stuck_id
    assert result.created is False
    assert provider.checkout_calls[0].idempotency_key == f"kaigo:{stuck_id}"


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
    assert provider.checkout_calls[0].idempotency_key == provider.checkout_calls[1].idempotency_key


@pytest.mark.asyncio
async def test_failed_provider_call_retries_same_attempt_and_provider_key(
    billing_db,
) -> None:
    _engine, factory = billing_db

    class FlakyProvider(FakeProvider):
        async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
            self.checkout_calls.append(command)
            if len(self.checkout_calls) == 1:
                raise RuntimeError("connection dropped after provider may have accepted")
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

    recovered = await service.create_checkout(
        10, "starter_monthly", "flaky-same-key"
    )

    assert recovered.created is False
    assert len(provider.checkout_calls) == 2
    assert provider.checkout_calls[0].idempotency_key == provider.checkout_calls[1].idempotency_key
    assert provider.checkout_calls[0].metadata == provider.checkout_calls[1].metadata


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
async def test_verified_webhook_fulfills_once_and_extends_subscription(billing_db) -> None:
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
        assert ledger.payment_attempt_id == checkout.payment_id
        assert ledger.bucket == "generation_tokens"
        assert ledger.amount == 1_000_000
        assert ledger.idempotency_key == (
            f"fakepay:payment.succeeded:{payment_id}:generation_tokens"
        )
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 1
    assert len(provider.verify_calls) == 2


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
        assert await database.scalar(select(func.count()).select_from(PaymentWebhookEvent)) == 1


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
    assert keys == [
        "provider_one:payment.succeeded:shared-provider-payment-id:generation_tokens",
        "provider_two:payment.succeeded:shared-provider-payment-id:generation_tokens",
    ]


@pytest.mark.asyncio
async def test_concurrent_webhooks_fulfill_once(billing_db) -> None:
    _engine, factory = billing_db
    provider = FakeProvider()
    service = BillingService(factory, provider)
    checkout = await service.create_checkout(10, "starter_monthly", "webhook-race")
    payload = {"payment_id": f"pay-{checkout.payment_id}"}

    results = await asyncio.gather(*[service.handle_notification(payload) for _ in range(5)])

    assert sum(result.processed for result in results) == 1
    async with factory() as database:
        assert await database.scalar(select(func.count()).select_from(Subscription)) == 1
        assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 1
        assert await database.scalar(select(func.count()).select_from(PaymentWebhookEvent)) == 1


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
        assert await database.scalar(select(func.count()).select_from(Subscription)) == 0
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
        assert subscription.plan_snapshot["amount_minor"] == 199_000
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
