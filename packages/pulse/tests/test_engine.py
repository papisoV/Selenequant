from __future__ import annotations

import pandas as pd

from ai_market_pulse import engine
from ai_market_pulse.config import (
    AnalysisSettings,
    AppConfig,
    BenchmarkSettings,
    DataSettings,
    LLMSettings,
    NewsSettings,
)
from ai_market_pulse.market_data import MarketDataError
from ai_market_pulse.models import Asset, PriceSnapshot


def _fake_history_df(rows: int = 210) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="B")
    close = pd.Series(range(1, rows + 1), dtype=float)
    return pd.DataFrame(
        {
            "Date": index,
            "Open": close.values,
            "High": (close + 1).values,
            "Low": (close - 1).values,
            "Close": close.values,
            "Volume": [100] * rows,
        }
    )


def _config(assets: list[Asset]) -> AppConfig:
    return AppConfig(
        title="Test",
        timezone="UTC",
        assets=assets,
        analysis=AnalysisSettings(),
        data=DataSettings(providers=["fake"]),
        benchmarks=BenchmarkSettings(enabled=False),
        news=NewsSettings(enabled=False),
        llm=LLMSettings(enabled=False),
    )


def test_run_analysis_builds_report_with_position(monkeypatch) -> None:
    def fake_fetch_history(asset, lookback_days, providers, as_of=None):
        frame = _fake_history_df()
        snapshot = PriceSnapshot(
            symbol=asset.symbol,
            name=asset.name or asset.symbol,
            currency="USD",
            last_close=210.0,
            previous_close=209.0,
            change_pct=0.0047,
            start_date="2024-01-01",
            end_date="2026-07-08",
            rows=len(frame),
            source="fake",
        )
        return asset, snapshot, frame

    monkeypatch.setattr(engine, "fetch_history", fake_fetch_history)
    monkeypatch.setattr(engine, "fetch_news", lambda asset, settings: [])
    monkeypatch.setattr(engine, "fetch_benchmarks", lambda *args, **kwargs: ({}, []))

    config = _config([Asset(symbol="AAA", quantity=10, cost_basis=100)])
    report = engine.run_analysis(config, "test")

    assert len(report.analyses) == 1
    analysis = report.analyses[0]
    assert analysis.snapshot.rows == 210
    # 210 trading rows must be enough to compute a 200-day moving average.
    assert analysis.metrics.get("sma200") is not None
    assert report.portfolio
    assert "analyzed" in report.market_brief


def test_run_analysis_handles_failed_fetch(monkeypatch) -> None:
    def boom(asset, lookback_days, providers, as_of=None):
        raise MarketDataError("no data")

    monkeypatch.setattr(engine, "fetch_history", boom)
    monkeypatch.setattr(engine, "fetch_news", lambda asset, settings: [])
    monkeypatch.setattr(engine, "fetch_benchmarks", lambda *args, **kwargs: ({}, []))

    config = _config([Asset(symbol="ZZZ")])
    report = engine.run_analysis(config, "test")

    assert len(report.analyses) == 1
    failed = report.analyses[0]
    assert failed.snapshot.rows == 0
    assert failed.warnings
