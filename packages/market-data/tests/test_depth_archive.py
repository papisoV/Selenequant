"""depth_archive / DepthStore — bookDepth ZIP → SQLite.

Network is never touched: the ZIP is synthesised in-memory so the tests pin
the CSV dialect, the signed-band convention, the UTC timestamp parse, and
the T+1 skip path.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest

from selene_space_market.tick_store.depth_archive import (
    archive_url,
    fetch_range_into_store,
    iter_day_depth,
    parse_book_depth_csv,
)
from selene_space_market.tick_store.depth_db import BANDS, DepthStore

HEADER = "timestamp,percentage,depth,notional"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[DepthStore]:
    """DepthStore opens a connection per call, so ":memory:" would hand
    every call a fresh empty database — a real file is required."""
    instance = DepthStore(str(tmp_path / f"depth_{uuid.uuid4().hex[:8]}.db"))
    try:
        yield instance
    finally:
        instance.close()


def _csv(*rows: str) -> io.StringIO:
    return io.StringIO("\n".join((HEADER, *rows)) + "\n")


def _zip_bytes(csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("SOLUSDT-bookDepth-2026-10-12.csv", csv_text)
    return buf.getvalue()


class TestArchiveUrl:
    def test_futures_url_shape(self) -> None:
        url = archive_url("SOL/USDT", date(2026, 10, 12))
        assert url == (
            "https://data.binance.vision/data/futures/um/daily/bookDepth/"
            "SOLUSDT/SOLUSDT-bookDepth-2026-10-12.zip"
        )

    def test_wire_form_symbol_matches_slash_form(self) -> None:
        assert archive_url("SOLUSDT", date(2026, 10, 12)) == archive_url(
            "SOL/USDT", date(2026, 10, 12)
        )


class TestParseBookDepth:
    def test_signed_bands_preserved(self) -> None:
        """Negative percentage is the bid side; flipping the sign would make
        every bid band look like an ask band downstream."""
        rows = list(
            parse_book_depth_csv(
                _csv(
                    "2026-10-12 00:00:01,-5.00,783193.87,85230774.43",
                    "2026-10-12 00:00:01,5.00,752935.00,85578620.00",
                )
            )
        )
        assert [r["percentage"] for r in rows] == [-5.0, 5.0]
        assert rows[0]["depth"] == pytest.approx(783193.87)
        assert rows[0]["notional"] == pytest.approx(85230774.43)

    def test_timestamp_is_utc_milliseconds(self) -> None:
        rows = list(
            parse_book_depth_csv(_csv("2026-10-12 00:00:01,-1.00,155042.0,17184750.0"))
        )
        want = int(
            datetime(2026, 10, 12, 0, 0, 1, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert rows[0]["timestamp_ms"] == want

    def test_header_optional(self) -> None:
        """Positional order must hold when the header is absent."""
        bare = io.StringIO("2026-10-12 00:00:01,-1.00,155042.0,17184750.0\n")
        rows = list(parse_book_depth_csv(bare))
        assert len(rows) == 1
        assert rows[0]["percentage"] == -1.0

    def test_malformed_row_skipped_not_fatal(self) -> None:
        rows = list(
            parse_book_depth_csv(
                _csv(
                    "2026-10-12 00:00:01,-1.00,155042.0,17184750.0",
                    "2026-10-12 00:00:01,not-a-number,x,y",
                    "2026-10-12 00:00:30,-1.00,162953.0,18000000.0",
                )
            )
        )
        assert [r["depth"] for r in rows] == [155042.0, 162953.0]


class TestDepthStore:
    def test_insert_and_query_roundtrip(self, store: DepthStore) -> None:
        store.insert_depth(
            [
                {
                    "exchange": "binance",
                    "symbol": "SOL/USDT",
                    "timestamp_ms": 1000,
                    "percentage": -1.0,
                    "depth": 155042.0,
                    "notional": 17184750.0,
                }
            ]
        )
        got = store.query("SOL/USDT", 0, 2000)
        assert len(got) == 1
        assert got[0]["percentage"] == -1.0
        assert store.count("SOL/USDT") == 1

    def test_snapshots_group_by_timestamp(self, store: DepthStore) -> None:
        rows = [
            {
                "exchange": "binance",
                "symbol": "SOL/USDT",
                "timestamp_ms": 1000 if band < 0 else 2000,
                "percentage": band,
                "depth": 100.0 + i,
                "notional": 1000.0 + i,
            }
            for i, band in enumerate(BANDS)
        ]
        store.insert_depth(rows)
        snaps = store.snapshots("SOL/USDT")
        assert len(snaps) == 2
        assert set(snaps[0]["bands"]) == {-5.0, -4.0, -3.0, -2.0, -1.0, -0.2}
        assert set(snaps[1]["bands"]) == {0.2, 1.0, 2.0, 3.0, 4.0, 5.0}

    def test_band_filter(self, store: DepthStore) -> None:
        store.insert_depth(
            [
                {
                    "exchange": "binance",
                    "symbol": "SOL/USDT",
                    "timestamp_ms": ts,
                    "percentage": pct,
                    "depth": 1.0,
                    "notional": 1.0,
                }
                for ts in (1000, 2000)
                for pct in (-1.0, 1.0)
            ]
        )
        assert len(store.query("SOL/USDT", percentage=-1.0)) == 2

    def test_empty_insert_is_noop(self, store: DepthStore) -> None:
        assert store.insert_depth([]) == 0
        assert store.count() == 0

    def test_bands_constant_matches_archive(self) -> None:
        assert BANDS == (
            -5.0,
            -4.0,
            -3.0,
            -2.0,
            -1.0,
            -0.2,
            0.2,
            1.0,
            2.0,
            3.0,
            4.0,
            5.0,
        )


class TestFetchRange:
    def test_inserts_and_reports_per_day(
        self, store: DepthStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = _zip_bytes(
            "\n".join(
                (
                    HEADER,
                    "2026-10-12 00:00:01,-1.00,155042.0,17184750.0",
                    "2026-10-12 00:00:01,1.00,159922.0,17872782.0",
                    "2026-10-12 00:00:30,-1.00,162953.0,18000000.0",
                    "2026-10-12 00:00:30,1.00,160000.0,17900000.0",
                )
            )
            + "\n"
        )

        def fake_download(symbol, day, **kwargs):
            return payload

        monkeypatch.setattr(
            "selene_space_market.tick_store.depth_archive._download_day_zip",
            fake_download,
        )
        seen: list[tuple[date, int]] = []
        total = fetch_range_into_store(
            store,
            "SOL/USDT",
            date(2026, 10, 12),
            date(2026, 10, 12),
            on_day=lambda d, n: seen.append((d, n)),
        )
        assert total == 4
        assert seen == [(date(2026, 10, 12), 4)]
        assert store.count("SOL/USDT") == 4

    def test_unpublished_day_is_skipped_not_fatal(
        self, store: DepthStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A range whose tail is 'today' must not abort — T+1 means the last
        day or two legitimately are not published yet."""
        calls = {"n": 0}

        def fake_download(symbol, day, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise FileNotFoundError("not published")
            return _zip_bytes(HEADER + "\n2026-10-12 00:00:01,-1.00,1.0,1.0\n")

        monkeypatch.setattr(
            "selene_space_market.tick_store.depth_archive._download_day_zip",
            fake_download,
        )
        total = fetch_range_into_store(
            store, "SOL/USDT", date(2026, 10, 11), date(2026, 10, 12)
        )
        assert total == 1

    def test_iter_day_depth_streams_rows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "selene_space_market.tick_store.depth_archive._download_day_zip",
            lambda *a, **k: _zip_bytes(
                HEADER + "\n2026-10-12 00:00:01,-1.00,155042.0,17184750.0\n"
            ),
        )
        rows = list(iter_day_depth("SOL/USDT", date(2026, 10, 12)))
        assert len(rows) == 1
        assert rows[0]["depth"] == pytest.approx(155042.0)
