"""add widget configuration columns"""

from alembic import op
import sqlalchemy as sa

revision = '0002_widget_settings'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('widgets', sa.Column('template', sa.String(length=50), nullable=False, server_default='blue'))
    op.add_column('widgets', sa.Column('stt_model', sa.String(length=255), nullable=True))
    op.add_column('widgets', sa.Column('temperature', sa.Float(), nullable=False, server_default='0.5'))
    op.add_column('widgets', sa.Column('max_tokens', sa.Integer(), nullable=False, server_default='1000'))
    op.execute("UPDATE widgets SET template='blue' WHERE template IS NULL")
    op.execute("UPDATE widgets SET temperature=0.5 WHERE temperature IS NULL")
    op.execute("UPDATE widgets SET max_tokens=1000 WHERE max_tokens IS NULL")
    op.alter_column('widgets', 'template', server_default=None)
    op.alter_column('widgets', 'temperature', server_default=None)
    op.alter_column('widgets', 'max_tokens', server_default=None)


def downgrade() -> None:
    op.drop_column('widgets', 'max_tokens')
    op.drop_column('widgets', 'temperature')
    op.drop_column('widgets', 'stt_model')
    op.drop_column('widgets', 'template')
