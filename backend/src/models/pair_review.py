"""相似表复核轮次：把人工复核结论与「当时依据」绑定，支持多轮归档审计。

一条 SimilarityPair 下可有多个 PairReview（generation 递增）：
  - pending：待复核项；人工 dismiss（判非重复）或 merged（确认合并）后成为结论轮次；
  - 依据变化的重算：结论轮次置 superseded（保留结论、原因、依据快照与时间），
    同时新建一条 pending 轮次作为新的待复核项，页面可对照前后依据；
  - 依据未变的重算：不动任何轮次，不新增记录，已排除结论持续生效。
"""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class PairReview(Base):
    __tablename__ = "pair_review"
    __table_args__ = (
        UniqueConstraint("pair_id", "generation", name="uq_pair_review_generation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pair_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("similarity_pair.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)

    # 该轮依据快照与确定性哈希——结论绑定的就是这份依据
    basis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    basis_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 与下一轮依据的对照（依据变化归档时回填，含列增删 / 行数差 / 指标差）
    basis_change: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # 人工结论（state=dismissed/merged 时填写）
    review_note: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    reviewed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
