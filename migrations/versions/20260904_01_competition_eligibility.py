from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260904_01"
down_revision: str | None = "20260903_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eligibility_manifests",
        sa.Column("manifest_id", sa.String(length=120), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=120), nullable=False),
        sa.Column("account_id", sa.String(length=120), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("baseline_manifest_id", sa.String(length=120), nullable=True),
        sa.PrimaryKeyConstraint("manifest_id"),
        sa.UniqueConstraint("manifest_hash"),
    )
    op.create_index(
        "ix_eligibility_manifests_workspace_id",
        "eligibility_manifests",
        ["workspace_id"],
    )
    op.create_index(
        "ix_eligibility_manifests_account_id",
        "eligibility_manifests",
        ["account_id"],
    )
    op.create_index(
        "ix_eligibility_manifests_outcome",
        "eligibility_manifests",
        ["outcome"],
    )
    op.create_index(
        "ix_eligibility_manifests_verified_at",
        "eligibility_manifests",
        ["verified_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_eligibility_manifests_verified_at", table_name="eligibility_manifests")
    op.drop_index("ix_eligibility_manifests_outcome", table_name="eligibility_manifests")
    op.drop_index("ix_eligibility_manifests_account_id", table_name="eligibility_manifests")
    op.drop_index("ix_eligibility_manifests_workspace_id", table_name="eligibility_manifests")
    op.drop_table("eligibility_manifests")
