from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_02"
down_revision: str | None = "20260904_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduler_leases",
        sa.Column("lease_name", sa.String(length=160), nullable=False),
        sa.Column("owner_instance_id", sa.String(length=120), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("renewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("release_sha", sa.String(length=80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("lease_name"),
    )
    op.create_index(
        "ix_scheduler_leases_owner_instance_id",
        "scheduler_leases",
        ["owner_instance_id"],
    )
    op.create_index("ix_scheduler_leases_expires_at", "scheduler_leases", ["expires_at"])
    op.create_table(
        "scheduler_heartbeats",
        sa.Column("lease_name", sa.String(length=160), nullable=False),
        sa.Column("owner_instance_id", sa.String(length=120), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("release_sha", sa.String(length=80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("lease_name", "owner_instance_id"),
    )
    op.create_index("ix_scheduler_heartbeats_observed_at", "scheduler_heartbeats", ["observed_at"])
    op.create_table(
        "agent_cycles",
        sa.Column("cycle_id", sa.String(length=120), nullable=False),
        sa.Column("lease_name", sa.String(length=160), nullable=False),
        sa.Column("workspace_id", sa.String(length=120), nullable=False),
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("agent_id", sa.String(length=120), nullable=False),
        sa.Column("owner_instance_id", sa.String(length=120), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("release_sha", sa.String(length=80), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("market_session_state", sa.String(length=40), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=80), nullable=True),
        sa.Column("entry_state", sa.String(length=80), nullable=True),
        sa.Column("safe_error_type", sa.String(length=120), nullable=True),
        sa.Column("next_scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("cycle_id"),
    )
    op.create_index("ix_agent_cycles_lease_name", "agent_cycles", ["lease_name"])
    op.create_index("ix_agent_cycles_workspace_id", "agent_cycles", ["workspace_id"])
    op.create_index("ix_agent_cycles_account_id", "agent_cycles", ["account_id"])
    op.create_index("ix_agent_cycles_agent_id", "agent_cycles", ["agent_id"])
    op.create_index("ix_agent_cycles_owner_instance_id", "agent_cycles", ["owner_instance_id"])
    op.create_index("ix_agent_cycles_started_at", "agent_cycles", ["started_at"])
    op.create_index("ix_agent_cycles_state", "agent_cycles", ["state"])
    op.create_table(
        "runtime_controls",
        sa.Column("control_id", sa.String(length=120), nullable=False),
        sa.Column("scope_type", sa.String(length=40), nullable=False),
        sa.Column("scope_id", sa.String(length=160), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("policy_version", sa.String(length=80), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovery_condition", sa.String(length=500), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("control_id"),
    )
    op.create_index("ix_runtime_controls_scope_type", "runtime_controls", ["scope_type"])
    op.create_index("ix_runtime_controls_scope_id", "runtime_controls", ["scope_id"])
    op.create_index("ix_runtime_controls_state", "runtime_controls", ["state"])
    op.create_index("ix_runtime_controls_effective_at", "runtime_controls", ["effective_at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_controls_effective_at", table_name="runtime_controls")
    op.drop_index("ix_runtime_controls_state", table_name="runtime_controls")
    op.drop_index("ix_runtime_controls_scope_id", table_name="runtime_controls")
    op.drop_index("ix_runtime_controls_scope_type", table_name="runtime_controls")
    op.drop_table("runtime_controls")
    op.drop_index("ix_agent_cycles_state", table_name="agent_cycles")
    op.drop_index("ix_agent_cycles_started_at", table_name="agent_cycles")
    op.drop_index("ix_agent_cycles_owner_instance_id", table_name="agent_cycles")
    op.drop_index("ix_agent_cycles_agent_id", table_name="agent_cycles")
    op.drop_index("ix_agent_cycles_account_id", table_name="agent_cycles")
    op.drop_index("ix_agent_cycles_workspace_id", table_name="agent_cycles")
    op.drop_index("ix_agent_cycles_lease_name", table_name="agent_cycles")
    op.drop_table("agent_cycles")
    op.drop_index("ix_scheduler_heartbeats_observed_at", table_name="scheduler_heartbeats")
    op.drop_table("scheduler_heartbeats")
    op.drop_index("ix_scheduler_leases_expires_at", table_name="scheduler_leases")
    op.drop_index("ix_scheduler_leases_owner_instance_id", table_name="scheduler_leases")
    op.drop_table("scheduler_leases")
