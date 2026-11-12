"""复核结论绑定依据：新增 pair_review 表 + similarity_pair 依据列，并回填存量轮次。

Revision ID: 0002_pair_review_basis
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.services.duplicate_service import backfill_legacy_reviews

revision = "0002_pair_review_basis"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "pair_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "pair_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("similarity_pair.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("basis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("basis_hash", sa.String(length=64), nullable=True),
        sa.Column("basis_change", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("review_note", sa.String(length=200), nullable=True),
        sa.Column("reviewed_by", sa.String(length=160), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("pair_id", "generation", name="uq_pair_review_generation"),
    )
    op.create_index("ix_pair_review_pair_id", "pair_review", ["pair_id"])
    op.create_index("ix_pair_review_state", "pair_review", ["state"])
    op.create_index("ix_pair_review_basis_hash", "pair_review", ["basis_hash"])
    op.create_index("ix_pair_review_reviewed_by", "pair_review", ["reviewed_by"])

    op.add_column(
        "similarity_pair",
        sa.Column("basis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "similarity_pair",
        sa.Column("basis_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "similarity_pair",
        sa.Column("active_review_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_similarity_pair_basis_hash", "similarity_pair", ["basis_hash"])
    op.create_index(
        "ix_similarity_pair_active_review_id", "similarity_pair", ["active_review_id"]
    )

    # 存量结论回填为第 1 代复核轮次（dismissed 保留原原因 / 操作人）
    backfill_legacy_reviews(bind)


def downgrade() -> None:
    op.drop_index("ix_similarity_pair_active_review_id", table_name="similarity_pair")
    op.drop_index("ix_similarity_pair_basis_hash", table_name="similarity_pair")
    op.drop_column("similarity_pair", "active_review_id")
    op.drop_column("similarity_pair", "basis_hash")
    op.drop_column("similarity_pair", "basis")
    op.drop_index("ix_pair_review_reviewed_by", table_name="pair_review")
    op.drop_index("ix_pair_review_basis_hash", table_name="pair_review")
    op.drop_index("ix_pair_review_state", table_name="pair_review")
    op.drop_index("ix_pair_review_pair_id", table_name="pair_review")
    op.drop_table("pair_review")
