"""Tests for factor warmup masking.

The defect these cover is silent fabrication, so every test here asserts
against the *value* a short series produces, not merely that masking ran:

- HIGH_52W_PROXIMITY on 140 bars reports 1.0 for every row (rolling max with
  min_periods=1) — "at the 52-week high" for a series it has only seen 140
  days of.
- GOLDEN_CROSS on 140 bars reports 0 for the first 59 rows — "no cross"
  rather than "cannot evaluate".
- MOMENTUM_12M on 140 bars is all NaN, and `generate_factor_report` dropped
  it via notna().any() with no explanation.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare import factor_calc
from selene_space_ashare.factor_report import generate_factor_report
from selene_space_ashare.factor_warmup import (
    apply_warmup_mask,
    declared_warmups,
    mask_warmup,
    warmup_bars_for,
)
from selene_space_ashare.factors import ALL_FACTORS


def _klines(n: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10 * np.exp(np.cumsum(rng.normal(scale=0.01, size=n)))
    dates = pd.bdate_range("2025-01-01", periods=n).strftime("%Y%m%d")
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1000, 5000, size=n),
            "amount": close * 1000,
            "turnover": rng.uniform(0.1, 1.0, size=n),
        }
    )


# ─── The declarations are the contract ─────────────────────────────


def test_every_technical_calculator_declares_a_warmup():
    """A technical factor with no declaration would silently skip masking."""
    for name in factor_calc.TECHNICAL_CALCULATORS:
        assert warmup_bars_for(name) >= 1, f"{name} declares no warmup_bars"


def test_warmups_match_the_documented_windows():
    expected = {
        "MOMENTUM_1M": 21,
        "MOMENTUM_3M": 63,
        "MOMENTUM_6M": 126,
        "MOMENTUM_12M": 252,
        "HIGH_52W_PROXIMITY": 252,
        "VOLATILITY_20D": 20,
        "BB_WIDTH": 20,
        "ATR_RATIO": 14,
        "PRICE_ZSCORE_20D": 20,
        "MA_DEVIATION": 20,
        "RSI_14D": 14,
        "MACD_SIGNAL": 26,
        "GOLDEN_CROSS": 60,
        "VOLUME_ZSCORE": 20,
        "VOLUME_PRICE_TREND": 20,
        "VOLUME_CLIMAX": 20,
    }
    assert declared_warmups(list(expected)) == expected


def test_unknown_factor_is_zero_not_invented():
    assert warmup_bars_for("NOT_A_FACTOR") == 0


def test_non_technical_factors_keep_the_default():
    for name, spec in ALL_FACTORS.items():
        if name not in factor_calc.TECHNICAL_CALCULATORS:
            assert spec.warmup_bars == 1, f"{name} should have no warmup"


# ─── Per-symbol masking: the fabricated values are gone ────────────


def test_high_52w_proximity_stops_claiming_a_52_week_high():
    """The load-bearing assertion: 1.0 inside warmup was a lie, not noise.

    Measured on real 600519 data (last 140 bars) the first five rows all read
    1.0 — close equals the running max because no 52-week lookback exists
    yet. The zeros below are asserted as "the fabrication is reachable", so
    the test cannot pass by the fixture failing to reproduce it.
    """
    k = _klines(140)
    raw = factor_calc.calculate_technical_factors(k)
    assert raw["HIGH_52W_PROXIMITY"].iloc[:5].notna().all()
    assert raw["HIGH_52W_PROXIMITY"].gt(0).iloc[:5].all()

    masked = mask_warmup(raw, len(k))
    assert masked["HIGH_52W_PROXIMITY"].iloc[:252].isna().all()


def test_golden_cross_warmup_is_missing_not_zero():
    k = _klines(140)
    raw = factor_calc.calculate_technical_factors(k)
    assert (raw["GOLDEN_CROSS"].iloc[:10] == 0).all()

    masked = mask_warmup(raw, len(k))
    assert masked["GOLDEN_CROSS"].iloc[:60].isna().all()
    assert masked["GOLDEN_CROSS"].iloc[60:].notna().any()


def test_mask_aligns_to_row_position_not_index_labels():
    k = _klines(80)
    raw = factor_calc.calculate_technical_factors(k)
    reindexed = raw.copy()
    reindexed.index = range(1000, 1000 + len(raw))  # arbitrary labels

    masked = mask_warmup(reindexed, len(k))
    assert masked["MOMENTUM_1M"].iloc[:21].isna().all()
    assert masked["MOMENTUM_1M"].iloc[21:].notna().any()


def test_mask_does_not_require_end_date_alignment():
    """mask_warmup takes the kline row count, not a date window."""
    k = _klines(300)
    raw = factor_calc.calculate_technical_factors(k)
    masked = mask_warmup(raw, len(k))
    # 300 rows clears 252 → MOMENTUM_12M has real values at the tail
    assert masked["MOMENTUM_12M"].iloc[252:].notna().any()


def test_mask_is_positional_and_rejects_misaligned_frames():
    k = _klines(100)
    raw = factor_calc.calculate_technical_factors(k)
    with pytest.raises(ValueError, match="positional"):
        mask_warmup(raw, len(k) + 5)


def test_mask_leaves_non_factor_columns_alone():
    k = _klines(80)
    raw = factor_calc.calculate_technical_factors(k)
    raw["close"] = k["close"].values
    masked = mask_warmup(raw, len(k))
    pd.testing.assert_series_equal(masked["close"], raw["close"])


def test_mask_does_not_mutate_the_input():
    k = _klines(80)
    raw = factor_calc.calculate_technical_factors(k)
    before = raw.copy()
    mask_warmup(raw, len(k))
    pd.testing.assert_frame_equal(raw, before)


# ─── Panel masking: the report explains itself ─────────────────────


def _panel(rows_per_symbol: int, symbols=("600000", "600001")) -> pd.DataFrame:
    frames = []
    for sym in symbols:
        k = _klines(rows_per_symbol, seed=hash(sym) % 1000)
        tech = factor_calc.calculate_technical_factors(k)
        tech["symbol"] = sym
        tech["close"] = k["close"].values
        frames.append(tech)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])
    for p in (5, 21):
        panel[f"fwd_{p}d"] = panel.groupby("symbol")["close"].transform(
            lambda s: s.shift(-p) / s - 1
        )
    return panel


def test_momentum_12m_is_skipped_with_a_reason_not_dropped():
    """Old behaviour: notna().any() filtered it out, indistinguishable from
    'never configured'. New behaviour: reported under warmup.skipped."""
    panel = _panel(140)
    report = generate_factor_report(panel, factors=["MOMENTUM_1M", "MOMENTUM_12M"])

    assert "MOMENTUM_12M" in report["warmup"]["skipped"]
    assert report["warmup"]["warmup_bars"]["MOMENTUM_12M"] == 252
    assert report["warmup"]["max_history_rows"] == 140
    names = {r["factor"] for r in report["ic_report"]}
    assert names == {"MOMENTUM_1M"}


def test_forward_window_that_has_not_happened_is_masked():
    panel = _panel(140)
    warm, _ = apply_warmup_mask(panel, ["MA_DEVIATION"], forward_horizons=(5, 21))
    per_sym = warm.groupby("symbol")
    for _, group in per_sym:
        assert group["fwd_5d"].iloc[-5:].isna().all()
        assert group["fwd_21d"].iloc[-21:].isna().all()
        assert group["fwd_21d"].iloc[:-21].notna().any()


def test_forward_window_guard_erases_a_tail_a_producer_filled_in():
    """``shift(-K)`` already leaves the tail NaN, so the guard above is
    vacuous against the in-repo producers. It is a contract guard for panels
    built elsewhere (cache dumps, research notebooks) — this panel starts
    with a *filled* tail, which is what makes the mask load-bearing."""
    panel = _panel(140)
    for horizon in (5, 21):
        panel[f"fwd_{horizon}d"] = panel[f"fwd_{horizon}d"].fillna(0.0)
    assert panel["fwd_21d"].notna().all()

    warm, _ = apply_warmup_mask(panel, ["MA_DEVIATION"], forward_horizons=(5, 21))
    for _, group in warm.groupby("symbol"):
        assert group["fwd_5d"].iloc[-5:].isna().all()
        assert group["fwd_21d"].iloc[-21:].isna().all()
        assert group["fwd_5d"].iloc[:-5].notna().all()
        assert group["fwd_21d"].iloc[:-21].notna().all()


def test_panel_mask_keeps_rows_after_warmup():
    panel = _panel(140)
    warm, report = apply_warmup_mask(panel, ["VOLUME_CLIMAX"])
    assert not report.skipped
    tail = warm.groupby("symbol")["VOLUME_CLIMAX"].apply(lambda s: s.iloc[20:])
    assert tail.notna().all()


def test_long_history_clears_the_widest_window():
    panel = _panel(300)
    report = generate_factor_report(panel, factors=["MOMENTUM_12M"])
    assert "MOMENTUM_12M" not in report["warmup"]["skipped"]
    assert report["warmup"]["max_history_rows"] == 300
    assert report["ic_report"]


def test_warmup_report_is_json_serializable():
    import json

    panel = _panel(140)
    report = generate_factor_report(panel, factors=["MOMENTUM_1M", "MOMENTUM_12M"])
    blob = json.dumps(report, ensure_ascii=False)
    assert '"warmup"' in blob
    assert '"skipped"' in blob


# ─── build_factor_panel wires the per-symbol mask ──────────────────


def test_build_factor_panel_masks_short_history(monkeypatch):
    """A symbol whose fetched history is short must not contribute a
    fabricated 1.0 to HIGH_52W_PROXIMITY's cross-section."""
    import selene_space_ashare.factor_report as fr

    short = _klines(140, seed=3)
    panel = fr.build_factor_panel(
        ["600519"],
        klines_fetch=lambda sym: short,
        include_industry=False,
    )
    assert panel["HIGH_52W_PROXIMITY"].isna().all()
    assert panel["GOLDEN_CROSS"].iloc[:60].isna().all()


# ─── calc_requires is read by someone ──────────────────────────────


def test_calc_requires_reports_a_missing_input_column():
    """The declarations used to be inert: an absent input column produced an
    all-NaN factor via calculate_technical_factors' broad except."""
    from selene_space_ashare.factor_warmup import missing_requirements

    assert missing_requirements(
        "VOLUME_CLIMAX", {"date", "open", "high", "low", "close"}
    ) == ["volume"]
    assert (
        missing_requirements(
            "VOLUME_CLIMAX", {"date", "high", "low", "close", "volume"}
        )
        == []
    )
    assert missing_requirements("NO_SUCH_FACTOR", set()) == []


def test_build_factor_panel_warns_when_a_required_column_is_absent(monkeypatch, caplog):
    """A kline frame without `volume` must be reported, not silently NaN."""
    import logging

    import selene_space_ashare.factor_report as fr

    k = _klines(140, seed=5).drop(columns=["volume", "amount", "turnover"])
    with caplog.at_level(logging.WARNING, logger="selene_space_ashare.factor_report"):
        fr.build_factor_panel(
            ["600519"], klines_fetch=lambda sym: k, include_industry=False
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "VOLUME_ZSCORE" in m and "volume" in m and "all-NaN" in m for m in messages
    ), messages
