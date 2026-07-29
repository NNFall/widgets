from __future__ import annotations

import importlib


def test_pattern_registry_migration_follows_worker_readiness() -> None:
    migration = importlib.import_module("migrations.versions.0013_pattern_registry")

    assert migration.revision == "0013_pattern_registry"
    assert migration.down_revision == "0012_worker_service_readiness"
