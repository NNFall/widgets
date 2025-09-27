"""initial schema"""

from alembic import op
import sqlalchemy as sa

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False, server_default='tenant_admin'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    )

    op.create_table(
        'widgets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False, unique=True),
        sa.Column('ai_model', sa.String(length=255), nullable=False),
        sa.Column('prompt_source', sa.Text(), nullable=True),
        sa.Column('intro_text', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='draft'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    )

    op.create_table(
        'widget_assets',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('widget_id', sa.Integer(), nullable=False),
        sa.Column('html', sa.Text(), nullable=True),
        sa.Column('css', sa.Text(), nullable=True),
        sa.Column('js', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['widget_id'], ['widgets.id'], ondelete='CASCADE'),
    )

    op.create_table(
        'widget_bindings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('widget_id', sa.Integer(), nullable=False),
        sa.Column('domain', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['widget_id'], ['widgets.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('widget_id', 'domain'),
    )


def downgrade() -> None:
    op.drop_table('widget_bindings')
    op.drop_table('widget_assets')
    op.drop_table('widgets')
    op.drop_table('users')
    op.drop_table('tenants')
