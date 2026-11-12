"""复核结论绑定依据的 service 层生命周期测试（SQLite 内存库）。

覆盖需求口径：
  ① 依据未变重复重算：已排除结论保持、不新增轮次/待办；
  ② 依据变化：旧结论轮次 superseded 归档（原因/依据保留）+ 新 pending 轮次 + 页面可对照；
  ③ 重算失败：事务回滚，已有结论和待办原样恢复，不丢记录；
  ④ 0002 之前的存量行：backfill 幂等回填第 1 代轮次；
  ⑤ 重新打开：已结论轮次可回退为待复核，历史留痕。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from src.constants.enums import PairReviewState, PairStatus
from src.models.asset import Asset
from src.models.asset_column import AssetColumn
from src.models.pair_review import PairReview
from src.models.similarity_pair import SimilarityPair
from src.models.user import User
from src.services import duplicate_service as svc

# 两张高度相似的表（mobile/msisdn 归一化后为同列）
SHARED_COLUMNS = [
    ("store_id", "int8"),
    ("sale_date", "date"),
    ("mobile_no", "varchar"),
    ("amount", "numeric"),
    ("channel_code", "varchar"),
]
NEAR_DUPLICATE_COLUMNS = [
    ("store_id", "int8"),
    ("sale_date", "date"),
    ("msisdn", "varchar"),
    ("amount", "numeric"),
    ("channel", "varchar"),
]
# 第三张表保证候选环境不为单点
OTHER_COLUMNS = [("cust_id", "int8"), ("cust_name", "varchar"), ("level", "varchar")]


def _make_actor(db) -> User:
    user = User(
        email="admin@catmap.demo",
        display_name="管理员",
        role="admin",
        password_hash="x",
    )
    db.add(user)
    db.flush()
    return user


def _make_asset(db, key: str, columns: list[tuple[str, str]], *, row_count: int = 100) -> Asset:
    asset = Asset(
        asset_key=key,
        schema_name="ods",
        table_name=key,
        layer="ods",
        domain="retail",
        owner_name="治理组",
        description="",
        row_count=row_count,
        column_count=len(columns),
        source_kind="sample_table",
    )
    db.add(asset)
    db.flush()
    for ordinal, (name, dtype) in enumerate(columns, start=1):
        db.add(
            AssetColumn(
                asset_id=asset.id,
                ordinal=ordinal,
                column_name=name,
                data_type=dtype,
                nullable=True,
                comment="",
            )
        )
    db.flush()
    return asset


def _first_pair(db) -> SimilarityPair:
    return db.scalars(select(SimilarityPair).order_by(SimilarityPair.score.desc())).first()


def _reviews(db, pair: SimilarityPair) -> list[PairReview]:
    return list(
        db.scalars(
            select(PairReview).where(PairReview.pair_id == pair.id).order_by(PairReview.generation)
        ).all()
    )


def test_recompute_creates_pending_review_with_basis(db_session):
    actor = _make_actor(db_session)
    _make_asset(db_session, "ods_sale_a", SHARED_COLUMNS)
    _make_asset(db_session, "ods_sale_b", NEAR_DUPLICATE_COLUMNS)
    _make_asset(db_session, "ods_cust", OTHER_COLUMNS)

    result = svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    assert result["created"] >= 1
    pair = _first_pair(db_session)
    assert pair.status == PairStatus.RECOMMENDED.value
    assert pair.basis and pair.basis_hash
    reviews = _reviews(db_session, pair)
    assert len(reviews) == 1
    assert reviews[0].state == PairReviewState.PENDING.value
    assert reviews[0].basis_hash == pair.basis_hash
    assert str(pair.active_review_id) == str(reviews[0].id)


def test_unchanged_basis_keeps_dismissed_and_adds_no_records(db_session):
    actor = _make_actor(db_session)
    _make_asset(db_session, "ods_sale_a", SHARED_COLUMNS)
    _make_asset(db_session, "ods_sale_b", NEAR_DUPLICATE_COLUMNS)
    _make_asset(db_session, "ods_cust", OTHER_COLUMNS)
    svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    pair = _first_pair(db_session)

    svc.dismiss(db_session, str(pair.id), "口径不同，非重复", actor)
    db_session.expire_all()
    pair = db_session.get(SimilarityPair, pair.id)
    assert pair.status == PairStatus.DISMISSED.value

    # 依据未变再次重算：结论保持、轮次不新增、待办不出现
    second = svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    assert second["reopened_pairs"] == 0
    assert second["superseded_pairs"] == 0
    assert second["unchanged_decisions"] >= 1
    db_session.expire_all()
    pair = db_session.get(SimilarityPair, pair.id)
    assert pair.status == PairStatus.DISMISSED.value
    reviews = _reviews(db_session, pair)
    assert len(reviews) == 1
    assert reviews[0].state == PairReviewState.DISMISSED.value
    assert reviews[0].review_note == "口径不同，非重复"
    listing = svc.list_pairs(db_session, limit=50)
    assert all(item["id"] != str(pair.id) for item in listing["items"])


def test_changed_basis_archives_old_decision_and_opens_new_review(db_session):
    actor = _make_actor(db_session)
    asset_a = _make_asset(db_session, "ods_sale_a", SHARED_COLUMNS)
    _make_asset(db_session, "ods_sale_b", NEAR_DUPLICATE_COLUMNS)
    _make_asset(db_session, "ods_cust", OTHER_COLUMNS)
    svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    pair = _first_pair(db_session)
    pair_id = pair.id
    svc.dismiss(db_session, str(pair_id), "口径不同，非重复", actor)

    # 表结构变化：A 表新增一个业务列
    db_session.add(
        AssetColumn(
            asset_id=asset_a.id,
            ordinal=len(SHARED_COLUMNS) + 1,
            column_name="promo_flag",
            data_type="boolean",
            nullable=True,
            comment="",
        )
    )
    asset_a.column_count = len(SHARED_COLUMNS) + 1
    db_session.flush()

    result = svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    assert result["superseded_pairs"] >= 1
    assert result["reopened_pairs"] >= 1

    db_session.expire_all()
    pair = db_session.get(SimilarityPair, pair_id)
    assert pair.status == PairStatus.PENDING_REVIEW.value
    reviews = _reviews(db_session, pair)
    assert len(reviews) == 2
    old_review, new_review = reviews
    assert old_review.generation == 1
    assert old_review.state == PairReviewState.SUPERSEDED.value
    # 旧结论原因与当时依据完整保留
    assert old_review.review_note == "口径不同，非重复"
    assert old_review.reviewed_by == "admin@catmap.demo"
    assert old_review.basis and old_review.basis_hash
    assert new_review.state == PairReviewState.PENDING.value
    assert new_review.generation == 2
    assert new_review.basis_hash == pair.basis_hash
    assert new_review.basis_hash != old_review.basis_hash
    # 前后依据对照已回填
    assert old_review.basis_change and old_review.basis_change["changed"] is True
    assert any("promo_flag" in sig for sig in old_review.basis_change["left_columns_added"])
    assert str(pair.active_review_id) == str(new_review.id)

    # 详情接口带出多轮台账
    detail = svc.get_pair(db_session, str(pair_id))
    assert len(detail["reviews"]) == 2
    assert detail["active_review"]["generation"] == 2

    # 依据未变再重算：新待办不重复生成
    svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    db_session.expire_all()
    assert len(_reviews(db_session, db_session.get(SimilarityPair, pair_id))) == 2


def test_recompute_failure_rolls_back_and_preserves_records(db_session, monkeypatch):
    actor = _make_actor(db_session)
    _make_asset(db_session, "ods_sale_a", SHARED_COLUMNS)
    _make_asset(db_session, "ods_sale_b", NEAR_DUPLICATE_COLUMNS)
    _make_asset(db_session, "ods_cust", OTHER_COLUMNS)
    svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    pair = _first_pair(db_session)
    svc.dismiss(db_session, str(pair.id), "口径不同，非重复", actor)
    pair_id = pair.id
    snapshot_hash = pair.basis_hash
    # 模拟生产：前序请求已成功提交，失败重算的回滚只应撤销本次事务
    db_session.commit()

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated recompute crash")

    monkeypatch.setattr(svc.similarity_engine, "rank_table_pairs", _boom)
    with pytest.raises(RuntimeError, match="simulated recompute crash"):
        svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    db_session.rollback()

    # 失败后已有结论与依据原样可查，没有任何半写入的轮次
    db_session.expire_all()
    pair = db_session.get(SimilarityPair, pair_id)
    assert pair.status == PairStatus.DISMISSED.value
    assert pair.basis_hash == snapshot_hash
    reviews = _reviews(db_session, pair)
    assert len(reviews) == 1
    assert reviews[0].state == PairReviewState.DISMISSED.value


def test_backfill_legacy_pair_is_idempotent(db_session):
    actor = _make_actor(db_session)
    _make_asset(db_session, "ods_sale_a", SHARED_COLUMNS)
    _make_asset(db_session, "ods_sale_b", NEAR_DUPLICATE_COLUMNS)
    # 手工模拟 0002 之前的存量行：无 basis、无 active_review
    legacy = SimilarityPair(
        left_asset_id=uuid.uuid4(),
        right_asset_id=uuid.uuid4(),
        score=0.8,
        jaccard=0.8,
        structure_score=0.8,
        status=PairStatus.DISMISSED.value,
        suggestion={"dismiss_note": "历史排除原因", "dismissed_by": "admin@catmap.demo"},
    )
    # 用真实资产 id 避免外键问题
    assets = db_session.scalars(select(Asset).order_by(Asset.asset_key)).all()
    legacy.left_asset_id, legacy.right_asset_id = tuple(sorted((assets[0].id, assets[1].id)))
    db_session.add(legacy)
    db_session.flush()

    first = svc.backfill_legacy_reviews(db_session)
    second = svc.backfill_legacy_reviews(db_session)
    assert first == 1 and second == 0  # 幂等
    db_session.expire_all()
    pair = db_session.get(SimilarityPair, legacy.id)
    reviews = _reviews(db_session, pair)
    assert len(reviews) == 1
    assert reviews[0].generation == 1
    assert reviews[0].state == PairReviewState.DISMISSED.value
    assert reviews[0].review_note == "历史排除原因"
    assert str(pair.active_review_id) == str(reviews[0].id)

    # 回填后首次重算（带依据）：历史空依据视为依据变化，生成新待办而不丢旧结论
    result = svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    assert result["reopened_pairs"] >= 1


def test_reopen_returns_decided_review_to_pending(db_session):
    actor = _make_actor(db_session)
    _make_asset(db_session, "ods_sale_a", SHARED_COLUMNS)
    _make_asset(db_session, "ods_sale_b", NEAR_DUPLICATE_COLUMNS)
    _make_asset(db_session, "ods_cust", OTHER_COLUMNS)
    svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    pair = _first_pair(db_session)
    svc.dismiss(db_session, str(pair.id), "口径不同，非重复", actor)

    result = svc.reopen(db_session, str(pair.id), "重新核对", actor)
    assert result["status"] == PairStatus.RECOMMENDED.value
    db_session.expire_all()
    pair = db_session.get(SimilarityPair, pair.id)
    review = db_session.get(PairReview, pair.active_review_id)
    assert review.state == PairReviewState.PENDING.value
    # 重新打开后可以再次排除，且不新增轮次
    svc.dismiss(db_session, str(pair.id), "二次确认仍非重复", actor)
    db_session.expire_all()
    assert len(_reviews(db_session, db_session.get(SimilarityPair, pair.id))) == 1


def test_dismiss_conflict_when_review_already_decided(db_session):
    from src.utils.errors import AppError

    actor = _make_actor(db_session)
    _make_asset(db_session, "ods_sale_a", SHARED_COLUMNS)
    _make_asset(db_session, "ods_sale_b", NEAR_DUPLICATE_COLUMNS)
    _make_asset(db_session, "ods_cust", OTHER_COLUMNS)
    svc.recompute(db_session, actor, top_n=6, min_score=0.1)
    pair = _first_pair(db_session)
    svc.dismiss(db_session, str(pair.id), "口径不同", actor)

    with pytest.raises(AppError):
        svc.dismiss(db_session, str(pair.id), "重复排除", actor)
