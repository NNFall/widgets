from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
from app.saas import models as saas_models  # noqa: F401


POSTGRES_URL = os.getenv("KAIGO_TEST_POSTGRES_URL")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_founder_publication_migration_identity_and_schema_contract() -> None:
    migration = importlib.import_module(
        "migrations.versions.0019_founder_publication_funnel"
    )
    source = Path(migration.__file__).read_text(encoding="utf-8")

    assert migration.revision == "0019_founder_publication_funnel"
    assert migration.down_revision == "0018_stage_aware_pattern_library"
    assert "founder_access_grants" in source
    assert "customer_contact_requests" in source
    assert "founder_grant_id" in source
    assert 'sa.Column("idempotency_key", sa.String(length=128), nullable=False)' in source
    assert (
        'sa.UniqueConstraint("user_id", "idempotency_key", '
        'name="uq_customer_contact_request_user_idempotency")'
    ) in source
    assert 'server_default=sa.text("now()")' not in source
    assert source.count("server_default=sa.func.now()") == 2

    founder = inspect(saas_models.FounderAccessGrant)
    contact = inspect(saas_models.CustomerContactRequest)
    usage = Base.metadata.tables["usage_ledger"]
    assert {
        "user_id",
        "project_id",
        "subscription_id",
        "source_origin",
        "position",
        "feedback_state",
        "starts_at",
        "ends_at",
    } <= {column.key for column in founder.columns}
    assert {
        "user_id",
        "project_id",
        "subscription_id",
        "founder_grant_id",
        "idempotency_key",
        "kind",
        "message",
        "rating",
        "testimonial_allowed",
    } <= {column.key for column in contact.columns}
    assert contact.columns.idempotency_key.type.length == 128
    assert contact.columns.idempotency_key.nullable is False
    assert any(
        constraint.name == "uq_customer_contact_request_user_idempotency"
        and {column.name for column in constraint.columns}
        == {"user_id", "idempotency_key"}
        for constraint in contact.local_table.constraints
    )
    assert "founder_grant_id" in usage.c


@pytest.mark.asyncio
@pytest.mark.postgres
@pytest.mark.skipif(
    not POSTGRES_URL, reason="KAIGO_TEST_POSTGRES_URL is not configured"
)
async def test_postgres_founder_publication_migration_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = make_url(POSTGRES_URL)
    if source_url.drivername == "postgresql":
        source_url = source_url.set(drivername="postgresql+asyncpg")
    database_name = f"kaigo_founder_publication_{uuid4().hex}"
    admin_url = source_url.set(database="postgres")
    target_url = source_url.set(database=database_name)
    rendered = target_url.render_as_string(hide_password=False)
    admin_engine = create_async_engine(
        admin_url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
    )
    target_engine = None
    created = False
    try:
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        monkeypatch.setenv("DATABASE_URL", rendered)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        await asyncio.to_thread(
            command.upgrade, config, "0018_stage_aware_pattern_library"
        )
        target_engine = create_async_engine(rendered)

        async with target_engine.connect() as connection:
            tables_before = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
        assert "customer_contact_requests" not in tables_before

        await asyncio.to_thread(
            command.upgrade, config, "0019_founder_publication_funnel"
        )
        async with target_engine.connect() as connection:
            columns = await connection.run_sync(
                lambda sync: {
                    column["name"]: column
                    for column in inspect(sync).get_columns(
                        "customer_contact_requests"
                    )
                }
            )
            unique_constraints = await connection.run_sync(
                lambda sync: inspect(sync).get_unique_constraints(
                    "customer_contact_requests"
                )
            )
            revision = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
        assert revision == "0019_founder_publication_funnel"
        assert columns["idempotency_key"]["nullable"] is False
        assert columns["idempotency_key"]["type"].length == 128
        assert any(
            constraint["name"] == "uq_customer_contact_request_user_idempotency"
            and set(constraint["column_names"]) == {"user_id", "idempotency_key"}
            for constraint in unique_constraints
        )

        async with target_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO tenants (id,name,slug) "
                    "VALUES (901,'Founder migration','founder-migration')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO users (id,tenant_id,email,role) "
                    "VALUES (901,901,'founder-migration@example.com','tenant_admin')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO customer_contact_requests "
                    "(id,user_id,idempotency_key,kind,message,testimonial_allowed) "
                    "VALUES (CAST(:id AS UUID),901,'contact-migration-key-0001',"
                    "'support','Migration contact request',FALSE)"
                ),
                {"id": str(uuid4())},
            )
        with pytest.raises(IntegrityError):
            async with target_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO customer_contact_requests "
                        "(id,user_id,idempotency_key,kind,message,testimonial_allowed) "
                        "VALUES (CAST(:id AS UUID),901,'contact-migration-key-0001',"
                        "'support','Duplicate contact request',FALSE)"
                    ),
                    {"id": str(uuid4())},
                )

        await asyncio.to_thread(
            command.downgrade, config, "0018_stage_aware_pattern_library"
        )
        async with target_engine.connect() as connection:
            tables_after = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
            usage_columns = await connection.run_sync(
                lambda sync: {
                    column["name"]
                    for column in inspect(sync).get_columns("usage_ledger")
                }
            )
            revision = await connection.run_sync(
                lambda sync: MigrationContext.configure(sync).get_current_revision()
            )
        assert revision == "0018_stage_aware_pattern_library"
        assert "customer_contact_requests" not in tables_after
        assert "founder_access_grants" not in tables_after
        assert "founder_grant_id" not in usage_columns
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
