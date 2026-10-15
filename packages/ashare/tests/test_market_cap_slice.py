"""Tests for fetch_market_cap history-path date-window splitting (US-2 LOW-1).

All EM pager calls are monkeypatched — no network access. Cache is isolated
via _read_cache/_write_cache stubs plus force=True.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from selene_space_ashare import data_fetcher

_MC_PAGE_ROWS = data_fetcher._MC_PAGE_ROWS
_MC_MAX_PAGES = data_fetcher._MC_MAX_PAGES
_TRUNCATION_ROWS = _MC_PAGE_ROWS * _MC_MAX_PAGES

_DATE_GTE = re.compile(r"TRADE_DATE>='(\d{4}-\d{2}-\d{2})'")
_DATE_LTE = re.compile(r"TRADE_DATE<='(\d{4}-\d{2}-\d{2})'")


def _parse_window(filter_expr: str) -> tuple[str | None, str | None]:
    gte = _DATE_GTE.search(filter_expr)
    lte = _DATE_LTE.search(filter_expr)
    return (gte.group(1) if gte else None), (lte.group(1) if lte else None)


def _make_rows(symbols: list[str], start: str | None, end: str | None) -> list[dict]:
    """One row per symbol per calendar day in the window (endpoints inclusive)."""
    if start and end:
        d0 = datetime.strptime(start, "%Y-%m-%d")
        d1 = datetime.strptime(end, "%Y-%m-%d")
        days = [
            (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range((d1 - d0).days + 1)
        ]
    elif start:
        days = [start]
    elif end:
        days = [end]
    else:
        days = ["2026-01-01"]
    return [
        {
            "symbol": sym,
            "date": day,
            "float_mv": 1e8,
            "total_mv": 2e8,
            "float_shares": 1e6,
        }
        for sym in symbols
        for day in days
    ]


def _symbols(n: int) -> list[str]:
    return [f"{600000 + i:06d}" for i in range(n)]


def _install_cache_stubs(monkeypatch, written: list) -> None:
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": None)
    monkeypatch.setattr(
        data_fetcher, "_write_cache", lambda df, name, subdir="": written.append(name)
    )


def test_truncated_window_is_split_by_date(monkeypatch, caplog):
    """Scenario A: >20000-row window hits max_pages -> date halves fetched."""
    symbols = _symbols(100)
    calls: list[str] = []

    def fake_pager(filter_expr: str, max_pages: int):
        calls.append(filter_expr)
        start, end = _parse_window(filter_expr)
        rows = _make_rows(symbols, start, end)
        if len(rows) >= _TRUNCATION_ROWS:
            return rows[:_TRUNCATION_ROWS], _MC_MAX_PAGES + 1, False
        return rows, max(1, (len(rows) + _MC_PAGE_ROWS - 1) // _MC_PAGE_ROWS), True

    monkeypatch.setattr(data_fetcher, "_em_mktcap_rows", fake_pager)
    written: list = []
    _install_cache_stubs(monkeypatch, written)

    df = data_fetcher.fetch_market_cap(
        symbols=symbols, start_date="2020-01-01", end_date="2020-12-31", force=True
    )

    starts = {_parse_window(c)[0] for c in calls}
    ends = {_parse_window(c)[1] for c in calls}
    assert len(starts) > 1 or len(ends) > 1, "expected date-window splitting"
    assert len(df) > _TRUNCATION_ROWS
    assert not df.duplicated(subset=["symbol", "date"]).any()
    assert df["symbol"].nunique() == 100
    assert len(written) == 1


def test_natural_page_end_makes_no_extra_requests(monkeypatch):
    """Scenario B: <500 rows on page 1 -> complete, single pager call."""
    symbols = _symbols(10)
    calls: list[str] = []

    def fake_pager(filter_expr: str, max_pages: int):
        calls.append(filter_expr)
        start, end = _parse_window(filter_expr)
        rows = _make_rows(symbols, start, end)
        return rows, 1, True

    monkeypatch.setattr(data_fetcher, "_em_mktcap_rows", fake_pager)
    written: list = []
    _install_cache_stubs(monkeypatch, written)

    df = data_fetcher.fetch_market_cap(
        symbols=symbols, start_date="2026-01-01", end_date="2026-01-20", force=True
    )

    assert len(calls) == 1
    assert len(df) == 10 * 20
    assert len(written) == 1


def test_depth_exhaustion_warns_and_returns_partial(monkeypatch, caplog):
    """Scenario C: every window truncated -> stop at depth cap, no cache."""
    symbols = _symbols(100)
    calls: list[str] = []

    def fake_pager(filter_expr: str, max_pages: int):
        calls.append(filter_expr)
        start, end = _parse_window(filter_expr)
        rows = _make_rows(symbols, start, end)
        while len(rows) < _TRUNCATION_ROWS:
            rows = rows + rows
        return rows[:_TRUNCATION_ROWS], _MC_MAX_PAGES + 1, False

    monkeypatch.setattr(data_fetcher, "_em_mktcap_rows", fake_pager)
    written: list = []
    _install_cache_stubs(monkeypatch, written)

    import logging

    with caplog.at_level(logging.WARNING, logger="selene_space_ashare.data_fetcher"):
        df = data_fetcher.fetch_market_cap(
            symbols=symbols, start_date="2020-01-01", end_date="2020-12-31", force=True
        )

    max_calls = 1 + 2 + 4 + 8
    assert len(calls) <= max_calls
    assert any("truncated at split depth" in rec.message for rec in caplog.records)
    assert not df.empty
    assert not written


def test_split_window_midpoint_partitions_closed_range():
    (a0, a1), (b0, b1) = data_fetcher._split_date_window("2020-01-01", "2020-12-31")
    assert (a0, a1) == ("2020-01-01", "2020-07-01")
    assert (b0, b1) == ("2020-07-01", "2020-12-31")


def test_split_window_open_bounds_still_shrink():
    left_open = data_fetcher._split_date_window("", "2026-09-24")
    assert left_open is not None
    (a0, a1), (b0, b1) = left_open
    assert a0 == "" and a1 < "2026-09-24"
    assert (b0, b1) == (a1, "2026-09-24")

    right_open = data_fetcher._split_date_window("2026-03-05", "")
    assert right_open is not None
    (c0, c1), (d0, d1) = right_open
    assert c0 == "2026-03-05" and c1 > "2026-03-05"
    assert (d0, d1) == (c1, "")

    assert data_fetcher._split_date_window("", "") is None
