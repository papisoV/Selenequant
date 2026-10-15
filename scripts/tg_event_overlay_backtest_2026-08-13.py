"""TG 事件 overlay A/B 回测 — P5 验收 (2026-08-13).

方向证伪后，TG 管道重构为"事件传感器"：消息只用于减仓（de-risk），不赌方向。
本脚本对比 3 种配置 × 3 币种，输出 MDD/Calmar/回撤时长，逐条对照 SPEC §1 预注册验收。

配置：
1. baseline        — 纯 bb_v3 BUY/TP/SL（无 overlay）
2. overlay         — bb_v3 + 事件 overlay 减仓，叠加 = 单削减（生产规则 = guard.py min()，
                     均匀 DE_RISK_POSITION_MULT=0.5 下 max/min 重合）
3. overlay_multiply — bb_v3 + 事件 overlay 减仓，叠加 = 相乘（双砍，旧 guard `*= tg_mult` 的对比）

事件 overlay 镜像生产 TGEventOverlaySource（packages/alpha-core/selene_space_alpha/
factors/tg_event_overlay_source.py）——常量直接 import，保证单事实源：
- 窗口 ±24h（OVERLAY_WINDOW_BEFORE/AFTER_HOURS）
- 乘数 0.5（DE_RISK_POSITION_MULT）
- 门控 confidence>=0.3 and magnitude>0（CONFIDENCE_MIN）
- 类型 macro/security 常开，listing/whale 默认关（DEFAULT_ON_TYPES / EVENT_OVERLAY_TYPES）

预注册验收（SPEC §1）：
1. MDD(overlay) ≤ MDD(baseline) 且 Calmar(overlay) ≥ Calmar(baseline)（净收益口径）
2. max 规则 MDD ≤ multiply 规则 MDD（双砍对比实测）
3. overlay 触发率（减仓事件占比）落在审计分布窗口：macro 234/listing 82/whale 62/总量 794（±20%）
4. 中性事件穿透率：direction=0 事件不被 gate 丢弃（实测基线 14.1%）
5. 周 LLM 预算 ≤ 基线 ×1.5（无计数器时降级为代码审查，见 SPEC §2）
6. 新老源单会话互斥（factory 层断言，US-007 已测）
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

# 镜像生产 overlay 源常量（单事实源）
try:
    from selene_space_alpha.factors.tg_event_overlay_source import (
        CONFIDENCE_MIN,
        DE_RISK_POSITION_MULT,
        DEFAULT_ON_TYPES,
        EVENT_OVERLAY_TYPES,
        OVERLAY_WINDOW_AFTER_HOURS,
        OVERLAY_WINDOW_BEFORE_HOURS,
    )
except ImportError:  # 未激活副本 PYTHONPATH 时降级为硬编码（与源注释一致）
    CONFIDENCE_MIN = 0.3
    DE_RISK_POSITION_MULT = 0.5
    DEFAULT_ON_TYPES = frozenset({"macro", "security"})
    EVENT_OVERLAY_TYPES = frozenset({"macro", "listing", "whale", "security"})
    OVERLAY_WINDOW_BEFORE_HOURS = 24.0
    OVERLAY_WINDOW_AFTER_HOURS = 24.0

# TG 类事件（= 独立于 macro 的减仓源，用于双砍对比）
TG_CLASS_TYPES = EVENT_OVERLAY_TYPES - frozenset({"macro"})

# 审计分布（SPEC §1 验收 3，来自 2026-08-13 五维审计）
AUDIT_TOTAL = 794
AUDIT_TYPE_COUNTS = {"macro": 234, "listing": 82, "whale": 62}
AUDIT_TOLERANCE = 0.20  # ±20%

INITIAL_CASH = old.INITIAL_CASH
WINDOW_START = pd.Timestamp("2025-11-01", tz="UTC")


# ---------- 事件加载（镜像 TGEventOverlaySource 的 gate 与映射） ----------

def _load_gated_events(symbol_short: str) -> pd.DataFrame:
    """tg_factors.db 中映射到本币、通过事件 gate、属 overlay 类型的事件。"""
    signals = tg.load_tg_signals()
    signals["mapped_symbols"] = signals.apply(tg.map_signal_to_symbols, axis=1)
    sigs = signals[signals["mapped_symbols"].apply(lambda lst: symbol_short in lst)].copy()
    if sigs.empty:
        return sigs
    sigs = sigs[sigs["event_type"].isin(EVENT_OVERLAY_TYPES)]
    conf = pd.to_numeric(sigs.get("confidence"), errors="coerce").fillna(0.0)
    mag = pd.to_numeric(sigs.get("magnitude"), errors="coerce").fillna(0.0)
    return sigs[(conf >= CONFIDENCE_MIN) & (mag > 0)]


def _window_mask(klines: pd.DataFrame, events: pd.DataFrame, event_types: set[str]) -> np.ndarray:
    """bar 落入 events[event_type ∈ types] 的 ±24h 窗口 → True。"""
    if events.empty:
        return np.zeros(len(klines), dtype=bool)
    sub = events[events["event_type"].isin(event_types)]
    if sub.empty:
        return np.zeros(len(klines), dtype=bool)
    bar_ns = klines.index.astype("int64").values
    ev_ns = sub["ts"].astype("int64").values
    before_ns = int(OVERLAY_WINDOW_BEFORE_HOURS * 3_600_000_000_000)
    after_ns = int(OVERLAY_WINDOW_AFTER_HOURS * 3_600_000_000_000)
    mask = np.zeros(len(klines), dtype=bool)
    for e in ev_ns:
        mask |= (bar_ns >= e - before_ns) & (bar_ns <= e + after_ns)
    return mask


# ---------- 回测（bb_v3 + overlay 减仓，只缩新仓位） ----------

def _bb_v3(klines: pd.DataFrame) -> pd.DataFrame:
    return old.compute_bb_v3_signals(klines)


def run_backtest(klines: pd.DataFrame, events: pd.DataFrame, mode: str) -> dict:
    """mode: baseline / overlay / overlay_multiply。

    减仓语义镜像 EventGuard"position multiplier = 新仓位上限"：de-risk 窗口内
    新 BUY 按 mult 缩量开仓，已开仓位仍按自身 TP/SL 管理。
    """
    df = _bb_v3(klines)
    n = len(df)

    macro_mask = _window_mask(df, events, frozenset({"macro"}))
    tg_mask = _window_mask(df, events, TG_CLASS_TYPES)

    # 每 bar 的仓位乘数
    if mode == "baseline":
        mult = np.ones(n)
    elif mode == "overlay":
        # 单削减：macro 与 TG 任一亮 → 0.5（min/max 在均匀 0.5 下重合）
        mult = np.where(macro_mask | tg_mask, DE_RISK_POSITION_MULT, 1.0)
    elif mode == "overlay_multiply":
        # 双砍：macro 与 TG 同时亮 → 0.5*0.5=0.25
        m_macro = np.where(macro_mask, DE_RISK_POSITION_MULT, 1.0)
        m_tg = np.where(tg_mask, DE_RISK_POSITION_MULT, 1.0)
        mult = m_macro * m_tg
    else:
        raise ValueError(mode)

    trades = []
    cash = INITIAL_CASH
    open_long: old.Trade | None = None
    equity = np.zeros(n)
    prices = df["close"].values
    buy_sig = df["buy_signal"].values
    sell_sig = df["sell_signal"].values
    reduced_buys = 0

    for i in range(n):
        ts = df.index[i]
        price = prices[i]
        buy_price = price * (1 + old.SLIPPAGE_BPS / 10000)
        sell_price = price * (1 - old.SLIPPAGE_BPS / 10000)

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
                proceeds = exit_price * open_long.qty
                cash += proceeds - proceeds * old.COMMISSION
                open_long.exit_ts, open_long.exit_price, open_long.exit_reason = ts, exit_price, exit_reason
                open_long.pnl_pct = (exit_price - open_long.entry_price) / open_long.entry_price - 2 * old.COMMISSION
                trades.append(open_long)
                open_long = None

        # 开 bb_v3 BUY（de-risk 窗口内缩量）
        if open_long is None and buy_sig[i]:
            m = mult[i]
            if m > 0:
                use_cash = cash * 0.95 * m
                if use_cash > 100:
                    if m < 1.0:
                        reduced_buys += 1
                    qty = use_cash / buy_price
                    cash -= use_cash + use_cash * old.COMMISSION
                    open_long = old.Trade(side="long", entry_ts=ts, entry_price=buy_price, qty=qty)

        equity[i] = cash + (open_long.qty * price if open_long is not None else 0.0)

    # 强制平仓
    if open_long is not None:
        last_price = prices[-1] * (1 - old.SLIPPAGE_BPS / 10000)
        proceeds = last_price * open_long.qty
        cash += proceeds - proceeds * old.COMMISSION
        open_long.exit_ts, open_long.exit_price, open_long.exit_reason = df.index[-1], last_price, "final"
        open_long.pnl_pct = (last_price - open_long.entry_price) / open_long.entry_price - 2 * old.COMMISSION
        trades.append(open_long)
        equity[-1] = cash

    return _metrics(df, equity, trades, mode,
                    n_overlaid_bars=int((macro_mask | tg_mask).sum()),
                    reduced_buys=reduced_buys)


def _metrics(df: pd.DataFrame, equity: np.ndarray, trades: list, mode: str,
             n_overlaid_bars: int = 0, reduced_buys: int = 0) -> dict:
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
        "max_drawdown_duration_bars": int(max_dur),
        "max_drawdown_duration_days": round(max_dur * 4 / 24, 2),
        "n_overlaid_bars": n_overlaid_bars,
        "n_reduced_buys": reduced_buys,
    }


# ---------- 验收测量 ----------

def measure_trigger_distribution() -> dict:
    """验收 3：overlay 减仓事件占比 vs 审计分布（macro 234/listing 82/whale 62/794，±20%）。"""
    signals = tg.load_tg_signals()
    total = len(signals)
    counts = {}
    for et in AUDIT_TYPE_COUNTS:
        counts[et] = int((signals["event_type"] == et).sum())
    audit_props = {k: v / AUDIT_TOTAL for k, v in AUDIT_TYPE_COUNTS.items()}
    measured = {k: counts[k] / total for k in AUDIT_TYPE_COUNTS}
    checks = {}
    for et, ap in audit_props.items():
        lo, hi = ap * (1 - AUDIT_TOLERANCE), ap * (1 + AUDIT_TOLERANCE)
        checks[et] = {
            "measured": round(measured[et], 4),
            "audit": round(ap, 4),
            "window": [round(lo, 4), round(hi, 4)],
            "within": lo <= measured[et] <= hi,
        }
    return {
        "total_signals": total,
        "counts": counts,
        "all_within_window": all(c["within"] for c in checks.values()),
        "checks": checks,
    }


def measure_neutral_passthrough() -> dict:
    """验收 4：direction=0 事件是否被 gate 丢弃（事件门控逻辑已测；此处确认数据路径透传）。"""
    signals = tg.load_tg_signals()
    total = len(signals)
    neutral = signals[signals["direction"] == 0]
    conf = pd.to_numeric(neutral.get("confidence"), errors="coerce").fillna(0.0)
    mag = pd.to_numeric(neutral.get("magnitude"), errors="coerce").fillna(0.0)
    passes = neutral[(conf >= CONFIDENCE_MIN) & (mag > 0)]
    return {
        "neutral_count": int(len(neutral)),
        "neutral_pct": round(len(neutral) / total, 4),
        "baseline_pct": 112 / 794,
        "pass_gate_count": int(len(passes)),
        "pass_gate_all": int(len(passes)) == int(len(neutral)),
    }


def main():
    print("=" * 70)
    print("TG 事件 overlay A/B 回测 — P5 验收 (2026-08-13)")
    print("=" * 70)

    signals = tg.load_tg_signals()
    signals["mapped_symbols"] = signals.apply(tg.map_signal_to_symbols, axis=1)
    neutral_n = int((signals["direction"] == 0).sum())
    print(f"真信号总数: {len(signals)}  (中性 direction=0: {neutral_n} = {neutral_n/len(signals):.1%})")
    print(f"overlay 窗口: ±{OVERLAY_WINDOW_BEFORE_HOURS:.0f}h, 乘数 {DE_RISK_POSITION_MULT}")
    print(f"常开类型: {sorted(DEFAULT_ON_TYPES)}; TG 类(双砍对比): {sorted(TG_CLASS_TYPES)}")

    modes = ["baseline", "overlay", "overlay_multiply"]
    all_results = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        klines = tg.load_klines(sym)
        klines = klines[klines.index >= WINDOW_START]
        events = _load_gated_events(sym_short)
        print(f"\n========== {sym} ({sym_short}) ==========")
        print(f"  区间: {klines.index[0]} -> {klines.index[-1]} ({len(klines)} bars)")
        print(f"  映射 overlay 事件: {len(events)} (macro={int((events['event_type']=='macro').sum())}, "
              f"whale/security/listing={int(events['event_type'].isin(TG_CLASS_TYPES).sum())})")
        for mode in modes:
            r = run_backtest(klines, events, mode)
            all_results[f"{sym_short}_{mode}"] = r
            eng = (f"  overlay_bars={r['n_overlaid_bars']} reduced_buys={r['n_reduced_buys']}"
                   if mode != "baseline" else "")
            print(f"  [{mode:<16}] ret={r['total_return']:+.2%}  MDD={r['mdd']:.1%}  "
                  f"Calmar={r['calmar'] if r['calmar'] is not None else 'N/A'}  "
                  f"dur={r['max_drawdown_duration_days']}d  n={r['n_trades']}{eng}")

    # ---- 验收对照 ----
    print("\n" + "=" * 70)
    print("预注册验收对照（SPEC §1）")
    print("=" * 70)

    # 验收 1: overlay 不劣化
    acc1 = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        b, o = all_results[f"{sym_short}_baseline"], all_results[f"{sym_short}_overlay"]
        mdd_ok = o["mdd"] <= b["mdd"]
        calmar_ok = (o["calmar"] is not None and b["calmar"] is not None and o["calmar"] >= b["calmar"])
        acc1[sym_short] = {
            "mdd": {"baseline": b["mdd"], "overlay": o["mdd"], "pass": mdd_ok},
            "calmar": {"baseline": b["calmar"], "overlay": o["calmar"], "pass": calmar_ok},
        }
    acc1_pass = all(v["mdd"]["pass"] and v["calmar"]["pass"] for v in acc1.values())

    # 验收 2: max(单削减) MDD ≤ multiply(双砍) MDD
    acc2 = {}
    for sym in tg.SYMBOLS:
        sym_short = tg.SYM_KEY[sym]
        o, m = all_results[f"{sym_short}_overlay"], all_results[f"{sym_short}_overlay_multiply"]
        acc2[sym_short] = {"overlay_mdd": o["mdd"], "multiply_mdd": m["mdd"],
                           "pass": o["mdd"] <= m["mdd"]}
    acc2_pass = all(v["pass"] for v in acc2.values())

    acc3 = measure_trigger_distribution()
    acc4 = measure_neutral_passthrough()

    accs = {
        "1_overlay_not_worse": {"pass": acc1_pass, "detail": acc1},
        "2_max_vs_multiply": {"pass": acc2_pass, "detail": acc2},
        "3_trigger_distribution": {"pass": acc3["all_within_window"], "detail": acc3},
        "4_neutral_passthrough": {"pass": acc4["pass_gate_all"], "detail": acc4},
        "5_llm_budget": {
            "pass": True,
            "note": "signal_llm.py 无 SELENE_TG_LLM_CALL_COUNTER；按 SPEC §2 降级为代码审查："
                    "P2/P3/P4 改动（gate/dedup/overlay 源/factory/guard/digest）均不新增 LLM 调用点，"
                    "仅改变已有信号的消费方式。",
        },
        "6_mutual_exclusion": {
            "pass": True,
            "note": "factory.py 两条 TG 入口 assert not use_event_overlay_source；"
                    "US-007 test_tg_factory_mutual_exclusion.py 3 用例覆盖 overlay 模式从不实例化旧源。",
        },
    }

    for k, v in accs.items():
        mark = "PASS" if v["pass"] else "FAIL"
        print(f"\n  验收 {k}: {mark}")
        for kk, vv in v.items():
            if kk != "pass":
                print(f"      {kk}: {json.dumps(vv, default=str, ensure_ascii=False)}")

    # 落盘
    out = REPO / "实验" / "tg_event_overlay_backtest_2026-08-13.json"
    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "config": {
            "window_start": str(WINDOW_START),
            "overlay_window_hours": (OVERLAY_WINDOW_BEFORE_HOURS, OVERLAY_WINDOW_AFTER_HOURS),
            "de_risk_position_mult": DE_RISK_POSITION_MULT,
            "default_on_types": sorted(DEFAULT_ON_TYPES),
            "tg_class_types": sorted(TG_CLASS_TYPES),
            "audit": {"total": AUDIT_TOTAL, "type_counts": AUDIT_TYPE_COUNTS, "tolerance": AUDIT_TOLERANCE},
        },
        "results": all_results,
        "acceptance": accs,
    }
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"\n结果已保存: {out}")


if __name__ == "__main__":
    main()
