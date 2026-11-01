"""Deterministic overlay-pattern detection (B4, design review Phase 3).

Three chart patterns the Stage 2 decision gets as an extra material layer
when detected on the primary window. Pure functions over bars; thresholds
are selene-chosen (ATR-multiples and slope ratios, see PROVENANCE §11).

Source basis: Al Brooks, "Reading Price Charts Bar by Bar" (Wiley, 2009)
Ch. 10-12 (channels, wedges, breakout failure); "Trading Price Action
Trends" (Wiley, 2011) Ch. 3-5 (pullback count: H1/H2 continuation logic).
"""

from __future__ import annotations

from typing import Any


def _atr(bars: list[dict[str, Any]], period: int = 14) -> float:
    trs = []
    for prev, cur in zip(bars, bars[1:]):
        tr = max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        )
        trs.append(tr)
    if not trs:
        return 0.0
    return float(sum(trs[-period:]) / min(period, len(trs)))


def _swing_highs(bars: list[dict[str, Any]], k: int = 2) -> list[int]:
    """Indices (0-based) of swing highs: bar strictly above k bars each side."""
    out: list[int] = []
    for i in range(k, len(bars) - k):
        window = bars[i - k : i + k + 1]
        if bars[i]["high"] == max(b["high"] for b in window):
            out.append(i)
    return out


def _swing_lows(bars: list[dict[str, Any]], k: int = 2) -> list[int]:
    out: list[int] = []
    for i in range(k, len(bars) - k):
        window = bars[i - k : i + k + 1]
        if bars[i]["low"] == min(b["low"] for b in window):
            out.append(i)
    return out


def detect_wedge(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Contracting range against the prevailing direction.

    Selene thresholds: over the inertia window (last 8 bars) the last two
    k=2 swing highs descend AND the last two swing lows ascend (rising
    wedge), or the mirrored falling-wedge case. Returns a descriptor the
    overlay prompt cites, or None.
    """
    if len(bars) < 14:
        return None
    seg = bars[-8:]
    highs = _swing_highs(seg)
    lows = _swing_lows(seg)
    if len(highs) < 2 and len(lows) < 2:
        return None
    contracting = False
    if len(highs) >= 2 and len(lows) >= 2:
        hs = [seg[i]["high"] for i in highs[-2:]]
        ls = [seg[i]["low"] for i in lows[-2:]]
        rising_wedge = hs[1] < hs[0] and ls[1] > ls[0]
        falling_wedge = hs[1] > hs[0] and ls[1] < ls[0]
        contracting = rising_wedge or falling_wedge
    if not contracting:
        return None
    seg_high = max(b["high"] for b in seg)
    seg_low = min(b["low"] for b in seg)
    span = seg_high - seg_low
    if span <= 0:
        return None
    atr = _atr(bars)
    return {
        "pattern": "wedge",
        "window": "inertia",
        "rising_wedge": bool(
            len(highs) >= 2
            and len(lows) >= 2
            and seg[highs[-1]]["high"] < seg[highs[-2]]["high"]
            and seg[lows[-1]]["low"] > seg[lows[-2]]["low"]
        ),
        "net_range": round(span, 2),
        "atr14": round(atr, 2),
    }


def detect_h2(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Second pullback low in an up leg (H2 continuation setup).

    Selene thresholds: last two swing lows in the signal window (last 10
    bars) with the second low higher than the first AND at least one
    bull bar closing above the mid between them.
    """
    if len(bars) < 12:
        return None
    seg = bars[-10:]
    lows = _swing_lows(seg)
    if len(lows) < 2:
        return None
    first, second = lows[-2], lows[-1]
    if seg[second]["low"] <= seg[first]["low"]:
        return None
    mid = (seg[first]["low"] + seg[second]["low"]) / 2
    follow_up = [b for b in seg[second:] if b["close"] > b["open"] and b["close"] > mid]
    if not follow_up:
        return None
    return {
        "pattern": "h2",
        "window": "signal",
        "first_low": seg[first]["low"],
        "second_low": seg[second]["low"],
        "higher_low": True,
    }


def detect_breakout_failure(bars: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A close beyond the prior 20-bar extreme that fails within 3 bars.

    Selene thresholds: some bar in the signal window (last 10) closes
    beyond a 20-bar extreme measured BEFORE the signal window (so the
    breakout bar itself cannot inflate its own baseline), and the LAST
    bar closes back inside that range.
    """
    if len(bars) < 30:
        return None
    prior = bars[-30:-10]  # 20 bars ending right before the signal window
    prior_high = max(b["high"] for b in prior)
    prior_low = min(b["low"] for b in prior)
    last = bars[-1]
    if prior_low <= last["close"] <= prior_high:
        # Last bar back inside → look for the failed excursion in the tail.
        tail = bars[-10:]
        broke_up = any(b["close"] > prior_high for b in tail[:-1])
        broke_down = any(b["close"] < prior_low for b in tail[:-1])
        if broke_up or broke_down:
            return {
                "pattern": "breakout_failure",
                "window": "signal",
                "direction": "up" if broke_up else "down",
                "prior_high": prior_high,
                "prior_low": prior_low,
                "last_close": last["close"],
            }
    return None


def detect_overlays(bars: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Run all three detectors; each returns [] when not present."""
    out: dict[str, list[dict[str, Any]]] = {
        "wedge": [],
        "h2": [],
        "breakout_failure": [],
    }
    w = detect_wedge(bars)
    if w:
        out["wedge"].append(w)
    h = detect_h2(bars)
    if h:
        out["h2"].append(h)
    b = detect_breakout_failure(bars)
    if b:
        out["breakout_failure"].append(b)
    return out
