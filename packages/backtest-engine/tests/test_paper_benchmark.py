"""Tests for the paper benchmark registry and the paper-vs-local report.

Covers: full README table ingestion (>=60 entries), spot-checked paper Sharpe
values, selene-template aliasing, the comparison verdict logic (MEETS / BELOW /
overfit-FAIL), tolerance handling, and text rendering.
"""

from __future__ import annotations

import pytest

from selene_space_backtest import (
    PAPER_BENCHMARKS,
    SELENE_ALIASES,
    all_benchmarks,
    compare_to_paper,
    get_benchmark,
    get_by_selene,
    render_benchmark_report,
)
from selene_space_backtest.overfit.report import OverfitReport


def _overfit(passed: bool) -> OverfitReport:
    return OverfitReport(
        verdict="PASS - survives multiple-testing correction" if passed else "FAIL - likely overfit",
        passed=passed,
        observed_sharpe_annual=0.6,
        skew=0.0,
        kurtosis=3.0,
        n_obs=252,
        n_trials=5,
        deflated_sharpe_ratio=0.6,
        deflation_benchmark_sr0_annual=0.3,
        psr_vs_zero=0.99,
        haircut_sharpe_annual=0.5,
        haircut_pct=0.2,
        minimum_track_record_length=50.0,
        pbo=None,
    )


class TestRegistry:
    def test_full_table_ingested(self) -> None:
        assert len(PAPER_BENCHMARKS) >= 60, "README strategy table has 60+ rows"

    def test_all_keys_unique_and_titled(self) -> None:
        keys = list(PAPER_BENCHMARKS)
        assert len(keys) == len(set(keys))
        for key, bm in PAPER_BENCHMARKS.items():
            assert bm.title, f"{key} has empty title"
            assert bm.paper_url.startswith("http"), f"{key} has bad URL"

    def test_paper_sharpe_spot_checks(self) -> None:
        expected = {
            "ts_momentum": 0.576,
            "overnight_bitcoin": 0.892,
            "rebalancing_premium": 0.698,
            "january_barometer": 0.365,
        }
        for key, sharpe in expected.items():
            assert PAPER_BENCHMARKS[key].sharpe == pytest.approx(sharpe)

    def test_volatility_stored_as_decimal(self) -> None:
        assert PAPER_BENCHMARKS["ts_momentum"].volatility == pytest.approx(0.205)
        assert PAPER_BENCHMARKS["rebalancing_premium"].volatility == pytest.approx(0.275)

    def test_na_entries_have_none_sharpe(self) -> None:
        assert PAPER_BENCHMARKS["esg_stochastic_optimization"].sharpe is None
        assert PAPER_BENCHMARKS["similarity_filings"].implementation is None

    def test_lookup_apis(self) -> None:
        assert get_benchmark("ts_momentum").title == "Time Series Momentum Effect"
        assert get_benchmark("does_not_exist") is None
        assert all_benchmarks() == PAPER_BENCHMARKS

    def test_selene_aliases_resolve(self) -> None:
        mapping = {
            "tsmom_vol_target": "ts_momentum",
            "intraday_seasonality": "overnight_bitcoin",
            "rebalance_premium": "rebalancing_premium",
            "january_barometer": "january_barometer",
        }
        for selene_key, paper_key in mapping.items():
            assert SELENE_ALIASES[selene_key] == paper_key
            assert get_by_selene(selene_key) is PAPER_BENCHMARKS[paper_key]

    def test_unmapped_selene_key(self) -> None:
        assert get_by_selene("not_a_template") is None


class TestComparison:
    def test_no_benchmark_is_info(self) -> None:
        comp = compare_to_paper("not_a_template", 1.0)
        assert comp.verdict.startswith("INFO")
        assert comp.meets_benchmark is None

    def test_meets_benchmark_passes(self) -> None:
        comp = compare_to_paper("tsmom_vol_target", 0.60)
        assert comp.paper_sharpe == pytest.approx(0.576)
        assert comp.meets_benchmark is True
        assert comp.verdict.startswith("PASS")
        assert "MEETS" in comp.verdict

    def test_below_benchmark_warns(self) -> None:
        comp = compare_to_paper("tsmom_vol_target", 0.30)
        assert comp.meets_benchmark is False
        assert comp.verdict.startswith("WARN")
        assert "BELOW" in comp.verdict

    def test_tolerance_lets_close_value_pass(self) -> None:
        # 0.56 is below 0.576, but within tolerance 0.05 -> MEETS
        comp = compare_to_paper("tsmom_vol_target", 0.56, tolerance=0.05)
        assert comp.meets_benchmark is True

    def test_overfit_fail_flips_verdict(self) -> None:
        comp = compare_to_paper("tsmom_vol_target", 0.60, overfit=_overfit(passed=False))
        assert comp.overfit_passed is False
        assert comp.verdict.startswith("FAIL")
        assert "overfit" in comp.verdict

    def test_overfit_pass_keeps_meets_verdict(self) -> None:
        comp = compare_to_paper("tsmom_vol_target", 0.60, overfit=_overfit(passed=True))
        assert comp.overfit_passed is True
        assert comp.verdict.startswith("PASS")

    def test_sharpe_gap_signed(self) -> None:
        comp = compare_to_paper("tsmom_vol_target", 0.70)
        assert comp.sharpe_gap == pytest.approx(0.124, abs=1e-9)

    def test_to_dict_serializable(self) -> None:
        comp = compare_to_paper("tsmom_vol_target", 0.60)
        d = comp.to_dict()
        assert d["local_sharpe_annual"] == 0.60
        assert d["paper_sharpe"] == 0.576

    def test_render_text_block(self) -> None:
        comp = compare_to_paper("tsmom_vol_target", 0.60, overfit=_overfit(passed=True))
        text = render_benchmark_report(comp, overfit=_overfit(passed=True))
        assert "PAPER BENCHMARK REPORT" in text
        assert "Time Series Momentum Effect" in text
        assert "PASS" in text
