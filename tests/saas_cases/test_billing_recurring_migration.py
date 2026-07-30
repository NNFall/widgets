from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.base import Base
from app.saas import models as saas_models  # noqa: F401


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_UNKNOWN_MERCHANT = "!" * 64


def _constraint_names(table_name: str, constraint_type: type) -> set[str]:
    return {
        constraint.name
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, constraint_type) and constraint.name
    }


def test_recurring_migration_follows_reserved_forensics_revision() -> None:
    migration = importlib.import_module(
        "migrations.versions.0015_yookassa_recurring_foundation"
    )

    assert migration.revision == "0015_yookassa_recurring_foundation"
    assert migration.down_revision == "0014_generation_forensics"


def test_recurring_migration_uses_cross_dialect_timestamp_defaults() -> None:
    source = (
        PROJECT_ROOT
        / "migrations"
        / "versions"
        / "0015_yookassa_recurring_foundation.py"
    ).read_text(encoding="utf-8")

    assert 'server_default=sa.text("now()")' not in source
    assert source.count("server_default=sa.func.now()") >= 2


def test_recurring_orm_registers_payment_methods_and_fail_closed_constraints() -> None:
    assert "billing_payment_methods" in Base.metadata.tables

    payment_methods = Base.metadata.tables["billing_payment_methods"]
    subscriptions = Base.metadata.tables["subscriptions"]
    attempts = Base.metadata.tables["payment_attempts"]
    webhooks = Base.metadata.tables["payment_webhook_events"]

    assert {
        "user_id",
        "provider",
        "merchant_account_fingerprint",
        "provider_payment_method_id",
        "source_payment_attempt_id",
        "status",
        "consent_version",
        "consented_at",
        "saved_at",
        "disabled_at",
        "created_at",
        "updated_at",
    } <= set(payment_methods.c.keys())
    assert {
        "merchant_account_fingerprint",
        "payment_method_id",
        "auto_renew",
        "next_renewal_at",
        "auto_renew_enabled_at",
        "auto_renew_disabled_at",
    } <= set(subscriptions.c.keys())
    assert {
        "purpose",
        "subscription_id",
        "payment_method_id",
        "billing_period_start",
        "billing_period_end",
        "renewal_attempt_number",
        "retry_of_payment_attempt_id",
        "retry_of_renewal_attempt_number",
        "next_dispatch_at",
        "auto_renew_requested",
        "consent_version",
        "consented_at",
        "save_payment_method_requested",
        "request_fingerprint",
        "first_dispatched_at",
        "provider_idempotency_expires_at",
        "last_reconciled_at",
        "next_reconcile_at",
        "reconcile_lease_token",
        "reconcile_lease_expires_at",
    } <= set(attempts.c.keys())
    assert "merchant_account_fingerprint" in webhooks.c

    assert "uq_billing_payment_method_provider_identity" in _constraint_names(
        "billing_payment_methods", UniqueConstraint
    )
    assert "ck_billing_payment_method_status" in _constraint_names(
        "billing_payment_methods", CheckConstraint
    )
    assert "ck_subscription_auto_renew_ready" in _constraint_names(
        "subscriptions", CheckConstraint
    )
    assert "uq_payment_attempt_retry_target" in _constraint_names(
        "payment_attempts", UniqueConstraint
    )
    assert "fk_payment_attempts_retry_target" in _constraint_names(
        "payment_attempts", ForeignKeyConstraint
    )
    assert {
        "ck_payment_attempt_purpose",
        "ck_payment_attempt_renewal_period",
        "ck_payment_attempt_auto_renew_consent",
        "ck_payment_attempt_reconcile_lease",
        "ck_payment_attempt_idempotency_window",
    } <= _constraint_names("payment_attempts", CheckConstraint)
    assert "uq_payment_attempt_renewal_sequence" in {
        index.name for index in attempts.indexes
    }
    assert all(
        next(iter(attempts.c[column].foreign_keys)).use_alter
        for column in ("subscription_id", "payment_method_id")
    )


def test_recurring_orm_legacy_defaults_are_safe() -> None:
    subscriptions = Base.metadata.tables["subscriptions"]
    attempts = Base.metadata.tables["payment_attempts"]

    assert subscriptions.c.auto_renew.nullable is False
    assert str(subscriptions.c.auto_renew.server_default.arg).lower() == "false"
    assert attempts.c.purpose.nullable is False
    assert str(attempts.c.purpose.server_default.arg).strip("'\"") == "initial"
    assert attempts.c.auto_renew_requested.nullable is False
    assert str(attempts.c.auto_renew_requested.server_default.arg).lower() == "false"


async def _revision(engine: AsyncEngine) -> str | None:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: MigrationContext.configure(sync).get_current_revision()
        )


async def _columns(engine: AsyncEngine, table_name: str) -> set[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: {
                column["name"] for column in inspect(sync).get_columns(table_name)
            }
        )


async def _table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync: set(inspect(sync).get_table_names())
        )


async def _expect_integrity_error(
    engine: AsyncEngine,
    statement: str,
    parameters: dict[str, object],
) -> None:
    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(text(statement), parameters)


@pytest.mark.asyncio
async def test_sqlite_recurring_migration_defaults_and_bucket_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'recurring.db'}"
    engine = create_async_engine(database_url)
    ledger_id = uuid4().hex
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "CREATE TABLE alembic_version ("
                    "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO alembic_version (version_num) "
                    "VALUES ('0014_generation_forensics')"
                )
            )
            await connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
            await connection.execute(
                text(
                    "CREATE TABLE payment_attempts ("
                    "id CHAR(32) PRIMARY KEY, provider_payment_id VARCHAR(255), "
                    "status VARCHAR(32) NOT NULL, created_at DATETIME NOT NULL, "
                    "merchant_account_fingerprint VARCHAR(64) NOT NULL)"
                )
            )
            await connection.execute(
                text("CREATE TABLE subscriptions (id CHAR(32) PRIMARY KEY)")
            )
            await connection.execute(
                text(
                    "CREATE TABLE payment_webhook_events ("
                    "id CHAR(32) PRIMARY KEY, payment_attempt_id CHAR(32))"
                )
            )
            await connection.execute(
                text(
                    "CREATE TABLE usage_ledger ("
                    "id CHAR(32) PRIMARY KEY, entry_type VARCHAR(64) NOT NULL, "
                    "bucket VARCHAR(64) NOT NULL, payload JSON NOT NULL)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id) VALUES (1);"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO usage_ledger (id,entry_type,bucket,payload) "
                    "VALUES (:id,'subscription.credit','generation_tokens','{}')"
                ),
                {"id": ledger_id},
            )
    finally:
        await engine.dispose()

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    await asyncio.to_thread(command.upgrade, config, "0015_yookassa_recurring_foundation")

    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            method_id = uuid4().hex
            await connection.execute(
                text(
                    "INSERT INTO billing_payment_methods "
                    "(id,user_id,provider,merchant_account_fingerprint,"
                    "provider_payment_method_id,status,consent_version,"
                    "consented_at,saved_at) VALUES "
                    "(:id,1,'yookassa',:merchant,'opaque','active','v1',"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {"id": method_id, "merchant": "a" * 64},
            )
            timestamps = (
                await connection.execute(
                    text(
                        "SELECT created_at,updated_at FROM billing_payment_methods "
                        "WHERE id=:id"
                    ),
                    {"id": method_id},
                )
            ).one()
            assert timestamps.created_at is not None
            assert timestamps.updated_at is not None
            normalized = (
                await connection.execute(
                    text("SELECT bucket,payload FROM usage_ledger WHERE id=:id"),
                    {"id": ledger_id},
                )
            ).one()
            assert normalized.bucket == "tokens"
            normalized_payload = json.loads(normalized.payload)
            assert normalized_payload["_kaigo_0015_original_bucket"] == (
                "generation_tokens"
            )
    finally:
        await engine.dispose()

    await asyncio.to_thread(command.downgrade, config, "0014_generation_forensics")
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            restored = (
                await connection.execute(
                    text("SELECT bucket,payload FROM usage_ledger WHERE id=:id"),
                    {"id": ledger_id},
                )
            ).one()
            assert restored.bucket == "generation_tokens"
            assert "_kaigo_0015_original_bucket" not in json.loads(restored.payload)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_recurring_migration_round_trip_and_constraints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_recurring_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    target_engine: AsyncEngine | None = None
    created = False

    user_id = 1
    subscription_id = uuid4()
    initial_attempt_id = uuid4()
    ambiguous_attempt_id = uuid4()
    dispatch_unknown_attempt_id = uuid4()
    linked_webhook_id = uuid4()
    orphan_webhook_id = uuid4()
    merchant_fingerprint = "a" * 64
    method_id = uuid4()
    period_start = datetime(2026, 8, 1, tzinfo=UTC)
    period_end = period_start + timedelta(days=30)

    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        monkeypatch.setenv("DATABASE_URL", rendered)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        await asyncio.to_thread(
            command.upgrade, config, "0014_generation_forensics"
        )
        target_engine = create_async_engine(rendered)

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id, name, slug) "
                    "VALUES (1, 'Recurring', 'recurring')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, role) "
                    "VALUES (:id, 1, 'recurring@example.com', 'tenant_admin')"
                ),
                {"id": user_id},
            )
            common_attempt = {
                "user_id": user_id,
                "merchant": merchant_fingerprint,
                "plan": json.dumps(
                    {
                        "code": "starter_monthly",
                        "title": "Starter",
                        "amount_minor": 199000,
                        "currency": "RUB",
                        "period_days": 30,
                        "generation_tokens": 1000000,
                    }
                ),
            }
            await connection.execute(
                text(
                    "INSERT INTO payment_attempts "
                    "(id,user_id,provider,merchant_account_fingerprint,"
                    "provider_payment_id,idempotency_key,plan_code,plan_snapshot,"
                    "plan_fingerprint,amount_minor,currency,status,checkout_url,payload) "
                    "VALUES "
                    "(CAST(:known AS UUID),:user_id,'yookassa',:merchant,"
                    "'payment-known','legacy-known','starter_monthly',CAST(:plan AS JSON),"
                    ":merchant,199000,'RUB','pending','https://example.test/pay',"
                    "CAST('{}' AS JSON)),"
                    "(CAST(:ambiguous AS UUID),:user_id,'yookassa',:merchant,NULL,"
                    "'legacy-ambiguous','starter_monthly',CAST(:plan AS JSON),"
                    ":merchant,199000,'RUB','failed',NULL,CAST('{}' AS JSON)),"
                    "(CAST(:dispatch_unknown AS UUID),:user_id,'yookassa',:merchant,NULL,"
                    "'legacy-dispatch-unknown','starter_monthly',CAST(:plan AS JSON),"
                    ":merchant,199000,'RUB','dispatch_unknown',NULL,CAST('{}' AS JSON))"
                ),
                {
                    **common_attempt,
                    "known": str(initial_attempt_id),
                    "ambiguous": str(ambiguous_attempt_id),
                    "dispatch_unknown": str(dispatch_unknown_attempt_id),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO subscriptions "
                    "(id,user_id,provider,payment_attempt_id,plan_code,plan_snapshot,"
                    "plan_fingerprint,status,current_period_start,current_period_end) "
                    "VALUES (CAST(:id AS UUID),:user_id,'yookassa',"
                    "CAST(:attempt AS UUID),'starter_monthly',CAST(:plan AS JSON),"
                    ":merchant,'active',:period_start,:period_end)"
                ),
                {
                    "id": str(subscription_id),
                    "user_id": user_id,
                    "attempt": str(initial_attempt_id),
                    "plan": common_attempt["plan"],
                    "merchant": merchant_fingerprint,
                    "period_start": period_start,
                    "period_end": period_end,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO usage_ledger "
                    "(id,user_id,payment_attempt_id,bucket,entry_type,amount,"
                    "idempotency_key,payload) VALUES "
                    "(CAST(:credit AS UUID),:user_id,CAST(:attempt AS UUID),"
                    "'generation_tokens','subscription.credit',100,'credit',CAST('{}' AS JSON)),"
                    "(CAST(:usage AS UUID),:user_id,NULL,'tokens','model.usage',-10,"
                    "'usage',CAST('{}' AS JSON)),"
                    "(CAST(:preexisting_credit AS UUID),:user_id,NULL,'tokens',"
                    "'subscription.credit',50,'preexisting-token-credit',"
                    "CAST('{}' AS JSON)),"
                    "(CAST(:reserve AS UUID),:user_id,NULL,'trial_reserved','trial.reserve',1,"
                    "'reserve',CAST('{}' AS JSON))"
                ),
                {
                    "credit": str(uuid4()),
                    "usage": str(uuid4()),
                    "preexisting_credit": str(uuid4()),
                    "reserve": str(uuid4()),
                    "user_id": user_id,
                    "attempt": str(initial_attempt_id),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO payment_webhook_events "
                    "(id,provider,provider_event_id,payment_attempt_id,payload) VALUES "
                    "(CAST(:linked AS UUID),'yookassa','linked',CAST(:attempt AS UUID),"
                    "CAST('{}' AS JSON)),"
                    "(CAST(:orphan AS UUID),'yookassa','orphan',NULL,CAST('{}' AS JSON))"
                ),
                {
                    "linked": str(linked_webhook_id),
                    "orphan": str(orphan_webhook_id),
                    "attempt": str(initial_attempt_id),
                },
            )

        await asyncio.to_thread(command.upgrade, config, "head")
        assert await _revision(target_engine) == "0015_yookassa_recurring_foundation"

        async with target_engine.connect() as connection:
            subscription = (
                await connection.execute(
                    text(
                        "SELECT auto_renew,payment_method_id,next_renewal_at,"
                        "merchant_account_fingerprint FROM subscriptions WHERE id=:id"
                    ),
                    {"id": subscription_id},
                )
            ).one()
            assert tuple(subscription) == (False, None, None, None)

            ledger = (
                await connection.execute(
                    text(
                        "SELECT idempotency_key,bucket FROM usage_ledger "
                        "ORDER BY idempotency_key"
                    )
                )
            ).all()
            assert [tuple(row) for row in ledger] == [
                ("credit", "tokens"),
                ("preexisting-token-credit", "tokens"),
                ("reserve", "trial_reserved"),
                ("usage", "tokens"),
            ]

            ambiguous = (
                await connection.execute(
                    text(
                        "SELECT purpose,auto_renew_requested,first_dispatched_at,"
                        "provider_idempotency_expires_at FROM payment_attempts "
                        "WHERE id=:id"
                    ),
                    {"id": ambiguous_attempt_id},
                )
            ).one()
            assert ambiguous.purpose == "initial"
            assert ambiguous.auto_renew_requested is False
            assert ambiguous.first_dispatched_at is not None
            assert (
                ambiguous.provider_idempotency_expires_at
                - ambiguous.first_dispatched_at
            ) == timedelta(hours=24)

            dispatch_unknown = (
                await connection.execute(
                    text(
                        "SELECT first_dispatched_at,provider_idempotency_expires_at "
                        "FROM payment_attempts WHERE id=:id"
                    ),
                    {"id": dispatch_unknown_attempt_id},
                )
            ).one()
            assert dispatch_unknown.first_dispatched_at is not None
            assert (
                dispatch_unknown.provider_idempotency_expires_at
                - dispatch_unknown.first_dispatched_at
            ) == timedelta(hours=24)

            webhooks = (
                await connection.execute(
                    text(
                        "SELECT provider_event_id,merchant_account_fingerprint "
                        "FROM payment_webhook_events ORDER BY provider_event_id"
                    )
                )
            ).all()
            assert [tuple(row) for row in webhooks] == [
                ("linked", merchant_fingerprint),
                ("orphan", LEGACY_UNKNOWN_MERCHANT),
            ]
            assert (
                await connection.scalar(text("SELECT count(*) FROM billing_payment_methods"))
                == 0
            )

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO usage_ledger "
                    "(id,user_id,payment_attempt_id,bucket,entry_type,amount,"
                    "idempotency_key,payload) VALUES "
                    "(CAST(:id AS UUID),:user_id,NULL,'tokens',"
                    "'subscription.credit',25,'new-token-credit',CAST('{}' AS JSON))"
                ),
                {"id": str(uuid4()), "user_id": user_id},
            )

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO billing_payment_methods "
                    "(id,user_id,provider,merchant_account_fingerprint,"
                    "provider_payment_method_id,source_payment_attempt_id,status,"
                    "consent_version,consented_at,saved_at) VALUES "
                    "(CAST(:id AS UUID),:user_id,'yookassa',:merchant,'method-opaque',"
                    "CAST(:source AS UUID),'active','kaigo-recurring-v1',now(),now())"
                ),
                {
                    "id": str(method_id),
                    "user_id": user_id,
                    "merchant": merchant_fingerprint,
                    "source": str(initial_attempt_id),
                },
            )
            await connection.execute(
                text(
                    "UPDATE subscriptions SET merchant_account_fingerprint=:merchant,"
                    "payment_method_id=CAST(:method AS UUID),auto_renew=true,"
                    "next_renewal_at=current_period_end,auto_renew_enabled_at=now() "
                    "WHERE id=CAST(:id AS UUID)"
                ),
                {
                    "merchant": merchant_fingerprint,
                    "method": str(method_id),
                    "id": str(subscription_id),
                },
            )

        await _expect_integrity_error(
            target_engine,
            "UPDATE subscriptions SET payment_method_id=NULL WHERE id=CAST(:id AS UUID)",
            {"id": str(subscription_id)},
        )
        await _expect_integrity_error(
            target_engine,
            "INSERT INTO billing_payment_methods "
            "(id,user_id,provider,merchant_account_fingerprint,"
            "provider_payment_method_id,status,consent_version,consented_at,saved_at) "
            "VALUES (CAST(:id AS UUID),:user_id,'yookassa',:merchant,'method-opaque',"
            "'active','kaigo-recurring-v1',now(),now())",
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "merchant": merchant_fingerprint,
            },
        )

        insert_attempt = (
            "INSERT INTO payment_attempts "
            "(id,user_id,provider,merchant_account_fingerprint,idempotency_key,"
            "plan_code,plan_snapshot,plan_fingerprint,amount_minor,currency,status,payload,"
            "purpose,subscription_id,payment_method_id,billing_period_start,"
            "billing_period_end,renewal_attempt_number,retry_of_payment_attempt_id,"
            "retry_of_renewal_attempt_number,next_dispatch_at,auto_renew_requested,"
            "consent_version,consented_at,"
            "save_payment_method_requested,request_fingerprint,first_dispatched_at,"
            "provider_idempotency_expires_at,reconcile_lease_token,"
            "reconcile_lease_expires_at) VALUES "
            "(CAST(:id AS UUID),:user_id,'yookassa',:merchant,:idem,'starter_monthly',"
            "CAST(:plan AS JSON),:merchant,199000,'RUB','creating',CAST('{}' AS JSON),"
            ":purpose,CAST(:subscription AS UUID),CAST(:method AS UUID),:period_start,"
            ":period_end,:number,CAST(:retry AS UUID),:retry_number,:next_dispatch,"
            ":auto_renew,"
            ":consent_version,:consented_at,:save_method,:request_fingerprint,"
            ":first_dispatched,:idempotency_expires,:lease_token,:lease_expires)"
        )
        base = {
            "user_id": user_id,
            "merchant": merchant_fingerprint,
            "plan": json.dumps({"code": "starter_monthly"}),
            "purpose": "renewal",
            "subscription": str(subscription_id),
            "method": str(method_id),
            "period_start": period_end,
            "period_end": period_end + timedelta(days=30),
            "number": 1,
            "retry": None,
            "retry_number": None,
            "next_dispatch": None,
            "auto_renew": True,
            "consent_version": "kaigo-recurring-v1",
            "consented_at": period_start,
            "save_method": False,
            "request_fingerprint": "b" * 64,
            "first_dispatched": None,
            "idempotency_expires": None,
            "lease_token": None,
            "lease_expires": None,
        }

        invalid_cases = {
            "renewal_without_subscription": {"subscription": None},
            "renewal_without_period": {"period_start": None, "period_end": None},
            "renewal_without_period_end": {"period_end": None},
            "renewal_without_attempt_number": {"number": None},
            "renewal_attempt_number_three": {"number": 3},
            "retry_without_primary_attempt": {
                "number": 2,
                "retry": None,
                "retry_number": None,
                "next_dispatch": period_end,
            },
            "retry_to_unrelated_initial_attempt": {
                "number": 2,
                "retry": str(initial_attempt_id),
                "retry_number": 1,
                "next_dispatch": period_end,
            },
            "initial_auto_renew_without_consent": {
                "purpose": "initial",
                "subscription": None,
                "method": None,
                "period_start": None,
                "period_end": None,
                "number": None,
                "auto_renew": True,
                "consent_version": None,
                "consented_at": None,
                "save_method": None,
            },
            "save_method_without_auto_renew_or_consent": {
                "purpose": "initial",
                "subscription": None,
                "method": None,
                "period_start": None,
                "period_end": None,
                "number": None,
                "retry": None,
                "retry_number": None,
                "auto_renew": False,
                "consent_version": None,
                "consented_at": None,
                "save_method": True,
            },
            "renewal_without_consent_lineage": {
                "consent_version": None,
                "consented_at": None,
            },
            "renewal_without_auto_renew_intent": {"auto_renew": False},
            "period_end_before_start": {"period_end": period_end},
            "half_reconcile_lease": {"lease_token": "lease-only"},
            "invalid_idempotency_window": {
                "first_dispatched": period_end,
                "idempotency_expires": period_end,
            },
            "half_idempotency_window": {
                "first_dispatched": period_end,
                "idempotency_expires": None,
            },
        }
        for case, changes in invalid_cases.items():
            params = {
                **base,
                **changes,
                "id": str(uuid4()),
                "idem": f"invalid-{case}",
            }
            await _expect_integrity_error(target_engine, insert_attempt, params)

        valid_primary = uuid4()
        async with target_engine.begin() as connection:
            await connection.execute(
                text(insert_attempt),
                {
                    **base,
                    "id": str(valid_primary),
                    "idem": "renewal-primary",
                },
            )
        await _expect_integrity_error(
            target_engine,
            insert_attempt,
            {
                **base,
                "id": str(uuid4()),
                "idem": "renewal-primary-duplicate",
            },
        )
        await _expect_integrity_error(
            target_engine,
            insert_attempt,
            {
                **base,
                "id": str(uuid4()),
                "idem": "renewal-retry-mismatched-period-end",
                "period_end": base["period_end"] + timedelta(days=1),
                "number": 2,
                "retry": str(valid_primary),
                "retry_number": 1,
                "next_dispatch": period_end + timedelta(hours=1),
            },
        )

        async with target_engine.begin() as connection:
            await connection.execute(
                text(insert_attempt),
                {
                    **base,
                    "id": str(uuid4()),
                    "idem": "renewal-retry",
                    "number": 2,
                    "retry": str(valid_primary),
                    "retry_number": 1,
                    "next_dispatch": period_end + timedelta(hours=1),
                },
            )

        await asyncio.to_thread(
            command.downgrade, config, "0014_generation_forensics"
        )
        assert await _revision(target_engine) == "0014_generation_forensics"
        assert "billing_payment_methods" not in await _table_names(target_engine)
        assert "auto_renew" not in await _columns(target_engine, "subscriptions")
        assert "purpose" not in await _columns(target_engine, "payment_attempts")
        assert "merchant_account_fingerprint" not in await _columns(
            target_engine, "payment_webhook_events"
        )
        async with target_engine.connect() as connection:
            credit_buckets = (
                await connection.execute(
                    text(
                        "SELECT idempotency_key,bucket,payload FROM usage_ledger "
                        "WHERE entry_type='subscription.credit' "
                        "ORDER BY idempotency_key"
                    )
                )
            ).all()
            model_bucket = await connection.scalar(
                text("SELECT bucket FROM usage_ledger WHERE entry_type='model.usage'")
            )
        assert [(row.idempotency_key, row.bucket) for row in credit_buckets] == [
            ("credit", "generation_tokens"),
            ("new-token-credit", "tokens"),
            ("preexisting-token-credit", "tokens"),
        ]
        assert all("_kaigo_0015_original_bucket" not in row.payload for row in credit_buckets)
        assert model_bucket == "tokens"
    finally:
        if target_engine is not None:
            await target_engine.dispose()
        if created:
            async with admin_engine.connect() as connection:
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=:name AND pid <> pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                await connection.execute(text(f'DROP DATABASE "{database_name}"'))
        await admin_engine.dispose()
