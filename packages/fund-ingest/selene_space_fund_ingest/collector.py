"""采集层 — akshare 三域接口封装,可注入失败退化。

设计要点(实测驱动):
- akshare 接口存在但端点级波动(2026-09-05 实测:LOF spot 390 行成功,
  bond_zh_cov/ETF spot 撞东财 SSL/防爬)⇒ 每个采集函数失败返回空列表,
  调用方(store)对空列表做清表语义,绝不向上抛网络异常
- akshare 通过模块属性注入(测试 monkeypatch ak.bond_zh_cov 等),
  复刻 news-ingest test_detector.py:150 的离线测试模式
- 列名映射:东财中文列名 → 英文 schema,unknown 列直接丢
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable

from selene_space_fund_ingest.config import FETCH_RETRIES


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_f(v: Any) -> float | None:
    """宽松数值转换:None/空串/'-' → None,其余 float()。"""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _call_with_retry(fn: Callable[..., Any], retries: int = FETCH_RETRIES) -> Any:
    last_exc: Exception | None = None
    for _ in range(retries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — 端点波动退化为空,不上抛
            last_exc = exc
            time.sleep(0.5)
    raise last_exc if last_exc else RuntimeError("unreachable")


def _safe_fetch(fn: Callable[..., Any]) -> Any:
    """采集失败退化:返回 None(调用方落空表),不向上抛。"""
    try:
        return _call_with_retry(fn)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 转债(bond_zh_cov 全表快照)
# ---------------------------------------------------------------------------
def fetch_cb_bonds(ak: Any) -> list[dict[str, Any]]:
    """转债全表 → cb_bonds schema 行;失败/空返回 []。

    列名自 akshare 1.18.55 bond_zh_cov 源码权威提取(2026-09-05):
    债券代码/债券简称/正股价/转股价/转股价值/债现价/转股溢价率(%)。
    """
    df = _safe_fetch(ak.bond_zh_cov)
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        code = str(r.get("债券代码") or "").strip()
        if not code:
            continue
        rows.append(
            {
                "code": code,
                "name": str(r.get("债券简称") or ""),
                "price": _to_f(r.get("债现价")),
                "convert_price": _to_f(r.get("转股价")),
                "premium_pct": _to_f(r.get("转股溢价率")),
                "redeem_trigger_price": None,  # bond_zh_cov 无此列;US-3 由强赎接口补
                "updated_at": _now(),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# LOF/ETF 场内价 + 净值 → 溢价率
# ---------------------------------------------------------------------------
def compute_premium_pct(price: float | None, nav: float | None) -> float | None:
    """溢价率(%) = (price - nav) / nav * 100 — stock LOF_arbitrage.py:38-40 策略形态。"""
    if price is None or nav is None or nav <= 0:
        return None
    return (price - nav) / nav * 100.0


def fetch_lof_etf_spot(ak: Any) -> list[dict[str, Any]]:
    """LOF+ETF 场内快照 → lof_etf_spot schema 行;失败/空返回 []。

    列名自 akshare 1.18.55 源码权威提取(2026-09-05):spot 接口只有
    代码/名称/最新价/涨跌幅 等,**不带净值列** — nav 留 None 由净值配对层
    (fund_open_fund_info_em,T+1 日频)单独补,溢价率在此层无法计算。
    """
    out: list[dict[str, Any]] = []
    for category, fetcher in (
        ("LOF", ak.fund_lof_spot_em),
        ("ETF", ak.fund_etf_spot_em),
    ):
        df = _safe_fetch(fetcher)
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            code = str(r.get("代码") or "").strip()
            if not code:
                continue
            out.append(
                {
                    "code": code,
                    "name": str(r.get("名称") or ""),
                    "category": category,
                    "price": _to_f(r.get("最新价")),
                    "nav": None,
                    "nav_date": None,
                    "premium_pct": None,
                    "updated_at": _now(),
                }
            )
    return out


# ---------------------------------------------------------------------------
# REITs 实时快照
# ---------------------------------------------------------------------------
def fetch_reits_spot(ak: Any) -> list[dict[str, Any]]:
    """REITs 实时 → reits_spot schema 行;失败/空返回 []。"""
    df = _safe_fetch(ak.reits_realtime_em)
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
                "name": str(r.get("名称") or ""),
                "price": _to_f(r.get("最新价")),
                "change_pct": _to_f(r.get("涨跌幅")),
                "updated_at": _now(),
            }
        )
    return rows
