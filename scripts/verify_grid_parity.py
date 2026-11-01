"""Parity harness (US-2): drive the original grid experiment's GridEngine
and the selene port with the SAME frozen fixture, compare bit-for-bit.

Offline drive pattern (grid_replay.py:63-88 in the source):
    GridEngine(spec, ledger) -> compute_levels -> seed_initial -> rearm
    -> per-bar check_fills -> rearm

Bounds derivation uses derive_bounds(rows, None) — the pure-ATR path
(diagnosis=None) so no LLM dependency.

Run:
    python scripts/verify_grid_parity.py            # run comparison, write report
    python scripts/verify_grid_parity.py --report   # print existing report only
"""

from __future__ import annotations

import argparse
import json
import math
import os
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

QUANTDINGER_SCRIPTS = r"<local-path-redacted>"
QD_COMMIT = "43eee46"
FIXTURE = os.path.join(
    PROJECT_ROOT,
    "packages",
    "grid-engine",
    "tests",
    "fixtures",
    "grid_fixture_btc_300.json",
)
REPORT = os.path.join(
    PROJECT_ROOT, "packages", "grid-engine", "tests", "fixtures", "parity_report.json"
)

# identical fee config on both sides (grid_sim GridEngine has no fee model;
# fees are compared separately on the selene side, gross PnL parity here)
GRID_COUNT = 8
BUDGET_QUOTE = 100.0
INITIAL_POSITION_PCT = 0.6
MAX_OPEN_ORDERS = 4
ATR_MULT = 1.5
PRICE_RTOL = 1e-12
PNL_ATOL = 1e-9


def load_fixture(path: str = FIXTURE) -> dict:
    with open(path, encoding="utf-8") as f:
        fixture: dict = json.load(f)
    return fixture


def derive_bounds_selene(
    rows: list[dict], atr_mult: float
) -> tuple[float, float, float]:
    """Port-independent bounds: reuse the ORIGINAL derive_bounds (pure ATR path)
    so both engines are anchored to identical (lower, upper, reference)."""
    if QUANTDINGER_SCRIPTS not in sys.path:
        sys.path.insert(0, QUANTDINGER_SCRIPTS)
    import grid_sim  # noqa: PLC0415 — deliberate cross-repo import for parity

    bounds: tuple[float, float, float] = grid_sim.derive_bounds(
        rows, None, atr_mult=atr_mult
    )
    return bounds


def drive_original(rows: list[dict], bounds: tuple[float, float, float]) -> dict:
    if QUANTDINGER_SCRIPTS not in sys.path:
        sys.path.insert(0, QUANTDINGER_SCRIPTS)
    import grid_sim  # noqa: PLC0415

    lower, upper, reference = bounds
    spec = grid_sim.GridSpec(
        symbol="BTC/USDT",
        grid_count=GRID_COUNT,
        total_budget_quote=BUDGET_QUOTE,
        initial_position_pct=INITIAL_POSITION_PCT,
        lower=lower,
        upper=upper,
        reference=reference,
        max_open_orders=MAX_OPEN_ORDERS,
    )
    ledger = grid_sim.GridLedger(
        path=os.path.join(os.environ.get("TEMP", "."), "parity_ledger_original.json")
    )
    ledger.data["spec_fingerprint"] = spec.fingerprint()
    ledger.data["symbol"] = spec.symbol
    engine = grid_sim.GridEngine(spec, ledger)
    engine.cells = engine._compute_levels()
    engine._seed_initial()
    engine._rearm()

    fills: list[dict] = []
    cumulative_pnl = 0.0
    for bar in rows:
        events = engine._check_fills([bar])
        for ev in events:
            fills.append(
                {
                    "ts": ev.ts,
                    "index": ev.index,
                    "side": ev.side,
                    "kind": ev.kind,
                    "price": ev.price,
                    "qty": ev.qty,
                }
            )
        cumulative_pnl += sum(e.realized_pnl for e in events)
        engine._rearm()
    return {
        "fills": fills,
        "cumulative_pnl": cumulative_pnl,
        "final_states": [c.state for c in engine.cells],
    }


def drive_port(rows: list[dict], bounds: tuple[float, float, float]) -> dict:
    from selene_space_grid import GridSpec
    from selene_space_grid.grid_engine import GridEngine

    lower, upper, reference = bounds
    spec = GridSpec(
        symbol="BTC/USDT",
        grid_count=GRID_COUNT,
        total_budget_quote=BUDGET_QUOTE,
        initial_position_pct=INITIAL_POSITION_PCT,
        lower=lower,
        upper=upper,
        reference=reference,
        max_open_orders=MAX_OPEN_ORDERS,
    )
    engine = GridEngine(spec, {"data": {}}, commission=None)  # gross PnL parity
    engine.compute_levels()
    engine.seed_initial()
    engine.rearm()

    fills: list[dict] = []
    cumulative_pnl = 0.0
    for bar in rows:
        events = engine.check_fills([bar])
        for ev in events:
            fills.append(
                {
                    "ts": ev.ts,
                    "index": ev.index,
                    "side": ev.side,
                    "kind": ev.kind,
                    "price": ev.price,
                    "qty": ev.qty,
                }
            )
        cumulative_pnl += sum(e.realized_pnl for e in events)
        engine.rearm()
    return {
        "fills": fills,
        "cumulative_pnl": cumulative_pnl,
        "final_states": [str(c.state) for c in engine.cells],
    }


def compare(orig: dict, port: dict) -> dict:
    of, pf = orig["fills"], port["fills"]
    fills_count_equal = len(of) == len(pf)
    price_details = []
    prices_bit_equal = fills_count_equal
    for i, (a, b) in enumerate(zip(of, pf)):
        if not fills_count_equal:
            break
        exact = a["price"] == b["price"]
        if not exact and not math.isclose(a["price"], b["price"], rel_tol=PRICE_RTOL):
            prices_bit_equal = False
        qty_exact = a["qty"] == b["qty"]
        meta_ok = (
            a["ts"] == b["ts"]
            and a["index"] == b["index"]
            and a["side"] == b["side"]
            and a["kind"] == b["kind"]
        )
        price_details.append(
            {"i": i, "exact": exact, "qty_exact": qty_exact, "meta_ok": meta_ok}
        )
    pnl_exact = orig["cumulative_pnl"] == port["cumulative_pnl"]
    pnl_close = math.isclose(
        orig["cumulative_pnl"], port["cumulative_pnl"], rel_tol=0.0, abs_tol=PNL_ATOL
    )
    states_equal = orig["final_states"] == port["final_states"]
    return {
        "fills_count": {
            "original": len(of),
            "port": len(pf),
            "equal": fills_count_equal,
        },
        "prices_bit_equal": prices_bit_equal,
        "pnl": {
            "original": orig["cumulative_pnl"],
            "port": port["cumulative_pnl"],
            "exact": pnl_exact,
            "within_atol": pnl_close,
        },
        "final_states_equal": states_equal,
        "verdict": (
            fills_count_equal
            and prices_bit_equal
            and (pnl_exact or pnl_close)
            and states_equal
        ),
        "details": price_details,
    }


def run_parity() -> dict:
    fixture = load_fixture()
    rows = fixture["bars"]
    closed_rows = [r for r in rows if r.get("closed", True)]
    bounds = derive_bounds_selene(closed_rows, ATR_MULT)
    orig = drive_original(closed_rows, bounds)
    port = drive_port(closed_rows, bounds)
    result = compare(orig, port)
    report = {
        "fixture": {
            "path": os.path.relpath(FIXTURE, PROJECT_ROOT),
            "bars": fixture["meta"]["bars"],
            "first_ts": fixture["meta"]["first_ts"],
            "last_ts": fixture["meta"]["last_ts"],
        },
        "source": {
            "repo": "<local-path-redacted>",
            "commit": QD_COMMIT,
            "module": "grid_sim.GridEngine",
        },
        "bounds": {
            "lower": bounds[0],
            "upper": bounds[1],
            "reference": bounds[2],
            "derivation": "derive_bounds(rows, None) pure ATR path, both sides",
        },
        "config": {
            "grid_count": GRID_COUNT,
            "budget_quote": BUDGET_QUOTE,
            "initial_position_pct": INITIAL_POSITION_PCT,
            "max_open_orders": MAX_OPEN_ORDERS,
            "atr_mult": ATR_MULT,
        },
        "comparison": result,
    }
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="US-2 grid parity verification")
    parser.add_argument("--report", action="store_true", help="print existing report")
    args = parser.parse_args()
    if args.report:
        with open(REPORT, encoding="utf-8") as f:
            print(json.dumps(json.load(f), indent=1))
        return 0
    report = run_parity()
    c = report["comparison"]
    print(
        f"fills: {c['fills_count']['original']} vs {c['fills_count']['port']} "
        f"(equal={c['fills_count']['equal']})"
    )
    print(f"prices bit-equal (rel_tol<=1e-12): {c['prices_bit_equal']}")
    print(
        f"cumulative PnL: {c['pnl']['original']!r} vs {c['pnl']['port']!r} "
        f"(exact={c['pnl']['exact']}, atol 1e-9={c['pnl']['within_atol']})"
    )
    print(f"final cell states equal: {c['final_states_equal']}")
    print(f"VERDICT: {'PASS' if c['verdict'] else 'FAIL'}")
    print(f"report -> {os.path.relpath(REPORT, PROJECT_ROOT)}")
    return 0 if c["verdict"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
