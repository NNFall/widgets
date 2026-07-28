"""Harden immutable publication releases and active release integrity.

Revision ID: 0008_publication_releases
Revises: 0007_project_recovery
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_publication_releases"
down_revision = "0007_project_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_publication_release_revision",
        "publication_releases",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_publication_release_artifact",
        "publication_releases",
        ["publication_id", "artifact_id"],
    )
    op.execute(sa.text(
        "UPDATE publications AS publication SET active_release_id = NULL "
        "WHERE publication.active_release_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM publication_releases AS release "
        "WHERE release.id = publication.active_release_id "
        "AND release.publication_id = publication.id)"
    ))
    op.create_foreign_key(
        "fk_publications_active_release_id",
        "publications",
        "publication_releases",
        ["active_release_id"],
        ["id"],
        ondelete="SET NULL",
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_publications_active_release_id",
        "publications",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_publication_release_artifact",
        "publication_releases",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_publication_release_revision",
        "publication_releases",
        ["publication_id", "revision"],
    )
