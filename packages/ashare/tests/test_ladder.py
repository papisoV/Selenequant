"""Tests for the limit-up ladder structure layer (pandas port of TSP ladder).

All synthetic — zero network.
"""

from __future__ import annotations

import pandas as pd
import pytest

from selene_space_ashare.ladder import (
    PROMO_MIN_POOL,
    attach_ladder_promo,
    attach_limit_flags,
    build_ladder_daily,
    build_seal_rate_daily,
    compute_consecutive_limit_ups,
    decode_high_days_value,
    ladder_completeness,
    ladder_daily_panel,
    ladder_history_from_pool,
    run_length,
)


def _frame(rows: list[tuple[str, str, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["symbol", "date", "consecutive_boards"])


class TestBuildLadderDaily:
    def test_basic_single_day(self) -> None:
        # 1 板 ×2, 2 板 ×1, 4 板 ×1  -> first=2, ge2=2, ge3=1, ge5=0,
        # rungs_filled = distinct levels >= 2 = {2,4} = 2, max=4
        df = _frame(
            [
                ("600001", "20240101", 1),
                ("600002", "20240101", 1),
                ("600003", "20240101", 2),
                ("600004", "20240101", 4),
            ]
        )
        out = build_ladder_daily(df)
        assert len(out) == 1
        r = out.iloc[0]
        assert r["first_board"] == 2
        assert r["ge2_count"] == 2
        assert r["ge3_count"] == 1
        assert r["ge5_count"] == 0
        assert r["rungs_filled"] == 2
        assert r["max_consecutive"] == 4

    def test_multiday_sorted(self) -> None:
        df = _frame(
            [
                ("600001", "20240102", 3),
                ("600001", "20240101", 1),
                ("600002", "20240101", 1),
            ]
        )
        out = build_ladder_daily(df)
        assert list(out["date"]) == ["20240101", "20240102"]
        assert out.iloc[0]["first_board"] == 2
        assert out.iloc[1]["max_consecutive"] == 3

    def test_ge3_ge5_boundaries(self) -> None:
        df = _frame(
            [
                ("a", "d1", 2),
                ("b", "d1", 3),
                ("c", "d1", 5),
                ("d", "d1", 6),
            ]
        )
        out = build_ladder_daily(df)
        r = out.iloc[0]
        assert r["ge2_count"] == 4
        assert r["ge3_count"] == 3  # 3,5,6
        assert r["ge5_count"] == 2  # 5,6

    def test_empty_frame(self) -> None:
        out = build_ladder_daily(pd.DataFrame(columns=["date", "consecutive_boards"]))
        assert out.empty
        assert "first_board" in out.columns


class TestAttachLadderPromo:
    def test_promotion_by_one_counts(self) -> None:
        # yesterday 2 板 -> today 3 板 = promoted
        df = _frame([("600001", "20240101", 2), ("600001", "20240102", 3)])
        out = attach_ladder_promo(df).sort_values("date").reset_index(drop=True)
        assert int(out.iloc[0]["promo_pool"]) == 0  # first row, no prev
        assert int(out.iloc[1]["promo_pool"]) == 1
        assert int(out.iloc[1]["promo_ok"]) == 1

    def test_broken_board_not_promoted(self) -> None:
        # yesterday 2 板 -> today 0 (fell out) = in pool, not promoted
        df = _frame([("600001", "20240101", 2), ("600001", "20240102", 0)])
        out = attach_ladder_promo(df).sort_values("date").reset_index(drop=True)
        assert int(out.iloc[1]["promo_pool"]) == 1
        assert int(out.iloc[1]["promo_ok"]) == 0

    def test_jump_more_than_one_not_promoted(self) -> None:
        # 2 -> 4 overshoots; promotion requires exactly +1
        df = _frame([("600001", "20240101", 2), ("600001", "20240102", 4)])
        out = attach_ladder_promo(df).sort_values("date").reset_index(drop=True)
        assert int(out.iloc[1]["promo_ok"]) == 0

    def test_per_symbol_isolation(self) -> None:
        # two symbols interleaved must not leak prev across symbols
        df = _frame(
            [
                ("600001", "20240101", 2),
                ("600002", "20240101", 0),
                ("600001", "20240102", 3),
                ("600002", "20240102", 1),
            ]
        )
        out = (
            attach_ladder_promo(df)
            .sort_values(["symbol", "date"])
            .reset_index(drop=True)
        )
        a2 = out[(out["symbol"] == "600001") & (out["date"] == "20240102")].iloc[0]
        b2 = out[(out["symbol"] == "600002") & (out["date"] == "20240102")].iloc[0]
        assert int(a2["promo_ok"]) == 1  # 2 -> 3
        assert int(b2["promo_pool"]) == 0  # prev day was 0, not in pool
        assert int(b2["promo_ok"]) == 0

    def test_first_row_prev_is_nan(self) -> None:
        df = _frame([("600001", "20240101", 5)])
        out = attach_ladder_promo(df)
        assert pd.isna(out.iloc[0]["_prev_consec"])
        assert int(out.iloc[0]["promo_pool"]) == 0

    def test_missing_prev_not_counted(self) -> None:
        # A NaN prev must never register as a promotion candidate
        df = _frame([("600001", "20240101", 0), ("600001", "20240102", 1)])
        out = attach_ladder_promo(df).sort_values("date").reset_index(drop=True)
        assert int(out.iloc[0]["promo_pool"]) == 0
        # prev was 0 (< 1) -> not in yesterday's board -> not promoted
        assert int(out.iloc[1]["promo_pool"]) == 0


class TestLadderCompleteness:
    def test_height_lt_3_is_zero(self) -> None:
        assert ladder_completeness(2, 1) == 0.0
        assert ladder_completeness(0, 0) == 0.0
        assert ladder_completeness(1, 0) == 0.0

    def test_full_ladder(self) -> None:
        # height 3 -> rungs 2..3 = 2 slots; both filled -> 2/2 = 1.0
        assert ladder_completeness(3, 2) == 1.0

    def test_partial_ladder(self) -> None:
        # height 5 -> 4 slots (2,3,4,5); 2 filled -> 0.5
        assert ladder_completeness(5, 2) == 0.5

    def test_no_div_by_zero(self) -> None:
        # must not raise for degenerate heights
        assert ladder_completeness(3, 0) == 0.0


class TestComputeConsecutiveLimitUps:
    def test_run_of_limit_ups(self) -> None:
        # 10.0 -> 11.0 -> 12.1 -> 12.1(not limit) -> 13.31
        # ratio 0.10 main board; limit_up_price rounds half-up to cent
        df = pd.DataFrame(
            {
                "date": ["d0", "d1", "d2", "d3", "d4"],
                "close": [10.00, 11.00, 12.10, 12.10, 13.31],
            }
        )
        out = compute_consecutive_limit_ups(df, code="600001", name="测试")
        # d0: no prev -> 0; d1: 11.00 >= 11.00 -> 1; d2: 12.10 >= 12.10 -> 2
        # d3: prev 12.10, limit 13.31, close 12.10 -> break -> 0
        # d4: 13.31 >= 13.31 -> 1
        assert list(out["consecutive_boards"]) == [0, 1, 2, 0, 1]

    def test_missing_prev_close_resets_to_zero(self) -> None:
        # mirrors TSP fill_null(False): a NaN prev close means no limit-up
        df = pd.DataFrame({"date": ["d0", "d1", "d2"], "close": [10.00, None, 11.00]})
        out = compute_consecutive_limit_ups(df, code="600001", name="测试")
        # d1 NaN close -> False (reset); d2 prev is NaN -> False (reset)
        assert list(out["consecutive_boards"]) == [0, 0, 0]

    def test_st_five_percent_limit(self) -> None:
        # ST name -> 5% limit; 10.50 from 10.00 is at limit for ST
        df = pd.DataFrame({"date": ["d0", "d1"], "close": [10.00, 10.50]})
        out = compute_consecutive_limit_ups(df, code="600001", name="ST测试")
        assert list(out["consecutive_boards"]) == [0, 1]

    def test_non_limit_close_is_zero(self) -> None:
        df = pd.DataFrame({"date": ["d0", "d1"], "close": [10.00, 10.50]})
        out = compute_consecutive_limit_ups(df, code="600001", name="测试")
        # main board needs 11.00; 10.50 is below -> 0
        assert list(out["consecutive_boards"]) == [0, 0]


class TestPromoMinPoolConstant:
    def test_pool_threshold_value(self) -> None:
        assert PROMO_MIN_POOL == 10


class TestRunLength:
    """The shared fold that both `compute_consecutive_limit_ups` and the
    backfill script rely on: reset-on-break, count consecutive truthy."""

    def test_resets_on_false(self) -> None:
        assert run_length([True, True, False, True]) == [1, 2, 0, 1]

    def test_all_false(self) -> None:
        assert run_length([False, False]) == [0, 0]

    def test_empty(self) -> None:
        assert run_length([]) == []

    def test_truthy_non_bool_counts(self) -> None:
        # backfill passes a pandas Series; truthiness, not identity, decides
        # the count (1/0 stand in for numpy.bool_ or object dtype).
        assert run_length(pd.Series([1, 0, 1, 1])) == [1, 0, 1, 2]

    def test_accepts_series(self) -> None:
        s = pd.Series([True, False, True, True])
        assert run_length(s) == [1, 0, 1, 2]


class TestLadderDailyPanel:
    def test_columns_and_shape(self) -> None:
        df = _frame(
            [
                ("600001", "20240101", 1),
                ("600002", "20240101", 3),
                ("600001", "20240102", 2),
                ("600002", "20240102", 4),
            ]
        )
        out = ladder_daily_panel(df)
        assert list(out["date"]) == ["20240101", "20240102"]
        for col in (
            "first_board",
            "ge2_count",
            "ge3_count",
            "ge5_count",
            "rungs_filled",
            "max_consecutive",
            "promo_pool",
            "promo_ok",
            "promo_rate",
            "ladder_completeness",
        ):
            assert col in out.columns

    def test_small_pool_promo_rate_is_none(self) -> None:
        # pool of 2 < PROMO_MIN_POOL(10) -> promo_rate None, not a noisy ratio
        df = _frame([("600001", "20240101", 2), ("600001", "20240102", 3)])
        out = ladder_daily_panel(df)
        assert out.iloc[1]["promo_rate"] is None

    def test_large_pool_promo_rate_computed(self) -> None:
        # 12 names board on d1; all 12 promote to 2 板 on d2 -> rate 1.0
        rows = [(f"60{i:04d}", "20240101", 1) for i in range(12)]
        rows += [(f"60{i:04d}", "20240102", 2) for i in range(12)]
        out = ladder_daily_panel(_frame(rows))
        d2 = out[out["date"] == "20240102"].iloc[0]
        assert d2["promo_pool"] == 12
        assert d2["promo_ok"] == 12
        assert d2["promo_rate"] == 1.0

    def test_completeness_column_populated(self) -> None:
        # d1 height 5 with rungs {2,3,5} -> rungs_filled 3 -> 3/(5-1)=0.75
        rows = [
            ("a", "d1", 2),
            ("b", "d1", 3),
            ("c", "d1", 5),
        ]
        out = ladder_daily_panel(_frame(rows))
        assert out.iloc[0]["ladder_completeness"] == 0.75

    def test_empty_frame_roundtrip(self) -> None:
        out = ladder_daily_panel(
            pd.DataFrame(columns=["symbol", "date", "consecutive_boards"])
        )
        assert out.empty
        assert "promo_rate" in out.columns

    def test_does_not_mutate_input(self) -> None:
        df = _frame([("600001", "20240101", 2), ("600001", "20240102", 3)])
        before = df.copy()
        ladder_daily_panel(df)
        pd.testing.assert_frame_equal(df, before)

    def test_pool_boundary_nine_none_ten_value(self) -> None:
        # pool == 9 (< PROMO_MIN_POOL) -> None; pool == 10 -> numeric ratio
        nine = [(f"60{i:04d}", "d1", 1) for i in range(9)]
        nine += [(f"60{i:04d}", "d2", 2) for i in range(9)]
        out9 = ladder_daily_panel(_frame(nine))
        assert out9[out9["date"] == "d2"].iloc[0]["promo_rate"] is None

        ten = [(f"60{i:04d}", "d1", 1) for i in range(10)]
        ten += [(f"60{i:04d}", "d2", 2) for i in range(10)]
        out10 = ladder_daily_panel(_frame(ten))
        assert out10[out10["date"] == "d2"].iloc[0]["promo_rate"] == 1.0


class TestAttributionHeader:
    def test_module_header_has_mit_attribution(self) -> None:
        import inspect

        import selene_space_ashare.ladder as mod

        src = inspect.getsource(mod)
        assert "Absorbed from" in src
        assert "MIT" in src
        assert "Copyright" in src
        assert "Source:" in src


class TestAttachLimitFlags:
    def test_sealed_day(self) -> None:
        # 10.00 -> 11.00 is the exact 10% limit -> sealed
        df = pd.DataFrame(
            {"date": ["d0", "d1"], "close": [10.00, 11.00], "high": [10.10, 11.00]}
        )
        out = attach_limit_flags(df, code="600001", name="测试")
        assert list(out["is_limit_up"]) == [False, True]
        assert list(out["touched_limit"]) == [False, True]

    def test_touched_not_sealed_is_broken_board(self) -> None:
        # high touches 11.00 (limit) but close falls back to 10.60 -> 炸板
        df = pd.DataFrame(
            {"date": ["d0", "d1"], "close": [10.00, 10.60], "high": [10.10, 11.00]}
        )
        out = attach_limit_flags(df, code="600001", name="测试")
        assert list(out["is_limit_up"]) == [False, False]
        assert list(out["touched_limit"]) == [False, True]

    def test_missing_prev_close_both_false(self) -> None:
        df = pd.DataFrame(
            {"date": ["d0", "d1"], "close": [None, 11.00], "high": [None, 11.00]}
        )
        out = attach_limit_flags(df, code="600001", name="测试")
        assert list(out["is_limit_up"]) == [False, False]
        assert list(out["touched_limit"]) == [False, False]

    def test_no_high_column_falls_back_to_close(self) -> None:
        df = pd.DataFrame({"date": ["d0", "d1"], "close": [10.00, 11.00]})
        out = attach_limit_flags(df, code="600001", name="测试")
        assert list(out["touched_limit"]) == [False, True]


class TestBuildSealRateDaily:
    def test_seal_rate_fraction(self) -> None:
        # 2 of 4 limit-touching names held -> 0.5
        df = pd.DataFrame(
            {
                "date": ["d1", "d1", "d1", "d1"],
                "is_limit_up": [True, True, False, False],
                "touched_limit": [True, True, True, True],
            }
        )
        out = build_seal_rate_daily(df)
        assert out.iloc[0]["limit_up_count"] == 2
        assert out.iloc[0]["touched_count"] == 4
        assert out.iloc[0]["seal_rate"] == 0.5

    def test_no_touch_records_none(self) -> None:
        df = pd.DataFrame(
            {
                "date": ["d1", "d1"],
                "is_limit_up": [False, False],
                "touched_limit": [False, False],
            }
        )
        out = build_seal_rate_daily(df)
        assert out.iloc[0]["seal_rate"] is None

    def test_empty_frame(self) -> None:
        out = build_seal_rate_daily(
            pd.DataFrame(columns=["date", "is_limit_up", "touched_limit"])
        )
        assert out.empty
        assert "seal_rate" in out.columns

    def test_multiday_sorted(self) -> None:
        df = pd.DataFrame(
            {
                "date": ["d2", "d1", "d1"],
                "is_limit_up": [True, True, False],
                "touched_limit": [True, True, True],
            }
        )
        out = build_seal_rate_daily(df)
        assert list(out["date"]) == ["d1", "d2"]
        assert out.iloc[1]["seal_rate"] == 1.0


# ─────────────────── 10jqka 端点解码(纯函数,零网络) ───────────────────


class TestDecodeHighDaysValue:
    """The packed pool field is ``(M << 16) | N`` = ``N天M板`` — never a run."""

    def test_first_board(self) -> None:
        assert decode_high_days_value(65537) == 1  # 0x1_0001 = 首板

    def test_three_day_three_board(self) -> None:
        assert decode_high_days_value(196611) == 3  # 0x3_0003 = 3天3板

    def test_lookback_window_is_dropped(self) -> None:
        # 19天11板 -> M=11; the 19 is the window, not a board count.
        assert decode_high_days_value(720915) == 11

    def test_none_and_garbage_are_zero(self) -> None:
        assert decode_high_days_value(None) == 0
        assert decode_high_days_value("not-a-number") == 0

    def test_is_not_the_consecutive_run(self) -> None:
        # The whole reason `ladder_history_from_pool` exists: 601811 on
        # 2026-10-12 was "11天8板" -> M=8, while its true consecutive run
        # (continue_num) was 2. Decoding M as a run would over-count it 6x.
        assert decode_high_days_value((8 << 16) | 11) == 8


class TestLadderHistoryFromPool:
    def _pool(self, codes: list[str]) -> pd.DataFrame:
        return pd.DataFrame({"code": codes, "date": ["20261014"] * len(codes)})

    def _cont(self, pairs: list[tuple[str, int]]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "code": [c for c, _ in pairs],
                "consecutive_boards": [b for _, b in pairs],
                "date": ["20261014"] * len(pairs),
            }
        )

    def test_absent_from_continuous_is_first_board(self) -> None:
        pool = self._pool(["600001", "600002", "600003"])
        cont = self._cont([("600001", 3)])
        out = ladder_history_from_pool(pool, cont)
        got = dict(zip(out["symbol"], out["consecutive_boards"]))
        assert got == {"600001": 3, "600002": 1, "600003": 1}

    def test_continuous_count_wins_over_pool_membership(self) -> None:
        # A code in BOTH endpoints must take the continuous count, never 1.
        pool = self._pool(["600001", "600002"])
        cont = self._cont([("600001", 5)])
        out = ladder_history_from_pool(pool, cont)
        assert out.loc[out["symbol"] == "600001", "consecutive_boards"].iloc[0] == 5

    def test_empty_continuous_makes_everything_a_first_board(self) -> None:
        pool = self._pool(["600001", "600002"])
        out = ladder_history_from_pool(pool, pd.DataFrame())
        assert set(out["consecutive_boards"]) == {1}
        assert len(out) == 2

    def test_empty_pool_and_continuous_is_empty_frame(self) -> None:
        out = ladder_history_from_pool(pd.DataFrame(), pd.DataFrame())
        assert out.empty
        assert list(out.columns) == ["symbol", "date", "consecutive_boards"]

    def test_date_carried_from_the_frames(self) -> None:
        out = ladder_history_from_pool(self._pool(["600001"]), pd.DataFrame())
        assert set(out["date"]) == {"20261014"}

    def test_output_is_densifiable_baseline_shape(self) -> None:
        # The consumer (live_ladder_row via densify) requires exactly these
        # columns; a rename here would silently break the run continuation.
        out = ladder_history_from_pool(self._pool(["600001"]), pd.DataFrame())
        assert list(out.columns) == ["symbol", "date", "consecutive_boards"]


# ───────────────────────── 网络冒烟(默认不跑) ─────────────────────────


@pytest.mark.network
def test_real_tencent_backfill_smoke() -> None:
    """Real Tencent pull through the backfill path: klines -> ladder columns.

    Marked ``network`` (excluded by the default ``-m "not network"`` addopts),
    run with ``pytest -m network``. Exercises the same callable the backfill
    uses (``ak.stock_zh_a_hist_tx`` via ``_fetch_with_retry``) so the unadjusted
    close that the limit-up rule depends on is verified end-to-end, not just
    against synthetic frames.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from backfill_ladder_panel import build_panel, fetch_one  # type: ignore

    # 600519 (贵州茅台) — liquid, long history, never ST: a stable smoke target.
    frame = fetch_one("600519", "贵州茅台", "20240924", "20241011")
    assert frame is not None, "Tencent fetch returned nothing"
    assert not frame.empty
    for col in ("date", "is_limit_up", "touched_limit", "consecutive_boards"):
        assert col in frame.columns
    # consecutive_boards is a non-negative int run length
    assert frame["consecutive_boards"].min() >= 0
    assert frame["consecutive_boards"].dtype.kind in "iu"

    panel = build_panel([frame])
    for col in (
        "date",
        "first_board",
        "ge2_count",
        "max_consecutive",
        "promo_rate",
        "seal_rate",
    ):
        assert col in panel.columns
