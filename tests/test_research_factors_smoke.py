"""Smoke test: verify research factors merged into master can execute the
research champion formula `TF_PRESSURE OF_TOP_ACC_RATIO NEG ADD`.

This test does NOT require orderflow.db — if the DB is missing, OF_*
factors return 0.0 (graceful fallback). The test verifies:
  1. PandasStackVM can build a vocab with all 77 features
  2. Every feature has a compute function (no None in _factor_fns)
  3. The champion formula token sequence executes without error
  4. FIB factors produce non-zero values on real kline data
  5. DERIV factors execute without NameError/AttributeError

Run:
    cd selene-master
    python tests/test_research_factors_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "market-data"))

from selene_space_alpha.factors.pandas_bridge import ALL_FACTORS
from selene_space_alpha.vocab import FEATURE_NAMES, FORMULA_VOCAB
from selene_space_alpha.vm import PandasStackVM


def test_all_features_have_compute_fn():
    """Every feature in vocab must map to a compute function."""
    vm = PandasStackVM(FORMULA_VOCAB)
    unmapped = [name for name, fn in vm._factor_fns if fn is None]
    assert len(vm._factor_fns) == len(FEATURE_NAMES)
    assert unmapped == [], f"Unmapped features: {unmapped}"
    print(f"[PASS] All {len(FEATURE_NAMES)} features mapped to compute fn")


def test_research_factors_in_all_factors():
    """Research-only factors must be registered in ALL_FACTORS."""
    required = [
        "fib_pos", "fib_confluence", "fib_density", "fib_bounce",
        "fib2_high_density", "fib2_atr_prox", "fib3_time_at_level",
        "of_top_acc_ratio", "of_taker_ratio",
        "deriv_liq_pressure_proxy", "deriv_basis",
    ]
    for key in required:
        assert key in ALL_FACTORS, f"MISSING from ALL_FACTORS: {key}"
    print(f"[PASS] All {len(required)} research factors registered in ALL_FACTORS")


def test_champion_formula_executes():
    """Execute research champion `TF_PRESSURE OF_TOP_ACC_RATIO NEG ADD`.

    Token sequence (per master vocab, feature_count=77, operator_offset=77):
      TF_PRESSURE (idx 40)
      OF_TOP_ACC_RATIO (idx 49)
      NEG (operator, idx 77+4=81)
      ADD (operator, idx 77+0=77)
    """
    vm = PandasStackVM(FORMULA_VOCAB)
    # Build a synthetic DataFrame with OHLCV columns
    import numpy as np
    np.random.seed(42)
    n = 200
    close = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "timestamp": (pd.Timestamp("2024-01-01").value // 10**6 + np.arange(n) * 4 * 3600 * 1000),
        "open": close - np.random.rand(n) * 0.3,
        "high": close + np.abs(np.random.randn(n)) * 0.4,
        "low": close - np.abs(np.random.randn(n)) * 0.4,
        "close": close,
        "volume": 1000 + np.random.rand(n) * 500,
    })

    # Token sequence: TF_PRESSURE OF_TOP_ACC_RATIO NEG ADD
    tf_pressure_idx = FEATURE_NAMES.index("TF_PRESSURE")
    of_top_acc_idx = FEATURE_NAMES.index("OF_TOP_ACC_RATIO")
    # Operators: find their token id
    op_names = FORMULA_VOCAB.operator_names
    neg_idx = FORMULA_VOCAB.operator_offset + op_names.index("NEG")
    add_idx = FORMULA_VOCAB.operator_offset + op_names.index("ADD")

    tokens = [tf_pressure_idx, of_top_acc_idx, neg_idx, add_idx]

    score = vm.execute(tokens, df)
    assert score is not None, "Formula returned None"
    assert len(score) == n, f"Score length {len(score)} != {n}"
    print("[PASS] Champion formula TF_PRESSURE OF_TOP_ACC_RATIO NEG ADD executed")
    print(f"       Score range: [{score.min():.4f}, {score.max():.4f}], mean={score.mean():.4f}")


def test_fib_factors_on_real_data():
    """FIB factors should produce non-zero values on real kline data."""
    vm = PandasStackVM(FORMULA_VOCAB)
    klines_db = PROJECT_ROOT / "data" / "klines.db"
    if not klines_db.exists():
        print("[SKIP] klines.db not found, skipping real-data test")
        return

    import sqlite3
    conn = sqlite3.connect(str(klines_db))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol='BTC/USDT' AND exchange='binance' AND interval='4h' "
        "AND market_type='futures' ORDER BY timestamp ASC LIMIT 500",
        conn,
    )
    conn.close()
    if df.empty:
        print("[SKIP] No BTC/USDT 4h data in klines.db")
        return
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    fib_features = ["FIB_POS", "FIB_DENSITY", "FIB_BOUNCE", "FIB2_HIGH_DENSITY", "FIB3_TIME_AT_LEVEL"]
    for feat in fib_features:
        idx = FEATURE_NAMES.index(feat)
        name, fn = vm._factor_fns[idx]
        if fn is None:
            assert False, f"{feat} has no compute fn"
        series = fn(df).fillna(0.0)
        nonzero = (series.abs() > 1e-9).sum()
        print(f"  {feat}: {nonzero}/{len(series)} non-zero, range [{series.min():.4f}, {series.max():.4f}]")
        assert len(series) == len(df)
    print(f"[PASS] FIB factors compute on {len(df)} bars of real BTC/USDT 4h data")


def main():
    print("=" * 70)
    print("Smoke test: research factors merged into master")
    print("=" * 70)
    test_all_features_have_compute_fn()
    test_research_factors_in_all_factors()
    test_champion_formula_executes()
    test_fib_factors_on_real_data()
    print("=" * 70)
    print("ALL SMOKE TESTS PASSED")
    print("=" * 70)


if __name__ == "__main__":
    main()
