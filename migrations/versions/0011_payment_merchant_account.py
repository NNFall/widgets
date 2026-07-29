"""Bind payment retries to the merchant account that created the attempt.

Revision ID: 0011_payment_merchant_account
Revises: 0010_funnel_events
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_payment_merchant_account"
down_revision = "0010_funnel_events"
branch_labels = None
depends_on = None


_LEGACY_UNKNOWN_MERCHANT = (
    "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
)


def upgrade() -> None:
    with op.batch_alter_table("payment_attempts") as batch:
        batch.add_column(
            sa.Column(
                "merchant_account_fingerprint",
                sa.String(64),
                nullable=True,
            )
        )
    # A historical attempt cannot be safely attributed to the currently
    # configured merchant: a previous provider request may have succeeded
    # before Kaigo persisted the response. Keep those rows usable for display
    # and webhook audit, but make every future provider dispatch fail closed.
    op.execute(
        "UPDATE payment_attempts "
        f"SET merchant_account_fingerprint = '{_LEGACY_UNKNOWN_MERCHANT}' "
        "WHERE merchant_account_fingerprint IS NULL"
    )
    with op.batch_alter_table("payment_attempts") as batch:
        batch.alter_column(
            "merchant_account_fingerprint",
            existing_type=sa.String(64),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("payment_attempts") as batch:
        batch.drop_column("merchant_account_fingerprint")
