"""Tests for factor_report — synthetic panel IC ranking + quantile monotonicity."""

import numpy as np
import pandas as pd
import pytest

from selene_space_ashare.factor_report import generate_factor_report


@pytest.fixture
def synthetic_panel() -> pd.DataFrame:
    """20 days x 10 stocks. STRONG factor drives fwd_5d; NOISE does not."""
    rng = np.random.default_rng(42)
    dates = pd.date_range("2025-01-01", periods=20, freq="B")
    symbols = [f"{600000 + i}" for i in range(10)]
    rows = []
    for d in dates:
        strong = rng.normal(size=10)
        noise = rng.normal(size=10)
        fwd = strong * 0.02 + rng.normal(scale=0.002, size=10)
        rows.append(
            pd.DataFrame(
                {
                    "date": d,
                    "symbol": symbols,
                    "STRONG": strong,
                    "NOISE": noise,
                    "fwd_5d": fwd,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def test_strong_factor_outranks_noise(synthetic_panel):
    report = generate_factor_report(synthetic_panel, factors=["STRONG", "NOISE"])
    ic = {r["factor"]: r for r in report["ic_report"]}
    assert abs(ic["STRONG"]["IC_mean"]) > abs(ic["NOISE"]["IC_mean"])
    assert abs(ic["STRONG"]["IR"]) > abs(ic["NOISE"]["IR"])


def test_strong_factor_ic_mean_positive(synthetic_panel):
    report = generate_factor_report(synthetic_panel, factors=["STRONG"])
    ic = report["ic_report"][0]
    assert ic["IC_mean"] > 0.5
    assert ic["t_stat"] > 3


def test_quantile_monotone(synthetic_panel):
    report = generate_factor_report(synthetic_panel, factors=["STRONG"])
    q = report["quantile_returns"]["STRONG"]
    assert q, "quantile records empty"
    means = [row["mean"] for row in q]
    # STRONG positively predicts returns → high quantile = high mean return
    assert means[-1] > means[0]


def test_report_json_serializable(synthetic_panel):
    import json

    report = generate_factor_report(synthetic_panel, factors=["STRONG", "NOISE"])
    blob = json.dumps(report, ensure_ascii=False)
    assert '"ic_report"' in blob
    assert '"factor_correlation"' in blob
    assert report["n_days"] == 20
    assert report["n_symbols"] == 10
