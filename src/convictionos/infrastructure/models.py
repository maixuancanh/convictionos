from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TradeIntentRow(Base):
    __tablename__ = "trade_intents"

    intent_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    account_id: Mapped[str] = mapped_column(String(120), index=True)
    operation_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    broker_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class RiskReservationRow(Base):
    __tablename__ = "risk_reservations"
    __table_args__ = (UniqueConstraint("intent_id"),)

    reservation_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("trade_intents.intent_id"))
    account_id: Mapped[str] = mapped_column(String(120), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    status: Mapped[str] = mapped_column(String(20), default="active")


class OutboxRow(Base):
    __tablename__ = "outbox"

    event_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    aggregate_id: Mapped[str] = mapped_column(String(120), index=True)
    topic: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DecisionReceiptRow(Base):
    __tablename__ = "decision_receipts"

    receipt_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("trade_intents.intent_id"), unique=True)
    previous_receipt_hash: Mapped[str] = mapped_column(String(64))
    receipt_hash: Mapped[str] = mapped_column(String(64), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IntelligenceArtifactRow(Base):
    __tablename__ = "intelligence_artifacts"

    artifact_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True)
    artifact_hash: Mapped[str] = mapped_column(String(64), unique=True)
    evidence_snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EligibilityManifestRow(Base):
    __tablename__ = "eligibility_manifests"

    manifest_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    manifest_hash: Mapped[str] = mapped_column(String(64), unique=True)
    workspace_id: Mapped[str] = mapped_column(String(120), index=True)
    account_id: Mapped[str] = mapped_column(String(120), index=True)
    outcome: Mapped[str] = mapped_column(String(40), index=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    baseline_manifest_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class ManagedOptionPositionRow(Base):
    __tablename__ = "managed_option_positions"

    position_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    opening_intent_id: Mapped[str] = mapped_column(
        ForeignKey("trade_intents.intent_id"), unique=True
    )
    close_intent_id: Mapped[str | None] = mapped_column(
        ForeignKey("trade_intents.intent_id"), unique=True, nullable=True
    )
    account_id: Mapped[str] = mapped_column(String(120), index=True)
    underlying: Mapped[str] = mapped_column(String(20), index=True)
    state: Mapped[str] = mapped_column(String(40), index=True)
    position_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionTransitionRow(Base):
    __tablename__ = "position_transitions"
    __table_args__ = (UniqueConstraint("position_id", "event_identity"),)

    transition_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    position_id: Mapped[str] = mapped_column(
        ForeignKey("managed_option_positions.position_id"), index=True
    )
    event_identity: Mapped[str] = mapped_column(String(160))
    previous_transition_hash: Mapped[str] = mapped_column(String(64))
    transition_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BrokerActivityCursorRow(Base):
    __tablename__ = "broker_activity_cursors"

    account_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    activity_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    cursor: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SchedulerLeaseRow(Base):
    __tablename__ = "scheduler_leases"

    lease_name: Mapped[str] = mapped_column(String(160), primary_key=True)
    owner_instance_id: Mapped[str] = mapped_column(String(120), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    renewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    release_sha: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)


class SchedulerHeartbeatRow(Base):
    __tablename__ = "scheduler_heartbeats"

    lease_name: Mapped[str] = mapped_column(String(160), primary_key=True)
    owner_instance_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    release_sha: Mapped[str] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AgentCycleRow(Base):
    __tablename__ = "agent_cycles"

    cycle_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    lease_name: Mapped[str] = mapped_column(String(160), index=True)
    workspace_id: Mapped[str] = mapped_column(String(120), index=True)
    account_id: Mapped[str] = mapped_column(String(120), index=True)
    agent_id: Mapped[str] = mapped_column(String(120), index=True)
    owner_instance_id: Mapped[str] = mapped_column(String(120), index=True)
    fencing_token: Mapped[int] = mapped_column(Integer)
    release_sha: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(40), index=True)
    market_session_state: Mapped[str] = mapped_column(String(40))
    lifecycle_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    entry_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    safe_error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    next_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RuntimeControlRow(Base):
    __tablename__ = "runtime_controls"

    control_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(40), index=True)
    scope_id: Mapped[str] = mapped_column(String(160), index=True)
    state: Mapped[str] = mapped_column(String(40), index=True)
    reason: Mapped[str] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(120))
    policy_version: Mapped[str] = mapped_column(String(80))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recovery_condition: Mapped[str | None] = mapped_column(String(500), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class RuntimeIncidentRow(Base):
    __tablename__ = "runtime_incidents"

    incident_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    public_id: Mapped[str] = mapped_column(String(80), unique=True)
    workspace_id: Mapped[str] = mapped_column(String(120), index=True)
    account_id: Mapped[str] = mapped_column(String(120), index=True)
    agent_id: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(40), index=True)
    state: Mapped[str] = mapped_column(String(40), index=True)
    trigger_evidence_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(String(500))
    recovery_condition: Mapped[str] = mapped_column(String(500))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recovery_evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    runtime_control_id: Mapped[str | None] = mapped_column(
        ForeignKey("runtime_controls.control_id"), nullable=True
    )


class RuntimeIncidentEventRow(Base):
    __tablename__ = "runtime_incident_events"

    event_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_incidents.incident_id"), index=True
    )
    actor: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
