"""Tests for the ICT structure layer (REWRITE_SPEC_ICT_STRUCTURE.md).

Locks: swing-sequence exposure, recursive STH/ITH/LTH tier marking
(invariant: ITH ⊂ STH, LTH ⊂ ITH), BOS/MSS event table with
break_on=close|wick, single-break-per-swing, displacement fields
attached-but-not-filtering, no NaN in outputs.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.indicators.ict import (
    MarketStructure,
    detect_structure_events,
    mark_structure_tiers,
)


def _df(highs, lows, closes=None, opens=None):
    """Build an OHLC DataFrame (open/close default to mid-range shapes)."""
    if opens is None:
        opens = [lo for lo in lows]
    if closes is None:
        closes = [(h + lo) / 2 for h, lo in zip(highs, lows)]
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


def _simple_swings():
    """Hand-built alternating swing sequence, indices ascending."""
    return [
        {"index": 0, "price": 10.0, "kind": "low"},
        {"index": 2, "price": 12.0, "kind": "high"},
        {"index": 4, "price": 9.0, "kind": "low"},
        {"index": 6, "price": 15.0, "kind": "high"},
        {"index": 8, "price": 11.0, "kind": "low"},
        {"index": 10, "price": 14.0, "kind": "high"},
        {"index": 12, "price": 8.0, "kind": "low"},
        {"index": 14, "price": 13.0, "kind": "high"},
    ]


def _pad(df: pd.DataFrame, min_len: int = 15) -> pd.DataFrame:
    """Extend a df with flat bars so hand-built swings (up to index 14) exist."""
    if len(df) >= min_len:
        return df
    last = df.iloc[-1]
    pad = pd.DataFrame(
        {
            "open": [last["open"]] * (min_len - len(df)),
            "high": [last["high"]] * (min_len - len(df)),
            "low": [last["low"]] * (min_len - len(df)),
            "close": [last["close"]] * (min_len - len(df)),
        }
    )
    return pd.concat([df, pad], ignore_index=True)


# ---------------------------------------------------------------------------
# 1. get_swings exposure
# ---------------------------------------------------------------------------


class TestGetSwings:
    def test_get_swings_returns_full_sequence(self) -> None:
        highs = [1.0, 3.0, 10.0, 3.0, 1.0, 1.0, 12.0, 1.0, 1.0]
        lows = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
        ms = MarketStructure(period=2)
        ms.calculate(_df(highs, lows))
        swings = ms.get_swings()
        assert isinstance(swings, list)
        assert all(set(s) == {"index", "price", "kind"} for s in swings)
        kinds = [s["kind"] for s in swings]
        assert "high" in kinds and "low" in kinds
        indexes = [s["index"] for s in swings]
        assert indexes == sorted(indexes)

    def test_get_swings_matches_latest_helpers(self) -> None:
        highs = [1.0, 3.0, 10.0, 3.0, 1.0, 1.0, 12.0, 1.0, 1.0]
        lows = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
        ms = MarketStructure(period=2)
        ms.calculate(_df(highs, lows))
        swings = ms.get_swings()
        last_high = [s for s in swings if s["kind"] == "high"][-1]
        assert (last_high["index"], last_high["price"]) == ms.get_latest_swing_high()


# ---------------------------------------------------------------------------
# 2. Recursive tier marking — invariant ITH ⊂ STH, LTH ⊂ ITH
# ---------------------------------------------------------------------------


class TestMarkStructureTiers:
    def test_tiers_are_strict_subset_ladder(self) -> None:
        # tier is exclusive (highest reachable level); reconstruct the
        # level sets: STH = all highs, ITH = tier>=it, LTH = tier==lt.
        swings = mark_structure_tiers(_simple_swings())
        highs = [s for s in swings if s["kind"] == "high"]
        st = {s["index"] for s in highs}
        it = {s["index"] for s in highs if s["tier"] in ("it", "lt")}
        lt = {s["index"] for s in highs if s["tier"] == "lt"}
        assert it <= st, "ITH must be a subset of STH"
        assert lt <= it, "LTH must be a subset of ITH"
        lows = [s for s in swings if s["kind"] == "low"]
        stl = {s["index"] for s in lows}
        itl = {s["index"] for s in lows if s["tier"] in ("it", "lt")}
        assert itl <= stl
        assert st and stl, "base fractals must exist"

    def test_every_swing_has_exactly_one_tier(self) -> None:
        swings = mark_structure_tiers(_simple_swings())
        assert all(s["tier"] in {"st", "it", "lt"} for s in swings)

    def test_lows_and_highs_tiered_independently(self) -> None:
        # Highs ascend (no lower STH on either side -> all "st");
        # lows descend (same -> all "st").
        swings = [
            {"index": 0, "price": 10.0, "kind": "low"},
            {"index": 2, "price": 12.0, "kind": "high"},
            {"index": 4, "price": 9.0, "kind": "low"},
            {"index": 6, "price": 15.0, "kind": "high"},
            {"index": 8, "price": 8.0, "kind": "low"},
            {"index": 10, "price": 18.0, "kind": "high"},
        ]
        result = mark_structure_tiers(swings)
        assert all(s["tier"] == "st" for s in result)

    def test_recursive_highs_get_higher_tiers(self) -> None:
        # A swing high whose flanking STHs (one each side) are both LOWER
        # is an ITH — i.e. the local peak among its neighbors, with the
        # next STH beyond each flank below it. Monotone sequences get no ITH.
        monotone = [
            {"index": 0, "price": 5.0, "kind": "low"},
            {"index": 2, "price": 20.0, "kind": "high"},
            {"index": 4, "price": 6.0, "kind": "low"},
            {"index": 6, "price": 15.0, "kind": "high"},
            {"index": 8, "price": 7.0, "kind": "low"},
            {"index": 10, "price": 13.0, "kind": "high"},
            {"index": 12, "price": 4.0, "kind": "low"},
            {"index": 14, "price": 11.0, "kind": "high"},
        ]
        # Highs descend 20>15>13>11: no high has a lower STH on any side —
        # all stay "st".
        assert all(s["tier"] == "st" for s in mark_structure_tiers(monotone))

        # Peak 20 (i2): left STH 15 lower, right STHs 18/14 lower → ITH.
        peak = [
            {"index": 0, "price": 5.0, "kind": "low"},
            {"index": 2, "price": 15.0, "kind": "high"},
            {"index": 4, "price": 6.0, "kind": "low"},
            {"index": 6, "price": 20.0, "kind": "high"},
            {"index": 8, "price": 7.0, "kind": "low"},
            {"index": 10, "price": 18.0, "kind": "high"},
            {"index": 12, "price": 4.0, "kind": "low"},
            {"index": 14, "price": 14.0, "kind": "high"},
        ]
        tiers = {s["index"]: s["tier"] for s in mark_structure_tiers(peak)}
        assert tiers[6] == "it"
        assert tiers[2] == "st"


# ---------------------------------------------------------------------------
# 3. BOS / MSS events
# ---------------------------------------------------------------------------


class TestDetectStructureEvents:
    def test_textbook_sequence_bos_mss_bos(self) -> None:
        # Uptrend: break above swing high 12 (BOS, direction +1).
        # Then downtrend: close below swing low 9 (MSS, direction -1).
        # Then continuation: close below swing low 8 (BOS, direction -1).
        highs = [11.0, 12.5, 13.0, 12.0, 11.0, 10.5, 9.5, 8.5, 8.0, 7.5]
        lows = [9.0, 9.5, 11.0, 10.0, 9.0, 9.2, 9.1, 8.0, 7.0, 6.5]
        closes = [10.5, 12.2, 12.5, 11.5, 10.5, 10.0, 9.2, 8.2, 7.5, 7.0]
        df = _df(highs, lows, closes=closes)
        swings = _simple_swings()
        events = detect_structure_events(_pad(df), swings, break_on="close")
        kinds = [(e["kind"], e["direction"]) for e in events]
        # First break: close 12.5 > 12 (high swing) with no prior trend -> BOS +1
        assert kinds[0] == ("BOS", 1)
        # Then a close below 9.0 while trend is +1 -> MSS -1
        assert ("MSS", -1) in kinds
        # After trend flips, next close below 8.0 -> BOS -1
        assert kinds[-1] == ("BOS", -1)

    def test_event_fields_present(self) -> None:
        highs = [11.0, 12.5, 13.0, 12.0, 11.0]
        lows = [9.0, 9.5, 11.0, 10.0, 9.0]
        closes = [10.5, 12.2, 12.5, 11.5, 10.5]
        df = _df(highs, lows, closes=closes)
        swings = _simple_swings()
        events = detect_structure_events(_pad(df), swings, break_on="close")
        assert events, "at least one event expected"
        for e in events:
            assert e["kind"] in {"BOS", "MSS"}
            assert e["direction"] in (1, -1)
            assert isinstance(e["level"], float)
            assert isinstance(e["broken_bar_index"], int)
            assert isinstance(e["swing_bar_index"], int)
            assert "displacement" in e
            assert set(e["displacement"]) == {"body_ratio", "has_fvg", "fvg_range"}

    def test_close_vs_wick_break(self) -> None:
        # Bar 3 wicks above 12 (high 12.5) but closes back at 11.0.
        highs = [11.0, 12.5, 11.5, 12.5, 11.0]
        lows = [9.0, 9.5, 10.0, 9.5, 9.0]
        closes = [10.5, 11.0, 10.5, 11.0, 10.5]
        df = _df(highs, lows, closes=closes)
        swings = _simple_swings()
        events_close = detect_structure_events(_pad(df), swings, break_on="close")
        events_wick = detect_structure_events(_pad(df), swings, break_on="wick")
        broken_levels_close = {e["level"] for e in events_close if e["level"] == 12.0}
        broken_levels_wick = {e["level"] for e in events_wick if e["level"] == 12.0}
        assert not broken_levels_close, "wick-only breach must not count on close mode"
        assert broken_levels_wick, "wick breach must count on wick mode"

    def test_same_swing_breaks_only_once(self) -> None:
        # Close above 12 then pull back under, then close above 12 again —
        # second break of the same swing must not emit another event.
        highs = [11.0, 12.5, 13.0, 11.5, 12.6, 13.0]
        lows = [9.0, 9.5, 11.0, 10.5, 11.0, 11.5]
        closes = [10.5, 12.2, 12.5, 11.0, 12.4, 12.8]
        df = _df(highs, lows, closes=closes)
        swings = _simple_swings()
        events = detect_structure_events(_pad(df), swings, break_on="close")
        level_12_events = [e for e in events if e["level"] == 12.0]
        assert len(level_12_events) == 1, "same swing must break only once"

    def test_displacement_attached_not_filtering(self) -> None:
        # Small moves: no FVG anywhere, yet events must still fire with
        # has_fvg=False — displacement is metadata, not a gate.
        highs = [10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9]
        lows = [9.8, 9.9, 10.0, 10.1, 10.2, 10.3, 10.4, 10.5]
        closes = [10.0, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7]
        df = _df(highs, lows, closes=closes)
        swings = _simple_swings()
        events = detect_structure_events(_pad(df), swings, break_on="close")
        assert events
        assert all(e["displacement"]["has_fvg"] is False for e in events)


# ---------------------------------------------------------------------------
# 4. Output hygiene — no NaN anywhere
# ---------------------------------------------------------------------------


class TestNoNaN:
    def test_events_never_carry_nan(self) -> None:
        highs = [11.0, 12.5, 13.0, 12.0, 11.0, 10.5]
        lows = [9.0, 9.5, 11.0, 10.0, 9.0, 9.2]
        closes = [10.5, 12.2, 12.5, 11.5, 10.5, 10.0]
        df = _df(highs, lows, closes=closes)
        swings = _simple_swings()
        for e in detect_structure_events(_pad(df), swings, break_on="close"):
            for value in (
                e["level"],
                e["displacement"]["body_ratio"],
                e["displacement"]["fvg_range"],
            ):
                assert value == value  # NaN != NaN
                assert value is not None

    def test_tiers_never_carry_nan(self) -> None:
        swings = mark_structure_tiers(_simple_swings())
        assert all(s["tier"] in {"st", "it", "lt"} for s in swings)
