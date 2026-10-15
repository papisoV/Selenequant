"""Weight-drift funding carry integration (P3-F-07).

Tests the FD1 3-phase funding insertion in ``WeightDriftBacktest.run()``:

- **Parity gates**: ``charge_funding=False`` leaves the golden AlphaPurify
  fixture untouched; ``charge_funding=True`` with zero funding is bit-identical
  to OFF on every shared column.
- **Hand-computed funding**: a real rate table (``SeleneCryptoCostModel.from_rates``)
  charges ``rate x notional`` at the event date only; a skewed model
  (``funding_top + funding_bottom != 0``) displaces the legs exactly and moves
  every stats key on both the ret side (mean_ret / vol / win_rate) and the cum
  side (Ann.Return / Max Drawdown / PnL).
- **Multi-epoch boundary notional (#7)**: a boundary date where bridge + normal
  rows coexist charges ``rate x 1.0``, not ``rate x 2.0`` — the bridge-row
  exclusion prevents double-charging.
- **Zero-notional cursor advance (#8)**: an empty bucket still advances the
  funding cursor, so the following date charges only its own rate (no catch-up
  of a stale event).
- **M3 fail-fast** and the **E2E** ``FundingRateSource -> build -> run`` path.

Modules are loaded by file path through the synthetic package ``_module_loader``
(consistent with ``test_weight_drift_backtest.py``), and an AST pass asserts
this test file never imports torch / polars / market-data.
"""
from __future__ import annotations

import ast
import json
import pathlib
import types

import numpy as np
import pandas as pd
import pytest

from _module_loader import MINING_DIR as _MINING, load_module

FIX = pathlib.Path(__file__).parent / "fixtures" / "weight_drift"


def _load(name: str) -> types.ModuleType:
    return load_module(name, _MINING, package="selene_wd")


cost_models = _load("cost_models")
weight_drift_result = _load("weight_drift_result")
wd = _load("weight_drift_backtest")
funding_source = _load("funding_source")

WeightDriftConfig = wd.WeightDriftConfig
WeightDriftBacktest = wd.WeightDriftBacktest
WeightDriftResult = weight_drift_result.WeightDriftResult
SeleneCryptoCostModel = cost_models.SeleneCryptoCostModel
AShareCostModel = cost_models.AShareCostModel
build_funding_cost_model = funding_source.build_funding_cost_model

_FUNDING_COLS = ("funding_top", "funding_bottom")


def _ts(date) -> int:
    return int(pd.Timestamp(date).value // 10**6)


def _panel(
    n_dates: int,
    n_symbols: int,
    *,
    n_bins: int = 2,
    factor: np.ndarray | None = None,
    ret: np.ndarray | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(0)
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    n = n_dates * n_symbols
    if factor is None:
        factor = rng.normal(0.0, 1.0, n)
    if ret is None:
        ret = 0.3 * np.asarray(factor, dtype=float) + 0.1 * rng.normal(0.0, 1.0, n)
    factor_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, n_symbols),
            "symbol": np.tile([f"S{i}" for i in range(n_symbols)], n_dates),
            "factor": np.asarray(factor, dtype=float),
        }
    )
    ret_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, n_symbols),
            "symbol": np.tile([f"S{i}" for i in range(n_symbols)], n_dates),
            "fut_ret_1": np.asarray(ret, dtype=float),
        }
    )
    return factor_df, ret_df


def _shared_cols(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in _FUNDING_COLS]


class _EventFundingModel(SeleneCryptoCostModel):
    """Deterministic hand-computable funding table.

    Stand-in for ``HistoricalFundingModel`` chosen so that
    ``funding_top + funding_bottom != 0`` (the real model nets to ~0 on the LS
    leg because long and short charge opposite signs at equal notional). Fires
    only at the configured ``ts_ms`` events, returning ``rate x notional`` with
    a per-side rate; every other timestamp charges nothing.
    """

    def __init__(self, events: dict[int, tuple[float, float]]) -> None:
        super().__init__()
        self.events = events

    def compute_funding(self, notional: float, side: str, timestamp: int) -> float:
        rates = self.events.get(timestamp)
        if rates is None:
            return 0.0
        return (rates[0] if side == "long" else rates[1]) * notional


def _fixture_config(meta: dict) -> WeightDriftConfig:
    rc, cols = meta["research_cfg"], meta["columns"]
    return WeightDriftConfig(
        n_bins=rc["bins"],
        epoch=meta["epoch"],
        w0_halflife=0,
        forward_fill_w0=True,
        bridge_epoch_boundary=True,
        tax_rate=rc["tax_rate"],
        fee_rate=rc["fee_rate"],
        slippage_rate=rc["slippage_rate"],
        tax_rate_direction=rc["tax_rate_direction"],
        base_rate=rc["base_rate"],
        factor_col=cols["factor_col"],
        trade_date_col=cols["trade_date_col"],
        symbol_col=cols["symbol_col"],
        ret_col=cols["ret_col"],
    )


def _assert_frame_close(got: pd.DataFrame, exp: pd.DataFrame, tol: float, date_col: str) -> None:
    assert set(got.columns) == set(exp.columns)
    g = got.reset_index(drop=True)
    e = exp.reset_index(drop=True)
    assert np.array_equal(
        pd.to_datetime(g[date_col]).fillna(pd.Timestamp(0)).to_numpy(),
        pd.to_datetime(e[date_col]).fillna(pd.Timestamp(0)).to_numpy(),
    ), "date rows misaligned"
    for c in exp.columns:
        if c == date_col:
            continue
        gv = g[c].to_numpy(dtype=float)
        ev = e[c].to_numpy(dtype=float)
        assert np.allclose(gv, ev, rtol=tol, atol=tol, equal_nan=True), (
            f"col {c}: maxdiff={np.nanmax(np.abs(gv - ev)):.3e} > tol={tol}"
        )


# --------------------------------------------------------------------------
# Independence guarantees.
# --------------------------------------------------------------------------
def test_testfile_imports_no_torch_polars_market_data() -> None:
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("torch", "polars", "selene_space_market", "alphapurify"), (
                    f"banned import {alias.name}"
                )


# --------------------------------------------------------------------------
# Parity gates.
# --------------------------------------------------------------------------
def test_charge_funding_false_golden_fixture_parity() -> None:
    """The OFF funding block is inert: the golden fixture matches at 1e-8."""
    meta = json.loads((FIX / "meta.json").read_text(encoding="utf-8"))
    factor_df = pd.read_parquet(FIX / meta["inputs"]["factor_df"])
    ret_df = pd.read_parquet(FIX / meta["inputs"]["ret_df"])
    cfg = _fixture_config(meta)
    assert cfg.charge_funding is False  # the parity gate is the default OFF path
    res = WeightDriftBacktest().run(factor_df, ret_df, cfg)
    exp = pd.read_parquet(FIX / meta["expected"]["df_result"])
    _assert_frame_close(res.df_result, exp, 1e-8, meta["columns"]["trade_date_col"])


@pytest.mark.parametrize("mode", ["no_funding", "empty_rates"])
def test_charge_funding_true_zero_funding_bit_identical_to_off(mode: str) -> None:
    factor_df, ret_df = _panel(6, 4, n_bins=2)
    base = WeightDriftConfig(epoch=6, n_bins=2, cost_model=SeleneCryptoCostModel())
    if mode == "no_funding":
        model = SeleneCryptoCostModel()
    else:
        model = SeleneCryptoCostModel.from_rates([])
    on = WeightDriftConfig(epoch=6, n_bins=2, cost_model=model, charge_funding=True)
    res_off = WeightDriftBacktest().run(factor_df, ret_df, base)
    res_on = WeightDriftBacktest().run(factor_df, ret_df, on)

    # Every pre-existing column is bit-identical; funding columns are all-zero.
    for c in _shared_cols(res_off.df_result):
        assert c in res_on.df_result.columns
        assert np.array_equal(
            res_on.df_result[c].to_numpy(dtype=float),
            res_off.df_result[c].to_numpy(dtype=float),
        ), f"col {c} drifted under zero funding"
    assert (res_on.df_result["funding_top"] == 0.0).all()
    assert (res_on.df_result["funding_bottom"] == 0.0).all()

    for attr in ("stats_ls", "stats_l", "stats_s"):
        got, exp = getattr(res_on, attr), getattr(res_off, attr)
        assert set(got) == set(exp)
        for k in exp:
            assert (got[k] == exp[k]) or (pd.isna(got[k]) and pd.isna(exp[k])), (attr, k)
    assert res_on.mean_ret == res_off.mean_ret


# --------------------------------------------------------------------------
# Hand-computed funding (real rate table).
# --------------------------------------------------------------------------
def test_funding_columns_real_rate_table_hand_computed() -> None:
    """from_rates charges rate x notional(=1.0) once, at the event date only."""
    dates = pd.bdate_range("2024-01-01", periods=6)
    factor_df, ret_df = _panel(6, 4, n_bins=2)
    model = SeleneCryptoCostModel.from_rates([(_ts(dates[0]), 0.001)])
    off = WeightDriftConfig(epoch=6, n_bins=2, cost_model=SeleneCryptoCostModel())
    on = WeightDriftConfig(epoch=6, n_bins=2, cost_model=model, charge_funding=True)
    res_off = WeightDriftBacktest().run(factor_df, ret_df, off)
    res_on = WeightDriftBacktest().run(factor_df, ret_df, on)
    got = res_on.df_result

    # Long pays +rate, short receives -rate, at the rebalance date (notional 1.0).
    assert got.loc[0, "funding_top"] == pytest.approx(0.001, abs=1e-12)
    assert got.loc[0, "funding_bottom"] == pytest.approx(-0.001, abs=1e-12)
    # The stateful model fires once per event — no other date charges.
    assert (got.loc[1:, "funding_top"] == 0.0).all()
    assert (got.loc[1:, "funding_bottom"] == 0.0).all()

    # Phase 2/3 displacement is exact: legs move by their funding, LS nets zero
    # (equal long/short notional x same rate -> (top + bottom) / 2 == 0).
    np.testing.assert_allclose(
        res_on.df_result["ret_net_2"] - res_off.df_result["ret_net_2"],
        -got["funding_top"], rtol=0, atol=1e-12,
    )
    np.testing.assert_allclose(
        res_on.df_result["ret_net_s_1"] - res_off.df_result["ret_net_s_1"],
        -got["funding_bottom"], rtol=0, atol=1e-12,
    )
    np.testing.assert_allclose(
        res_on.df_result["ret_net_ls"] - res_off.df_result["ret_net_ls"],
        -(got["funding_top"] + got["funding_bottom"]) / 2, rtol=0, atol=1e-12,
    )


# --------------------------------------------------------------------------
# Skewed funding: exact displacement + both stats sides move.
# --------------------------------------------------------------------------
def test_skewed_funding_displaces_legs_and_moves_both_stats_sides() -> None:
    dates = pd.bdate_range("2024-01-01", periods=6)
    # Deterministic monotone factor and all-negative returns: base ret_net_ls < 0
    # every date (win_rate 0.0) so the credits below flip it deterministically.
    factor = np.tile(np.arange(4) + 1.0, 6)  # S0<S1<S2<S3 every date
    ret = -0.01 * np.abs(factor)
    factor_df, ret_df = _panel(6, 4, n_bins=2, factor=factor, ret=ret)
    events = {
        _ts(dates[0]): (-0.05, -0.04),  # both sides credited (negative funding)
        _ts(dates[3]): (-0.03, -0.02),
    }
    model = _EventFundingModel(events)
    off = WeightDriftConfig(epoch=6, n_bins=2, cost_model=SeleneCryptoCostModel())
    on = WeightDriftConfig(epoch=6, n_bins=2, cost_model=model, charge_funding=True)
    res_off = WeightDriftBacktest().run(factor_df, ret_df, off)
    res_on = WeightDriftBacktest().run(factor_df, ret_df, on)
    got = res_on.df_result
    off_df = res_off.df_result

    # funding_top + funding_bottom != 0 on the event rows (the LS leg moves).
    assert got.loc[0, "funding_top"] == pytest.approx(-0.05, abs=1e-12)
    assert got.loc[0, "funding_bottom"] == pytest.approx(-0.04, abs=1e-12)
    assert got.loc[3, "funding_top"] == pytest.approx(-0.03, rel=1e-6)
    assert got.loc[3, "funding_bottom"] == pytest.approx(-0.02, rel=1e-6)
    assert got.loc[0, "funding_top"] + got.loc[0, "funding_bottom"] != 0.0
    assert (got.loc[[1, 2, 4, 5], "funding_top"] == 0.0).all()
    assert (got.loc[[1, 2, 4, 5], "funding_bottom"] == 0.0).all()

    # Exact per-leg displacement (Phase 2/3).
    for leg, fcol in (("ret_net_2", "funding_top"), ("ret_net_s_1", "funding_bottom")):
        np.testing.assert_allclose(
            got[leg] - off_df[leg], -got[fcol], rtol=0, atol=1e-12
        )
    np.testing.assert_allclose(
        got["ret_net_ls"] - off_df["ret_net_ls"],
        -(got["funding_top"] + got["funding_bottom"]) / 2, rtol=0, atol=1e-12,
    )

    # Hand-recompute the LS stats from the displaced net returns and compare
    # exactly: proves BOTH the ret side (mean_ret / win_rate) and the cum side
    # (Ann.Return / Max Drawdown / PnL) carry the funding.
    disp = (got["funding_top"] + got["funding_bottom"]) / 2
    exp_ret_net_ls = off_df["ret_net_ls"] - disp
    exp_cum = (1.0 + exp_ret_net_ls).cumprod().to_numpy()
    days = (dates[-1] - dates[0]).days
    assert res_on.mean_ret == pytest.approx(float(exp_ret_net_ls.mean()), abs=1e-12)
    assert res_on.stats_ls["WinRate"] == pytest.approx(float((exp_ret_net_ls > 0).mean()))
    assert res_on.stats_ls["Ann. Return"] == pytest.approx(exp_cum[-1] ** (365.25 / days) - 1)
    running_max = np.maximum.accumulate(exp_cum)
    assert res_on.stats_ls["Max Drawdown"] == pytest.approx(float(np.nanmin(exp_cum / running_max - 1)))
    assert res_on.stats_ls["PnL"] == pytest.approx(float(exp_cum[-1] - 1))

    # L and S legs: hand-compute every stat from the displaced net returns and
    # compare exactly. This proves the funding flows through the per-leg ret
    # side (Ann.Std / WinRate) AND the cum side (Ann.Return / MaxDD / PnL),
    # for both legs — no hard-coded win-rate assumption.
    n_periods = len(got)
    annual_factor = 365.25 / (days / n_periods)
    for attr, leg, fcol in (
        ("stats_l", "ret_net_2", "funding_top"),
        ("stats_s", "ret_net_s_1", "funding_bottom"),
    ):
        exp_ret = off_df[leg] - got[fcol]
        exp_cum = (1.0 + exp_ret).cumprod().to_numpy()
        running_max = np.maximum.accumulate(exp_cum)
        moved = getattr(res_on, attr)
        assert moved["WinRate"] == pytest.approx(float((exp_ret > 0).mean()))
        assert moved["Ann. Std"] == pytest.approx(float(exp_ret.std(ddof=1) * np.sqrt(annual_factor)))
        assert moved["Ann. Return"] == pytest.approx(exp_cum[-1] ** (365.25 / days) - 1)
        assert moved["Max Drawdown"] == pytest.approx(float(np.nanmin(exp_cum / running_max - 1)))
        assert moved["PnL"] == pytest.approx(float(exp_cum[-1] - 1))


# --------------------------------------------------------------------------
# Multi-epoch boundary notional (#7, CRITICAL).
# --------------------------------------------------------------------------
@pytest.mark.parametrize("n_dates,epoch,boundary_row", [(21, 10, 10), (41, 20, 20)])
def test_multi_epoch_boundary_notional_is_one_not_two(
    n_dates: int, epoch: int, boundary_row: int
) -> None:
    """A boundary date (bridge + normal coexist) charges rate x 1.0, not x 2.0.

    The panel spans > 2 x epoch so at least one epoch boundary falls inside it.
    The bridge rows carry the previous epoch's holdings to the boundary with
    ``fut_ret_1 = NaN``; the v4 bridge-row filter excludes them from the
    notional, so the charge is ``rate x 1.0`` — a full-group sum would be
    ``~rate x 2.0`` and double-charge.
    """
    dates = pd.bdate_range("2024-01-01", periods=n_dates)
    factor_df, ret_df = _panel(n_dates, 4, n_bins=2)
    model = SeleneCryptoCostModel.from_rates([(_ts(dates[boundary_row]), 0.001)])
    cfg = WeightDriftConfig(epoch=epoch, n_bins=2, cost_model=model, charge_funding=True)
    res = WeightDriftBacktest().run(factor_df, ret_df, cfg)
    got = res.df_result
    assert got.shape[0] == n_dates

    # Boundary notional ~1.0 (rate x 1.0 = 0.001), not ~2.0 (0.002).
    assert got.loc[boundary_row, "funding_top"] == pytest.approx(0.001, abs=0.0005)
    assert got.loc[boundary_row, "funding_bottom"] == pytest.approx(-0.001, abs=0.0005)
    # Every other date charges nothing (the stateful model fires once).
    others = got.index[got.index != boundary_row]
    assert (got.loc[others, "funding_top"] == 0.0).all()
    assert (got.loc[others, "funding_bottom"] == 0.0).all()


# --------------------------------------------------------------------------
# Zero-notional cursor advance (#8).
# --------------------------------------------------------------------------
def test_empty_bucket_advances_cursor_no_catch_up() -> None:
    """An empty bucket (notional 0) still advances the funding cursor.

    Without the unconditional ``compute_funding(0, side, ts)`` call at the empty
    date, the following date would catch up the stale rate too: 0.002 instead of
    0.001. This locks the #8 contract.
    """
    dates = pd.bdate_range("2024-01-01", periods=6)
    n_dates, n_symbols = 6, 4
    factor = np.tile(np.arange(n_symbols) + 1.0, n_dates)
    for s in (0, 1):  # empty the BOTTOM bucket (S0,S1) on date idx 2
        factor[2 * n_symbols + s] = np.nan
    ret = 0.3 * np.nan_to_num(factor) + 0.1 * np.random.default_rng(1).normal(
        size=n_dates * n_symbols
    )
    factor_df, ret_df = _panel(n_dates, n_symbols, n_bins=2, factor=factor, ret=ret)
    model = SeleneCryptoCostModel.from_rates([(_ts(dates[2]), 0.001), (_ts(dates[3]), 0.001)])
    cfg = WeightDriftConfig(epoch=6, n_bins=2, cost_model=model, charge_funding=True)
    res = WeightDriftBacktest().run(factor_df, ret_df, cfg)
    got = res.df_result

    # Empty date: zero notional -> zero charge, but the call still advances the
    # short-side cursor past the ts(dates[2]) event.
    assert got.loc[2, "funding_bottom"] == 0.0
    # Following date charges ONLY its own rate (0.001), not a 0.002 catch-up.
    assert got.loc[3, "funding_bottom"] == pytest.approx(-0.001, abs=1e-12)
    # The top bucket is full on both dates and charges its own rate each time.
    assert got.loc[2, "funding_top"] == pytest.approx(0.001, abs=1e-12)
    assert got.loc[3, "funding_top"] == pytest.approx(0.001, abs=1e-12)


# --------------------------------------------------------------------------
# M3 fail-fast.
# --------------------------------------------------------------------------
def test_m3_fail_fast_charge_funding_ashare_raises() -> None:
    factor_df, ret_df = _panel(6, 4, n_bins=2)
    cfg = WeightDriftConfig(
        epoch=6, n_bins=2, cost_model=AShareCostModel(), charge_funding=True
    )
    with pytest.raises(ValueError, match="charge_funding"):
        WeightDriftBacktest().run(factor_df, ret_df, cfg)


def test_m3_off_ashare_runs() -> None:
    factor_df, ret_df = _panel(6, 4, n_bins=2)
    cfg = WeightDriftConfig(epoch=6, n_bins=2, cost_model=AShareCostModel())
    res = WeightDriftBacktest().run(factor_df, ret_df, cfg)
    assert isinstance(res, WeightDriftResult)


# --------------------------------------------------------------------------
# E2E: FundingRateSource -> build_funding_cost_model -> run.
# --------------------------------------------------------------------------
class _StubSource:
    def __init__(self, pairs: list[tuple[int, float]]) -> None:
        self.pairs = pairs
        self.calls = 0

    def get_history(
        self, *, exchange: str, symbol: str, start_ms: int, end_ms: int
    ) -> list[tuple[int, float]]:
        self.calls += 1
        return self.pairs


def test_e2e_stub_funding_source_through_backtest() -> None:
    dates = pd.bdate_range("2024-01-01", periods=6)
    # Deterministic small rets (monotone factor, all-negative) so the legs stay
    # far from the -1.0 cum-saturation point and the funding shift is visible.
    factor = np.tile(np.arange(4) + 1.0, 6)
    ret = -0.01 * np.abs(factor)
    factor_df, ret_df = _panel(6, 4, n_bins=2, factor=factor, ret=ret)
    source = _StubSource([(_ts(dates[0]), 0.001)])
    model = build_funding_cost_model(
        source, exchange="test", symbol="X", start_ms=0, end_ms=10**13
    )
    assert source.calls == 1
    off = WeightDriftConfig(epoch=6, n_bins=2, cost_model=SeleneCryptoCostModel())
    on = WeightDriftConfig(epoch=6, n_bins=2, cost_model=model, charge_funding=True)
    res_off = WeightDriftBacktest().run(factor_df, ret_df, off)
    res_on = WeightDriftBacktest().run(factor_df, ret_df, on)

    # Funding columns appear and carry the rate. The single symmetric rate nets
    # to zero on the LS leg ((top + bottom) / 2 == 0) but is visible on the
    # per-leg rets and their cum-side stats — the documented funding convention.
    assert "funding_top" in res_on.df_result.columns
    assert "funding_bottom" in res_on.df_result.columns
    assert res_on.df_result.loc[0, "funding_top"] == pytest.approx(0.001, abs=1e-12)
    assert res_on.df_result.loc[0, "funding_bottom"] == pytest.approx(-0.001, abs=1e-12)
    np.testing.assert_allclose(
        res_on.df_result["ret_net_2"] - res_off.df_result["ret_net_2"],
        -res_on.df_result["funding_top"], rtol=0, atol=1e-12,
    )
    np.testing.assert_allclose(
        res_on.df_result["ret_net_s_1"] - res_off.df_result["ret_net_s_1"],
        -res_on.df_result["funding_bottom"], rtol=0, atol=1e-12,
    )
    np.testing.assert_allclose(
        res_on.df_result["ret_net_ls"].to_numpy(),
        res_off.df_result["ret_net_ls"].to_numpy(),
        rtol=0, atol=1e-12,
    )
    assert res_on.stats_l["Ann. Return"] != res_off.stats_l["Ann. Return"]
    assert res_on.stats_s["Ann. Return"] != res_off.stats_s["Ann. Return"]
