"""复核依据（basis）的确定性、哈希稳定性与前后对照测试。"""
from __future__ import annotations

from src.services import similarity_engine as engine


def _asset(key: str, columns: list[tuple[str, str]], row_count: int = 100) -> dict:
    return {
        "asset_key": key,
        "table_name": key,
        "layer": "ods",
        "row_count": row_count,
        "columns": [{"column_name": name, "data_type": dtype} for name, dtype in columns],
    }


COLUMNS_A = [
    ("store_id", "int8"),
    ("sale_date", "date"),
    ("mobile_no", "varchar"),
    ("amount", "numeric"),
]
COLUMNS_B = [
    ("store_id", "int8"),
    ("sale_date", "date"),
    ("msisdn", "varchar"),
    ("amount", "numeric"),
]


def test_basis_hash_is_deterministic_and_order_independent():
    left = _asset("ods_sale_a", COLUMNS_A)
    right = _asset("ods_sale_b", COLUMNS_B)
    basis = engine.build_pair_basis(left, right, jaccard=0.75, structure_score=0.9, score=0.8)
    # 同结构内容哈希稳定；列签名是集合，字典插入顺序不影响结果
    assert engine.basis_hash(basis) == engine.basis_hash(basis)
    rebuilt = engine.build_pair_basis(left, right, jaccard=0.75, structure_score=0.9, score=0.8)
    assert engine.basis_hash(basis) == engine.basis_hash(rebuilt)
    assert len(engine.basis_hash(basis)) == 64


def test_basis_changes_when_column_dropped():
    left = _asset("ods_sale_a", COLUMNS_A)
    right = _asset("ods_sale_b", COLUMNS_B)
    old_basis = engine.build_pair_basis(left, right, jaccard=0.75, structure_score=0.9, score=0.8)

    changed_right = _asset("ods_sale_b", COLUMNS_B[:-1])  # 删掉 amount 列
    new_basis = engine.build_pair_basis(left, changed_right, jaccard=0.5, structure_score=0.7, score=0.56)
    diff = engine.diff_basis(old_basis, new_basis)

    assert diff["changed"] is True
    assert any("amount" in sig for sig in diff["right_columns_removed"])
    assert diff["metrics_delta"]["score"] == round(0.56 - 0.8, 4)


def test_basis_changes_when_row_count_moves():
    left = _asset("ods_sale_a", COLUMNS_A, row_count=100)
    right = _asset("ods_sale_b", COLUMNS_B, row_count=100)
    old_basis = engine.build_pair_basis(left, right, jaccard=0.75, structure_score=0.9, score=0.8)

    grown = _asset("ods_sale_b", COLUMNS_B, row_count=240)
    new_basis = engine.build_pair_basis(left, grown, jaccard=0.75, structure_score=0.9, score=0.8)
    diff = engine.diff_basis(old_basis, new_basis)

    assert diff["changed"] is True
    assert diff["row_count_delta"] == {"right": 140}
    assert diff["metrics_delta"] == {}  # 结构没变，指标不动


def test_diff_unchanged_basis_reports_no_change():
    left = _asset("ods_sale_a", COLUMNS_A)
    right = _asset("ods_sale_b", COLUMNS_B)
    basis = engine.build_pair_basis(left, right, jaccard=0.75, structure_score=0.9, score=0.8)
    diff = engine.diff_basis(basis, dict(basis))
    assert diff["changed"] is False
    assert diff["reason"] == "unchanged"


def test_rank_table_pairs_carries_basis_and_hash():
    assets = [
        _asset("ods_sale_a", COLUMNS_A),
        _asset("ods_sale_b", COLUMNS_B),
        _asset("ods_other", [("id", "int8"), ("name", "varchar")]),
    ]
    ranking = engine.rank_table_pairs(assets, top_n=6, min_score=0.1)
    assert ranking["pairs"]
    top = ranking["pairs"][0]
    assert "basis" in top and "basis_hash" in top
    assert top["basis_hash"] == engine.basis_hash(top["basis"])
