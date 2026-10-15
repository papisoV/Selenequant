"""Funding carry+reversal simulation (gate 2) — 2026-09-26.

Turns the archived multi-horizon finding (funding_level 7d IC -0.11..-0.17,
high funding -> negative forward return) into a testable rule:

  at each daily (00:00 UTC) funding settlement, compute the funding
  percentile on a trailing 180-obs window (no look-ahead);
    - pctile > 90  -> SHORT (crowded longs pay you carry; reversion tailwind)
    - pctile < 10  -> LONG  (crowded shorts; mirror logic)
    - otherwise    -> no position
  hold exactly 7 days, flat otherwise, one position at a time per symbol.

Legs (pre-registered decomposition):
  price leg  = signed (exit_open / entry_open - 1)
  carry leg  = -side * sum(rates over (entry, exit] settlements) —
               positive funding means LONGS pay SHORTS, so a short
               (side=-1) RECEIVES +rate and a long PAYS -rate. The entry
               settlement itself is NOT collected (position opens at/after
               settlement); the exit-day settlement IS (still held then).
  costs      = 0.075% per side (taker + slippage) on entry + exit notional,
               ~0.15% round trip, single parameter SIDE_COST.

Verdict rule (pre-registered), on the ETH+SOL pooled episode set:
  GO           pooled mean episode PnL > 0 AND bootstrap 95% CI excludes 0
               AND both legs' means >= 0
  NO-GO        pooled mean episode PnL <= 0
  INCONCLUSIVE mean > 0 but CI crosses 0  (data keeps accruing; cheap re-run
               quarterly; not a death sentence)
BTC reported separately (3-month sign flip known from the 0925 sweep;
expected weak/negative — not part of the verdict).

Signal-study caveat: fixed notional, no portfolio sizing, no liquidation
model, no funding-cap edge cases. GO here is a mandate for a proper
multi-day-track design, NOT a production go-live.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TypedDict

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent

KLINES_DB = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
DERIV_DB = PROJECT_ROOT / "data" / "derivatives.db"
OUT_PATH = PROJECT_ROOT / "scripts" / "funding_carry_reversal_sim_2026-09-26.json"

SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")
PCT_WINDOW = 180  # trailing funding obs (~60d) for the percentile
LOW, HIGH = 10.0, 90.0  # entry bands
HOLD_DAYS = 7
SIDE_COST = 0.00075  # 0.075% per side (taker + slippage)
N_BOOT = 2000
RNG_SEED = 20260926


class SimSummary(TypedDict, total=False):
    label: str
    n: int
    mean_pnl: float
    median_pnl: float
    win_rate: float
    ci95: list[float]
    mean_price_leg: float
    mean_carry_leg: float
    mean_cost: float
    mean_short_pnl: float | None
    n_short: int
    mean_long_pnl: float | None
    n_long: int
    episodes: list[dict[str, object]]


def load_daily_bars(symbol: str) -> pd.DataFrame:
    conn = sqlite3.connect(str(KLINES_DB))
    try:
        df = pd.read_sql_query(
            "SELECT timestamp, open FROM klines "
            "WHERE symbol=? AND interval='1d' AND market_type='futures' "
            "ORDER BY timestamp",
            conn,
            params=(symbol,),
        )
    finally:
        conn.close()
    df["timestamp"] = df["timestamp"].astype("int64")
    return df


def load_funding(symbol: str) -> pd.DataFrame:
    conn = sqlite3.connect(str(DERIV_DB))
    try:
        df = pd.read_sql_query(
            "SELECT timestamp, funding_rate FROM funding_rates "
            "WHERE exchange='binance' AND symbol=? ORDER BY timestamp",
            conn,
            params=(symbol,),
        )
    finally:
        conn.close()
    df["timestamp"] = df["timestamp"].astype("int64")
    df["funding_rate"] = df["funding_rate"].astype(float)
    return df


def funding_pctile_at(fr: pd.DataFrame, ts_ms: int) -> float:
    """Trailing percentile of the last-known funding obs vs prior 180 obs."""
    prior = fr[fr["timestamp"] < ts_ms]
    if len(prior) < 60:
        return float("nan")
    window = prior["funding_rate"].iloc[-PCT_WINDOW:]
    current = float(prior["funding_rate"].iloc[-1])
    return float((window < current).mean() * 100)


class EpisodeList(TypedDict):
    n_episodes: int
    episodes: list[dict[str, object]]


def run_symbol(symbol: str) -> EpisodeList:
    bars = load_daily_bars(symbol)
    fr = load_funding(symbol)

    # daily settlements: funding obs whose UTC date has a matching 1d bar
    bars_open = bars.set_index("timestamp")["open"]
    bars_dates = {ts // 86_400_000: ts for ts in bars["timestamp"]}

    episodes: list[dict[str, object]] = []
    in_pos_until = -1  # ms timestamp; serial non-overlapping positions
    # only 00:00 UTC settlements: the 1d bar open is the price at that exact
    # moment — trading 08:00/16:00 settlements off the day's open would be
    # look-ahead (the signal exists hours after the price we'd fill at)
    fr_daily = fr[fr["timestamp"] % 86_400_000 < 60_000].reset_index(drop=True)
    for _, row in fr_daily.iterrows():
        ts = int(row["timestamp"])
        day = ts // 86_400_000
        if day not in bars_dates or ts <= in_pos_until:
            continue
        pct = funding_pctile_at(fr, ts)
        if np.isnan(pct) or LOW < pct < HIGH:
            continue
        side = -1 if pct > HIGH else 1  # >90 short, <10 long

        entry_day = day
        exit_day = day + HOLD_DAYS
        if exit_day not in bars_dates:
            continue
        entry_ts, exit_ts = bars_dates[entry_day], bars_dates[exit_day]
        entry_px, exit_px = (
            float(bars_open.loc[entry_ts]),
            float(bars_open.loc[exit_ts]),
        )

        price_leg = side * (exit_px / entry_px - 1.0)
        # carry: settlements strictly after entry, up to and including exit day.
        # positive funding = longs pay shorts -> short receives, long pays.
        mask = (fr["timestamp"] > ts) & (fr["timestamp"] <= exit_ts)
        carry = -side * float(fr.loc[mask, "funding_rate"].sum())
        cost = SIDE_COST * (1.0 + exit_px / entry_px)
        pnl = price_leg + carry - cost

        in_pos_until = exit_ts
        episodes.append(
            {
                "entry_date": pd.Timestamp(ts, unit="ms").strftime("%Y-%m-%d"),
                "side": "short" if side < 0 else "long",
                "funding_pctile": round(pct, 1),
                "entry_px": entry_px,
                "exit_px": exit_px,
                "price_leg": round(price_leg, 6),
                "carry_leg": round(carry, 6),
                "cost": round(cost, 6),
                "pnl": round(pnl, 6),
            }
        )

    ep = pd.DataFrame(episodes)
    return {
        "n_episodes": int(len(ep)),
        "episodes": ep.to_dict(orient="records"),
    }


def bootstrap_ci(
    vals: np.ndarray, n_boot: int = N_BOOT, seed: int = RNG_SEED
) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    n = len(vals)
    if n < 5:
        return (float("nan"), float("nan"))
    means = rng.choice(vals, size=(n_boot, n), replace=True).mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def summarize(label: str, eps: pd.DataFrame) -> SimSummary:
    out: SimSummary = {
        "label": label,
        "n": int(len(eps)),
        "ci95": [float("nan"), float("nan")],
    }
    if eps.empty:
        return out
    vals = eps["pnl"].to_numpy()
    lo, hi = bootstrap_ci(vals)
    out.update(
        {
            "mean_pnl": float(vals.mean()),
            "median_pnl": float(np.median(vals)),
            "win_rate": float((vals > 0).mean()),
            "ci95": [lo, hi],
            "mean_price_leg": float(eps["price_leg"].mean()),
            "mean_carry_leg": float(eps["carry_leg"].mean()),
            "mean_cost": float(eps["cost"].mean()),
            "mean_short_pnl": float(eps.loc[eps["side"] == "short", "pnl"].mean())
            if (eps["side"] == "short").any()
            else None,
            "n_short": int((eps["side"] == "short").sum()),
            "mean_long_pnl": float(eps.loc[eps["side"] == "long", "pnl"].mean())
            if (eps["side"] == "long").any()
            else None,
            "n_long": int((eps["side"] == "long").sum()),
        }
    )
    return out


def main() -> None:
    results: dict[str, SimSummary] = {}
    per_symbol_frames: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        res = run_symbol(symbol)
        per_symbol_frames[symbol] = pd.DataFrame(res["episodes"])
        results[symbol] = summarize(symbol, per_symbol_frames[symbol])
        results[symbol]["episodes"] = res["episodes"]

    pool = pd.concat(
        [per_symbol_frames[s].assign(symbol=s) for s in ("ETH/USDT", "SOL/USDT")],
        ignore_index=True,
    )
    verdict_block = summarize("ETH+SOL pooled", pool)

    mean_pnl = float(verdict_block.get("mean_pnl", 0.0))
    ci_raw = verdict_block.get("ci95", [float("nan"), float("nan")])
    ci = [float(x) for x in ci_raw]
    ci_excl_0 = ci[0] > 0 or ci[1] < 0
    legs_ok = (
        float(verdict_block.get("mean_price_leg", -1.0)) >= 0
        and float(verdict_block.get("mean_carry_leg", -1.0)) >= 0
    )
    if mean_pnl <= 0:
        verdict = "NO-GO"
    elif ci_excl_0 and legs_ok:
        verdict = "GO"
    else:
        verdict = "INCONCLUSIVE"

    out = {
        "date": "2026-09-26",
        "params": {
            "pct_window": PCT_WINDOW,
            "bands": [LOW, HIGH],
            "hold_days": HOLD_DAYS,
            "side_cost": SIDE_COST,
            "settlements_traded": "00:00 UTC only (1d bar open = fill price)",
            "carry_sign_convention": "-side * sum(rates in (entry, exit]): short receives positive funding",
            "n_boot": N_BOOT,
            "verdict_rule": "GO iff pooled mean>0 AND CI95 excludes 0 AND both legs>=0; NO-GO iff mean<=0; else INCONCLUSIVE",
        },
        "results": results,
        "pooled_eth_sol": verdict_block,
        "verdict": verdict,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")

    for label in (*SYMBOLS, "ETH+SOL pooled"):
        b = (
            summarize(label, per_symbol_frames[label])
            if label in per_symbol_frames
            else verdict_block
        )
        ci_b = [float(x) for x in b.get("ci95", [float("nan"), float("nan")])]
        print(
            f"{label:16s} n={int(b.get('n', 0)):3d}  meanPnL {float(b.get('mean_pnl', 0.0)):+.5f}  "
            f"WR {float(b.get('win_rate', 0.0)):.2f}  CI95 [{ci_b[0]:+.5f},{ci_b[1]:+.5f}]  "
            f"price {float(b.get('mean_price_leg', 0.0)):+.5f}  carry {float(b.get('mean_carry_leg', 0.0)):+.5f}"
        )
    print(f"\nVERDICT: {verdict}")
    print(f"saved -> {OUT_PATH.name}")


if __name__ == "__main__":
    main()
