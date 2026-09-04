from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260830_01"
down_revision: str | None = "20260829_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intelligence_artifacts",
        sa.Column("artifact_id", sa.String(length=120), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id"),
        sa.UniqueConstraint("identity_hash"),
        sa.UniqueConstraint("artifact_hash"),
    )
    op.create_index(
        "ix_intelligence_artifacts_evidence_snapshot_hash",
        "intelligence_artifacts",
        ["evidence_snapshot_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_intelligence_artifacts_evidence_snapshot_hash",
        table_name="intelligence_artifacts",
    )
    op.drop_table("intelligence_artifacts")
