"""add singleton worker service readiness lease

Revision ID: 0012_worker_service_readiness
Revises: 0011_payment_merchant_account
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_worker_service_readiness"
down_revision = "0011_payment_merchant_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_service_leases",
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("boot_id", sa.String(length=128), nullable=False),
        sa.Column("deployment_id", sa.String(length=128), nullable=False),
        sa.Column("image_identity", sa.String(length=512), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("service_name"),
    )


def downgrade() -> None:
    op.drop_table("worker_service_leases")
