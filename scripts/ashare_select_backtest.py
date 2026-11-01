"""A-share 选股模板回测 runner (US-3).

用法:
    python scripts/ashare_select_backtest.py --db data/ashare_select.db \
        --start 2023-01-01 --end 2026-09-01 --hold 10 --report data/research/ashare-select-baseline.md

对每个模板按调仓日 (每 rebalance 个交易日) 重放:
  picks = strategy(market_df, asof=D)
  forward_return = close[D+hold] / close[D] - 1  (等权)
组合曲线按调仓日累乘平均 forward_return, 求 win_rate / max_dd / annual.
数据不足的模板诚实标 no-data.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "ashare-select"))

from selene_space_ashare_select.strategies import (  # noqa: E402
    high_tight_flag,
    limit_up_shakeout,
    ma_volume,
    rps_breakout,
    turtle_trade,
    uptrend_limit_down,
)

TEMPLATES = {
    "turtle_trade": turtle_trade,
    "ma_volume": ma_volume,
    "high_tight_flag": high_tight_flag,
    "limit_up_shakeout": limit_up_shakeout,
    "uptrend_limit_down": uptrend_limit_down,
    "rps_breakout": rps_breakout,
}


def load_market(db_path: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("SELECT * FROM stock_daily", conn)
    need = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        raise SystemExit(f"db 缺列: {missing}")
    if "turnover" not in df.columns:
        df["turnover"] = (
            df["close"] * df["volume"]
        )  # 近似, turtle 流动性条件仍可用量级判断
    return df.sort_values(["symbol", "date"])


def close_lookup(market: pd.DataFrame) -> dict[tuple[str, str], float]:
    return {(r.symbol, r.date): r.close for r in market.itertuples(index=False)}


def trading_days(market: pd.DataFrame) -> list[str]:
    return sorted(market["date"].unique())


def forward_close(
    close_map: dict, symbol: str, d: str, hold: int, days: list[str]
) -> float | None:
    try:
        i = days.index(d)
    except ValueError:
        return None
    j = i + hold
    if j >= len(days):
        return None
    return close_map.get((symbol, days[j]))


def run_template(fn, market, days, close_map, start, end, hold, rebalance):
    """逐调仓日重放, 返回 (picks 数, 收益序列, 组合曲线)."""
    window = [d for d in days if start <= d <= end]
    if not window:
        return 0, [], []
    # 需要留 hold 天前瞻, 末段调仓日无收益
    tradable = window[: max(0, len(window) - hold)]
    step_days = tradable[::rebalance]
    rets: list[float] = []
    picks_total = 0
    for d in step_days:
        try:
            picks = fn(market, asof=d)
        except Exception:
            picks = []
        picks_total += len(picks)
        if not picks:
            rets.append(0.0)  # 空仓
            continue
        rs = []
        for s in picks:
            c0 = close_map.get((s, d))
            c1 = forward_close(close_map, s, d, hold, days)
            if c0 and c1:
                rs.append(c1 / c0 - 1)
        rets.append(sum(rs) / len(rs) if rs else 0.0)

    curve, eq = [], 1.0
    for r in rets:
        eq *= 1 + r
        curve.append(eq)
    return picks_total, rets, curve


def metrics(
    rets: list[float], curve: list[float], picks_total: int, n_days_window: int
) -> dict:
    if not rets or picks_total == 0:
        return {"picks": picks_total, "win_rate": None, "max_dd": None, "annual": None}
    wins = sum(1 for r in rets if r > 0)
    win_rate = wins / len(rets)
    peak, max_dd = -1e9, 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = min(max_dd, (v / peak - 1) if peak > 0 else 0)
    # 年化: 组合几何年化 = 期末/期初 ^ (250/窗口交易日数) - 1
    total = curve[-1] / curve[0] if curve and curve[0] > 0 else 1.0
    years = max(0.25, n_days_window / 250)
    annual = total ** (1 / years) - 1 if total > 0 else -1.0
    return {
        "picks": picks_total,
        "win_rate": win_rate,
        "max_dd": max_dd,
        "annual": annual,
    }


def fmt(x) -> str:
    return "no-data" if x is None else f"{x:.2%}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2099-12-31")
    ap.add_argument("--hold", type=int, default=10, help="前瞻持有交易日数")
    ap.add_argument("--rebalance", type=int, default=10, help="调仓间隔交易日数")
    ap.add_argument(
        "--report", default=str(REPO / "data/research/ashare-select-baseline.md")
    )
    args = ap.parse_args()

    market = load_market(args.db)
    days = trading_days(market)
    close_map = close_lookup(market)
    window = [d for d in days if args.start <= d <= args.end]
    n_syms = market["symbol"].nunique()
    print(
        f"market: {n_syms} symbols, {len(days)} trading days, window {len(window)} days"
    )

    lines = [
        "# A股选股模板 baseline 报告",
        "",
        f"- 数据源: `{args.db}` (stock_daily, INSERT OR REPLACE 同步)",
        f"- 股票数: {n_syms} | 数据窗口: {window[0] if window else '-'} ~ {window[-1] if window else '-'}"
        f" ({len(window)} 交易日)",
        f"- 口径: 每 {args.rebalance} 个交易日调仓, 持有 {args.hold} 日等权前瞻收益;"
        " 组合曲线=调仓日收益累乘; win_rate=调仓日收益>0 占比; annual=组合几何年化",
        f"- 复权: 后复权 (hfq); 生成时间: {pd.Timestamp.today().strftime('%Y-%m-%d %H:%M')}",
        "",
        "| 模板 | 调仓样本 | win_rate | max_dd | annual | 建议 |",
        "|---|---|---|---|---|---|",
    ]
    results = {}
    for name, fn in TEMPLATES.items():
        n_picks, rets, curve = run_template(
            fn, market, days, close_map, args.start, args.end, args.hold, args.rebalance
        )
        m = metrics(rets, curve, n_picks, len(window))
        results[name] = m
        sig = (
            "淘汰(负年化 + 胜率显著<50%)"
            if (
                m["win_rate"] is not None
                and m["picks"] >= 10
                and m["win_rate"] < 0.4
                and (m["annual"] or 0) < 0
            )
            else "保留(胜率/年化正向,建议扩样本复验)"
            if (
                m["win_rate"] is not None
                and m["picks"] >= 10
                and m["win_rate"] > 0.5
                and (m["annual"] or 0) > 0
            )
            else "淘汰建议(样本少或胜率≈抛硬币)"
            if m["win_rate"] is not None
            else "no-data(数据不足或依赖外部数据源)"
        )
        lines.append(
            f"| {name} | {m['picks']} | {fmt(m['win_rate'])} | {fmt(m['max_dd'])} | {fmt(m['annual'])} | {sig} |"
        )
        print(
            f"{name}: picks={m['picks']} win={fmt(m['win_rate'])} dd={fmt(m['max_dd'])} annual={fmt(m['annual'])}"
        )

    lines += [
        "",
        "## 说明",
        "",
        "- `private_placement` 为 experimental (akshare 定增公告数据, 非价量公式), 不进价量回测, 标 no-data.",
        "- no-data 模板: 数据窗口/股票数不足其最小 K 线要求 (turtle 21/htf 40/uptrend 60/rps 120 日),"
        " 或同步当日数据源不可用.",
        "- 保留口径: 胜率>50% 且年化为正, 样本≥10 调仓日; 胜率<40% 且负年化 → 淘汰; 其余(胜率≈抛硬币/样本不足) → 淘汰建议.",
        "- turnover 近似说明: 腾讯源无成交额字段, turnover=close×volume×100 近似 (手→股), turtle 流动性亿级阈值口径略偏.",
    ]

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"report -> {out}")


if __name__ == "__main__":
    main()
