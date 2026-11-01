"""Factor lifecycle — a closed loop from raw factor to health verdict.

AlphaPurify produces factors but has no notion of a factor's *lifecycle*: once
built, a factor is treated as valid forever. This module closes the loop with
a four-state machine — ``ACTIVE → WATCH → DECAYING → RETIRED`` — that each
``cycle()`` runs through the full chain: construction (factor panel in),
cleaning (:class:`PreprocessingPipeline`), weight-drift backtest
(:class:`WeightDriftBacktest`), factor-model attribution
(:func:`~selene_space_alpha.attribution.factor_model.factor_attribution`),
IC monitoring (:func:`~selene_space_alpha.mining.ic_decay.compute_ic_decay` +
:class:`ICPanel`), and a decay verdict that drives the transition.

Decay is judged **temporally**: the mean rank-IC over the most recent
``rolling_window`` bars is compared against a baseline learned on the first
cycle (or supplied at construction). A recent IC that shrinks below
``decay_factor`` of the baseline, or an IC below ``ic_floor``, triggers
``WATCH``/``DECAYING``; persistence escalates to ``RETIRED``. Horizon-decay
diagnostics (half-life, peak IC across forward lags) are still computed and
reported but do NOT drive the verdict — a factor that predicts only short
horizons is a characteristic, not decay (a cross-lag criterion mislabels
naturally short-horizon trend factors as DECAYING). The report also carries
QuantSkills-style validity metrics adapted to single-symbol time series:
ICIR, trailing-quintile long/short spread, and top-quintile turnover. An
optional ``on_alert`` callback fires on every degrading transition — it is
intentionally not bound to any alerting service (no hard dependency on
``pulse``); wiring a grid alert is a caller-side choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

import numpy as np
import pandas as pd

from selene_space_alpha.attribution.factor_model import (
    build_crypto_factor_returns,
    build_factor_exposures,
    factor_attribution,
)
from selene_space_alpha.mining.ic_decay import compute_ic_decay
from selene_space_factor_research.mining.ic_panel import ICPanel
from selene_space_factor_research.mining.weight_drift_backtest import (
    WeightDriftBacktest,
    WeightDriftConfig,
)
from selene_space_factor_research.preprocessing.pipeline import PreprocessingPipeline

__all__ = [
    "CycleReport",
    "FactorLifecycle",
    "FactorMonitor",
    "FactorState",
    "TRANSITIONS",
    "transition",
]


class FactorState(str, Enum):
    """Lifecycle state of a factor."""

    ACTIVE = "ACTIVE"
    WATCH = "WATCH"
    DECAYING = "DECAYING"
    RETIRED = "RETIRED"


# from-state -> verdict -> next state. ``RETIRED`` is terminal.
TRANSITIONS: dict[FactorState, dict[str, FactorState]] = {
    FactorState.ACTIVE: {"healthy": FactorState.ACTIVE, "decay": FactorState.WATCH},
    FactorState.WATCH: {"healthy": FactorState.ACTIVE, "decay": FactorState.DECAYING},
    FactorState.DECAYING: {"healthy": FactorState.WATCH, "decay": FactorState.RETIRED},
    FactorState.RETIRED: {"healthy": FactorState.RETIRED, "decay": FactorState.RETIRED},
}

_ALERT_LEVELS: dict[FactorState, str] = {
    FactorState.WATCH: "warning",
    FactorState.DECAYING: "critical",
    FactorState.RETIRED: "fatal",
}


def transition(state: FactorState, verdict: str) -> FactorState:
    """Next state for ``state`` under ``verdict`` (``"healthy"`` | ``"decay"``)."""
    if verdict not in ("healthy", "decay"):
        raise ValueError(f"verdict must be 'healthy' or 'decay', got {verdict!r}")
    return TRANSITIONS[state][verdict]


@dataclass(frozen=True)
class FactorMonitor:
    """IC health snapshot of one cycle (temporal-decay criterion)."""

    mean_ic: float
    rolling_ic: float
    icir: float
    long_short_spread: float
    turnover_top_quintile: float
    half_life: float | None
    peak_ic: float
    is_decayed: bool
    turnover: float


@dataclass(frozen=True)
class CycleReport:
    """Everything one :meth:`FactorLifecycle.cycle` computed and decided."""

    cycle: int
    state_before: FactorState
    state_after: FactorState
    decision: str
    reason: str
    mean_ic: float
    rolling_ic: float
    icir: float
    long_short_spread: float
    turnover_top_quintile: float
    half_life: float | None
    baseline_ic: float | None
    backtest: dict[str, float]
    attribution: dict[str, float]
    trace_hash: str


def _decide(
    monitor: FactorMonitor,
    baseline_ic: float | None,
    ic_floor: float,
    decay_factor: float,
) -> tuple[str, str]:
    """Verdict + reason from the IC monitor against the temporal baseline.

    Decay = the recent IC has shrunk below ``decay_factor`` of the baseline
    learned on the first cycle, or IC sits below ``ic_floor`` outright.
    """
    ic_shrunk = (
        baseline_ic is not None
        and not np.isnan(monitor.rolling_ic)
        and monitor.rolling_ic < baseline_ic * decay_factor
    )
    weak_ic = (not np.isnan(monitor.rolling_ic) and monitor.rolling_ic < ic_floor) or (
        not np.isnan(monitor.mean_ic) and monitor.mean_ic < ic_floor
    )
    if ic_shrunk and weak_ic:
        return "decay", "recent IC below decay_factor of baseline and below floor"
    if ic_shrunk:
        return (
            "decay",
            f"recent IC {monitor.rolling_ic:.4f} below {decay_factor:.0%} of baseline {baseline_ic:.4f}",
        )
    if weak_ic:
        return "decay", "recent IC below floor"
    return "healthy", "recent IC stable vs baseline"


def _monitor(
    core: pd.DataFrame,
    ret_df: pd.DataFrame,
    cfg: WeightDriftConfig,
    horizon: int,
    rolling_window: int,
    max_lag: int,
) -> FactorMonitor:
    """Temporal IC health + QuantSkills-style validity metrics, one symbol.

    ``core``/``ret_df`` are single- or multi-symbol panels; the health snapshot
    uses every (date, symbol) row via per-date cross-sectional IC when multiple
    symbols exist, else the plain time-series rank IC. All metrics are trailing
    (rank then roll) — no full-sample fit, so no look-ahead.
    """
    ic = ICPanel().compute(
        core,
        ret_df,
        horizon=horizon,
        rolling_window=rolling_window,
        date_col=cfg.trade_date_col,
        symbol_col=cfg.symbol_col,
        factor_col=cfg.factor_col,
        ret_col=cfg.ret_col,
    )
    sym = core[cfg.symbol_col].iloc[0]
    sub_f = core[core[cfg.symbol_col] == sym].sort_values(cfg.trade_date_col)
    sub_r = ret_df[ret_df[cfg.symbol_col] == sym].sort_values(cfg.trade_date_col)
    decay = compute_ic_decay(
        sub_f.set_index(cfg.trade_date_col)[cfg.factor_col],
        sub_r.set_index(cfg.trade_date_col)[cfg.ret_col],
        max_lag=max_lag,
    )
    rolling_ic = float(ic.rolling_ic.iloc[-1]) if len(ic.rolling_ic) else float("nan")
    icir = (
        float(ic.ic_distribution["IR"])
        if ic.ic_distribution["IR"] is not None
        else float("nan")
    )
    spread, top_turnover = _long_short_metrics(
        sub_f.set_index(cfg.trade_date_col)[cfg.factor_col],
        sub_r.set_index(cfg.trade_date_col)[cfg.ret_col],
        rolling_window,
    )
    return FactorMonitor(
        mean_ic=float(ic.ic_distribution["Mean IC"]),
        rolling_ic=rolling_ic,
        icir=icir,
        long_short_spread=spread,
        turnover_top_quintile=top_turnover,
        half_life=decay.half_life,
        peak_ic=decay.peak_ic,
        is_decayed=decay.is_decayed,
        turnover=decay.turnover,
    )


def _long_short_metrics(
    factor: pd.Series,
    ret: pd.Series,
    window: int,
    n_bins: int = 5,
) -> tuple[float, float]:
    """Trailing-quintile long/short spread + top-quintile turnover (time series).

    Adapted from QuantSkills' cross-sectional quintile spread to a single
    symbol: at each bar the factor's trailing ``window``-bar percentile rank
    picks the quintile; the spread is mean(recent returns | top quintile) minus
    mean(recent returns | bottom quintile); turnover is the mean absolute
    top-quintile membership change per bar. All trailing, no look-ahead.
    """
    rank = factor.rolling(window, min_periods=max(2, window // 4)).rank(pct=True)
    top = rank >= 1.0 - 1.0 / n_bins
    bottom = rank <= 1.0 / n_bins
    spread = (
        float(ret[top].mean() - ret[bottom].mean())
        if (top.sum() and bottom.sum())
        else float("nan")
    )
    membership = top.astype(float)
    turnover = float(membership.diff().abs().mean()) if top.sum() > 1 else float("nan")
    return spread, turnover


def _attribution_summary(
    ret_df: pd.DataFrame,
    cfg: WeightDriftConfig,
    price_df: pd.DataFrame | None,
    volume_df: pd.DataFrame | None,
    factor_returns: pd.DataFrame | None,
    portfolio_weights: np.ndarray | None,
) -> dict[str, float]:
    """Factor-model attribution of asset returns; returns scalar summaries."""
    ret_wide = ret_df.pivot(
        index=cfg.trade_date_col, columns=cfg.symbol_col, values=cfg.ret_col
    )
    if factor_returns is None:
        if price_df is not None:
            factor_returns = build_crypto_factor_returns(price_df, volume_df)
        else:
            factor_returns = ret_wide.mean(axis=1).to_frame("market")
    common_idx = ret_wide.index.intersection(factor_returns.index)
    ret_wide = ret_wide.loc[common_idx]
    factor_returns = factor_returns.loc[common_idx]
    weights = (
        portfolio_weights
        if portfolio_weights is not None
        else np.ones(ret_wide.shape[1], dtype=float) / max(ret_wide.shape[1], 1)
    )
    exposures = build_factor_exposures(ret_wide, factor_returns)
    attr = factor_attribution(weights, ret_wide, factor_returns, exposures=exposures)
    factor_mag = float(np.nanmean(np.abs(attr.total_factor_contrib)))
    specific_mag = float(np.nanmean(np.abs(attr.total_specific_contrib)))
    factor_share = factor_mag / max(factor_mag + specific_mag, 1e-12)
    return {
        "factor_share": factor_share,
        "specific_share": 1.0 - factor_share,
        "exposure_r2": float(np.nanmean(exposures.r_squared)),
    }


class FactorLifecycle:
    """Stateful per-factor health loop; call :meth:`cycle` once per period."""

    def __init__(
        self,
        *,
        preprocessing_config: Any = None,
        backtest_config: WeightDriftConfig | None = None,
        baseline_ic: float | None = None,
        ic_floor: float = 0.02,
        decay_factor: float = 0.6,
        horizon: int = 1,
        rolling_window: int = 60,
        max_lag: int = 20,
        on_alert: Callable[[str, str, CycleReport], None] | None = None,
    ) -> None:
        self.preprocessing_config = (
            preprocessing_config if preprocessing_config is not None else []
        )
        self.backtest_config = backtest_config or WeightDriftConfig()
        self.baseline_ic = baseline_ic
        self.ic_floor = ic_floor
        self.decay_factor = decay_factor
        self.horizon = horizon
        self.rolling_window = rolling_window
        self.max_lag = max_lag
        self.on_alert = on_alert
        self.state = FactorState.ACTIVE
        self.history: list[tuple[int, FactorState, FactorState, str]] = []
        self._cycles_run = 0

    @property
    def n_cycles(self) -> int:
        return self._cycles_run

    def cycle(
        self,
        factor_df: pd.DataFrame,
        ret_df: pd.DataFrame,
        *,
        price_df: pd.DataFrame | None = None,
        volume_df: pd.DataFrame | None = None,
        factor_returns: pd.DataFrame | None = None,
        portfolio_weights: np.ndarray | None = None,
    ) -> CycleReport:
        """Run one full cycle and advance the state machine."""
        before = self.state
        self._cycles_run += 1
        cycle_no = self._cycles_run

        cleaned = PreprocessingPipeline.from_config(self.preprocessing_config).run(
            factor_df
        )
        cfg = self.backtest_config
        core = cleaned.df[[cfg.trade_date_col, cfg.symbol_col, cfg.factor_col]]
        backtest = {
            k: float(v)
            for k, v in WeightDriftBacktest().run(core, ret_df, cfg).stats_ls.items()
        }
        attribution = _attribution_summary(
            ret_df, cfg, price_df, volume_df, factor_returns, portfolio_weights
        )
        monitor = _monitor(
            core, ret_df, cfg, self.horizon, self.rolling_window, self.max_lag
        )

        if self.baseline_ic is None and not np.isnan(monitor.rolling_ic):
            self.baseline_ic = monitor.rolling_ic

        decision, reason = _decide(
            monitor, self.baseline_ic, self.ic_floor, self.decay_factor
        )
        after = transition(before, decision)
        self.state = after
        self.history.append((cycle_no, before, after, reason))

        report = CycleReport(
            cycle=cycle_no,
            state_before=before,
            state_after=after,
            decision=decision,
            reason=reason,
            mean_ic=monitor.mean_ic,
            rolling_ic=monitor.rolling_ic,
            icir=monitor.icir,
            long_short_spread=monitor.long_short_spread,
            turnover_top_quintile=monitor.turnover_top_quintile,
            half_life=monitor.half_life,
            baseline_ic=self.baseline_ic,
            backtest=backtest,
            attribution=attribution,
            trace_hash=cleaned.trace_hash,
        )
        if (
            self.on_alert is not None
            and after != before
            and after != FactorState.ACTIVE
        ):
            self.on_alert(
                _ALERT_LEVELS[after],
                f"{before.value} -> {after.value}: {reason} (cycle {cycle_no})",
                report,
            )
        return report
