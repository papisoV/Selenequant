"""Tests for Jin10 macro fetcher (real dates + forecast) and auto fallback.

Covers the 6 focus areas from the design review plan:
  1. Jin10 row parsing -> MacroEvent with genuine surprise
  2. surprise computation edge cases (missing / zero forecast -> None)
  3. date alignment (Jin10 date -> UTC ms matches ET->UTC expectation)
  4. fail-loud: network error -> logger.error + Jin10 returns [] -> auto falls back
  5. 429 -> exponential backoff, then fallback + ERROR log
  6. sanity check: out-of-range date / NaN -> rejected, not persisted, fallback
Plus pagination dedup and DB round-trip.

Import note: `macro` is imported directly (selene_space_alpha dir on sys.path)
to bypass selene_space_alpha/__init__.py, which pulls in vocab/vm (torch chain).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import httpx
import pytest

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from macro.fetcher import (  # noqa: E402
    MacroFetcher,
    _KNOWN_CPI_DATES,
    _covered_by_jin10,
    _date_to_utc_ms,
)
from macro.jin10_fetcher import Jin10Fetcher  # noqa: E402
from macro.models import MacroEvent, MacroEventType  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _row(date: str, actual, forecast, previous) -> list:
    return [date, actual, forecast, previous]


def _fetcher_with_responses(pages: list[list[list]]) -> Jin10Fetcher:
    """Jin10Fetcher whose _fetch_page yields canned pages, in order."""
    f = Jin10Fetcher()
    it = iter(pages)

    async def _fake_fetch_page(event_type, max_date):
        try:
            return next(it)
        except StopIteration:
            return []

    f._fetch_page = _fake_fetch_page  # type: ignore[method-assign]
    return f


async def _noop():
    return None


# ---------------------------------------------------------------------------
# 1. Jin10 row parsing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_row_to_macro_event_with_real_surprise() -> None:
    """Sample row -> MacroEvent with forecast populated and genuine surprise."""
    f = _fetcher_with_responses([
        [
            _row("2025-08-12", "0.2", "0.2", "0.3"),
            _row("2025-07-15", "0.3", "0.3", "0.1"),
            _row("2025-06-11", "0.1", "0.2", "0.2"),
        ],
    ])
    evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert len(evs) == 3
    first = evs[0]
    assert isinstance(first, MacroEvent)
    assert first.event_type == MacroEventType.CPI
    assert first.date == "2025-08-12"
    assert first.actual_value == 0.2
    assert first.forecast_value == 0.2
    assert first.previous_value == 0.3
    assert first.surprise == 0.0  # (0.2 - 0.2) / 0.2
    assert first.fred_series == "CPIAUCSL"
    assert first.release_time == "08:30"


@pytest.mark.asyncio
async def test_parse_nfp_row_units() -> None:
    """NFP is in 万 (ten-thousands); surprise uses the raw magnitudes."""
    f = _fetcher_with_responses([
        [_row("2025-08-01", "7.3", "10.6", "1.4")],
    ])
    evs = await f.fetch_macro_events(MacroEventType.NFP, "2025-01-01", "2025-12-31")
    await f.close()

    assert len(evs) == 1
    assert evs[0].surprise == pytest.approx((7.3 - 10.6) / abs(10.6))


# ---------------------------------------------------------------------------
# 2. surprise computation edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_surprise_none_when_forecast_missing() -> None:
    """Forecast None (pending release) -> surprise None, not a proxy."""
    f = _fetcher_with_responses([
        [_row("2025-09-11", None, None, "0.2")],
    ])
    evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert len(evs) == 1
    assert evs[0].actual_value is None
    assert evs[0].forecast_value is None
    assert evs[0].surprise is None


@pytest.mark.asyncio
async def test_surprise_none_when_forecast_zero() -> None:
    """Forecast 0 -> surprise None (division by zero is not a signal)."""
    f = _fetcher_with_responses([
        [_row("2025-01-15", "0.2", "0", "0.3")],
    ])
    evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert len(evs) == 1
    assert evs[0].surprise is None


# ---------------------------------------------------------------------------
# 3. date alignment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_date_alignment_matches_et_to_utc() -> None:
    """Jin10 date -> timestamp_utc must equal _date_to_utc_ms for 08:30 ET."""
    date_str = "2025-08-12"
    expected_ms = _date_to_utc_ms(date_str, "08:30")

    f = _fetcher_with_responses([
        [_row(date_str, "0.2", "0.2", "0.3")],
    ])
    evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert evs[0].timestamp_utc == expected_ms


@pytest.mark.asyncio
async def test_date_alignment_fixes_hardcoded_drift() -> None:
    """Jin10 real release date differs from the hardcoded BLS estimate for the
    same release window — the bug the plan targets. Jin10 wins where it has data."""
    # Hardcoded CPI dates include 2025-08-13; Jin10 says 2025-08-12.
    assert "2025-08-13" in _KNOWN_CPI_DATES

    f = _fetcher_with_responses([
        [_row("2025-08-12", "0.2", "0.2", "0.3")],
    ])
    evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert any(e.date == "2025-08-12" for e in evs)


# ---------------------------------------------------------------------------
# 4. fail-loud: network error -> logger.error + auto falls back
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fail_loud_network_error_logs_and_returns_empty(monkeypatch, caplog) -> None:
    """Network error inside Jin10Fetcher -> logger.error (not silent) + []."""

    class _ConnErrorClient:
        async def get(self, url, params=None):
            raise httpx.ConnectError("connection refused")

    async def _conn_error_client():
        return _ConnErrorClient()

    f = Jin10Fetcher()
    monkeypatch.setattr(f, "_get_client", _conn_error_client)

    with caplog.at_level(logging.ERROR, logger="macro.jin10_fetcher"):
        evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert any("Jin10 GET failed" in r.message for r in caplog.records)
    assert evs == []


@pytest.mark.asyncio
async def test_auto_falls_back_when_jin10_empty(monkeypatch, caplog) -> None:
    """Jin10 returns [] (any failure path) -> auto logs warning + falls back to FRED."""

    class _EmptyJin10:
        async def fetch_macro_events(self, event_type, start_date, end_date):
            return []

        async def close(self) -> None:
            pass

    # fetch_macro_events_auto does `from .jin10_fetcher import Jin10Fetcher` inside
    # the method body, so the patch must land on macro.jin10_fetcher.Jin10Fetcher.
    monkeypatch.setattr("macro.jin10_fetcher.Jin10Fetcher", _EmptyJin10)

    fetcher = MacroFetcher(fred_api_key="")
    calls: list[bool] = []

    async def _fake_fetch_macro_events(event_type, start_date, end_date):
        calls.append(True)
        return []

    monkeypatch.setattr(fetcher, "fetch_macro_events", _fake_fetch_macro_events)

    with caplog.at_level(logging.WARNING, logger="macro.fetcher"):
        result = await fetcher.fetch_macro_events_auto(
            MacroEventType.CPI, "2025-01-01", "2025-12-31"
        )

    assert calls == [True], "auto should have fallen back to FRED path"
    assert any("Jin10 unavailable" in r.message for r in caplog.records)
    assert result == []


# ---------------------------------------------------------------------------
# 5. 429 -> backoff, then fallback + ERROR
# ---------------------------------------------------------------------------

class _Always429:
    """httpx-like response always returning 429."""

    status_code = 429

    def raise_for_status(self):
        return None


class _Always429Client:
    async def get(self, url, params=None):
        return _Always429()


async def _always429_client():
    return _Always429Client()


@pytest.mark.asyncio
async def test_429_backoff_then_fallback(monkeypatch, caplog) -> None:
    """429 x3 -> exponential backoff attempts, then give up + []."""
    f = Jin10Fetcher()
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float):
        sleep_calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(f, "_get_client", _always429_client)

    with caplog.at_level(logging.WARNING, logger="macro.jin10_fetcher"):
        evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert sleep_calls == [1.0, 2.0, 4.0], "backoff schedule should be 1s/2s/4s"
    assert any("rate-limited" in r.message for r in caplog.records)
    assert evs == []


@pytest.mark.asyncio
async def test_429_exhausts_to_auto_fallback(monkeypatch, caplog) -> None:
    """End-to-end: Jin10 always 429 -> fetch_macro_events_auto falls back."""
    monkeypatch.setattr("macro.jin10_fetcher._BACKOFF_SECONDS", [0.001, 0.002, 0.004])
    monkeypatch.setattr(asyncio, "sleep", lambda d: _noop())
    # Patch the class method so the Jin10Fetcher instantiated inside
    # fetch_macro_events_auto picks up the always-429 client.
    monkeypatch.setattr(
        "macro.jin10_fetcher.Jin10Fetcher._get_client",
        lambda self: _always429_client(),
    )

    fetcher = MacroFetcher(fred_api_key="")
    calls: list[bool] = []

    async def _fake_fetch_macro_events(event_type, start_date, end_date):
        calls.append(True)
        return []

    monkeypatch.setattr(fetcher, "fetch_macro_events", _fake_fetch_macro_events)

    with caplog.at_level(logging.WARNING, logger="macro.fetcher"):
        result = await fetcher.fetch_macro_events_auto(
            MacroEventType.CPI, "2025-01-01", "2025-12-31"
        )

    assert calls == [True], "429 exhausted -> should fall back to FRED path"
    assert result == []


# ---------------------------------------------------------------------------
# 5b. 5xx -> HTTPStatusError caught -> [] (fallback, not crash)
# ---------------------------------------------------------------------------

class _Always5xx:
    """httpx-like response always returning a 5xx status."""

    status_code = 502

    def raise_for_status(self):
        request = httpx.Request("GET", "https://datacenter-api.jin10.com/reports/list_v2")
        response = httpx.Response(502, request=request)
        response.raise_for_status()  # raises httpx.HTTPStatusError


class _Always5xxClient:
    async def get(self, url, params=None):
        return _Always5xx()


async def _always5xx_client():
    return _Always5xxClient()


@pytest.mark.asyncio
async def test_5xx_caught_not_crash(monkeypatch, caplog) -> None:
    """Jin10 5xx -> raise_for_status raises HTTPStatusError -> caught, [] returned."""
    f = Jin10Fetcher()
    monkeypatch.setattr(f, "_get_client", _always5xx_client)

    with caplog.at_level(logging.ERROR, logger="macro.jin10_fetcher"):
        evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert any("HTTP 502" in r.message for r in caplog.records)
    assert evs == [], "5xx should degrade to [], not crash the script"


@pytest.mark.asyncio
async def test_5xx_exhausts_to_auto_fallback(monkeypatch, caplog) -> None:
    """End-to-end: Jin10 always 5xx -> fetch_macro_events_auto falls back."""
    monkeypatch.setattr(
        "macro.jin10_fetcher.Jin10Fetcher._get_client",
        lambda self: _always5xx_client(),
    )

    fetcher = MacroFetcher(fred_api_key="")
    calls: list[bool] = []

    async def _fake_fetch_macro_events(event_type, start_date, end_date):
        calls.append(True)
        return []

    monkeypatch.setattr(fetcher, "fetch_macro_events", _fake_fetch_macro_events)

    with caplog.at_level(logging.WARNING, logger="macro.fetcher"):
        result = await fetcher.fetch_macro_events_auto(
            MacroEventType.CPI, "2025-01-01", "2025-12-31"
        )

    assert calls == [True], "5xx -> should fall back to FRED/hardcoded path"
    assert result == []


# ---------------------------------------------------------------------------
# 5c. hardcoded top-up fuzzy match (_covered_by_jin10)
# ---------------------------------------------------------------------------

def test_covered_by_jin10_exact_match() -> None:
    assert _covered_by_jin10("2025-08-12", {"2025-08-12"})


def test_covered_by_jin10_one_day_apart() -> None:
    """Hardcoded BLS estimate one day off Jin10's real date = same release window."""
    assert _covered_by_jin10("2025-08-13", {"2025-08-12"})
    assert _covered_by_jin10("2025-08-11", {"2025-08-12"})
    assert _covered_by_jin10("2025-09-10", {"2025-09-11"})


def test_covered_by_jin10_more_than_one_day_apart() -> None:
    """Two+ days apart = genuinely different events, hardcoded date must top up."""
    assert not _covered_by_jin10("2025-08-14", {"2025-08-12"})
    assert not _covered_by_jin10("2025-08-01", {"2025-08-12"})


def test_covered_by_jin10_empty_set() -> None:
    assert not _covered_by_jin10("2025-08-12", set())


def test_covered_by_jin10_unparseable() -> None:
    assert not _covered_by_jin10("not-a-date", {"2025-08-12"})
    assert not _covered_by_jin10("2025-08-12", {"not-a-date"})


# ---------------------------------------------------------------------------
# 6. sanity check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sanity_rejects_out_of_range_date(caplog) -> None:
    """Out-of-range date -> row rejected, not persisted, fetcher returns only valid rows."""
    f = _fetcher_with_responses([
        [
            _row("1899-01-01", "0.2", "0.2", "0.3"),
            _row("2025-08-12", "0.2", "0.2", "0.3"),
        ],
    ])
    with caplog.at_level(logging.ERROR, logger="macro.jin10_fetcher"):
        evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert any("out of range" in r.message for r in caplog.records)
    assert len(evs) == 1, "only the valid row should survive"
    assert evs[0].date == "2025-08-12"


@pytest.mark.asyncio
async def test_sanity_rejects_nan_actual(caplog) -> None:
    """NaN actual -> row rejected (can't poison the DB), fetcher empty-safe."""
    f = _fetcher_with_responses([
        [_row("2025-08-12", "NaN", "0.2", "0.3")],
    ])
    with caplog.at_level(logging.ERROR, logger="macro.jin10_fetcher"):
        evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert any("non-finite" in r.message for r in caplog.records)
    assert evs == []


@pytest.mark.asyncio
async def test_sanity_rejects_unparseable_date() -> None:
    """Non-date string -> row rejected."""
    f = _fetcher_with_responses([
        [_row("not-a-date", "0.2", "0.2", "0.3")],
    ])
    evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    assert evs == []


# ---------------------------------------------------------------------------
# FOMC: attr_id 24 + Beijing->US-Eastern date correction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fomc_attr_id_registered() -> None:
    """FOMC maps to Jin10 attr_id 24 (akshare dc_usa_interest_rate_decision)."""
    from macro.jin10_fetcher import ATTR_IDS

    assert ATTR_IDS[MacroEventType.FOMC] == 24


@pytest.mark.asyncio
async def test_fomc_date_corrected_from_beijing_to_et() -> None:
    """Jin10 FOMC dates are Beijing-local (+1 day vs the 14:00 ET decision);
    the fetcher must recover the US-Eastern release date."""
    f = _fetcher_with_responses([
        [
            _row("2025-07-31", "4.5", "4.5", "4.5"),
            _row("2025-06-19", "4.5", "4.5", "4.5"),
        ],
    ])
    evs = await f.fetch_macro_events(MacroEventType.FOMC, "2025-01-01", "2025-12-31")
    await f.close()

    assert len(evs) == 2
    assert evs[0].date == "2025-07-30", "Jin10 07-31 -> ET 07-30"
    assert evs[1].date == "2025-06-18", "Jin10 06-19 -> ET 06-18"
    assert evs[0].release_time == "14:00"
    assert evs[0].fred_series == "FEDFUNDS"
    assert evs[0].surprise == 0.0  # (4.5 - 4.5) / 4.5


@pytest.mark.asyncio
async def test_fomc_caught_special_october_meeting() -> None:
    """Jin10 catches the unscheduled 2025-10-29 FOMC meeting that the hardcoded
    BLS-free list missed (Jin10 Beijing date 2025-10-30 -> ET 2025-10-29)."""
    from macro.fetcher import _KNOWN_FOMC_DATES

    assert "2025-10-29" not in _KNOWN_FOMC_DATES, "hardcoded list predates the special meeting"

    f = _fetcher_with_responses([
        [_row("2025-10-30", None, None, None)],
    ])
    evs = await f.fetch_macro_events(MacroEventType.FOMC, "2025-01-01", "2025-12-31")
    await f.close()

    assert any(e.date == "2025-10-29" for e in evs)


# ---------------------------------------------------------------------------
# Pagination dedup + DB round-trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pagination_dedup_overlapping_pages() -> None:
    """Two pages sharing a boundary row -> merged without duplicates."""
    f = _fetcher_with_responses([
        [
            _row("2025-08-12", "0.2", "0.2", "0.3"),
            _row("2025-07-15", "0.3", "0.3", "0.1"),
        ],
        [
            _row("2025-07-15", "0.3", "0.3", "0.1"),  # overlap
            _row("2025-06-11", "0.1", "0.2", "0.2"),
        ],
    ])
    evs = await f.fetch_macro_events(MacroEventType.CPI, "2025-01-01", "2025-12-31")
    await f.close()

    dates = [e.date for e in evs]
    assert len(dates) == len(set(dates)), "no duplicates after dedup"
    assert len(evs) == 3


@pytest.mark.asyncio
async def test_db_round_trip_filters_by_type(tmp_path) -> None:
    """put_events -> get_events(event_type=CPI) returns only CPI rows."""
    from macro.db import MacroEventDB

    db = MacroEventDB(tmp_path / "macro_test.db")
    db.open()
    try:
        events = [
            MacroEvent(
                event_type=MacroEventType.CPI,
                date="2025-08-12",
                release_time="08:30",
                actual_value=0.2,
                forecast_value=0.2,
                previous_value=0.3,
                surprise=0.0,
                fred_series="CPIAUCSL",
                timestamp_utc=_date_to_utc_ms("2025-08-12", "08:30"),
            ),
            MacroEvent(
                event_type=MacroEventType.NFP,
                date="2025-08-01",
                release_time="08:30",
                actual_value=7.3,
                forecast_value=10.6,
                previous_value=1.4,
                surprise=(7.3 - 10.6) / 10.6,
                fred_series="PAYEMS",
                timestamp_utc=_date_to_utc_ms("2025-08-01", "08:30"),
            ),
        ]
        rows = [
            (
                e.event_type.value, e.date, e.release_time, e.actual_value,
                e.forecast_value, e.previous_value, e.surprise,
                e.fred_series, e.timestamp_utc,
            )
            for e in events
        ]
        db.put_events(rows)

        cpi = db.get_events(event_type="CPI")
        assert len(cpi) == 1
        assert cpi.iloc[0]["event_type"] == "CPI"
        assert cpi.iloc[0]["forecast_value"] == 0.2
        assert cpi.iloc[0]["surprise"] == 0.0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_replace_events_removes_stale_ghost_rows(tmp_path) -> None:
    """replace_events deletes the type's stale rows, healing a poisoned DB.

    INSERT OR REPLACE can never remove a ghost row left by a fixed top-up bug;
    replace_events (used by the sync scripts) must converge the table to the
    source's current output.
    """
    from macro.db import MacroEventDB

    db = MacroEventDB(tmp_path / "macro_test.db")
    db.open()
    try:
        def _fallback_row(date: str) -> tuple:
            return (
                "CPI", date, "08:30", None, None, None, None,
                "CPIAUCSL", _date_to_utc_ms(date, "08:30"),
            )

        # Seed a poisoned DB: real Jin10 date + stale ghost date one day off.
        db.put_events([_fallback_row("2025-08-12"), _fallback_row("2025-08-13")])
        assert len(db.get_events(event_type="CPI")) == 2

        # Re-run the fixed sync: only the real date survives the fuzzy top-up.
        db.replace_events([_fallback_row("2025-08-12")], ("CPI",))
        cpi = db.get_events(event_type="CPI")
        assert len(cpi) == 1, "stale ghost row must be removed, not left behind"
        assert cpi.iloc[0]["date"] == "2025-08-12"

        # replace_events scoped to one type must not touch another type.
        db.put_events([
            ("NFP", "2025-08-01", "08:30", None, None, None, None, "PAYEMS",
             _date_to_utc_ms("2025-08-01", "08:30")),
        ])
        db.replace_events([_fallback_row("2025-08-12")], ("CPI",))
        assert len(db.get_events(event_type="NFP")) == 1
    finally:
        db.close()
