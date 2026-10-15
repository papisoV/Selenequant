# Cleanroom implementation: design intent derived from prior art; independent expression.
"""T4 validation protocol — walk-forward train/OOS robustness audit for top-N formulas.

US-04 of the absorption plan: take the top-N formulas produced by
:mod:`genetic_search` and decide, per formula, whether the OOS evidence
supports ``VALID`` / ``SUSPICIOUS`` / ``INVALID`` (or ``INSUFFICIENT_DATA``).
The protocol is deliberately independent of the search's ``score``: it never
reuses ``score``'s train/OOS split or its composite scalar, and it performs its
own expanding-window walk-forward over **OOS-only** windows.

Design:

- **Multi-dataset contract** — ``datasets: dict[dataset_key, (data_by_symbol,
  ret_by_symbol)]``. Cross-market / cross-period consistency is derived by
  pooling per-(dataset, fold) OOS metrics; each dataset may carry one or more
  symbols (a multi-symbol dataset yields a cross-sectional Rank-IC, a
  single-symbol dataset contributes per-symbol time-series ICs and LS returns
  but no cross-sectional IC).
- **Expanding-window walk-forward** — ``FoldConfig(min_train, fold_size,
  n_folds)``. Fold ``k`` trains on ``all_dates[:min_train + k*fold_size]`` and
  scores OOS on ``all_dates[min_train + k*fold_size : ... + fold_size]``. The
  number of folds is auto-reduced when the data is too short
  (``effective_folds``), so a ~150-bar slice still yields several folds.
- **OOS time-misalignment is a hard constraint** — every ``oos_*`` metric is
  computed exclusively on the fold's OOS window; train-window scores never
  leak into them. Fold k's ``train_*`` metrics come only from the expanding
  train window.
- **Metrics per fold** — train Rank-IC / LS net Sharpe / turnover; OOS
  Rank-IC (per-date cross-sectional Spearman, same ``groupby(...).apply(
  include_groups=False)`` shape as ``fitness._daily_ic`` but implemented
  independently here); per-symbol OOS time-series IC (the multi-symbol
  same-direction evidence); and the OOS long-short net return series (the DSR
  evidence).
- **Overfit audit** — per formula, ``build_overfit_report`` is called on the
  concatenated OOS LS net returns (all datasets, all folds, chronological).
  DSR (deflated Sharpe, ``deflated_sharpe_ratio``) is the decision gate; PBO is
  ``None`` because the search does not persist a full trial return matrix (an
  optional ``trials_matrix`` hook is provided for future runs that do). The
  import is lazy so the top-level search chain stays torch-free and
  dependency-light (``test_no_torch_in_search_import`` asserts it).
- **Decision table** (user-confirmed §10 thresholds):
  ``VALID`` = OOS Rank-IC > 0.02 ∧ subinterval same-sign rate ≥ 70% ∧
  ≥ 2/3 of symbols agree ∧ DSR > 0.95. ``SUSPICIOUS`` = OOS passes but one of
  the consistency/audit gates fails. ``INVALID`` = OOS IC ≤ 0.02 (weak or
  negative) or a degenerate/constant output. ``INSUFFICIENT_DATA`` = fewer
  than two finite OOS folds (e.g. the data has no OOS window at all).
- **Determinism** — no randomness, no input mutation, no RNG. Same tokens +
  same datasets + same config ⇒ identical report.
- **Noise-band calibration (US-201)** — every reported mean IC carries
  ``ic_noise_band`` = ``1/sqrt(n_cross)/sqrt(n_obs)``, the theoretical standard
  error of a zero-signal cross-sectional Rank-IC (:func:`noise_band`). A report
  whose median |IC| across formulas sits below its median band sets
  ``noise_suspect`` — the whole library is then statistically
  indistinguishable from noise (no per-formula verdict changes).
- **Snapshot binding** — the report records ``data_snapshot_sha256`` (canonical
  byte digest of the input panels), ``script_sha256`` (this module's own
  source bytes) and ``snapshot_params`` (per-dataset symbol count / date
  range), so a persisted verdict is traceable to the exact data + code that
  produced it.
- **Efficiency** — feature maps are computed once per dataset
  (``PandasStackVM._compute_features``) and reused across every formula, so
  validating N formulas costs one VM feature pass per dataset instead of N.

Torch isolation is a HARD constraint: this module imports only pandas, numpy,
scipy, ``selene_space_alpha.vm.vm_pandas`` and the factor-research mining
modules — no torch, no ``real_engine``, no ``factor_lifecycle``.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from selene_space_alpha.vm.vm_pandas import PandasStackVM
from selene_space_factor_research.mining.dsl import canonical_hash, render
from selene_space_factor_research.mining.structure_validation import validate
from selene_space_factor_research.mining.weight_drift_backtest import (
    WeightDriftBacktest,
    WeightDriftConfig,
)

__all__ = [
    "AggregateValidation",
    "DatasetValidation",
    "FoldConfig",
    "FoldMetrics",
    "FormulaValidation",
    "SymbolSolo",
    "ValidationReport",
    "noise_band",
    "validate_formula",
    "validate_formulas",
]

# ---------------------------------------------------------------------------
# Decision thresholds (§10, user-confirmed).
# ---------------------------------------------------------------------------
#: Minimum pooled OOS Rank-IC for a formula to be OOS-acceptable.
OOS_IC_MIN: float = 0.02
#: Minimum fraction of OOS sub-intervals (folds) sharing the pooled sign.
SUBINTERVAL_AGREEMENT_MIN: float = 0.70
#: Minimum fraction of per-symbol OOS ICs sharing the pooled sign.
MULTI_SYMBOL_AGREEMENT_MIN: float = 2.0 / 3.0
#: Deflated Sharpe Ratio gate for the overfit audit.
DSR_THRESHOLD: float = 0.95
#: Factor-panel std below which a formula's output is degenerate (mirrors
#: ``fitness._STD_EPS``).
_STD_EPS: float = 1e-8
#: Per-side incremental cost rate (fraction of notional, per unit of turnover)
#: used to re-price the OOS net returns under extra cost stress. ``ret_net_ls``
#: already embeds the 1x cost; ``m-1`` recovers the incremental ``(m-1)x``
#: penalty, so ``m=1`` is a no-op. Independent derivation (design intent from
#: the upstream reference's 2x-cost SUSPICIOUS rule; see factor-research
#: PROVENANCE).
COST_RATE: float = 0.0003

# --- Solo (per-symbol) decomposition thresholds ---
# Selene-derived gates (NOT upstream values): they are independent absolute
# diagnostics for a single-name 100%-notional long-only backtest (n_bins=1),
# deliberately separate from the pooled cross-sectional decision table.
#: Minimum net-of-cost annualized return for a symbol's solo leg to be
#: financially meaningful (same "lowest acceptable signal" order as pooled
#: ``OOS_IC_MIN``).
SOLO_ANN_RET_MIN: float = 0.02
#: Minimum annualized Sharpe for a solo leg to be "valid evidence": the
#: conservative lower edge of the market-neutral target band [0.5, 1.0].
SOLO_SHARPE_MIN: float = 0.50
#: Maximum acceptable single-name drawdown — below forced-liquidation
#: proximity, amortizable within the annual-return budget.
SOLO_MDD_MAX: float = 0.10

#: Verdict -> lifecycle-style action (aligns with ``CycleReport.decision``).
_ACTION_BY_VERDICT: dict[str, str] = {
    "VALID": "accept",
    "SUSPICIOUS": "hold",
    "INVALID": "reject",
    "INSUFFICIENT_DATA": "defer",
}


# ---------------------------------------------------------------------------
# Fold geometry.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FoldConfig:
    """Expanding-window walk-forward geometry.

    Fold ``k`` trains on ``[0, min_train + k*fold_size)`` and evaluates OOS on
    ``[min_train + k*fold_size, min_train + (k+1)*fold_size)``.
    """

    min_train: int = 70
    fold_size: int = 20
    n_folds: int = 5

    def effective_folds(self, n_dates: int) -> int:
        """Number of complete folds that fit ``n_dates`` (auto-reduced)."""
        if n_dates <= self.min_train:
            return 0
        return max(
            0, min(int(self.n_folds), (n_dates - self.min_train) // self.fold_size)
        )

    def boundaries(self, n_dates: int) -> list[tuple[int, int, int, int]]:
        """``(train_start, train_end, test_start, test_end)`` per fold."""
        out: list[tuple[int, int, int, int]] = []
        for k in range(self.effective_folds(n_dates)):
            train_end = self.min_train + k * self.fold_size
            test_start = train_end
            test_end = test_start + self.fold_size
            out.append((0, train_end, test_start, test_end))
        return out


# ---------------------------------------------------------------------------
# Per-fold / per-dataset / per-formula result containers.
# ---------------------------------------------------------------------------
@dataclass
class FoldMetrics:
    """All metrics one fold computed, on its own train and OOS windows."""

    fold_index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    train_rank_ic: float | None = None
    train_ls_sharpe: float | None = None
    train_turnover: float | None = None
    oos_rank_ic: float | None = None
    oos_ls_sharpe: float | None = None
    oos_turnover: float | None = None
    per_symbol_oos_ic: dict[str, float] = field(default_factory=dict)
    oos_n_obs: int = 0
    #: Noise band qualifying ``oos_rank_ic`` (US-201): ``noise_band(n_symbols,
    #: n_test_dates)`` — the |IC| a zero-signal factor plausibly reaches on
    #: this fold's OOS geometry. ``None`` when the fold has no OOS window.
    ic_noise_band: float | None = None

    def to_dict(self) -> dict:
        return {
            "fold_index": self.fold_index,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "train_rank_ic": _jsonable(self.train_rank_ic),
            "train_ls_sharpe": _jsonable(self.train_ls_sharpe),
            "train_turnover": _jsonable(self.train_turnover),
            "oos_rank_ic": _jsonable(self.oos_rank_ic),
            "oos_ls_sharpe": _jsonable(self.oos_ls_sharpe),
            "oos_turnover": _jsonable(self.oos_turnover),
            "per_symbol_oos_ic": {
                str(k): _jsonable(v) for k, v in sorted(self.per_symbol_oos_ic.items())
            },
            "oos_n_obs": int(self.oos_n_obs),
            "ic_noise_band": _jsonable(self.ic_noise_band),
        }


@dataclass
class SymbolSolo:
    """Per-symbol independent long-only backtest result (a decomposition).

    A single-symbol panel assigns every observation to quantile 1, so the
    long-short leg (``stats_ls``) would describe *shorting* the symbol at half
    notional — not its independent long performance. The solo decomposition
    therefore runs a ``n_bins=1`` long-only WeightDrift backtest and reads the
    ``stats_l`` (long-leg) net-of-cost metrics ("Ann. Return" / "Ann. Sharpe"
    / "Max Drawdown").

    Verdict semantics — solo is an independent ABSOLUTE diagnostic, not a
    pooled-relative one: a single name at 100% notional is not
    same-distribution comparable to the pooled equal-weighted bucket, and a
    single-name Sharpe is naturally lower. Solo can reveal weak symbols that
    pooled panels bury. A missing symbol (no OOS data) is simply absent from
    the ``solo`` dict — absence is NOT ``PASS``, it means no evidence.

    Propagation (user decision item B, 2026-09-10): a solo ``SUSPICIOUS``
    downgrades the formula-level verdict to SUSPICIOUS/hold — a single-name
    disaster must not ride a healthy pooled panel to accept.
    ``INSUFFICIENT_DATA`` stays record-only (no evidence, not an accusation);
    a solo ``INVALID`` also stays record-only because the pooled gates judge
    the formula on its own terms.
    """

    symbol: str
    ann_ret: float | None
    sharpe: float | None
    max_drawdown: float | None
    verdict: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "symbol": str(self.symbol),
            "ann_ret": _jsonable(self.ann_ret),
            "sharpe": _jsonable(self.sharpe),
            "max_drawdown": _jsonable(self.max_drawdown),
            "verdict": self.verdict,
            "reason": self.reason,
        }


@dataclass
class DatasetValidation:
    """Per-dataset walk-forward result for one formula."""

    dataset_key: str
    n_dates: int
    n_symbols: int
    effective_folds: int
    folds: list[FoldMetrics] = field(default_factory=list)
    train_rank_ic: float | None = None
    oos_rank_ic: float | None = None
    subinterval_agreement: float | None = None
    multi_symbol_agreement: float | None = None
    oos_ls_sharpe: float | None = None
    oos_turnover: float | None = None
    cost_stress: dict[str, float] | None = None
    n_oos_obs: int = 0
    #: Noise band qualifying ``oos_rank_ic`` (US-201), computed from the
    #: dataset's median fold geometry (median names per cross-section, median
    #: OOS dates per fold). ``None`` when there are no OOS folds.
    ic_noise_band: float | None = None
    overfit: dict | None = None
    solo: dict[str, SymbolSolo] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "dataset_key": str(self.dataset_key),
            "n_dates": int(self.n_dates),
            "n_symbols": int(self.n_symbols),
            "effective_folds": int(self.effective_folds),
            "train_rank_ic": _jsonable(self.train_rank_ic),
            "oos_rank_ic": _jsonable(self.oos_rank_ic),
            "subinterval_agreement": _jsonable(self.subinterval_agreement),
            "multi_symbol_agreement": _jsonable(self.multi_symbol_agreement),
            "oos_ls_sharpe": _jsonable(self.oos_ls_sharpe),
            "oos_turnover": _jsonable(self.oos_turnover),
            "cost_stress": self.cost_stress,
            "n_oos_obs": int(self.n_oos_obs),
            "ic_noise_band": _jsonable(self.ic_noise_band),
            "overfit": self.overfit,
            "solo": {
                str(k): v.to_dict()
                for k, v in sorted(
                    self.solo.items(),
                    key=lambda kv: (
                        1 if kv[1].ann_ret is None else 0,
                        -1.0 if kv[1].ann_ret is None else -float(kv[1].ann_ret),
                        str(kv[0]),
                    ),
                )
            },
            "folds": [f.to_dict() for f in self.folds],
        }


@dataclass
class AggregateValidation:
    """Pooled, cross-dataset decision inputs for one formula."""

    train_rank_ic: float | None = None
    oos_rank_ic: float | None = None
    subinterval_agreement: float | None = None
    multi_symbol_agreement: float | None = None
    dsr: float | None = None
    pbo: float | None = None
    cost_stress: dict[str, float] | None = None
    n_oos_obs: int = 0
    n_oos_folds: int = 0
    n_datasets_with_evidence: int = 0
    #: Noise band qualifying ``oos_rank_ic`` (US-201): median of the
    #: contributing datasets' bands. ``None`` when there is no OOS IC.
    ic_noise_band: float | None = None

    def to_dict(self) -> dict:
        return {
            "train_rank_ic": _jsonable(self.train_rank_ic),
            "oos_rank_ic": _jsonable(self.oos_rank_ic),
            "subinterval_agreement": _jsonable(self.subinterval_agreement),
            "multi_symbol_agreement": _jsonable(self.multi_symbol_agreement),
            "dsr": _jsonable(self.dsr),
            "pbo": _jsonable(self.pbo),
            "cost_stress": self.cost_stress,
            "n_oos_obs": int(self.n_oos_obs),
            "n_oos_folds": int(self.n_oos_folds),
            "n_datasets_with_evidence": int(self.n_datasets_with_evidence),
            "ic_noise_band": _jsonable(self.ic_noise_band),
        }


@dataclass
class FormulaValidation:
    """One formula's verdict + per-fold detail + pooled summary."""

    readable: str
    tokens: list[int]
    canonical_hash: str | None
    verdict: str
    decision: str
    reason: str
    datasets: dict[str, DatasetValidation] = field(default_factory=dict)
    aggregate: AggregateValidation = field(default_factory=AggregateValidation)

    def to_dict(self) -> dict:
        # CycleReport-aligned naming: mean_ic / rolling_ic / decision / reason.
        rolling_ic: list[float | None] = []
        for ds in self.datasets.values():
            rolling_ic.extend(_jsonable(f.oos_rank_ic) for f in ds.folds)
        return {
            "readable": self.readable,
            "tokens": [int(t) for t in self.tokens],
            "canonical_hash": self.canonical_hash,
            "verdict": self.verdict,
            "decision": self.decision,
            "reason": self.reason,
            "mean_ic": _jsonable(self.aggregate.oos_rank_ic),
            "rolling_ic": rolling_ic,
            "train_mean_ic": _jsonable(self.aggregate.train_rank_ic),
            "subinterval_agreement": _jsonable(self.aggregate.subinterval_agreement),
            "multi_symbol_agreement": _jsonable(self.aggregate.multi_symbol_agreement),
            "dsr": _jsonable(self.aggregate.dsr),
            "pbo": _jsonable(self.aggregate.pbo),
            "cost_stress": self.aggregate.cost_stress,
            "n_oos_obs": int(self.aggregate.n_oos_obs),
            "ic_noise_band": _jsonable(self.aggregate.ic_noise_band),
            "datasets": {str(k): v.to_dict() for k, v in sorted(self.datasets.items())},
        }


@dataclass
class ValidationReport:
    """The full T4 output: config echo + per-formula verdicts."""

    config: dict[str, Any] = field(default_factory=dict)
    formulas: list[FormulaValidation] = field(default_factory=list)
    #: US-201 batch verdict — True when the median |OOS IC| across all
    #: formulas sits below the median ``ic_noise_band`` (the library's IC
    #: dispersion is then statistically indistinguishable from noise). Purely
    #: informational: it never flips an individual formula's verdict.
    noise_suspect: bool = False
    #: Snapshot binding — digests of the exact data and code this report was
    #: produced from, plus an echo of the panel geometry.
    data_snapshot_sha256: str | None = None
    script_sha256: str | None = None
    snapshot_params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "generated_at": self.config.get("generated_at"),
            "config": {k: v for k, v in self.config.items() if k != "generated_at"},
            "formulas": [f.to_dict() for f in self.formulas],
            "noise_suspect": bool(self.noise_suspect),
            "data_snapshot_sha256": self.data_snapshot_sha256,
            "script_sha256": self.script_sha256,
            "snapshot_params": self.snapshot_params,
        }

    def to_json(self, indent: int = 2) -> str:
        import json

        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Small numeric/panel helpers (independent implementations; contract mirrors
# ``fitness`` so the panels match ``WeightDriftBacktest`` exactly).
# ---------------------------------------------------------------------------
def noise_band(n_cross: int, n_obs: int) -> float:
    """Theoretical standard error of a zero-signal cross-sectional IC.

    Under cross-sectional randomness (no predictive signal), the per-date
    Rank-IC of an i.i.d. panel is zero-mean with std ~ ``1/sqrt(n_cross)``
    (``n_cross`` = names per cross-section); averaging over ``n_obs``
    independent dates shrinks the mean's std to ``1/sqrt(n_cross)/sqrt(n_obs)``.
    Any reported |IC| comfortably below this band is indistinguishable from
    zero — and a *library* whose median |IC| sits below its median band is
    noise end-to-end (US-201; the motivation case had 216 reported ICs with
    median |0.00326| vs a theoretical band of ~0.005).

    Returns ``nan`` for degenerate inputs (``n_cross <= 0`` or ``n_obs <= 0``)
    — mirroring the module's "None/NaN means no evidence" convention rather
    than raising, so a caller that skips the check gets a metric that compares
    False against every threshold.
    """
    if n_cross <= 0 or n_obs <= 0:
        return float("nan")
    return 1.0 / math.sqrt(float(n_cross)) / math.sqrt(float(n_obs))


def _panel_snapshot_sha256(
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict[str, pd.DataFrame | pd.Series],
) -> str:
    """Deterministic sha256 of one dataset's input panels.

    Canonical form: per symbol (sorted), each frame is serialized column-sorted
    via pandas' hash_pandas_object (index + every column, order-independent w.r.t.
    column storage order but sensitive to every value and index label), then the
    per-symbol digests are folded in with the symbol name. Same data twice ->
    same digest; a single changed price -> a different one.
    """
    h = hashlib.sha256()
    for sym in sorted(data_by_symbol):
        h.update(sym.encode("utf-8"))
        for frame in (data_by_symbol[sym], ret_by_symbol.get(sym)):
            if frame is None:
                continue
            cols = sorted(frame.columns) if isinstance(frame, pd.DataFrame) else None
            obj = frame[cols] if cols is not None else frame
            hv = pd.util.hash_pandas_object(obj, index=True).to_numpy(dtype=np.uint64)
            h.update(hv.tobytes())
    return h.hexdigest()


def _script_sha256() -> str:
    """sha256 of this module's own source bytes (snapshot binding)."""
    path = Path(__file__).resolve()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_params(
    datasets: dict[str, tuple[dict, dict]],
) -> dict[str, dict[str, Any]]:
    """Per-dataset snapshot echo: symbol count, symbols, date range."""
    out: dict[str, dict[str, Any]] = {}
    for key, (data_by_symbol, _) in datasets.items():
        date_min: str | None = None
        date_max: str | None = None
        for df in data_by_symbol.values():
            dates = pd.to_datetime(_date_axis(df))
            if len(dates) == 0:
                continue
            lo, hi = str(dates.min().date()), str(dates.max().date())
            date_min = lo if date_min is None else min(date_min, lo)
            date_max = hi if date_max is None else max(date_max, hi)
        out[str(key)] = {
            "n_symbols": len(data_by_symbol),
            "symbols": sorted(data_by_symbol.keys()),
            "date_min": date_min,
            "date_max": date_max,
        }
    return out


def _jsonable(x: Any) -> Any:
    """Convert a metric value to a JSON-safe scalar (NaN/±inf -> None)."""
    if isinstance(x, (np.floating, float)):
        v = float(x)
        return None if not math.isfinite(v) else v
    if isinstance(x, (np.integer, int)):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _annualized_sharpe(returns: np.ndarray, periods_per_year: int) -> float | None:
    """Sample Sharpe of a return stream annualized by ``sqrt(periods_per_year)``.

    Uses the sample std (ddof=1, mirroring ``WeightDriftBacktest``'s sharpe
    convention). Returns None when fewer than 2 finite samples or when the
    stream is degenerate (zero std) — such a stream cannot be scored, so
    callers treat None as "no evidence" rather than a failure.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return None
    std = float(np.std(r, ddof=1))
    if std == 0.0 or not math.isfinite(std):
        return None
    return float(np.mean(r)) / (std * math.sqrt(periods_per_year))


def _date_axis(df: pd.DataFrame) -> np.ndarray:
    """Trade-date axis of a per-symbol frame: ``trade_date`` column or index."""
    if "trade_date" in df.columns:
        return np.asarray(pd.to_datetime(df["trade_date"]).to_numpy())
    return np.asarray(pd.to_datetime(pd.Index(df.index)).to_numpy())


def _ret_lookup(sym: str, ret_by_symbol: dict) -> dict[pd.Timestamp, float]:
    """Per-symbol ``{trade_date: fut_ret_1}`` map (Series or DataFrame)."""
    r = ret_by_symbol.get(sym)
    if r is None:
        return {}
    if isinstance(r, pd.Series):
        return {pd.Timestamp(k): float(v) for k, v in r.items()}
    if isinstance(r, pd.DataFrame) and "fut_ret_1" in r.columns:
        dates = _date_axis(r)
        return {pd.Timestamp(d): float(v) for d, v in zip(dates, r["fut_ret_1"])}
    return {}


def _f2e(x: Any) -> float | None:
    """Coerce a value to float, normalizing NaN/inf to None.

    ``WeightDriftBacktest._stats`` can emit NaN (zero/NaN vol -> NaN Sharpe,
    NaN-polluted NAV -> NaN "Ann. Return"), and a NaN metric compares False to
    every ``<``/``>`` threshold — which would let a NaN + small-MDD pair
    mis-verdict PASS. Normalizing at the solo boundary keeps ``None`` as the
    single "no evidence" sentinel.
    """
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(v) else v


def _symbol_solo_verdict(
    ann_ret: float | None,
    sharpe: float | None,
    max_drawdown: float | None,
) -> tuple[str, str]:
    """Absolute solo verdict from the long-leg annualized stats.

    Any ``None``/non-finite input (NaN normalized by :func:`_f2e` here as well,
    so the verdict is NaN-safe even when called directly) is "no evidence" ->
    INSUFFICIENT_DATA. Below the net-of-cost annualized floor -> INVALID.
    Otherwise a failures list: Sharpe below the solo gate or drawdown beyond the
    cap -> SUSPICIOUS (reasons joined); all clear -> PASS. Thresholds are
    Selene-derived (:data:`SOLO_ANN_RET_MIN` etc.), not upstream values.
    """
    ann_ret = _f2e(ann_ret)
    sharpe = _f2e(sharpe)
    max_drawdown = _f2e(max_drawdown)
    if ann_ret is None or sharpe is None or max_drawdown is None:
        return "INSUFFICIENT_DATA", "insufficient solo backtest data"
    if ann_ret < SOLO_ANN_RET_MIN:
        return (
            "INVALID",
            f"annualized return {ann_ret:.4f} below {SOLO_ANN_RET_MIN}",
        )
    failures: list[str] = []
    if sharpe < SOLO_SHARPE_MIN:
        failures.append(f"sharpe {sharpe:.3f} below {SOLO_SHARPE_MIN}")
    # Max Drawdown is stored loss-is-negative (cum_nv/cummax - 1 <= 0), so the
    # cap is a magnitude floor on the negative side.
    if max_drawdown < -SOLO_MDD_MAX:
        failures.append(
            f"max drawdown magnitude {abs(max_drawdown):.3f} above {SOLO_MDD_MAX}"
        )
    if failures:
        return "SUSPICIOUS", "; ".join(failures)
    return "PASS", "annualized return, sharpe and drawdown all acceptable"


def _run_symbol_solo(
    sym_factor: pd.DataFrame,
    sym_ret: pd.DataFrame,
    bt_cfg: WeightDriftConfig,
) -> tuple[float | None, float | None, float | None]:
    """One symbol's independent long-only OOS backtest.

    A single-symbol panel puts every observation in quantile 1, so the
    long-short leg would be the *short* return of the symbol at half notional.
    ``n_bins=1`` + ``stats_l`` is the only honest construction: it holds the
    symbol long at 100% notional, net of its own cost, reusing the same cost
    model / epoch / annualization as the pooled backtest. Any metric is NaN
    normalized to None via :func:`_f2e`; an unsalvageable run returns
    ``(None, None, None)``.
    """
    try:
        solo_cfg = replace(bt_cfg, n_bins=1)
        result = WeightDriftBacktest().run(sym_factor, sym_ret, solo_cfg)
        s = result.stats_l
        if not s:
            return None, None, None
        return (
            _f2e(s.get("Ann. Return")),
            _f2e(s.get("Ann. Sharpe")),
            _f2e(s.get("Max Drawdown")),
        )
    except Exception:
        return None, None, None


def _compute_dataset_solo(
    factor_df: pd.DataFrame,
    ret_df: pd.DataFrame,
    factor_dates: np.ndarray,
    ret_dates: np.ndarray,
    oos_dates: np.ndarray,
    symbols: list[str],
    bt_cfg: WeightDriftConfig,
) -> dict[str, SymbolSolo]:
    """Per-symbol long-only OOS backtests over the dataset's OOS window."""
    out: dict[str, SymbolSolo] = {}
    for sym in symbols:
        sym_factor = factor_df[
            (factor_df["symbol"] == sym) & np.isin(factor_dates, oos_dates)
        ]
        sym_ret = ret_df[(ret_df["symbol"] == sym) & np.isin(ret_dates, oos_dates)]
        if sym_factor.empty or sym_ret.empty:
            # Absent symbol -> no entry (absence is NOT PASS).
            continue
        ann_ret, sharpe, mdd = _run_symbol_solo(sym_factor, sym_ret, bt_cfg)
        verdict, reason = _symbol_solo_verdict(ann_ret, sharpe, mdd)
        out[sym] = SymbolSolo(
            symbol=sym,
            ann_ret=ann_ret,
            sharpe=sharpe,
            max_drawdown=mdd,
            verdict=verdict,
            reason=reason,
        )
    return out


def _spearman_pair(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rho over the finite intersection (mirrors ``fitness``)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan
    return float(stats.spearmanr(x, y)[0])


def _cross_section_ic(factor_df: pd.DataFrame, ret_df: pd.DataFrame) -> pd.Series:
    """Per-date cross-sectional Spearman IC (factor vs forward return)."""
    panel = factor_df.merge(
        ret_df[["trade_date", "symbol", "fut_ret_1"]],
        on=["trade_date", "symbol"],
        how="inner",
    )
    panel = panel.dropna(subset=["factor", "fut_ret_1"])
    if panel.empty:
        return pd.Series(dtype=float)
    daily = (
        panel.groupby("trade_date")
        .apply(
            lambda g: _spearman_pair(g["factor"].to_numpy(), g["fut_ret_1"].to_numpy()),
            include_groups=False,
        )
        .dropna()
    )
    return daily


def _per_symbol_ts_ic(
    factor_df: pd.DataFrame,
    ret_df: pd.DataFrame,
    symbols: list[str],
) -> dict[str, float]:
    """Per-symbol time-series Spearman IC over the panel's dates."""
    out: dict[str, float] = {}
    for sym in symbols:
        sub_f = factor_df[factor_df["symbol"] == sym]
        sub_r = ret_df[ret_df["symbol"] == sym]
        if sub_f.empty or sub_r.empty:
            out[sym] = np.nan
            continue
        m = sub_f.merge(
            sub_r[["trade_date", "symbol", "fut_ret_1"]],
            on=["trade_date", "symbol"],
            how="inner",
        ).dropna(subset=["factor", "fut_ret_1"])
        out[sym] = _spearman_pair(m["factor"].to_numpy(), m["fut_ret_1"].to_numpy())
    return out


def _compute_feature_maps(
    data_by_symbol: dict[str, pd.DataFrame],
    vm: PandasStackVM,
) -> dict[str, dict[int, pd.Series]]:
    """One VM feature pass per symbol, cached for all formulas in a run."""
    return {sym: vm._compute_features(df) for sym, df in data_by_symbol.items()}


def _stack_panels(
    tokens: list[int],
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict,
    vm: PandasStackVM,
    feat_map: dict[str, dict[int, pd.Series]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Execute ``tokens`` per symbol and stack into ``[trade_date, symbol,
    factor]`` / ``[trade_date, symbol, fut_ret_1]`` long panels.

    Faithful mirror of ``fitness.build_factor_panel``'s contract, but reusing a
    pre-computed feature map so N formulas cost one VM feature pass per dataset.
    """
    factor_rows: list[tuple] = []
    ret_rows: list[tuple] = []
    for sym, data in data_by_symbol.items():
        dates = _date_axis(data)
        series = vm.execute(tokens, data, precomputed_features=feat_map[sym])
        if series is None or len(series) != len(dates):
            continue
        values = series.to_numpy()
        ret_map = _ret_lookup(sym, ret_by_symbol)
        for i in range(len(values)):
            date = pd.Timestamp(dates[i])
            factor_rows.append((date, sym, values[i]))
            if date in ret_map:
                ret_rows.append((date, sym, ret_map[date]))
    factor_df = pd.DataFrame(factor_rows, columns=["trade_date", "symbol", "factor"])
    ret_df = pd.DataFrame(ret_rows, columns=["trade_date", "symbol", "fut_ret_1"])
    return factor_df, ret_df


# ---------------------------------------------------------------------------
# Overfit audit (lazy import keeps the top-level search chain torch-free).
# ---------------------------------------------------------------------------
def _overfit_report(
    oos_returns: np.ndarray,
    n_trials: int,
    *,
    periods_per_year: int,
    dsr_threshold: float,
    pbo_threshold: float,
    trials_matrix: np.ndarray | None,
) -> Any:
    """Wrap ``build_overfit_report`` with a lazy import; None when n_obs < 2."""
    from selene_space_backtest.overfit.report import build_overfit_report

    r = np.asarray(oos_returns, dtype=float)
    r = r[~np.isnan(r)]
    if r.size < 2:
        return None
    return build_overfit_report(
        r,
        n_trials=n_trials,
        trials_matrix=trials_matrix,
        periods_per_year=periods_per_year,
        dsr_threshold=dsr_threshold,
        pbo_threshold=pbo_threshold,
    )


# ---------------------------------------------------------------------------
# Core per-dataset fold evaluation.
# ---------------------------------------------------------------------------
def _eval_dataset(
    tokens: list[int],
    dataset_key: str,
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict,
    vm: PandasStackVM,
    feat_map: dict[str, dict[int, pd.Series]],
    fold_config: FoldConfig,
    backtest_config: WeightDriftConfig,
    periods_per_year: int,
    dsr_threshold: float,
    pbo_threshold: float,
    n_trials: int,
    cost_stress_multipliers: tuple[float, ...] = (),
) -> tuple[
    DatasetValidation | None,
    bool,
    float | None,
    float | None,
    list[float],
    dict[int, list[float]],
]:
    """Run the walk-forward on one dataset; return ``(dataset, degenerate_flag,
    pooled_oos_ic, pooled_subinterval_agreement, oos_returns, cost_stress)``.

    ``degenerate_flag`` is True when the formula produces a constant/empty
    panel (a 退化 sentinel, mirroring ``fitness``'s ``std < 1e-8`` rule).
    ``oos_returns`` is the concatenated per-fold OOS LS net return stream,
    which feeds the pooled DSR audit at the formula level. ``cost_stress`` maps
    each enabled multiplier ``m`` to the per-fold-concatenated incremental-cost
    stressed net return stream (``m=1`` yields an empty stream — a no-op).
    """
    factor_df, ret_df = _stack_panels(
        tokens, data_by_symbol, ret_by_symbol, vm, feat_map
    )
    degenerate = (
        factor_df.empty
        or not math.isfinite(float(factor_df["factor"].std()))
        or float(factor_df["factor"].std()) < _STD_EPS
    )
    if degenerate:
        return None, True, None, None, [], {}

    all_dates = np.sort(pd.to_datetime(factor_df["trade_date"].unique()))
    factor_dates = pd.to_datetime(factor_df["trade_date"]).to_numpy()
    ret_dates = pd.to_datetime(ret_df["trade_date"]).to_numpy()
    symbols = list(data_by_symbol.keys())

    folds: list[FoldMetrics] = []
    oos_returns: list[float] = []
    oos_ic_series: list[float] = []
    oos_sharpes: list[float] = []
    train_ic_series: list[float] = []
    per_symbol_pool: list[float] = []
    #: Every fold's OOS dates, accumulated for the per-symbol solo slice down
    #: the fold path (empty when ``effective_folds() == 0`` -> solo skipped).
    oos_dates: list[np.ndarray] = []
    #: m -> concatenated (across this dataset's folds) stressed OOS LS returns.
    stressed_streams: dict[int, list[float]] = (
        {}
        if not cost_stress_multipliers
        else {int(m): [] for m in cost_stress_multipliers}
    )

    for train_start, train_end, test_start, test_end in fold_config.boundaries(
        len(all_dates)
    ):
        train_dates = all_dates[train_start:train_end]
        test_dates = all_dates[test_start:test_end]
        oos_dates.append(test_dates)
        train_factor = factor_df[np.isin(factor_dates, train_dates)]
        train_ret = ret_df[np.isin(ret_dates, train_dates)]
        test_factor = factor_df[np.isin(factor_dates, test_dates)]
        test_ret = ret_df[np.isin(ret_dates, test_dates)]

        fm = FoldMetrics(
            fold_index=len(folds),
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
        )

        # ---- train-window metrics (train dates only) ----
        try:
            train_ic = _cross_section_ic(train_factor, train_ret)
            if not train_ic.empty:
                v = float(train_ic.mean())
                if math.isfinite(v):
                    fm.train_rank_ic = v
                    train_ic_series.append(v)
        except Exception:
            pass
        try:
            wd_train = WeightDriftBacktest().run(
                train_factor, train_ret, backtest_config
            )
            sharpe = float(wd_train.stats_ls["Ann. Sharpe"])
            if math.isfinite(sharpe):
                fm.train_ls_sharpe = sharpe
            fm.train_turnover = float(wd_train.avg_turnover_ls)
        except Exception:
            pass

        # ---- OOS-window metrics (test dates only — hard isolation) ----
        try:
            oos_ic = _cross_section_ic(test_factor, test_ret)
            if not oos_ic.empty:
                v = float(oos_ic.mean())
                if math.isfinite(v):
                    fm.oos_rank_ic = v
                    oos_ic_series.append(v)
        except Exception:
            pass
        try:
            wd_test = WeightDriftBacktest().run(test_factor, test_ret, backtest_config)
            sharpe = float(wd_test.stats_ls["Ann. Sharpe"])
            if math.isfinite(sharpe):
                fm.oos_ls_sharpe = sharpe
                oos_sharpes.append(sharpe)
            fm.oos_turnover = float(wd_test.avg_turnover_ls)
            ls_net = wd_test.df_result["ret_net_ls"].dropna()
            fm.oos_n_obs = int(len(ls_net))
            net = ls_net.to_numpy()
            oos_returns.extend(float(v) for v in net)
            for m, stream in stressed_streams.items():
                # 1x is already embedded in ret_net_ls; m-1 recovers the
                # incremental (m-1)x penalty at this fold's actual turnover.
                stream.extend(
                    float(v) - fm.oos_turnover * COST_RATE * (m - 1) for v in net
                )
        except Exception:
            pass

        # Per-symbol OOS IC — the multi-symbol same-direction evidence.
        sym_ics = _per_symbol_ts_ic(test_factor, test_ret, symbols)
        fm.per_symbol_oos_ic = sym_ics
        per_symbol_pool.extend(float(v) for v in sym_ics.values() if math.isfinite(v))

        # US-201: noise band of this fold's OOS Rank-IC (n_cross = names per
        # cross-section, n_obs = OOS dates in the fold).
        fm.ic_noise_band = _jsonable(noise_band(len(symbols), len(test_dates)))

        folds.append(fm)

    ds = DatasetValidation(
        dataset_key=str(dataset_key),
        n_dates=len(all_dates),
        n_symbols=len(symbols),
        effective_folds=len(folds),
        folds=folds,
        n_oos_obs=len(oos_returns),
    )

    if oos_dates:
        ds.solo = _compute_dataset_solo(
            factor_df,
            ret_df,
            factor_dates,
            ret_dates,
            np.unique(np.concatenate(oos_dates)),
            symbols,
            backtest_config,
        )

    if oos_ic_series:
        ds.oos_rank_ic = float(np.mean(oos_ic_series))
        overall_sign = 1.0 if ds.oos_rank_ic > 0.0 else -1.0
        ds.subinterval_agreement = float(
            np.mean([1.0 if np.sign(v) == overall_sign else 0.0 for v in oos_ic_series])
        )
        if per_symbol_pool:
            ds.multi_symbol_agreement = float(
                np.mean(
                    [
                        1.0 if np.sign(v) == overall_sign else 0.0
                        for v in per_symbol_pool
                    ]
                )
            )
    if train_ic_series:
        ds.train_rank_ic = float(np.mean(train_ic_series))
    if oos_sharpes:
        ds.oos_ls_sharpe = float(np.mean(oos_sharpes))

    # Dataset-level OOS noise band: median fold geometry (n_cross is
    # ``n_symbols``; n_obs is the median OOS dates across folds).
    if folds:
        ds.ic_noise_band = _jsonable(
            noise_band(
                len(symbols),
                int(np.median([max(1, f.test_end - f.test_start) for f in folds])),
            )
        )

    # Per-dataset overfit audit on its own OOS LS net returns.
    rep = _overfit_report(
        np.asarray(oos_returns, dtype=float),
        n_trials,
        periods_per_year=periods_per_year,
        dsr_threshold=dsr_threshold,
        pbo_threshold=pbo_threshold,
        trials_matrix=None,
    )
    if rep is not None:
        ds.overfit = rep.to_dict()

    # Dataset-level OOS turnover (n_obs-weighted across folds) and per-m
    # stressed Sharpe over the concatenated stressed stream.
    turns = [(f.oos_turnover, f.oos_n_obs) for f in folds if f.oos_turnover is not None]
    if turns:
        ds.oos_turnover = sum(t * n for t, n in turns) / sum(n for _, n in turns)
    if cost_stress_multipliers:
        stressed: dict[str, float] = {}
        for m, stream in stressed_streams.items():
            if m <= 1:  # m=1 is a no-op (empty stream); don't emit a vacuous cell.
                continue
            sharpe_m = _annualized_sharpe(
                np.asarray(stream, dtype=float), periods_per_year
            )
            if sharpe_m is not None:
                stressed[str(m)] = sharpe_m
        ds.cost_stress = stressed or None

    return (
        ds,
        degenerate,
        ds.oos_rank_ic,
        ds.subinterval_agreement,
        oos_returns,
        stressed_streams,
    )


# ---------------------------------------------------------------------------
# Decision.
# ---------------------------------------------------------------------------
def _decide(
    *,
    degenerate: bool,
    pooled_oos_ic: float | None,
    subinterval_agreement: float | None,
    multi_symbol_agreement: float | None,
    dsr: float | None,
    n_oos_folds: int,
    cost_stress_sharpes: dict[str, float] | None = None,
    solo_failures: list[str] | None = None,
) -> tuple[str, str]:
    """Return ``(verdict, reason)`` per the §10 decision table.

    ``cost_stress_sharpes`` (``None`` by default — cost stress disabled) maps
    each enabled multiplier ``m`` to its incremental-cost stressed Sharpe. Any
    strictly negative stressed Sharpe downgrades the formula to SUSPICIOUS,
    mirroring the design intent of the upstream reference's 2x-cost SUSPICIOUS rule
    (independent derivation; see factor-research PROVENANCE). A ``None`` value
    (degenerate/empty stressed stream) contributes no failure.

    ``solo_failures`` (``None`` by default) carries one entry per symbol whose
    absolute solo diagnostic came back SUSPICIOUS (e.g. drawdown beyond
    ``SOLO_MDD_MAX``). Any entry downgrades the formula to SUSPICIOUS the same
    way cost stress does — user decision item (B) from the UNI solo-gate
    verification: a single-name disaster must not ride to accept on a healthy
    pooled panel. ``INSUFFICIENT_DATA`` solo entries are *not* failures (no
    evidence is the solo contract's conservative exit, not an accusation).
    """
    if degenerate:
        return "INVALID", "degenerate output (constant/empty factor panel)"
    if n_oos_folds < 2 or pooled_oos_ic is None or not math.isfinite(pooled_oos_ic):
        return (
            "INSUFFICIENT_DATA",
            "fewer than 2 finite OOS folds — no usable OOS evidence",
        )
    if pooled_oos_ic <= OOS_IC_MIN:
        return (
            "INVALID",
            f"OOS Rank-IC {pooled_oos_ic:.4f} <= {OOS_IC_MIN:.2f}",
        )
    failures: list[str] = []
    if (
        subinterval_agreement is None
        or subinterval_agreement < SUBINTERVAL_AGREEMENT_MIN
    ):
        failures.append(
            f"subinterval agreement "
            f"{'n/a' if subinterval_agreement is None else f'{subinterval_agreement:.2f}'} "
            f"< {SUBINTERVAL_AGREEMENT_MIN:.2f}"
        )
    if (
        multi_symbol_agreement is None
        or multi_symbol_agreement < MULTI_SYMBOL_AGREEMENT_MIN
    ):
        failures.append(
            f"multi-symbol agreement "
            f"{'n/a' if multi_symbol_agreement is None else f'{multi_symbol_agreement:.2f}'} "
            f"< {MULTI_SYMBOL_AGREEMENT_MIN:.2f}"
        )
    if dsr is None or not (dsr > DSR_THRESHOLD):
        failures.append(
            f"DSR {'n/a' if dsr is None else f'{dsr:.4f}'} <= {DSR_THRESHOLD}"
        )
    if cost_stress_sharpes:
        for m, s in cost_stress_sharpes.items():
            if s is not None and s < 0.0:
                failures.append(f"cost stress {m}x Sharpe {s:.2f} < 0")
    if solo_failures:
        failures.extend(solo_failures)
    if failures:
        return "SUSPICIOUS", "; ".join(failures)
    return (
        "VALID",
        "OOS Rank-IC, subinterval agreement, multi-symbol agreement and DSR all pass",
    )


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def _normalize_tokens(entry: Any) -> list[int]:
    """Accept a token list, a FormulaEntry-like, or a JSON record dict."""
    if isinstance(entry, (list, tuple)):
        return [int(t) for t in entry]
    if hasattr(entry, "tokens"):
        return [int(t) for t in entry.tokens]
    if isinstance(entry, dict) and "tokens" in entry:
        return [int(t) for t in entry["tokens"]]
    raise TypeError(f"cannot extract formula tokens from {type(entry).__name__}")


def _batch_noise_suspect(validations: list[FormulaValidation]) -> bool:
    """US-201 batch verdict over a finished run's formulas.

    True when the median |OOS IC| across formulas sits below the median
    ``ic_noise_band`` — the library's IC dispersion is then statistically
    indistinguishable from noise (the motivating case: 216 reported ICs with
    median |0.00326| vs a theoretical band of ~0.005). Informational only; it
    never flips per-formula verdicts.
    """
    ic_pool: list[float] = []
    band_pool: list[float] = []
    for f in validations:
        if f.aggregate.oos_rank_ic is not None and math.isfinite(
            f.aggregate.oos_rank_ic
        ):
            ic_pool.append(abs(f.aggregate.oos_rank_ic))
        if f.aggregate.ic_noise_band is not None and math.isfinite(
            f.aggregate.ic_noise_band
        ):
            band_pool.append(f.aggregate.ic_noise_band)
    return bool(ic_pool and band_pool and np.median(ic_pool) < np.median(band_pool))


def validate_formula(
    tokens: list[int],
    datasets: dict[str, tuple[dict, dict]],
    *,
    n_trials: int,
    fold_config: FoldConfig | None = None,
    dsr_threshold: float = DSR_THRESHOLD,
    pbo_threshold: float = 0.50,
    backtest_config: WeightDriftConfig | None = None,
    periods_per_year: int = 252,
    trials_matrix: np.ndarray | None = None,
    cost_stress_multipliers: tuple[float, ...] = (),
) -> FormulaValidation:
    """Validate one formula across all datasets; see :func:`validate_formulas`."""
    report = validate_formulas(
        [tokens],
        datasets,
        n_trials=n_trials,
        fold_config=fold_config,
        dsr_threshold=dsr_threshold,
        pbo_threshold=pbo_threshold,
        backtest_config=backtest_config,
        periods_per_year=periods_per_year,
        trials_matrix=trials_matrix,
        cost_stress_multipliers=cost_stress_multipliers,
    )
    return report.formulas[0]


def validate_formulas(
    formulas: list[Any],
    datasets: dict[str, tuple[dict, dict]],
    *,
    n_trials: int,
    fold_config: FoldConfig | None = None,
    dsr_threshold: float = DSR_THRESHOLD,
    pbo_threshold: float = 0.50,
    backtest_config: WeightDriftConfig | None = None,
    periods_per_year: int = 252,
    trials_matrix: np.ndarray | None = None,
    cost_stress_multipliers: tuple[float, ...] = (),
) -> ValidationReport:
    """Run the T4 walk-forward validation on every formula.

    Args:
        formulas: token lists, FormulaEntry-like objects (``.tokens``), or JSON
            records (``{"tokens": [...]}``).
        datasets: ``{dataset_key: (data_by_symbol, ret_by_symbol)}`` — the
            multi-market / multi-period evidence set.
        n_trials: number of search evaluations (the DSR multiple-testing count).
        fold_config: walk-forward geometry (default ``FoldConfig()``; folds are
            auto-reduced to fit each dataset's length).
        dsr_threshold: DSR pass gate (default 0.95).
        pbo_threshold: PBO fail gate (recorded only; PBO needs a trials matrix).
        backtest_config: ``WeightDriftConfig`` for the per-fold LS backtests
            (default ``WeightDriftConfig()`` — the same default ``fitness``
            uses, so the LS Sharpe is directly comparable).
        periods_per_year: annualization for the overfit report's cosmetic
            annual fields (the DSR probability itself is invariant to it).
        trials_matrix: optional ``(T x N)`` trial return matrix for PBO (the
            search currently does not persist one, so this defaults to None).
        cost_stress_multipliers: extra cost multiples to price against the OOS
            net returns (e.g. ``(2,)`` prices a 2x-cost scenario). Empty
            (default) disables the cost-stress dimension entirely — verdicts
            and reasons are byte-identical to a run without this parameter.

    Returns:
        A :class:`ValidationReport` with one :class:`FormulaValidation` per
        formula. Never raises on a malformed formula — it is recorded INVALID.
    """
    cfg = fold_config or FoldConfig()
    bt_cfg = backtest_config or WeightDriftConfig()

    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")
    if not datasets:
        raise ValueError("datasets must be non-empty")

    vm = PandasStackVM()
    # One feature pass per dataset, reused by every formula (efficiency).
    feat_maps: dict[str, dict[str, dict[int, pd.Series]]] = {}
    for key, (data_by_symbol, _ret) in datasets.items():
        feat_maps[str(key)] = _compute_feature_maps(data_by_symbol, vm)

    validations: list[FormulaValidation] = []
    for entry in formulas:
        try:
            tokens = _normalize_tokens(entry)
            hash_ = canonical_hash(tokens)
            readable = render(tokens)
            valid_rpn = validate(tokens).valid
        except Exception:
            validations.append(
                FormulaValidation(
                    readable="<unparseable>",
                    tokens=[int(t) for t in _normalize_tokens(entry)],
                    canonical_hash=None,
                    verdict="INVALID",
                    decision="reject",
                    reason="unparseable or invalid RPN formula",
                )
            )
            continue

        dataset_vals: dict[str, DatasetValidation] = {}
        degenerate = not valid_rpn
        oos_ic_values: list[float] = []
        train_ic_values: list[float] = []
        all_oos_returns: list[float] = []
        #: m -> concatenated stressed OOS returns across all datasets/folds.
        all_stressed: dict[int, list[float]] = {}
        n_datasets_with_evidence = 0
        n_oos_folds = 0

        for key, (data_by_symbol, ret_by_symbol) in datasets.items():
            ds, ds_degenerate, _ic, _sub, fold_returns, ds_stressed = _eval_dataset(
                tokens,
                key,
                data_by_symbol,
                ret_by_symbol,
                vm,
                feat_maps[str(key)],
                cfg,
                bt_cfg,
                periods_per_year,
                dsr_threshold,
                pbo_threshold,
                n_trials,
                cost_stress_multipliers,
            )
            degenerate = degenerate or ds_degenerate
            if ds is None:
                continue
            dataset_vals[str(key)] = ds
            for m, stream in ds_stressed.items():
                all_stressed.setdefault(m, []).extend(stream)
            if ds.train_rank_ic is not None:
                train_ic_values.append(ds.train_rank_ic)
            for f in ds.folds:
                n_oos_folds += 1
                if f.oos_rank_ic is not None:
                    oos_ic_values.append(f.oos_rank_ic)
            if ds.n_oos_obs > 0:
                n_datasets_with_evidence += 1
            all_oos_returns.extend(fold_returns)

        agg = AggregateValidation()
        if train_ic_values:
            agg.train_rank_ic = float(np.mean(train_ic_values))
        if oos_ic_values:
            agg.oos_rank_ic = float(np.mean(oos_ic_values))
            overall_sign = 1.0 if agg.oos_rank_ic > 0.0 else -1.0
            finite_oos = [v for v in oos_ic_values if math.isfinite(v)]
            if finite_oos:
                agg.subinterval_agreement = float(
                    np.mean(
                        [1.0 if np.sign(v) == overall_sign else 0.0 for v in finite_oos]
                    )
                )
            # Multi-symbol agreement: pool per-symbol OOS ICs across datasets.
            sym_pool: list[float] = []
            for ds in dataset_vals.values():
                for f in ds.folds:
                    sym_pool.extend(
                        float(v)
                        for v in f.per_symbol_oos_ic.values()
                        if math.isfinite(v)
                    )
            if sym_pool:
                agg.multi_symbol_agreement = float(
                    np.mean(
                        [1.0 if np.sign(v) == overall_sign else 0.0 for v in sym_pool]
                    )
                )
        agg.n_oos_folds = n_oos_folds
        agg.n_datasets_with_evidence = n_datasets_with_evidence
        agg.n_oos_obs = len(all_oos_returns)

        # US-201: the aggregate IC's noise band is the median of the
        # contributing datasets' bands (same geometry-pooling spirit as the
        # pooled OOS IC itself).
        ds_bands = [
            ds.ic_noise_band
            for ds in dataset_vals.values()
            if ds.ic_noise_band is not None and math.isfinite(ds.ic_noise_band)
        ]
        if ds_bands:
            agg.ic_noise_band = _jsonable(float(np.median(ds_bands)))

        # Pooled DSR over all OOS LS net returns (all datasets, chronological).
        rep = _overfit_report(
            np.asarray(all_oos_returns, dtype=float),
            n_trials,
            periods_per_year=periods_per_year,
            dsr_threshold=dsr_threshold,
            pbo_threshold=pbo_threshold,
            trials_matrix=trials_matrix,
        )
        if rep is not None:
            agg.dsr = float(rep.deflated_sharpe_ratio)
            agg.pbo = float(rep.pbo) if rep.pbo is not None else None

        # Formula-level cost stress: per m, concatenate every fold's stressed
        # stream across all datasets and annualize. m=1 is a no-op — skip it.
        if cost_stress_multipliers and all_stressed:
            agg.cost_stress = {
                str(m): sharpe
                for m, stream in all_stressed.items()
                if m > 1
                if (
                    sharpe := _annualized_sharpe(
                        np.asarray(stream, dtype=float), periods_per_year
                    )
                )
                is not None
            } or None

        # Solo absolute-diagnostic propagation (user decision item B): a
        # per-symbol SUSPICIOUS solo verdict downgrades the formula level,
        # so a single-name disaster cannot ride a healthy pooled panel to
        # accept. Only SUSPICIOUS propagates — INVALID means the pooled
        # evidence already failed on its own terms, and INSUFFICIENT_DATA
        # is "no evidence", not an accusation.
        solo_failures: list[str] = []
        for ds in dataset_vals.values():
            for sym, solo in ds.solo.items():
                if solo.verdict == "SUSPICIOUS":
                    solo_failures.append(f"solo {sym}: {solo.reason}")

        verdict, reason = _decide(
            degenerate=degenerate,
            pooled_oos_ic=agg.oos_rank_ic,
            subinterval_agreement=agg.subinterval_agreement,
            multi_symbol_agreement=agg.multi_symbol_agreement,
            dsr=agg.dsr,
            n_oos_folds=agg.n_oos_folds,
            cost_stress_sharpes=agg.cost_stress,
            solo_failures=solo_failures,
        )
        validations.append(
            FormulaValidation(
                readable=readable,
                tokens=tokens,
                canonical_hash=hash_,
                verdict=verdict,
                decision=_ACTION_BY_VERDICT.get(verdict, "hold"),
                reason=reason,
                datasets=dataset_vals,
                aggregate=agg,
            )
        )

    # US-201 batch verdict: median |OOS IC| below the median noise band means
    # the whole library's IC dispersion is statistically indistinguishable
    # from noise. Informational only — it never flips per-formula verdicts.
    noise_suspect = _batch_noise_suspect(validations)

    # Snapshot binding: hash the input panels (deterministic, value-sensitive),
    # this module's own bytes, and echo the panel geometry.
    data_snapshot = hashlib.sha256()
    for key in sorted(datasets):
        data_snapshot.update(str(key).encode("utf-8"))
        data_snapshot.update(
            _panel_snapshot_sha256(datasets[key][0], datasets[key][1]).encode("ascii")
        )
    data_snapshot_sha256 = data_snapshot.hexdigest()

    return ValidationReport(
        config={
            "n_trials": int(n_trials),
            "fold_config": {
                "min_train": cfg.min_train,
                "fold_size": cfg.fold_size,
                "n_folds": cfg.n_folds,
            },
            "thresholds": {
                "oos_ic_min": OOS_IC_MIN,
                "subinterval_agreement_min": SUBINTERVAL_AGREEMENT_MIN,
                "multi_symbol_agreement_min": round(MULTI_SYMBOL_AGREEMENT_MIN, 4),
                "dsr_threshold": DSR_THRESHOLD,
            },
            "periods_per_year": int(periods_per_year),
            "datasets": {
                str(k): {
                    "n_symbols": len(v[0]),
                    "symbols": sorted(v[0].keys()),
                }
                for k, v in datasets.items()
            },
        },
        formulas=validations,
        noise_suspect=noise_suspect,
        data_snapshot_sha256=data_snapshot_sha256,
        script_sha256=_script_sha256(),
        snapshot_params=_snapshot_params(datasets),
    )
