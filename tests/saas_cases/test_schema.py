from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

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
