from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.db.base import Base
from app.saas import models as saas_models  # noqa: F401


EXPECTED_TABLES = {
    "user_identities",
    "auth_sessions",
    "oauth_states",
    "anonymous_drafts",
    "projects",
    "generation_runs",
    "generation_events",
    "generation_artifacts",
    "artifact_evidence",
    "model_calls",
    "usage_ledger",
    "trial_entitlements",
    "subscriptions",
    "payment_attempts",
    "payment_webhook_events",
    "publications",
    "publication_releases",
}


def _constraint_names(table_name: str, constraint_type: type) -> set[str]:
    table = Base.metadata.tables[table_name]
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, constraint_type) and constraint.name
    }


def test_saas_tables_are_registered() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables)


def test_idempotency_and_ledger_constraints_are_registered() -> None:
    assert "uq_identity_subject" in _constraint_names("user_identities", UniqueConstraint)
    assert "uq_run_event_sequence" in _constraint_names("generation_events", UniqueConstraint)
    assert "uq_payment_event" in _constraint_names("payment_webhook_events", UniqueConstraint)
    assert "ck_usage_ledger_nonzero" in _constraint_names("usage_ledger", CheckConstraint)


def test_oauth_only_users_may_have_no_password() -> None:
    assert Base.metadata.tables["users"].c.password_hash.nullable is True


def test_trial_cycles_and_model_call_mode_are_persisted() -> None:
    assert "reservation_epoch" in Base.metadata.tables["trial_entitlements"].c
    assert "mode" in Base.metadata.tables["model_calls"].c


def test_terminal_trial_settlement_fields_and_recovery_index_are_persisted() -> None:
    runs = Base.metadata.tables["generation_runs"]
    assert {"failure_category", "trial_settlement", "trial_settled_at"} <= set(runs.c.keys())
    assert "ix_generation_runs_unsettled_terminal" in {index.name for index in runs.indexes}


def test_publication_release_constraints_are_artifact_idempotent_and_referential() -> None:
    assert "uq_publication_release_artifact" in _constraint_names(
        "publication_releases", UniqueConstraint
    )
    assert "uq_publication_release_revision" not in _constraint_names(
        "publication_releases", UniqueConstraint
    )
    assert "uq_publication_release_membership" in _constraint_names(
        "publication_releases", UniqueConstraint
    )
    active_release = next(
        foreign_key
        for foreign_key in Base.metadata.tables["publications"].foreign_keys
        if foreign_key.parent.name == "active_release_id"
        and foreign_key.constraint.name == "fk_publications_active_release_id"
    )
    assert active_release.target_fullname == "publication_releases.id"
    assert active_release.ondelete == "SET NULL"
    assert active_release.deferrable is True
    assert active_release.initially == "DEFERRED"
    composite = next(
        constraint
        for constraint in Base.metadata.tables["publications"].constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_publications_active_release_membership"
    )
    assert tuple(element.parent.name for element in composite.elements) == (
        "id",
        "active_release_id",
    )
    assert tuple(element.target_fullname for element in composite.elements) == (
        "publication_releases.publication_id",
        "publication_releases.id",
    )
    assert composite.deferrable is True
    assert composite.initially == "DEFERRED"


def test_billing_constraints_and_payment_links_are_registered() -> None:
    assert (
        Base.metadata.tables["payment_attempts"]
        .c.merchant_account_fingerprint.nullable
        is False
    )
    assert "uq_payment_attempt_user_idempotency" in _constraint_names(
        "payment_attempts", UniqueConstraint
    )
    assert "uq_payment_attempt_provider_payment" in _constraint_names(
        "payment_attempts", UniqueConstraint
    )
    assert "ck_payment_attempt_positive_amount" in _constraint_names(
        "payment_attempts", CheckConstraint
    )
    assert "ck_subscription_finite_period" in _constraint_names(
        "subscriptions", CheckConstraint
    )
    assert "uq_subscriptions_one_active_user" in {
        index.name for index in Base.metadata.tables["subscriptions"].indexes
    }
    assert Base.metadata.tables["usage_ledger"].c.payment_attempt_id.foreign_keys
    assert Base.metadata.tables["payment_webhook_events"].c.payment_attempt_id.foreign_keys
    subscription_check = next(
        constraint
        for constraint in Base.metadata.tables["subscriptions"].constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_subscription_finite_period"
    )
    assert "status <> 'active'" in str(subscription_check.sqltext)
    assert "current_period_start IS NOT NULL" in str(subscription_check.sqltext)
