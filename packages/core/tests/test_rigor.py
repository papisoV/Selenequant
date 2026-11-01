"""Tests for selene_space_core.rigor — exact Decimal engine (absorbed from ai-berkshire)."""
import math
from decimal import Decimal

import pytest

from selene_space_core.rigor import (
    benford,
    cross_validate,
    exact,
    exact_calc,
    fmt_number,
    three_scenario_valuation,
    verify_market_cap,
    verify_valuation,
)


# --- Decimal precision vs float -------------------------------------------------

def test_exact_adds_precisely_where_float_drifts():
    assert 0.1 + 0.2 != 0.3  # float drift is real
    assert exact(0.1) + exact(0.2) == Decimal("0.3")  # exact engine recovers it


def test_exact_passthrough_for_decimal():
    d = Decimal("1.23")
    assert exact(d) is d


# --- Market cap cross-validation -------------------------------------------------

def test_verify_market_cap_exact_product():
    res = verify_market_cap(510, 9.11e9, 4.65e12, currency="HKD")
    assert res["calculated_cap"] == Decimal("4646100000000")
    assert res["deviation_pct"] < 5.0
    assert res["is_valid"] is True


def test_verify_market_cap_flags_large_deviation():
    res = verify_market_cap(510, 9.11e9, 2e12, currency="HKD")
    assert res["is_valid"] is False
    assert res["deviation_pct"] > 5.0


# --- cross-validate multi-source tolerance --------------------------------------

def test_cross_validate_consistent_sources():
    res = cross_validate("revenue", {"年报": 7518, "Yahoo": 7500, "StockAnalysis": 7520}, unit="亿")
    assert res["consensus"] == 7518
    assert res["all_consistent"] is True


def test_cross_validate_flags_outlier():
    res = cross_validate("revenue", {"A": 100, "B": 100, "C": 200})
    assert res["all_consistent"] is False
    assert any(d["deviation_pct"] > 2.0 for d in res["details"])


def test_cross_validate_tolerance_parameter():
    res = cross_validate("revenue", {"A": 100, "B": 100, "C": 200}, tolerance_pct=150.0)
    assert res["all_consistent"] is True


# --- benford first-digit distribution -------------------------------------------

def test_benford_returns_none_for_small_sample():
    assert benford([1, 2, 3]) is None


def test_benford_leading_digit_distribution():
    # 20 numbers per leading digit; offset pushes log10 sig clearly past each
    # integer boundary so float precision cannot flip a digit.
    values = [d * 10**4 + 7 + i * 10 for d in range(1, 10) for i in range(20)]
    res = benford(values)
    assert res is not None
    assert res["n"] == 180
    assert res["observed"][1] == 20 / 180
    # Uniform first digits deviate strongly from Benford's expectation.
    assert res["is_conforming"] is False
    assert res["mad"] > 0.015


def test_benford_conforming_data():
    counts = {d: round(1000 * math.log10(1 + 1 / d)) for d in range(1, 10)}
    values = [d * 10**6 + i for d, cnt in counts.items() for i in range(cnt)]
    res = benford(values)
    assert res is not None
    assert res["n"] == 1000
    assert res["is_conforming"] is True


# --- fmt 亿/万亿/B/T -------------------------------------------------------------

def test_fmt_number_yi_units():
    assert fmt_number(Decimal("123.45"), "亿") == "123.45亿"
    assert fmt_number(Decimal("12345"), "亿") == "1.23万亿"
    assert fmt_number(Decimal("5000"), "亿元") == "5000.00亿元"
    assert fmt_number(Decimal("20000"), "亿元") == "2.00万亿元"


def test_fmt_number_b_t_m():
    assert fmt_number(Decimal("1e12")) == "1.00T"
    assert fmt_number(Decimal("2e9")) == "2.00B"
    assert fmt_number(Decimal("3e6")) == "3.00M"
    assert fmt_number(Decimal("999999")) == "999,999.00"


# --- exact_calc -----------------------------------------------------------------

def test_exact_calc_decimal_arithmetic():
    assert exact_calc("0.1 + 0.2") == Decimal("0.3")
    assert exact_calc("510 * 9.11e9") == Decimal("4646100000000")
    assert exact_calc("(1 + 2) * 3") == Decimal("9")


def test_exact_calc_rejects_unsafe_or_invalid():
    assert exact_calc("__import__('os')") is None
    assert exact_calc("1/0") is None
    assert exact_calc("1 ** 2") is None


# --- three_scenario Decimal arithmetic smoke ------------------------------------

def test_three_scenario_decimal_arithmetic():
    res = three_scenario_valuation(
        current_price=100, current_eps=10, shares_billion=1,
        growth_optimistic=0.10, growth_neutral=0.05, growth_pessimistic=0.0,
        pe_optimistic=20, pe_neutral=15, pe_pessimistic=10,
        years=2,
    )
    assert len(res) == 3
    bull = res[0]
    assert bull["scenario"] == "Bull"
    assert bull["future_eps"] == Decimal("12.1")
    assert bull["target_price"] == Decimal("242.0")
    assert bull["change_pct"] == pytest.approx(142.0)


# --- verify_valuation ------------------------------------------------------------

def test_verify_valuation_ratios():
    res = verify_valuation(price=100, eps=10, bvps=50)
    assert res["PE"] == pytest.approx(10.0)
    assert res["PB"] == pytest.approx(2.0)
    assert res["ROE"] == pytest.approx(20.0)


# --- market cap 边界：精确命中 / 警告档（源 test_financial_rigor.py 的 pass/warn 路径） ----

def test_market_cap_exact_match_zero_deviation():
    res = verify_market_cap(8.00, 500000000, 4000000000, currency="CNY")
    assert res["calculated_cap"] == Decimal("4000000000.00")
    assert res["deviation_pct"] == 0.0
    assert res["is_valid"] is True


def test_market_cap_within_warn_band_still_valid():
    # 8.00 × 500,000,000 = 4,000,000,000；报告值放大 3% 命中源 CLI 的警告档（1%~5%）
    res = verify_market_cap(8.00, 500000000, 4120000000, currency="CNY")
    assert res["is_valid"] is True
    assert 1.0 < res["deviation_pct"] < 5.0
