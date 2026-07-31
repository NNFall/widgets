from __future__ import annotations

import json
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.billing.catalog import PLAN_CATALOG
from app.billing.contracts import CheckoutCommand, RecurringPaymentCommand
from app.billing.payments import (
    BillingError,
    BillingService,
    _canonical_provider_request,
    _request_fingerprint,
)
from app.billing.runtime import create_billing_service
from app.billing.receipts import (
    BillingReceiptSettings,
    ReceiptCustomerUnavailable,
    build_payment_receipt,
    resolve_payment_receipt,
    verified_receipt_email,
)
from app.config import AppConfig
from app.db.base import Base
from app.db.models import Tenant, User
from app.saas.models import PaymentAttempt, UserIdentity
from tests.saas_cases.test_billing_service import FakeProvider


@pytest_asyncio.fixture
async def receipt_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'receipts.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database, database.begin():
        database.add(Tenant(id=1, name="Alpha", slug="alpha"))
        database.add_all(
            [
                User(id=10, tenant_id=1, email="Owner@Example.COM"),
                User(id=11, tenant_id=1, email="unverified@example.com"),
                User(id=12, tenant_id=1, email="account@example.com"),
            ]
        )
        database.add_all(
            [
                UserIdentity(
                    user_id=10,
                    provider="yandex",
                    provider_subject="receipt-owner",
                    email=" owner@example.com ",
                    email_verified=True,
                ),
                UserIdentity(
                    user_id=11,
                    provider="yandex",
                    provider_subject="receipt-unverified",
                    email="unverified@example.com",
                    email_verified=False,
                ),
                UserIdentity(
                    user_id=12,
                    provider="yandex",
                    provider_subject="receipt-mismatch",
                    email="different@example.com",
                    email_verified=True,
                ),
            ]
        )
    try:
        yield engine, factory
    finally:
        await engine.dispose()


def enabled_settings() -> BillingReceiptSettings:
    return BillingReceiptSettings(
        enabled=True,
        vat_code=1,
        payment_mode="full_payment",
        payment_subject="service",
        tax_system_code=1,
    )


def test_receipt_settings_are_fail_closed_and_frozen() -> None:
    with pytest.raises(ValueError, match="vat_code"):
        BillingReceiptSettings(enabled=True)
    with pytest.raises(ValueError, match="payment_mode"):
        BillingReceiptSettings(enabled=True, vat_code=1, payment_mode="invalid")


@pytest.mark.asyncio
async def test_billing_runtime_passes_validated_receipt_policy_to_service(
    receipt_db,
) -> None:
    _engine, factory = receipt_db
    provider = FakeProvider()
    config = AppConfig(
        database_url="sqlite+aiosqlite:///:memory:",
        public_base_url="https://kaigo.space",
        yookassa_shop_id="shop-123",
        yookassa_secret_key="secret",
        yookassa_receipts_enabled=True,
        yookassa_receipt_vat_code=1,
        yookassa_receipt_tax_system_code=1,
        yookassa_receipt_payment_subject="service",
        yookassa_receipt_payment_mode="full_payment",
    )

    service = create_billing_service(
        config,
        factory,
        provider_factory=lambda **_kwargs: provider,
    )

    assert service is not None
    assert service._receipt_settings == enabled_settings()

    settings = enabled_settings()
    with pytest.raises((AttributeError, TypeError)):
        settings.vat_code = 2  # type: ignore[misc]


@pytest.mark.asyncio
async def test_disabled_receipts_do_not_query_customer_email() -> None:
    class DatabaseThatMustNotBeUsed:
        async def scalar(self, _statement):
            raise AssertionError("disabled receipt policy queried the database")

    receipt = await resolve_payment_receipt(
        DatabaseThatMustNotBeUsed(),  # type: ignore[arg-type]
        user_id=10,
        plan=PLAN_CATALOG["starter_monthly"],
        settings=BillingReceiptSettings(),
    )

    assert receipt is None


@pytest.mark.asyncio
async def test_verified_receipt_email_requires_a_normalized_verified_match(
    receipt_db,
) -> None:
    _engine, factory = receipt_db
    async with factory() as database:
        assert await verified_receipt_email(database, user_id=10) == "owner@example.com"
        assert await verified_receipt_email(database, user_id=11) is None
        assert await verified_receipt_email(database, user_id=12) is None


def test_receipt_builder_creates_one_balanced_service_item() -> None:
    plan = PLAN_CATALOG["starter_monthly"]
    receipt = build_payment_receipt(
        customer_email=" OWNER@example.com ",
        plan=plan,
        settings=enabled_settings(),
    )

    assert receipt.customer_email == "owner@example.com"
    assert receipt.tax_system_code == 1
    assert len(receipt.items) == 1
    item = receipt.items[0]
    assert item.description == plan.title.strip()
    assert item.quantity == Decimal("1")
    assert item.amount == plan.amount
    assert item.vat_code == 1
    assert item.measure == "piece"
    assert item.payment_mode == "full_payment"
    assert item.payment_subject == "service"


@pytest.mark.asyncio
async def test_enabled_receipts_reject_unverified_email_before_attempt(
    receipt_db,
) -> None:
    _engine, factory = receipt_db
    provider = FakeProvider()
    service = BillingService(factory, provider, receipt_settings=enabled_settings())

    with pytest.raises(ReceiptCustomerUnavailable):
        await service.create_checkout(
            11,
            "starter_monthly",
            "receipt-unverified-email",
            auto_renew=True,
        )

    async with factory() as database:
        count = await database.scalar(select(func.count()).select_from(PaymentAttempt))
    assert count == 0
    assert provider.checkout_calls == []


@pytest.mark.asyncio
async def test_initial_checkout_stores_only_the_exact_receipt_fingerprint(
    receipt_db,
) -> None:
    _engine, factory = receipt_db
    provider = FakeProvider()
    service = BillingService(factory, provider, receipt_settings=enabled_settings())

    result = await service.create_checkout(
        10,
        "starter_monthly",
        "receipt-exact-initial",
        auto_renew=True,
    )

    command = provider.checkout_calls[0]
    assert command.receipt is not None
    assert command.receipt.customer_email == "owner@example.com"
    async with factory() as database:
        attempt = await database.get(PaymentAttempt, result.payment_id)
        assert attempt is not None
        assert attempt.request_fingerprint == _request_fingerprint(command)
        assert "owner@example.com" not in json.dumps(attempt.payload).lower()


def test_initial_and_renewal_commands_share_the_exact_receipt_document() -> None:
    plan = PLAN_CATALOG["starter_monthly"]
    receipt = build_payment_receipt(
        customer_email="owner@example.com",
        plan=plan,
        settings=enabled_settings(),
    )
    metadata = {"payment_attempt_id": "attempt-1", "user_id": "10"}
    initial = CheckoutCommand(
        idempotency_key="initial-receipt-key",
        amount=plan.amount,
        description=plan.title,
        metadata=metadata,
        save_payment_method=True,
        receipt=receipt,
    )
    renewal = RecurringPaymentCommand(
        idempotency_key="renewal-receipt-key",
        payment_method_id="saved_method_1",
        amount=plan.amount,
        description=plan.title,
        metadata=metadata,
        receipt=receipt,
    )

    initial_document = _canonical_provider_request(initial)
    renewal_document = _canonical_provider_request(renewal)
    assert initial_document["receipt"] == renewal_document["receipt"]
    assert initial_document["confirmation"] is True
    assert initial_document["save_payment_method"] is True
    assert renewal_document["confirmation"] is False
    assert renewal_document["payment_method_id"] == "saved_method_1"
    assert _request_fingerprint(initial) != _request_fingerprint(renewal)


@pytest.mark.asyncio
async def test_receipt_change_blocks_ambiguous_replay(receipt_db) -> None:
    _engine, factory = receipt_db

    class LostFirstResponseProvider(FakeProvider):
        async def create_checkout(self, command: CheckoutCommand):
            self.checkout_calls.append(command)
            if len(self.checkout_calls) == 1:
                raise RuntimeError("response lost after provider accepted request")
            return await super().create_checkout(command)

    provider = LostFirstResponseProvider()
    service = BillingService(factory, provider, receipt_settings=enabled_settings())
    with pytest.raises(RuntimeError, match="response lost"):
        await service.create_checkout(
            10,
            "starter_monthly",
            "receipt-changed-replay",
            auto_renew=True,
        )

    async with factory() as database, database.begin():
        user = await database.get(User, 10)
        identity = await database.scalar(
            select(UserIdentity).where(UserIdentity.user_id == 10)
        )
        assert user is not None and identity is not None
        user.email = "new-owner@example.com"
        identity.email = "new-owner@example.com"

    with pytest.raises(BillingError, match="stored provider request was modified"):
        await service.create_checkout(
            10,
            "starter_monthly",
            "receipt-changed-replay",
            auto_renew=True,
        )

    assert len(provider.checkout_calls) == 1
