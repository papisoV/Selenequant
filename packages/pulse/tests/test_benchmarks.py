from __future__ import annotations

from datetime import date

from ai_market_pulse import benchmarks as benchmarks_module
from ai_market_pulse.benchmarks import (
    BenchmarkData,
    attach_benchmark_comparisons,
    build_data_freshness,
    fetch_benchmarks,
)
from ai_market_pulse.config import BenchmarkSettings
from ai_market_pulse.models import Asset, AssetAnalysis, PriceSnapshot, SignalScore


def test_attach_benchmark_comparison_marks_outperformance() -> None:
    analysis = AssetAnalysis(
        asset=Asset(symbol="AAA", market="US"),
        snapshot=_snapshot("AAA"),
        metrics={"return_20d": 0.12, "return_60d": 0.18},
        signal=SignalScore(score=70, stance="constructive", risk_level="low", reasons=[]),
        news=[],
    )
    benchmark = BenchmarkData(
        asset=Asset(symbol="SPY", name="S&P 500 ETF", market="US"),
        snapshot=_snapshot("SPY"),
        metrics={"return_20d": 0.04, "return_60d": 0.08},
        freshness=build_data_freshness(_snapshot("SPY"), date(2026, 7, 8)),
    )

    enriched = attach_benchmark_comparisons([analysis], {"SPY": benchmark}, BenchmarkSettings())

    assert enriched[0].benchmark is not None
    assert enriched[0].benchmark.symbol == "SPY"
    assert enriched[0].benchmark.relative_return_20d == 0.08
    assert enriched[0].benchmark.verdict == "outperforming"


def test_build_data_freshness_flags_stale_data() -> None:
    freshness = build_data_freshness(_snapshot("AAA", end_date="2026-06-30"), date(2026, 7, 8), stale_after_days=4)

    assert freshness.status == "stale"
    assert freshness.age_days == 8
    assert "verify data freshness" in freshness.message


def test_fetch_benchmarks_exercises_fetch_history_and_forwards_as_of(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_fetch_history(asset, lookback_days, providers, as_of=None):
        calls.append(
            {
                "symbol": asset.symbol,
                "lookback_days": lookback_days,
                "providers": providers,
                "as_of": as_of,
            }
        )
        snapshot = _snapshot(asset.symbol)
        frame_history = _history_frame()
        return asset, snapshot, frame_history

    monkeypatch.setattr(benchmarks_module, "fetch_history", fake_fetch_history)

    settings = BenchmarkSettings(symbols=["SPY", "QQQ"], default_by_market={}, compare={})
    today = date(2026, 7, 8)

    data, snapshots = fetch_benchmarks([], settings, ["yfinance"], 60, today)

    assert calls, "fetch_benchmarks must call market_data.fetch_history"
    assert {call["symbol"] for call in calls} == {"SPY", "QQQ"}
    assert all(call["as_of"] == today for call in calls)
    assert {snapshot.symbol for snapshot in snapshots} == {"SPY", "QQQ"}
    assert set(data.keys()) == {"SPY", "QQQ"}


def _history_frame():
    import pandas as pd

    index = pd.date_range("2026-01-01", periods=30, freq="B")
    close = [float(i + 1) for i in range(30)]
    return pd.DataFrame(
        {
            "Date": index,
            "Open": close,
            "High": [c + 1 for c in close],
            "Low": [c - 1 for c in close],
            "Close": close,
            "Volume": [100] * 30,
        }
    )


def _snapshot(symbol: str, end_date: str = "2026-07-08") -> PriceSnapshot:
    return PriceSnapshot(
        symbol=symbol,
        name=symbol,
        currency="USD",
        last_close=100,
        previous_close=99,
        change_pct=0.01,
        start_date="2026-01-01",
        end_date=end_date,
        rows=120,
        source="unit",
    )
