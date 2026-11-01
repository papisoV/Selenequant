"""Async fetcher for FRED API + yfinance macro data.

FRED provides release dates and economic observations (free API key required).
yfinance provides VIX/DXY OHLCV data (no key, but DXY ticker is unreliable).
Fallback chain: yfinance -> FRED daily series.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from selene_space_core.resilience import OutboundGateway

from .models import DxyDaily, MacroEvent, MacroEventType, VixDaily

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# FRED series config for CPI/NFP (observation-based event detection)
_FRED_SERIES_CONFIG: dict[MacroEventType, dict[str, Any]] = {
    MacroEventType.CPI: {
        "series_id": "CPIAUCSL",
        "default_time": "08:30",
    },
    MacroEventType.NFP: {
        "series_id": "PAYEMS",
        "default_time": "08:30",
    },
}

# Known FOMC meeting dates (public info, published at start of each year)
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
_KNOWN_FOMC_DATES: list[str] = [
    # 2024
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17",
    # 2026
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
]

# Known CPI release dates (public info, Bureau of Labor Statistics schedule)
# Typically released 10-13th of the following month, at 08:30 ET
# Source: https://www.bls.gov/schedule/news_release/cpi.htm
_KNOWN_CPI_DATES: list[str] = [
    # 2024
    "2024-01-11", "2024-02-13", "2024-03-12", "2024-04-10",
    "2024-05-15", "2024-06-12", "2024-07-11", "2024-08-14",
    "2024-09-11", "2024-10-10", "2024-11-13", "2024-12-11",
    # 2025
    "2025-01-15", "2025-02-12", "2025-03-12", "2025-04-10",
    "2025-05-13", "2025-06-11", "2025-07-10", "2025-08-13",
    "2025-09-10", "2025-10-10", "2025-11-12", "2025-12-10",
    # 2026
    "2026-01-14", "2026-02-12", "2026-03-11", "2026-04-10",
    "2026-05-13", "2026-06-10", "2026-07-10",
]

# Known NFP (Employment Situation) release dates
# Typically released first Friday of the month at 08:30 ET
# Source: https://www.bls.gov/schedule/news_release/empsit.htm
_KNOWN_NFP_DATES: list[str] = [
    # 2024
    "2024-01-05", "2024-02-02", "2024-03-08", "2024-04-05",
    "2024-05-03", "2024-06-07", "2024-07-05", "2024-08-02",
    "2024-09-06", "2024-10-04", "2024-11-01", "2024-12-06",
    # 2025
    "2025-01-10", "2025-02-07", "2025-03-07", "2025-04-04",
    "2025-05-02", "2025-06-06", "2025-07-04", "2025-08-01",
    "2025-09-05", "2025-10-03", "2025-11-07", "2025-12-05",
    # 2026
    "2026-01-09", "2026-02-06", "2026-03-06", "2026-04-03",
    "2026-05-01", "2026-06-05", "2026-07-03",
]


def _date_to_utc_ms(date_str: str, time_str: str) -> int:
    """Convert US Eastern date+time to UTC ms epoch."""
    if time_str:
        dt_naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
    else:
        dt_naive = datetime.strptime(date_str, "%Y-%m-%d")
    dt_et = dt_naive.replace(tzinfo=ET)
    dt_utc = dt_et.astimezone(UTC)
    return int(dt_utc.timestamp() * 1000)


def _compute_surprise(actual: float | None, forecast: float | None) -> float | None:
    if actual is None or forecast is None or forecast == 0:
        return None
    return (actual - forecast) / abs(forecast)


def _covered_by_jin10(date: str, jin10_dates: set[str]) -> bool:
    """True if `date` is within 1 day of any Jin10 release date.

    Jin10's real release date wins over the hardcoded BLS estimate: when a
    hardcoded date falls inside the same release window (the BLS/Fed calendar
    rarely lands more than a day off), the top-up must skip it, or the DB ends
    up with near-duplicate events that fire the guard on a non-release day.
    """
    try:
        target = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return False
    for d in jin10_dates:
        try:
            if abs((datetime.strptime(d, "%Y-%m-%d") - target).days) <= 1:
                return True
        except ValueError:
            continue
    return False


class MacroFetcher:
    """Async fetcher for FRED + yfinance macro data."""

    def __init__(
        self,
        *,
        fred_api_key: str = "",
        proxy_url: str | None = None,
    ) -> None:
        self._fred_api_key = fred_api_key
        self._proxy_url = proxy_url
        self._client: OutboundGateway | None = None

    async def _get_client(self) -> OutboundGateway:
        if self._client is None or self._client.is_closed:
            self._client = OutboundGateway(
                proxy=self._proxy_url,
                default_timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                default_circuit=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # FRED API
    # ------------------------------------------------------------------

    async def _fred_get(self, endpoint: str, params: dict[str, Any]) -> Any:
        """Make a FRED API request."""
        client = await self._get_client()
        params["api_key"] = self._fred_api_key
        params["file_type"] = "json"
        url = f"https://api.stlouisfed.org/fred/{endpoint}"
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("FRED API %s failed: %s", endpoint, e)
            return None

    async def fetch_series_observations(
        self,
        series_id: str,
        start_date: str,
        end_date: str,
    ) -> dict[str, float]:
        """Fetch FRED series observations. Returns {date_str: value}."""
        raw = await self.fetch_series_observations_raw(series_id, start_date, end_date)
        return {d: v for d, v in raw}

    async def fetch_series_observations_raw(
        self,
        series_id: str,
        start_date: str,
        end_date: str,
    ) -> list[tuple[str, float]]:
        """Fetch FRED series observations as ordered (date, value) pairs."""
        data = await self._fred_get(
            "series/observations",
            {
                "series_id": series_id,
                "observation_start": start_date,
                "observation_end": end_date,
                "sort_order": "asc",
            },
        )
        if data is None:
            return []
        result: list[tuple[str, float]] = []
        for obs in data.get("observations", []):
            date_str = obs.get("date", "")
            value_str = obs.get("value", ".")
            if date_str and value_str != ".":
                try:
                    result.append((date_str, float(value_str)))
                except (ValueError, TypeError):
                    pass
        return result

    async def fetch_macro_events(
        self,
        event_type: MacroEventType,
        start_date: str,
        end_date: str,
    ) -> list[MacroEvent]:
        """Fetch macro events using hardcoded known release dates + FRED actual values."""
        known_dates_map: dict[MacroEventType, list[str]] = {
            MacroEventType.FOMC: _KNOWN_FOMC_DATES,
            MacroEventType.CPI: _KNOWN_CPI_DATES,
            MacroEventType.NFP: _KNOWN_NFP_DATES,
        }
        series_map: dict[MacroEventType, str] = {
            MacroEventType.FOMC: "FEDFUNDS",
            MacroEventType.CPI: "CPIAUCSL",
            MacroEventType.NFP: "PAYEMS",
        }
        time_map: dict[MacroEventType, str] = {
            MacroEventType.FOMC: "14:00",
            MacroEventType.CPI: "08:30",
            MacroEventType.NFP: "08:30",
        }

        dates = [d for d in known_dates_map.get(event_type, []) if start_date <= d <= end_date]
        if not dates:
            logger.warning("No known dates for %s in range %s to %s", event_type.value, start_date, end_date)
            return []

        series_id = series_map.get(event_type, "")
        # Fetch observations — use wide range to find the value for each release date
        actuals = await self.fetch_series_observations(series_id, start_date, end_date)

        events: list[MacroEvent] = []
        prev_value: float | None = None
        default_time = time_map.get(event_type, "")

        for date_str in dates:
            # Find actual value: observation date may differ from release date
            # For monthly data (CPI/NFP), the obs date is the reference month (1st),
            # so find the observation with date <= release date
            actual: float | None = None
            for d in sorted(actuals.keys(), reverse=True):
                if d <= date_str:
                    actual = actuals[d]
                    break

            # FRED has no forecast value, so surprise must be None — a month-over-
            # month proxy (prev_value as forecast) would be fake data. Honest degrade.
            surprise: float | None = None
            ts_utc = _date_to_utc_ms(date_str, default_time)

            events.append(
                MacroEvent(
                    event_type=event_type,
                    date=date_str,
                    release_time=default_time,
                    actual_value=actual,
                    forecast_value=None,
                    previous_value=prev_value,
                    surprise=surprise,
                    fred_series=series_id,
                    timestamp_utc=ts_utc,
                )
            )
            prev_value = actual

        logger.info(
            "Fetched %d %s events (%s to %s)",
            len(events), event_type.value, start_date, end_date,
        )
        return events

    async def fetch_macro_events_auto(
        self,
        event_type: MacroEventType,
        start_date: str,
        end_date: str,
    ) -> list[MacroEvent]:
        """Fetch macro events: Jin10 first (real dates + forecast), fallback to FRED.

        Fallback triggers on any of: Jin10 network/parse error, HTTP 429 backoff
        exhausted, sanity check failure, or empty result. Degradation is loud:
        Jin10 logs its own ERROR internally; the fallback path logs a WARNING here.
        """
        from .jin10_fetcher import Jin10Fetcher

        jin10 = Jin10Fetcher()
        try:
            events = await jin10.fetch_macro_events(event_type, start_date, end_date)
        finally:
            await jin10.close()

        if events:
            return events

        logger.warning(
            "Jin10 unavailable for %s, falling back to FRED/hardcoded dates",
            event_type.value,
        )
        return await self.fetch_macro_events(event_type, start_date, end_date)

    # ------------------------------------------------------------------
    # FRED daily series (VIX, DXY)
    # ------------------------------------------------------------------

    async def fetch_vix_fred(self, start_date: str, end_date: str) -> list[VixDaily]:
        """Fetch VIX daily close from FRED (VIXCLS series)."""
        data = await self._fred_get(
            "series/observations",
            {
                "series_id": "VIXCLS",
                "observation_start": start_date,
                "observation_end": end_date,
            },
        )
        if data is None:
            return []
        result: list[VixDaily] = []
        for obs in data.get("observations", []):
            date_str = obs.get("date", "")
            value_str = obs.get("value", ".")
            if date_str and value_str != ".":
                try:
                    close = float(value_str)
                    ts = _date_to_utc_ms(date_str, "16:00")  # market close
                    result.append(
                        VixDaily(
                            date=date_str,
                            open=close,  # FRED only has close
                            high=close,
                            low=close,
                            close=close,
                            timestamp=ts,
                        )
                    )
                except (ValueError, TypeError):
                    pass
        logger.info("Fetched %d VIX daily rows from FRED", len(result))
        return result

    async def fetch_dxy_fred(self, start_date: str, end_date: str) -> list[DxyDaily]:
        """Fetch DXY daily close from FRED (DTWEXBGS series)."""
        data = await self._fred_get(
            "series/observations",
            {
                "series_id": "DTWEXBGS",
                "observation_start": start_date,
                "observation_end": end_date,
            },
        )
        if data is None:
            return []
        result: list[DxyDaily] = []
        for obs in data.get("observations", []):
            date_str = obs.get("date", "")
            value_str = obs.get("value", ".")
            if date_str and value_str != ".":
                try:
                    close = float(value_str)
                    ts = _date_to_utc_ms(date_str, "17:00")  # forex close
                    result.append(DxyDaily(date=date_str, close=close, timestamp=ts))
                except ():
                    pass
        logger.info("Fetched %d DXY daily rows from FRED", len(result))
        return result

    # ------------------------------------------------------------------
    # yfinance fallback
    # ------------------------------------------------------------------

    async def fetch_vix_yfinance(self, start_date: str, end_date: str) -> list[VixDaily]:
        """Fetch VIX OHLCV from yfinance (has OHLC, not just close)."""
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed, skipping yfinance VIX fetch")
            return []

        def _download() -> Any:
            return yf.download("^VIX", start=start_date, end=end_date, progress=False)

        try:
            df = await asyncio.to_thread(_download)
            if df is None or df.empty:
                return []
            result: list[VixDaily] = []
            for date_idx, row in df.iterrows():
                date_str = date_idx.strftime("%Y-%m-%d")  # type: ignore[union-attr]
                close = float(row["Close"].iloc[0]) if hasattr(row["Close"], "iloc") else float(row["Close"])
                open_ = float(row["Open"].iloc[0]) if hasattr(row["Open"], "iloc") else float(row["Open"])
                high = float(row["High"].iloc[0]) if hasattr(row["High"], "iloc") else float(row["High"])
                low = float(row["Low"].iloc[0]) if hasattr(row["Low"], "iloc") else float(row["Low"])
                ts = _date_to_utc_ms(date_str, "16:00")
                result.append(
                    VixDaily(date=date_str, open=open_, high=high, low=low, close=close, timestamp=ts)
                )
            logger.info("Fetched %d VIX daily rows from yfinance", len(result))
            return result
        except Exception as e:
            logger.error("yfinance VIX fetch failed: %s", e)
            return []

    async def fetch_dxy_yfinance(self, start_date: str, end_date: str) -> list[DxyDaily]:
        """Fetch DXY from yfinance (DX-Y.NYB ticker, intermittent 500 errors)."""
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance not installed, skipping yfinance DXY fetch")
            return []

        def _download() -> Any:
            return yf.download("DX-Y.NYB", start=start_date, end=end_date, progress=False)

        try:
            df = await asyncio.to_thread(_download)
            if df is None or df.empty:
                logger.warning("yfinance DXY returned empty (known intermittent issue)")
                return []
            result: list[DxyDaily] = []
            for date_idx, row in df.iterrows():
                date_str = date_idx.strftime("%Y-%m-%d")  # type: ignore[union-attr]
                close = float(row["Close"].iloc[0]) if hasattr(row["Close"], "iloc") else float(row["Close"])
                ts = _date_to_utc_ms(date_str, "17:00")
                result.append(DxyDaily(date=date_str, close=close, timestamp=ts))
            logger.info("Fetched %d DXY daily rows from yfinance", len(result))
            return result
        except Exception as e:
            logger.error("yfinance DXY fetch failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Smart fetch with fallback
    # ------------------------------------------------------------------

    async def fetch_vix(self, start_date: str, end_date: str) -> list[VixDaily]:
        """Fetch VIX with fallback: yfinance (OHLC) -> FRED (close only)."""
        rows = await self.fetch_vix_yfinance(start_date, end_date)
        if rows:
            return rows
        logger.info("yfinance VIX failed, falling back to FRED VIXCLS")
        return await self.fetch_vix_fred(start_date, end_date)

    async def fetch_dxy(self, start_date: str, end_date: str) -> list[DxyDaily]:
        """Fetch DXY with fallback: yfinance -> FRED DTWEXBGS."""
        rows = await self.fetch_dxy_yfinance(start_date, end_date)
        if rows:
            return rows
        logger.info("yfinance DXY failed, falling back to FRED DTWEXBGS")
        return await self.fetch_dxy_fred(start_date, end_date)
