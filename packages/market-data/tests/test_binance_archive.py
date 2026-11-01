"""binance_archive — aggTrades ZIP → TickStore.

Network is never touched: the ZIP is synthesised in-memory so the tests pin
the CSV dialect, the aggressor convention, and the T+1 skip path.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterator

import pytest

from selene_space_market.tick_store.binance_archive import (
    archive_url,
    fetch_range_into_store,
    iter_day_trades,
    parse_agg_trades_csv,
)
from selene_space_market.tick_store.tick_db import TickStore

HEADER = "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"
BASE_MS = 1_759_977_600_000  # 2025-10-08 00:00:00 UTC


@pytest.fixture
def store(tmp_path: Path) -> Iterator[TickStore]:
    """TickStore opens a connection per call, so ":memory:" would hand every
    call a fresh empty database — a real file is required."""
    instance = TickStore(str(tmp_path / f"ticks_{uuid.uuid4().hex[:8]}.db"))
    try:
        yield instance
    finally:
        instance.close()


def _csv(*rows: str) -> io.StringIO:
    return io.StringIO("\n".join((HEADER, *rows)) + "\n")


def _zip_bytes(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SOLUSDT-aggTrades-2025-10-08.csv", csv_text)
    return buf.getvalue()


class TestArchiveUrl:
    def test_futures_url_shape(self) -> None:
        url = archive_url("SOL/USDT", date(2025, 10, 8))
        assert url == (
            "https://data.binance.vision/data/futures/um/daily/aggTrades/"
            "SOLUSDT/SOLUSDT-aggTrades-2025-10-08.zip"
        )

    def test_wire_form_symbol_matches_slash_form(self) -> None:
        assert archive_url("SOLUSDT", date(2025, 10, 8)) == archive_url(
            "SOL/USDT", date(2025, 10, 8)
        )

    def test_spot_url_shape(self) -> None:
        url = archive_url("SOL/USDT", date(2025, 10, 8), market_type="spot")
        assert "/data/spot/daily/aggTrades/SOLUSDT/" in url

    def test_unknown_market_type_rejected(self) -> None:
        with pytest.raises(ValueError):
            archive_url("SOL/USDT", date(2025, 10, 8), market_type="options")


class TestParseAggTrades:
    def test_aggressor_convention(self) -> None:
        """is_buyer_maker=True means the SELLER aggressed, so side must be
        'sell' — getting this backwards inverts every downstream imbalance."""
        rows = list(
            parse_agg_trades_csv(
                _csv(
                    f"1,110.5,2.0,10,11,{BASE_MS + 1000},true",
                    f"2,110.6,3.0,12,13,{BASE_MS + 2000},false",
                )
            )
        )
        assert rows[0]["side"] == "sell"
        assert rows[1]["side"] == "buy"
        assert rows[0]["price"] == 110.5
        assert rows[0]["quantity"] == 2.0
        assert rows[0]["timestamp_ms"] == BASE_MS + 1000

    def test_headerless_csv_parses(self) -> None:
        text = io.StringIO(f"1,110.5,2.0,10,11,{BASE_MS},true\n")
        rows = list(parse_agg_trades_csv(text))
        assert len(rows) == 1
        assert rows[0]["side"] == "sell"

    def test_malformed_rows_skipped_not_fatal(self) -> None:
        rows = list(
            parse_agg_trades_csv(
                _csv(
                    f"1,110.5,2.0,10,11,{BASE_MS},true",
                    "2,not-a-price,3.0,12,13,abc,false",
                    "",
                    f"3,110.7,1.0,14,15,{BASE_MS + 500},false",
                )
            )
        )
        assert [r["price"] for r in rows] == [110.5, 110.7]

    def test_uppercase_bool_tolerated(self) -> None:
        rows = list(parse_agg_trades_csv(_csv(f"1,1,1,1,1,{BASE_MS},True")))
        assert rows[0]["side"] == "sell"


class TestIterDayTrades:
    def test_streams_from_zip_without_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _zip_bytes(
            f"{HEADER}\n1,110.5,2.0,10,11,{BASE_MS},true\n"
            f"2,110.6,3.0,12,13,{BASE_MS + 1000},false\n"
        )
        monkeypatch.setattr(
            "selene_space_market.tick_store.binance_archive.download_day_zip",
            lambda *a, **kw: payload,
        )
        rows = list(iter_day_trades("SOL/USDT", date(2025, 10, 8)))
        assert len(rows) == 2
        assert {r["side"] for r in rows} == {"buy", "sell"}

    def test_zip_without_csv_member_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("readme.txt", "nothing here")
        monkeypatch.setattr(
            "selene_space_market.tick_store.binance_archive.download_day_zip",
            lambda *a, **kw: buf.getvalue(),
        )
        with pytest.raises(ValueError):
            list(iter_day_trades("SOL/USDT", date(2025, 10, 8)))


class TestFetchRangeIntoStore:
    def _patch(self, monkeypatch: pytest.MonkeyPatch, payload: bytes) -> None:
        monkeypatch.setattr(
            "selene_space_market.tick_store.binance_archive.download_day_zip",
            lambda *a, **kw: payload,
        )

    def test_inserts_into_tick_store(
        self, monkeypatch: pytest.MonkeyPatch, store: TickStore
    ) -> None:
        payload = _zip_bytes(
            f"{HEADER}\n1,110.5,2.0,10,11,{BASE_MS},true\n"
            f"2,110.6,3.0,12,13,{BASE_MS + 1000},false\n"
        )
        self._patch(monkeypatch, payload)

        seen: list[tuple[date, int]] = []
        total = fetch_range_into_store(
            store,
            "SOL/USDT",
            date(2025, 10, 8),
            date(2025, 10, 9),
            on_day=lambda d, n: seen.append((d, n)),
        )

        assert total == 4  # two days × two trades
        assert [d for d, _ in seen] == [date(2025, 10, 8), date(2025, 10, 9)]
        stored = store.query("SOL/USDT")
        assert len(stored) == 4
        assert {r["exchange"] for r in stored} == {"binance"}
        assert {r["side"] for r in stored} == {"buy", "sell"}
        # round-trips through the store in the shape TickReplay expects
        assert stored[0]["timestamp_ms"] == BASE_MS

    def test_unpublished_day_is_skipped_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch, store: TickStore
    ) -> None:
        """A range ending 'today' hits the T+1 lag; that must not abort the
        whole backfill."""
        payload = _zip_bytes(f"{HEADER}\n1,110.5,2.0,10,11,{BASE_MS},true\n")
        calls = {"n": 0}

        def fake_download(*a: object, **kw: object) -> bytes:
            calls["n"] += 1
            if calls["n"] == 2:
                raise FileNotFoundError("archive not published")
            return payload

        monkeypatch.setattr(
            "selene_space_market.tick_store.binance_archive.download_day_zip",
            fake_download,
        )
        total = fetch_range_into_store(
            store, "SOL/USDT", date(2025, 10, 8), date(2025, 10, 9)
        )
        assert total == 1
        assert len(store.query("SOL/USDT")) == 1

    def test_single_day_range(
        self, monkeypatch: pytest.MonkeyPatch, store: TickStore
    ) -> None:
        payload = _zip_bytes(f"{HEADER}\n1,110.5,2.0,10,11,{BASE_MS},true\n")
        self._patch(monkeypatch, payload)
        total = fetch_range_into_store(
            store, "SOL/USDT", date(2025, 10, 8), date(2025, 10, 8)
        )
        assert total == 1
