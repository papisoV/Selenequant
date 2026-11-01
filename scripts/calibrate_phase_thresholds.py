"""Calibrate the sentiment-phase thresholds from a selene-built ladder panel.

Reads ``packages/ashare/data_cache/ladder_panel.parquet`` (produced by
``scripts/backfill_ladder_panel.py``) and prints quantiles for every driver
the :mod:`selene_space_ashare.market_phase` classifier reads, plus a ready-to-
paste constant block. Those constants are what satisfy P4: the thresholds must
come from *selene's own* panel, never copied from the source project's
constants.

Usage::

    python scripts/calibrate_phase_thresholds.py
    python scripts/calibrate_phase_thresholds.py --window 20240101 20240229
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PANEL_PATH = ROOT / "packages" / "ashare" / "data_cache" / "ladder_panel.parquet"

# driver -> (out percentiles) drawn from the panel's own distribution
_PCTS = (0.10, 0.20, 0.40, 0.55, 0.60, 0.85, 0.90, 0.95)

# Minimum number of dated rows before quantiles are trustworthy. A full
# multi-year backfill is thousands of days; this floor only rejects obviously
# partial panels (e.g. a smoke run over a handful of names).
_MIN_ROWS = 250


def _quantiles(series: pd.Series) -> dict[float, float]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {p: float("nan") for p in _PCTS}
    return {p: float(s.quantile(p)) for p in _PCTS}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--panel", default=str(PANEL_PATH))
    ap.add_argument(
        "--window",
        nargs=2,
        metavar=("START", "END"),
        default=None,
        help="restrict to a date window (YYYYMMDD) before computing quantiles",
    )
    args = ap.parse_args(argv)

    panel = pd.read_parquet(args.panel)
    if args.window:
        start, end = args.window
        panel = panel[(panel["date"] >= start) & (panel["date"] <= end)]
    if panel.empty:
        print("panel is empty after windowing — nothing to calibrate", file=sys.stderr)
        return 1

    print(f"panel rows={len(panel)} dates={panel['date'].min()}..{panel['date'].max()}")
    print()

    drivers = [
        "max_consecutive",
        "first_board",
        "ge2_count",
        "ge5_count",
        "promo_rate",
        "seal_rate",
        "ladder_completeness",
    ]
    header = "driver".ljust(20) + "".join(
        f"p{int(p * 100):>3d}".rjust(9) for p in _PCTS
    )
    print(header)
    print("-" * len(header))
    quant: dict[str, dict[float, float]] = {}
    for d in drivers:
        if d not in panel.columns:
            continue
        q = _quantiles(panel[d])
        quant[d] = q
        row = d.ljust(20) + "".join(f"{q[p]:9.4f}" for p in _PCTS)
        print(row)

    print()
    print(
        "# ─── calibrated from selene ladder panel (scripts/calibrate_phase_thresholds.py) ───"
    )
    print(
        "# paste into selene_space_ashare/market_phase.py, replacing the placeholders"
    )
    print(f"# window={panel['date'].min()}..{panel['date'].max()} n={len(panel)}")
    h = quant.get("max_consecutive", {})
    fb = quant.get("first_board", {})
    g2 = quant.get("ge2_count", {})
    pr = quant.get("promo_rate", {})
    sr = quant.get("seal_rate", {})

    # P5 不静默留缺口: a degenerate panel (a driver that is all-NaN / all-zero,
    # or a full run over an incomplete backfill) yields nan or floor-ed "1"
    # thresholds that look like numbers but are meaningless. The quantile table
    # above is still printed for inspection; only the constant block is refused.
    problems: list[str] = []
    if args.window is None and len(panel) < _MIN_ROWS:
        problems.append(
            f"全量面板仅 {len(panel)} 行 < {_MIN_ROWS}(回填不完整;"
            "如仅需查看某窗口分位请加 --window)"
        )
    # Guard every driver the constant block below consumes, not just the rate
    # drivers: an all-NaN height column would reach `round(nan)` and raise
    # ValueError *after* the guard, defeating the graceful refusal.
    for name, q in (
        ("promo_rate", pr),
        ("seal_rate", sr),
        ("ge2_count", g2),
        ("max_consecutive", h),
        ("first_board", fb),
    ):
        vals: list[float] = [v for v in (q.get(p) for p in _PCTS) if v is not None]
        if not vals or any(v != v for v in vals):  # all-NaN
            problems.append(f"{name} 分位全为 NaN")
        elif max(abs(v) for v in vals) == 0.0:
            problems.append(f"{name} 分位全为 0(驱动无信号)")
    if problems:
        print("# ⚠️ 标定未通过 — 拒绝输出常量(以下问题先修复):", file=sys.stderr)
        for p in problems:
            print(f"#   - {p}", file=sys.stderr)
        return 2

    print(f"CLIMAX_GE2 = {max(1, round(g2.get(0.90, 0) * 2))}      # ~2x p90(ge2)")
    print(
        f"CLIMAX_FIRST_BOARD = {max(1, round(fb.get(0.90, 0) * 2.5))}  # ~2.5x p90(first_board)"
    )
    print(f"RALLY_HEIGHT = {max(1, round(h.get(0.60, 0)))}")
    print(f"RALLY_GE2 = {max(1, round(g2.get(0.60, 0)))}")
    print(f"RALLY_PROMO = {pr.get(0.60, 0):.4f}")
    print(f"RALLY_PROMO_ALT = {pr.get(0.85, 0):.4f}")
    print(f"RALLY_GE2_ALT = {max(1, round(g2.get(0.60, 0) * 0.8))}")
    print(f"RALLY_HEIGHT_ALT = {max(1, round(h.get(0.40, 0)))}")
    print(f"EBB_PROMO = {pr.get(0.20, 0):.4f}")
    print(f"EBB_PROMO_STRICT = {pr.get(0.10, 0):.4f}")
    print(f"EBB_SEAL = {sr.get(0.10, 0):.4f}")
    print(f"EBB_RECENT_GE2 = {max(1, round(g2.get(0.60, 0) * 0.8))}")
    print(f"EBB_RECENT_HEIGHT = {max(1, round(h.get(0.60, 0)))}")
    print(f"IGNITE_GE2_DELTA = {max(1, round(g2.get(0.20, 0) * 0.5))}")
    print(f"IGNITE_GE2 = {max(1, round(g2.get(0.20, 0)))}")
    print(f"IGNITE_PROMO = {pr.get(0.55, 0):.4f}")
    print("IGNITE_HEIGHT_DELTA = 1")
    print(f"IGNITE_HEIGHT = {max(1, round(h.get(0.40, 0)))}")
    print(f"IGNITE_PROMO_SOFT = {pr.get(0.55, 0) * 0.95:.4f}")
    print(f"ICE_HEIGHT = {max(1, round(h.get(0.20, 0)))}")
    print(f"ICE_GE2 = {max(1, round(g2.get(0.10, 0)))}")
    print(f"ICE_FIRST_BOARD = {max(1, round(fb.get(0.10, 0)))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
