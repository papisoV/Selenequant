"""SeleneCryptoCostModel — crypto cost model, drop-in for WeightDriftConfig.

Tests :class:`~selene_space_factor_research.mining.cost_models.SeleneCryptoCostModel`:
no-tax buy/sell fee, optional funding carry via :class:`FundingRateModel`, and the
:meth:`from_rates` bridge to backtest-engine's ``HistoricalFundingModel``.
"""
from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from selene_space_factor_research.mining.cost_models import (
    AShareCostModel,
    NoFunding,
    SeleneCryptoCostModel,
)
from selene_space_factor_research.mining.weight_drift_backtest import (
    WeightDriftBacktest,
    WeightDriftConfig,
)

_MODULE = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "factor-research" / "selene_space_factor_research" / "mining" / "cost_models.py"
)


class _CFG:
    """Minimal :class:`CostConfig`-shaped stand-in."""

    tax_rate: float = 0.02
    fee_rate: float = 0.0003
    slippage_rate: float = 0.001
    tax_rate_direction: str = "sell"


class _RecordingFunding:
    """Fake funding model that records calls and returns a canned amount."""

    def __init__(self, result: float = 0.0) -> None:
        self.result = result
        self.calls: list[tuple[float, str, int]] = []

    def apply_funding(self, notional: float, side: str, timestamp: int) -> float:
        self.calls.append((notional, side, timestamp))
        return self.result


# --------------------------------------------------------------------------
# No-tax transaction fee
# --------------------------------------------------------------------------
def test_buy_sell_fee_is_fee_plus_slippage_both_sides() -> None:
    m = SeleneCryptoCostModel()
    cfg = _CFG()
    expected = (cfg.fee_rate + cfg.slippage_rate,) * 2
    assert m.compute_buy_sell_fee(cfg) == expected


def test_buy_sell_fee_ignores_tax() -> None:
    m = SeleneCryptoCostModel()
    low = _CFG()
    high = _CFG()
    high.tax_rate = 0.99
    assert m.compute_buy_sell_fee(low) == m.compute_buy_sell_fee(high)


def test_buy_sell_fee_independent_of_tax_rate_direction() -> None:
    m = SeleneCryptoCostModel()
    cfg = _CFG()
    cfg.tax_rate_direction = "both"
    both = m.compute_buy_sell_fee(cfg)
    cfg.tax_rate_direction = "buy"
    buy = m.compute_buy_sell_fee(cfg)
    cfg.tax_rate_direction = "sell"
    sell = m.compute_buy_sell_fee(cfg)
    assert both == buy == sell


def test_ashare_direction_still_applies_tax() -> None:
    # Sanity: AShare DOES tax per direction — the crypto model's tax-freedom
    # is an intentional difference, not a shared bug.
    cfg = _CFG()
    buy, sell = AShareCostModel().compute_buy_sell_fee(cfg)
    fee = cfg.fee_rate + cfg.slippage_rate
    assert buy == fee
    assert sell == pytest.approx(fee + cfg.tax_rate)
    assert sell > buy


# --------------------------------------------------------------------------
# Funding carry
# --------------------------------------------------------------------------
def test_default_funding_none_is_zero() -> None:
    m = SeleneCryptoCostModel()
    assert isinstance(m.funding, NoFunding)
    assert m.compute_funding(1000.0, "long", 1) == 0.0


def test_compute_funding_delegates() -> None:
    rec = _RecordingFunding(result=3.5)
    m = SeleneCryptoCostModel(funding=rec)
    assert m.compute_funding(1234.0, "short", 42) == 3.5
    assert rec.calls == [(1234.0, "short", 42)]


def test_funding_models_are_swappable() -> None:
    a = _RecordingFunding(result=1.0)
    b = _RecordingFunding(result=-2.0)
    assert SeleneCryptoCostModel(a).compute_funding(10.0, "long", 1) == 1.0
    assert SeleneCryptoCostModel(b).compute_funding(10.0, "long", 1) == -2.0


# --------------------------------------------------------------------------
# from_rates -> HistoricalFundingModel (lazy bridge)
# --------------------------------------------------------------------------
def test_from_rates_wraps_historical_funding_model() -> None:
    pytest.importorskip("selene_space_backtest")
    m = SeleneCryptoCostModel.from_rates([(1000, 0.001), (2000, -0.002)])

    # Before the first funding event -> 0.
    assert m.compute_funding(1000.0, "long", 500) == 0.0
    # Long pays positive rate.
    assert m.compute_funding(1000.0, "long", 1000) == pytest.approx(1.0)
    # Same event already applied -> 0 (no double count).
    assert m.compute_funding(1000.0, "long", 1500) == 0.0


def test_from_rates_catch_up_applies_all_missed_events() -> None:
    pytest.importorskip("selene_space_backtest")
    m = SeleneCryptoCostModel.from_rates([(1000, 0.001), (2000, -0.002)])
    # One call spanning both events: long pays 1.0 at +0.001, receives 2.0 at -0.002.
    assert m.compute_funding(1000.0, "long", 5000) == pytest.approx(-1.0)


def test_from_rates_short_receives_positive_rate() -> None:
    pytest.importorskip("selene_space_backtest")
    m = SeleneCryptoCostModel.from_rates([(1000, 0.001)])
    assert m.compute_funding(1000.0, "short", 1000) == pytest.approx(-1.0)


def test_from_rates_filters_zero_rates() -> None:
    pytest.importorskip("selene_space_backtest")
    m = SeleneCryptoCostModel.from_rates([(1000, 0.0), (2000, 0.001)])
    # The zero-rate entry is filtered, so no event fires at ts=1000...
    assert m.compute_funding(1000.0, "long", 1000) == 0.0
    # ...and the first (only) event is the +0.001 at ts=2000.
    assert m.compute_funding(1000.0, "long", 2000) == pytest.approx(1.0)


# --------------------------------------------------------------------------
# Per-side state isolation (FD4 / F-O3)
# --------------------------------------------------------------------------
def test_from_rates_creates_independent_per_side_instances() -> None:
    """from_rates builds two separate HistoricalFundingModel instances."""
    pytest.importorskip("selene_space_backtest")
    m = SeleneCryptoCostModel.from_rates([(1000, 0.001)])
    assert m._long_funding is not m._short_funding


def test_compute_funding_routes_by_side() -> None:
    """Each side advances only its own model — no cross-side state bleed."""
    pytest.importorskip("selene_space_backtest")
    m = SeleneCryptoCostModel.from_rates([(1000, 0.001)])
    # A long call advances ONLY the long-side model.
    assert m.compute_funding(1000.0, "long", 1000) == pytest.approx(1.0)
    assert m.compute_funding(1000.0, "long", 1500) == 0.0  # long-side already applied
    # The short side is still fresh and charges the same event.
    assert m.compute_funding(1000.0, "short", 1500) == pytest.approx(-1.0)


def test_same_timestamp_both_sides_charge_once() -> None:
    """F-O3 regression: one shared stateful model would zero the second side."""
    pytest.importorskip("selene_space_backtest")
    m = SeleneCryptoCostModel.from_rates([(1000, 0.001)])
    long_charge = m.compute_funding(1000.0, "long", 1000)
    short_charge = m.compute_funding(1000.0, "short", 1000)
    # Long pays the positive rate, short receives it — both nonzero proves the
    # second call did NOT hit an already-applied shared cursor.
    assert long_charge == pytest.approx(1.0)
    assert short_charge == pytest.approx(-1.0)
    assert long_charge != 0.0
    assert short_charge != 0.0


# --------------------------------------------------------------------------
# M2 constructor guard
# --------------------------------------------------------------------------
def test_m2_guard_rejects_stateful_historical_funding_model() -> None:
    pytest.importorskip("selene_space_backtest")
    from selene_space_backtest.funding import HistoricalFundingModel

    hf = HistoricalFundingModel([(1000, 0.001)])
    with pytest.raises(ValueError, match="from_rates"):
        SeleneCryptoCostModel(funding=hf)


def test_m2_guard_allows_stateless_injection() -> None:
    """A custom stateless model (no _last_applied_idx) is never killed."""
    rec = _RecordingFunding(result=2.0)
    m = SeleneCryptoCostModel(funding=rec)
    assert m.compute_funding(1.0, "long", 1) == 2.0


def test_m2_guard_allows_no_funding() -> None:
    m = SeleneCryptoCostModel()
    assert isinstance(m.funding, NoFunding)


# --------------------------------------------------------------------------
# Drop-in for WeightDriftBacktest
# --------------------------------------------------------------------------
def _panel(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=10)
    n = 10 * 10
    factor = rng.normal(0.0, 1.0, n)
    ret = 0.3 * factor + 0.1 * rng.normal(0.0, 1.0, n)
    factor_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, 10),
            "symbol": np.tile([f"S{i}" for i in range(10)], 10),
            "factor": factor,
        }
    )
    ret_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, 10),
            "symbol": np.tile([f"S{i}" for i in range(10)], 10),
            "fut_ret_1": ret,
        }
    )
    return factor_df, ret_df


def _dict_close(a: dict[str, float], b: dict[str, float]) -> None:
    """Assert two metric dicts equal, treating NaN as equal (backtest stats may be NaN)."""
    assert set(a) == set(b)
    for k in a:
        if pd.isna(a[k]) and pd.isna(b[k]):
            continue
        assert a[k] == pytest.approx(b[k])


def test_drop_in_weight_drift_backtest_uses_injected_model() -> None:
    factor_df, ret_df = _panel()
    core = factor_df[["trade_date", "symbol", "factor"]]

    # Crypto model ignores tax: if the backtest honored the injected model,
    # tax_rate 0.02 vs 0.99 must produce identical stats (an AShare default
    # would diverge wildly at 99% sell tax).
    lo = WeightDriftConfig(epoch=3, n_bins=3, tax_rate=0.02, cost_model=SeleneCryptoCostModel())
    hi = WeightDriftConfig(epoch=3, n_bins=3, tax_rate=0.99, cost_model=SeleneCryptoCostModel())
    res_lo = WeightDriftBacktest().run(core, ret_df, lo)
    res_hi = WeightDriftBacktest().run(core, ret_df, hi)
    assert isinstance(res_lo.stats_ls, dict) and len(res_lo.stats_ls) > 0
    _dict_close(res_lo.stats_ls, res_hi.stats_ls)


def test_port_module_imports_no_torch_or_polars() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("torch", "polars"), f"banned import {alias.name}"
