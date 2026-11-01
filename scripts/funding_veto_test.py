"""Funding veto test (gate 1) — does extreme funding hurt V52 entries?

Pre-registered hypothesis: entries taken at extreme funding percentiles
(crowded positioning) have systematically worse quality (avg PnL / win
rate) than mid-band entries. If confirmed, V52 gains a free entry veto
rule (no kernel change); if not, the idea is archived.

Method:
  - Reuse data/v10_macro_guard_report.json (3243 tagged V5.2 trades,
    BTC/ETH/SOL, 2024-01..2026-09).
  - For each trade: funding percentile at entry_ts, computed on a
    TRAILING window of funding observations (no look-ahead), per symbol.
  - Bands (pre-registered): low <10 / mid 40-60 / high >90, plus all
    deciles for the full picture.

Verdict rule (pre-registered): veto is justified iff BOTH
  (a) avg PnL of combined low+high extreme trades < 50% of mid-band avg PnL
      (relative bar, since PnL scale is notional-dependent), AND
  (b) extreme win rate trails mid-band win rate by >= 5pp on every symbol.
Otherwise: archive.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TypedDict

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    "packages/core",
    "packages/shared-types",
    "packages/backtest-engine",
    "packages/market-data",
    "packages/exchange-sdk",
    "packages/paper-engine",
    "packages/strategy-engine",
    "packages/llm-adapter",
    "packages/agent-runtime",
    "packages/factor-research",
    "packages/alpha-core",
):
    sys.path.insert(0, str(PROJECT_ROOT / _p))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from selene_space_market.derivatives_db import DerivativesDB

DERIV_PATH = PROJECT_ROOT / "data" / "derivatives.db"
TRADES_PATH = PROJECT_ROOT / "data" / "v10_macro_guard_report.json"
OUT_PATH = PROJECT_ROOT / "scripts" / "funding_veto_test_2026-09-26.json"

# pre-registered bands (funding percentile at entry)
LOW, HIGH = 10.0, 90.0
MID_LO, MID_HI = 40.0, 60.0


class Agg(TypedDict, total=False):
    n: int
    avg_pnl: float
    win_rate: float
    sum_pnl: float


def load_funding(symbol: str) -> pd.DataFrame:
    db = DerivativesDB(str(DERIV_PATH))
    db.open()
    try:
        return db.get_funding_rates("binance", symbol, 0, 2**62)
    finally:
        db.close()


def funding_pctile_at(funding: pd.DataFrame, entry_ts_ms: int) -> float:
    """Trailing percentile of the most recent funding obs vs prior history.

    Uses only funding obs with timestamp <= entry_ts (no look-ahead).
    Compares the last-known rate against the trailing 180-obs (~60d) window.
    """
    fr = funding[funding["timestamp"] <= entry_ts_ms]
    if len(fr) < 60:
        return float("nan")
    window = fr["funding_rate"].iloc[-180:]
    current = float(fr["funding_rate"].iloc[-1])
    return float((window < current).mean() * 100)


def main() -> None:
    trades = json.loads(TRADES_PATH.read_text(encoding="utf-8"))
    df = pd.DataFrame(trades)
    df["entry_ts"] = pd.to_numeric(df["entry_ts"], errors="coerce")
    df = df.dropna(subset=["entry_ts"])

    funding_cache: dict[str, pd.DataFrame] = {}
    pctiles: list[float] = []
    for sym, ts in zip(df["symbol"], df["entry_ts"]):
        if sym not in funding_cache:
            funding_cache[sym] = load_funding(sym)
        pctiles.append(funding_pctile_at(funding_cache[sym], int(ts)))
    df["funding_pctile"] = pctiles

    df["pnl"] = pd.to_numeric(df["pnl"], errors="coerce")
    df["band"] = pd.cut(
        df["funding_pctile"],
        bins=[-0.1, LOW, 25, MID_LO, MID_HI, 75, HIGH, 100.1],
        labels=["<10", "10-25", "25-40", "40-60", "60-75", "75-90", ">90"],
    )

    out: dict[str, object] = {
        "date": "2026-09-26",
        "n_trades": int(len(df)),
        "n_trades_scored": int(df["funding_pctile"].notna().sum()),
        "bands_low_high": [LOW, HIGH],
        "mid_band": [MID_LO, MID_HI],
        "verdict_rule": "veto iff extreme avgPnL < 50% of mid AND every symbol's extreme WR trails mid by >=5pp",
        "by_band": {},
        "by_symbol_band": {},
    }
    by_band: dict[str, Agg] = {}

    def agg(sub: pd.DataFrame) -> Agg:
        n = len(sub)
        if n == 0:
            return {"n": 0}
        return {
            "n": int(n),
            "avg_pnl": float(sub["pnl"].mean()),
            "win_rate": float((sub["pnl"] > 0).mean()),
            "sum_pnl": float(sub["pnl"].sum()),
        }

    for band, sub in df.groupby("band", observed=True):
        by_band[str(band)] = agg(sub)
    out["by_band"] = by_band

    by_sym: dict[str, dict[str, Agg]] = {}
    for sym, sym_df in df.groupby("symbol"):
        sym_bands: dict[str, Agg] = {}
        extreme = sym_df[
            (sym_df["funding_pctile"] < LOW) | (sym_df["funding_pctile"] > HIGH)
        ]
        mid = sym_df[
            (sym_df["funding_pctile"] >= MID_LO) & (sym_df["funding_pctile"] <= MID_HI)
        ]
        sym_bands["extreme"] = agg(extreme)
        sym_bands["mid"] = agg(mid)
        by_sym[str(sym)] = sym_bands
    out["by_symbol_band"] = by_sym

    # verdict
    ext_all = df[(df["funding_pctile"] < LOW) | (df["funding_pctile"] > HIGH)]
    mid_all = df[(df["funding_pctile"] >= MID_LO) & (df["funding_pctile"] <= MID_HI)]
    a_ext, a_mid = agg(ext_all), agg(mid_all)
    cond_a = a_ext["avg_pnl"] < 0.5 * a_mid["avg_pnl"]
    cond_b_parts = []
    for sym, sb in by_sym.items():
        e, m = sb["extreme"], sb["mid"]
        ok = (
            e.get("n", 0) >= 30
            and m.get("n", 0) >= 30
            and e["win_rate"] <= m["win_rate"] - 0.05
        )
        cond_b_parts.append(ok)
    cond_b = all(cond_b_parts)
    verdict_block = {
        "extreme": a_ext,
        "mid": a_mid,
        "cond_a_relative_pnl": bool(cond_a),
        "cond_b_per_symbol_wr": cond_b_parts,
        "veto_justified": bool(cond_a and cond_b),
    }
    out["verdict"] = verdict_block

    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"scored {out['n_trades_scored']}/{out['n_trades']} trades")
    print("\n=== by band (all symbols) ===")
    for b, s in by_band.items():
        if s.get("n"):
            print(
                f"  {b:7s} n={s['n']:4d}  avgPnL {s['avg_pnl']:+8.2f}  "
                f"WR {s['win_rate']:.3f}"
            )
    print(
        f"\ncond_a (extreme avgPnL < 50% mid): {verdict_block['cond_a_relative_pnl']}"
    )
    print(f"cond_b (per-symbol WR gap >=5pp): {verdict_block['cond_b_per_symbol_wr']}")
    print(f"VETO JUSTIFIED: {verdict_block['veto_justified']}")
    print(f"saved -> {OUT_PATH.name}")


if __name__ == "__main__":
    main()
