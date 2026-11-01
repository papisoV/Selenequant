"""Multi-scale four-window aggregation (B3, design review Phase 3).

Brooks convention (public methodology): a 120-bar window is read at four
scales — long-range, recent, inertia, signal. Pure functions, zero IO;
K-numbering matches the main summary (K1 = oldest bar).

Source basis: Al Brooks, "Reading Price Charts Bar by Bar" (Wiley, 2009)
Ch. 2-4; "Trading Price Action Trends" (Wiley, 2011) Ch. 21-23. Window
ratios and thresholds are selene-chosen parameters (see PROVENANCE §11),
not transcribed values.
"""

from __future__ import annotations

from typing import Any

# Window parameters (absolute bar counts). Anchors at N=120 (Brooks
# multi-scale convention): long-range K41-K100 (60 bars ending 20 short
# of the last bar), recent K81-K120 (last 40), inertia K113-K120 (last
# 8), signal K111-K120 (last 10). Long-range deliberately stops before
# the final 20 bars so it measures structure, not the live edge.
LONG_RANGE_BARS = 60  # absolute bar count
LONG_RANGE_END_GAP = 20  # long-range window ends N-20 (structure, not edge)
RECENT_BARS = 40  # absolute bar count
INERTIA_BARS = 8  # absolute bar count
SIGNAL_BARS = 10  # absolute bar count


def _window_slices(n: int) -> dict[str, tuple[int, int]]:
    """1-based inclusive K-ranges per window, shrinking gracefully with N."""
    recent_start = max(1, n - RECENT_BARS + 1)
    long_end = max(1, n - LONG_RANGE_END_GAP)
    long_start = max(1, long_end - LONG_RANGE_BARS + 1)
    inertia_start = max(1, n - INERTIA_BARS + 1)
    signal_start = max(1, n - SIGNAL_BARS + 1)
    return {
        "long_range": (long_start, long_end),
        "recent": (recent_start, n),
        "inertia": (inertia_start, n),
        "signal": (signal_start, n),
    }


def four_windows(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the four-scale reading the Stage 2 prompt annotates.

    Each window reports its K-range plus directional read: net move,
    bull/bear bar counts, close position within the window's range, and
    (for the two wide windows) high/low anchors. Overlap between windows
    is intentional — recent/inertia/signal all end at the last bar; the
    scales differ in depth, not endpoint.
    """
    n = len(bars)
    if n < 50:
        raise ValueError(f"four_windows needs >= 50 bars, got {n}")
    ranges = _window_slices(n)
    out: dict[str, Any] = {"bars_total": n}
    for name, (k_start, k_end) in ranges.items():
        seg = bars[k_start - 1 : k_end]
        highs = [b["high"] for b in seg]
        lows = [b["low"] for b in seg]
        closes = [b["close"] for b in seg]
        opens = [b["open"] for b in seg]
        w_high, w_low = max(highs), min(lows)
        span = w_high - w_low
        # Close position: 1.0 = at window high, 0.0 = at window low.
        close_pos = (closes[-1] - w_low) / span if span > 0 else 0.5
        out[name] = {
            "k_range": f"K{k_start}-K{k_end}",
            "net_move": round(closes[-1] - opens[0], 2),
            "bull_bars": sum(1 for o, c in zip(opens, closes) if c > o),
            "bear_bars": sum(1 for o, c in zip(opens, closes) if c < o),
            "close_pos_in_range": round(close_pos, 3),
            "window_high": w_high,
            "window_low": w_low,
        }
    return out
