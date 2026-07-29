from __future__ import annotations

import importlib

from app.saas.models import OAuthState


def test_funnel_migration_is_additive_after_billing_foundation() -> None:
    migration = importlib.import_module("migrations.versions.0010_funnel_events")
    source = open(migration.__file__, encoding="utf-8").read()

    assert migration.revision == "0010_funnel_events"
    assert migration.down_revision == "0009_billing_foundation"
    assert 'create_table(\n        "funnel_events"' in source
    assert "uq_funnel_events_event_key" in source
    assert "event_key" in source
    assert "event_type" in source
    assert "occurred_at" in source
    assert "oauth_state_id" in source
    assert "campaign_source" in source
    assert "campaign_medium" in source
    assert "campaign_name" in source
    assert "campaign_term" in source
    assert "campaign_content" in source

    forbidden_storage = (
        "source_url",
        '"url"',
        '"brief"',
        '"prompt"',
        '"email"',
        '"profile"',
        '"raw_ip"',
        '"ip_address"',
        '"payload"',
    )
    assert all(name not in source for name in forbidden_storage)


def test_funnel_migration_round_trips_all_additive_objects() -> None:
    migration = importlib.import_module("migrations.versions.0010_funnel_events")
    source = open(migration.__file__, encoding="utf-8").read()
    downgrade = source.split("def downgrade()", 1)[1]

    assert 'drop_table("funnel_events")' in downgrade
    assert 'drop_column("anonymous_drafts", "campaign")' in downgrade
    assert 'drop_column("oauth_states", "session_binding_digest")' in downgrade


def test_oauth_session_binding_invalidates_legacy_states_before_not_null() -> None:
    migration = importlib.import_module("migrations.versions.0010_funnel_events")
    source = open(migration.__file__, encoding="utf-8").read()
    upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]

    add_position = upgrade.index('"session_binding_digest"')
    invalidate_position = upgrade.index("UPDATE oauth_states")
    not_null_position = upgrade.index('alter_column(\n        "oauth_states"')

    assert add_position < invalidate_position < not_null_position
    assert "'" + ("!" * 64) + "'" in upgrade
    assert "nullable=False" in upgrade[not_null_position:]
    assert OAuthState.__table__.c.session_binding_digest.nullable is False
