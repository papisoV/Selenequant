"""setup 回测模拟器单测。

覆盖验收标准：
- 已知 entry 触发 N 个 setup，SL/TP 触发计数与胜率正确
- gap-through-entry（open 穿过 entry）
- 同 bar 内 SL/TP 都触达 → 保守解析（entry 侧优先记亏损）
- 无触发 → total_setups=0 + reason
- 无效参数（SL==TP / 方向矛盾）→ 抛 SetupBacktestError
- 所有 float 输出有限（无 inf/nan，防 -Infinity 序列化）
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from selene_space_market_analysis.setup_backtest import (
    SetupBacktestError,
    SetupBacktestResult,
    run_setup_backtest,
)

# 模拟逻辑单测用小块合成数据，跳过 bar 数守卫（该守卫由 test_insufficient_candles_empty 覆盖）
_SIM_MIN_CANDLES = 2


def _klines(rows: list[tuple[int, float, float, float, float]]) -> pd.DataFrame:
    """(timestamp, open, high, low, close) → K 线 DataFrame。"""
    return pd.DataFrame(
        {
            "timestamp": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[2] for r in rows],
            "low": [r[3] for r in rows],
            "close": [r[4] for r in rows],
        }
    )


def _assert_finite_float(value: float | int | None) -> None:
    assert value is None or isinstance(value, int) or math.isfinite(float(value))


def _assert_result_finite(result: SetupBacktestResult) -> None:
    d = result.to_dict()
    for key in (
        "win_rate",
        "avg_win",
        "avg_loss",
        "expectancy",
        "profit_factor",
        "max_drawdown",
        "avg_bars_held",
    ):
        _assert_finite_float(d[key])
    for bucket in d["rr_buckets"].values():
        for v in bucket.values():
            _assert_finite_float(v)
    for trade in d["trades"]:
        _assert_finite_float(trade["pnl_r"])
        _assert_finite_float(trade["reward_risk_ratio"])


# ---------------------------------------------------------------------------
# 基本：已知 entry 触发 N 个 setup，SL/TP 计数与胜率
# ---------------------------------------------------------------------------


def test_long_setup_win_loss_counts_and_metrics() -> None:
    """Long entry=100 SL=95 TP=110（RR=2）：1 胜 1 负。"""
    df = _klines(
        [
            (1, 102, 105, 100, 104),  # trigger，未收敛
            (2, 104, 112, 103, 111),  # high>=110 → WIN
            (3, 108, 109, 101, 107),  # 无触发
            (4, 106, 108, 98, 100),  # trigger，未收敛
            (5, 100, 102, 94, 96),  # low<=95 → LOSS
        ]
    )
    res = run_setup_backtest(
        df, 100, 95, 110, direction="long", min_candles=_SIM_MIN_CANDLES
    )

    assert res.total_setups == 2
    assert [t["outcome"] for t in res.trades] == ["win", "loss"]
    assert res.win_rate == pytest.approx(0.5)
    assert res.avg_win == pytest.approx(2.0)
    assert res.avg_loss == pytest.approx(-1.0)
    assert res.expectancy == pytest.approx(0.5)
    assert res.profit_factor == pytest.approx(2.0)
    assert res.avg_bars_held == pytest.approx(1.0)  # 触发 bar 不算持仓，两笔各 1 根解析
    assert res.max_drawdown == pytest.approx(1.0)
    assert res.no_trigger is False
    assert res.reason is None
    assert "2" in res.rr_buckets  # RR=2.0 分桶键
    assert res.rr_buckets["2"]["count"] == 2
    assert res.rr_buckets["2"]["wins"] == 1
    _assert_result_finite(res)


def test_trade_entry_exit_timestamps() -> None:
    df = _klines(
        [
            (1, 102, 105, 100, 104),
            (2, 104, 112, 103, 111),  # WIN
        ]
    )
    res = run_setup_backtest(
        df, 100, 95, 110, direction="long", min_candles=_SIM_MIN_CANDLES
    )
    assert res.total_setups == 1
    trade = res.trades[0]
    assert trade["entry_timestamp"] == 1
    assert trade["exit_timestamp"] == 2
    assert trade["bars_held"] == 1  # 触发 bar1 不算持仓，bar2 解析
    assert trade["entry_price"] == 100
    assert trade["stop_loss_price"] == 95
    assert trade["take_profit_price"] == 110
    assert trade["reward_risk_ratio"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# gap-through-entry（open 穿过 entry）
# ---------------------------------------------------------------------------


def test_gap_through_entry_rejects_close_below() -> None:
    """Long：open gap 破位到 entry 下方但收盘未收回 → 不触发（非确认信号）。"""
    df = _klines(
        [
            (1, 102, 105, 100, 104),  # trigger（low=100<=100 且 close=104>=100），未收敛
            (2, 104, 112, 103, 111),  # high>=110 → WIN
            (3, 110, 110, 108, 109),  # 无触发（low=108>100）
            (4, 97, 99, 90, 91),  # open=97 gap 破位，close=91<100 → 收盘未收回 → 不触发
        ]
    )
    res = run_setup_backtest(
        df, 100, 95, 110, direction="long", min_candles=_SIM_MIN_CANDLES
    )

    assert res.total_setups == 1  # 只有 bar1 触发
    assert [t["outcome"] for t in res.trades] == ["win"]
    _assert_result_finite(res)


def test_gap_reject_then_close_recover_triggers() -> None:
    """Long：gap 破位后一根收盘收回 entry 上方 → 该根触发，下一根解析 SL/TP。"""
    df = _klines(
        [
            (1, 102, 105, 100, 104),  # 前一个 setup 触发点（未收敛）
            (2, 104, 112, 103, 111),  # high>=110 → WIN（bar1 的 setup 收敛）
            (3, 110, 110, 108, 109),  # 无触发
            (4, 97, 99, 96, 94),  # gap 破位，close=94<100 → 不触发
            (5, 95, 106, 94, 103),  # low=94<=100 且 close=103>=100 → pin-bar 回踩触发
            (6, 103, 111, 102, 110),  # high>=110 → WIN
        ]
    )
    res = run_setup_backtest(
        df, 100, 95, 110, direction="long", min_candles=_SIM_MIN_CANDLES
    )

    assert res.total_setups == 2
    assert [t["outcome"] for t in res.trades] == ["win", "win"]
    assert res.trades[1]["entry_timestamp"] == 5
    assert res.trades[1]["exit_timestamp"] == 6
    assert res.trades[1]["bars_held"] == 1  # 触发 bar 不算持仓，bar6 解析
    _assert_result_finite(res)


# ---------------------------------------------------------------------------
# 同 bar SL/TP 都触达 → 保守解析
# ---------------------------------------------------------------------------


def test_same_bar_both_sl_tp_conservative_loss() -> None:
    """Long：bar 内 low<=SL 且 high>=TP，保守记亏损（SL 先查）。"""
    df = _klines(
        [
            (1, 101, 104, 98, 103),  # trigger，未收敛
            (2, 99, 108, 94, 100),  # low<=95(SL) 且 high>=105(TP) → 保守 LOSS
            (3, 101, 103, 99, 102),  # trigger，未收敛
            (4, 102, 107, 101, 106),  # high>=105 → WIN
        ]
    )
    res = run_setup_backtest(
        df, 100, 95, 105, direction="long", min_candles=_SIM_MIN_CANDLES
    )

    assert res.total_setups == 2
    assert [t["outcome"] for t in res.trades] == ["loss", "win"]
    assert res.trades[0]["exit_timestamp"] == 2
    assert res.trades[0]["bars_held"] == 1  # 触发 bar1 不算持仓，bar2 解析
    assert res.win_rate == pytest.approx(0.5)
    _assert_result_finite(res)


def test_same_bar_short_conservative_loss() -> None:
    """Short：bar 内 high>=SL 且 low<=TP，保守记亏损（SL 先查）。"""
    df = _klines(
        [
            (1, 98, 100, 97, 99),  # high=100>=entry → trigger，未收敛
            (2, 100, 107, 93, 96),  # high>=105(SL) 且 low<=95(TP) → 保守 LOSS
        ]
    )
    res = run_setup_backtest(
        df, 100, 105, 95, direction="short", min_candles=_SIM_MIN_CANDLES
    )

    assert res.total_setups == 1
    assert res.trades[0]["outcome"] == "loss"
    _assert_result_finite(res)


# ---------------------------------------------------------------------------
# 无触发 / 数据不足
# ---------------------------------------------------------------------------


def test_no_trigger_empty_result() -> None:
    df = _klines(
        [(i, 112, 118, 110, 115) for i in range(1, 31)]  # 全部 low>100
    )
    res = run_setup_backtest(df, 100, 90, 120, direction="long")

    assert res.total_setups == 0
    assert res.no_trigger is True
    assert res.reason is not None
    assert "未触发" in res.reason
    assert res.trades == []
    assert res.win_rate == 0.0
    assert res.profit_factor is None
    _assert_result_finite(res)


def test_insufficient_candles_empty() -> None:
    df = _klines([(1, 99, 101, 98, 100)] * 5)  # 5 根 < min_candles
    res = run_setup_backtest(df, 100, 95, 110, direction="long")

    assert res.total_setups == 0
    assert res.reason is not None
    assert "K 线不足" in res.reason
    assert res.no_trigger is False


def test_never_resolved_counts_open_not_crashed() -> None:
    """末尾仍未触达 SL/TP 的 setup 计为 open，不抛错、不产出交易。"""
    df = _klines(
        [
            (1, 102, 105, 100, 104),  # trigger
            (2, 103, 106, 102, 105),  # 横盘，未触达 SL/TP
            (3, 104, 107, 103, 106),
            (4, 105, 108, 104, 107),
        ]
    )
    res = run_setup_backtest(
        df, 100, 95, 110, direction="long", min_candles=_SIM_MIN_CANDLES
    )

    assert res.total_setups == 0
    assert res.open_setups == 1
    assert res.reason is not None
    _assert_result_finite(res)


# ---------------------------------------------------------------------------
# 无效参数
# ---------------------------------------------------------------------------


def test_sl_equals_tp_raises() -> None:
    df = _klines([(1, 102, 105, 100, 104)] * 30)
    with pytest.raises(SetupBacktestError):
        run_setup_backtest(df, 100, 105, 105, direction="long")


def test_direction_price_mismatch_raises() -> None:
    df = _klines([(1, 102, 105, 100, 104)] * 30)
    # long 但 sl>entry
    with pytest.raises(SetupBacktestError):
        run_setup_backtest(df, 100, 105, 110, direction="long")
    # short 但 sl<entry
    with pytest.raises(SetupBacktestError):
        run_setup_backtest(df, 100, 95, 90, direction="short")
    # 非法方向
    with pytest.raises(SetupBacktestError):
        run_setup_backtest(df, 100, 95, 110, direction="sideways")  # type: ignore[arg-type]


def test_entry_equals_sl_raises_when_inferring() -> None:
    df = _klines([(1, 102, 105, 100, 104)] * 30)
    with pytest.raises(SetupBacktestError):
        run_setup_backtest(df, 100, 100, 110)  # direction=None，risk=0


def test_non_finite_price_raises() -> None:
    df = _klines([(1, 102, 105, 100, 104)] * 30)
    with pytest.raises(SetupBacktestError):
        run_setup_backtest(df, float("inf"), 95, 110, direction="long")


def test_missing_columns_raises() -> None:
    df = pd.DataFrame({"timestamp": [1], "close": [100.0]})
    with pytest.raises(ValueError, match="缺列"):
        run_setup_backtest(df, 100, 95, 110, direction="long")


# ---------------------------------------------------------------------------
# 方向推断 / short 语义
# ---------------------------------------------------------------------------


def test_direction_inferred_from_entry_vs_sl() -> None:
    df = _klines(
        [
            (1, 102, 105, 100, 104),
            (2, 104, 112, 103, 111),  # WIN
        ]
    )
    long_res = run_setup_backtest(
        df, 100, 95, 110, min_candles=_SIM_MIN_CANDLES
    )  # entry>sl → long
    assert long_res.total_setups == 1
    assert long_res.trades[0]["outcome"] == "win"


def test_short_setup_semantics() -> None:
    """Short entry=100 SL=105 TP=95（RR=1）：high>=entry 且收盘收回下方才触发。"""
    df = _klines(
        [
            (1, 98, 100, 97, 99),  # high=100>=100 且 close=99<=100 → trigger，未收敛
            (2, 99, 103, 94, 95),  # low<=95 → TP → WIN
            (3, 97, 98, 96, 97),  # 无触发
            (4, 102, 107, 101, 103),  # high=107>=100 但 close=103>=100 → 未收回下方 → 不触发
            (5, 99, 101, 98, 99),  # high=101>=100 且 close=99<=100 → trigger
            (6, 99, 106, 98, 105),  # high>=105(SL) → LOSS
        ]
    )
    res = run_setup_backtest(
        df, 100, 105, 95, direction="short", min_candles=_SIM_MIN_CANDLES
    )

    assert res.total_setups == 2
    assert [t["outcome"] for t in res.trades] == ["win", "loss"]
    assert res.win_rate == pytest.approx(0.5)
    assert res.avg_bars_held == pytest.approx(1.0)  # 触发 bar 不算持仓，各 1 根解析
    _assert_result_finite(res)


# ---------------------------------------------------------------------------
# 全部输出有限（防 -inf 序列化）
# ---------------------------------------------------------------------------


def test_all_outputs_finite_varied_rr() -> None:
    """不同 RR 下所有 float 输出有限。"""
    configs = [
        (100, 95, 110),  # RR=2 long
        (100, 90, 120),  # RR=2
        (100, 98, 102),  # RR=1
        (100, 50, 150),  # RR=5
    ]
    base = [(1, 102, 105, 100, 104), (2, 104, 112, 103, 111)]
    for entry, sl, tp in configs:
        res = run_setup_backtest(
            _klines(base * 5), entry, sl, tp, min_candles=_SIM_MIN_CANDLES
        )
        _assert_result_finite(res)
