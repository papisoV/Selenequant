"""T4 validation protocol (US-04): walk-forward verdicts on synthetic panels.

Exercises :mod:`selene_space_factor_research.mining.validation_protocol` on
deterministic synthetic AR(1) OHLCV panels (the same ``_syn`` style as
``test_genetic_search.py``, plus an ``n_strong`` strong/weak switch). The
momentum-following formula ``[RET, TS_RANK]`` predicts next-bar returns on
AR(1) data; ``[RET, NEG]`` is its anti-momentum inverse.

Acceptance covered:

- ``test_momentum_valid_antimomentum_invalid`` — the HARD discrimination:
  ``[RET, TS_RANK]`` is VALID (OOS IC / subinterval / multi-symbol / DSR all
  pass) while ``[RET, NEG]`` is INVALID (negative OOS IC).
- ``test_oos_metrics_do_not_leak_train_window`` — when the strong signal stops
  after bar ``n_strong``, the OOS window's IC collapses while the train window
  stays high: the protocol's OOS metrics are computed on the OOS window only.
- ``test_fold_config_adaptive_and_per_fold_metrics`` — ``effective_folds``
  auto-reduces on short data and the expanding-window train boundary grows.
- ``test_multi_symbol_agreement_path`` — >= 2/3 of symbols share the pooled OOS
  sign for a genuine momentum formula.
- ``test_insufficient_data_no_oos`` — data with no OOS window is
  INSUFFICIENT_DATA, not a false VALID/INVALID.
- ``test_deterministic`` — same inputs, bit-identical report.
- ``test_to_dict_json_roundtrip`` — ``to_json()`` parses back to ``to_dict()``.

US-201 additions (noise-band calibration + snapshot binding):

- ``test_noise_band_pure_function`` — the theoretical zero-signal IC standard
  error, incl. degenerate-input NaN convention.
- ``test_noise_suspect_pure_noise_library`` — 216 synthetic random-IC formulas
  (fixed seed) flag ``noise_suspect=True``.
- ``test_noise_suspect_false_with_real_signal`` — a library with a genuinely
  strong factor among noise does NOT flag.
- ``test_ic_noise_band_wired_into_metrics`` — per-fold / dataset / aggregate
  bands present and equal to ``noise_band(n_symbols, fold_dates)``.
- ``test_snapshot_binding_deterministic_and_value_sensitive`` — same data
  twice -> same ``data_snapshot_sha256``; one mutated price -> different;
  ``script_sha256`` matches the module's own bytes.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.validation_protocol import (
    DSR_THRESHOLD,
    MULTI_SYMBOL_AGREEMENT_MIN,
    OOS_IC_MIN,
    SUBINTERVAL_AGREEMENT_MIN,
    FoldConfig,
    ValidationReport,
    noise_band,
    validate_formulas,
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
#: Anti-momentum — the primary INVALID case (negative OOS IC).
ANTI = [RET, NEG]


def _syn(
    n_dates: int = 40,
    n_symbols: int = 2,
    seed: int = 42,
    ar: float = 0.5,
    sigma: float = 0.02,
    n_strong: int | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Synthetic OHLCV panels: AR(1) per-symbol returns (momentum persistence).

    ``n_strong`` truncates the AR(1) regime: bars before ``n_strong`` follow
    ``rets[t] = ar*rets[t-1] + noise[t]``, bars at/after follow iid noise
    (``ar=0``) — the strong/weak construction used by the OOS-misalignment
    test. Returns ``(data_by_symbol, ret_by_symbol)`` matching ``fitness``'s
    contract (DatetimeIndex + open/high/low/close/volume; forward-return
    Series).
    """
    if n_strong is None:
        n_strong = n_dates
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=n_dates)
    noise = rng.normal(0.0, sigma, (n_dates, n_symbols))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n_dates):
        coeff = ar if t < n_strong else 0.0
        rets[t] = coeff * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    open_ = close / (1.0 + rets)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    volume = 1000.0 * (1.0 + 0.5 * rng.random((n_dates, n_symbols)))

    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s in range(n_symbols):
        sym = f"S{s}"
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": open_[:, s],
                "high": high[:, s],
                "low": low[:, s],
                "close": close[:, s],
                "volume": volume[:, s],
            },
            index=dates,
        )
        closes = pd.Series(close[:, s], index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return data_by_symbol, ret_by_symbol


def _validate(
    formulas: list[list[int]],
    data: dict[str, pd.DataFrame],
    ret: dict[str, pd.Series],
    *,
    n_trials: int,
    fold_config: FoldConfig,
    epoch: int = 12,
) -> ValidationReport:
    """One validated run on the synthetic dataset keyed ``"syn"``."""
    return validate_formulas(
        formulas,
        {"syn": (data, ret)},
        n_trials=n_trials,
        fold_config=fold_config,
        backtest_config=WeightDriftConfig(epoch=epoch),
    )


# --------------------------------------------------------------------------
# HARD discrimination: momentum VALID, anti-momentum INVALID.
# --------------------------------------------------------------------------
def test_momentum_valid_antimomentum_invalid() -> None:
    # Strong clean AR(1) momentum, 4 symbols, seed=2 (verified: DSR=1.0, and
    # no solo SUSPICIOUS entries that would propagate to the formula level).
    data, ret = _syn(n_dates=200, n_symbols=4, seed=2, ar=0.95, sigma=0.002)
    report = _validate(
        [MOM, ANTI],
        data,
        ret,
        n_trials=20,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
    )
    mom, anti = report.formulas

    assert mom.verdict == "VALID"
    assert mom.decision == "accept"
    agg = mom.aggregate
    assert agg.oos_rank_ic is not None and agg.oos_rank_ic > OOS_IC_MIN
    assert agg.subinterval_agreement is not None
    assert agg.subinterval_agreement >= SUBINTERVAL_AGREEMENT_MIN
    assert agg.multi_symbol_agreement is not None
    assert agg.multi_symbol_agreement >= MULTI_SYMBOL_AGREEMENT_MIN
    assert agg.dsr is not None and agg.dsr > DSR_THRESHOLD
    assert agg.n_oos_folds >= 3
    # OOS evidence is real, not a single-fold fluke: at least 60% of the folds'
    # OOS ICs share the pooled positive sign (cross-sectional Rank-IC on a
    # 24-bar x 4-symbol window is noisy, so the aggregate gates — subinterval
    # agreement >= 0.7 above — are the binding checks, not every single fold).
    for ds in mom.datasets.values():
        positive = [
            f for f in ds.folds if f.oos_rank_ic is not None and f.oos_rank_ic > 0.0
        ]
        assert len(positive) >= 3

    assert anti.verdict == "INVALID"
    assert anti.decision == "reject"
    assert anti.aggregate.oos_rank_ic is not None
    assert anti.aggregate.oos_rank_ic <= OOS_IC_MIN


# --------------------------------------------------------------------------
# OOS time-misalignment: the OOS window must not inherit the train signal.
# --------------------------------------------------------------------------
def test_oos_metrics_do_not_leak_train_window() -> None:
    # Strong momentum until bar 160, then iid noise. Fold 0 trains on [0:160)
    # (high IC) and scores OOS on [160:180) (signal gone -> IC ~ 0).
    # (n_strong+min_train scaled 80→160 when _robust_norm went trailing-window:
    # the rolling warmup eats the first ~20 bars, and an 80-bar train window
    # no longer carried enough live bars to clear the 0.3 IC bar.)
    data, ret = _syn(
        n_dates=240, n_symbols=3, seed=7, ar=0.9, sigma=0.002, n_strong=160
    )
    report = _validate(
        [MOM],
        data,
        ret,
        n_trials=10,
        fold_config=FoldConfig(min_train=160, fold_size=20, n_folds=3),
        epoch=10,
    )
    v = report.formulas[0]
    ds = v.datasets["syn"]
    assert len(ds.folds) == 3

    fold0 = ds.folds[0]
    # (0.3→0.25 when _robust_norm went trailing-window: ~20 neutral warmup
    # bars dilute the 160-bar train IC slightly; the load-bearing assertions
    # are the OOS-vs-train comparisons below.)
    assert fold0.train_rank_ic is not None and fold0.train_rank_ic > 0.25
    assert fold0.oos_rank_ic is not None and fold0.oos_rank_ic < 0.15
    assert fold0.oos_rank_ic < fold0.train_rank_ic

    # Pooled over all folds: OOS IC is structurally lower than train IC.
    assert v.aggregate.train_rank_ic is not None
    assert v.aggregate.oos_rank_ic is not None
    assert v.aggregate.oos_rank_ic < 0.5 * v.aggregate.train_rank_ic

    # Per-fold detail is JSON-shaped and has the CycleReport-aligned fields.
    detail = fold0.to_dict()
    for key in (
        "fold_index",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "train_rank_ic",
        "oos_rank_ic",
        "per_symbol_oos_ic",
        "oos_n_obs",
    ):
        assert key in detail


# --------------------------------------------------------------------------
# Fold geometry: auto-reduced folds + expanding train window.
# --------------------------------------------------------------------------
def test_fold_config_adaptive_and_per_fold_metrics() -> None:
    cfg = FoldConfig(min_train=80, fold_size=20, n_folds=5)
    assert cfg.effective_folds(80) == 0  # no OOS window at all
    assert cfg.effective_folds(140) == 3  # (140 - 80) // 20 = 3, capped at 5
    assert cfg.effective_folds(200) == 5  # full requested count
    assert cfg.effective_folds(400) == 5  # capped: never exceeds n_folds

    bounds = cfg.boundaries(140)
    assert [(t0, te0, s0, e0) for (t0, te0, s0, e0) in bounds] == [
        (0, 80, 80, 100),
        (0, 100, 100, 120),
        (0, 120, 120, 140),
    ]
    # expanding-window: train_end grows; OOS windows are adjacent and disjoint.
    assert [b[1] for b in bounds] == [80, 100, 120]


# --------------------------------------------------------------------------
# Multi-symbol same-direction evidence.
# --------------------------------------------------------------------------
def test_multi_symbol_agreement_path() -> None:
    data, ret = _syn(n_dates=200, n_symbols=3, seed=2, ar=0.95, sigma=0.002)
    report = _validate(
        [MOM],
        data,
        ret,
        n_trials=20,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
    )
    v = report.formulas[0]
    ds = v.datasets["syn"]
    assert ds.multi_symbol_agreement is not None
    assert ds.multi_symbol_agreement >= 2.0 / 3.0
    assert v.aggregate.multi_symbol_agreement is not None
    assert v.aggregate.multi_symbol_agreement >= MULTI_SYMBOL_AGREEMENT_MIN
    # every per-symbol OOS IC in every fold shares the pooled positive sign
    for f in ds.folds:
        for sym, ic in f.per_symbol_oos_ic.items():
            assert ic is not None and ic > 0.0, (sym, ic)


# --------------------------------------------------------------------------
# No OOS evidence -> INSUFFICIENT_DATA (never a false verdict).
# --------------------------------------------------------------------------
def test_insufficient_data_no_oos() -> None:
    data, ret = _syn(n_dates=80, n_symbols=2, seed=3)
    report = _validate(
        [MOM],
        data,
        ret,
        n_trials=5,
        fold_config=FoldConfig(min_train=80, fold_size=20, n_folds=3),
    )
    v = report.formulas[0]
    assert v.verdict == "INSUFFICIENT_DATA"
    assert v.decision == "defer"
    assert v.aggregate.n_oos_folds == 0


# --------------------------------------------------------------------------
# Determinism: same inputs, bit-identical report.
# --------------------------------------------------------------------------
def test_deterministic() -> None:
    data, ret = _syn(n_dates=120, n_symbols=3, seed=11, ar=0.5)
    kwargs = dict(
        n_trials=10,
        fold_config=FoldConfig(min_train=60, fold_size=15, n_folds=3),
    )
    r1 = validate_formulas(
        [MOM, ANTI],
        {"syn": (data, ret)},
        **kwargs,
        backtest_config=WeightDriftConfig(epoch=10),
    )
    r2 = validate_formulas(
        [MOM, ANTI],
        {"syn": (data, ret)},
        **kwargs,
        backtest_config=WeightDriftConfig(epoch=10),
    )
    assert r1.to_dict() == r2.to_dict()


# --------------------------------------------------------------------------
# Cost-stress dimension (B2): re-price OOS net returns under extra multiples.
# --------------------------------------------------------------------------
def test_cost_stress_disabled_by_default() -> None:
    # NO cost_stress_multipliers arg -> dimension fully off: verdict/reason
    # byte-identical, no cost-stress fields populated, no downgrade.
    # seed=2: baseline whose solo entries are all PASS/INVALID (no solo
    # SUSPICIOUS), so the verdict attribution here stays pure.
    data, ret = _syn(n_dates=200, n_symbols=4, seed=2, ar=0.95, sigma=0.002)
    report = _validate(
        [MOM],
        data,
        ret,
        n_trials=20,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
    )
    v = report.formulas[0]
    ds = v.datasets["syn"]
    assert v.verdict == "VALID"
    assert v.reason == (
        "OOS Rank-IC, subinterval agreement, multi-symbol agreement and DSR all pass"
    )
    assert "cost stress" not in v.reason
    assert v.aggregate.cost_stress is None
    assert ds.cost_stress is None
    assert ds.oos_turnover is not None  # turnover is always measured
    assert "oos_turnover" in ds.folds[0].to_dict()


def test_cost_stress_numeric_anchor() -> None:
    # The pure helper: sample Sharpe annualized by sqrt(ppy); None on tiny
    # samples / degenerate streams.
    from selene_space_factor_research.mining.validation_protocol import (
        _annualized_sharpe,
    )

    assert _annualized_sharpe(np.asarray([], dtype=float), 252) is None
    assert _annualized_sharpe(np.asarray([1.0], dtype=float), 252) is None
    assert _annualized_sharpe(np.asarray([1.0, 1.0, 1.0], dtype=float), 252) is None
    # mean/sqrt-annualized Sharpe, verified against the numpy formula
    # (mean 0.000714285..., std ddof=1 0.001799470...) -> 0.025005001...
    assert (
        abs(
            _annualized_sharpe(
                np.asarray(
                    [0.0, 0.001, 0.002, -0.001, 0.002, 0.003, -0.002], dtype=float
                ),
                252,
            )
            - 0.02500500150050017
        )
        < 1e-12
    )


def test_cost_stress_trigger_suspicious() -> None:
    # Strong MOM passes every gate at 1x; a large enough cost multiple makes the
    # stressed OOS Sharpe strictly negative -> the ONLY failure is cost stress,
    # so the downgrade to SUSPICIOUS is attributable to the cost-stress branch.
    data, ret = _syn(n_dates=200, n_symbols=4, seed=2, ar=0.95, sigma=0.002)
    report = _validate(
        [MOM],
        data,
        ret,
        n_trials=20,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
    )
    v = report.formulas[0]
    assert v.verdict == "VALID"  # precondition: base passes everything

    stressed = validate_formulas(
        [MOM],
        {"syn": (data, ret)},
        n_trials=20,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
        backtest_config=WeightDriftConfig(epoch=12),
        cost_stress_multipliers=(30,),
    ).formulas[0]
    assert stressed.verdict == "SUSPICIOUS"
    assert stressed.aggregate.cost_stress is not None
    assert stressed.aggregate.cost_stress["30"] < 0.0
    assert "cost stress 30x Sharpe" in stressed.reason


def test_cost_stress_high_sharpe_low_relative_impact_not_degraded() -> None:
    # At 2x the strong momentum formula keeps a positive stressed Sharpe: it
    # stays VALID with no cost-stress failure in the reason.
    data, ret = _syn(n_dates=200, n_symbols=4, seed=2, ar=0.95, sigma=0.002)
    report = validate_formulas(
        [MOM],
        {"syn": (data, ret)},
        n_trials=20,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
        backtest_config=WeightDriftConfig(epoch=12),
        cost_stress_multipliers=(2,),
    )
    v = report.formulas[0]
    assert v.verdict == "VALID"
    assert v.aggregate.cost_stress == {
        "2": pytest.approx(0.06698660420005513, abs=1e-6)
    }
    assert "cost stress" not in v.reason


def test_cost_stress_to_dict_fields() -> None:
    data, ret = _syn(n_dates=200, n_symbols=4, seed=1, ar=0.95, sigma=0.002)
    report = validate_formulas(
        [MOM],
        {"syn": (data, ret)},
        n_trials=20,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
        backtest_config=WeightDriftConfig(epoch=12),
        cost_stress_multipliers=(2, 5),
    )
    payload = report.to_dict()
    f = payload["formulas"][0]
    assert "cost_stress" in f  # aggregate flattened into formula payload
    ds = f["datasets"]["syn"]
    assert "cost_stress" in ds and "oos_turnover" in ds
    assert ds["cost_stress"] is not None and "2" in ds["cost_stress"]
    assert "oos_turnover" in ds["folds"][0]
    # JSON round-trip stays stable with the new fields present.
    assert json.loads(report.to_json()) == payload


def test_cost_stress_deterministic() -> None:
    data, ret = _syn(n_dates=120, n_symbols=3, seed=11, ar=0.5)
    kwargs = dict(
        n_trials=10,
        fold_config=FoldConfig(min_train=60, fold_size=15, n_folds=3),
        backtest_config=WeightDriftConfig(epoch=10),
        cost_stress_multipliers=(2, 4),
    )
    r1 = validate_formulas([MOM], {"syn": (data, ret)}, **kwargs)
    r2 = validate_formulas([MOM], {"syn": (data, ret)}, **kwargs)
    assert r1.to_dict() == r2.to_dict()


# --------------------------------------------------------------------------
# Serialization: to_json() parses back to to_dict().
# --------------------------------------------------------------------------
def test_to_dict_json_roundtrip() -> None:
    data, ret = _syn(n_dates=120, n_symbols=3, seed=12, ar=0.5)
    report = validate_formulas(
        [MOM, ANTI],
        {"syn": (data, ret)},
        n_trials=10,
        fold_config=FoldConfig(min_train=60, fold_size=15, n_folds=3),
        backtest_config=WeightDriftConfig(epoch=10),
    )
    payload = report.to_dict()
    back = json.loads(report.to_json())
    assert back == payload
    for f in payload["formulas"]:
        assert f["verdict"] in (
            "VALID",
            "SUSPICIOUS",
            "INVALID",
            "INSUFFICIENT_DATA",
        )
        assert f["mean_ic"] is None or isinstance(f["mean_ic"], float)
        assert isinstance(f["rolling_ic"], list)


# --------------------------------------------------------------------------
# US-201: noise-band calibration — the pure function.
# --------------------------------------------------------------------------
def test_noise_band_pure_function() -> None:
    import math as _math

    # Theoretical anchor: 1/sqrt(n_cross)/sqrt(n_obs).
    assert noise_band(147, 1402) == pytest.approx(
        1.0 / _math.sqrt(147) / _math.sqrt(1402)
    )
    assert noise_band(10, 100) == pytest.approx(0.0316227766)
    # Degenerate inputs -> NaN (the module's "no evidence" convention).
    assert _math.isnan(noise_band(0, 10))
    assert _math.isnan(noise_band(10, 0))
    assert _math.isnan(noise_band(-3, -3))


# --------------------------------------------------------------------------
# US-201: batch verdict — a pure-noise library is flagged.
# --------------------------------------------------------------------------
def test_noise_suspect_pure_noise_library() -> None:
    # 216 synthetic random per-formula OOS ICs (fixed seed) at a
    # noise-band-typical geometry: band = 1/sqrt(10)/sqrt(25) = 0.0632.
    # Random ICs scattered inside the band cannot lift the median above it,
    # so the batch verdict must flag the library.
    from selene_space_factor_research.mining.validation_protocol import (
        AggregateValidation,
        FormulaValidation,
        _batch_noise_suspect,
    )

    rng = np.random.default_rng(2026)
    band = noise_band(10, 25)
    ics = rng.normal(0.0, band / 2.0, 216)
    formulas = [
        FormulaValidation(
            readable=f"noise_{i}",
            tokens=[0],
            canonical_hash=None,
            verdict="INVALID",
            decision="reject",
            reason="synthetic",
            aggregate=AggregateValidation(oos_rank_ic=float(ic), ic_noise_band=band),
        )
        for i, ic in enumerate(ics)
    ]
    assert float(np.median(np.abs(ics))) < band  # the QuantSkills-like shape
    assert _batch_noise_suspect(formulas) is True

    # End-to-end: a real run whose formulas are degenerate-free but whose
    # OOS ICs are noise-small (no AR persistence -> IC ~ 0 while folds exist).
    data, ret = _syn(n_dates=120, n_symbols=3, seed=11, ar=0.0)
    report = validate_formulas(
        [MOM],
        {"syn": (data, ret)},
        n_trials=5,
        fold_config=FoldConfig(min_train=60, fold_size=15, n_folds=3),
        backtest_config=WeightDriftConfig(epoch=10),
    )
    assert isinstance(report.noise_suspect, bool)
    payload = report.to_dict()
    assert "noise_suspect" in payload
    assert "data_snapshot_sha256" in payload
    assert "script_sha256" in payload
    assert "snapshot_params" in payload


# --------------------------------------------------------------------------
# US-201: batch verdict — a library with a real factor is NOT flagged.
# --------------------------------------------------------------------------
def test_noise_suspect_false_with_real_signal() -> None:
    # The strong momentum baseline's OOS IC sits far above its noise band.
    data, ret = _syn(n_dates=200, n_symbols=4, seed=1, ar=0.95, sigma=0.002)
    report = validate_formulas(
        [MOM, ANTI],
        {"syn": (data, ret)},
        n_trials=10,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
        backtest_config=WeightDriftConfig(epoch=12),
    )
    mom = report.formulas[0]
    assert mom.aggregate.oos_rank_ic is not None
    assert mom.aggregate.ic_noise_band is not None
    assert abs(mom.aggregate.oos_rank_ic) > mom.aggregate.ic_noise_band
    assert report.noise_suspect is False


# --------------------------------------------------------------------------
# US-201: ic_noise_band wired next to every reported mean IC.
# --------------------------------------------------------------------------
def test_ic_noise_band_wired_into_metrics() -> None:
    data, ret = _syn(n_dates=200, n_symbols=4, seed=1, ar=0.95, sigma=0.002)
    report = validate_formulas(
        [MOM],
        {"syn": (data, ret)},
        n_trials=10,
        fold_config=FoldConfig(min_train=80, fold_size=24, n_folds=5),
        backtest_config=WeightDriftConfig(epoch=10),
    )
    v = report.formulas[0]
    ds = v.datasets["syn"]
    assert ds.ic_noise_band is not None
    # Fold geometry: 5 folds of 24 OOS dates x 4 symbols.
    assert ds.ic_noise_band == pytest.approx(noise_band(4, 24), rel=1e-9)
    for f in ds.folds:
        assert f.ic_noise_band == pytest.approx(noise_band(4, 24), rel=1e-9)
        assert "ic_noise_band" in f.to_dict()
    # Aggregate band = median of the datasets' bands.
    assert v.aggregate.ic_noise_band == pytest.approx(ds.ic_noise_band)
    assert "ic_noise_band" in v.to_dict()
    assert "ic_noise_band" in ds.to_dict()
    # No OOS window -> no band (consistent with "None means no evidence").
    data0, ret0 = _syn(n_dates=80, n_symbols=2, seed=3)
    empty = validate_formulas(
        [MOM],
        {"syn": (data0, ret0)},
        n_trials=5,
        fold_config=FoldConfig(min_train=80, fold_size=20, n_folds=3),
        backtest_config=WeightDriftConfig(epoch=10),
    ).formulas[0]
    assert empty.aggregate.ic_noise_band is None


# --------------------------------------------------------------------------
# US-201: snapshot binding — deterministic, value-sensitive, self-hashing.
# --------------------------------------------------------------------------
def test_snapshot_binding_deterministic_and_value_sensitive() -> None:
    data, ret = _syn(n_dates=120, n_symbols=3, seed=12, ar=0.5)
    kwargs = dict(
        n_trials=5,
        fold_config=FoldConfig(min_train=60, fold_size=15, n_folds=3),
        backtest_config=WeightDriftConfig(epoch=10),
    )
    r1 = validate_formulas([MOM], {"syn": (data, ret)}, **kwargs)
    r2 = validate_formulas([MOM], {"syn": (data, ret)}, **kwargs)

    # Same data twice -> identical data + script digests.
    assert r1.data_snapshot_sha256 == r2.data_snapshot_sha256
    assert r1.script_sha256 == r2.script_sha256

    # The script digest is the sha256 of validation_protocol.py's own bytes.
    import selene_space_factor_research.mining.validation_protocol as vp_mod

    mod_path = pathlib.Path(vp_mod.__file__).resolve()
    assert r1.script_sha256 == hashlib.sha256(mod_path.read_bytes()).hexdigest()

    # Snapshot params echo the panel geometry.
    sp = r1.snapshot_params["syn"]
    assert sp["n_symbols"] == 3
    assert sp["symbols"] == ["S0", "S1", "S2"]
    assert sp["date_min"] is not None and sp["date_max"] is not None

    # Mutate ONE price -> different digest.
    data2 = {sym: df.copy() for sym, df in data.items()}
    data2["S1"].iloc[10, data2["S1"].columns.get_loc("close")] *= 1.0001
    r3 = validate_formulas([MOM], {"syn": (data2, ret)}, **kwargs)
    assert r3.data_snapshot_sha256 != r1.data_snapshot_sha256

    # Mutate one return -> different digest too.
    ret2 = {sym: s.copy() for sym, s in ret.items()}
    ret2["S0"].iloc[5] = ret2["S0"].iloc[5] + 1e-9
    r4 = validate_formulas([MOM], {"syn": (data, ret2)}, **kwargs)
    assert r4.data_snapshot_sha256 != r1.data_snapshot_sha256

    # digests are proper sha256 hex strings.
    for r in (r1, r3, r4):
        assert len(r.data_snapshot_sha256) == 64
        int(r.data_snapshot_sha256, 16)
