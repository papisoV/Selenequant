from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from ai_market_pulse import cli, engine
from ai_market_pulse.config import (
    AnalysisSettings,
    AppConfig,
    BenchmarkSettings,
    DataSettings,
    LLMSettings,
    NewsSettings,
    NotificationTarget,
)
from ai_market_pulse.market_data import MarketDataError
from selene_space_types.stock import AssetAnalysis, DailyReport, PriceSnapshot, SignalScore, StockAsset


def _failed_report() -> DailyReport:
    return DailyReport(
        title="Test",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        market_brief="No valid market data was available.",
        analyses=[
            AssetAnalysis(
                asset=StockAsset(symbol="ZZZ"),
                snapshot=PriceSnapshot(
                    symbol="ZZZ",
                    name="ZZZ",
                    currency=None,
                    last_close=0,
                    previous_close=None,
                    change_pct=None,
                    start_date="",
                    end_date="",
                    rows=0,
                ),
                metrics={},
                signal=SignalScore(score=50, stance="neutral", risk_level="low", reasons=[]),
                news=[],
                warnings=["boom"],
            )
        ],
    )


def test_run_aborts_and_skips_notify_when_no_valid_data(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        title="Test",
        timezone="UTC",
        assets=[StockAsset(symbol="ZZZ")],
        analysis=AnalysisSettings(),
        data=DataSettings(),
        benchmarks=BenchmarkSettings(enabled=False),
        news=NewsSettings(enabled=False),
        llm=LLMSettings(enabled=False),
        notifications=[NotificationTarget(type="webhook", settings={"url": "https://x"})],
    )
    monkeypatch.setattr(cli, "load_config", lambda path: config)
    monkeypatch.setattr(cli, "run_analysis", lambda config, label: _failed_report())

    attempted: list[object] = []
    monkeypatch.setattr(
        cli, "send_notifications", lambda *args, **kwargs: attempted.append(args) or []
    )

    with pytest.raises(SystemExit):
        cli._run(
            config_path=tmp_path / "watchlist.yaml",
            output_dir=tmp_path / "reports",
            history_path=tmp_path / "history.jsonl",
            no_history=False,
            no_notify=False,
            no_ai=False,
            ai_only=False,
        )

    # The circuit breaker must prevent publishing a broken report and history.
    assert attempted == []
    assert not (tmp_path / "history.jsonl").exists()


def _fixture_history(last_close: float, rows: int = 60) -> pd.DataFrame:
    dates = pd.date_range("2026-04-01", periods=rows, freq="B")
    closes = [last_close - (rows - 1 - i) * 0.1 for i in range(rows)]
    return pd.DataFrame(
        {
            "Date": dates,
            "Open": [value - 0.2 for value in closes],
            "High": [value + 0.3 for value in closes],
            "Low": [value - 0.3 for value in closes],
            "Close": closes,
            "Volume": [1_000_000 for _ in range(rows)],
        }
    )


def _fixture_snapshot(symbol: str, name: str, last_close: float, rows: int = 60) -> PriceSnapshot:
    history = _fixture_history(last_close, rows)
    previous_close = float(history["Close"].iloc[-2])
    return PriceSnapshot(
        symbol=symbol,
        name=name,
        currency="USD",
        last_close=round(last_close, 6),
        previous_close=round(previous_close, 6),
        change_pct=round(last_close / previous_close - 1, 6),
        start_date=str(history["Date"].iloc[0].date()),
        end_date=str(history["Date"].iloc[-1].date()),
        rows=rows,
        source="fixture",
    )


def _write_minimal_watchlist(path, symbols: list[str], with_notification: bool = False) -> None:
    assets_yaml = "\n".join(
        f'  - symbol: "{symbol}"\n    name: "{symbol}"\n    market: "US"' for symbol in symbols
    )
    notifications_yaml = (
        """
notifications:
  - type: "webhook"
    url: "https://example.invalid/webhook"
"""
        if with_notification
        else ""
    )
    path.write_text(
        f"""
title: "Fixture Watchlist"
timezone: "UTC"

analysis:
  language: "en-US"
  lookback_days: 60
  min_history_rows: 10

data:
  providers: ["yfinance"]

benchmarks:
  enabled: false

news:
  enabled: false

llm:
  enabled: false

assets:
{assets_yaml}
{notifications_yaml}""",
        encoding="utf-8",
    )


def _make_fake_fetch_history(prices: dict[str, float], failing_symbols: set[str] | None = None):
    failing = failing_symbols or set()

    def _fake_fetch_history(asset, lookback_days, providers=None, as_of=None):
        if asset.symbol in failing:
            raise MarketDataError(f"No market data returned for {asset.symbol}.")
        last_close = prices[asset.symbol]
        snapshot = _fixture_snapshot(asset.symbol, asset.name or asset.symbol, last_close)
        history = _fixture_history(last_close)
        return asset, snapshot, history

    return _fake_fetch_history


def test_run_dashboard_site_pipeline_end_to_end(tmp_path, monkeypatch) -> None:
    symbols = ["AAA", "BBB"]
    prices = {"AAA": 101.5, "BBB": 55.25}
    monkeypatch.setattr(
        engine,
        "fetch_history",
        _make_fake_fetch_history(prices),
    )

    config_path = tmp_path / "watchlist.yaml"
    _write_minimal_watchlist(config_path, symbols)

    reports_dir = tmp_path / "reports"
    history_path = tmp_path / "history.jsonl"

    cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output",
            str(reports_dir),
            "--history",
            str(history_path),
            "--no-notify",
        ]
    )

    generated_reports = list(reports_dir.glob("market-pulse-*.html"))
    assert generated_reports, "run must write an HTML report"
    assert list(reports_dir.glob("market-pulse-*.md"))
    assert list(reports_dir.glob("market-pulse-*.json"))
    assert history_path.exists()
    history_lines = [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(history_lines) == len(symbols)

    dashboard_path = tmp_path / "dashboard.html"
    cli.main(
        [
            "dashboard",
            "--history",
            str(history_path),
            "--output",
            str(dashboard_path),
        ]
    )
    assert dashboard_path.exists()
    dashboard_html = dashboard_path.read_text(encoding="utf-8")
    for symbol in symbols:
        assert symbol in dashboard_html

    site_output = tmp_path / "site"
    cli.main(
        [
            "site",
            "--reports",
            str(reports_dir),
            "--output",
            str(site_output),
        ]
    )
    assert (site_output / "index.html").exists()
    site_index_html = (site_output / "index.html").read_text(encoding="utf-8")
    generated_report_name = generated_reports[0].name
    assert generated_report_name in site_index_html
    assert (site_output / "reports" / generated_report_name).exists()


def test_run_partial_failure_still_appends_history_and_notifies(tmp_path, monkeypatch) -> None:
    symbols = ["AAA", "BBB", "CCC"]
    prices = {"AAA": 101.5, "BBB": 55.25, "CCC": 10.0}
    monkeypatch.setattr(
        engine,
        "fetch_history",
        _make_fake_fetch_history(prices, failing_symbols={"CCC"}),
    )

    config_path = tmp_path / "watchlist.yaml"
    _write_minimal_watchlist(config_path, symbols, with_notification=True)

    reports_dir = tmp_path / "reports"
    history_path = tmp_path / "history.jsonl"

    attempted: list[object] = []
    monkeypatch.setattr(
        cli, "send_notifications", lambda *args, **kwargs: attempted.append(args) or ["webhook:default sent"]
    )

    cli.main(
        [
            "run",
            "--config",
            str(config_path),
            "--output",
            str(reports_dir),
            "--history",
            str(history_path),
        ]
    )

    assert history_path.exists()
    history_lines = [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # All configured symbols get a history row; the failing one just has close=None.
    assert len(history_lines) == len(symbols)
    assert attempted, "notifications must be attempted when at least one symbol has valid data"


def test_test_notify_calls_send_notifications_with_config_targets(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "watchlist.yaml"
    _write_minimal_watchlist(config_path, ["AAA"], with_notification=True)

    captured_calls: list[tuple] = []

    def _fake_send_notifications(report, targets, html_path=None, report_url=None):
        captured_calls.append((report, targets, html_path, report_url))
        return ["webhook:default sent"]

    monkeypatch.setattr(cli, "send_notifications", _fake_send_notifications)

    cli.main(["test-notify", "--config", str(config_path)])

    assert len(captured_calls) == 1
    _, targets, _, _ = captured_calls[0]
    assert targets and targets[0].type == "webhook"

    captured = capsys.readouterr()
    assert "webhook:default sent" in captured.out


def test_test_notify_reports_when_nothing_configured(tmp_path, monkeypatch, capsys) -> None:
    config_path = tmp_path / "watchlist.yaml"
    _write_minimal_watchlist(config_path, ["AAA"], with_notification=False)

    called = False

    def _fake_send_notifications(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(cli, "send_notifications", _fake_send_notifications)

    cli.main(["test-notify", "--config", str(config_path)])

    assert called is False
    captured = capsys.readouterr()
    assert "--telegram-token" in captured.out


def test_run_rejects_no_ai_and_ai_only_together(tmp_path, monkeypatch) -> None:
    config = AppConfig(
        title="Test",
        timezone="UTC",
        assets=[StockAsset(symbol="ZZZ")],
        analysis=AnalysisSettings(),
        data=DataSettings(),
        benchmarks=BenchmarkSettings(enabled=False),
        news=NewsSettings(enabled=False),
        llm=LLMSettings(enabled=False),
        notifications=[],
    )
    monkeypatch.setattr(cli, "load_config", lambda path: config)

    with pytest.raises(SystemExit, match="--no-ai and --ai-only cannot be used together."):
        cli._run(
            config_path=tmp_path / "watchlist.yaml",
            output_dir=tmp_path / "reports",
            history_path=tmp_path / "history.jsonl",
            no_history=False,
            no_notify=False,
            no_ai=True,
            ai_only=True,
        )
