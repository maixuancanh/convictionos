from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260829_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trade_intents",
        sa.Column("intent_id", sa.String(length=80), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("operation_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("broker_order_id", sa.String(length=120), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("intent_id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_trade_intents_account_id", "trade_intents", ["account_id"])
    op.create_table(
        "risk_reservations",
        sa.Column("reservation_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("intent_id", sa.String(length=80), nullable=False),
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("amount", sa.Numeric(precision=20, scale=8), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["intent_id"], ["trade_intents.intent_id"]),
        sa.PrimaryKeyConstraint("reservation_id"),
        sa.UniqueConstraint("intent_id"),
    )
    op.create_index("ix_risk_reservations_account_id", "risk_reservations", ["account_id"])
    op.create_table(
        "outbox",
        sa.Column("event_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("aggregate_id", sa.String(length=120), nullable=False),
        sa.Column("topic", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_outbox_aggregate_id", "outbox", ["aggregate_id"])
    op.create_table(
        "decision_receipts",
        sa.Column("receipt_id", sa.String(length=120), nullable=False),
        sa.Column("intent_id", sa.String(length=80), nullable=False),
        sa.Column("previous_receipt_hash", sa.String(length=64), nullable=False),
        sa.Column("receipt_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["intent_id"], ["trade_intents.intent_id"]),
        sa.PrimaryKeyConstraint("receipt_id"),
        sa.UniqueConstraint("intent_id"),
        sa.UniqueConstraint("receipt_hash"),
    )


def downgrade() -> None:
    op.drop_table("decision_receipts")
    op.drop_index("ix_outbox_aggregate_id", table_name="outbox")
    op.drop_table("outbox")
    op.drop_index("ix_risk_reservations_account_id", table_name="risk_reservations")
    op.drop_table("risk_reservations")
    op.drop_index("ix_trade_intents_account_id", table_name="trade_intents")
    op.drop_table("trade_intents")
