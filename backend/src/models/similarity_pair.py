"""相似表对：score 落库 + 合并建议 JSON + 人工状态 + 当前复核依据快照。

复核依据（basis）与人工结论的绑定规则见 services/duplicate_service.py：
  - basis / basis_hash：本轮落库依据及其确定性哈希，人工结论绑定的是结论当时的哈希；
  - active_review_id：当前生效的复核轮次（pair_review 主键），为空表示历史数据尚未回填；
  - 依据变化时旧轮次置 superseded 归档、生成新轮次，本行始终保留最新依据不丢记录。
"""
from __future__ import annotations

import uuid

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class SimilarityPair(Base):
    __tablename__ = "similarity_pair"
    __table_args__ = (
        UniqueConstraint("left_asset_id", "right_asset_id", name="uq_similarity_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    left_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    right_asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("asset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    jaccard: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    structure_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    method: Mapped[str] = mapped_column(String(40), nullable=False, default="minhash-lsh")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="recommended", index=True)
    suggestion: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # 当前一轮计算的复核依据快照与哈希（人工结论绑定结论轮次里的哈希，见 pair_review）
    basis: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    basis_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 当前生效复核轮次；不加 FK 约束以避免与 pair_review.pair_id 的循环外键
    active_review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    computed_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
