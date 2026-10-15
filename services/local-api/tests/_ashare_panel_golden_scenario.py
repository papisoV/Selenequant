"""Shared deterministic scenario for the US-101 golden regression.

Imported by BOTH test_api_ashare_panel_helper.py (assertions) and
scratch_capture_golden.py (fixture re-capture). Must stay import-free of
app.* and selene_space_* so the capture script can run against the
pre-refactor ashare.py (which lacks build_ashare_panel).

Determinism notes:
- every fake reseeds np.random per call (order-independent), because the
  refactor moves the get_index_daily call before the klines loop — a
  shared RNG stream would shift which numbers each fake returns and the
  golden would break for fixture reasons, not behavior reasons.
- no turnover/amount columns on the fake klines: with turnover present
  the technical_fundamental path hits a PRE-EXISTING KeyError('volume')
  in the SIZE branch (panel never carries `volume`; the endpoint copies
  only turnover/amount from klines). Mirrors the upstream US-B3 test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

N_DAYS = 90
CODES = ["600519", "000858", "000001"]

REQUEST_QS = (
    "symbols=600519,000858,000001&weight_method=equal&n_long=2"
    "&factor_set=technical_fundamental"
)

FUND_FACTORS = {"EP", "BP", "YOY_NI", "ROE", "GP_MARGIN", "LEVERAGE", "CURRENT_RATIO"}


def fake_klines(symbol: str, start_date: str = "20240101") -> pd.DataFrame:
    """Per-symbol seeded klines — order-independent (see module docstring)."""
    np.random.seed(100 + int(symbol))
    n = N_DAYS
    rng_close = 10 + np.cumsum(np.random.randn(n) * 0.1)
    return pd.DataFrame(
        {
            # %Y%m%d strings: matches get_daily_klines real output
            "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y%m%d"),
            "open": rng_close,
            "high": rng_close * 1.02,
            "low": rng_close * 0.98,
            "close": rng_close,
            "volume": np.full(n, 1_000_000.0),
        }
    )


def financials() -> list[dict]:
    """NON-EMPTY PIT financials — the Major-1 path (fund factors appended)."""
    rows = [
        {
            "symbol": code,
            "pubDate": "2024-03-15",
            "statDate": "2024-02-29",
            "BPS": 3.0,
            "epsTTM": 0.5,
            "YOYNI": 12.0,
            "roeAvg": 0.15,
            "gpMargin": 0.4,
            "liabilityToAsset": 0.55,
            "currentRatio": 1.8,
        }
        for code in CODES
    ]
    rows.append(
        {
            "symbol": "600519",
            "pubDate": "2024-06-15",
            "statDate": "2024-05-31",
            "BPS": 3.2,
            "epsTTM": 0.55,
            "YOYNI": 10.0,
            "roeAvg": 0.16,
            "gpMargin": 0.41,
            "liabilityToAsset": 0.54,
            "currentRatio": 1.9,
        }
    )
    return rows


def fake_index_daily() -> pd.DataFrame:
    np.random.seed(7)
    n = N_DAYS
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y%m%d"),
            "close": 3000 + np.cumsum(np.random.randn(n) * 10),
        }
    )
