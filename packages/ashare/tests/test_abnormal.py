"""B2 — 交易所异动偏离监控单测(纯离线,无网络/无库)。

口径对照 TSP backend/app/services/abnormal_moves.py:偏离值 = 个股 N 日累计
涨跌幅 − 指数同期涨跌幅;接近度 = |偏离| / 该方向阈值(≥1.0/≥0.7/≥0.5)。
"""

from __future__ import annotations

import pytest

from selene_space_ashare.abnormal import (
    CLOSENESS_EDGE,
    CLOSENESS_TRIGGERED,
    CLOSENESS_WATCH,
    STATUS_EDGE,
    STATUS_TRIGGERED,
    STATUS_WATCH,
    DeviationWindow,
    SymbolDeviation,
    align_by_date,
    bench_candidates_of,
    bench_key_of,
    bench_symbol_of,
    closeness_of,
    compose_live_deviation,
    cumulative_pct,
    deviation_pct,
    deviation_windows,
    evaluate_deviation,
    max_closeness,
    rule_for,
    status_of,
)


# ───────────────────────── 阈值表(板块口径) ─────────────────────────


class TestRuleThresholds:
    def test_main_board_symmetric_3d(self) -> None:
        rule = rule_for("600519.SH")
        assert rule.board == "main"
        assert rule.thresholds[3] == (0.20, 0.20)

    def test_gem_board_3d_is_30pct(self) -> None:
        rule = rule_for("300750.SZ")  # 宁德时代 = 创业板
        assert rule.board == "cyb"
        assert rule.thresholds[3] == (0.30, 0.30)

    def test_star_board_3d_is_30pct(self) -> None:
        rule = rule_for("688981.SH")
        assert rule.board == "kcb"
        assert rule.thresholds[3] == (0.30, 0.30)

    def test_bse_board_3d_is_40pct(self) -> None:
        rule = rule_for("920953.BJ")
        assert rule.board == "bse"
        assert rule.thresholds[3] == (0.40, 0.40)

    def test_severe_windows_asymmetric_down_stricter(self) -> None:
        # 10/30 日严重异动:负向阈值显著严于正向,各板块一致。
        for sym in ("600519.SH", "300750.SZ", "920953.BJ"):
            rule = rule_for(sym)
            assert rule.thresholds[10] == (1.00, 0.50)
            assert rule.thresholds[30] == (2.00, 0.70)
            assert rule.thresholds[10][1] < rule.thresholds[10][0]
            assert rule.thresholds[30][1] < rule.thresholds[30][0]

    def test_st_flag_does_not_change_thresholds(self) -> None:
        # 2026-07-06 起主板风险警示股同普通股标准;ST 仅作展示标记。
        plain = rule_for("600519.SH", "贵州茅台")
        st = rule_for("600519.SH", "ST 某股")
        assert st.st is True
        assert st.thresholds == plain.thresholds


# ───────────────────────── 基准指数映射 ─────────────────────────


class TestBenchMapping:
    def test_bench_keys(self) -> None:
        assert bench_key_of("600519.SH") == "SH"
        assert bench_key_of("688981.SH") == "STAR"
        assert bench_key_of("000001.SZ") == "SZ"
        assert bench_key_of("300750.SZ") == "GEM"
        assert bench_key_of("920953.BJ") == "BJ"

    def test_bse_by_code_prefix_without_bj_suffix(self) -> None:
        # 8/4/920 开头即使后缀缺失也判北交所(与 TSP code[:2] 判定一致)。
        assert bench_key_of("830799") == "BJ"

    def test_unknown_returns_empty(self) -> None:
        assert bench_key_of("BTC/USDT") == ""
        assert bench_symbol_of("BTC/USDT") is None
        assert bench_candidates_of("BTC/USDT") == ()

    def test_bench_symbol_resolves_to_tsp_preference(self) -> None:
        # 首选指数逐条对齐 TSP pipeline._BENCHMARK_PREFERENCE:
        # 创业板=创业板综指(399102,非创业板指 399006)、沪主板=上证A指(000002)。
        assert bench_symbol_of("300750.SZ") == "399102.SZ"
        assert bench_symbol_of("600519.SH") == "000002.SH"

    def test_bench_candidates_order_and_fallback(self) -> None:
        assert bench_candidates_of("600519.SH") == ("000002.SH", "000001.SH")
        assert bench_candidates_of("300750.SZ") == ("399102.SZ", "399107.SZ")
        assert bench_candidates_of("688981.SH") == ("000688.SH", "000002.SH")
        assert bench_candidates_of("920953.BJ") == ("899050.BJ", "000001.SH")


# ───────────────────────── 累计涨跌幅 / 偏离 ─────────────────────────


class TestCumulativePct:
    def test_simple_gain(self) -> None:
        # 3 日: 100 → 120 = +20%
        assert cumulative_pct([100.0, 110.0, 115.0, 120.0], 3) == pytest.approx(0.20)

    def test_insufficient_data_returns_none(self) -> None:
        # 需要 days+1 个点;2 个点算不出 3 日。
        assert cumulative_pct([100.0, 110.0], 3) is None

    def test_zero_base_returns_none(self) -> None:
        assert cumulative_pct([0.0, 1.0, 2.0, 3.0], 3) is None

    def test_exactly_days_plus_one_ok(self) -> None:
        assert cumulative_pct([100.0, 100.0, 105.0, 121.0], 3) == pytest.approx(0.21)

    def test_days_zero_invalid(self) -> None:
        assert cumulative_pct([100.0, 110.0], 0) is None


class TestDeviation:
    def test_stock_outruns_index(self) -> None:
        # 个股 3 日 +30%,指数 3 日 +10% → 偏离 +20%
        stock = [100.0, 110.0, 120.0, 130.0]
        index = [100.0, 103.0, 107.0, 110.0]
        assert deviation_pct(stock, index, 3) == pytest.approx(0.20, abs=1e-9)

    def test_index_missing_history_returns_none(self) -> None:
        assert deviation_pct([100.0, 110.0, 120.0, 130.0], [100.0], 3) is None

    def test_negative_deviation(self) -> None:
        stock = [100.0, 95.0, 90.0, 85.0]
        index = [100.0, 99.0, 98.0, 97.0]
        # −15% − (−3%) = −12%
        assert deviation_pct(stock, index, 3) == pytest.approx(-0.12, abs=1e-9)


# ───────────────────────── 接近度 + 状态 ─────────────────────────


class TestCloseness:
    def test_positive_uses_up_threshold(self) -> None:
        # 主板 3 日 +20% → 接近度 1.0(恰触发)
        assert closeness_of(0.20, {3: (0.20, 0.20)}, 3) == pytest.approx(1.0)

    def test_negative_uses_down_threshold(self) -> None:
        # 10 日 −50% → 接近度 1.0(负向阈值 0.50)
        assert closeness_of(-0.50, {10: (1.00, 0.50)}, 10) == pytest.approx(1.0)

    def test_partial_closeness(self) -> None:
        # 主板 3 日 +15% → 0.75(边缘)
        assert closeness_of(0.15, {3: (0.20, 0.20)}, 3) == pytest.approx(0.75)

    def test_unknown_window_returns_none(self) -> None:
        assert closeness_of(0.1, {3: (0.2, 0.2)}, 5) is None

    def test_status_bands(self) -> None:
        assert status_of(1.0) == STATUS_TRIGGERED
        assert status_of(1.5) == STATUS_TRIGGERED
        assert status_of(0.7) == STATUS_EDGE
        assert status_of(0.9) == STATUS_EDGE
        assert status_of(0.5) == STATUS_WATCH
        assert status_of(0.0) == STATUS_WATCH

    def test_band_constants_consistent(self) -> None:
        assert CLOSENESS_TRIGGERED == 1.0
        assert CLOSENESS_EDGE == 0.7
        assert CLOSENESS_WATCH == 0.5


# ───────────────────────── 逐窗口聚合 ─────────────────────────


def _flat(n: int, value: float) -> list[float]:
    return [value] * n


class TestDeviationWindows:
    def test_gem_3d_triggers(self) -> None:
        # 创业板 3 日偏离 +30% → 接近度 1.0
        stock = [100.0, 110.0, 120.0, 130.0]  # +30%
        index = _flat(4, 100.0)  # 0%
        wins = deviation_windows(stock, index, symbol="300750.SZ")
        d3 = next(w for w in wins if w.days == 3)
        assert d3.deviation == pytest.approx(0.30, abs=1e-9)
        assert d3.threshold == 0.30
        assert d3.closeness == pytest.approx(1.0)

    def test_insufficient_history_skips_window(self) -> None:
        # 只有 4 个点:3 日窗口可算,10/30 日跳过 —— 短序列绝不当信号。
        stock = [100.0, 101.0, 102.0, 103.0]
        index = _flat(4, 100.0)
        wins = deviation_windows(stock, index, symbol="600519.SH")
        assert [w.days for w in wins] == [3]

    def test_max_closeness_picks_largest(self) -> None:
        wins = [
            DeviationWindow(3, 0.10, 0.20, 0.5),
            DeviationWindow(10, 0.80, 1.00, 0.8),
            DeviationWindow(30, 0.30, 2.00, 0.15),
        ]
        assert max_closeness(wins) == pytest.approx(0.8)

    def test_max_closeness_empty_is_zero(self) -> None:
        assert max_closeness([]) == 0.0


# ───────────────────────── 盘中实时叠加 ─────────────────────────


class TestLiveComposition:
    def test_adds_today_when_absent(self) -> None:
        # 历史偏离 +20%,今日个股 +5%、指数 +1% → +24%
        got = compose_live_deviation(
            0.20, stock_rt_pct=0.05, index_rt_pct=0.01, includes_today=False
        )
        assert got == pytest.approx(0.24)

    def test_no_double_count_when_today_included(self) -> None:
        # 历史已含今日 → 不再叠加(否则把最近交易日算两遍)。
        got = compose_live_deviation(
            0.20, stock_rt_pct=0.05, index_rt_pct=0.01, includes_today=True
        )
        assert got == pytest.approx(0.20)

    def test_index_outruns_stock_reduces_deviation(self) -> None:
        got = compose_live_deviation(
            0.20, stock_rt_pct=0.00, index_rt_pct=0.03, includes_today=False
        )
        assert got == pytest.approx(0.17)


# ───────────────────────── 消费入口:日期对齐 + 评估 ─────────────────────────


class TestAlignByDate:
    def test_common_dates_only_ascending(self) -> None:
        stock = [("20260101", 10.0), ("20260102", 11.0), ("20260105", 12.0)]
        index = [("20260102", 100.0), ("20260105", 102.0), ("20260106", 103.0)]
        s, i = align_by_date(stock, index)
        # 0101 无指数、0106 无个股 → 交集只剩 0102/0105,升序。
        assert s == [11.0, 12.0]
        assert i == [100.0, 102.0]

    def test_suspension_day_not_backfilled(self) -> None:
        # 个股 0103 停牌(无 bar);若用最近价顶替会与指数错位 → 必须剔除。
        stock = [("20260102", 10.0), ("20260105", 12.0)]
        index = [("20260102", 100.0), ("20260103", 101.0), ("20260105", 103.0)]
        s, i = align_by_date(stock, index)
        assert len(s) == len(i) == 2
        assert s == [10.0, 12.0]

    def test_disjoint_is_empty(self) -> None:
        s, i = align_by_date([("20260101", 10.0)], [("20260201", 100.0)])
        assert s == [] and i == []


class TestEvaluateDeviation:
    def test_daily_path_matches_deviation_windows(self) -> None:
        # 个股 30 日翻倍(+100%),指数同期不动 → 3日偏离约 +100%,接近度 5.0。
        stock = [10.0, 10.0, 10.0, 20.0]
        index = [100.0, 100.0, 100.0, 100.0]
        got = evaluate_deviation(stock, index, symbol="600519.SH")
        assert isinstance(got, SymbolDeviation)
        assert got.intraday is False
        assert got.board == "main"
        w3 = next(w for w in got.windows if w.days == 3)
        assert w3.deviation == pytest.approx(1.0)
        assert w3.closeness == pytest.approx(5.0)
        assert got.status == STATUS_TRIGGERED

    def test_intraday_path_adds_today(self) -> None:
        # 历史 3 日偏离 +18%(接近度 0.9=边缘);今日个股 +5%、指数 -1% → +24% → 触发。
        stock = [10.0, 10.0, 10.0, 11.8]
        index = [100.0, 100.0, 100.0, 100.0]
        daily = evaluate_deviation(stock, index, symbol="600519.SH")
        w3_daily = next(w for w in daily.windows if w.days == 3)
        assert w3_daily.closeness == pytest.approx(0.9)
        assert daily.status == STATUS_EDGE

        live = evaluate_deviation(
            stock,
            index,
            symbol="600519.SH",
            stock_rt_pct=0.05,
            index_rt_pct=-0.01,
        )
        assert live.intraday is True
        w3_live = next(w for w in live.windows if w.days == 3)
        # 0.18 + (0.05 - (-0.01)) = 0.24 → 接近度 1.2
        assert w3_live.deviation == pytest.approx(0.24)
        assert w3_live.closeness == pytest.approx(1.2)
        assert live.status == STATUS_TRIGGERED

    def test_direction_flip_reselects_threshold(self) -> None:
        # 历史偏离 +8%(主板 3 日,接近度 0.4=观察);今日个股 -2%、指数 +10%
        # → 偏离翻负 -4% → 阈值改用负向(0.20),接近度 0.2。若仍用正向同值,
        # 这里数值相同,故取不对称的 10 日窗口验证方向翻转选阈值。
        stock = [10.0, 10.0, 10.0, 10.8]
        index = [100.0, 100.0, 100.0, 100.0]
        live = evaluate_deviation(
            stock,
            index,
            symbol="600519.SH",
            windows=(3,),
            stock_rt_pct=-0.02,
            index_rt_pct=0.10,
        )
        w3 = live.windows[0]
        # 0.08 + (-0.02 - 0.10) = -0.04 → 负向阈值 0.20 → 接近度 0.2
        assert w3.deviation == pytest.approx(-0.04)
        assert w3.threshold == pytest.approx(0.20)
        assert w3.closeness == pytest.approx(0.2)

    def test_insufficient_data_yields_no_window(self) -> None:
        # 只有 2 根收盘,3 日窗口需要 4 根 → 不产生假信号。
        got = evaluate_deviation([10.0, 11.0], [100.0, 100.0], symbol="600519.SH")
        assert got.windows == []
        assert got.max_closeness == 0.0
        assert got.status == STATUS_WATCH

    def test_board_routed_threshold_in_result(self) -> None:
        # 创业板用 0.30 阈值:同样 +18% 偏离在主板=0.9(边缘)、创业板=0.6(观察)。
        stock = [10.0, 10.0, 10.0, 11.8]
        index = [100.0, 100.0, 100.0, 100.0]
        cyb = evaluate_deviation(stock, index, symbol="300750.SZ")
        w3 = next(w for w in cyb.windows if w.days == 3)
        assert w3.threshold == pytest.approx(0.30)
        assert w3.closeness == pytest.approx(0.6)
        assert cyb.status == STATUS_WATCH
