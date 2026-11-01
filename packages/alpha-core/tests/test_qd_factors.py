"""QD-absorption B3 factor anchors — hand-computed expected values.

Cleanroom anchors for the three QD-absorption OHLCV factors
(:mod:`selene_space_alpha.factors.qd_factors`): CHOPPINESS_14,
ELDER_BULL_13, ELDER_BEAR_13. Every expected value is computed by hand
from the textbook indicator definitions (see module docstring) on small
synthetic series — no reference implementation is consulted.

Also locks the vocabulary wiring: the three tokens exist at the tail of
FEATURE_NAMES (97..99), the shared-segment digest is unchanged (W-tree
compatibility preserved), and the PandasStackVM resolves them to real
(non-degenerate) factor functions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_alpha.factors.pandas_bridge import ALL_FACTORS
from selene_space_alpha.factors.qd_factors import (
    compute_choppiness_14,
    compute_elder_bear_13,
    compute_elder_bull_13,
)
from selene_space_alpha.vocab import FEATURE_NAMES, FORMULA_VOCAB
from selene_space_alpha.vm.vm_pandas import PandasStackVM


def _ohlc(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """rows of (open, high, low, close); volume column zeroed."""
    idx = pd.bdate_range("2026-01-05", periods=len(rows))
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close"], index=idx
    ).assign(volume=0.0)


# --- CHOPPINESS_14 hand anchors ---------------------------------------------


def test_choppiness_constant_price_is_zero() -> None:
    """Flat series: TR sum == range == 0... denominator guards -> 0 fill.

    With high == low == close on every bar, sum(TR) = 0 and (hi - lo) = 0.
    The zero-range guard fills with 0.0 (choppiness undefined, degraded).
    """
    rows = [(10.0, 10.0, 10.0, 10.0)] * 20
    ci = compute_choppiness_14(_ohlc(rows))
    assert (ci == 0.0).all()


def test_choppiness_trending_market_is_low() -> None:
    """Monotonic +1 uptrend: gaps dominate TR -> low CI.

    Hand-computed for bar t (t>=1): high=t+1, low=t, prev_close=t-0.5.
    TR = max(1, 1.5, 0.5) = 1.5 each bar. 14-bar window ending at index 14
    covers bars 1..14: sum(TR) = 21. hi = 15 (bar-14 high), lo = 1 (bar-1
    low), range = 14. Ratio = 21/14 = 1.5.
    CI = 100 * log10(1.5) / log10(14) = 15.364013...
    """
    rows = [(float(i), float(i) + 1.0, float(i), float(i) + 0.5) for i in range(20)]
    ci = compute_choppiness_14(_ohlc(rows))
    # First 13 bars: window incomplete -> 0.0 (fillna); bar 14 onward = anchor.
    assert ci.iloc[:13].eq(0.0).all()
    expected = 100.0 * np.log10(1.5) / np.log10(14.0)
    assert abs(ci.iloc[14] - expected) < 1e-9


def test_choppiness_wide_swing_is_high() -> None:
    """Alternating 10-unit swings: TR sum >> net range -> CI near 100.

    Bars alternate even: (5, 10, 0, 5) / odd: (15, 20, 10, 15).
    Bar t>=1: high-low = 10; |high - prev_close| = 15; |low - prev_close| = 15
    -> TR = 15. 14-bar window ending at 14: sum = 210; hi = 20, lo = 0,
    range = 20. Ratio = 210/20 = 10.5.
    CI = 100 * log10(10.5) / log10(14) = 89.0992...
    """
    rows = []
    for i in range(20):
        base = 10.0 * (i % 2)
        rows.append((base + 5.0, base + 10.0, base, base + 5.0))
    ci = compute_choppiness_14(_ohlc(rows))
    expected = 100.0 * np.log10(10.5) / np.log10(14.0)
    assert abs(ci.iloc[14] - expected) < 1e-9


# --- ELDER_BULL_13 / ELDER_BEAR_13 hand anchors ------------------------------


def test_elder_bull_bear_constant_price_is_zero() -> None:
    """Flat close: EMA(13) == close, high == low == close -> both powers 0."""
    rows = [(50.0, 50.0, 50.0, 50.0)] * 20
    data = _ohlc(rows)
    bull = compute_elder_bull_13(data)
    bear = compute_elder_bear_13(data)
    assert (bull == 0.0).all()
    assert (bear == 0.0).all()


def test_elder_bull_bear_hand_computed_anchor() -> None:
    """Hand-computed EMA(13) on a 14-bar ramp.

    Bars 0..13 close = 0,1,2,...,13; high = close + 2; low = close - 1.
    EMA (span=13, adjust=False, min_periods=13): seed = close[0] = 0, then
      ema_t = ema_{t-1} + (2/14) * (close_t - ema_{t-1}).
    Hand recurrence (alpha = 1/7):
      e0=0
      e1=1/7
      e2=1/7+6/49=13/49
      e3=13/49+6/343=97/343
      ... closed form: e_t = 6*(1 - (6/7)^t) for the unit ramp
      e13 = 6*(1 - (6/7)^13) = 6*(1 - 0.0659145) = 5.6045128...
    Anchor at t=13 (first full window):
      bull = high - ema = 15 - 5.6045128 = 9.3954872...
      bear = low  - ema = 12 - 5.6045128 = 6.3954872...
    """
    rows = [(float(i), float(i) + 2.0, float(i) - 1.0, float(i)) for i in range(14)]
    data = _ohlc(rows)
    bull = compute_elder_bull_13(data)
    bear = compute_elder_bear_13(data)

    alpha = 1.0 / 7.0
    ema = 0.0
    for i in range(1, 14):
        ema = ema + alpha * (float(i) - ema)
    # EMA seed is close[0]=0; min_periods=13 makes bars 0..11 NaN -> 0.0 fill.
    assert bull.iloc[:12].eq(0.0).all()
    assert bear.iloc[:12].eq(0.0).all()

    assert abs(bull.iloc[13] - (15.0 - ema)) < 1e-6
    assert abs(bear.iloc[13] - (12.0 - ema)) < 1e-6


def test_elder_bull_positive_when_price_above_ema() -> None:
    """After a jump, high sits above the lagging EMA -> bull > 0."""
    rows = [(100.0, 100.0, 100.0, 100.0)] * 13 + [(100.0, 120.0, 99.0, 110.0)]
    data = _ohlc(rows)
    bull = compute_elder_bull_13(data)
    bear = compute_elder_bear_13(data)
    assert bull.iloc[13] > 0.0
    assert bear.iloc[13] < 0.0  # low (99) below EMA (~100) -> negative


# --- vocabulary wiring -------------------------------------------------------


def test_qd_tokens_at_vocab_tail() -> None:
    # QD B3 tokens are no longer the vocab tail — the B2 BTC_BG_* block
    # (3 background factors) and then the 7 artifact-bridge factors were
    # appended after them.
    qd_tail = FEATURE_NAMES[-13:-10]
    assert qd_tail == ("CHOPPINESS_14", "ELDER_BULL_13", "ELDER_BEAR_13")
    assert FEATURE_NAMES[-10:-7] == (
        "BTC_BG_RS_LEVEL",
        "BTC_BG_RS_SLOPE",
        "BTC_BG_RS_EXTREME",
    )
    assert FEATURE_NAMES[-7:] == (
        "ICHIMOKU_KIJUN_DEV",
        "GK_VOL",
        "RET_ENTROPY_20",
        "RVOL",
        "MACD_HIST",
        "TRIX_15",
        "HMA_DEV_20",
    )
    assert len(FEATURE_NAMES) == 110
    assert FORMULA_VOCAB.feature_count == 110
    assert FORMULA_VOCAB.size == 110 + 23


def test_shared_segment_digest_unchanged() -> None:
    """AW_*/QD_* tails don't participate in the shared digest (W compat);
    the TS_STD_20 operator append (2026-09-18 artifact batch) DID move it —
    re-blessed, see test_vocab_k_superset.W_VOCAB_DIGEST_ORACLE."""
    assert FORMULA_VOCAB.vocab_digest == "selene-vocab-09b5d4b06dc5ed2b"


def test_all_factors_registry_contains_qd() -> None:
    for key in ("qd_choppiness_14", "qd_elder_bull_13", "qd_elder_bear_13"):
        assert key in ALL_FACTORS


def test_vm_resolves_qd_tokens_to_real_functions() -> None:
    """PandasStackVM must bind the three tokens to non-degenerate factors."""
    vm = PandasStackVM()
    names = ("CHOPPINESS_14", "ELDER_BULL_13", "ELDER_BEAR_13")
    for name in names:
        idx = list(FEATURE_NAMES).index(name)
        feat_idx, fn = vm._factor_fns[idx]
        assert feat_idx == name
        assert fn is not None


def test_vm_executes_qd_token_on_real_data() -> None:
    """End-to-end: single-token formula runs through PandasStackVM.execute."""
    rows = [
        (float(i), float(i) + 2.0, max(float(i) - 1.0, 0.0), float(i) + 0.5)
        for i in range(30)
    ]
    data = _ohlc(rows).assign(volume=100.0)
    vm = PandasStackVM()
    for name in ("CHOPPINESS_14", "ELDER_BULL_13", "ELDER_BEAR_13"):
        tok = list(FEATURE_NAMES).index(name)
        out = vm.execute([tok], data)
        assert out is not None, name
        # non-degenerate: at least the full-window bars carry signal
        assert out.iloc[14:].abs().sum() > 0.0, name
