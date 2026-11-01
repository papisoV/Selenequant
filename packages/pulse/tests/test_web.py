from __future__ import annotations

import json

import pandas as pd
import pytest

from ai_market_pulse import engine, notify
from ai_market_pulse.models import PriceSnapshot
from ai_market_pulse.web import ConsoleHandler, options_from_payload, render_console_html, run_console_analysis


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


def _fake_fetch_history(asset, lookback_days, providers, as_of=None):
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


def test_render_console_html_contains_visual_symbol_form() -> None:
    html = render_console_html()

    assert "data-run-form" in html
    assert 'name="symbols"' in html
    assert 'value="我的自选股每日分析报告"' in html
    assert 'data-default-en="My Daily Stock Analysis Report"' in html
    assert "/api/analyze" in html
    assert "分析任意股票池" in html


def test_options_from_payload_parses_custom_symbols() -> None:
    options = options_from_payload(
        {
            "symbols": "AAPL MSFT，600519 BTC-USD",
            "title": "Customer",
            "providers": "yfinance",
            "includeNews": False,
            "buildDashboard": True,
        }
    )

    assert options.symbols == ["AAPL", "MSFT", "600519", "BTC-USD"]
    assert options.title == "Customer"
    assert options.providers == ["yfinance"]
    assert options.include_news is False
    assert options.build_dashboard is True


def test_options_from_payload_rejects_empty_symbols() -> None:
    with pytest.raises(ValueError, match="symbol"):
        options_from_payload({"symbols": ""})


def test_options_from_payload_parses_notification_fields_when_present() -> None:
    options = options_from_payload(
        {
            "symbols": "AAPL",
            "telegramToken": "tg-token",
            "telegramChatId": "tg-chat",
            "feishuWebhook": "https://feishu.example/webhook",
        }
    )

    assert options.telegram_token == "tg-token"
    assert options.telegram_chat_id == "tg-chat"
    assert options.feishu_webhook == "https://feishu.example/webhook"


def test_options_from_payload_defaults_notification_fields_to_none() -> None:
    options = options_from_payload({"symbols": "AAPL"})

    assert options.telegram_token is None
    assert options.telegram_chat_id is None
    assert options.feishu_webhook is None


def test_run_console_analysis_writes_reports_and_returns_links(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine, "fetch_history", _fake_fetch_history)
    monkeypatch.setattr(engine, "fetch_news", lambda asset, settings: [])
    monkeypatch.setattr(engine, "fetch_benchmarks", lambda *args, **kwargs: ({}, []))

    options = options_from_payload(
        {
            "symbols": "AAPL",
            "title": "Test Console Report",
            "providers": "yfinance",
            "includeNews": False,
            "buildDashboard": True,
            "buildSite": True,
        }
    )

    result = run_console_analysis(options, root=tmp_path)

    assert result["symbols"] == ["AAPL"]
    links = result["links"]
    for key in ("html", "markdown", "json", "dashboard", "site"):
        assert key in links
        assert links[key], f"expected a link for {key!r}"

    for key in ("html", "markdown", "json", "dashboard", "site"):
        href = links[key]
        on_disk_path = tmp_path / href.lstrip("/")
        assert on_disk_path.exists(), f"expected file for {key!r} to exist at {on_disk_path}"


def test_run_console_analysis_appends_history(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine, "fetch_history", _fake_fetch_history)
    monkeypatch.setattr(engine, "fetch_news", lambda asset, settings: [])
    monkeypatch.setattr(engine, "fetch_benchmarks", lambda *args, **kwargs: ({}, []))

    options = options_from_payload(
        {
            "symbols": "AAPL",
            "includeNews": False,
            "buildDashboard": False,
            "buildSite": False,
        }
    )

    run_console_analysis(options, root=tmp_path)

    history_path = tmp_path / "data" / "history.jsonl"
    assert history_path.exists()
    lines = [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["symbol"] == "AAPL"


def test_run_console_analysis_sends_telegram_notification_when_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine, "fetch_history", _fake_fetch_history)
    monkeypatch.setattr(engine, "fetch_news", lambda asset, settings: [])
    monkeypatch.setattr(engine, "fetch_benchmarks", lambda *args, **kwargs: ({}, []))

    calls: list[dict[str, object]] = []

    def fake_send_notifications(report, targets, html_path=None, report_url=None):
        calls.append({"report": report, "targets": targets, "html_path": html_path, "report_url": report_url})
        return ["telegram:telegram sent"]

    monkeypatch.setattr(notify, "send_notifications", fake_send_notifications)

    options = options_from_payload(
        {
            "symbols": "AAPL",
            "includeNews": False,
            "buildDashboard": False,
            "buildSite": False,
            "telegramToken": "tg-token",
            "telegramChatId": "tg-chat",
        }
    )

    result = run_console_analysis(options, root=tmp_path)

    assert len(calls) == 1
    targets = calls[0]["targets"]
    assert len(targets) == 1
    assert targets[0].type == "telegram"
    assert targets[0].enabled is True
    assert targets[0].settings["token"] == "tg-token"
    assert targets[0].settings["chat_id"] == "tg-chat"
    assert result["notifications"] == ["telegram:telegram sent"]


def test_run_console_analysis_skips_notifications_when_not_configured(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(engine, "fetch_history", _fake_fetch_history)
    monkeypatch.setattr(engine, "fetch_news", lambda asset, settings: [])
    monkeypatch.setattr(engine, "fetch_benchmarks", lambda *args, **kwargs: ({}, []))

    calls: list[dict[str, object]] = []

    def fake_send_notifications(report, targets, html_path=None, report_url=None):
        calls.append({"targets": targets})
        return ["should not be called"]

    monkeypatch.setattr(notify, "send_notifications", fake_send_notifications)

    options = options_from_payload(
        {
            "symbols": "AAPL",
            "includeNews": False,
            "buildDashboard": False,
            "buildSite": False,
        }
    )

    result = run_console_analysis(options, root=tmp_path)

    assert calls == []
    assert result["notifications"] == []


def test_do_post_error_response_hides_exception_details(monkeypatch) -> None:
    sensitive = "/Users/someone/secret/path/config.yaml"

    def boom(options, root):
        raise RuntimeError(f"failed to read {sensitive}")

    monkeypatch.setattr("ai_market_pulse.web.run_console_analysis", boom)

    handler = ConsoleHandler.__new__(ConsoleHandler)
    handler.root = "."
    sent: dict[str, object] = {}

    def fake_send_json(body, status=200):
        sent["body"] = body
        sent["status"] = status

    handler._send_json = fake_send_json
    payload_bytes = b'{"symbols": "AAPL"}'
    handler.headers = {"Content-Length": str(len(payload_bytes))}

    class FakeRfile:
        def read(self, length):
            return payload_bytes

    handler.rfile = FakeRfile()
    handler.path = "/api/analyze"

    handler.do_POST()

    assert sent["status"] == 400
    assert sensitive not in json.dumps(sent["body"])
    assert sent["body"]["error"] == "Analysis failed. Check the server console for details."
