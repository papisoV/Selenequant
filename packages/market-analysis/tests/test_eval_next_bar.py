"""US-012 C3 — next_bar_forecast 离线评估单测。

覆盖验收标准：
- direction_of / momentum_predictor 纯函数方向判定
- extract_forecast 抽取 LLM 预测（缺失/非法返回 None）
- evaluate_next_bar 出命中率报告：预测 vs 动量基线，跳过无后续数据的记录
"""
from __future__ import annotations

import pandas as pd
import pytest

from selene_space_market_analysis.eval_next_bar import (
    compute_subsequent_closes,
    direction_of,
    evaluate_next_bar,
    extract_forecast,
    momentum_predictor,
)


def _record(record_id: str, closes: list[float], forecast: str | None) -> dict:
    return {
        "meta": {"record_id": record_id},
        "input_data": {
            "klines": [{"close": c} for c in closes],
        },
        "stage2": (
            {"next_bar_forecast": {"direction": forecast, "note": "x"}}
            if forecast is not None
            else {}
        ),
    }


# ---------------------------------------------------------------------------
# direction_of / momentum_predictor
# ---------------------------------------------------------------------------


def test_direction_of_up_down_flat() -> None:
    assert direction_of(100.0, 101.0) == "up"
    assert direction_of(101.0, 100.0) == "down"
    assert direction_of(100.0, 100.0) == "flat"
    assert direction_of(100.0, 100.001, tolerance=0.01) == "flat"


def test_momentum_predictor_trend_continuation() -> None:
    # 最近 3 根上涨 → 预测 up（趋势延续基线）
    assert momentum_predictor([99.0, 100.0, 101.0], window=3) == "up"
    # 最近 3 根下跌 → 预测 down
    assert momentum_predictor([101.0, 100.0, 99.0], window=3) == "down"
    # 数据不足 → flat
    assert momentum_predictor([100.0]) == "flat"
    # 窗口内先跌后涨（锚点更高）→ 预测 down
    assert momentum_predictor([103.0, 100.0, 101.0], window=3) == "down"


# ---------------------------------------------------------------------------
# extract_forecast
# ---------------------------------------------------------------------------


def test_extract_forecast_valid() -> None:
    assert extract_forecast(_record("r1", [100.0], "up")) == "up"
    assert extract_forecast(_record("r2", [100.0], "flat")) == "flat"


def test_extract_forecast_missing_or_illegal() -> None:
    assert extract_forecast(_record("r1", [100.0], None)) is None
    rec = _record("r2", [100.0], "sideways")
    assert extract_forecast(rec) is None
    rec2 = _record("r3", [100.0], None)
    rec2["stage2"] = None
    assert extract_forecast(rec2) is None


# ---------------------------------------------------------------------------
# evaluate_next_bar — 出数报告
# ---------------------------------------------------------------------------


def test_evaluate_next_bar_hit_rate_forecast() -> None:
    # 3 条带预测记录：前一根 close=100，后续 close 决定实际方向
    records = [
        _record("a", [100.0, 102.0], "up"),    # 后续 103 → up，命中
        _record("b", [100.0, 102.0], "down"),  # 后续 103 → up，未命中
        _record("c", [100.0, 98.0], "down"),   # 后续 97 → down，命中
    ]
    subsequent = {"a": 103.0, "b": 103.0, "c": 97.0}

    report = evaluate_next_bar(records, subsequent)

    assert report["samples_total"] == 3
    assert report["samples_with_forecast"] == 3
    assert report["hit_rate_forecast"] == pytest.approx(2 / 3)
    assert report["hit_rate_baseline"] is not None
    assert [r["record_id"] for r in report["rows"]] == ["a", "b", "c"]


def test_evaluate_next_bar_skips_records_without_subsequent_bar() -> None:
    records = [
        _record("a", [100.0, 102.0], "up"),
        _record("b", [100.0, 102.0], "up"),
    ]
    subsequent = {"a": 103.0}  # b 无后续数据 → 跳过

    report = evaluate_next_bar(records, subsequent)

    assert report["samples_total"] == 1
    assert report["rows"][0]["record_id"] == "a"


def test_evaluate_next_bar_empty_report() -> None:
    report = evaluate_next_bar([], {})
    assert report["samples_total"] == 0
    assert report["hit_rate_forecast"] is None
    assert report["hit_rate_baseline"] is None
    assert report["rows"] == []


def test_evaluate_next_bar_forecast_none_still_scores_baseline() -> None:
    """无预测的记录不占 forecast 样本，但仍参与基线对照。"""
    records = [
        _record("a", [100.0, 102.0], None),  # 无预测
        _record("b", [100.0, 102.0], "up"),  # 有预测
    ]
    subsequent = {"a": 103.0, "b": 103.0}

    report = evaluate_next_bar(records, subsequent)

    assert report["samples_total"] == 2
    assert report["samples_with_forecast"] == 1
    assert report["hit_rate_forecast"] == pytest.approx(1.0)
    assert report["hit_rate_baseline"] is not None


# ---------------------------------------------------------------------------
# compute_subsequent_closes — record_id → 后继收盘价
# ---------------------------------------------------------------------------


def _record_with_last_ts(record_id: str, last_ts: int) -> dict:
    return {
        "meta": {"record_id": record_id},
        "input_data": {"klines": [{"close": 100.0, "timestamp": last_ts}]},
        "stage2": {},
    }


def _fake_df(ts_closes: list[tuple[int, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"timestamp": [t for t, _ in ts_closes], "close": [c for _, c in ts_closes]}
    )


def test_compute_subsequent_closes_next_bar_only() -> None:
    """取记录最后一根 K 线之后那根的收盘；无后续的记录缺省。"""
    records = [_record_with_last_ts("a", 20), _record_with_last_ts("b", 40)]
    df = _fake_df([(10, 100.0), (20, 101.0), (30, 102.0), (40, 103.0)])

    result = compute_subsequent_closes(records, df)

    # a: 第一个 ts > 20 是 30 → 102.0；b: 无 ts > 40 → 缺省
    assert result == {"a": 102.0}


def test_compute_subsequent_closes_empty_df() -> None:
    result = compute_subsequent_closes(
        [_record_with_last_ts("a", 20)], _fake_df([])
    )
    assert result == {}


def test_compute_subsequent_closes_record_without_klines() -> None:
    records = [{"meta": {"record_id": "x"}, "input_data": {"klines": []}, "stage2": {}}]
    result = compute_subsequent_closes(records, _fake_df([(10, 100.0)]))
    assert result == {}
