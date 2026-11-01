"""Tests for backtest_gates: limit-board fillability interception."""

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare.backtest_gates import (
    attach_limit_flags,
    defer_limit_down_exits,
    mask_limit_up_entries,
)


def _panel(rows: list[tuple[str, str, float]], names: dict[str, str] | None = None):
    """rows: (date, symbol, close); close series per symbol defines bands."""
    df = pd.DataFrame(rows, columns=["date", "symbol", "close"])
    if names:
        df["name"] = df["symbol"].map(names).fillna("")
    return df


def test_seal_flags_judge_against_prev_close_not_self():
    # close=11.0, prev=10.0 → main board limit-up price = 11.00 → sealed.
    # The FIRST bar (no prev_close) must read False — fail-open, never a
    # self-referential band.
    df = _panel([("2026-01-01", "600000", 10.0), ("2026-01-02", "600000", 11.0)])
    out = attach_limit_flags(df)
    assert not bool(out.iloc[0]["limit_up_sealed"])
    assert bool(out.iloc[1]["limit_up_sealed"])
    assert not bool(out.iloc[1]["limit_down_sealed"])


def test_board_scope_changes_the_band():
    # 30% growth board: close 12.0 from prev 10.0 = +20% → NOT sealed on cyb
    # (limit-up price 12.00; equality seals, and 12.0 == 12.0 is the boundary
    # — use 11.5 to stay strictly inside the band).
    df = _panel([("2026-01-01", "300001", 10.0), ("2026-01-02", "300001", 11.5)])
    out = attach_limit_flags(df)
    assert not bool(out.iloc[1]["limit_up_sealed"])
    # +20% on main board: beyond the 10% band → sealed.
    df2 = _panel([("2026-01-01", "600000", 10.0), ("2026-01-02", "600000", 12.0)])
    out2 = attach_limit_flags(df2)
    assert bool(out2.iloc[1]["limit_up_sealed"])


def test_down_seal_and_symmetry():
    # main board: close 9.0 from prev 10.0 → limit-down price 9.00 → sealed.
    df = _panel([("2026-01-01", "600000", 10.0), ("2026-01-02", "600000", 9.0)])
    out = attach_limit_flags(df)
    assert bool(out.iloc[1]["limit_down_sealed"])
    assert not bool(out.iloc[1]["limit_up_sealed"])


def test_symbols_do_not_borrow_prev_close():
    # Symbol B's first bar must not use symbol A's last close as its prev.
    df = _panel(
        [
            ("2026-01-01", "600000", 10.0),
            ("2026-01-01", "600001", 11.0),  # no prev → False
        ]
    )
    out = attach_limit_flags(df)
    row_b = out[out["symbol"] == "600001"].iloc[0]
    assert not bool(row_b["limit_up_sealed"])
    assert not bool(row_b["limit_down_sealed"])


def test_mask_entries_drops_sealed_rows_and_keeps_rest():
    panel = _panel(
        [
            ("2026-01-01", "B", 10.0),
            ("2026-01-02", "B", 11.0),  # sealed (main board limit-up price 11.00)
            ("2026-01-02", "A", 10.0),  # not sealed (first bar, fail-open)
        ]
    )
    panel["factor"] = [1.0, 2.0, 3.0]
    flagged = attach_limit_flags(panel)
    kept = mask_limit_up_entries(flagged)
    # B's sealed row (its 01-02 bar) gone; B's unsealed bar and A stay.
    assert list(kept["symbol"]) == ["B", "A"]
    assert list(kept["close"]) == [10.0, 10.0]


def test_mask_entries_without_flags_column_is_inert():
    df = _panel([("2026-01-01", "A", 10.0)])
    out = mask_limit_up_entries(df)
    assert out.equals(df) or len(out) == len(df)


def test_defer_rewrites_only_sealed_horizon_rows():
    # 5 bars; horizon 1; bar 2 (index 2) sealed limit-down.
    # Row i=1's horizon close (bar 2) is sealed → deferred to bar 3.
    panel = _panel(
        [
            ("2026-01-01", "600000", 10.0),
            ("2026-01-02", "600000", 10.0),  # entry row: fwd_1d uses bar 2
            ("2026-01-03", "600000", 9.0),  # sealed limit-down
            ("2026-01-04", "600000", 9.5),  # first sellable
            ("2026-01-05", "600000", 9.6),
        ]
    )
    panel["fwd_1d"] = panel["close"].shift(-1) / panel["close"] - 1
    flagged = attach_limit_flags(panel)
    out = defer_limit_down_exits(flagged, "fwd_1d", 1)
    # row 1: original fwd = 9/10-1 = -0.10; deferred = 9.5/10-1 = -0.05
    assert out.loc[panel.index[1], "fwd_1d"] == pytest.approx(-0.05)
    # untouched rows value-identical — a bypassed sealed check would rewrite
    # row 3's fwd to 9.6/9-1 (re-read against the sealed bar's close), so
    # assert row 3 exactly; row 0's recomputed value would coincidentally
    # match, so it is covered by the mutation suite instead.
    assert out.loc[panel.index[0], "fwd_1d"] == pytest.approx(0.0)
    assert out.loc[panel.index[2], "fwd_1d"] == pytest.approx(9.5 / 9.0 - 1)
    assert out.loc[panel.index[3], "fwd_1d"] == pytest.approx(9.6 / 9.5 - 1)
    assert pd.isna(out.loc[panel.index[4], "fwd_1d"])


def test_defer_gives_up_after_window():
    # Consecutive main-board limit-downs compound: 10 → 9 → 8.1 → ... each
    # exactly -10%, so every following day stays sealed → give up → NaN.
    closes = [10.0, 10.0]
    c = 10.0
    for _ in range(12):
        c = round(c * 0.9, 2)
        closes.append(c)
    panel = _panel(
        [(f"2026-01-{d:02d}", "600000", c) for d, c in zip(range(1, 15), closes)]
    )
    panel["fwd_1d"] = panel["close"].shift(-1) / panel["close"] - 1
    flagged = attach_limit_flags(panel)
    assert bool(flagged["limit_down_sealed"].iloc[2:].all())  # precondition
    out = defer_limit_down_exits(flagged, "fwd_1d", 1, max_defer_days=3)
    assert pd.isna(out.loc[panel.index[1], "fwd_1d"])


def test_defer_without_flags_column_passes_through():
    panel = _panel([("2026-01-01", "600000", 10.0), ("2026-01-02", "600000", 9.0)])
    panel["fwd_1d"] = [np.nan, 0.0]
    out = defer_limit_down_exits(panel, "fwd_1d", 1)
    assert out["fwd_1d"].equals(panel["fwd_1d"])


def test_defer_never_mutates_input():
    panel = _panel(
        [
            ("2026-01-01", "600000", 10.0),
            ("2026-01-02", "600000", 10.0),
            ("2026-01-03", "600000", 9.0),
            ("2026-01-04", "600000", 9.5),
        ]
    )
    panel["fwd_1d"] = panel["close"].shift(-1) / panel["close"] - 1
    original = panel["fwd_1d"].copy()
    flagged = attach_limit_flags(panel)
    defer_limit_down_exits(flagged, "fwd_1d", 1)
    # the input panel the caller kept must be untouched
    assert panel["fwd_1d"].equals(original)
