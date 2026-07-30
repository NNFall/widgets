"""Add immutable project-version lineage without enabling behavior.

Revision ID: 0016_project_versions
Revises: 0015_yookassa_recurring_foundation
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_project_versions"
down_revision = "0015_yookassa_recurring_foundation"
branch_labels = None
depends_on = None


_BACKFILL_PROJECT_VERSIONS = sa.text(
    """
    WITH candidates AS (
        SELECT
            project.id AS project_id,
            artifact.run_id AS run_id,
            artifact.id AS artifact_id,
            artifact.revision AS revision,
            artifact.created_at AS created_at,
            1 AS priority
        FROM projects AS project
        JOIN generation_runs AS run
          ON run.id = project.active_run_id
         AND run.project_id = project.id
        JOIN generation_artifacts AS artifact
          ON artifact.run_id = run.id
         AND artifact.revision = project.active_revision
        WHERE project.active_run_id IS NOT NULL
          AND project.active_revision IS NOT NULL
          AND run.state = 'completed'
          AND artifact.quality_status IN ('accepted', 'verified')

        UNION ALL

        SELECT
            project.id AS project_id,
            artifact.run_id AS run_id,
            artifact.id AS artifact_id,
            artifact.revision AS revision,
            artifact.created_at AS created_at,
            2 AS priority
        FROM projects AS project
        JOIN generation_runs AS run
          ON run.id = project.active_run_id
         AND run.project_id = project.id
        JOIN generation_artifacts AS artifact
          ON artifact.run_id = run.id
        WHERE run.state = 'completed'
          AND artifact.quality_status IN ('accepted', 'verified')

        UNION ALL

        SELECT
            project.id AS project_id,
            artifact.run_id AS run_id,
            artifact.id AS artifact_id,
            artifact.revision AS revision,
            artifact.created_at AS created_at,
            3 AS priority
        FROM projects AS project
        JOIN publications AS publication
          ON publication.project_id = project.id
        JOIN publication_releases AS release
          ON release.publication_id = publication.id
         AND release.id = publication.active_release_id
        JOIN generation_artifacts AS artifact
          ON artifact.id = release.artifact_id
        JOIN generation_runs AS run
          ON run.id = artifact.run_id
         AND run.project_id = project.id
        WHERE publication.active_release_id IS NOT NULL
          AND run.state = 'completed'
          AND artifact.quality_status IN ('accepted', 'verified')
    ),
    ranked AS (
        SELECT
            project_id,
            run_id,
            artifact_id,
            created_at,
            row_number() OVER (
                PARTITION BY project_id
                ORDER BY priority, revision DESC, created_at DESC, artifact_id
            ) AS candidate_rank
        FROM candidates
    )
    INSERT INTO project_versions (
        id,
        project_id,
        ordinal,
        run_id,
        artifact_id,
        parent_version_id,
        kind,
        change_request,
        idempotency_key,
        created_at
    )
    SELECT
        artifact_id,
        project_id,
        1,
        run_id,
        artifact_id,
        NULL,
        'initial',
        NULL,
        NULL,
        created_at
    FROM ranked
    WHERE candidate_rank = 1
    """
)


_BACKFILL_ACTIVE_VERSION = sa.text(
    """
    UPDATE projects
    SET active_version_id = (
        SELECT project_version.id
        FROM project_versions AS project_version
        WHERE project_version.project_id = projects.id
          AND project_version.ordinal = 1
    )
    WHERE EXISTS (
        SELECT 1
        FROM project_versions AS project_version
        WHERE project_version.project_id = projects.id
          AND project_version.ordinal = 1
    )
    """
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_generation_run_membership",
        "generation_runs",
        ["project_id", "id"],
    )
    op.create_unique_constraint(
        "uq_generation_artifact_membership",
        "generation_artifacts",
        ["run_id", "id"],
    )

    op.create_table(
        "project_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("change_request", sa.String(length=2000), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name="ck_project_version_ordinal_positive",
        ),
        sa.CheckConstraint(
            "kind IN ('initial', 'refinement', 'restore')",
            name="ck_project_version_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'initial' AND parent_version_id IS NULL "
            "AND change_request IS NULL) "
            "OR (kind = 'refinement' AND parent_version_id IS NOT NULL "
            "AND change_request IS NOT NULL "
            "AND length(trim(change_request)) BETWEEN 1 AND 2000) "
            "OR (kind = 'restore' AND parent_version_id IS NOT NULL "
            "AND change_request IS NULL)",
            name="ck_project_version_shape",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_versions_project_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "ordinal",
            name="uq_project_version_ordinal",
        ),
        sa.UniqueConstraint(
            "project_id",
            "id",
            name="uq_project_version_membership",
        ),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_project_version_restore_idempotency",
        ),
    )
    op.create_index(
        "ix_project_versions_project_id",
        "project_versions",
        ["project_id"],
    )
    op.create_index(
        "ix_project_versions_run_id",
        "project_versions",
        ["run_id"],
    )
    op.create_index(
        "ix_project_versions_artifact_id",
        "project_versions",
        ["artifact_id"],
    )
    op.create_index(
        "ix_project_versions_parent_version_id",
        "project_versions",
        ["parent_version_id"],
    )

    op.add_column(
        "projects",
        sa.Column("active_version_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "generation_runs",
        sa.Column("source_version_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "generation_runs",
        sa.Column("change_request", sa.String(length=2000), nullable=True),
    )
    op.add_column(
        "publication_releases",
        sa.Column("project_version_id", sa.Uuid(), nullable=True),
    )

    op.execute(_BACKFILL_PROJECT_VERSIONS)
    op.execute(_BACKFILL_ACTIVE_VERSION)

    op.create_foreign_key(
        "fk_project_versions_run_membership",
        "project_versions",
        "generation_runs",
        ["project_id", "run_id"],
        ["project_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_project_versions_artifact_membership",
        "project_versions",
        "generation_artifacts",
        ["run_id", "artifact_id"],
        ["run_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_project_versions_parent_membership",
        "project_versions",
        "project_versions",
        ["project_id", "parent_version_id"],
        ["project_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_projects_active_version_membership",
        "projects",
        "project_versions",
        ["id", "active_version_id"],
        ["project_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_generation_runs_source_version_membership",
        "generation_runs",
        "project_versions",
        ["project_id", "source_version_id"],
        ["project_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_publication_releases_project_version_id",
        "publication_releases",
        "project_versions",
        ["project_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_publication_releases_project_version_id",
        "publication_releases",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_generation_runs_source_version_membership",
        "generation_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_projects_active_version_membership",
        "projects",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_project_versions_parent_membership",
        "project_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_project_versions_artifact_membership",
        "project_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_project_versions_run_membership",
        "project_versions",
        type_="foreignkey",
    )

    op.drop_column("publication_releases", "project_version_id")
    op.drop_column("generation_runs", "change_request")
    op.drop_column("generation_runs", "source_version_id")
    op.drop_column("projects", "active_version_id")

    op.drop_index(
        "ix_project_versions_parent_version_id",
        table_name="project_versions",
    )
    op.drop_index(
        "ix_project_versions_artifact_id",
        table_name="project_versions",
    )
    op.drop_index("ix_project_versions_run_id", table_name="project_versions")
    op.drop_index(
        "ix_project_versions_project_id",
        table_name="project_versions",
    )
    op.drop_table("project_versions")

    op.drop_constraint(
        "uq_generation_artifact_membership",
        "generation_artifacts",
        type_="unique",
    )
    op.drop_constraint(
        "uq_generation_run_membership",
        "generation_runs",
        type_="unique",
    )
