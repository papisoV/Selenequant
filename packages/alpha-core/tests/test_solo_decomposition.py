"""Solo per-symbol decomposition (round 6): ``ds.solo`` long-only audit.

Exercises :mod:`selene_space_factor_research.mining.validation_protocol`'s new
per-symbol independent OOS backtest (``DatasetValidation.solo``). A
single-symbol panel puts every observation in quantile 1, so the long-short leg
would measure *shorting* the name at half notional; solo therefore runs a
``n_bins=1`` long-only WeightDrift backtest and reads the ``stats_l`` long-leg
metrics ("Ann. Return" / "Ann. Sharpe" / "Max Drawdown").

The ``_syn`` helper here mirrors ``test_validation_protocol.py`` but takes
per-symbol AR coefficients (``ars``): a positive coefficient means momentum
persistence, a negative one mean-reversion — the lever for making one
symbol's solo verdict diverge from the pooled verdict.

Acceptance covered:

- ``test_solo_populated_for_nondegenerate`` — non-degenerate panel yields one
  ``SymbolSolo`` per symbol with finite metrics.
- ``test_solo_unmasks_weak_symbol_invalid`` — an isolated mean-reverting symbol
  fails its own solo backtest; under solo propagation (decision item B) a solo
  SUSPICIOUS downgrades the formula level (a solo INVALID stays record-only).
- ``test_solo_rank_sorted_by_ann_ret_desc`` — ``to_dict()["solo"]`` is ranked
  by annualized return, descending.
- ``test_solo_skip_symbol_absent_in_oos`` — a symbol with no OOS rows is simply
  absent from the dict (absence is NOT PASS); no crash.
- ``test_solo_sufficient_data_sharpe_degenerate`` — a zero-variance return
  stream yields ``None`` Sharpe -> single-value INSUFFICIENT_DATA.
- ``test_solo_threshold_boundaries`` — exact threshold semantics for
  ``_symbol_solo_verdict`` (strict ``<`` / ``>``).
- ``test_solo_deterministic`` — same inputs, bit-identical report.
- ``test_solo_json_roundtrip`` — ``to_json()`` parses back to ``to_dict()``
  with the solo fields present.
- ``test_existing_fields_unchanged_by_solo`` — solo INVALID is record-only:
  pooled verdict/reason/aggregate stand even with failing solo entries.
- ``test_solo_no_oos_dates_no_crash`` — ``effective_folds()==0`` skips the solo
  block entirely instead of crashing (the R1 guard).
- ``test_solo_nan_never_pass`` — NaN/inf metrics are INSUFFICIENT_DATA, never
  misjudged PASS (the R2 guard).
- ``test_solo_does_not_reverse_pooled_verdict`` — the mirror direction: pooled
  INVALID stays INVALID even when a single name's solo is non-INVALID.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.validation_protocol import (
    OOS_IC_MIN,
    SOLO_ANN_RET_MIN,
    FoldConfig,
    SymbolSolo,
    _decide,
    _run_symbol_solo,
    _symbol_solo_verdict,
    validate_formulas,
    ValidationReport,
)
from selene_space_factor_research.mining.weight_drift_backtest import (
    WeightDriftConfig,
)


def _tok(name: str) -> int:
    return list(FORMULA_VOCAB.token_names).index(name)


RET = _tok("RET")
TS_RANK = _tok("TS_RANK")
NEG = _tok("NEG")

#: Momentum-following formula — strong, known-valid baseline on AR(1) data.
MOM = [RET, TS_RANK]
#: Anti-momentum — the primary INVALID case under momentum data.
ANTI = [RET, NEG]


def _syn(
    n_dates: int = 200,
    seed: int = 1,
    ars: tuple[float, ...] = (0.95, 0.95, 0.95, 0.95),
    sigma: float = 0.002,
    lengths: list[int] | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Synthetic OHLCV panels with per-symbol AR(1) return coefficients.

    ``ars[s] > 0`` makes symbol ``s`` momentum-persistent, ``ars[s] < 0`` makes
    it mean-reverting. ``lengths`` (optional) truncates each symbol's bar count
    so a shallow symbol can be absent from the OOS window. Returns
    ``(data_by_symbol, ret_by_symbol)`` matching the stack/validation contract.
    """
    n_symbols = len(ars)
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=n_dates)
    noise = rng.normal(0.0, sigma, (n_dates, n_symbols))
    volume_all = 1000.0 * (1.0 + 0.5 * rng.random((n_dates, n_symbols)))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    ar = np.asarray(ars, dtype=float)
    for t in range(1, n_dates):
        rets[t] = ar * rets[t - 1] + noise[t]

    if lengths is None:
        lengths = [n_dates] * n_symbols
    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s in range(n_symbols):
        sym = f"S{s}"
        n_s = max(1, min(lengths[s], n_dates))
        cl = 100.0 * np.cumprod(1.0 + rets[:n_s, s])
        op = cl / (1.0 + rets[:n_s, s])
        hi = np.maximum(op, cl) * 1.004
        lo = np.minimum(op, cl) * 0.996
        d_idx = dates[:n_s]
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": op,
                "high": hi,
                "low": lo,
                "close": cl,
                "volume": volume_all[:n_s, s],
            },
            index=d_idx,
        )
        closes = pd.Series(cl, index=d_idx)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return data_by_symbol, ret_by_symbol


def _validate(
    formula: list[int],
    data: dict[str, pd.DataFrame],
    ret: dict[str, pd.Series],
    *,
    n_trials: int = 20,
    min_train: int = 80,
    fold_size: int = 24,
    n_folds: int = 5,
    epoch: int = 12,
) -> "ValidationReport":
    return validate_formulas(
        [formula],
        {"syn": (data, ret)},
        n_trials=n_trials,
        fold_config=FoldConfig(
            min_train=min_train, fold_size=fold_size, n_folds=n_folds
        ),
        backtest_config=WeightDriftConfig(epoch=epoch),
    ).formulas[0]


def _single_symbol_frames(ret_values) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A one-symbol factor/ret panel in the stacked-panel column contract."""
    dates = pd.bdate_range(start="2024-01-01", periods=len(ret_values))
    rng = np.random.default_rng(7)
    factor = rng.normal(0.0, 1.0, len(ret_values))
    factor_df = pd.DataFrame({"trade_date": dates, "symbol": "S0", "factor": factor})
    ret_df = pd.DataFrame(
        {
            "trade_date": dates,
            "symbol": "S0",
            "fut_ret_1": np.asarray(ret_values, dtype=float),
        }
    )
    return factor_df, ret_df


# --------------------------------------------------------------------------
# Structure: solo populated with finite metrics for a non-degenerate panel.
# --------------------------------------------------------------------------
def test_solo_populated_for_nondegenerate() -> None:
    data, ret = _syn(n_dates=200, seed=1, ars=(0.9, 0.3))
    v = _validate(MOM, data, ret)
    solo = v.datasets["syn"].solo
    assert set(solo.keys()) == {"S0", "S1"}
    for s in solo.values():
        assert isinstance(s, SymbolSolo)
        assert s.ann_ret is not None and np.isfinite(s.ann_ret)
        assert s.sharpe is not None and np.isfinite(s.sharpe)
        assert s.max_drawdown is not None and np.isfinite(s.max_drawdown)
        assert s.verdict in ("PASS", "SUSPICIOUS", "INVALID", "INSUFFICIENT_DATA")
        assert s.reason


# --------------------------------------------------------------------------
# The core value: solo unmasks a pooled-buried weak symbol without reversing
# the pooled verdict.
# --------------------------------------------------------------------------
def test_solo_unmasks_weak_symbol_invalid() -> None:
    # Three momentum names carry the pooled panel; the mean-reverting S3 is
    # buried in the cross-section but fails its own solo backtest. Under solo
    # propagation (decision item B) any solo non-PASS verdict below VALID
    # evidence downgrades the formula level — here S3's solo trips the gate
    # and the formula can no longer surface as clean VALID.
    data, ret = _syn(n_dates=200, seed=1, ars=(0.95, 0.95, 0.95, -0.9))
    v = _validate(MOM, data, ret)
    solo = v.datasets["syn"].solo
    assert solo["S3"].verdict != "PASS"
    if solo["S3"].verdict == "SUSPICIOUS":
        # Propagation path: formula downgrades and names the symbol.
        assert v.verdict == "SUSPICIOUS"
        assert v.decision == "hold"
        assert "S3" in v.reason
    else:
        # Solo INVALID is record-only: pooled verdict stands on its own terms.
        assert "annualized return" in solo["S3"].reason


def test_solo_does_not_reverse_pooled_verdict() -> None:
    # Mirror: under the anti-momentum formula the pooled panel is INVALID, but
    # the mean-reverting name is actually *positively* served by that formula —
    # its solo is non-INVALID. Pooled INVALID must stay put; solo only records.
    data, ret = _syn(n_dates=200, seed=1, ars=(0.95, 0.95, 0.95, -0.9))
    v = _validate(ANTI, data, ret)
    assert v.verdict == "INVALID"
    solo = v.datasets["syn"].solo
    non_invalid = [s for s in solo.values() if s.verdict != "INVALID"]
    assert non_invalid, "expected >=1 symbol whose solo is not INVALID"


# --------------------------------------------------------------------------
# Ranking: to_dict()["solo"] ordered by ann_ret descending.
# --------------------------------------------------------------------------
def test_solo_rank_sorted_by_ann_ret_desc() -> None:
    data, ret = _syn(n_dates=200, seed=1, ars=(0.95, 0.95, 0.95, -0.9))
    v = _validate(MOM, data, ret)
    solo_payload = v.datasets["syn"].to_dict()["solo"]
    vals = [s["ann_ret"] for s in solo_payload.values()]
    assert vals == sorted(vals, reverse=True)


# --------------------------------------------------------------------------
# Absence: a symbol with no OOS rows is simply missing (not PASS), no crash.
# --------------------------------------------------------------------------
def test_solo_skip_symbol_absent_in_oos() -> None:
    data, ret = _syn(n_dates=200, seed=3, lengths=[200, 200, 80, 200])
    v = _validate(MOM, data, ret)
    solo = v.datasets["syn"].solo
    assert "S2" not in solo  # shallow symbol has no OOS slice
    assert {"S0", "S1", "S3"} <= set(solo.keys())


# --------------------------------------------------------------------------
# Degenerate returns -> deterministic non-PASS (never a false PASS).
# --------------------------------------------------------------------------
def test_solo_sufficient_data_sharpe_degenerate() -> None:
    # Zero-variance returns: the flat NAV only ever sheds costs, so the
    # annualized return lands below the net-of-cost floor -> INVALID
    # (deterministically, and never a false PASS).
    f_df, r_df = _single_symbol_frames([0.0] * 60)
    triple = _run_symbol_solo(f_df, r_df, WeightDriftConfig(epoch=12))
    status, reason = _symbol_solo_verdict(*triple)
    assert status == "INVALID"
    assert "annualized return" in reason
    assert triple[0] is not None and triple[0] < SOLO_ANN_RET_MIN


# --------------------------------------------------------------------------
# Threshold semantics (direct helper test).
# --------------------------------------------------------------------------
def test_solo_threshold_boundaries() -> None:
    # Max Drawdown is stored loss-is-negative (cum_nv/cummax - 1 <= 0), so the
    # cap is a magnitude floor on the negative side.
    v, reason = _symbol_solo_verdict(0.0199, 0.7, -0.05)
    assert v == "INVALID" and "annualized return" in reason
    assert _symbol_solo_verdict(0.05, 0.49, -0.05)[0] == "SUSPICIOUS"
    assert _symbol_solo_verdict(0.05, 0.7, -0.101)[0] == "SUSPICIOUS"
    # Exact boundaries: strict < / > semantics mean equality passes.
    assert _symbol_solo_verdict(0.02, 0.5, -0.10)[0] == "PASS"
    assert _symbol_solo_verdict(0.05, 0.7, -0.05)[0] == "PASS"
    assert _symbol_solo_verdict(None, None, None)[0] == "INSUFFICIENT_DATA"


def test_solo_nan_never_pass() -> None:
    # NaN/inf inputs — even without the upstream _f2e normalization — must not
    # misjudge PASS (NaN compares False to every threshold).
    assert _symbol_solo_verdict(np.nan, np.nan, np.nan)[0] == "INSUFFICIENT_DATA"
    assert _symbol_solo_verdict(np.nan, 0.7, -0.02)[0] == "INSUFFICIENT_DATA"
    assert _symbol_solo_verdict(np.inf, 0.7, -0.02)[0] == "INSUFFICIENT_DATA"
    # An all-NaN return stream is "no usable evidence": the backtest metrics
    # degrade to None -> INSUFFICIENT_DATA (the mixed-NaN case is filtered by
    # the engine's dropna and is a legitimate backtest, not a NaN metric).
    f_df, r_df = _single_symbol_frames([np.nan] * 60)
    triple = _run_symbol_solo(f_df, r_df, WeightDriftConfig(epoch=12))
    assert triple == (None, None, None)
    assert _symbol_solo_verdict(*triple)[0] == "INSUFFICIENT_DATA"


# --------------------------------------------------------------------------
# Determinism / serialization.
# --------------------------------------------------------------------------
def test_solo_deterministic() -> None:
    data, ret = _syn(n_dates=120, seed=11, ars=(0.95, 0.3))
    kwargs = dict(
        n_trials=10,
        min_train=60,
        fold_size=15,
        n_folds=3,
        epoch=10,
    )
    r1 = _validate(MOM, data, ret, **kwargs)
    r2 = _validate(MOM, data, ret, **kwargs)
    assert r1.to_dict() == r2.to_dict()


def test_solo_json_roundtrip() -> None:
    data, ret = _syn(n_dates=120, seed=12, ars=(0.9, 0.3))
    report = validate_formulas(
        [MOM],
        {"syn": (data, ret)},
        n_trials=10,
        fold_config=FoldConfig(min_train=60, fold_size=15, n_folds=3),
        backtest_config=WeightDriftConfig(epoch=10),
    )
    payload = report.to_dict()
    assert json.loads(report.to_json()) == payload
    solo0 = payload["formulas"][0]["datasets"]["syn"]["solo"]["S0"]
    for key in ("symbol", "ann_ret", "sharpe", "max_drawdown", "verdict", "reason"):
        assert key in solo0


# --------------------------------------------------------------------------
# Add-only: solo is a diagnostic field; pooled verdict/reason stay identical.
# --------------------------------------------------------------------------
def test_existing_fields_unchanged_by_solo() -> None:
    # Mirrors test_cost_stress_disabled_by_default's frozen invariants with the
    # solo feature present. Solo INVALID entries are record-only: the pooled
    # verdict stands on its own terms even though three names fail solo.
    data, ret = _syn(n_dates=200, seed=1, ars=(0.9, 0.8, 0.7, 0.6))
    v = _validate(MOM, data, ret)
    assert v.verdict == "VALID"
    assert v.reason == (
        "OOS Rank-IC, subinterval agreement, multi-symbol agreement and DSR all pass"
    )
    assert v.aggregate.oos_rank_ic > OOS_IC_MIN
    solo = v.datasets["syn"].solo
    assert solo  # populated
    assert any(s.verdict == "INVALID" for s in solo.values())  # yet non-gating


# --------------------------------------------------------------------------
# No OOS window -> solo skipped entirely (no UnboundLocalError crash).
# --------------------------------------------------------------------------
def test_solo_no_oos_dates_no_crash() -> None:
    data, ret = _syn(n_dates=70, seed=3, ars=(0.9, 0.3))
    v = _validate(MOM, data, ret, min_train=80, fold_size=20, n_folds=3)
    assert v.verdict == "INSUFFICIENT_DATA"
    assert v.datasets["syn"].solo == {}


# --------------------------------------------------------------------------
# Solo SUSPICIOUS propagation (user decision item B, 2026-09-10): a per-symbol
# SUSPICIOUS solo verdict downgrades the formula level to SUSPICIOUS/hold —
# a single-name disaster must not ride a healthy pooled panel to accept.
# --------------------------------------------------------------------------
def _passing_decide_kwargs() -> dict:
    return {
        "degenerate": False,
        "pooled_oos_ic": OOS_IC_MIN + 0.1,
        "subinterval_agreement": 1.0,
        "multi_symbol_agreement": 1.0,
        "dsr": 0.99,
        "n_oos_folds": 5,
    }


def test_decide_solo_failure_downgrades_to_suspicious() -> None:
    # Unit: one solo SUSPICIOUS entry flips an otherwise-clean panel.
    verdict, reason = _decide(
        **_passing_decide_kwargs(),
        solo_failures=["solo UNI/USDT: max drawdown magnitude 0.319 above 0.1"],
    )
    assert verdict == "SUSPICIOUS"
    assert "solo UNI/USDT" in reason


def test_decide_insufficient_solo_is_not_a_failure() -> None:
    # No evidence is the solo contract's conservative exit — it must never
    # accuse the formula. A solo INSUFFICIENT_DATA entry does not propagate.
    verdict, _ = _decide(
        **_passing_decide_kwargs(),
        solo_failures=[],
    )
    assert verdict == "VALID"
    verdict, _ = _decide(**_passing_decide_kwargs(), solo_failures=None)
    assert verdict == "VALID"


def test_solo_suspicious_propagates_to_formula_level() -> None:
    # Integration over the real data flow: three healthy momentum names +
    # one name whose solo backtest trips the SUSPICIOUS band (mdd beyond cap
    # or sharpe below floor). The formula-level verdict must surface the
    # solo failure as SUSPICIOUS with decision == "hold".
    data, ret = _syn(n_dates=200, seed=1, ars=(0.95, 0.95, 0.95, -0.35))
    v = _validate(MOM, data, ret)
    solo = v.datasets["syn"].solo
    suspicious = [s for s in solo.values() if s.verdict == "SUSPICIOUS"]
    assert suspicious, "expected >=1 solo SUSPICIOUS under the tuned AR mix"
    assert v.verdict == "SUSPICIOUS"
    assert v.decision == "hold"
    for s in suspicious:
        assert s.symbol in v.reason
