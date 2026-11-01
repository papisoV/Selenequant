"""The cross-section floor for factor mining (2026-10-30).

``fitness.score``'s Rank-IC is per-date *cross-sectional*. One symbol means one
row per date, ``_spearman_pair`` returns NaN for ``len(x) < 2``, the daily IC
series is empty and every formula becomes a ``-inf`` sentinel — the whole
``--budget`` buys nothing. Two symbols score, but Spearman on two points is
always ±1, so the IC term has zero expectation.

These tests pin the entrance guards that turn both failure modes into a
message instead of a silent all-``-inf`` artifact:

1. ``_check_symbol_count`` rejects a single symbol and warns on two.
2. The CLI default is a real cross-section (never one symbol).
3. ``main()`` exits 2 (a flag error, not a traceback) on a single symbol.
4. The regression itself: a single-symbol panel scores ``-inf``, two symbols
   score. This is what the guard exists to prevent — if scoring ever learns to
   handle one symbol, that test fails and the guard becomes removable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_factor_research.mining import fitness, runner


# ---------------------------------------------------------------------------
# The guard itself.
# ---------------------------------------------------------------------------
def test_single_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 2 symbols"):
        runner._check_symbol_count(["BTC/USDT"])


def test_two_symbols_warn_about_the_degenerate_ic() -> None:
    warning = runner._check_symbol_count(["BTC/USDT", "ETH/USDT"])
    assert warning is not None
    assert "only 2 symbols" in warning
    assert "+/-1" in warning  # ASCII: a GBK console mangles the real ± glyph


def test_three_symbols_warn_too_because_four_is_the_comfort_floor() -> None:
    assert runner._check_symbol_count(["A", "B", "C"]) is not None


def test_four_symbols_are_quiet() -> None:
    assert runner._check_symbol_count(["A", "B", "C", "D"]) is None
    assert runner._check_symbol_count(["A", "B", "C", "D", "E"]) is None


def test_guard_constants_are_ordered() -> None:
    """The hard floor must sit below the comfort threshold, else a run that
    passes the floor can never warn."""
    assert runner._MIN_SYMBOLS < runner._COMFORTABLE_SYMBOLS


# ---------------------------------------------------------------------------
# The default must be a cross-section.
# ---------------------------------------------------------------------------
def test_cli_default_symbols_clear_the_guard() -> None:
    """The old default was ``BTC/USDT`` — one symbol, guaranteed all -inf."""
    default = runner._parse_symbols(runner.DEFAULT_SYMBOLS)
    assert len(default) >= runner._MIN_SYMBOLS
    assert runner._check_symbol_count(default) is None


def test_main_with_no_symbols_flag_runs_a_cross_section(tmp_path, monkeypatch) -> None:
    """The parser default itself must clear the guard: run ``main()`` with no
    ``--symbols`` and assert it does not exit 2 before reaching the loader.

    The loader is stubbed to ``SystemExit(0)``: the guard runs via argparse
    (exit 2) before any data is touched, so reaching the stub is exactly the
    proof that the default cleared the floor.
    """
    monkeypatch.setattr(
        runner, "load_klines", lambda *a, **k: (_ for _ in ()).throw(SystemExit(0))
    )
    with pytest.raises(SystemExit) as exc:
        runner.main(["--db", str(tmp_path / "missing.db")])
    # 0 = reached the (stubbed) loader; 2 would mean argparse rejected the default.
    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# main() fails fast through argparse (exit 2), before loading data.
# ---------------------------------------------------------------------------
def test_main_rejects_one_symbol_with_argparse_error(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        runner.main(["--symbols", "BTC/USDT", "--db", str(tmp_path / "nope.db")])
    # argparse's usage error exit code — a bad flag, not a crash.
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "at least 2 symbols" in err


# ---------------------------------------------------------------------------
# The underlying regression: why the guard exists.
# ---------------------------------------------------------------------------
def _ar1_panel(n_dates: int = 60, n_symbols: int = 5, seed: int = 7):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=n_dates)
    noise = rng.normal(0.0, 0.02, (n_dates, n_symbols))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n_dates):
        rets[t] = 0.5 * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s in range(n_symbols):
        sym = f"S{s}"
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": close[:, s],
                "high": close[:, s] * 1.004,
                "low": close[:, s] * 0.996,
                "close": close[:, s],
                "volume": np.full(n_dates, 1000.0),
            },
            index=dates,
        )
        closes = pd.Series(close[:, s], index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return data_by_symbol, ret_by_symbol


#: Simple momentum-reversal expression; any well-formed leaf formula works here
#: — the point is the panel width, not the formula.
def _tokens() -> list[int]:
    from selene_space_alpha.vocab import FORMULA_VOCAB

    names = list(FORMULA_VOCAB.token_names)
    return [names.index("RET"), names.index("NEG")]


def test_single_symbol_panel_scores_the_inf_sentinel() -> None:
    """The regression the guard prevents: one symbol ⇒ -inf for every formula."""
    data, ret = _ar1_panel(n_symbols=5)
    one = {next(iter(data)): data[next(iter(data))]}
    one_ret = {next(iter(ret)): ret[next(iter(ret))]}

    factor_df, ret_df = fitness.build_factor_panel(_tokens(), one, one_ret)
    assert len(fitness._daily_ic(factor_df, ret_df)) == 0  # no cross-section

    result = fitness.score(_tokens(), one, one_ret)
    assert result.valid is False
    assert result.score == float("-inf")


def test_multi_symbol_panel_scores_finitely() -> None:
    """Two or more symbols produce a scoreable panel and a finite score.

    Two symbols still leave the IC term degenerate (that is what the warning is
    for), but the sentinel is gone — which is the line the hard floor draws.
    """
    data, ret = _ar1_panel(n_symbols=5)
    two_syms = list(data)[:2]
    two = {s: data[s] for s in two_syms}
    two_ret = {s: ret[s] for s in two_syms}

    factor_df, ret_df = fitness.build_factor_panel(_tokens(), two, two_ret)
    assert len(fitness._daily_ic(factor_df, ret_df)) > 0

    result = fitness.score(_tokens(), two, two_ret)
    assert result.valid is True
    assert np.isfinite(result.score)
