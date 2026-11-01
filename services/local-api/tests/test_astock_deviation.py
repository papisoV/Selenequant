"""Tests for app.astock_deviation — B2 deviation read path ((a) daily / (b) live).

Offline: KlineDB and the index fetcher are fakes; live quotes come from a stub
source. The point of these tests is the *glue* between the pure 口径
(selene_space_ashare.abnormal, tested separately) and the two substrates —
especially the intraday overlay guard, whose failure mode is silent
double-counting of today's move.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

pytest.importorskip("selene_space_ashare")

from app.astock_deviation import collect_live_quotes, deviation_report  # noqa: E402

_CST = timezone(timedelta(hours=8))
_TODAY = datetime(2026, 10, 14, 10, 30, tzinfo=_CST)
_DAYS = [
    (datetime(2026, 9, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(40)
]


class _FakeKlineDB:
    """Mimics KlineDB.get_klines for the ('astock','spot',sym,'1d') partition."""

    def __init__(self, series: dict[str, list[tuple[str, float]]], partition="spot"):
        self._series = series
        self._partition = partition
        self.calls: list[str] = []

    def get_klines(self, exchange, market_type, symbol, interval, *a, **k):
        self.calls.append(market_type)
        if market_type != self._partition or symbol not in self._series:
            return pd.DataFrame(columns=["timestamp", "close"])
        rows = []
        for day, close in self._series[symbol]:
            dt = datetime.strptime(day, "%Y%m%d").replace(tzinfo=_CST)
            rows.append({"timestamp": int(dt.timestamp() * 1000), "close": close})
        return pd.DataFrame(rows)


def _flat_index_frame() -> pd.DataFrame:
    return pd.DataFrame({"date": _DAYS, "close": [100.0] * 40})


def _ramp(mult: float, n: int = 40) -> list[tuple[str, float]]:
    return [(d, 10.0 if i < n - 3 else 10.0 * mult) for i, d in enumerate(_DAYS)]


def _index_fn(frame: pd.DataFrame):
    return lambda code: frame


def _ticker(symbol: str, day: str, pct: float):
    from selene_space_types.market import TickerEvent

    ts = int(
        datetime.strptime(day, "%Y%m%d").replace(hour=10, tzinfo=_CST).timestamp()
        * 1000
    )
    return TickerEvent(
        exchange="astock",
        symbol=symbol,
        timestamp=ts,
        last_price=10.0,
        bid=9.99,
        ask=10.01,
        volume_24h=1.0,
        change_24h_pct=pct,
    )


class _StubSource:
    def __init__(self, tickers):
        self._tickers = tickers

    async def fetch_tickers_rest(self, symbols):
        return {s: self._tickers[s] for s in symbols if s in self._tickers}


class _StubMarketSvc:
    def __init__(self, source):
        self._source = source

    def get_source(self, exchange, market_type):
        return (
            self._source if (exchange, market_type) == ("astock", "intraday") else None
        )


# ---------------------------------------------------------------------------
# (a) daily
# ---------------------------------------------------------------------------


def test_daily_uses_spot_partition_and_tsp_bench() -> None:
    db = _FakeKlineDB({"600519.SH": _ramp(1.30)})
    rep = deviation_report(
        symbols=["600519.SH"],
        kline_db=db,
        index_daily_fn=_index_fn(_flat_index_frame()),
        today=_TODAY,
    )
    row = rep["rows"][0]
    assert row["basis"] == "spot"
    assert row["bench"] == "000002.SH"  # 上证A指首选
    assert row["intraday"] is False
    w3 = row["windows"]["3d"]
    assert w3["value"] == pytest.approx(0.30)
    assert w3["closeness"] == pytest.approx(1.5)
    assert row["status"] == "triggered"
    # spot 命中后不应再读 astock 回退分区
    assert db.calls == ["spot"]


def test_daily_falls_back_to_astock_partition_and_labels_basis() -> None:
    # spot 分区空 → 回退 astock(导入 qfq),basis 必须如实标出,不静默。
    db = _FakeKlineDB({"600519.SH": _ramp(1.30)}, partition="astock")
    rep = deviation_report(
        symbols=["600519.SH"],
        kline_db=db,
        index_daily_fn=_index_fn(_flat_index_frame()),
        today=_TODAY,
    )
    row = rep["rows"][0]
    assert row["basis"] == "astock"
    assert db.calls == ["spot", "astock"]


def test_bench_falls_back_to_second_candidate() -> None:
    # 首选 000002.SH 无指数数据 → 回退 000001.SH;bench 标出实际所用。
    empty = pd.DataFrame(columns=["date", "close"])

    def index_fn(code: str) -> pd.DataFrame:
        return _flat_index_frame() if code == "000001" else empty

    db = _FakeKlineDB({"600519.SH": _ramp(1.30)})
    rep = deviation_report(
        symbols=["600519.SH"],
        kline_db=db,
        index_daily_fn=index_fn,
        today=_TODAY,
    )
    assert rep["rows"][0]["bench"] == "000001.SH"


def test_no_index_data_skips_symbol() -> None:
    db = _FakeKlineDB({"600519.SH": _ramp(1.30)})
    rep = deviation_report(
        symbols=["600519.SH"],
        kline_db=db,
        index_daily_fn=_index_fn(pd.DataFrame(columns=["date", "close"])),
        today=_TODAY,
    )
    assert rep["rows"] == []
    assert rep["skipped"] == [{"symbol": "600519.SH", "reason": "no_bench_index"}]


# ---------------------------------------------------------------------------
# (b) intraday overlay guard
# ---------------------------------------------------------------------------


def test_intraday_overlays_when_quote_is_today() -> None:
    db = _FakeKlineDB({"600519.SH": _ramp(1.30)})
    quotes = {
        "600519.SH": ("20261014", 0.05),
        "000002.SH": ("20261014", 0.01),
    }
    rep = deviation_report(
        symbols=["600519.SH"],
        kline_db=db,
        index_daily_fn=_index_fn(_flat_index_frame()),
        today=_TODAY,
        live_quotes=quotes,
    )
    row = rep["rows"][0]
    assert row["intraday"] is True
    # 0.30 + (0.05 - 0.01) = 0.34
    assert row["windows"]["3d"]["value"] == pytest.approx(0.34)
    assert rep["live"] is True


def test_intraday_stale_quote_day_does_not_overlay() -> None:
    # 报价日是昨天(非交易日拉到的是上一交易日涨跌,已含在历史里)→ 不叠加。
    db = _FakeKlineDB({"600519.SH": _ramp(1.30)})
    quotes = {
        "600519.SH": ("20261013", 0.05),
        "000002.SH": ("20261013", 0.01),
    }
    rep = deviation_report(
        symbols=["600519.SH"],
        kline_db=db,
        index_daily_fn=_index_fn(_flat_index_frame()),
        today=_TODAY,
        live_quotes=quotes,
    )
    row = rep["rows"][0]
    assert row["intraday"] is False
    assert row["windows"]["3d"]["value"] == pytest.approx(0.30)


def test_intraday_missing_index_quote_does_not_overlay() -> None:
    # 只有个股实时价、缺基准指数实时价 → 叠加会失真,故整体退回 (a)。
    db = _FakeKlineDB({"600519.SH": _ramp(1.30)})
    quotes = {"600519.SH": ("20261014", 0.05)}
    rep = deviation_report(
        symbols=["600519.SH"],
        kline_db=db,
        index_daily_fn=_index_fn(_flat_index_frame()),
        today=_TODAY,
        live_quotes=quotes,
    )
    assert rep["rows"][0]["intraday"] is False


def test_today_bar_in_store_is_excluded_from_baseline() -> None:
    # 日线分区里若已有一根「今日」发展中 bar(盘中可能发生),它必须被
    # 「严格早于会话日」的过滤剔除 —— 否则今日涨跌会同时进历史与实时叠加,
    # 被算两遍。这里断言过滤生效:basis_date 停在昨天,且实时叠加仍正确。
    series = [*_ramp(1.30), ("20261014", 13.0)]
    db = _FakeKlineDB({"600519.SH": series})
    quotes = {
        "600519.SH": ("20261014", 0.05),
        "000002.SH": ("20261014", 0.01),
    }
    rep = deviation_report(
        symbols=["600519.SH"],
        kline_db=db,
        index_daily_fn=_index_fn(_flat_index_frame()),
        today=_TODAY,
        live_quotes=quotes,
    )
    row = rep["rows"][0]
    assert row["basis_date"] == _DAYS[-1]  # 今日 bar 被剔除,basis 停在最后一根已结算日
    assert row["intraday"] is True
    # 历史 +30%(不含今日) + (0.05 - 0.01) = 0.34 —— 今日只计一次。
    assert row["windows"]["3d"]["value"] == pytest.approx(0.34)


# ---------------------------------------------------------------------------
# collect_live_quotes
# ---------------------------------------------------------------------------


def test_collect_live_quotes_maps_day_and_pct() -> None:
    src = _StubSource({"600519.SH": _ticker("600519.SH", "20261014", 5.0)})
    svc = _StubMarketSvc(src)
    quotes = asyncio.run(collect_live_quotes(svc, ["600519.SH"]))
    assert quotes == {"600519.SH": ("20261014", 0.05)}


def test_collect_live_quotes_no_source_is_empty() -> None:
    quotes = asyncio.run(collect_live_quotes(None, ["600519.SH"]))
    assert quotes == {}


def test_collect_live_quotes_fetch_failure_is_empty() -> None:
    class _Boom:
        async def fetch_tickers_rest(self, symbols):
            raise RuntimeError("sina down")

    quotes = asyncio.run(collect_live_quotes(_StubMarketSvc(_Boom()), ["600519.SH"]))
    assert quotes == {}
