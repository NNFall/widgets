from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import importlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_billing_migration_is_after_publication_and_enforces_core_invariants() -> None:
    migration = importlib.import_module("migrations.versions.0009_billing_foundation")
    assert migration.down_revision == "0008_publication_releases"
    source = open(migration.__file__, encoding="utf-8").read()

    assert "uq_payment_attempt_user_idempotency" in source
    assert "uq_payment_attempt_provider_payment" in source
    assert "ck_payment_attempt_positive_amount" in source
    assert "ck_subscription_finite_period" in source
    assert "uq_subscriptions_one_active_user" in source
    assert "payment_attempt_id" in source
    assert "plan_snapshot" in source
    assert "plan_fingerprint" in source
    assert "status = 'active'" in source
    assert "status <> 'active'" in source
    assert "legacy_migrated" in source
    assert "generation_tokens" in source
    assert "sha256" in source


def test_billing_migration_downgrade_removes_new_constraints_before_columns() -> None:
    migration = importlib.import_module("migrations.versions.0009_billing_foundation")
    source = open(migration.__file__, encoding="utf-8").read()
    downgrade = source.split("def downgrade()", 1)[1]

    assert downgrade.index("uq_subscriptions_one_active_user") < downgrade.index(
        'drop_column("subscriptions", "current_period_start")'
    )
    assert downgrade.index("fk_usage_ledger_payment_attempt") < downgrade.index(
        'drop_column("usage_ledger", "payment_attempt_id")'
    )


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured")
async def test_postgres_billing_migration_and_concurrent_fulfillment(monkeypatch) -> None:
    from app.billing.catalog import PLAN_CATALOG, BillingPlan
    from app.billing.payments import BillingService
    from app.db.models import Tenant, User
    from app.saas.models import (
        PaymentAttempt,
        PaymentWebhookEvent,
        Subscription,
        UsageLedger,
    )
    from tests.saas_cases.test_billing_service import FakeProvider

    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_billing_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False), isolation_level="AUTOCOMMIT"
    )
    target_engine = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        monkeypatch.setenv("DATABASE_URL", rendered)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        await asyncio.to_thread(command.upgrade, config, "0008_publication_releases")
        target_engine = create_async_engine(rendered)
        factory = async_sessionmaker(target_engine, expire_on_commit=False)
        async with factory() as database, database.begin():
            database.add(Tenant(id=1, name="Billing", slug="billing"))
            database.add(User(id=1, tenant_id=1, email="billing@example.com"))
            database.add(User(id=2, tenant_id=1, email="legacy@example.com"))
        legacy_id = uuid4()
        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO payment_attempts "
                    "(id,user_id,provider,provider_payment_id,idempotency_key,"
                    "amount_minor,currency,status,checkout_url,payload) VALUES "
                    "(:id,2,'fakepay','legacy-pay-1','legacy-before-0009',"
                    "199000,'rub','pending','https://pay.example/legacy-pay-1',"
                    "CAST('{}' AS json))"
                ),
                {"id": legacy_id},
            )
        await asyncio.to_thread(command.upgrade, config, "head")

        provider = FakeProvider()
        async with factory() as database:
            legacy = await database.get(PaymentAttempt, legacy_id)
            legacy_plan = BillingPlan.from_snapshot(legacy.plan_snapshot)
            assert legacy.plan_code == "legacy_migrated"
            assert legacy.currency == "RUB"
            assert legacy.plan_fingerprint == legacy_plan.fingerprint()
            assert legacy_plan.generation_tokens == 1_000_000
        legacy_result = await BillingService(factory, provider).handle_notification(
            {"payment_id": "legacy-pay-1"}
        )
        assert legacy_result.processed is True
        async with factory() as database:
            legacy_subscription = await database.get(
                Subscription, legacy_result.subscription_id
            )
            assert legacy_subscription.user_id == 2
            legacy_credit = await database.scalar(
                select(UsageLedger).where(UsageLedger.user_id == 2)
            )
            assert legacy_credit.amount == 1_000_000

        plan = PLAN_CATALOG["starter_monthly"]
        winner_session = factory()
        winner_transaction = await winner_session.begin()
        winner = PaymentAttempt(
            user_id=1,
            provider=provider.name,
            idempotency_key="postgres-insert-race",
            plan_code=plan.code,
            plan_snapshot=plan.snapshot(),
            plan_fingerprint=plan.fingerprint(),
            amount_minor=plan.amount.amount_minor,
            currency=plan.amount.currency,
            status="creating",
            payload={},
        )
        winner_session.add(winner)
        await winner_session.flush()
        winner_id = winner.id
        racing_service = BillingService(factory, provider)
        losing_request = asyncio.create_task(
            racing_service.create_checkout(
                1, "starter_monthly", "postgres-insert-race"
            )
        )
        await asyncio.sleep(0.2)
        assert not losing_request.done()
        await winner_transaction.commit()
        await winner_session.close()
        await asyncio.sleep(0.1)
        async with factory() as database, database.begin():
            persisted = await database.get(PaymentAttempt, winner_id)
            persisted.provider_payment_id = f"pay-{winner_id}"
            persisted.checkout_url = f"https://pay.example/{winner_id}"
            persisted.status = "pending"
        recovered = await losing_request
        assert recovered.payment_id == winner_id
        assert recovered.created is False
        assert provider.checkout_calls == []

        checkout_service = BillingService(factory, provider)
        checkout = await checkout_service.create_checkout(
            1, "starter_monthly", "postgres-checkout"
        )
        payload = {"payment_id": f"pay-{checkout.payment_id}"}
        first_service = BillingService(factory, provider)
        second_service = BillingService(factory, provider)
        results = await asyncio.gather(
            first_service.handle_notification(payload),
            second_service.handle_notification(payload),
        )
        assert sum(result.processed for result in results) == 1

        async with factory() as database:
            assert await database.scalar(select(func.count()).select_from(Subscription)) == 2
            assert await database.scalar(select(func.count()).select_from(UsageLedger)) == 2
            assert await database.scalar(
                select(func.count()).select_from(PaymentWebhookEvent)
            ) == 2
        with pytest.raises(IntegrityError):
            async with factory() as database, database.begin():
                database.add(
                    Subscription(
                        user_id=1,
                        provider="test",
                        plan_code="duplicate",
                        status="active",
                        current_period_start=datetime.now(UTC),
                        current_period_end=datetime.now(UTC) + timedelta(days=1),
                    )
                )

        async with target_engine.connect() as connection:
            revision = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
        assert revision == "0009_billing_foundation"

        await asyncio.to_thread(command.downgrade, config, "0008_publication_releases")
        async with target_engine.connect() as connection:
            revision = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
        assert revision == "0008_publication_releases"
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        if created:
            async with admin_engine.connect() as connection:
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid<>pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                await connection.execute(text(f'DROP DATABASE "{database_name}"'))
        await admin_engine.dispose()
