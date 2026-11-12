"""相似表推荐服务（算法 B 的落库层）。

复核结论与依据绑定的生命周期：
  1. recompute 每轮把引擎依据快照（basis + basis_hash）随候选对落库，并为每个对
     维护 PairReview 复核轮次（pair_review 表）；
  2. 依据哈希未变：不新增任何轮次，人工 dismissed/merged 结论持续生效，待办不重复；
  3. 依据哈希变化：旧结论轮次置 superseded 归档（保留结论、原因、当时依据与前后
     对照 basis_change），同时生成一条 pending 新轮次，pair.status= pending_review；
  4. 重算在单事务内完成，异常整体回滚——已有结论与待办原样保留，不丢记录；
  5. 历史数据（0002 之前）由 backfill_legacy_reviews 幂等回填为第 1 代轮次。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from src.config.settings import settings
from src.constants import error_codes as codes
from src.constants.enums import PairReviewState, PairStatus
from src.models.asset import Asset
from src.models.pair_review import PairReview
from src.models.similarity_pair import SimilarityPair
from src.models.user import User
from src.schemas.common import asset_brief, iso
from src.services import audit_service, similarity_engine
from src.utils.errors import AppError

# 仍在待办队列中的 pair 状态
PENDING_PAIR_STATUSES = (PairStatus.RECOMMENDED.value, PairStatus.PENDING_REVIEW.value)
# 已形成人工结论的复核轮次状态
DECIDED_REVIEW_STATES = (PairReviewState.DISMISSED.value, PairReviewState.MERGED.value)


def _asset_with_columns(db: Session, asset_id: uuid.UUID) -> Asset | None:
    return db.scalars(
        select(Asset).options(selectinload(Asset.columns)).where(Asset.id == asset_id)
    ).first()


def _pair_key(left_id: uuid.UUID, right_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return tuple(sorted((left_id, right_id)))  # type: ignore[return-value]


# ---------------------------------------------------------- 历史数据回填 ----
def backfill_legacy_reviews(bind: Session | object) -> int:
    """把 0002 迁移前的 similarity_pair 存量行幂等回填为第 1 代 PairReview。

    迁移脚本传 Connection、服务层传 Session，均可用；已回填的对跳过。
    """
    session = bind if isinstance(bind, Session) else Session(bind=bind)  # type: ignore[arg-type]
    created = 0
    pairs = session.scalars(select(SimilarityPair)).all()
    for pair in pairs:
        if pair.active_review_id:
            continue
        if pair.status == PairStatus.DISMISSED.value:
            state = PairReviewState.DISMISSED.value
        elif pair.status == PairStatus.MERGED.value:
            state = PairReviewState.MERGED.value
        else:
            state = PairReviewState.PENDING.value
        suggestion = pair.suggestion or {}
        review = PairReview(
            pair_id=pair.id,
            generation=1,
            state=state,
            basis=pair.basis,
            basis_hash=pair.basis_hash,
            review_note=suggestion.get("dismiss_note"),
            reviewed_by=suggestion.get("dismissed_by"),
            reviewed_at=pair.updated_at if state in DECIDED_REVIEW_STATES else None,
        )
        session.add(review)
        session.flush()
        pair.active_review_id = review.id
        created += 1
    session.flush()
    return created


# ---------------------------------------------------------------- 重算 ----
def recompute(db: Session, actor: User, *, top_n: int | None = None, min_score: float | None = None) -> dict:
    # 兜底回填：即便迁移未跑（如 SQLite 测试库），也保证每个对都有复核轮次
    backfill_legacy_reviews(db)

    top_n = top_n or settings.similarity_top_n
    min_score = settings.similarity_min_score if min_score is None else min_score
    assets = db.scalars(select(Asset).options(selectinload(Asset.columns)).order_by(Asset.asset_key)).all()
    # 同会话内资产可能已被其它操作改动过（如新增列）；显式刷新列关系，保证依据反映最新结构
    for asset in assets:
        db.refresh(asset, ["columns", "row_count", "column_count"])
    payload = [
        {
            "asset_key": asset.asset_key,
            "table_name": asset.table_name,
            "layer": asset.layer,
            "row_count": asset.row_count,
            "id": asset.id,
            "columns": [
                {"column_name": column.column_name, "data_type": column.data_type}
                for column in asset.columns
            ],
        }
        for asset in assets
    ]
    # 纯计算在事务内完成；下方任何写库失败都会随请求回滚，旧结论与待办不受影响
    ranking = similarity_engine.rank_table_pairs(payload, top_n=top_n, min_score=min_score)

    existing = {
        _pair_key(pair.left_asset_id, pair.right_asset_id): pair
        for pair in db.scalars(select(SimilarityPair)).all()
    }
    asset_by_key = {asset.asset_key: asset for asset in assets}
    created = 0
    updated = 0
    reopened = 0
    superseded = 0
    unchanged_decisions = 0
    now = datetime.now(timezone.utc)

    for candidate in ranking["pairs"]:
        left_asset = asset_by_key[candidate["left_key"]]
        right_asset = asset_by_key[candidate["right_key"]]
        key = _pair_key(left_asset.id, right_asset.id)
        basis = candidate["basis"]
        basis_hash_value = candidate["basis_hash"]
        pair = existing.get(key)

        if pair is None:
            pair = SimilarityPair(
                left_asset_id=key[0],
                right_asset_id=key[1],
                score=candidate["score"],
                jaccard=candidate["jaccard"],
                structure_score=candidate["structure_score"],
                method=candidate["method"],
                status=PairStatus.RECOMMENDED.value,
                suggestion=candidate["suggestion"],
                basis=basis,
                basis_hash=basis_hash_value,
                computed_at=now,
            )
            db.add(pair)
            db.flush()
            review = PairReview(
                pair_id=pair.id,
                generation=1,
                state=PairReviewState.PENDING.value,
                basis=basis,
                basis_hash=basis_hash_value,
            )
            db.add(review)
            db.flush()
            pair.active_review_id = review.id
            created += 1
            continue

        # 已存在的对：指标与合并建议随本轮刷新（依据快照本身保存在轮次里，可审计）
        pair.score = candidate["score"]
        pair.jaccard = candidate["jaccard"]
        pair.structure_score = candidate["structure_score"]
        pair.method = candidate["method"]
        pair.suggestion = candidate["suggestion"]
        pair.computed_at = now
        updated += 1

        active = _get_active_review(db, pair)

        # ① 依据未变：结论/待办原样保留，不新增轮次、不新增待办
        if pair.basis_hash == basis_hash_value and pair.basis_hash is not None:
            if active and active.state in DECIDED_REVIEW_STATES:
                unchanged_decisions += 1
            continue

        # ② 依据变化（含历史空依据的首轮重算）
        pair.basis = basis
        pair.basis_hash = basis_hash_value

        if active is None:
            review = PairReview(
                pair_id=pair.id,
                generation=1,
                state=PairReviewState.PENDING.value,
                basis=basis,
                basis_hash=basis_hash_value,
            )
            db.add(review)
            db.flush()
            pair.active_review_id = review.id
            pair.status = PairStatus.RECOMMENDED.value
            reopened += 1
        elif active.state == PairReviewState.PENDING.value:
            # 本就待复核：直接把轮次依据刷新为最新，不制造重复待办
            active.basis = basis
            active.basis_hash = basis_hash_value
            reopened += 1
        else:
            # 已有结论且依据变化：旧轮次归档 + 生成新待复核项
            active.state = PairReviewState.SUPERSEDED.value
            active.basis_change = similarity_engine.diff_basis(active.basis, basis)
            next_generation = (
                db.scalar(
                    select(func.max(PairReview.generation)).where(PairReview.pair_id == pair.id)
                )
                or 0
            ) + 1
            review = PairReview(
                pair_id=pair.id,
                generation=next_generation,
                state=PairReviewState.PENDING.value,
                basis=basis,
                basis_hash=basis_hash_value,
            )
            db.add(review)
            db.flush()
            pair.active_review_id = review.id
            pair.status = PairStatus.PENDING_REVIEW.value
            superseded += 1
            reopened += 1

    db.flush()

    audit_service.record(
        db,
        actor_email=actor.email,
        actor_role=actor.role,
        action="duplicate.recompute",
        entity_type="similarity_pair",
        detail=(
            f"候选 {ranking['candidate_pairs']} 对，落库 top{top_n}，新增 {created} 更新 {updated}；"
            f"依据变化新待办 {reopened}（归档旧结论 {superseded}），同依据维持结论 {unchanged_decisions}"
        ),
    )
    recommendations = _recommendations(db)
    return {
        "computed_pairs": ranking["computed_pairs"],
        "candidate_pairs": ranking["candidate_pairs"],
        "eligible_assets": ranking["eligible_assets"],
        "skipped_assets": ranking["skipped_assets"],
        "persisted_top_n": len(ranking["pairs"]),
        "created": created,
        "updated": updated,
        "reopened_pairs": reopened,
        "superseded_pairs": superseded,
        "unchanged_decisions": unchanged_decisions,
        "recommendations": recommendations,
    }


def _get_active_review(db: Session, pair: SimilarityPair) -> PairReview | None:
    if pair.active_review_id:
        return db.get(PairReview, pair.active_review_id)
    return db.scalars(
        select(PairReview)
        .where(PairReview.pair_id == pair.id)
        .order_by(PairReview.generation.desc())
    ).first()


# ------------------------------------------------------------- 序列化 ----
def _review_payload(review: PairReview | None) -> dict | None:
    if review is None:
        return None
    return {
        "id": str(review.id),
        "generation": review.generation,
        "state": review.state,
        "basis_hash": review.basis_hash,
        "basis": review.basis,
        "basis_change": review.basis_change,
        "review_note": review.review_note,
        "reviewed_by": review.reviewed_by,
        "reviewed_at": iso(review.reviewed_at),
        "created_at": iso(review.created_at),
        "updated_at": iso(review.updated_at),
    }


def _pair_payload(db: Session, pair: SimilarityPair) -> dict:
    left = _asset_with_columns(db, pair.left_asset_id)
    right = _asset_with_columns(db, pair.right_asset_id)
    suggestion = pair.suggestion or {}
    active_review = _get_active_review(db, pair)
    return {
        "id": str(pair.id),
        "score": round(pair.score, 4),
        "jaccard": round(pair.jaccard, 4),
        "structure_score": round(pair.structure_score, 4),
        "method": pair.method,
        "status": pair.status,
        "verdict": suggestion.get("verdict", ""),
        "computed_at": iso(pair.computed_at),
        "basis_hash": pair.basis_hash,
        "basis": pair.basis,
        "active_review": _review_payload(active_review),
        "left": {
            **_asset_brief(left),
            "owner_name": left.owner_name,
            "source_kind": left.source_kind,
        },
        "right": {
            **_asset_brief(right),
            "owner_name": right.owner_name,
            "source_kind": right.source_kind,
        },
        "suggestion": suggestion,
    }


def _asset_brief(asset: Asset) -> dict:
    return asset_brief(asset)


def _recommendations(db: Session) -> list[dict]:
    """按分数倒序返回推荐对；dismissed（人工确认非重复且依据未变）不再推荐。"""
    pairs = db.scalars(
        select(SimilarityPair)
        .where(SimilarityPair.status != PairStatus.DISMISSED.value)
        .order_by(SimilarityPair.score.desc(), SimilarityPair.id)
    ).all()
    return [_pair_payload(db, pair) for pair in pairs]


def list_pairs(db: Session, *, limit: int = 5) -> dict:
    all_pairs = db.scalars(
        select(SimilarityPair).order_by(SimilarityPair.score.desc(), SimilarityPair.id)
    ).all()
    recommended_models = [pair for pair in all_pairs if pair.status in PENDING_PAIR_STATUSES]
    dismissed = [pair for pair in all_pairs if pair.status == PairStatus.DISMISSED.value]
    pending_review = [
        pair for pair in all_pairs if pair.status == PairStatus.PENDING_REVIEW.value
    ]
    return {
        "items": [_pair_payload(db, pair) for pair in recommended_models[:limit]],
        "total": len(all_pairs),
        "recommended_total": len(recommended_models),
        "dismissed_total": len(dismissed),
        "pending_review_total": len(pending_review),
        "true_duplicate_total": sum(1 for pair in recommended_models if pair.score >= 0.85),
        "top_n": limit,
    }


def get_pair(db: Session, pair_id: str) -> dict:
    pair = db.get(SimilarityPair, pair_id)
    if pair is None:
        raise AppError(codes.NOT_FOUND, "相似表对不存在", f"pair_id={pair_id}")
    left = _asset_with_columns(db, pair.left_asset_id)
    right = _asset_with_columns(db, pair.right_asset_id)
    payload = _pair_payload(db, pair)
    left_columns = [
        {
            "id": str(column.id),
            "column_name": column.column_name,
            "data_type": column.data_type,
            "inferred_type": column.inferred_type,
            "confidence": round(column.confidence or 0.0, 3),
        }
        for column in left.columns
    ]
    right_columns = [
        {
            "id": str(column.id),
            "column_name": column.column_name,
            "data_type": column.data_type,
            "inferred_type": column.inferred_type,
            "confidence": round(column.confidence or 0.0, 3),
        }
        for column in right.columns
    ]
    matched = (pair.suggestion or {}).get("merged_columns") or _matched_columns(left, right)
    reviews = db.scalars(
        select(PairReview)
        .where(PairReview.pair_id == pair.id)
        .order_by(PairReview.generation.asc())
    ).all()
    payload.update(
        {
            "left_columns": left_columns,
            "right_columns": right_columns,
            "diff": _diff_rows(left_columns, right_columns, matched),
            "reviews": [_review_payload(review) for review in reviews],
        }
    )
    return payload


def _matched_columns(left: Asset, right: Asset) -> list[dict]:
    _, matched = similarity_engine.structure_similarity(
        [{"column_name": column.column_name, "data_type": column.data_type} for column in left.columns],
        [{"column_name": column.column_name, "data_type": column.data_type} for column in right.columns],
    )
    return matched


def _diff_rows(left_columns: list[dict], right_columns: list[dict], matched: list[dict]) -> list[dict]:
    left_by_name = {item["column_name"]: item for item in left_columns}
    right_by_name = {item["column_name"]: item for item in right_columns}
    rows = []
    for match in matched:
        rows.append(
            {
                "state": "shared",
                "left_column": left_by_name.get(match["left"]),
                "right_column": right_by_name.get(match["right"]),
                "similarity": match.get("ratio", 1.0),
            }
        )
    matched_left = {match["left"] for match in matched}
    matched_right = {match["right"] for match in matched}
    for column in left_columns:
        if column["column_name"] not in matched_left:
            rows.append({"state": "left_only", "left_column": column, "right_column": None, "similarity": 0})
    for column in right_columns:
        if column["column_name"] not in matched_right:
            rows.append({"state": "right_only", "left_column": None, "right_column": column, "similarity": 0})
    return rows


def generate_merge_suggestion(db: Session, pair_id: str, actor: User) -> dict:
    pair = db.get(SimilarityPair, pair_id)
    if pair is None:
        raise AppError(codes.NOT_FOUND, "相似表对不存在", f"pair_id={pair_id}")
    left = _asset_with_columns(db, pair.left_asset_id)
    right = _asset_with_columns(db, pair.right_asset_id)
    merged = _matched_columns(left, right)
    payload = [
        {
            "asset_key": asset.asset_key,
            "table_name": asset.table_name,
            "row_count": asset.row_count,
            "columns": [
                {"column_name": column.column_name, "data_type": column.data_type}
                for column in asset.columns
            ],
        }
        for asset in (left, right)
    ]
    suggestion = similarity_engine.build_merge_suggestion(
        payload[0], payload[1], pair.score, pair.jaccard, pair.structure_score
    )
    suggestion["merged_columns"] = merged
    suggestion["generated_by"] = actor.email
    suggestion["generated_at"] = datetime.now(timezone.utc).isoformat()
    pair.suggestion = suggestion
    db.flush()
    audit_service.record(
        db,
        actor_email=actor.email,
        actor_role=actor.role,
        action="duplicate.merge_suggestion",
        entity_type="similarity_pair",
        entity_id=str(pair.id),
        detail=f"{suggestion['drop_asset_key']} → {suggestion['keep_asset_key']}（并集 {len(suggestion['union_columns'])} 列）",
    )
    return {"pair_id": str(pair.id), "suggestion": suggestion, "status": pair.status}


# ------------------------------------------------------------- 人工复核 ----
def dismiss(db: Session, pair_id: str, note: str, actor: User) -> dict:
    """人工判定非重复：结论写入当前 pending 轮次，并与该轮依据哈希绑定。"""
    pair = db.get(SimilarityPair, pair_id)
    if pair is None:
        raise AppError(codes.NOT_FOUND, "相似表对不存在", f"pair_id={pair_id}")
    review = _get_active_review(db, pair)
    if review is None or review.state != PairReviewState.PENDING.value:
        raise AppError(
            codes.CONFLICT,
            "当前复核轮次已结论，不能重复排除；如依据已变化请在新待复核项上操作",
            f"pair_id={pair_id} review_state={review.state if review else None}",
        )
    now = datetime.now(timezone.utc)
    review.state = PairReviewState.DISMISSED.value
    review.review_note = note
    review.reviewed_by = actor.email
    review.reviewed_at = now
    pair.status = PairStatus.DISMISSED.value
    # 兼容旧前端/导出：suggestion 内同步一份排除信息
    suggestion = dict(pair.suggestion or {})
    suggestion["dismiss_note"] = note
    suggestion["dismissed_by"] = actor.email
    pair.suggestion = suggestion
    db.flush()
    audit_service.record(
        db,
        actor_email=actor.email,
        actor_role=actor.role,
        action="duplicate.dismiss",
        entity_type="similarity_pair",
        entity_id=str(pair.id),
        detail=f"第 {review.generation} 代轮次判非重复；依据 {str(review.basis_hash)[:12]}；原因：{note}",
    )
    return {"pair_id": str(pair.id), "status": pair.status, "review": _review_payload(review)}


def reopen(db: Session, pair_id: str, note: str, actor: User) -> dict:
    """重新打开已结论轮次（人工推翻旧判断）；原原因保留在轮次上，动作落审计。"""
    pair = db.get(SimilarityPair, pair_id)
    if pair is None:
        raise AppError(codes.NOT_FOUND, "相似表对不存在", f"pair_id={pair_id}")
    review = _get_active_review(db, pair)
    if review is None or review.state == PairReviewState.PENDING.value:
        raise AppError(
            codes.CONFLICT,
            "该对当前没有已结论的复核轮次，无需重新打开",
            f"pair_id={pair_id} review_state={review.state if review else None}",
        )
    review.state = PairReviewState.PENDING.value
    review.basis_change = None
    pair.status = PairStatus.RECOMMENDED.value
    db.flush()
    audit_service.record(
        db,
        actor_email=actor.email,
        actor_role=actor.role,
        action="duplicate.reopen",
        entity_type="similarity_pair",
        entity_id=str(pair.id),
        detail=f"重新打开第 {review.generation} 代轮次（原状态已留痕）；原因：{note}",
    )
    return {"pair_id": str(pair.id), "status": pair.status, "review": _review_payload(review)}
