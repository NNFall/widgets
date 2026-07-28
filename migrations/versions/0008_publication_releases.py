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
    op.create_unique_constraint(
        "uq_publication_release_membership",
        "publication_releases",
        ["publication_id", "id"],
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
    op.create_foreign_key(
        "fk_publications_active_release_membership",
        "publications",
        "publication_releases",
        ["id", "active_release_id"],
        ["publication_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_publications_active_release_membership",
        "publications",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_publications_active_release_id",
        "publications",
        type_="foreignkey",
    )
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS publication_release_downgrade_archive (
            release_id UUID PRIMARY KEY,
            publication_id UUID NOT NULL,
            artifact_id UUID NOT NULL,
            previous_release_id UUID,
            revision INTEGER NOT NULL,
            asset_manifest JSON NOT NULL,
            checksum VARCHAR(128) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE,
            archived_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
        )
    """))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT release.*,
                   row_number() OVER (
                       PARTITION BY release.publication_id, release.revision
                       ORDER BY
                           CASE WHEN publication.active_release_id = release.id THEN 0 ELSE 1 END,
                           release.created_at,
                           release.id
                   ) AS duplicate_rank
            FROM publication_releases AS release
            JOIN publications AS publication ON publication.id = release.publication_id
        )
        INSERT INTO publication_release_downgrade_archive (
            release_id, publication_id, artifact_id, previous_release_id,
            revision, asset_manifest, checksum, created_at
        )
        SELECT id, publication_id, artifact_id, previous_release_id,
               revision, asset_manifest, checksum, created_at
        FROM ranked
        WHERE duplicate_rank > 1
        ON CONFLICT (release_id) DO NOTHING
    """))
    op.execute(sa.text("""
        WITH ranked AS (
            SELECT release.id,
                   row_number() OVER (
                       PARTITION BY release.publication_id, release.revision
                       ORDER BY
                           CASE WHEN publication.active_release_id = release.id THEN 0 ELSE 1 END,
                           release.created_at,
                           release.id
                   ) AS duplicate_rank
            FROM publication_releases AS release
            JOIN publications AS publication ON publication.id = release.publication_id
        )
        DELETE FROM publication_releases AS release
        USING ranked
        WHERE release.id = ranked.id AND ranked.duplicate_rank > 1
    """))
    op.drop_constraint(
        "uq_publication_release_artifact",
        "publication_releases",
        type_="unique",
    )
    op.drop_constraint(
        "uq_publication_release_membership",
        "publication_releases",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_publication_release_revision",
        "publication_releases",
        ["publication_id", "revision"],
    )
