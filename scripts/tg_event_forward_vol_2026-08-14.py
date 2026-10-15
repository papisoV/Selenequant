"""
TG 事件前向波动率因子 (2026-08-14)
==================================

P5 证伪后方向: 事件不是方向水晶球, 但审计证实事件 bar 量能/振幅扩张
(macro 59-60% / listing 73-75% / whale 61-69% 量能百分位, p≈0.002)。
本脚本回答下一问题: 扩张是否**向前持续**? 能否构成可交易的波动率因子?

测量 (每条事件映射到 4h bar, 过事件门控 conf>=0.3 & mag>0, 同生产事件传感器):
  Q6 前向实现波动率: 事件 bar 之后 [t0+1 .. t0+H] bar 的实现波动率 vs 全窗口基线,
      H ∈ {1,6,12} (= 4h/24h/48h)。逐事件类型/币种 + permutation p。
  Q7 前向量能持续性: 同一前向窗口的平均量能百分位 vs 基线。
  Q8 分级检验: 事件 bar 内部, magnitude 与未来 H bar 实现波动率的 Spearman 秩相关
      (bigger event -> more forward vol?), permutation p。

实现波动率口径 (比审计的振幅-绝对收益和更严格):
  H=1: |r[t0]| (单 bar 绝对收益, bar t0 -> t0+1)
  H>=2: std(r[t0:t0+H], ddof=1)  (穿过前向窗口的 H 个连续收益的离散度)
基线 = 全部 bar 的无条件前向窗口分布。

事件对齐同审计: 消息时间 <= bar open 的所属 4h bar; 同 bar 多事件按 bar 去重。

用法: PYTHONIOENCODING=utf-8 python scripts/tg_event_forward_vol_2026-08-14.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
KLINES_DB = REPO / "services" / "local-api" / "data" / "klines.db"
TG_DB = REPO / "实验" / "tg_factors.db"
OUT_DIR = REPO / "实验"
OUT_DIR.mkdir(exist_ok=True)

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
SYM_KEY = {"BTC/USDT": "BTC", "ETH/USDT": "ETH", "SOL/USDT": "SOL"}
INTERVAL = "4h"
EVENT_TYPES = ["macro", "market", "regulatory", "listing", "whale", "security", "partnership"]
HORIZONS = {"4h": 1, "24h": 6, "48h": 12}
CONFIDENCE_MIN = 0.3
NPERM = 500
SEED = 42
WINDOW_START = pd.Timestamp("2025-11-01", tz="UTC")


def load_klines(symbol: str) -> pd.DataFrame:
    conn = sqlite3.connect(str(KLINES_DB))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE exchange='binance' AND market_type='futures' AND symbol=? AND interval=? "
        "ORDER BY timestamp",
        conn, params=(symbol, INTERVAL),
    )
    conn.close()
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("ts").sort_index()


def load_tg_signals() -> pd.DataFrame:
    conn = sqlite3.connect(str(TG_DB))
    df = pd.read_sql_query(
        "SELECT ts, symbols, event_type, direction, magnitude, confidence "
        "FROM tg_factors WHERE reasoning NOT LIKE 'noise:%'",
        conn,
    )
    conn.close()
    df["ts"] = pd.to_datetime(df["ts"])
    df["symbols_list"] = df["symbols"].apply(lambda s: json.loads(s) if s else [])
    return df


def map_signal_to_symbols(symbols_list: list[str]) -> list[str]:
    if "*" in symbols_list:
        return ["BTC", "ETH", "SOL"]
    return [s for s in symbols_list if s in ("BTC", "ETH", "SOL")]


def gated(signals: pd.DataFrame) -> pd.DataFrame:
    """事件门控 (同 signal_engine._compute_is_signal 的事件传感器语义)."""
    sigs = signals.copy()
    sigs["magnitude_f"] = pd.to_numeric(sigs.get("magnitude"), errors="coerce").fillna(0.0)
    sigs["confidence_f"] = pd.to_numeric(sigs.get("confidence"), errors="coerce").fillna(0.0)
    return sigs[(sigs["magnitude_f"] > 0) & (sigs["confidence_f"] >= CONFIDENCE_MIN)]


def spearman_ic(x: np.ndarray, y: np.ndarray) -> float:
    """秩相关 (pandas 平均 ties 处理) 等价 Spearman."""
    rx = pd.Series(x).rank(method="average").values
    ry = pd.Series(y).rank(method="average").values
    return float(np.corrcoef(rx, ry)[0, 1])


def permutation_p(obs: float, nulls: np.ndarray) -> float:
    """单侧 (>=) permutation p, +1 平滑 (同审计口径)."""
    return (float((nulls >= obs).mean()) * NPERM + 1) / (NPERM + 1)


def analyze_symbol(sym: str, signals: pd.DataFrame, rng: np.random.Generator) -> dict:
    sym_short = SYM_KEY[sym]
    k = load_klines(sym)
    k = k[k.index >= WINDOW_START]
    close = k["close"].values
    n = len(k)
    r = close[1:] / close[:-1] - 1          # r[i] = bar i -> i+1
    pct = k["volume"].rank(pct=True).values * 100

    # 事件 bar 对齐 + 按 bar 去重
    opens = k.index.values.astype("int64") // 10**6
    ss = signals[signals["mapped"].apply(lambda lst: sym_short in lst)]
    if ss.empty:
        return {"n_bars": n, "types": {}}
    sig_ms = (ss["ts"].astype("int64") // 10**6).values
    bar_idx = np.searchsorted(opens, sig_ms, side="right") - 1
    ss = ss.assign(_bar=bar_idx)
    ss = ss[(ss["_bar"] >= 0) & (ss["_bar"] < n - 1)]
    valid = ss[~ss["_bar"].duplicated(keep="first")]  # 同 bar 多事件取首条

    # 每 bar 的 max magnitude (Q8 分级因子用) — 按类型分别构建 (per-type 分级)
    def type_mag_at_bar(ev: np.ndarray, mags: np.ndarray) -> np.ndarray:
        arr = np.zeros(n)
        np.maximum.at(arr, ev, mags)
        return arr

    def fwd_rv(t0: int, H: int) -> float:
        # 前向窗口 [t0+1..t0+H] 实现波动率
        if H == 1:
            return float(abs(r[t0]))
        return float(np.std(r[t0:t0 + H], ddof=1))

    def fwd_vol_pct(t0: int, H: int) -> float:
        # 前向窗口 [t0+1..t0+H] 平均量能百分位
        return float(np.mean(pct[t0 + 1:t0 + H + 1]))

    types_out: dict[str, dict] = {}
    for et in EVENT_TYPES:
        et_rows = valid.loc[valid["event_type"] == et]
        ev = np.sort(np.unique(et_rows["_bar"].values))
        ev = ev[ev < n - 1]
        # per-type max magnitude per bar (Q8 分级检验用)
        mag_type = np.zeros(n)
        np.maximum.at(mag_type, et_rows["_bar"].values, et_rows["magnitude_f"].values)

        per_h: dict[str, dict] = {}
        for hname, H in HORIZONS.items():
            max_t0 = n - H - 1
            ev_h = ev[ev <= max_t0]
            if len(ev_h) < 4:
                per_h[hname] = None
                continue
            base_rv = np.array([fwd_rv(t0, H) for t0 in range(max_t0 + 1)])
            obs_rv = float(np.mean([fwd_rv(i, H) for i in ev_h]))
            nulls_rv = np.array([
                float(np.mean([fwd_rv(j, H) for j in rng.choice(max_t0 + 1, size=len(ev_h), replace=False)]))
                for _ in range(NPERM)
            ])
            # Q7 前向量能
            obs_v = float(np.mean([fwd_vol_pct(i, H) for i in ev_h]))
            nulls_v = np.array([
                float(np.mean([fwd_vol_pct(j, H) for j in rng.choice(max_t0 + 1, size=len(ev_h), replace=False)]))
                for _ in range(NPERM)
            ])
            # Q8 分级: 事件 bar 内部 magnitude vs 前向实现波动 (bigger event -> more vol?)
            ev_mags = mag_type[ev_h]
            y_ev = np.array([fwd_rv(i, H) for i in ev_h])
            ic = spearman_ic(ev_mags, y_ev)
            nulls_ic = np.array([
                spearman_ic(rng.permutation(ev_mags), y_ev) for _ in range(NPERM)
            ])
            p_ic = (float((np.abs(nulls_ic) >= abs(ic)).mean()) * NPERM + 1) / (NPERM + 1)

            per_h[hname] = {
                "n_events": int(len(ev_h)),
                "rv_obs": obs_rv,
                "rv_base": float(base_rv.mean()),
                "rv_ratio": obs_rv / float(base_rv.mean()),
                "rv_p": permutation_p(obs_rv, nulls_rv),
                "vol_pct_obs": obs_v,
                "vol_pct_base": 50.0,
                "vol_pct_p": permutation_p(obs_v, nulls_v),
                "ic_graded": ic,
                "ic_graded_p": p_ic,
            }
        types_out[et] = per_h

    # 合并全类型 (所有 gated 事件)
    all_ev = np.sort(np.unique(valid["_bar"].values))
    all_ev = all_ev[all_ev < n - 1]
    mag_all = np.zeros(n)
    np.maximum.at(mag_all, valid["_bar"].values, valid["magnitude_f"].values)
    per_h_all = {}
    for hname, H in HORIZONS.items():
        max_t0 = n - H - 1
        ev_h = all_ev[all_ev <= max_t0]
        if len(ev_h) < 4:
            per_h_all[hname] = None
            continue
        base_rv = np.array([fwd_rv(t0, H) for t0 in range(max_t0 + 1)])
        obs_rv = float(np.mean([fwd_rv(i, H) for i in ev_h]))
        nulls_rv = np.array([
            float(np.mean([fwd_rv(j, H) for j in rng.choice(max_t0 + 1, size=len(ev_h), replace=False)]))
            for _ in range(NPERM)
        ])
        obs_v = float(np.mean([fwd_vol_pct(i, H) for i in ev_h]))
        ev_mags = mag_all[ev_h]
        y_ev = np.array([fwd_rv(i, H) for i in ev_h])
        ic = spearman_ic(ev_mags, y_ev)
        nulls_ic = np.array([spearman_ic(rng.permutation(ev_mags), y_ev) for _ in range(NPERM)])
        per_h_all[hname] = {
            "n_events": int(len(ev_h)),
            "rv_obs": obs_rv,
            "rv_base": float(base_rv.mean()),
            "rv_ratio": obs_rv / float(base_rv.mean()),
            "rv_p": permutation_p(obs_rv, nulls_rv),
            "vol_pct_obs": obs_v,
            "vol_pct_base": 50.0,
            "vol_pct_p": permutation_p(obs_v, nulls_v),
            "ic_graded": ic,
            "ic_graded_p": p_ic,
        }
    types_out["ALL"] = per_h_all
    return {"n_bars": n, "types": types_out}


def main():
    rng = np.random.default_rng(SEED)
    signals = load_tg_signals()
    signals["mapped"] = signals["symbols_list"].apply(map_signal_to_symbols)
    sigs = gated(signals)
    print(f"klines: {KLINES_DB}")
    print(f"gated 真信号: {len(sigs)} / 全部 {len(signals)}")
    print(f"horizons: {HORIZONS} (bar 数), 门控 conf>={CONFIDENCE_MIN} & mag>0")

    results = {}
    for sym in SYMBOLS:
        results[sym] = analyze_symbol(sym, sigs, rng)

    # 控制台表格: 每币种/类型 x horizon
    for sym in SYMBOLS:
        print(f"\n{'='*78}\n{sym}\n{'='*78}")
        for et, per_h in results[sym]["types"].items():
            row = f"{et:<12}"
            for hname in HORIZONS:
                q = per_h[hname]
                if q is None:
                    row += f" | {hname:<3} n<4 "
                    continue
                row += (f" | {hname:<3} n={q['n_events']:<4} "
                        f"rv={q['rv_ratio']:.2f}x(p={q['rv_p']:.3f}) "
                        f"vol={q['vol_pct_obs']:5.1f}%(p={q['vol_pct_p']:.3f}) "
                        f"grad={q['ic_graded']:+.3f}(p={q['ic_graded_p']:.3f})")
            print(row)

    out = OUT_DIR / "tg_event_forward_vol_2026-08-14.json"
    payload = {
        "config": {
            "interval": INTERVAL, "window_start": str(WINDOW_START),
            "horizons_bars": HORIZONS, "confidence_min": CONFIDENCE_MIN,
            "nperm": NPERM, "seed": SEED,
        },
        "results": json.loads(json.dumps(results, default=float)),
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n结果已保存: {out}")


if __name__ == "__main__":
    main()
