from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260904_03"
down_revision: str | None = "20260904_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_incidents",
        sa.Column("incident_id", sa.String(length=120), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("workspace_id", sa.String(length=120), nullable=False),
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("agent_id", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("trigger_evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("recovery_condition", sa.String(length=500), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=120), nullable=True),
        sa.Column("resolved_by", sa.String(length=120), nullable=True),
        sa.Column("recovery_evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("runtime_control_id", sa.String(length=120), nullable=True),
        sa.ForeignKeyConstraint(["runtime_control_id"], ["runtime_controls.control_id"]),
        sa.PrimaryKeyConstraint("incident_id"),
        sa.UniqueConstraint("public_id"),
    )
    for column in (
        "workspace_id",
        "account_id",
        "agent_id",
        "kind",
        "severity",
        "state",
        "opened_at",
    ):
        op.create_index(f"ix_runtime_incidents_{column}", "runtime_incidents", [column])
    op.create_table(
        "runtime_incident_events",
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("incident_id", sa.String(length=120), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["runtime_incidents.incident_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_runtime_incident_events_incident_id",
        "runtime_incident_events",
        ["incident_id"],
    )
    op.create_index(
        "ix_runtime_incident_events_event_type",
        "runtime_incident_events",
        ["event_type"],
    )
    op.create_index(
        "ix_runtime_incident_events_observed_at",
        "runtime_incident_events",
        ["observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_runtime_incident_events_observed_at", table_name="runtime_incident_events")
    op.drop_index("ix_runtime_incident_events_event_type", table_name="runtime_incident_events")
    op.drop_index("ix_runtime_incident_events_incident_id", table_name="runtime_incident_events")
    op.drop_table("runtime_incident_events")
    for column in reversed(
        (
            "workspace_id",
            "account_id",
            "agent_id",
            "kind",
            "severity",
            "state",
            "opened_at",
        )
    ):
        op.drop_index(f"ix_runtime_incidents_{column}", table_name="runtime_incidents")
    op.drop_table("runtime_incidents")
