"""转债衍生层 — 双低排序 + 集思录强赎回填。

bond_cb_redeem_jsl 列(2026-09-05 实采 314 行权威):
代码/名称/现价/正股代码/正股名称/规模/剩余规模/转股起始日/最后交易日/
到期日/转股价/强赎触发比/强赎触发价/正股价/强赎价/强赎天计数/强赎条款/强赎状态
"""

from __future__ import annotations

from typing import Any

from selene_space_fund_ingest.collector import _now, _safe_fetch, _to_f


def compute_double_low(price: float | None, premium_pct: float | None) -> float | None:
    """双低 = 债现价 + 转股溢价率(经典公式,策略形态来自 stock 项目生态惯例)。"""
    if price is None or premium_pct is None:
        return None
    return price + premium_pct


def rank_by_double_low(
    cb_rows: list[dict[str, Any]], top: int = 10
) -> list[dict[str, Any]]:
    """cb_bonds 行按双低升序取前 N(缺价或溢价的行排除,不参与排名)。"""
    ranked = []
    for r in cb_rows:
        dl = compute_double_low(r.get("price"), r.get("premium_pct"))
        if dl is not None:
            ranked.append({**r, "double_low": dl})
    ranked.sort(key=lambda r: r["double_low"])
    return ranked[:top]


def fetch_redeem_flags(ak: Any) -> list[dict[str, Any]]:
    """集思录强赎表 → (code, redeem_status, redeem_days, last_trade_date) 行;
    失败/空返回 []。"""
    df = _safe_fetch(ak.bond_cb_redeem_jsl)
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        code = str(r.get("代码") or "").strip()
        if not code:
            continue
        rows.append(
            {
                "code": code,
                "redeem_status": str(r.get("强赎状态") or ""),
                "redeem_days": _to_f(r.get("强赎天计数")),
                "last_trade_date": str(r.get("最后交易日") or "")[:10] or None,
                "updated_at": _now(),
            }
        )
    return rows


def apply_redeem_to_cb(
    cb_rows: list[dict[str, Any]],
    redeem_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把强赎信息回填进 cb 行(原地);不在强赎表的行 redeem_status 留空串。

    返回回填后的 cb 行列表 — cb_bonds 表增列 redeem_status/redeem_days/
    last_trade_date 由 store 层 upsert 前补齐。
    """
    by_code = {r["code"]: r for r in redeem_rows}
    for row in cb_rows:
        info = by_code.get(row["code"])
        if info:
            row["redeem_status"] = info["redeem_status"]
            row["redeem_days"] = info["redeem_days"]
            row["last_trade_date"] = info["last_trade_date"]
        else:
            row.setdefault("redeem_status", "")
            row.setdefault("redeem_days", None)
            row.setdefault("last_trade_date", None)
    return cb_rows
