from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Float,
    Integer,
    Index,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db import models as legacy_models  # noqa: F401
from app.db.base import Base


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


def _json_document():
    return JSON().with_variant(JSONB(), "postgresql")


class FunnelJourney(Base):
    __tablename__ = "funnel_journeys"

    id: Mapped[UUID] = _uuid_pk()
    campaign_source: Mapped[str | None] = mapped_column(String(255))
    campaign_medium: Mapped[str | None] = mapped_column(String(255))
    campaign_name: Mapped[str | None] = mapped_column(String(255))
    campaign_term: Mapped[str | None] = mapped_column(String(255))
    campaign_content: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class UserIdentity(Base):
    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_subject", name="uq_identity_subject"),)

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[UUID] = _uuid_pk()
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_prefix_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthState(Base):
    __tablename__ = "oauth_states"

    id: Mapped[UUID] = _uuid_pk()
    state_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    session_binding_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    pkce_verifier: Mapped[str] = mapped_column(String(255), nullable=False)
    nonce: Mapped[str | None] = mapped_column(String(255))
    return_path: Mapped[str] = mapped_column(String(1024), nullable=False, default="/studio")
    draft_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    journey_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "funnel_journeys.id",
            name="fk_oauth_states_journey_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnonymousDraft(Base):
    __tablename__ = "anonymous_drafts"

    id: Mapped[UUID] = _uuid_pk()
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    brief: Mapped[str | None] = mapped_column(Text)
    campaign: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    claim_token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    journey_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "funnel_journeys.id",
            name="fk_anonymous_drafts_journey_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "active_version_id"],
            ["project_versions.project_id", "project_versions.id"],
            name="fk_projects_active_version_membership",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    journey_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "funnel_journeys.id",
            name="fk_projects_journey_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    brief: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    active_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    active_revision: Mapped[int | None] = mapped_column(Integer)
    active_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_project_run_idempotency"
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_generation_run_membership"
        ),
        ForeignKeyConstraint(
            ["project_id", "source_version_id"],
            ["project_versions.project_id", "project_versions.id"],
            name="fk_generation_runs_source_version_membership",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    journey_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "funnel_journeys.id",
            name="fk_generation_runs_journey_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_stage: Mapped[str | None] = mapped_column(String(64))
    last_completed_stage: Mapped[str | None] = mapped_column(String(64))
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    source_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    change_request: Mapped[str | None] = mapped_column(String(2000))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage_retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    retry_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    failure_category: Mapped[str | None] = mapped_column(String(64))
    trial_settlement: Mapped[str | None] = mapped_column(String(32))
    trial_settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GenerationStageAttempt(Base):
    __tablename__ = "generation_stage_attempts"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage",
            "ordinal",
            name="uq_generation_stage_attempt_run_stage_ordinal",
        ),
        UniqueConstraint(
            "id",
            "run_id",
            name="uq_generation_stage_attempt_id_run",
        ),
        CheckConstraint(
            "ordinal > 0",
            name="ck_generation_stage_attempt_ordinal_positive",
        ),
        CheckConstraint(
            "status IN ('running', 'result_staged', 'completed', 'failed', "
            "'interrupted', 'cancelled', 'accounting_failed')",
            name="ck_generation_stage_attempt_status",
        ),
        CheckConstraint(
            "status IN ('running', 'result_staged') OR finished_at IS NOT NULL",
            name="ck_generation_stage_attempt_terminal_finished",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkerServiceLease(Base):
    """Singleton liveness record for the currently expected builder process."""

    __tablename__ = "worker_service_leases"

    service_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    boot_id: Mapped[str] = mapped_column(String(128), nullable=False)
    deployment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    image_identity: Mapped[str] = mapped_column(String(512), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenerationEvent(Base):
    __tablename__ = "generation_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_run_event_sequence"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    public_message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    registry_version: Mapped[int | None] = mapped_column(Integer)
    public_payload: Mapped[dict[str, Any] | None] = mapped_column(_json_document())
    forensic_ref: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GenerationForensicManifest(Base):
    __tablename__ = "generation_forensic_manifests"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_generation_forensic_manifest_run"),
        UniqueConstraint(
            "storage_key", name="uq_generation_forensic_manifest_storage_key"
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_generation_forensic_manifest_schema_version_positive",
        ),
        CheckConstraint(
            "state IN ('pending', 'active', 'completed', 'failed', "
            "'cancelled', 'degraded')",
            name="ck_generation_forensic_manifest_state",
        ),
        CheckConstraint(
            "last_event_sequence >= 0 AND entry_count >= 0 AND byte_count >= 0",
            name="ck_generation_forensic_manifest_counts_nonnegative",
        ),
        CheckConstraint(
            "manifest_sha256 IS NULL OR length(manifest_sha256) = 64",
            name="ck_generation_forensic_manifest_sha256_length",
        ),
        Index(
            "ix_generation_forensic_manifests_user_created_at",
            "user_id",
            "created_at",
        ),
        Index(
            "ix_generation_forensic_manifests_state_expires_at",
            "state",
            "expires_at",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    last_event_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    entry_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", _json_document(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class GenerationForensicAccessLog(Base):
    __tablename__ = "generation_forensic_access_logs"
    __table_args__ = (
        CheckConstraint(
            "action IN ('search', 'view', 'export')",
            name="ck_generation_forensic_access_action",
        ),
        Index(
            "ix_generation_forensic_access_logs_run_created_at",
            "run_id",
            "created_at",
        ),
        Index(
            "ix_generation_forensic_access_logs_actor_created_at",
            "actor_email",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="SET NULL"), index=True
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", _json_document(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index(
    "ix_generation_runs_unsettled_terminal",
    GenerationRun.state,
    GenerationRun.trial_settled_at,
    GenerationRun.created_at,
    postgresql_where=text(
        "state IN ('completed', 'failed', 'cancelled') AND trial_settled_at IS NULL"
    ),
)
Index(
    "ix_generation_runs_worker_claim",
    GenerationRun.state,
    GenerationRun.retry_not_before,
    GenerationRun.lease_expires_at,
    GenerationRun.created_at,
    GenerationRun.id,
    postgresql_where=text("state IN ('queued', 'running')"),
)
Index(
    "ix_generation_events_run_type_sequence_desc",
    GenerationEvent.run_id,
    GenerationEvent.event_type,
    GenerationEvent.sequence.desc(),
)


class GenerationArtifact(Base):
    __tablename__ = "generation_artifacts"
    __table_args__ = (
        UniqueConstraint("run_id", "revision", name="uq_run_artifact_revision"),
        UniqueConstraint(
            "run_id", "id", name="uq_generation_artifact_membership"
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(ForeignKey("generation_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    css: Mapped[str] = mapped_column(Text, nullable=False, default="")
    javascript: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unreviewed")
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectVersion(Base):
    __tablename__ = "project_versions"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "ordinal", name="uq_project_version_ordinal"
        ),
        UniqueConstraint(
            "project_id", "id", name="uq_project_version_membership"
        ),
        UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_project_version_restore_idempotency",
        ),
        CheckConstraint(
            "ordinal > 0",
            name="ck_project_version_ordinal_positive",
        ),
        CheckConstraint(
            "kind IN ('initial', 'refinement', 'restore')",
            name="ck_project_version_kind",
        ),
        CheckConstraint(
            "(kind = 'initial' AND parent_version_id IS NULL "
            "AND change_request IS NULL) "
            "OR (kind = 'refinement' AND parent_version_id IS NOT NULL "
            "AND change_request IS NOT NULL "
            "AND length(trim(change_request)) BETWEEN 1 AND 2000) "
            "OR (kind = 'restore' AND parent_version_id IS NOT NULL "
            "AND change_request IS NULL)",
            name="ck_project_version_shape",
        ),
        ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["generation_runs.project_id", "generation_runs.id"],
            name="fk_project_versions_run_membership",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["run_id", "artifact_id"],
            ["generation_artifacts.run_id", "generation_artifacts.id"],
            name="fk_project_versions_artifact_membership",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["project_id", "parent_version_id"],
            ["project_versions.project_id", "project_versions.id"],
            name="fk_project_versions_parent_membership",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "projects.id",
            name="fk_project_versions_project_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    artifact_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, index=True
    )
    parent_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    change_request: Mapped[str | None] = mapped_column(String(2000))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ArtifactEvidence(Base):
    __tablename__ = "artifact_evidence"

    id: Mapped[UUID] = _uuid_pk()
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("generation_artifacts.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (
        ForeignKeyConstraint(
            ["stage_attempt_id", "run_id"],
            ["generation_stage_attempts.id", "generation_stage_attempts.run_id"],
            name="fk_model_calls_stage_attempt_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "logical_invocation_id",
            "fallback_index",
            name="uq_model_call_logical_fallback",
        ),
        CheckConstraint(
            "stage_attempt_id IS NULL OR run_id IS NOT NULL",
            name="ck_model_calls_stage_attempt_requires_run",
        ),
        CheckConstraint(
            "semantic_attempt > 0",
            name="ck_model_calls_semantic_attempt_positive",
        ),
        CheckConstraint(
            "fallback_index > 0",
            name="ck_model_calls_fallback_index_positive",
        ),
        CheckConstraint(
            "cache_read_tokens >= 0 AND cache_write_tokens >= 0",
            name="ck_model_calls_cache_tokens_nonnegative",
        ),
        CheckConstraint(
            "thinking_tokens <= output_tokens AND "
            "cache_read_tokens + cache_write_tokens <= input_tokens",
            name="ck_model_calls_token_subsets",
        ),
        CheckConstraint(
            "(actual_provider IS NULL) = (actual_model IS NULL)",
            name="ck_model_calls_actual_identity_pair",
        ),
        CheckConstraint(
            "cost_state IN ('reported', 'estimated', 'unknown', 'not_billed')",
            name="ck_model_calls_cost_state_value",
        ),
        CheckConstraint(
            "cost_microusd IS NULL OR cost_microusd >= 0",
            name="ck_model_calls_cost_nonnegative",
        ),
        CheckConstraint(
            "(cost_state = 'unknown' AND cost_microusd IS NULL) OR "
            "(cost_state = 'not_billed' AND cost_microusd = 0) OR "
            "(cost_state IN ('reported', 'estimated') "
            "AND cost_microusd IS NOT NULL)",
            name="ck_model_calls_cost_state_amount",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("generation_runs.id", ondelete="SET NULL"), index=True)
    stage_attempt_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), index=True
    )
    logical_invocation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, default=uuid4
    )
    operation: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legacy_unclassified"
    )
    semantic_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    candidate_id: Mapped[str | None] = mapped_column(String(64))
    persona: Mapped[str | None] = mapped_column(String(64))
    fallback_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    actual_provider: Mapped[str | None] = mapped_column(String(64))
    actual_model: Mapped[str | None] = mapped_column(String(128))
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey("generation_artifacts.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="direct")
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_dispatched: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    thinking_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    cache_write_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    latency_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    cost_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="estimated"
    )
    cost_microusd: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=0
    )
    pricing_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WidgetPatternVersion(Base):
    __tablename__ = "widget_pattern_versions"
    __table_args__ = (
        UniqueConstraint("pattern_id", "version", name="uq_pattern_version"),
        CheckConstraint("version > 0", name="ck_pattern_version_positive"),
    )

    id: Mapped[UUID] = _uuid_pk()
    pattern_id: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_snapshot: Mapped[dict[str, Any]] = mapped_column(
        _json_document(), nullable=False
    )
    implementation_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompositionPlanRecord(Base):
    __tablename__ = "composition_plans"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_composition_plan_run"),
        CheckConstraint(
            "schema_version > 0",
            name="ck_composition_plan_schema_version_positive",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "generation_runs.id",
            name="fk_composition_plans_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    direction_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "generation_artifacts.id",
            name="fk_composition_plans_direction_artifact_id",
            ondelete="SET NULL",
        )
    )
    planner_model_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "model_calls.id",
            name="fk_composition_plans_planner_model_call_id",
            ondelete="SET NULL",
        )
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    direction_id: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    custom_escape: Mapped[dict[str, Any] | None] = mapped_column(_json_document())
    registry_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompositionPlanItem(Base):
    __tablename__ = "composition_plan_items"
    __table_args__ = (
        UniqueConstraint(
            "composition_plan_id",
            "slot",
            name="uq_composition_slot",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    composition_plan_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "composition_plans.id",
            name="fk_composition_plan_items_plan_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    pattern_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "widget_pattern_versions.id",
            name="fk_composition_plan_items_pattern_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(
        _json_document(), nullable=False, default=dict
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatternOutcome(Base):
    __tablename__ = "pattern_outcomes"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_pattern_outcome_idempotency"),
        CheckConstraint(
            "repair_count >= 0",
            name="ck_pattern_outcome_repairs_nonnegative",
        ),
        CheckConstraint(
            "visual_score IS NULL OR (visual_score >= 0 AND visual_score <= 1)",
            name="ck_pattern_outcome_visual_score_range",
        ),
        CheckConstraint(
            "cost_microusd >= 0",
            name="ck_pattern_outcome_cost_nonnegative",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    composition_plan_item_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "composition_plan_items.id",
            name="fk_pattern_outcomes_plan_item_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "generation_runs.id",
            name="fk_pattern_outcomes_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    final_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "generation_artifacts.id",
            name="fk_pattern_outcomes_final_artifact_id",
            ondelete="SET NULL",
        )
    )
    model_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "model_calls.id",
            name="fk_pattern_outcomes_model_call_id",
            ondelete="SET NULL",
        )
    )
    technical_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    visual_score: Mapped[float | None] = mapped_column(Float)
    repair_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    thinking_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    adopted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        _json_document(), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatternCandidatePlanRecord(Base):
    """Durable schema-v2 shortlist captured for one generation run."""

    __tablename__ = "pattern_candidate_plans"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_pattern_candidate_plan_run"),
        CheckConstraint(
            "schema_version = 2",
            name="ck_pattern_candidate_plan_schema_version",
        ),
        CheckConstraint(
            "length(registry_digest) = 64",
            name="ck_pattern_candidate_plan_registry_digest_sha256",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "generation_runs.id",
            name="fk_pattern_candidate_plans_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    direction_artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "generation_artifacts.id",
            name="fk_pattern_candidate_plans_direction_artifact_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    selector_model_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "model_calls.id",
            name="fk_pattern_candidate_plans_selector_model_call_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    direction_id: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    registry_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatternCandidateGroupRecord(Base):
    """One category in a persisted shortlist and its stage routing snapshot."""

    __tablename__ = "pattern_candidate_groups"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "category",
            name="uq_pattern_candidate_group_plan_category",
        ),
        UniqueConstraint(
            "plan_id",
            "ordinal",
            name="uq_pattern_candidate_group_plan_ordinal",
        ),
        CheckConstraint(
            "ordinal BETWEEN 1 AND 14",
            name="ck_pattern_candidate_group_ordinal",
        ),
        Index("ix_pattern_candidate_groups_category", "category"),
    )

    id: Mapped[UUID] = _uuid_pk()
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "pattern_candidate_plans.id",
            name="fk_pattern_candidate_groups_plan_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    stage_mapping: Mapped[list[str]] = mapped_column(
        "stage_mapping",
        _json_document(),
        nullable=False,
        default=list,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatternCandidateItemRecord(Base):
    """Exact immutable pattern version selected within a candidate group."""

    __tablename__ = "pattern_candidate_items"
    __table_args__ = (
        UniqueConstraint(
            "group_id",
            "rank",
            name="uq_pattern_candidate_item_group_rank",
        ),
        UniqueConstraint(
            "group_id",
            "pattern_version_id",
            name="uq_pattern_candidate_item_group_pattern_version",
        ),
        CheckConstraint(
            "rank BETWEEN 1 AND 5",
            name="ck_pattern_candidate_item_rank",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    group_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "pattern_candidate_groups.id",
            name="fk_pattern_candidate_items_group_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    pattern_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "widget_pattern_versions.id",
            name="fk_pattern_candidate_items_pattern_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatternStageExposure(Base):
    """A shortlist item exposed to one generation stage/model invocation.

    The idempotency key is the tuple ``(run_id, stage, candidate_item_id,
    idempotency_model_call_id)``.  ``model_call_id`` remains nullable so a
    deleted model call is represented accurately; the stable identity column
    keeps idempotency deterministic even after that foreign key is set NULL.
    """

    __tablename__ = "pattern_stage_exposures"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "stage",
            "candidate_item_id",
            "idempotency_model_call_id",
            name="uq_pattern_stage_exposure_idempotency",
        ),
        CheckConstraint(
            "stage IN ('foundation', 'identity', 'conversation', 'motion_polish')",
            name="ck_pattern_stage_exposure_stage",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "generation_runs.id",
            name="fk_pattern_stage_exposures_run_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_item_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "pattern_candidate_items.id",
            name="fk_pattern_stage_exposures_candidate_item_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    model_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "model_calls.id",
            name="fk_pattern_stage_exposures_model_call_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    idempotency_model_call_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatternStageUsageClaim(Base):
    """One mutually-exclusive generator usage claim for an exposure."""

    __tablename__ = "pattern_stage_usage_claims"
    __table_args__ = (
        UniqueConstraint(
            "exposure_id",
            name="uq_pattern_stage_usage_claim_exposure",
        ),
        CheckConstraint(
            "usage_mode IN ('primary', 'combined', 'inspiration')",
            name="ck_pattern_stage_usage_mode",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    exposure_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "pattern_stage_exposures.id",
            name="fk_pattern_stage_usage_claims_exposure_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    usage_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    model_call_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "model_calls.id",
            name="fk_pattern_stage_usage_claims_model_call_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PatternReview(Base):
    """Append-only human review provenance for an immutable pattern version."""

    __tablename__ = "pattern_reviews"
    __table_args__ = (
        CheckConstraint(
            "status IN ('approved', 'rejected')",
            name="ck_pattern_review_status",
        ),
        Index(
            "ix_pattern_reviews_pattern_version_created_at",
            "pattern_version_id",
            "created_at",
        ),
        Index("ix_pattern_reviews_status", "status"),
    )

    id: Mapped[UUID] = _uuid_pk()
    pattern_version_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "widget_pattern_versions.id",
            name="fk_pattern_reviews_pattern_version_id",
            ondelete="RESTRICT",
        ),
        nullable=False,
        index=True,
    )
    reviewer_email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UsageLedger(Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_usage_ledger_nonzero"),
        UniqueConstraint("idempotency_key", name="uq_usage_ledger_idempotency"),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"))
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("generation_runs.id", ondelete="SET NULL"))
    model_call_id: Mapped[UUID | None] = mapped_column(ForeignKey("model_calls.id", ondelete="SET NULL"))
    payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL"), index=True
    )
    founder_grant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("founder_access_grants.id", ondelete="SET NULL"), index=True
    )
    bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrialEntitlement(Base):
    __tablename__ = "trial_entitlements"

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="available")
    granted_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    reserved_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    consumed_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reservation_key: Mapped[str | None] = mapped_column(String(128))
    reservation_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BillingPaymentMethod(Base):
    __tablename__ = "billing_payment_methods"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "merchant_account_fingerprint",
            "provider_payment_method_id",
            name="uq_billing_payment_method_provider_identity",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'invalid')",
            name="ck_billing_payment_method_status",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(
        ForeignKey(
            "users.id",
            name="fk_billing_payment_methods_user_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    merchant_account_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    provider_payment_method_id: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    source_payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "payment_attempts.id",
            name="fk_billing_payment_methods_source_attempt",
            ondelete="SET NULL",
        )
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    consent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    saved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status <> 'active' OR (current_period_start IS NOT NULL AND "
            "current_period_end IS NOT NULL AND current_period_end > current_period_start)",
            name="ck_subscription_finite_period",
        ),
        CheckConstraint(
            "NOT auto_renew OR (payment_method_id IS NOT NULL "
            "AND merchant_account_fingerprint IS NOT NULL "
            "AND next_renewal_at IS NOT NULL)",
            name="ck_subscription_auto_renew_ready",
        ),
        Index(
            "uq_subscriptions_one_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_customer_id: Mapped[str | None] = mapped_column(String(255))
    provider_subscription_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    merchant_account_fingerprint: Mapped[str | None] = mapped_column(String(64))
    payment_method_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "billing_payment_methods.id",
            name="fk_subscriptions_payment_method",
            ondelete="SET NULL",
        )
    )
    payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL")
    )
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_renew: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    next_renewal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_renew_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    auto_renew_disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FounderAccessGrant(Base):
    __tablename__ = "founder_access_grants"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_founder_access_grant_user"),
        UniqueConstraint("source_origin", name="uq_founder_access_grant_origin"),
        UniqueConstraint("position", name="uq_founder_access_grant_position"),
        CheckConstraint(
            "position BETWEEN 1 AND 20",
            name="ck_founder_access_grant_position",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            "subscriptions.id",
            name="fk_founder_access_grant_subscription",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=False,
        unique=True,
    )
    source_origin: Mapped[str] = mapped_column(String(2048), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    feedback_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="requested", server_default="requested"
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CustomerContactRequest(Base):
    __tablename__ = "customer_contact_requests"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_customer_contact_request_user_idempotency",
        ),
        CheckConstraint(
            "kind IN ('support', 'founder_feedback')",
            name="ck_customer_contact_request_kind",
        ),
        CheckConstraint(
            "rating IS NULL OR rating BETWEEN 1 AND 5",
            name="ck_customer_contact_request_rating",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="SET NULL"), index=True
    )
    founder_grant_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("founder_access_grants.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    testimonial_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_payment_attempt_positive_amount"),
        UniqueConstraint(
            "user_id", "idempotency_key", name="uq_payment_attempt_user_idempotency"
        ),
        UniqueConstraint(
            "provider", "provider_payment_id", name="uq_payment_attempt_provider_payment"
        ),
        UniqueConstraint(
            "id",
            "subscription_id",
            "billing_period_start",
            "billing_period_end",
            "renewal_attempt_number",
            name="uq_payment_attempt_retry_target",
        ),
        ForeignKeyConstraint(
            [
                "retry_of_payment_attempt_id",
                "subscription_id",
                "billing_period_start",
                "billing_period_end",
                "retry_of_renewal_attempt_number",
            ],
            [
                "payment_attempts.id",
                "payment_attempts.subscription_id",
                "payment_attempts.billing_period_start",
                "payment_attempts.billing_period_end",
                "payment_attempts.renewal_attempt_number",
            ],
            name="fk_payment_attempts_retry_target",
            use_alter=True,
        ),
        CheckConstraint(
            "purpose IN ('initial', 'renewal')",
            name="ck_payment_attempt_purpose",
        ),
        CheckConstraint(
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
            name="ck_payment_attempt_renewal_period",
        ),
        CheckConstraint(
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
            name="ck_payment_attempt_auto_renew_consent",
        ),
        CheckConstraint(
            "(reconcile_lease_token IS NULL "
            "AND reconcile_lease_expires_at IS NULL) "
            "OR (reconcile_lease_token IS NOT NULL "
            "AND reconcile_lease_expires_at IS NOT NULL)",
            name="ck_payment_attempt_reconcile_lease",
        ),
        CheckConstraint(
            "(first_dispatched_at IS NULL "
            "AND provider_idempotency_expires_at IS NULL) "
            "OR (first_dispatched_at IS NOT NULL "
            "AND provider_idempotency_expires_at IS NOT NULL "
            "AND provider_idempotency_expires_at > first_dispatched_at)",
            name="ck_payment_attempt_idempotency_window",
        ),
        Index(
            "uq_payment_attempt_renewal_sequence",
            "subscription_id",
            "billing_period_start",
            "renewal_attempt_number",
            unique=True,
            postgresql_where=text("purpose = 'renewal'"),
            sqlite_where=text("purpose = 'renewal'"),
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "projects.id",
            name="fk_payment_attempts_project_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    journey_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "funnel_journeys.id",
            name="fk_payment_attempts_journey_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    merchant_account_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    purpose: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="initial",
        server_default=text("'initial'"),
    )
    subscription_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "subscriptions.id",
            name="fk_payment_attempts_subscription",
            ondelete="SET NULL",
            use_alter=True,
        )
    )
    payment_method_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "billing_payment_methods.id",
            name="fk_payment_attempts_payment_method",
            ondelete="SET NULL",
            use_alter=True,
        )
    )
    billing_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    billing_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    renewal_attempt_number: Mapped[int | None] = mapped_column(SmallInteger)
    retry_of_payment_attempt_id: Mapped[UUID | None] = mapped_column(Uuid)
    retry_of_renewal_attempt_number: Mapped[int | None] = mapped_column(
        SmallInteger
    )
    next_dispatch_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    auto_renew_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    consent_version: Mapped[str | None] = mapped_column(String(64))
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    save_payment_method_requested: Mapped[bool | None] = mapped_column(Boolean)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    first_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    provider_idempotency_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    next_reconcile_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reconcile_lease_token: Mapped[str | None] = mapped_column(String(64))
    reconcile_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    provider_payment_id: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checkout_url: Mapped[str | None] = mapped_column(String(2048))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"
    __table_args__ = (UniqueConstraint("provider", "provider_event_id", name="uq_payment_event"),)

    id: Mapped[UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    merchant_account_fingerprint: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="!" * 64,
        server_default=text("'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!'"),
    )
    payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL"), index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FunnelEvent(Base):
    __tablename__ = "funnel_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_funnel_events_event_key"),
        Index(
            "ix_funnel_events_journey_type_occurred",
            "journey_id",
            "event_type",
            "occurred_at",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    journey_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "funnel_journeys.id",
            name="fk_funnel_events_journey_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    anonymous_draft_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("anonymous_drafts.id", ondelete="SET NULL")
    )
    oauth_state_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("oauth_states.id", ondelete="SET NULL")
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    project_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_runs.id", ondelete="SET NULL"), index=True
    )
    artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("generation_artifacts.id", ondelete="SET NULL")
    )
    payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL")
    )
    publication_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("publications.id", ondelete="SET NULL")
    )
    campaign_source: Mapped[str | None] = mapped_column(String(255))
    campaign_medium: Mapped[str | None] = mapped_column(String(255))
    campaign_name: Mapped[str | None] = mapped_column(String(255))
    campaign_term: Mapped[str | None] = mapped_column(String(255))
    campaign_content: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "project_id",
            name="uq_publication_project_membership",
        ),
        ForeignKeyConstraint(
            ["id", "active_release_id"],
            ["publication_releases.publication_id", "publication_releases.id"],
            name="fk_publications_active_release_membership",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), unique=True, nullable=False)
    journey_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "funnel_journeys.id",
            name="fk_publications_journey_id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    stable_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    active_release_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "publication_releases.id",
            ondelete="SET NULL",
            name="fk_publications_active_release_id",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        )
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PublicationRelease(Base):
    __tablename__ = "publication_releases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["publication_id", "project_id"],
            ["publications.id", "publications.project_id"],
            name="fk_publication_releases_publication_membership",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "project_version_id"],
            ["project_versions.project_id", "project_versions.id"],
            name="fk_publication_releases_project_version_membership",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        UniqueConstraint(
            "publication_id",
            "id",
            name="uq_publication_release_membership",
        ),
        Index(
            "uq_publication_release_legacy_artifact",
            "publication_id",
            "artifact_id",
            unique=True,
            postgresql_where=text("project_version_id IS NULL"),
            sqlite_where=text("project_version_id IS NULL"),
        ),
        Index(
            "uq_publication_release_project_version",
            "publication_id",
            "project_version_id",
            unique=True,
            postgresql_where=text("project_version_id IS NOT NULL"),
            sqlite_where=text("project_version_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    publication_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    project_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("generation_artifacts.id", ondelete="RESTRICT"), nullable=False)
    project_version_id: Mapped[UUID | None] = mapped_column()
    previous_release_id: Mapped[UUID | None] = mapped_column(ForeignKey("publication_releases.id", ondelete="SET NULL"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
