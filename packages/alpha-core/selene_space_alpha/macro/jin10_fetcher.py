"""Async fetcher for Jin10 datacenter macro event calendar.

Jin10 (金十数据) is a free, key-less reverse-engineered API that returns real
release dates + forecast + actual for US macro events. Unlike the FRED/hardcoded
path, this gives us a genuine forecast so surprise = (actual - forecast)/|forecast|
is meaningful instead of a month-over-month proxy.

Usage is fail-loud: any network / parse / sanity failure logs ERROR and returns
[], so the caller can fall back to FRED/hardcoded dates. HTTP 429 gets bounded
exponential backoff (1s/2s/4s, max 3 attempts).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import httpx

from selene_space_core.resilience import (
    CircuitBreaker,
    CircuitOpenError,
    OutboundGateway,
)

from .fetcher import _compute_surprise, _date_to_utc_ms
from .models import MacroEvent, MacroEventType

logger = logging.getLogger(__name__)

# Jin10 datacenter API (reverse-engineered, key-less). Values are overridable via
# env for header drift: JIN10_APP_ID / JIN10_CSRF_TOKEN / JIN10_VERSION.
JIN10_BASE_URL = "https://datacenter-api.jin10.com"
JIN10_APP_ID = "rU6QIu7JHe2gOUeR"
JIN10_CSRF_TOKEN = "x-csrf-token"
JIN10_VERSION = "1.0.0"

# Jin10 report attr_id per macro event type. CPI/NFP verified by live probe
# (2026-08-15); FOMC = 24 confirmed via akshare dc_usa_interest_rate_decision
# (economic/macro_bank.py) + live probe (returns %, one row per FOMC decision).
ATTR_IDS: dict[MacroEventType, int] = {
    MacroEventType.CPI: 9,
    MacroEventType.NFP: 33,
    MacroEventType.FOMC: 24,
}

# Release time per event type (US Eastern). Jin10 rows carry the date only.
_RELEASE_TIMES: dict[MacroEventType, str] = {
    MacroEventType.CPI: "08:30",
    MacroEventType.NFP: "08:30",
    MacroEventType.FOMC: "14:00",
}

# Canonical FRED series id per event type (stored in fred_series for provenance).
_FRED_SERIES: dict[MacroEventType, str] = {
    MacroEventType.CPI: "CPIAUCSL",
    MacroEventType.NFP: "PAYEMS",
    MacroEventType.FOMC: "FEDFUNDS",
}

# Jin10 datetimes are Beijing-local. FOMC announces at 14:00 ET, which is 02:00 the
# NEXT day in Beijing, so Jin10's FOMC date is +1 day vs the US-Eastern release
# date (verified: Jin10 2025-07-31 <-> BLS 2025-07-30, etc.). CPI/NFP announce at
# 08:30 ET = same-day Beijing, shift 0. Corrections are applied to the raw date to
# recover the US-Eastern date before building the MacroEvent.
_DATE_SHIFT_DAYS: dict[MacroEventType, int] = {
    MacroEventType.FOMC: -1,
}

# Pagination params confirmed against akshare economic/macro_bank.py + live probe
# (2026-08-15): `limit` is honored (verified up to 200+; 100 covers 2024-2026 in
# one page) and `max_date` filters rows by `date <= max_date` (verified at the
# boundary). akshare pages with `max_date = last_date - 1 day`; we page with
# `max_date = page_min` and dedup the one-row overlap instead.
_PAGE_SIZE = 100          # covers 2024-2026 in a single page; verified supported
_MAX_PAGES = 10           # hard cap (1000 rows) to bound runaway pagination
_BACKOFF_SECONDS = [1.0, 2.0, 4.0]
_MIN_YEAR, _MAX_YEAR = 1900, 2099


def _as_float(value: Any) -> float | None:
    """Coerce a Jin10 value (str, int, float, None) to float or None."""
    if value is None or value == "":
        return None
    return float(value)


class _Jin10Gateway(OutboundGateway):
    """OutboundGateway that injects the Jin10 auth headers on every request.

    ``OutboundGateway`` has no client-level ``headers=`` option (the previous
    ``httpx.AsyncClient(headers=...)`` did), and the caller's ``client.get(url,
    params=params)`` must stay byte-identical, so the headers are defaulted here
    instead of at each call site.
    """

    def __init__(self, *, headers: dict[str, str]) -> None:
        super().__init__(
            default_timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            default_circuit=True,
        )
        self._default_headers = headers

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: httpx.Timeout | float | None = None,
        circuit: CircuitBreaker | str | None = None,
    ) -> httpx.Response:
        """GET with the Jin10 auth headers merged in by default."""
        merged = {**self._default_headers, **(headers or {})}
        return await super().get(
            url,
            params=params,
            headers=merged,
            timeout=timeout,
            circuit=circuit,
        )


class Jin10Fetcher:
    """Fetch CPI/NFP macro events (date, actual, forecast, previous) from Jin10."""

    def __init__(self) -> None:
        import os

        self._base_url = JIN10_BASE_URL
        self._headers = {
            "x-app-id": os.environ.get("JIN10_APP_ID", JIN10_APP_ID),
            "x-csrf-token": os.environ.get("JIN10_CSRF_TOKEN", JIN10_CSRF_TOKEN),
            "x-version": os.environ.get("JIN10_VERSION", JIN10_VERSION),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        }
        self._client: OutboundGateway | None = None

    async def _get_client(self) -> OutboundGateway:
        if self._client is None or self._client.is_closed:
            self._client = _Jin10Gateway(headers=self._headers)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get_with_backoff(self, url: str, params: dict[str, Any]) -> httpx.Response | None:
        """GET with bounded exponential backoff on 429. None = gave up (caller logs)."""
        client = await self._get_client()
        for attempt, delay in enumerate(_BACKOFF_SECONDS):
            try:
                resp = await client.get(url, params=params)
            except (httpx.HTTPError, CircuitOpenError) as e:
                logger.error("Jin10 GET failed (attempt %d): %s", attempt + 1, e)
                return None
            if resp.status_code == 429:
                logger.warning(
                    "Jin10 rate-limited (429), backing off %.0fs (attempt %d/%d)",
                    delay, attempt + 1, len(_BACKOFF_SECONDS),
                )
                await asyncio.sleep(delay)
                continue
            try:
                resp.raise_for_status()
            except httpx.HTTPError as e:
                logger.error(
                    "Jin10 HTTP %s (attempt %d): %s",
                    resp.status_code, attempt + 1, e,
                )
                return None
            return resp
        logger.error("Jin10 rate-limited (429) after %d attempts, giving up", len(_BACKOFF_SECONDS))
        return None

    def _sanity_check(self, event_type: MacroEventType, date_str: str, actual: Any, forecast: Any) -> bool:
        """Reject implausible rows before they can poison the event DB.

        A date outside [1900, 2099] or a value that can't parse to a finite float
        means the API shape drifted — treat as failure (skip row, caller sees empty).
        None values are legitimate (pending release / missing forecast), so they pass.
        """
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if not (_MIN_YEAR <= dt.year <= _MAX_YEAR):
                logger.error("Jin10 sanity failed: date out of range %s", date_str)
                return False
        except ValueError:
            logger.error("Jin10 sanity failed: unparseable date %r", date_str)
            return False
        for label, value in (("actual", actual), ("forecast", forecast)):
            if value is None or value == "":
                continue
            try:
                parsed = float(value)
            except (ValueError, TypeError):
                logger.error("Jin10 sanity failed: non-numeric %s=%r", label, value)
                return False
            if parsed != parsed or parsed in (float("inf"), float("-inf")):
                logger.error("Jin10 sanity failed: non-finite %s=%r", label, value)
                return False
        if event_type not in ATTR_IDS:
            logger.error("Jin10 sanity failed: unknown event_type %s", event_type)
            return False
        return True

    async def _fetch_page(self, event_type: MacroEventType, max_date: str | None) -> list[list[Any]]:
        """Fetch one page of raw [date, actual, forecast, previous] rows, newest first."""
        url = f"{self._base_url}/reports/list_v2"
        params: dict[str, Any] = {
            "category": "ec",
            "attr_id": ATTR_IDS[event_type],
            "limit": _PAGE_SIZE,
        }
        if max_date:
            params["max_date"] = max_date
        resp = await self._get_with_backoff(url, params)
        if resp is None:
            return []
        try:
            payload = resp.json()
            if payload.get("status") != 200:
                logger.error("Jin10 status != 200: %s", payload.get("status"))
                return []
            return payload.get("data", {}).get("values", []) or []
        except ValueError as e:
            logger.error("Jin10 response parse failed: %s", e)
            return []

    async def fetch_macro_events(
        self,
        event_type: MacroEventType,
        start_date: str,
        end_date: str,
    ) -> list[MacroEvent]:
        """Fetch CPI/NFP events from Jin10 for the [start_date, end_date] range.

        Returns MacroEvents with genuine forecast/surprise and real release dates.
        Empty on failure (fail-loud: ERROR logged by the caller path). Paginates via
        the max_date cursor until rows stop growing or _MAX_PAGES is reached.
        """
        if event_type not in ATTR_IDS:
            logger.error("Jin10 has no attr_id for %s, returning []", event_type)
            return []

        release_time = _RELEASE_TIMES[event_type]
        fred_series = _FRED_SERIES[event_type]

        seen: set[tuple[str, str]] = set()
        events: list[MacroEvent] = []
        cursor: str | None = None
        prev_page_min: str | None = None

        for _ in range(_MAX_PAGES):
            rows = await self._fetch_page(event_type, cursor)
            if not rows:
                break

            for row in rows:
                if not isinstance(row, list) or len(row) < 4:
                    logger.error("Jin10 row shape unexpected: %r", row)
                    continue
                date_str, actual_raw, forecast_raw, _previous_raw = row[:4]
                if not isinstance(date_str, str) or not self._sanity_check(event_type, date_str, actual_raw, forecast_raw):
                    continue
                # Recover the US-Eastern release date from Jin10's Beijing-local date.
                date_et = date_str
                shift = _DATE_SHIFT_DAYS.get(event_type, 0)
                if shift:
                    date_et = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=shift)).strftime("%Y-%m-%d")
                key = (event_type.value, date_et)
                if key in seen:
                    continue
                if not (start_date <= date_et <= end_date):
                    continue
                seen.add(key)
                actual = _as_float(actual_raw)
                forecast = _as_float(forecast_raw)
                events.append(
                    MacroEvent(
                        event_type=event_type,
                        date=date_et,
                        release_time=release_time,
                        actual_value=actual,
                        forecast_value=forecast,
                        previous_value=_as_float(_previous_raw),
                        surprise=_compute_surprise(actual, forecast),
                        fred_series=fred_series,
                        timestamp_utc=_date_to_utc_ms(date_et, release_time),
                    )
                )

            # Advance the max_date cursor past the oldest date seen this page.
            page_min = min((r[0] for r in rows if isinstance(r[0], str)), default=None)
            if page_min is None:
                break
            if page_min == prev_page_min:
                break  # no progress -> fully paginated
            if page_min < start_date:
                break  # past the requested window's lower bound; later pages only older
            prev_page_min = page_min
            cursor = page_min

        logger.info("Fetched %d %s events from Jin10 (%s to %s)", len(events), event_type.value, start_date, end_date)
        return events
