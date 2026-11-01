# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Fitness scoring for deterministic genetic formula search (pandas, no torch).

Task 2 of the absorption plan: the scoring layer that turns a
formula's per-symbol output into a single comparable scalar. Cross-symbol
combination happens HERE (not in the VM) by assembling a ``date x symbol``
long panel and scoring it with a per-date cross-sectional Rank-IC plus a
weight-drift LS net Sharpe backtest.

Pipeline per formula:

1. **Structure gate** — ``structure_validation.validate`` rejects 恒正感染链
   (positive-infection chain) / malformed RPN formulas with a
   ``valid=False, score=-inf`` sentinel. Never raises.
2. **Panel assembly** — each symbol is executed through ``PandasStackVM`` and
   the per-symbol Series are stacked into ``factor_df = [trade_date, symbol,
   factor]``; forward returns become ``ret_df = [trade_date, symbol,
   fut_ret_1]`` (the exact ``WeightDriftBacktest`` / ``ICPanel`` contract).
3. **Rank-IC** — per-date cross-sectional Spearman on the TRAIN window;
   ``rank_ic`` = mean, ``ic_stability`` = std, ``subperiod_ic_consistency`` =
   fraction of train sub-periods whose mean IC shares the overall IC sign.
4. **LS net Sharpe** — ``WeightDriftBacktest`` on the train window
   (costs built in; ``charge_funding=False`` keeps funding inert).
5. **Degenerate guard** — a factor panel with std < 1e-8 (constant output)
   is a ``valid=False, score=-inf`` sentinel, per the plan's
   ``退化 std<1e-8 → -inf 哨兵`` rule.
6. **Reward + penalties + composite** — the composite blends Rank-IC, LS net
   Sharpe, LS net Sortino, LS net Calmar, an IC-stability bonus, and a
   cross-symbol IC agreement term (each weight-scaled), minus the turnover and
   sub-period consistency penalties, minus a cost-stress penalty (when
   ``cost_stress_multipliers`` is set). The annualized risk-adjusted ratios
   (Sharpe/Sortino/Calmar) are passed through ``_bounded_risk`` (a monotone odd
   map onto (-1, 1)) before weighting so a lucky near-flat factor's extreme
   Sortino/Calmar tail cannot swamp the Rank-IC term. Zeroing the new weights
   reproduces the original two-component composite. Default weights are
   Selene's own calibration — not derived from any upstream defaults.
7. **OOS fields** — ``oos_rank_ic`` / ``oos_ls_net_sharpe`` are computed on the
   out-of-sample window and reserved for the T4 validation protocol; they never
   enter ``score``.

Determinism: no randomness, no in-place mutation of inputs (all frames are
rebuilt). Same tokens + same data + same config -> bit-identical score.

Torch isolation is a HARD constraint: this module imports only pandas, numpy,
scipy, ``selene_space_alpha.vm.vm_pandas`` and the factor-research mining
modules — all verified torch-free. ``test_no_torch_in_search_import.py``
asserts it at import time.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np
import pandas as pd
from scipy import stats

from selene_space_alpha.vm.vm_pandas import PandasStackVM
from selene_space_factor_research.mining.dsl import canonical_hash
from selene_space_factor_research.mining.structure_validation import validate
from selene_space_factor_research.mining.weight_drift_backtest import (
    WeightDriftBacktest,
    WeightDriftConfig,
)

if TYPE_CHECKING:  # pragma: no cover - annotation-only, never evaluated at runtime
    from selene_space_alpha.vocab import FormulaVocab

__all__ = [
    "FitnessProtocol",
    "FormulaFitness",
    "build_factor_panel",
    "score",
    "top_formulas_to_warm_start",
    "warm_start_engine",
]

# --------------------------------------------------------------------------
# Tunable scoring constants — T3's search_config calibration targets.
# --------------------------------------------------------------------------
#: Weight of the train-window Rank-IC in the composite score.
W_IC: float = 1.0
#: Weight of the train-window LS net Sharpe in the composite score.
W_SHARPE: float = 1.0
#: Weight of the train-window LS net Sortino in the composite score.
W_SORTINO: float = 0.5
#: Weight of the train-window LS net Calmar in the composite score.
W_CALMAR: float = 0.5
#: Weight of the IC-stability bonus (lower daily-IC std is rewarded).
W_STABILITY: float = 0.25
#: Weight of the cross-symbol IC sign agreement in the composite score.
W_SYMBOL_AGREEMENT: float = 0.25
#: Weight of the cost-stress penalty in the composite score.
W_COST_STRESS: float = 0.5
#: Reference daily-IC std; ``stability_bonus = 1 - min(std / ref, 1)``.
IC_STD_REF: float = 0.5
#: Scale reference for the bounded risk-adjusted ratios (Sharpe/Sortino/Calmar).
#: Annualized ratios on the train panel are heavy-tailed and can swamp the
#: Rank-IC term (a near-flat factor gets an extreme Sortino/Calmar despite zero
#: predictive IC); ``_bounded_risk`` maps them monotonically onto (-1, 1) so the
#: weights express priority over comparable-scale components.
RISK_RATIO_REF: float = 2.0
#: Average LS turnover above which a turnover penalty accrues (per rebalance).
TURNOVER_THRESHOLD: float = 0.5
#: Factor-panel std below which a formula's output is considered degenerate.
_STD_EPS: float = 1e-8
#: Max cache entries; the oldest half is evicted when exceeded.
_CACHE_SIZE: int = 512


@dataclass
class FormulaFitness:
    """Scorecard for one formula evaluation.

    ``score`` is the main scalar used by the search layer; ``valid=False``
    marks a rejected formula (structure violation or degenerate output) and
    ``score`` is ``-inf`` so it sorts below every real candidate. The
    ``oos_*`` fields are reserved for the T4 validation protocol and are never
    used to compute ``score``.
    """

    rank_ic: float
    ls_net_sharpe: float
    turnover: float
    ic_stability: float
    subperiod_ic_consistency: float
    oos_rank_ic: float | None
    oos_ls_net_sharpe: float | None
    valid: bool
    score: float
    ls_sortino: float = 0.0
    ls_calmar: float = 0.0
    symbol_agreement: float = 0.0
    stability_bonus: float = 0.0


# --------------------------------------------------------------------------
# Small helpers.
# --------------------------------------------------------------------------
def _date_axis(df: pd.DataFrame) -> np.ndarray:
    """Trade-date axis of a per-symbol frame: ``trade_date`` column or index."""
    if "trade_date" in df.columns:
        return pd.to_datetime(df["trade_date"]).to_numpy()
    return pd.to_datetime(pd.Index(df.index)).to_numpy()


def _ret_lookup(sym: str, ret_by_symbol: dict) -> dict[pd.Timestamp, float]:
    """Per-symbol ``{trade_date: fut_ret_1}`` map from a Series or DataFrame."""
    r = ret_by_symbol.get(sym)
    if r is None:
        return {}
    if isinstance(r, pd.Series):
        return {pd.Timestamp(k): float(v) for k, v in r.items()}
    if isinstance(r, pd.DataFrame):
        if "fut_ret_1" in r.columns:
            dates = _date_axis(r)
            return {
                pd.Timestamp(d): float(v) for d, v in zip(dates, r["fut_ret_1"])
            }
    return {}


def _spearman_pair(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rho over the finite intersection (mirrors ``ic_panel``)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan
    return float(stats.spearmanr(x, y)[0])


def _daily_ic(factor_df: pd.DataFrame, ret_df: pd.DataFrame) -> pd.Series:
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


def _subperiod_consistency(
    daily_ic: pd.Series, rank_ic: float, n_subperiods: int
) -> float:
    """Fraction of train sub-periods whose mean IC matches the overall sign.

    Zero overall IC has no meaningful direction -> consistency 0.0 (penalty
    fires). A sub-period whose mean IC is exactly zero does not count as
    agreeing.
    """
    n_subperiods = max(1, int(n_subperiods))
    if len(daily_ic) < 2 or rank_ic == 0.0:
        return 0.0
    overall_sign = 1.0 if rank_ic > 0.0 else -1.0
    chunks = np.array_split(daily_ic.sort_index().to_numpy(), n_subperiods)
    nonempty = [c for c in chunks if len(c)]
    if not nonempty:
        return 0.0
    agreeing = sum(1.0 for c in nonempty if np.sign(float(np.mean(c))) == overall_sign)
    return agreeing / len(nonempty)


def _symbol_agreement(
    train_factor_df: pd.DataFrame,
    train_ret_df: pd.DataFrame,
    rank_ic: float,
) -> float:
    """Fraction of symbols whose time-series IC sign matches the overall IC sign.

    Each symbol's IC is the time-series Spearman of factor vs forward return
    *within* that symbol (a single-symbol cross-sectional IC is always NaN, so
    per-symbol evidence must be time-series — same evidence as
    ``validation_protocol``'s ``_per_symbol_ts_ic``). The same-sign-rate pattern
    mirrors ``validation_protocol.multi_symbol_agreement``. ``rank_ic == 0`` has
    no meaningful direction -> 0.0.
    """
    if rank_ic == 0.0:
        return 0.0
    overall_sign = 1.0 if rank_ic > 0.0 else -1.0
    per_symbol: list[float] = []
    for sym in sorted(train_factor_df["symbol"].unique()):
        sub_f = train_factor_df[train_factor_df["symbol"] == sym]
        sub_r = train_ret_df[train_ret_df["symbol"] == sym]
        m = sub_f.merge(
            sub_r[["trade_date", "symbol", "fut_ret_1"]],
            on=["trade_date", "symbol"],
            how="inner",
        ).dropna(subset=["factor", "fut_ret_1"])
        if len(m) < 2:
            continue
        ic = _spearman_pair(m["factor"].to_numpy(), m["fut_ret_1"].to_numpy())
        if math.isfinite(ic):
            per_symbol.append(ic)
    if not per_symbol:
        return 0.0
    return float(
        np.mean([1.0 if np.sign(v) == overall_sign else 0.0 for v in per_symbol])
    )


def _bounded_risk(x: float) -> float:
    """Monotone odd map ``x / (abs(x) + RISK_RATIO_REF)`` onto (-1, 1).

    Preserves sign and ordering while capping the tail of an annualized
    risk-adjusted ratio, so the composite cannot be dominated by one lucky
    near-flat factor's extreme Sharpe/Sortino/Calmar.
    """
    return x / (abs(x) + RISK_RATIO_REF)


def _sentinel() -> FormulaFitness:
    """Rejected-formula sentinel: ``valid=False``, ``score=-inf``."""
    return FormulaFitness(
        rank_ic=0.0,
        ls_net_sharpe=0.0,
        turnover=0.0,
        ic_stability=0.0,
        subperiod_ic_consistency=0.0,
        oos_rank_ic=None,
        oos_ls_net_sharpe=None,
        valid=False,
        score=-float("inf"),
        ls_sortino=0.0,
        ls_calmar=0.0,
        symbol_agreement=0.0,
        stability_bonus=0.0,
    )


# --------------------------------------------------------------------------
# Public API.
# --------------------------------------------------------------------------
def build_factor_panel(
    formula_tokens: list[int],
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Execute ``formula_tokens`` per symbol and stack into long panels.

    Args:
        formula_tokens: RPN token sequence (``FormulaVocab`` ids).
        data_by_symbol: ``{symbol: DataFrame}`` with OHLCV columns and either a
            ``trade_date`` column or a DatetimeIndex.
        ret_by_symbol: ``{symbol: Series}`` (DatetimeIndex) or
            ``{symbol: DataFrame}`` with a ``fut_ret_1`` column.

    Returns:
        ``(factor_df, ret_df)`` with the shared panel contract —
        ``[trade_date, symbol, factor]`` and ``[trade_date, symbol, fut_ret_1]``.
        A symbol whose VM execution fails (``None``) contributes no rows; the
        backtest drops NaN factors downstream.
    """
    vm = PandasStackVM()
    factor_rows: list[tuple] = []
    ret_rows: list[tuple] = []
    for sym, data in data_by_symbol.items():
        dates = _date_axis(data)
        series = vm.execute(formula_tokens, data)
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


def score(
    formula_tokens: list[int],
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict[str, pd.DataFrame],
    *,
    train_frac: float = 0.7,
    turnover_penalty_weight: float = 1.0,
    subperiod_consistency_weight: float = 1.0,
    min_subperiod_consistency: float = 0.5,
    cache: dict | None = None,
    n_subperiods: int = 4,
    w_ic: float = W_IC,
    w_sharpe: float = W_SHARPE,
    ic_std_ref: float = IC_STD_REF,
    sortino_weight: float = W_SORTINO,
    calmar_weight: float = W_CALMAR,
    stability_weight: float = W_STABILITY,
    symbol_agreement_weight: float = W_SYMBOL_AGREEMENT,
    cost_stress_multipliers: tuple[float, ...] = (),
    cost_stress_weight: float = W_COST_STRESS,
) -> FormulaFitness:
    """Score a formula on a multi-symbol panel.

    Args:
        formula_tokens: RPN token sequence (``FormulaVocab`` ids).
        data_by_symbol: ``{symbol: DataFrame}`` — see :func:`build_factor_panel`.
        ret_by_symbol: ``{symbol: Series | DataFrame}`` — see
            :func:`build_factor_panel`.
        train_frac: Fraction of dates (time-ordered) used as the train window;
            the remainder is OOS and only fills the ``oos_*`` fields.
        turnover_penalty_weight: Scales ``max(0, turnover - TURNOVER_THRESHOLD)``.
        subperiod_consistency_weight: Scales the sub-period consistency penalty,
            which fires only when ``subperiod_ic_consistency <
            min_subperiod_consistency``.
        min_subperiod_consistency: Consistency floor; below it the penalty is
            ``weight * (floor - consistency)``.
        cache: Optional ``{canonical_hash -> FormulaFitness}`` dict. When given,
            results are written back (bounded by ``_CACHE_SIZE``, oldest half
            evicted). The caller must scope the dict to one (data, config)
            pair — the key is formula-only.
        n_subperiods: Number of train sub-periods for consistency.
        w_ic: Weight of the train Rank-IC in the composite.
        w_sharpe: Weight of the train LS net Sharpe in the composite.
        ic_std_ref: Reference daily-IC std for the stability bonus
            (``1 - min(ic_stability / ic_std_ref, 1)``).
        sortino_weight: Weight of the train LS net Sortino.
        calmar_weight: Weight of the train LS net Calmar.
        stability_weight: Weight of the stability bonus.
        symbol_agreement_weight: Weight of the cross-symbol IC agreement.
        cost_stress_multipliers: Fee/slippage multipliers for cost-stress
            re-runs. Empty disables the cost-stress penalty entirely (the
            composite is then bit-identical to no-cost-stress scoring).
        cost_stress_weight: Weight of the cost-stress penalty.

    Returns:
        A :class:`FormulaFitness`. Rejected formulas (structure violation,
        degenerate constant output, unevaluable IC/backtest) return
        ``valid=False, score=-inf`` — never raised.
    """
    key: str | None = None
    if cache is not None:
        try:
            key = canonical_hash(formula_tokens)
        except ValueError:
            key = None
        if key is not None and key in cache:
            return cache[key]

    if not validate(formula_tokens).valid:
        return _sentinel()

    factor_df, ret_df = build_factor_panel(formula_tokens, data_by_symbol, ret_by_symbol)
    if factor_df.empty:
        return _sentinel()
    factor_std = float(factor_df["factor"].std())
    if not math.isfinite(factor_std) or factor_std < _STD_EPS:
        return _sentinel()

    # Train / OOS split by date (time-ordered).
    all_dates = np.sort(pd.to_datetime(factor_df["trade_date"].unique()))
    n_train = max(1, int(round(len(all_dates) * train_frac)))
    train_dates = all_dates[:n_train]
    oos_dates = all_dates[n_train:]
    factor_dates = pd.to_datetime(factor_df["trade_date"]).to_numpy()
    ret_dates = pd.to_datetime(ret_df["trade_date"]).to_numpy()
    train_factor_df = factor_df[np.isin(factor_dates, train_dates)]
    train_ret_df = ret_df[np.isin(ret_dates, train_dates)]

    # Train-window Rank-IC.
    train_ic = _daily_ic(train_factor_df, train_ret_df)
    if train_ic.empty:
        return _sentinel()
    rank_ic = float(train_ic.mean())
    if not math.isfinite(rank_ic):
        return _sentinel()
    ic_stability = float(train_ic.std(ddof=1)) if len(train_ic) >= 2 else 0.0
    subperiod_ic_consistency = _subperiod_consistency(train_ic, rank_ic, n_subperiods)

    # Train-window LS net Sharpe (costs built in; funding inert by default).
    try:
        wd_result = WeightDriftBacktest().run(train_factor_df, train_ret_df)
    except ValueError:
        return _sentinel()
    ls_net_sharpe = float(wd_result.stats_ls["Ann. Sharpe"])
    if not math.isfinite(ls_net_sharpe):
        return _sentinel()
    turnover = float(wd_result.avg_turnover_ls)

    # Multi-component reward (WS-A): Sortino / Calmar / IC-stability bonus /
    # cross-symbol agreement extend the old two-component composite.
    ls_sortino = float(wd_result.stats_ls.get("Ann. Sortino", math.nan))
    if not math.isfinite(ls_sortino):
        return _sentinel()
    ls_calmar = float(wd_result.stats_ls.get("Ann. Calmar", math.nan))
    if not math.isfinite(ls_calmar):
        return _sentinel()
    if ic_std_ref > 0.0:
        stability_bonus = 1.0 - min(ic_stability / ic_std_ref, 1.0)
    else:
        stability_bonus = 0.0
    symbol_agreement = _symbol_agreement(train_factor_df, train_ret_df, rank_ic)

    # Cost stress: re-run the train backtest under scaled fee/slippage and
    # penalize by how much the net Sharpe degrades vs the 1x baseline.
    cost_penalty = 0.0
    if cost_stress_multipliers:
        base_cfg = WeightDriftConfig()
        stress_sharpes: list[float] = []
        for m in cost_stress_multipliers:
            stress_cfg = WeightDriftConfig(
                fee_rate=base_cfg.fee_rate * m,
                slippage_rate=base_cfg.slippage_rate * m,
            )
            try:
                stress_wd = WeightDriftBacktest().run(
                    train_factor_df, train_ret_df, config=stress_cfg
                )
            except ValueError:
                continue
            stress_sharpe = float(stress_wd.stats_ls["Ann. Sharpe"])
            if math.isfinite(stress_sharpe):
                stress_sharpes.append(stress_sharpe)
        if stress_sharpes:
            cost_penalty = float(
                np.mean([max(0.0, ls_net_sharpe - s) for s in stress_sharpes])
            )

    # Penalties (only for non-sentinel formulas).
    turnover_penalty = turnover_penalty_weight * max(
        0.0, turnover - TURNOVER_THRESHOLD
    )
    if subperiod_ic_consistency < min_subperiod_consistency:
        consistency_penalty = subperiod_consistency_weight * (
            min_subperiod_consistency - subperiod_ic_consistency
        )
    else:
        consistency_penalty = 0.0
    penalty = turnover_penalty + consistency_penalty
    composite = (
        rank_ic * w_ic
        + _bounded_risk(ls_net_sharpe) * w_sharpe
        + _bounded_risk(ls_sortino) * sortino_weight
        + _bounded_risk(ls_calmar) * calmar_weight
        + stability_bonus * stability_weight
        + symbol_agreement * symbol_agreement_weight
        - penalty
        - cost_stress_weight * cost_penalty
    )

    # OOS fields — reserved for T4; never used in ``score``.
    oos_rank_ic: float | None = None
    oos_ls_net_sharpe: float | None = None
    if len(oos_dates) >= 2:
        oos_factor_df = factor_df[np.isin(factor_dates, oos_dates)]
        oos_ret_df = ret_df[np.isin(ret_dates, oos_dates)]
        oos_ic = _daily_ic(oos_factor_df, oos_ret_df)
        if not oos_ic.empty:
            oos_rank_ic = float(oos_ic.mean())
        try:  # best-effort: a thin OOS window may legitimately fail the backtest
            oos_wd = WeightDriftBacktest().run(oos_factor_df, oos_ret_df)
            oos_sharpe = float(oos_wd.stats_ls["Ann. Sharpe"])
            if math.isfinite(oos_sharpe):
                oos_ls_net_sharpe = oos_sharpe
        except Exception:
            oos_ls_net_sharpe = None

    fitness = FormulaFitness(
        rank_ic=rank_ic,
        ls_net_sharpe=ls_net_sharpe,
        turnover=turnover,
        ic_stability=ic_stability,
        subperiod_ic_consistency=subperiod_ic_consistency,
        oos_rank_ic=oos_rank_ic,
        oos_ls_net_sharpe=oos_ls_net_sharpe,
        valid=True,
        score=composite,
        ls_sortino=ls_sortino,
        ls_calmar=ls_calmar,
        symbol_agreement=symbol_agreement,
        stability_bonus=stability_bonus,
    )
    if cache is not None and key is not None:
        cache[key] = fitness
        if len(cache) > _CACHE_SIZE:
            keys = list(cache.keys())
            for k in keys[: len(keys) // 2]:
                del cache[k]
    return fitness


# --------------------------------------------------------------------------
# RL interface (US-05 / T5): FitnessProtocol + deterministic warm-start bridge.
# --------------------------------------------------------------------------
@runtime_checkable
class FitnessProtocol(Protocol):
    """Contract for a formula-fitness evaluator usable by the RL engine.

    The RL engine (``RealFitnessAlphaEngine``) scores a candidate via
    ``evaluate(formula_tokens, vocab)``. ``RealBacktestFitness.evaluate``
    (torch side) implements exactly this signature; the deterministic
    :func:`score` does not (its signature is the full multi-symbol panel
    contract), so this protocol names the RL-facing boundary. Torch-free by
    construction: it is only a typing/structural contract.
    """

    def evaluate(self, formula_tokens: list[int], vocab: FormulaVocab) -> float: ...


def top_formulas_to_warm_start(
    path: str | Path,
    n: int = 10,
) -> list[list[int]]:
    """Serialize the deterministic top-N valid formulas into RL warm-start tokens.

    Reads the T3 runner's ``top_formulas.json`` and returns the token-id lists
    of the top ``n`` *scored-valid* records (``valid=True`` with a finite
    ``score``), in rank order. The output shape — ``list[list[int]]`` — is
    exactly what ``RealFitnessAlphaEngine.warm_start_formulas`` consumes (the RL
    engine injects them into the first ``warm_start_steps=10`` REINFORCE
    batches, so the policy starts from proven alpha sources). ``-inf`` sentinel
    records (a search that produced no usable formula) are skipped, so a failed
    search yields ``[]`` rather than garbage tokens.

    Torch isolation is a HARD constraint: this is a pure JSON + list function —
    no torch, no ``real_engine`` import (``test_no_torch_in_search_import``
    stays green).
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"top-formulas file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    records: list = payload.get("top_formulas", [])
    valid_records: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict) or rec.get("valid") is not True:
            continue
        score = rec.get("score")
        if score is None or not isinstance(score, (int, float)) or not math.isfinite(
            float(score)
        ):
            continue
        tokens = rec.get("tokens")
        if not isinstance(tokens, (list, tuple)) or not tokens:
            continue
        valid_records.append(rec)
    return [
        [int(t) for t in rec["tokens"]]
        for rec in valid_records[: max(0, int(n))]
    ]


def warm_start_engine(
    engine_cls: type,
    *,
    vocab: object,
    fitness: FitnessProtocol,
    top_formulas_path: str | Path,
    n: int = 10,
    **engine_kwargs: object,
) -> object:
    """Construct an RL engine wired to warm-start from the deterministic top-N.

    ``engine_cls`` is a ``RealFitnessAlphaEngine``-compatible class, passed in so
    this torch-free module never imports ``real_engine``. The deterministic
    top-N valid formulas are serialized via :func:`top_formulas_to_warm_start`
    and passed as ``warm_start_formulas`` — the RL policy then starts from
    proven alpha sources instead of cold random formulas.
    """
    warm_start = top_formulas_to_warm_start(top_formulas_path, n=n)
    return engine_cls(
        vocab=vocab,
        fitness=fitness,
        warm_start_formulas=warm_start,
        **engine_kwargs,
    )
