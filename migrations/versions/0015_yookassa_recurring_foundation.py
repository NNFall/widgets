"""Add recurring billing persistence without enabling renewal dispatch.

Revision ID: 0015_yookassa_recurring_foundation
Revises: 0014_generation_forensics
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_yookassa_recurring_foundation"
down_revision = "0014_generation_forensics"
branch_labels = None
depends_on = None


_LEGACY_UNKNOWN_MERCHANT = "!" * 64
_USAGE_BUCKET_MARKER = "_kaigo_0015_original_bucket"


def _legacy_idempotency_backfill() -> None:
    bind = op.get_bind()
    expires_expression = (
        "datetime(created_at, '+24 hours')"
        if bind.dialect.name == "sqlite"
        else "created_at + interval '24 hours'"
    )
    op.execute(
        sa.text(
            "UPDATE payment_attempts "
            "SET purpose = 'initial', "
            "auto_renew_requested = false, "
            "save_payment_method_requested = NULL, "
            "first_dispatched_at = CASE "
            "WHEN provider_payment_id IS NULL "
            "AND status IN ('creating', 'failed', 'dispatch_unknown') "
            "THEN created_at ELSE first_dispatched_at END, "
            "provider_idempotency_expires_at = CASE "
            "WHEN provider_payment_id IS NULL "
            "AND status IN ('creating', 'failed', 'dispatch_unknown') "
            f"THEN {expires_expression} "
            "ELSE provider_idempotency_expires_at END"
        )
    )


def _normalize_legacy_credit_bucket() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(
            sa.text(
                "UPDATE usage_ledger SET bucket = 'tokens', "
                "payload = json_set(payload, "
                f"'$.{_USAGE_BUCKET_MARKER}', 'generation_tokens') "
                "WHERE entry_type = 'subscription.credit' "
                "AND bucket = 'generation_tokens'"
            )
        )
        return
    op.execute(
        sa.text(
            "UPDATE usage_ledger SET bucket = 'tokens', "
            "payload = (payload::jsonb || "
            f"jsonb_build_object('{_USAGE_BUCKET_MARKER}', "
            "'generation_tokens'))::json "
            "WHERE entry_type = 'subscription.credit' "
            "AND bucket = 'generation_tokens'"
        )
    )


def _restore_legacy_credit_bucket() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(
            sa.text(
                "UPDATE usage_ledger SET bucket = 'generation_tokens', "
                "payload = json_remove(payload, "
                f"'$.{_USAGE_BUCKET_MARKER}') "
                "WHERE entry_type = 'subscription.credit' "
                "AND bucket = 'tokens' AND json_extract(payload, "
                f"'$.{_USAGE_BUCKET_MARKER}') = 'generation_tokens'"
            )
        )
        return
    op.execute(
        sa.text(
            "UPDATE usage_ledger SET bucket = 'generation_tokens', "
            f"payload = (payload::jsonb - '{_USAGE_BUCKET_MARKER}')::json "
            "WHERE entry_type = 'subscription.credit' AND bucket = 'tokens' "
            f"AND payload::jsonb ->> '{_USAGE_BUCKET_MARKER}' = "
            "'generation_tokens'"
        )
    )


def upgrade() -> None:
    # The reserved revision name is longer than the historical Alembic
    # VARCHAR(32). Widen it transactionally before Alembic records this step.
    with op.batch_alter_table("alembic_version") as batch:
        batch.alter_column(
            "version_num",
            existing_type=sa.String(32),
            type_=sa.String(64),
            nullable=False,
        )

    op.create_table(
        "billing_payment_methods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("merchant_account_fingerprint", sa.String(64), nullable=False),
        sa.Column("provider_payment_method_id", sa.String(255), nullable=False),
        sa.Column("source_payment_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("consent_version", sa.String(64), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_billing_payment_methods_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_payment_attempt_id"],
            ["payment_attempts.id"],
            name="fk_billing_payment_methods_source_attempt",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "merchant_account_fingerprint",
            "provider_payment_method_id",
            name="uq_billing_payment_method_provider_identity",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'invalid')",
            name="ck_billing_payment_method_status",
        ),
    )
    op.create_index(
        "ix_billing_payment_methods_user_id",
        "billing_payment_methods",
        ["user_id"],
    )

    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(sa.Column("merchant_account_fingerprint", sa.String(64)))
        batch.add_column(sa.Column("payment_method_id", sa.Uuid()))
        batch.add_column(
            sa.Column(
                "auto_renew",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("next_renewal_at", sa.DateTime(timezone=True))
        )
        batch.add_column(
            sa.Column("auto_renew_enabled_at", sa.DateTime(timezone=True))
        )
        batch.add_column(
            sa.Column("auto_renew_disabled_at", sa.DateTime(timezone=True))
        )
        batch.create_foreign_key(
            "fk_subscriptions_payment_method",
            "billing_payment_methods",
            ["payment_method_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_subscription_auto_renew_ready",
            "NOT auto_renew OR (payment_method_id IS NOT NULL "
            "AND merchant_account_fingerprint IS NOT NULL "
            "AND next_renewal_at IS NOT NULL)",
        )
    op.execute(
        sa.text(
            "UPDATE subscriptions SET auto_renew = false, "
            "payment_method_id = NULL, next_renewal_at = NULL, "
            "auto_renew_enabled_at = NULL, auto_renew_disabled_at = NULL"
        )
    )

    with op.batch_alter_table("payment_attempts") as batch:
        batch.add_column(
            sa.Column(
                "purpose",
                sa.String(16),
                nullable=False,
                server_default=sa.text("'initial'"),
            )
        )
        batch.add_column(sa.Column("subscription_id", sa.Uuid()))
        batch.add_column(sa.Column("payment_method_id", sa.Uuid()))
        batch.add_column(
            sa.Column("billing_period_start", sa.DateTime(timezone=True))
        )
        batch.add_column(
            sa.Column("billing_period_end", sa.DateTime(timezone=True))
        )
        batch.add_column(sa.Column("renewal_attempt_number", sa.SmallInteger()))
        batch.add_column(sa.Column("retry_of_payment_attempt_id", sa.Uuid()))
        batch.add_column(
            sa.Column("retry_of_renewal_attempt_number", sa.SmallInteger())
        )
        batch.add_column(sa.Column("next_dispatch_at", sa.DateTime(timezone=True)))
        batch.add_column(
            sa.Column(
                "auto_renew_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(sa.Column("consent_version", sa.String(64)))
        batch.add_column(sa.Column("consented_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("save_payment_method_requested", sa.Boolean()))
        batch.add_column(sa.Column("request_fingerprint", sa.String(64)))
        batch.add_column(
            sa.Column("first_dispatched_at", sa.DateTime(timezone=True))
        )
        batch.add_column(
            sa.Column(
                "provider_idempotency_expires_at", sa.DateTime(timezone=True)
            )
        )
        batch.add_column(
            sa.Column("last_reconciled_at", sa.DateTime(timezone=True))
        )
        batch.add_column(sa.Column("next_reconcile_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("reconcile_lease_token", sa.String(64)))
        batch.add_column(
            sa.Column("reconcile_lease_expires_at", sa.DateTime(timezone=True))
        )
        batch.create_foreign_key(
            "fk_payment_attempts_subscription",
            "subscriptions",
            ["subscription_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_payment_attempts_payment_method",
            "billing_payment_methods",
            ["payment_method_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            "uq_payment_attempt_retry_target",
            [
                "id",
                "subscription_id",
                "billing_period_start",
                "billing_period_end",
                "renewal_attempt_number",
            ],
        )
        batch.create_foreign_key(
            "fk_payment_attempts_retry_target",
            "payment_attempts",
            [
                "retry_of_payment_attempt_id",
                "subscription_id",
                "billing_period_start",
                "billing_period_end",
                "retry_of_renewal_attempt_number",
            ],
            [
                "id",
                "subscription_id",
                "billing_period_start",
                "billing_period_end",
                "renewal_attempt_number",
            ],
        )
        batch.create_check_constraint(
            "ck_payment_attempt_purpose",
            "purpose IN ('initial', 'renewal')",
        )
        batch.create_check_constraint(
            "ck_payment_attempt_renewal_period",
            "(purpose = 'initial' AND billing_period_start IS NULL "
            "AND billing_period_end IS NULL AND renewal_attempt_number IS NULL "
            "AND retry_of_payment_attempt_id IS NULL "
            "AND retry_of_renewal_attempt_number IS NULL "
            "AND next_dispatch_at IS NULL) "
            "OR (purpose = 'renewal' AND subscription_id IS NOT NULL "
            "AND payment_method_id IS NOT NULL "
            "AND renewal_attempt_number IS NOT NULL "
            "AND renewal_attempt_number IN (1, 2) "
            "AND billing_period_start IS NOT NULL "
            "AND billing_period_end IS NOT NULL "
            "AND billing_period_end > billing_period_start "
            "AND ((renewal_attempt_number = 1 "
            "AND retry_of_payment_attempt_id IS NULL "
            "AND retry_of_renewal_attempt_number IS NULL "
            "AND next_dispatch_at IS NULL) "
            "OR (renewal_attempt_number = 2 "
            "AND retry_of_payment_attempt_id IS NOT NULL "
            "AND retry_of_renewal_attempt_number = 1 "
            "AND next_dispatch_at IS NOT NULL)))",
        )
        batch.create_check_constraint(
            "ck_payment_attempt_auto_renew_consent",
            "(purpose = 'initial' AND ((auto_renew_requested IS FALSE "
            "AND (save_payment_method_requested IS NULL "
            "OR save_payment_method_requested IS FALSE) "
            "AND consent_version IS NULL AND consented_at IS NULL) "
            "OR (auto_renew_requested IS TRUE "
            "AND save_payment_method_requested IS TRUE "
            "AND consent_version IS NOT NULL AND consented_at IS NOT NULL))) "
            "OR (purpose = 'renewal' AND auto_renew_requested IS TRUE "
            "AND save_payment_method_requested IS FALSE "
            "AND consent_version IS NOT NULL AND consented_at IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_payment_attempt_reconcile_lease",
            "(reconcile_lease_token IS NULL "
            "AND reconcile_lease_expires_at IS NULL) "
            "OR (reconcile_lease_token IS NOT NULL "
            "AND reconcile_lease_expires_at IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_payment_attempt_idempotency_window",
            "(first_dispatched_at IS NULL "
            "AND provider_idempotency_expires_at IS NULL) "
            "OR (first_dispatched_at IS NOT NULL "
            "AND provider_idempotency_expires_at IS NOT NULL "
            "AND provider_idempotency_expires_at > first_dispatched_at)",
        )
    _legacy_idempotency_backfill()
    op.create_index(
        "uq_payment_attempt_renewal_sequence",
        "payment_attempts",
        ["subscription_id", "billing_period_start", "renewal_attempt_number"],
        unique=True,
        postgresql_where=sa.text("purpose = 'renewal'"),
        sqlite_where=sa.text("purpose = 'renewal'"),
    )

    with op.batch_alter_table("payment_webhook_events") as batch:
        batch.add_column(
            sa.Column(
                "merchant_account_fingerprint",
                sa.String(64),
                nullable=True,
                server_default=sa.text(f"'{_LEGACY_UNKNOWN_MERCHANT}'"),
            )
        )
    op.execute(
        sa.text(
            "UPDATE payment_webhook_events AS webhook "
            "SET merchant_account_fingerprint = COALESCE(("
            "SELECT attempt.merchant_account_fingerprint "
            "FROM payment_attempts AS attempt "
            "WHERE attempt.id = webhook.payment_attempt_id"
            f"), '{_LEGACY_UNKNOWN_MERCHANT}')"
        )
    )
    with op.batch_alter_table("payment_webhook_events") as batch:
        batch.alter_column(
            "merchant_account_fingerprint",
            existing_type=sa.String(64),
            nullable=False,
            server_default=sa.text(f"'{_LEGACY_UNKNOWN_MERCHANT}'"),
        )

    _normalize_legacy_credit_bucket()


def downgrade() -> None:
    _restore_legacy_credit_bucket()

    with op.batch_alter_table("payment_webhook_events") as batch:
        batch.drop_column("merchant_account_fingerprint")

    op.drop_index(
        "uq_payment_attempt_renewal_sequence", table_name="payment_attempts"
    )
    with op.batch_alter_table("payment_attempts") as batch:
        batch.drop_constraint(
            "ck_payment_attempt_idempotency_window", type_="check"
        )
        batch.drop_constraint("ck_payment_attempt_reconcile_lease", type_="check")
        batch.drop_constraint(
            "ck_payment_attempt_auto_renew_consent", type_="check"
        )
        batch.drop_constraint("ck_payment_attempt_renewal_period", type_="check")
        batch.drop_constraint("ck_payment_attempt_purpose", type_="check")
        batch.drop_constraint(
            "fk_payment_attempts_retry_target", type_="foreignkey"
        )
        batch.drop_constraint("uq_payment_attempt_retry_target", type_="unique")
        batch.drop_constraint(
            "fk_payment_attempts_payment_method", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_payment_attempts_subscription", type_="foreignkey"
        )
        batch.drop_column("reconcile_lease_expires_at")
        batch.drop_column("reconcile_lease_token")
        batch.drop_column("next_reconcile_at")
        batch.drop_column("last_reconciled_at")
        batch.drop_column("provider_idempotency_expires_at")
        batch.drop_column("first_dispatched_at")
        batch.drop_column("request_fingerprint")
        batch.drop_column("save_payment_method_requested")
        batch.drop_column("consented_at")
        batch.drop_column("consent_version")
        batch.drop_column("auto_renew_requested")
        batch.drop_column("next_dispatch_at")
        batch.drop_column("retry_of_renewal_attempt_number")
        batch.drop_column("retry_of_payment_attempt_id")
        batch.drop_column("renewal_attempt_number")
        batch.drop_column("billing_period_end")
        batch.drop_column("billing_period_start")
        batch.drop_column("payment_method_id")
        batch.drop_column("subscription_id")
        batch.drop_column("purpose")

    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_constraint("ck_subscription_auto_renew_ready", type_="check")
        batch.drop_constraint(
            "fk_subscriptions_payment_method", type_="foreignkey"
        )
        batch.drop_column("auto_renew_disabled_at")
        batch.drop_column("auto_renew_enabled_at")
        batch.drop_column("next_renewal_at")
        batch.drop_column("auto_renew")
        batch.drop_column("payment_method_id")
        batch.drop_column("merchant_account_fingerprint")

    op.drop_index(
        "ix_billing_payment_methods_user_id",
        table_name="billing_payment_methods",
    )
    op.drop_table("billing_payment_methods")
