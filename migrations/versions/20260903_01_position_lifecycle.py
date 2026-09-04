from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_01"
down_revision: str | None = "20260830_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "managed_option_positions",
        sa.Column("position_id", sa.String(length=120), nullable=False),
        sa.Column("opening_intent_id", sa.String(length=80), nullable=False),
        sa.Column("close_intent_id", sa.String(length=80), nullable=True),
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("underlying", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("position_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["opening_intent_id"], ["trade_intents.intent_id"]),
        sa.ForeignKeyConstraint(["close_intent_id"], ["trade_intents.intent_id"]),
        sa.PrimaryKeyConstraint("position_id"),
        sa.UniqueConstraint("opening_intent_id"),
        sa.UniqueConstraint("close_intent_id"),
    )
    op.create_index(
        "ix_managed_option_positions_account_id", "managed_option_positions", ["account_id"]
    )
    op.create_index(
        "ix_managed_option_positions_underlying", "managed_option_positions", ["underlying"]
    )
    op.create_index("ix_managed_option_positions_state", "managed_option_positions", ["state"])
    op.create_table(
        "position_transitions",
        sa.Column("transition_id", sa.String(length=120), nullable=False),
        sa.Column("position_id", sa.String(length=120), nullable=False),
        sa.Column("event_identity", sa.String(length=160), nullable=False),
        sa.Column("previous_transition_hash", sa.String(length=64), nullable=False),
        sa.Column("transition_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["position_id"], ["managed_option_positions.position_id"]),
        sa.PrimaryKeyConstraint("transition_id"),
        sa.UniqueConstraint("position_id", "event_identity"),
    )
    op.create_index("ix_position_transitions_position_id", "position_transitions", ["position_id"])
    op.create_table(
        "broker_activity_cursors",
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("activity_type", sa.String(length=20), nullable=False),
        sa.Column("cursor", sa.String(length=500), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "activity_type"),
    )


def downgrade() -> None:
    op.drop_table("broker_activity_cursors")
    op.drop_index("ix_position_transitions_position_id", table_name="position_transitions")
    op.drop_table("position_transitions")
    op.drop_index("ix_managed_option_positions_state", table_name="managed_option_positions")
    op.drop_index("ix_managed_option_positions_underlying", table_name="managed_option_positions")
    op.drop_index("ix_managed_option_positions_account_id", table_name="managed_option_positions")
    op.drop_table("managed_option_positions")
