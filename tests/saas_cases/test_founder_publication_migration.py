from __future__ import annotations

import importlib
from pathlib import Path

from sqlalchemy import inspect

from app.db.base import Base
from app.saas import models as saas_models  # noqa: F401


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
        "kind",
        "message",
        "rating",
        "testimonial_allowed",
    } <= {column.key for column in contact.columns}
    assert "founder_grant_id" in usage.c
