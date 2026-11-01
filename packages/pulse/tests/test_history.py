from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from ai_market_pulse.dashboard import build_dashboard_data
from ai_market_pulse.history import append_history, load_history
from ai_market_pulse.models import (
    Asset,
    AssetAnalysis,
    DailyReport,
    NewsItem,
    PriceSnapshot,
    SignalScore,
)


def test_load_history_skips_rows_missing_required_fields(tmp_path: Path) -> None:
    history_path = tmp_path / "history.jsonl"
    valid_row = {
        "date": "2026-07-07",
        "symbol": "MSFT",
        "close": 410.0,
        "score": 55,
        "stance": "neutral",
        "risk_level": "low",
    }
    broken_row = {"date": "2026-07-08", "symbol": "AAPL"}
    history_path.write_text(
        json.dumps(valid_row) + "\n" + json.dumps(broken_row) + "\n",
        encoding="utf-8",
    )

    records = load_history(history_path)

    assert len(records) == 1
    assert records[0].symbol == "MSFT"

    data = build_dashboard_data(records)

    assert data.symbol_trends[0].symbol == "MSFT"


def test_append_history_is_safe_under_concurrent_writers(tmp_path: Path) -> None:
    history_path = tmp_path / "history.jsonl"
    reports = [_report(score=60 + i) for i in range(5)]

    threads = [threading.Thread(target=append_history, args=(history_path, report)) for report in reports]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = [line for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    matching = [
        line
        for line in lines
        if json.loads(line)["symbol"] == "AAPL" and json.loads(line)["date"] == "2026-07-08"
    ]

    assert len(matching) == 1


def _report(score: int) -> DailyReport:
    analysis = AssetAnalysis(
        asset=Asset(symbol="AAPL", name="Apple"),
        snapshot=PriceSnapshot(
            symbol="AAPL",
            name="Apple",
            currency="USD",
            last_close=200.0,
            previous_close=198.0,
            change_pct=0.01,
            start_date="2026-01-01",
            end_date="2026-07-08",
            rows=100,
        ),
        metrics={"last_close": 200.0},
        signal=SignalScore(score=score, stance="neutral", risk_level="low", reasons=["reason"]),
        news=[NewsItem(title="news", link="https://example.com")],
    )
    return DailyReport(
        title="Test",
        generated_at=datetime(2026, 7, 8, 8, 0),
        timezone="UTC",
        language="en-US",
        analyses=[analysis],
        market_brief="brief",
    )
