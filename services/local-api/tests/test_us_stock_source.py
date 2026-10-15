"""Tests for USStockSource (Yahoo v8 chart adapter).

Fixture payloads are de-identified reconstructions of the v8 chart response
shape (chart.result[0].timestamp + indicators.quote[0] + indicators.adjclose).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from selene_space_market.sources.us_stock_source import (
    USStockSource,
    _normalize_symbol,
    _parse_chart,
    _resolve_interval,
)


def _chart_payload(
    bars: list[dict[str, Any]], with_adjclose: bool = True
) -> dict[str, Any]:
    """bars: dicts with ts,o,h,l,c,v[,a]. Builds a minimal v8 chart response."""
    quote: dict[str, list[Any]] = {
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
    }
    adj: list[Any] = []
    ts: list[int] = []
    for b in bars:
        ts.append(b["ts"])
        quote["open"].append(b.get("o"))
        quote["high"].append(b.get("h"))
        quote["low"].append(b.get("l"))
        quote["close"].append(b.get("c"))
        quote["volume"].append(b.get("v"))
        adj.append(b.get("a", b.get("c")) if with_adjclose else None)
    indicators: dict[str, Any] = {"quote": [quote]}
    if with_adjclose:
        indicators["adjclose"] = [{"adjclose": adj}]
    return {"chart": {"result": [{"timestamp": ts, "indicators": indicators}]}}


@pytest.fixture(autouse=True)
def _isolate_adjust_sidecar(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Keep the adjust-mode sidecar out of the repo data/ dir."""
    monkeypatch.setenv("SELENE_DATA_DIR", str(tmp_path))


class FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeGateway:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def get(
        self, url: str, params: Any = None, headers: Any = None, **_: Any
    ) -> FakeResp:
        self.calls.append({"url": url, "params": dict(params or {})})
        return FakeResp(self._payload)


# ── AC3: symbol normalization ────────────────────────────────────────────────


def test_symbol_normalization() -> None:
    assert _normalize_symbol(" aapl ") == "AAPL"
    assert _normalize_symbol("brk.a") == "BRK-A"
    assert _normalize_symbol("BRK$A") == "BRK-A"
    with pytest.raises(ValueError):
        _normalize_symbol("BTC/USDT")
    with pytest.raises(ValueError):
        _normalize_symbol("")


# ── AC4: interval mapping ────────────────────────────────────────────────────


def test_interval_mapping() -> None:
    assert _resolve_interval("1h") == "1h"
    assert _resolve_interval("1d") == "1d"
    assert _resolve_interval("1w") == "1w"
    assert _resolve_interval("4h") == "1h"
    assert _resolve_interval("banana") == "1d"


def test_yahoo_request_interval_labels() -> None:
    """Canonical 1h/1w request Yahoo labels 60m/1wk (AC4 request side)."""
    src = USStockSource()
    gw = FakeGateway(_chart_payload([{"ts": 1_760_000_000, "c": 100.0}]))
    src._http = gw  # type: ignore[assignment]
    asyncio.run(src.fetch_klines_rest("AAPL", "1h", limit=5))
    assert gw.calls[0]["params"]["interval"] == "60m"
    asyncio.run(src.fetch_klines_rest("AAPL", "1w", limit=5))
    assert gw.calls[1]["params"]["interval"] == "1wk"


# ── AC1/AC2: parse + adjclose ratio scaling ──────────────────────────────────


def test_parse_chart_adjclose_scaling() -> None:
    """close==adjclose; OHL scaled by r=adjclose/close; volume untouched (O-A)."""
    payload = _chart_payload(
        [
            {
                "ts": 1_760_000_000,
                "o": 100.0,
                "h": 110.0,
                "l": 99.0,
                "c": 105.0,
                "v": 1000,
                "a": 210.0,
            },
        ]
    )
    events = _parse_chart(payload, "AAPL", "1d")
    assert len(events) == 1
    e = events[0]
    assert e.exchange == "yahoo"
    assert e.market_type == "us_stock"
    assert e.closed is True
    assert e.close == pytest.approx(210.0)
    assert e.open == pytest.approx(200.0)  # 100 * 2
    assert e.high == pytest.approx(220.0)
    assert e.low == pytest.approx(198.0)
    assert e.volume == 1000.0  # untouched
    assert e.timestamp == 1_760_000_000_000  # s → ms
    assert min(e.open, e.high, e.low, e.close) > 0


def test_parse_chart_identity_when_adjclose_absent() -> None:
    payload = _chart_payload(
        [
            {
                "ts": 1_760_000_000,
                "o": 100.0,
                "h": 110.0,
                "l": 99.0,
                "c": 105.0,
                "v": 1000,
            }
        ],
        with_adjclose=True,
    )
    events = _parse_chart(payload, "AAPL", "1d")
    e = events[0]
    assert e.close == pytest.approx(105.0)
    assert e.open == pytest.approx(100.0)


def test_parse_chart_skips_none_close() -> None:
    """us_radar.py:124-125 same guard: None-close bars dropped."""
    payload = _chart_payload(
        [
            {"ts": 1_760_000_000, "o": None, "h": None, "l": None, "c": None, "v": 0},
            {
                "ts": 1_760_086_400,
                "o": 100.0,
                "h": 110.0,
                "l": 99.0,
                "c": 105.0,
                "v": 1000,
            },
        ]
    )
    events = _parse_chart(payload, "AAPL", "1d")
    assert len(events) == 1
    assert events[0].timestamp == 1_760_086_400_000


def test_fetch_klines_rest_full_path() -> None:
    """AC1: full path with gateway mocked — exchange/market_type/OHLC>0."""
    src = USStockSource()
    gw = FakeGateway(
        _chart_payload(
            [
                {
                    "ts": 1_760_000_000,
                    "o": 100.0,
                    "h": 110.0,
                    "l": 99.0,
                    "c": 105.0,
                    "v": 1000,
                },
                {
                    "ts": 1_760_086_400,
                    "o": 105.0,
                    "h": 112.0,
                    "l": 104.0,
                    "c": 111.0,
                    "v": 1200,
                },
            ]
        )
    )
    src._http = gw  # type: ignore[assignment]
    events = asyncio.run(src.fetch_klines_rest("aapl", "1d", limit=5))
    assert len(events) == 2
    for e in events:
        assert e.exchange == "yahoo"
        assert e.market_type == "us_stock"
        assert min(e.open, e.high, e.low, e.close) > 0
    assert events[-1].symbol == "AAPL"  # normalized
    assert gw.calls[0]["url"].endswith("/v8/finance/chart/AAPL")


def test_fetch_klines_rest_period_ms_to_s() -> None:
    """AC5: end_time/start_time (ms) → period1/period2 (s)."""
    src = USStockSource()
    gw = FakeGateway(_chart_payload([{"ts": 1_760_000_000, "c": 100.0}]))
    src._http = gw  # type: ignore[assignment]
    asyncio.run(
        src.fetch_klines_rest(
            "AAPL",
            "1d",
            limit=5,
            end_time=1_760_000_000_000,
            start_time=1_750_000_000_000,
        )
    )
    params = gw.calls[0]["params"]
    assert params["period2"] == "1760000000"
    assert params["period1"] == "1750000000"


def test_fetch_klines_rest_bad_symbol_raises() -> None:
    src = USStockSource()
    src._http = FakeGateway(_chart_payload([]))  # type: ignore[assignment]
    with pytest.raises(ValueError):
        asyncio.run(src.fetch_klines_rest("BTC/USDT", "1d"))


def test_fetch_klines_rest_http_failure_returns_empty() -> None:
    class ExplodingGateway:
        async def get(self, *a: Any, **k: Any) -> Any:
            raise ConnectionError("boom")

    src = USStockSource()
    src._http = ExplodingGateway()  # type: ignore[assignment]
    assert asyncio.run(src.fetch_klines_rest("AAPL", "1d")) == []


# ── AC11: split-restatement sentinel ─────────────────────────────────────────


def test_split_restatement_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Adjacent 1d |log return| > 0.35 + volume blowout → loud warning."""
    payload = _chart_payload(
        [
            {
                "ts": 1_760_000_000,
                "o": 100.0,
                "h": 110.0,
                "l": 99.0,
                "c": 105.0,
                "v": 10_000,
            },
            # close jumps ~2.5x with 50x volume → sentinel fires
            {
                "ts": 1_760_086_400,
                "o": 260.0,
                "h": 270.0,
                "l": 255.0,
                "c": 265.0,
                "v": 500_000,
            },
        ]
    )
    with caplog.at_level(
        logging.WARNING, logger="selene_space_market.sources.us_stock_source"
    ):
        _parse_chart(payload, "AAPL", "1d")  # parse alone does not warn
        src = USStockSource()
        src._http = FakeGateway(payload)  # type: ignore[assignment]
        asyncio.run(src.fetch_klines_rest("AAPL", "1d"))
    assert any("split restatement" in r.message for r in caplog.records)


def test_no_warning_on_normal_moves(caplog: pytest.LogCaptureFixture) -> None:
    payload = _chart_payload(
        [
            {
                "ts": 1_760_000_000,
                "o": 100.0,
                "h": 110.0,
                "l": 99.0,
                "c": 105.0,
                "v": 10_000,
            },
            {
                "ts": 1_760_086_400,
                "o": 105.0,
                "h": 112.0,
                "l": 104.0,
                "c": 111.0,
                "v": 12_000,
            },
        ]
    )
    with caplog.at_level(
        logging.WARNING, logger="selene_space_market.sources.us_stock_source"
    ):
        src = USStockSource()
        src._http = FakeGateway(payload)  # type: ignore[assignment]
        asyncio.run(src.fetch_klines_rest("AAPL", "1d"))
    assert not any("split restatement" in r.message for r in caplog.records)


def test_sentinel_ignores_intraday(caplog: pytest.LogCaptureFixture) -> None:
    """Heuristic is 1d/1w only — 1h series with the same shape stays quiet."""
    payload = _chart_payload(
        [
            {
                "ts": 1_760_000_000,
                "o": 100.0,
                "h": 110.0,
                "l": 99.0,
                "c": 105.0,
                "v": 10_000,
            },
            {
                "ts": 1_760_360_000,
                "o": 260.0,
                "h": 270.0,
                "l": 255.0,
                "c": 265.0,
                "v": 100_000,
            },
        ]
    )
    with caplog.at_level(
        logging.WARNING, logger="selene_space_market.sources.us_stock_source"
    ):
        src = USStockSource()
        src._http = FakeGateway(payload)  # type: ignore[assignment]
        asyncio.run(src.fetch_klines_rest("AAPL", "1h"))
    assert not any("split restatement" in r.message for r in caplog.records)


# ── adjust-mode sidecar (plan R3) ─────────────────────────────────────────────


def test_adjust_mode_sidecar_mismatch_warning(
    tmp_path: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SELENE_DATA_DIR", str(tmp_path))
    (tmp_path / "us_stock_adjust_mode.json").write_text(
        '{"yahoo:us_stock:AAPL:1d": "raw"}', encoding="utf-8"
    )
    with caplog.at_level(
        logging.WARNING, logger="selene_space_market.sources.us_stock_source"
    ):
        src = USStockSource()
        src._http = FakeGateway(_chart_payload([{"ts": 1_760_000_000, "c": 100.0}]))  # type: ignore[assignment]
        asyncio.run(src.fetch_klines_rest("AAPL", "1d"))
    assert any("mixed calibers" in r.message for r in caplog.records)
