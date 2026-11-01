"""TG 波动率体制 feed A/B 回测 — B 首期骨架验收 (2026-08-14).

把"事件后 24-48h 前向波动率抬升"(持续性信号)做成独立风险预算输入,与 de-risk
overlay(冲击信号)分离。本脚本对比 2 种配置 × 3 币种,逐条对照
the pre-registration record §1 预注册验收。

配置:
1. baseline — 纯 bb_v3 BUY/TP/SL(无 feed)
2. regime   — bb_v3 + 波动率体制 feed:白名单组合事件后 48h 仅后向窗口 ×0.85

机制 = 暴露度缩放(风险预算语义,非 entry-cap),用**真实暴露再平衡**实现:
目标暴露 mult[i](窗口内 0.85 / 窗口外 1.0),当目标暴露变化且持仓中时,
按市场价调仓 qty(进入窗口卖 15% 减仓、离开窗口买回)。净值恒为
equity[i] = cash + qty·price[i],无水平标记跳变。

**测量修正(用户 2026-08-14 批准)**:初版用水平标记 equity=cash+qty·price·mult,
mult 翻转(1.0↔0.85)在窗口边界制造 ±15% 市值跳变,污染窗口内波动率/MDD/Sharpe
(实测 window_vol 被抬到 0.85 暴露机制物理上不可能达到的 3.9 倍)。
改为真实减仓/回补后净值连续,窗口内 P&L 真降 15%。

理由(SPEC §7/P5 教训):overlay 的 entry-cap 在 bb_v3 稀疏交易下几乎不触发,
测不出风险预算概念本身。

feed 镜像生产 TGVolRegimeSource(packages/alpha-core/selene_space_alpha/
factors/tg_vol_regime_source.py)——常量直接 import,保证单事实源:
- 窗口 48h 仅后向(REGIME_WINDOW_AFTER_HOURS)
- 乘数 0.85(REGIME_MULT,vol-targeting 中值,非调优)
- 白名单组合 REGIME_TYPES_BY_SYMBOL(BTC:market / ETH:macro / SOL:macro)
- 门控 confidence>=0.3 and magnitude>0(CONFIDENCE_MIN)

预注册验收(SPEC §1):
1. 机制(源级)——由单元测试 test_tg_vol_regime_source.py 覆盖,此处 sanity 打印
2. 风险降低:窗口内 regime 净值实现波动率 ≤ 窗口内 baseline(3/3)
3. 尾部:MDD(regime) ≤ MDD(baseline)(≥2/3)
4. 不毁收益:Sharpe(regime) ≥ 0.9 × Sharpe(baseline)(3/3)
5. 触发量 sanity:触发事件数 vs 研究 n(BTC 68/ETH 92/SOL 91,±30%)
6. 架构独立:vol_regime_mult 与 event_overlay_mult 正交——单元测试
   test_orthogonal_to_event_overlay_mult 覆盖;本脚本 regime 模式不 import overlay 源
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]

# 复用 2026-07-18 回测的 bb_v3 信号与参数（同一口径）
_spec = importlib.util.spec_from_file_location("tg_overlay_old", REPO / "scripts" / "tg_overlay_backtest_2026-07-18.py")
old = importlib.util.module_from_spec(_spec)
sys.modules["tg_overlay_old"] = old
_spec.loader.exec_module(old)  # type: ignore

# 复用重测模块的 klines / 信号加载
_spec2 = importlib.util.spec_from_file_location("tg_reval", REPO / "scripts" / "tg_revalidation_2026-07-18.py")
tg = importlib.util.module_from_spec(_spec2)
sys.modules["tg_reval"] = tg
_spec2.loader.exec_module(tg)  # type: ignore

# 镜像生产 regime 源常量（单事实源）
try:
    from selene_space_alpha.factors.tg_vol_regime_source import (
        CONFIDENCE_MIN,
        REGIME_MULT,
        REGIME_TYPES_BY_SYMBOL,
        REGIME_WINDOW_AFTER_HOURS,
    )
except ImportError:  # 未激活副本 PYTHONPATH 时降级为硬编码（与源注释一致）
    CONFIDENCE_MIN = 0.3
    REGIME_MULT = 0.85
    REGIME_TYPES_BY_SYMBOL = {
        "BTC": frozenset({"market"}),
        "ETH": frozenset({"macro"}),
        "SOL": frozenset({"macro"}),
    }
    REGIME_WINDOW_AFTER_HOURS = 48.0

# 研究 n（SPEC §3, 来自 tg_event_forward_vol_2026-08-14.json）——验收 5 对照
RESEARCH_N = {"BTC": 68, "ETH": 92, "SOL": 91}
REGIME_TOLERANCE = 0.30  # ±30%

INITIAL_CASH = old.INITIAL_CASH
WINDOW_START = pd.Timestamp("2025-11-01", tz="UTC")

# 4h bar 年化 bar 数
BARS_PER_YEAR = 365.25 * 24 / 4


# ---------- 事件加载（镜像 TGVolRegimeSource 的 gate 与白名单映射） ----------

def _load_gated_regime_events(symbol_short: str) -> pd.DataFrame:
    """tg_factors.db 中映射到本币、通过 gate、属白名单组合的事件。"""
    signals = tg.load_tg_signals()
    signals["mapped_symbols"] = signals.apply(tg.map_signal_to_symbols, axis=1)
    sigs = signals[signals["mapped_symbols"].apply(lambda lst: symbol_short in lst)].copy()
    if sigs.empty:
        return sigs
    whitelist = REGIME_TYPES_BY_SYMBOL.get(symbol_short, frozenset())
    sigs = sigs[sigs["event_type"].isin(whitelist)]
    conf = pd.to_numeric(sigs.get("confidence"), errors="coerce").fillna(0.0)
    mag = pd.to_numeric(sigs.get("magnitude"), errors="coerce").fillna(0.0)
    return sigs[(conf >= CONFIDENCE_MIN) & (mag > 0)]


def _regime_mask(klines: pd.DataFrame, events: pd.DataFrame) -> np.ndarray:
    """bar_open 落入白名单事件的 [ts, ts+48h] 仅后向窗口 → True。"""
    if events.empty:
        return np.zeros(len(klines), dtype=bool)
    bar_ns = klines.index.astype("int64").values
    ev_ns = events["ts"].astype("int64").values
    after_ns = int(REGIME_WINDOW_AFTER_HOURS * 3_600_000_000_000)
    mask = np.zeros(len(klines), dtype=bool)
    for e in ev_ns:
        mask |= (bar_ns >= e) & (bar_ns <= e + after_ns)
    return mask


def _all_gated_mapped_events(symbol_short: str) -> pd.DataFrame:
    """全部映射到本币、通过 gate 的事件（含所有类型）——研究口径计数的输入。"""
    signals = tg.load_tg_signals()
    signals["mapped_symbols"] = signals.apply(tg.map_signal_to_symbols, axis=1)
    sigs = signals[signals["mapped_symbols"].apply(lambda lst: symbol_short in lst)].copy()
    if sigs.empty:
        return sigs
    conf = pd.to_numeric(sigs.get("confidence"), errors="coerce").fillna(0.0)
    mag = pd.to_numeric(sigs.get("magnitude"), errors="coerce").fillna(0.0)
    return sigs[(conf >= CONFIDENCE_MIN) & (mag > 0)]


def _research_n_count(klines: pd.DataFrame, events_all_types: pd.DataFrame,
                      event_type: str, forward_h: int = 6) -> int:
    """研究口径 n（镜像 tg_event_forward_vol_2026-08-14.py，验收 5 对照 RESEARCH_N）。

    事件对齐到 bar（searchsorted 右对齐取前一根）→ **跨类型**同 bar 去重（ts 首条）
    → 按类型取 unique bar → 要求 forward_h 根完整前向 bar。
    修正前我按"白名单事件独立标记"计得 99/127/126，研究 68/92/91 是跨类型去重后的
    per-type 口径——两套数字描述不同对象，须分开上报。
    """
    if events_all_types.empty:
        return 0
    opens = klines.index.astype("int64").values
    sig_ms = events_all_types["ts"].astype("int64").values
    bar_idx = np.searchsorted(opens, sig_ms, side="right") - 1
    n_bars = len(klines)
    ev_df = events_all_types.assign(_bar=bar_idx)
    ev_df = ev_df[(ev_df["_bar"] >= 0) & (ev_df["_bar"] < n_bars - 1)]
    valid = ev_df[~ev_df["_bar"].duplicated(keep="first")]  # 跨类型同 bar 去重
    et_rows = valid.loc[valid["event_type"] == event_type]
    ev = np.sort(np.unique(et_rows["_bar"].values))
    ev_h = ev[ev <= n_bars - forward_h - 1]
    return int(len(ev_h))


def _raw_trigger_bars(klines: pd.DataFrame, events: pd.DataFrame) -> int:
    """机制实际标记的去重 bar 数（白名单事件独立标记，无跨类型去重）。

    这是 _regime_mask 真实触发的窗口 bar 数下限（每事件 48h 窗口内的首 bar），
    与 _research_n_count 不同：后者是研究 per-type 口径。
    """
    if events.empty:
        return 0
    opens = klines.index.astype("int64").values
    sig_ms = events["ts"].astype("int64").values
    bar_idx = np.searchsorted(opens, sig_ms, side="right") - 1
    n_bars = len(klines)
    valid = bar_idx[(bar_idx >= 0) & (bar_idx < n_bars)]
    return int(len(np.unique(valid)))


# ---------- 回测（bb_v3 + 波动率体制 feed，暴露度缩放） ----------

def _bb_v3(klines: pd.DataFrame) -> pd.DataFrame:
    return old.compute_bb_v3_signals(klines)


def run_backtest(klines: pd.DataFrame, events: pd.DataFrame, mode: str) -> dict:
    """mode: baseline / regime。

    机制 = 真实暴露再平衡（风险预算语义）：目标暴露 mult[i]（窗口内 0.85 / 外 1.0）。
    目标暴露变化且持仓中时按市场价调仓 qty（进入窗口卖 15% 减仓、离开买回回补），
    净值恒为 equity[i] = cash + qty·price[i] —— 连续、无水平标记跳变。
    baseline 下 mult 恒 1.0 → 永不调仓，与 2026-07-18 纯 bb_v3 逐 bar 逐分一致。
    """
    df = _bb_v3(klines)
    n = len(df)

    regime_mask = _regime_mask(df, events)
    if mode == "baseline":
        mult = np.ones(n)
    elif mode == "regime":
        mult = np.where(regime_mask, REGIME_MULT, 1.0)
    else:
        raise ValueError(mode)

    trades = []
    cash = INITIAL_CASH
    open_long: old.Trade | None = None
    position_qty = 0.0  # 实际持仓 qty（暴露再平衡会调它；open_long.qty 保持入场口径供报表）
    prev_mult = 1.0
    equity = np.zeros(n)
    prices = df["close"].values
    buy_sig = df["buy_signal"].values
    sell_sig = df["sell_signal"].values
    reduced_buys = 0
    rebalances = 0

    for i in range(n):
        ts = df.index[i]
        price = prices[i]
        buy_price = price * (1 + old.SLIPPAGE_BPS / 10000)
        sell_price = price * (1 - old.SLIPPAGE_BPS / 10000)
        m = mult[i]

        # 暴露度再平衡：目标暴露变化且持仓中 → 按市场价调仓 qty
        if open_long is not None and m != prev_mult:
            ratio = m / prev_mult
            if ratio < 1.0:  # 进入 regime 窗口：卖出 (1-ratio) 减仓
                sell_qty = position_qty * (1 - ratio)
                proceeds = sell_qty * price
                cash += proceeds - proceeds * old.COMMISSION
                position_qty *= ratio
            else:  # 离开 regime 窗口：买回 (ratio-1) 回补
                buy_qty = position_qty * (ratio - 1)
                cost = buy_qty * price
                cash -= cost + cost * old.COMMISSION
                position_qty *= ratio
            rebalances += 1
        prev_mult = m

        # 平多（TP/SL/signal/hold_end）
        if open_long is not None:
            ret = (price - open_long.entry_price) / open_long.entry_price
            exit_reason, exit_price = None, None
            if ret >= old.BB_V3_PARAMS["buy_tp_pct"]:
                exit_reason, exit_price = "tp", open_long.entry_price * (1 + old.BB_V3_PARAMS["buy_tp_pct"])
            elif ret <= -old.BB_V3_PARAMS["buy_sl_pct"]:
                exit_reason, exit_price = "sl", open_long.entry_price * (1 - old.BB_V3_PARAMS["buy_sl_pct"])
            elif sell_sig[i]:
                exit_reason, exit_price = "signal", sell_price
            elif (ts - open_long.entry_ts).total_seconds() >= 7 * 24 * 3600:
                exit_reason, exit_price = "hold_end", sell_price
            if exit_reason:
                proceeds = exit_price * position_qty
                cash += proceeds - proceeds * old.COMMISSION
                open_long.exit_ts, open_long.exit_price, open_long.exit_reason = ts, exit_price, exit_reason
                open_long.pnl_pct = (exit_price - open_long.entry_price) / open_long.entry_price - 2 * old.COMMISSION
                trades.append(open_long)
                open_long = None
                position_qty = 0.0

        # 开 bb_v3 BUY（regime 窗口内按目标暴露缩量开仓）
        if open_long is None and buy_sig[i]:
            m_entry = mult[i]
            if m_entry > 0:
                use_cash = cash * 0.95 * m_entry
                if use_cash > 100:
                    if m_entry < 1.0:
                        reduced_buys += 1
                    qty = use_cash / buy_price
                    cash -= use_cash + use_cash * old.COMMISSION
                    open_long = old.Trade(side="long", entry_ts=ts, entry_price=buy_price, qty=qty)
                    position_qty = qty

        equity[i] = cash + (position_qty * price if open_long is not None else 0.0)

    # 强制平仓
    if open_long is not None:
        last_price = prices[-1] * (1 - old.SLIPPAGE_BPS / 10000)
        proceeds = last_price * position_qty
        cash += proceeds - proceeds * old.COMMISSION
        open_long.exit_ts, open_long.exit_price, open_long.exit_reason = df.index[-1], last_price, "final"
        open_long.pnl_pct = (last_price - open_long.entry_price) / open_long.entry_price - 2 * old.COMMISSION
        trades.append(open_long)
        position_qty = 0.0
        equity[-1] = cash

    return _metrics(df, equity, trades, mode, regime_mask,
                    reduced_buys=reduced_buys, rebalances=rebalances)


def _metrics(df: pd.DataFrame, equity: np.ndarray, trades: list, mode: str,
             regime_mask: np.ndarray, reduced_buys: int = 0, rebalances: int = 0) -> dict:
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak  # ≤ 0
    mdd = float(-dd.min()) if len(dd) else 0.0

    # 回撤时长（bar 数）：从峰值到恢复
    in_dd = dd < 0
    max_dur = 0
    cur = 0
    for flag in in_dd:
        cur = cur + 1 if flag else 0
        max_dur = max(max_dur, cur)

    # 年化 Sharpe（全区间）
    rets = np.diff(equity) / equity[:-1]
    rets = rets[np.isfinite(rets)]
    if len(rets) > 1 and rets.std(ddof=1) > 0:
        sharpe = float(rets.mean() / rets.std(ddof=1) * np.sqrt(BARS_PER_YEAR))
    else:
        sharpe = None

    # 验收 2:窗口内净值实现波动率(仅 regime 窗口 bar)
    win_rets = []
    for i in range(1, len(equity)):
        if regime_mask[i] and equity[i - 1] > 0:
            win_rets.append(equity[i] / equity[i - 1] - 1)
    window_vol = float(np.std(win_rets, ddof=1)) if len(win_rets) >= 2 else None

    days = max((df.index[-1] - df.index[0]).days, 1)
    total_return = float(equity[-1] / INITIAL_CASH - 1)
    if mdd > 0:
        cagr = (equity[-1] / INITIAL_CASH) ** (365.25 / days) - 1 if equity[-1] > 0 else -1.0
        calmar = float(cagr) / mdd
    else:
        cagr, calmar = (0.0, None)

    n_trades = len(trades)
    win_rate = (sum(1 for t in trades if t.pnl_pct > 0) / n_trades) if n_trades else None
    return {
        "mode": mode,
        "n_trades": n_trades,
        "win_rate": round(win_rate, 4) if win_rate is not None else None,
        "total_return": round(total_return, 4),
        "mdd": round(mdd, 4),
        "calmar": round(calmar, 4) if calmar is not None else None,
        "cagr": round(float(cagr), 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown_duration_bars": int(max_dur),
        "max_drawdown_duration_days": round(max_dur * 4 / 24, 2),
        "window_vol": round(window_vol, 6) if window_vol is not None else None,
        "n_regime_bars": int(regime_mask.sum()),
        "n_reduced_buys": reduced_buys,
        "n_rebalances": rebalances,
    }


# ---------- 验收测量 ----------

def main():
    print("=" * 70)
    print("TG 波动率体制 feed A/B 回测 — B 首期骨架验收 (2026-08-14)")
    print("=" * 70)

    signals = tg.load_tg_signals()
    print(f"真信号总数: {len(signals)}")
    print(f"regime 窗口: 后向 {REGIME_WINDOW_AFTER_HOURS:.0f}h, 乘数 {REGIME_MULT}")
    print(f"白名单组合: { {k: sorted(v) for k, v in REGIME_TYPES_BY_SYMBOL.items()} }")

    modes = ["baseline", "regime"]
    all_results = {}
    trigger_counts = {}
    mechanism_bars = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = tg.load_klines(sym)
        klines = klines[klines.index >= WINDOW_START]
        events = _load_gated_regime_events(sym_short)
        all_gated = _all_gated_mapped_events(sym_short)
        wl_type = next(iter(REGIME_TYPES_BY_SYMBOL.get(sym_short, frozenset())))
        n_research = _research_n_count(klines, all_gated, wl_type, forward_h=6)
        raw_fires = _raw_trigger_bars(klines, events)
        trigger_counts[sym_short] = n_research
        mechanism_bars[sym_short] = raw_fires
        print(f"\n========== {sym} ({sym_short}) ==========")
        print(f"  区间: {klines.index[0]} -> {klines.index[-1]} ({len(klines)} bars)")
        print(f"  白名单事件: {len(events)} (机制触发 bar {raw_fires} / 研究口径 n={n_research} / "
              f"研究 n={RESEARCH_N[sym_short]})")
        for mode in modes:
            r = run_backtest(klines, events, mode)
            all_results[f"{sym_short}_{mode}"] = r
            eng = (f"  regime_bars={r['n_regime_bars']} reduced_buys={r['n_reduced_buys']}"
                   f" rebalance={r['n_rebalances']}"
                   if mode != "baseline" else "")
            print(f"  [{mode:<9}] ret={r['total_return']:+.2%}  MDD={r['mdd']:.1%}  "
                  f"Sharpe={r['sharpe'] if r['sharpe'] is not None else 'N/A'}  "
                  f"Calmar={r['calmar'] if r['calmar'] is not None else 'N/A'}  "
                  f"winvol={r['window_vol'] if r['window_vol'] is not None else 'N/A'}  "
                  f"n={r['n_trades']}{eng}")

    # ---- 验收对照 ----
    print("\n" + "=" * 70)
    print("预注册验收对照（the pre-registration record §1）")
    print("=" * 70)

    # 验收 2:窗口内实现波动率 regime ≤ baseline(3/3)
    acc2 = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        b, r = all_results[f"{sym_short}_baseline"], all_results[f"{sym_short}_regime"]
        ok = (r["window_vol"] is not None and b["window_vol"] is not None
              and r["window_vol"] <= b["window_vol"])
        acc2[sym_short] = {"baseline_winvol": b["window_vol"], "regime_winvol": r["window_vol"],
                           "pass": ok}
    acc2_pass = all(v["pass"] for v in acc2.values())

    # 验收 3:MDD(regime) ≤ MDD(baseline)(≥2/3)
    acc3 = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        b, r = all_results[f"{sym_short}_baseline"], all_results[f"{sym_short}_regime"]
        acc3[sym_short] = {"baseline_mdd": b["mdd"], "regime_mdd": r["mdd"],
                           "pass": r["mdd"] <= b["mdd"]}
    acc3_pass = sum(1 for v in acc3.values() if v["pass"]) >= 2

    # 验收 4:Sharpe(regime) ≥ 0.9 × Sharpe(baseline)(3/3)
    # 规则修正(用户 2026-08-14 批准):负基线时 "0.9×负值" 要求负方向再改进 10%,
    # 数学上不可能代表"不毁收益"(regime 已全部 ≥ baseline 却被误报毁收益)。
    # 正基线保持 ≥0.9×,负基线改判 ≥ baseline(不劣于)。0.85/48h/白名单不动。
    acc4 = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        b, r = all_results[f"{sym_short}_baseline"], all_results[f"{sym_short}_regime"]
        if b["sharpe"] is None or r["sharpe"] is None:
            ok = False
        elif b["sharpe"] >= 0:
            ok = r["sharpe"] >= 0.9 * b["sharpe"]
        else:
            ok = r["sharpe"] >= b["sharpe"]
        acc4[sym_short] = {"baseline_sharpe": b["sharpe"], "regime_sharpe": r["sharpe"],
                           "rule": "pos:≥0.9× / neg:≥baseline", "pass": ok}
    acc4_pass = all(v["pass"] for v in acc4.values())

    # 验收 5:研究口径触发 n vs 研究 n ±30%（机制实际触发 bar 数另报）
    acc5 = {}
    for sym_short, n_trig in trigger_counts.items():
        rn = RESEARCH_N[sym_short]
        lo, hi = rn * (1 - REGIME_TOLERANCE), rn * (1 + REGIME_TOLERANCE)
        acc5[sym_short] = {"research_n": n_trig, "research_n_pre": rn,
                           "mechanism_raw_bars": mechanism_bars[sym_short],
                           "window": [round(lo, 1), round(hi, 1)],
                           "pass": lo <= n_trig <= hi}
    acc5_pass = all(v["pass"] for v in acc5.values())

    accs = {
        "1_mechanism_source_level": {
            "pass": True,
            "note": "源级机制由单元测试 test_tg_vol_regime_source.py 覆盖(白名单组合→48h 窗口 0.85;"
                    "非白名单不触发);此处 sanity:各币种白名单事件数与触发数见结果。",
        },
        "2_windowed_vol_reduction": {"pass": acc2_pass, "detail": acc2},
        "3_mdd_not_worse": {"pass": acc3_pass, "detail": acc3,
                            "rule": "≥2/3 币种 regime MDD ≤ baseline"},
        "4_sharpe_not_ruined": {"pass": acc4_pass, "detail": acc4,
                                "rule": "3/3: 正基线 ≥0.9×baseline / 负基线 ≥baseline(修正)"},
        "5_trigger_sanity": {"pass": acc5_pass, "detail": acc5, "tolerance": REGIME_TOLERANCE},
        "6_arch_independence": {
            "pass": True,
            "note": "test_orthogonal_to_event_overlay_mult 断言 vol_regime_mult 与 "
                    "event_overlay_mult 为两条独立列;本脚本 regime 模式只消费 vol_regime_mult,"
                    "未 import overlay 源。",
        },
    }

    for k, v in accs.items():
        mark = "PASS" if v["pass"] else "FAIL"
        print(f"\n  验收 {k}: {mark}")
        for kk, vv in v.items():
            if kk != "pass":
                print(f"      {kk}: {json.dumps(vv, default=str, ensure_ascii=False)}")

    # 落盘
    out = REPO / "实验" / "tg_vol_regime_backtest_2026-08-14.json"
    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "config": {
            "window_start": str(WINDOW_START),
            "regime_window_after_hours": REGIME_WINDOW_AFTER_HOURS,
            "regime_mult": REGIME_MULT,
            "regime_types_by_symbol": {k: sorted(v) for k, v in REGIME_TYPES_BY_SYMBOL.items()},
            "research_n": RESEARCH_N,
            "regime_tolerance": REGIME_TOLERANCE,
        },
        "results": all_results,
        "acceptance": accs,
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\n结果已保存: {out}")


if __name__ == "__main__":
    main()
