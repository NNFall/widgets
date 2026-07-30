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
    "generation_stage_attempts",
    "generation_forensic_manifests",
    "generation_forensic_access_logs",
    "usage_ledger",
    "trial_entitlements",
    "billing_payment_methods",
    "subscriptions",
    "payment_attempts",
    "payment_webhook_events",
    "publications",
    "publication_releases",
    "project_versions",
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


def test_generation_event_forensic_columns_are_additive_and_nullable() -> None:
    events = Base.metadata.tables["generation_events"]
    assert events.c.registry_version.nullable
    assert events.c.public_payload.nullable
    assert events.c.forensic_ref.nullable


def test_generation_forensic_tables_enforce_manifest_and_access_contracts() -> None:
    manifest = Base.metadata.tables["generation_forensic_manifests"]
    access = Base.metadata.tables["generation_forensic_access_logs"]

    assert manifest.c.run_id.nullable is False
    assert manifest.c.storage_key.nullable is False
    assert manifest.c.expires_at.type.timezone is True
    assert access.c.actor_email.nullable is False
    assert access.c.created_at.type.timezone is True
    assert {
        "ck_generation_forensic_manifest_state",
        "ck_generation_forensic_manifest_schema_version_positive",
        "ck_generation_forensic_manifest_counts_nonnegative",
    } <= _constraint_names("generation_forensic_manifests", CheckConstraint)
    assert "ck_generation_forensic_access_action" in _constraint_names(
        "generation_forensic_access_logs", CheckConstraint
    )


def test_model_call_lineage_schema_and_cross_run_membership_are_registered() -> None:
    calls = Base.metadata.tables["model_calls"]
    attempts = Base.metadata.tables["generation_stage_attempts"]

    assert {
        "stage_attempt_id",
        "logical_invocation_id",
        "operation",
        "semantic_attempt",
        "candidate_id",
        "persona",
        "fallback_index",
        "actual_provider",
        "actual_model",
        "cache_read_tokens",
        "cache_write_tokens",
        "cost_state",
    } <= set(calls.c.keys())
    assert calls.c.cost_microusd.nullable is True
    assert {
        "uq_generation_stage_attempt_run_stage_ordinal",
        "uq_generation_stage_attempt_id_run",
    } <= _constraint_names("generation_stage_attempts", UniqueConstraint)
    assert "uq_model_call_logical_fallback" in _constraint_names(
        "model_calls", UniqueConstraint
    )
    composite = next(
        constraint
        for constraint in calls.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_model_calls_stage_attempt_run"
    )
    assert tuple(element.parent.name for element in composite.elements) == (
        "stage_attempt_id",
        "run_id",
    )
    assert tuple(element.target_fullname for element in composite.elements) == (
        "generation_stage_attempts.id",
        "generation_stage_attempts.run_id",
    )
    assert attempts.c.ordinal.nullable is False
    assert attempts.c.started_at.type.timezone is True


def test_terminal_trial_settlement_fields_and_recovery_index_are_persisted() -> None:
    runs = Base.metadata.tables["generation_runs"]
    assert {"failure_category", "trial_settlement", "trial_settled_at"} <= set(runs.c.keys())
    assert "ix_generation_runs_unsettled_terminal" in {index.name for index in runs.indexes}


def _foreign_key(table_name: str, constraint_name: str) -> ForeignKeyConstraint:
    return next(
        constraint
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == constraint_name
    )


def test_project_version_schema_is_exact_and_project_scoped() -> None:
    versions = Base.metadata.tables["project_versions"]

    assert set(versions.c.keys()) == {
        "id",
        "project_id",
        "ordinal",
        "run_id",
        "artifact_id",
        "parent_version_id",
        "kind",
        "change_request",
        "idempotency_key",
        "created_at",
    }
    assert {
        "uq_project_version_ordinal",
        "uq_project_version_membership",
        "uq_project_version_restore_idempotency",
    } <= _constraint_names("project_versions", UniqueConstraint)
    assert {
        "ck_project_version_ordinal_positive",
        "ck_project_version_kind",
        "ck_project_version_shape",
    } <= _constraint_names("project_versions", CheckConstraint)
    assert {
        "fk_project_versions_run_membership",
        "fk_project_versions_artifact_membership",
        "fk_project_versions_parent_membership",
    } <= _constraint_names("project_versions", ForeignKeyConstraint)

    shape = next(
        constraint
        for constraint in versions.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_project_version_shape"
    )
    shape_sql = " ".join(str(shape.sqltext).split())
    assert "kind = 'initial'" in shape_sql
    assert "parent_version_id IS NULL" in shape_sql
    assert "change_request IS NULL" in shape_sql
    assert "kind = 'refinement'" in shape_sql
    assert "parent_version_id IS NOT NULL" in shape_sql
    assert "length(trim(change_request)) BETWEEN 1 AND 2000" in shape_sql
    assert "kind = 'restore'" in shape_sql

    run_membership = _foreign_key(
        "project_versions", "fk_project_versions_run_membership"
    )
    assert tuple(element.parent.name for element in run_membership.elements) == (
        "project_id",
        "run_id",
    )
    assert tuple(element.target_fullname for element in run_membership.elements) == (
        "generation_runs.project_id",
        "generation_runs.id",
    )
    artifact_membership = _foreign_key(
        "project_versions", "fk_project_versions_artifact_membership"
    )
    assert tuple(element.parent.name for element in artifact_membership.elements) == (
        "run_id",
        "artifact_id",
    )
    assert tuple(
        element.target_fullname for element in artifact_membership.elements
    ) == (
        "generation_artifacts.run_id",
        "generation_artifacts.id",
    )
    parent_membership = _foreign_key(
        "project_versions", "fk_project_versions_parent_membership"
    )
    assert tuple(element.parent.name for element in parent_membership.elements) == (
        "project_id",
        "parent_version_id",
    )
    assert tuple(element.target_fullname for element in parent_membership.elements) == (
        "project_versions.project_id",
        "project_versions.id",
    )


def test_project_version_pointers_and_membership_keys_are_deferred() -> None:
    projects = Base.metadata.tables["projects"]
    runs = Base.metadata.tables["generation_runs"]
    releases = Base.metadata.tables["publication_releases"]

    assert projects.c.active_version_id.nullable is True
    assert runs.c.source_version_id.nullable is True
    assert runs.c.change_request.nullable is True
    assert runs.c.change_request.type.length == 2000
    assert releases.c.project_version_id.nullable is True
    assert next(iter(releases.c.project_version_id.foreign_keys)).target_fullname == (
        "project_versions.id"
    )
    assert "uq_generation_run_membership" in _constraint_names(
        "generation_runs", UniqueConstraint
    )
    assert "uq_generation_artifact_membership" in _constraint_names(
        "generation_artifacts", UniqueConstraint
    )

    active_membership = _foreign_key(
        "projects", "fk_projects_active_version_membership"
    )
    assert tuple(element.parent.name for element in active_membership.elements) == (
        "id",
        "active_version_id",
    )
    assert tuple(
        element.target_fullname for element in active_membership.elements
    ) == (
        "project_versions.project_id",
        "project_versions.id",
    )
    assert active_membership.deferrable is True
    assert active_membership.initially == "DEFERRED"

    source_membership = _foreign_key(
        "generation_runs", "fk_generation_runs_source_version_membership"
    )
    assert tuple(element.parent.name for element in source_membership.elements) == (
        "project_id",
        "source_version_id",
    )
    assert tuple(
        element.target_fullname for element in source_membership.elements
    ) == (
        "project_versions.project_id",
        "project_versions.id",
    )
    assert source_membership.deferrable is True
    assert source_membership.initially == "DEFERRED"


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
