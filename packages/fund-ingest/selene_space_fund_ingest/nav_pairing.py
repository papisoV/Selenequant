"""净值配对层 — spot 场内价 × 天天基金净值 → 溢价率回填。

fund_open_fund_info_em(symbol, '单位净值走势') 返回 净值日期/单位净值,
逐基金调用(东财 pingzhongdata 端点)。净值 T+1 语义:nav_date 戳带出,
不做假实时。失败单基金跳过(nav 维持 None,溢价不计算)。
"""

from __future__ import annotations

from typing import Any

from selene_space_fund_ingest.collector import (
    _call_with_retry,
    _to_f,
    compute_premium_pct,
)


def fetch_latest_nav(ak: Any, symbol: str) -> tuple[float | None, str | None]:
    """单基金最新(单位净值,净值日期);失败返回 (None, None)。"""
    try:
        df = _call_with_retry(
            lambda: ak.fund_open_fund_info_em(symbol=symbol, indicator="单位净值走势")
        )
    except Exception:
        return None, None
    if df is None or df.empty:
        return None, None
    last = df.iloc[-1]
    nav = _to_f(last.get("单位净值"))
    date = str(last.get("净值日期") or "")[:10]
    return nav, (date or None)


def enrich_with_nav(
    ak: Any,
    spot_rows: list[dict[str, Any]],
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """对 spot 行回填 nav/nav_date/premium_pct(原地改写并返回)。

    limit 截断调试用(逐基金网络调用,全量 LOF~300 只会慢)。
    nav 缺失行三字段维持 None — 溢价不计算,诚实留空。
    """
    targets = spot_rows if limit is None else spot_rows[:limit]
    for row in targets:
        nav, nav_date = fetch_latest_nav(ak, row["code"])
        if nav is not None:
            row["nav"] = nav
            row["nav_date"] = nav_date
            row["premium_pct"] = compute_premium_pct(row.get("price"), nav)
    return targets
