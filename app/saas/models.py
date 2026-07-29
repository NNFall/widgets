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
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnonymousDraft(Base):
    __tablename__ = "anonymous_drafts"

    id: Mapped[UUID] = _uuid_pk()
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    brief: Mapped[str | None] = mapped_column(Text)
    campaign: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    claim_token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[UUID] = _uuid_pk()
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    brief: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="draft")
    active_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    active_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class GenerationRun(Base):
    __tablename__ = "generation_runs"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key", name="uq_project_run_idempotency"),)

    id: Mapped[UUID] = _uuid_pk()
    project_id: Mapped[UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(50), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_stage: Mapped[str | None] = mapped_column(String(64))
    last_completed_stage: Mapped[str | None] = mapped_column(String(64))
    next_event_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    __table_args__ = (UniqueConstraint("run_id", "revision", name="uq_run_artifact_revision"),)

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

    id: Mapped[UUID] = _uuid_pk()
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("generation_runs.id", ondelete="SET NULL"), index=True)
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
    latency_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    cost_microusd: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
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


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status <> 'active' OR (current_period_start IS NOT NULL AND "
            "current_period_end IS NOT NULL AND current_period_end > current_period_start)",
            name="ck_subscription_finite_period",
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
    payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL")
    )
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    merchant_account_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
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
    )

    id: Mapped[UUID] = _uuid_pk()
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
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
        UniqueConstraint(
            "publication_id",
            "artifact_id",
            name="uq_publication_release_artifact",
        ),
        UniqueConstraint(
            "publication_id",
            "id",
            name="uq_publication_release_membership",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    publication_id: Mapped[UUID] = mapped_column(ForeignKey("publications.id", ondelete="CASCADE"), nullable=False, index=True)
    artifact_id: Mapped[UUID] = mapped_column(ForeignKey("generation_artifacts.id", ondelete="RESTRICT"), nullable=False)
    previous_release_id: Mapped[UUID | None] = mapped_column(ForeignKey("publication_releases.id", ondelete="SET NULL"))
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
