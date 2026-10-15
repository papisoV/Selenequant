"""Single-symbol offline grid replay over selene klines.db (research pipeline step 1).

Orchestration only — all engine logic lives in selene_space_grid.

Drives the ported GridEngine over the last N closed bars for one symbol,
deriving bounds from the pure-ATR path, and reports fills / cycles / PnL /
max drawdown with sanity checks (no negative cash, >0 fills, finite PnL).

Usage:
    python scripts/grid_replay.py                                # BTC/USDT 30m 5000 bars
    python scripts/grid_replay.py --symbol ETH/USDT --bars 2000
    python scripts/grid_replay.py --gate-mode er-gated           # reserved for US-5
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (
    PROJECT_ROOT,
    os.path.join(PROJECT_ROOT, "packages", "grid-engine"),
    os.path.join(PROJECT_ROOT, "packages", "shared-types"),
    os.path.join(PROJECT_ROOT, "packages", "backtest-engine"),
    os.path.join(PROJECT_ROOT, "packages", "strategy-engine"),
):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dataclasses import replace

from selene_space_grid import GridSpec
from selene_space_grid.grid_engine import GridEngine

QUANTDINGER_SCRIPTS = r"<local-path-redacted>"
DEFAULT_DB = os.path.join(PROJECT_ROOT, "services", "local-api", "data", "klines.db")
DEFAULT_SYMBOL = "BTC/USDT"
DEFAULT_INTERVAL = "30m"
DEFAULT_BARS = 5000
GRID_COUNT = 8
BUDGET_QUOTE = 100.0
INITIAL_POSITION_PCT = 0.6
MAX_OPEN_ORDERS = 4
ATR_MULT = 1.5
MAKER_RATE = 0.0002
TAKER_RATE = 0.0005
GATE_MODES = ("always-on", "er-gated", "adx-gated")


def load_bars(db_path: str, symbol: str, interval: str, bars: int) -> list[dict]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """SELECT timestamp, open, high, low, close, volume, closed
               FROM klines
               WHERE exchange='binance' AND market_type='futures'
                     AND symbol=? AND interval=?
               ORDER BY timestamp DESC LIMIT ?""",
            (symbol, interval, bars),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise SystemExit(f"no klines for {symbol} {interval} in {db_path}")
    rows.reverse()
    return [
        {
            "time": int(ts),
            "open": float(o),
            "high": float(h),
            "low": float(lo),
            "close": float(c),
            "volume": float(v),
            "closed": bool(cl),
        }
        for ts, o, h, lo, c, v, cl in rows
    ]


def derive_bounds(rows: list[dict], atr_mult: float) -> tuple[float, float, float]:
    """Pure-ATR bounds derivation (diagnosis=None path of the source)."""
    # deliberately reuse the original implementation so bounds semantics are
    # shared with the parity harness; import is offline-safe (no HTTP at module level)
    if QUANTDINGER_SCRIPTS not in sys.path:
        sys.path.insert(0, QUANTDINGER_SCRIPTS)
    import grid_sim  # noqa: PLC0415

    bounds: tuple[float, float, float] = grid_sim.derive_bounds(
        rows, None, atr_mult=atr_mult
    )
    return bounds


def max_drawdown(equity_curve: list[float]) -> float:
    peak = -math.inf
    mdd = 0.0
    for eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return mdd


def _bars_frame(window: list[dict]):
    import pandas as pd  # noqa: PLC0415 — heavy import kept inside gated path

    return pd.DataFrame(window)


def replay(
    symbol: str, interval: str, bars: int, db_path: str, gate_mode: str = "always-on"
) -> dict:
    rows = load_bars(db_path, symbol, interval, bars)
    closed_rows = [r for r in rows if r["closed"]]
    if not closed_rows:
        raise SystemExit("no closed bars found")

    bounds = derive_bounds(closed_rows, ATR_MULT)
    lower, upper, reference = bounds
    spec = GridSpec(
        symbol=symbol,
        grid_count=GRID_COUNT,
        total_budget_quote=BUDGET_QUOTE,
        initial_position_pct=INITIAL_POSITION_PCT,
        lower=lower,
        upper=upper,
        reference=reference,
        max_open_orders=MAX_OPEN_ORDERS,
    )
    engine = GridEngine(
        spec, {"data": {}}, maker_rate=MAKER_RATE, taker_rate=TAKER_RATE
    )

    gate = None
    if gate_mode != "always-on":
        from selene_space_grid.cycle_gate import ADXGateSignal, CycleGate, ERGateSignal

        signal = ERGateSignal() if gate_mode == "er-gated" else ADXGateSignal()
        # backtest posture: a single stop vote tears the grid down (the source
        # passes dangerous_stop_needs=1 in backtests for the same reason)
        gate = CycleGate(signal, dangerous_stop_needs=1)

    engine.compute_levels()
    engine.seed_initial()
    engine.rearm()

    fills = []
    equity_curve = [BUDGET_QUOTE]
    cumulative_pnl = 0.0
    cumulative_fees = 0.0
    gate_events = []

    def _absorb(events) -> None:
        nonlocal cumulative_pnl, cumulative_fees
        for ev in events:
            fills.append(ev.to_dict())
            cumulative_pnl += ev.realized_pnl
            cumulative_fees += ev.fee
            equity_curve.append(BUDGET_QUOTE + cumulative_pnl - cumulative_fees)

    WARMUP = 60  # bars before the gate may fire (indicator warmup margin)
    for i, bar in enumerate(closed_rows):
        _absorb(engine.check_fills([bar]))
        engine.rearm()

        if gate is not None and i >= WARMUP:
            window = closed_rows[max(0, i - 99) : i + 1]
            decision = gate.evaluate(_bars_frame(window))
            grid_active = any(c.state != "disabled" for c in engine.cells)
            if (
                decision.action == "stop"
                and grid_active
                and any(
                    c.state in ("exit_ready", "exit_pending") and c.qty > 0
                    for c in engine.cells
                )
            ):
                gate_events.append(
                    {
                        "ts": bar["time"],
                        "action": "stop",
                        "value": decision.value,
                        "reason": decision.reason,
                    }
                )
                _absorb(engine.waterfall_exit("gate_stop", bar["close"], bar["time"]))
            elif decision.action == "start" and not grid_active:
                gate_events.append(
                    {
                        "ts": bar["time"],
                        "action": "start",
                        "value": decision.value,
                        "reason": decision.reason,
                    }
                )
                # fresh anchor from the recent window (GridSpec is frozen —
                # rebuild via dataclasses.replace), then rearm
                lo, hi, ref = derive_bounds(window, ATR_MULT)
                spec = replace(spec, lower=lo, upper=hi, reference=ref)
                engine.spec = spec
                engine.cells = engine.compute_levels()
                engine.initial_allocated = False
                engine.seed_initial()
                engine.rearm()

    waterfall_pnl = 0.0
    waterfall_fees = 0.0
    waterfall_events = []
    # end-of-replay teardown at the last close (marks unrealized as realized)
    last_close = closed_rows[-1]["close"]
    wf = engine.waterfall_exit("end_of_replay", last_close, closed_rows[-1]["time"])
    for ev in wf:
        waterfall_events.append(ev.to_dict())
        waterfall_pnl += ev.realized_pnl
        waterfall_fees += ev.fee

    total_fills = fills + waterfall_events
    total_pnl = cumulative_pnl + waterfall_pnl
    total_fees = cumulative_fees + waterfall_fees
    cycles = sum(1 for e in fills if e["kind"] == "exit")
    mdd = max_drawdown(equity_curve)

    sanity = {
        "fills_gt_zero": len(total_fills) > 0,
        "pnl_finite": math.isfinite(total_pnl),
        "cash_floor_ok": (BUDGET_QUOTE + total_pnl - total_fees) > 0,
    }
    if not all(sanity.values()):
        raise SystemExit(f"sanity check FAILED: {sanity}")

    return {
        "symbol": symbol,
        "interval": interval,
        "bars": len(closed_rows),
        "first_ts": closed_rows[0]["time"],
        "last_ts": closed_rows[-1]["time"],
        "bounds": {
            "lower": lower,
            "upper": upper,
            "reference": reference,
            "atr_mult": ATR_MULT,
        },
        "config": {
            "grid_count": GRID_COUNT,
            "budget_quote": BUDGET_QUOTE,
            "initial_position_pct": INITIAL_POSITION_PCT,
            "max_open_orders": MAX_OPEN_ORDERS,
            "maker_rate": MAKER_RATE,
            "taker_rate": TAKER_RATE,
            "gate_mode": gate_mode,
            "gate_dangerous_stop_needs": (1 if gate else None),
        },
        "gate_events": gate_events,
        "stats": {
            "fills": len(total_fills),
            "intra_replay_fills": len(fills),
            "waterfall_fills": len(waterfall_events),
            "completed_cycles": cycles,
            "cumulative_pnl_gross": total_pnl,
            "cumulative_fees": total_fees,
            "net_pnl": total_pnl - total_fees,
            "pnl_pct_of_budget": (total_pnl - total_fees) / BUDGET_QUOTE,
            "max_drawdown": mdd,
        },
        "sanity": sanity,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline grid replay (research)")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--gate-mode", default="always-on", choices=GATE_MODES)
    parser.add_argument("--out", default=None, help="write JSON here instead of stdout")
    args = parser.parse_args()

    result = replay(args.symbol, args.interval, args.bars, args.db, args.gate_mode)
    payload = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"replay result -> {args.out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
