"""CLI surface for `ashare paramlab` (batch11 US-4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from selene_space_cli.commands.ashare import ashare_group


def _synthetic_panel(tmp_path: Path, *, varying_close: bool = False) -> Path:
    """Small panel for paramlab smoke tests.

    varying_close=False gives a flat close per symbol (inv_vol has no
    signal, falls back to equal); True gives a random walk so inv_vol has
    a real dispersion to weight on.
    """
    rng = np.random.default_rng(7 if not varying_close else 11)
    dates = pd.bdate_range("2025-01-06", periods=20)
    symbols = [f"S{i:02d}" for i in range(10)]
    lasts = {s: 10.0 + float(i) for i, s in enumerate(symbols)}
    rows = []
    for k, d in enumerate(dates):
        mom = rng.normal(size=len(symbols))
        for j, s in enumerate(symbols):
            if varying_close:
                lasts[s] *= 1.0 + float(rng.normal(scale=0.01 * (j + 1)))
                close = lasts[s]
            else:
                close = 10.0 + float(j)
            rows.append(
                {
                    "date": d,
                    "symbol": s,
                    "MOMENTUM_1M": mom[j],
                    "close": close,
                    "fwd_5d": float(0.01 * mom[j]) if k < 15 else np.nan,
                    "fwd_21d": np.nan,
                }
            )
    path = tmp_path / ("panel_vol.parquet" if varying_close else "panel.parquet")
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def _fake_index_fetcher(dates: list[pd.Timestamp]):
    def fetcher(symbol: str) -> pd.DataFrame:
        assert symbol == "000905"
        return pd.DataFrame(
            {
                "date": [d.strftime("%Y%m%d") for d in dates],
                "close": np.linspace(100.0, 115.0, len(dates)),
            }
        )

    return fetcher


def test_paramlab_registered_and_csv_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """paramlab subcommand runs end-to-end on a synthetic panel and writes CSV."""
    import selene_space_ashare.portfolio_rebal as pr_mod

    panel_path = _synthetic_panel(tmp_path)
    out_csv = tmp_path / "grid.csv"
    dates = sorted(pd.read_parquet(panel_path)["date"].unique())

    monkeypatch.setattr(pr_mod, "get_index_daily", _fake_index_fetcher(dates))

    result = CliRunner().invoke(
        ashare_group,
        [
            "paramlab",
            "--panel",
            str(panel_path),
            "--benchmark",
            "000905",
            "--rebalances",
            "5",
            "--n-longs",
            "5,8",
            "--stock-weights",
            "equal,slsqp",
            "--weight-methods",
            "equal",
            "--cost-bps",
            "0",
            "--output",
            str(out_csv),
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"Saved 4 rows to {out_csv}" in result.output
    saved = pd.read_csv(out_csv)
    assert len(saved) == 4
    assert set(saved["stock_weight"]) == {"equal", "slsqp"}


def test_paramlab_stdout_has_reading_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stdout mode prints the grid plus one of the two reading hints."""
    import selene_space_ashare.portfolio_rebal as pr_mod

    panel_path = _synthetic_panel(tmp_path)
    dates = sorted(pd.read_parquet(panel_path)["date"].unique())

    monkeypatch.setattr(pr_mod, "get_index_daily", _fake_index_fetcher(dates))

    result = CliRunner().invoke(
        ashare_group,
        [
            "paramlab",
            "--panel",
            str(panel_path),
            "--benchmark",
            "000905",
            "--rebalances",
            "5",
            "--n-longs",
            "5",
            "--stock-weights",
            "equal",
            "--weight-methods",
            "equal",
            "--cost-bps",
            "0",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "excess_annual" in result.output
    assert "[Read]" in result.output


def test_paramlab_inv_vol_csv_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """paramlab accepts --stock-weights equal,inv_vol and writes both schemes."""
    import selene_space_ashare.portfolio_rebal as pr_mod

    panel_path = _synthetic_panel(tmp_path, varying_close=True)
    out_csv = tmp_path / "grid_invvol.csv"
    dates = sorted(pd.read_parquet(panel_path)["date"].unique())

    monkeypatch.setattr(pr_mod, "get_index_daily", _fake_index_fetcher(dates))

    result = CliRunner().invoke(
        ashare_group,
        [
            "paramlab",
            "--panel",
            str(panel_path),
            "--benchmark",
            "000905",
            "--rebalances",
            "5",
            "--n-longs",
            "4,6",
            "--stock-weights",
            "equal,inv_vol",
            "--weight-methods",
            "equal",
            "--cost-bps",
            "0",
            "--output",
            str(out_csv),
        ],
    )

    assert result.exit_code == 0, result.output
    assert f"Saved 4 rows to {out_csv}" in result.output
    saved = pd.read_csv(out_csv)
    assert len(saved) == 4
    assert set(saved["stock_weight"]) == {"equal", "inv_vol"}
    inv = saved[saved["stock_weight"] == "inv_vol"]
    assert (inv["n_windows"] > 0).all()
