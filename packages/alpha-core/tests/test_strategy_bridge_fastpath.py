"""Full-series fast path equivalence tests.

The fast path (prepare_full_series + O(1) lookups in on_kline) must produce
byte-identical signals AND strengths to the bar-by-bar path for causal
formulas, and must refuse to arm (falling back to the slow path) when it
cannot prove equivalence — the spot check compares prepared scores against
trailing-200 window recompute at sampled positions.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy


def _make_df(n: int = 400, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, n))
    high = close + rng.random(n) + 0.5
    low = close - rng.random(n) - 0.5
    open_ = close + rng.normal(0, 0.4, n)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.random(n) * 1000 + 1.0,
            "liquidity": rng.random(n) * 100.0 + 1.0,
            "fdv": rng.random(n) * 500.0 + 100.0,
        }
    )


class _Kline:
    def __init__(self, row, ts: int):
        self.symbol = "TEST/USDT"
        self.timestamp = ts
        self.interval = "1h"
        self.open = float(row["open"])
        self.high = float(row["high"])
        self.low = float(row["low"])
        self.close = float(row["close"])
        self.volume = float(row["volume"])


def _replay_signals(tokens: list[int], df: pd.DataFrame, fast: bool):
    strategy = AlphaFormulaStrategy(
        formula_tokens=tokens, buy_threshold=0.6, sell_threshold=0.4
    )
    if fast:
        assert strategy.prepare_full_series(df), "fast path should arm here"
    results = []
    for i, (_, row) in enumerate(df.iterrows()):
        sig = strategy.on_kline(_Kline(row, 1_700_000_000_000 + i * 3_600_000))
        if sig is not None:
            results.append((sig.side.name, round(sig.strength, 9)))
    return results


# Default vocab (OPERATOR_DEFS order incl. GATE): ops start at feat_count=110.
# 2=PRESSURE (element-wise causal), 123=TS_MEAN (rolling 5, causal),
# 127=RANK (whole-frame rank — windowless, must decline).
_TOKENS = [2, 123]  # PRESSURE -> TS_MEAN


def test_fast_path_signals_equal_slow_path():
    df = _make_df()
    assert _replay_signals(_TOKENS, df, fast=False) == _replay_signals(
        _TOKENS, df, fast=True
    )


def test_fast_path_armed_slow_path_not():
    strategy = AlphaFormulaStrategy(formula_tokens=_TOKENS)
    assert strategy.prepare_full_series(_make_df())
    assert strategy._fast_scores is not None

    bare = AlphaFormulaStrategy(formula_tokens=_TOKENS)
    assert bare._fast_scores is None


def test_prepare_declines_on_short_frame():
    strategy = AlphaFormulaStrategy(formula_tokens=_TOKENS)
    assert not strategy.prepare_full_series(_make_df(40))


def test_prepare_declines_on_windowless_formula():
    # RANK normalizes over the whole given frame: a full-series run shifts
    # semantics vs the trailing-200 windows. The spot check must catch the
    # divergence and refuse to arm.
    df = _make_df(400, seed=11)
    strategy = AlphaFormulaStrategy(formula_tokens=[2, 127])  # PRESSURE -> RANK
    assert not strategy.prepare_full_series(df)
    assert strategy._fast_scores is None


def test_reset_clears_fast_state():
    strategy = AlphaFormulaStrategy(formula_tokens=_TOKENS)
    assert strategy.prepare_full_series(_make_df())
    strategy.reset()
    assert strategy._fast_scores is None
    assert strategy._fast_pos == 0
