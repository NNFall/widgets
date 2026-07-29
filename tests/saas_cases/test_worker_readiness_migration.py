from __future__ import annotations

import importlib


def test_worker_readiness_migration_follows_payment_merchant_account() -> None:
    migration = importlib.import_module(
        "migrations.versions.0012_worker_service_readiness"
    )

    assert migration.revision == "0012_worker_service_readiness"
    assert migration.down_revision == "0011_payment_merchant_account"
