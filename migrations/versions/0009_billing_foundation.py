"""Add provider-neutral billing fulfillment foundations.

Revision ID: 0009_billing_foundation
Revises: 0008_publication_releases
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_billing_foundation"
down_revision = "0008_publication_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payment_attempts", sa.Column("plan_code", sa.String(64)))
    op.add_column("payment_attempts", sa.Column("plan_snapshot", sa.JSON()))
    op.add_column("payment_attempts", sa.Column("plan_fingerprint", sa.String(64)))
    op.add_column(
        "payment_attempts",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.execute(sa.text("UPDATE payment_attempts SET currency = upper(currency)"))
    op.get_bind().exec_driver_sql("""
        UPDATE payment_attempts
        SET plan_code = 'legacy_migrated',
            plan_snapshot = json_build_object(
                'code', 'legacy_migrated',
                'title', 'Legacy Kaigo plan',
                'amount_minor', amount_minor,
                'currency', currency,
                'period_days', 30,
                'generation_tokens', 1000000
            ),
            plan_fingerprint = encode(sha256(convert_to(
                '{"amount_minor":' || amount_minor::text ||
                ',"code":"legacy_migrated"' ||
                ',"currency":' || to_json(currency)::text ||
                ',"generation_tokens":1000000' ||
                ',"period_days":30' ||
                ',"title":"Legacy Kaigo plan"}',
                'UTF8'
            )), 'hex')
        WHERE plan_code IS NULL
    """)
    op.alter_column("payment_attempts", "plan_code", nullable=False)
    op.alter_column("payment_attempts", "plan_snapshot", nullable=False)
    op.alter_column("payment_attempts", "plan_fingerprint", nullable=False)
    op.alter_column("payment_attempts", "updated_at", nullable=False)
    op.execute(sa.text(
        "ALTER TABLE payment_attempts DROP CONSTRAINT IF EXISTS "
        "payment_attempts_idempotency_key_key"
    ))
    op.execute(sa.text(
        "ALTER TABLE payment_attempts DROP CONSTRAINT IF EXISTS "
        "payment_attempts_provider_payment_id_key"
    ))
    op.create_unique_constraint(
        "uq_payment_attempt_user_idempotency",
        "payment_attempts",
        ["user_id", "idempotency_key"],
    )
    op.create_unique_constraint(
        "uq_payment_attempt_provider_payment",
        "payment_attempts",
        ["provider", "provider_payment_id"],
    )
    op.create_check_constraint(
        "ck_payment_attempt_positive_amount",
        "payment_attempts",
        "amount_minor > 0",
    )

    op.add_column(
        "subscriptions",
        sa.Column("payment_attempt_id", sa.Uuid()),
    )
    op.add_column("subscriptions", sa.Column("plan_snapshot", sa.JSON()))
    op.add_column("subscriptions", sa.Column("plan_fingerprint", sa.String(64)))
    op.add_column(
        "subscriptions", sa.Column("current_period_start", sa.DateTime(timezone=True))
    )
    op.add_column(
        "subscriptions",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.execute(sa.text("""
        UPDATE subscriptions
        SET plan_snapshot = json_build_object('code', plan_code),
            plan_fingerprint = md5('legacy:' || plan_code) || md5('billing:' || plan_code),
            current_period_start = COALESCE(created_at, now()),
            current_period_end = CASE
                WHEN current_period_end IS NULL OR current_period_end <= COALESCE(created_at, now())
                    THEN COALESCE(created_at, now()) + interval '30 days'
                ELSE current_period_end
            END
    """))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY user_id
                ORDER BY current_period_end DESC, created_at DESC, id DESC
            ) AS active_rank
            FROM subscriptions
            WHERE status = 'active'
        )
        UPDATE subscriptions AS subscription
        SET status = 'superseded'
        FROM ranked
        WHERE subscription.id = ranked.id AND ranked.active_rank > 1
    """))
    op.alter_column("subscriptions", "plan_snapshot", nullable=False)
    op.alter_column("subscriptions", "plan_fingerprint", nullable=False)
    op.alter_column("subscriptions", "updated_at", nullable=False)
    op.create_foreign_key(
        "fk_subscriptions_payment_attempt",
        "subscriptions",
        "payment_attempts",
        ["payment_attempt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_subscription_finite_period",
        "subscriptions",
        "status <> 'active' OR (current_period_start IS NOT NULL AND "
        "current_period_end IS NOT NULL AND current_period_end > current_period_start)",
    )
    op.create_index(
        "uq_subscriptions_one_active_user",
        "subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.add_column("usage_ledger", sa.Column("payment_attempt_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_usage_ledger_payment_attempt",
        "usage_ledger",
        "payment_attempts",
        ["payment_attempt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_usage_ledger_payment_attempt_id",
        "usage_ledger",
        ["payment_attempt_id"],
    )

    op.add_column(
        "payment_webhook_events", sa.Column("payment_attempt_id", sa.Uuid())
    )
    op.create_foreign_key(
        "fk_payment_webhook_payment_attempt",
        "payment_webhook_events",
        "payment_attempts",
        ["payment_attempt_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_payment_webhook_events_payment_attempt_id",
        "payment_webhook_events",
        ["payment_attempt_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payment_webhook_events_payment_attempt_id",
        table_name="payment_webhook_events",
    )
    op.drop_constraint(
        "fk_payment_webhook_payment_attempt",
        "payment_webhook_events",
        type_="foreignkey",
    )
    op.drop_column("payment_webhook_events", "payment_attempt_id")

    op.drop_index("ix_usage_ledger_payment_attempt_id", table_name="usage_ledger")
    op.drop_constraint(
        "fk_usage_ledger_payment_attempt", "usage_ledger", type_="foreignkey"
    )
    op.drop_column("usage_ledger", "payment_attempt_id")

    op.drop_index("uq_subscriptions_one_active_user", table_name="subscriptions")
    op.drop_constraint(
        "ck_subscription_finite_period", "subscriptions", type_="check"
    )
    op.drop_constraint(
        "fk_subscriptions_payment_attempt", "subscriptions", type_="foreignkey"
    )
    op.drop_column("subscriptions", "updated_at")
    op.drop_column("subscriptions", "current_period_start")
    op.drop_column("subscriptions", "plan_fingerprint")
    op.drop_column("subscriptions", "plan_snapshot")
    op.drop_column("subscriptions", "payment_attempt_id")

    op.drop_constraint(
        "ck_payment_attempt_positive_amount", "payment_attempts", type_="check"
    )
    op.drop_constraint(
        "uq_payment_attempt_provider_payment", "payment_attempts", type_="unique"
    )
    op.drop_constraint(
        "uq_payment_attempt_user_idempotency", "payment_attempts", type_="unique"
    )
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY idempotency_key ORDER BY created_at, id
            ) AS duplicate_rank
            FROM payment_attempts
        )
        UPDATE payment_attempts AS attempt
        SET idempotency_key = left(attempt.idempotency_key, 91) || ':' || attempt.id::text
        FROM ranked
        WHERE attempt.id = ranked.id AND ranked.duplicate_rank > 1
    """))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT id, row_number() OVER (
                PARTITION BY provider_payment_id ORDER BY created_at, id
            ) AS duplicate_rank
            FROM payment_attempts
            WHERE provider_payment_id IS NOT NULL
        )
        UPDATE payment_attempts AS attempt
        SET provider_payment_id = NULL
        FROM ranked
        WHERE attempt.id = ranked.id AND ranked.duplicate_rank > 1
    """))
    op.create_unique_constraint(
        "payment_attempts_idempotency_key_key",
        "payment_attempts",
        ["idempotency_key"],
    )
    op.create_unique_constraint(
        "payment_attempts_provider_payment_id_key",
        "payment_attempts",
        ["provider_payment_id"],
    )
    op.drop_column("payment_attempts", "updated_at")
    op.drop_column("payment_attempts", "plan_fingerprint")
    op.drop_column("payment_attempts", "plan_snapshot")
    op.drop_column("payment_attempts", "plan_code")
