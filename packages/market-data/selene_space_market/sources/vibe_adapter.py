"""Vibe loader adapter — wraps vendored vibe loaders as ExchangeSource.

Phase 3.8 wiring piece. Each vibe loader (yfinance, akshare, tushare,
ccxt, okx, dexscreener, coingecko, etc.) is exposed as a separate
``VibeLoaderSource`` instance namespaced under ``exchange="vibe:<id>"``
so it can be registered in ``MarketDataService._sources`` and dispatched
to via the standard ``fetch_klines_rest(exchange="vibe:<id>", ...)`` path.

The adapter translates selene's REST kline contract (timestamp in ms,
limit, end_time/start_time) to vibe's date-range contract (YYYY-MM-DD
strings, returns ``pd.DataFrame`` with DatetimeIndex). Vibe's framework
returns daily/hourly OHLCV DataFrames; we convert each row into a
``KlineEvent`` consistent with what
``BinanceSource.fetch_klines_rest`` produces.

Design notes:
- Vibe's ``fetch_klines_via_vibe`` is **synchronous** and may perform
  network I/O. We offload it to a thread via ``asyncio.to_thread`` so
  the selene async event loop is not blocked.
- The adapter implements the full ``ExchangeSource`` Protocol surface
  required by ``MarketDataService.register_source`` (start/stop/
  subscribe/on_ticker/etc.) as no-ops — vibe loaders are pull-only
  REST sources, they do not stream WS.
- Idempotent: constructing twice with the same ``loader_id`` produces
  equal-config sources; ``register_source`` will replace the prior
  entry by key.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from selene_space_market.source import ExchangeSourceConfig
from selene_space_types.market import (
    KlineEvent,
    MarketType,
    OrderBookL2Snapshot,
    TickerEvent,
)

logger = logging.getLogger(__name__)


_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1wk": 604_800_000,
    "1mo": 2_592_000_000,
}


def _interval_to_ms(interval: str) -> int:
    """Return milliseconds for *interval*, defaulting to 1h."""
    if interval in _INTERVAL_MS:
        return _INTERVAL_MS[interval]
    # Vibe uses '1D' / '1H' / '1W' / '1M' uppercase variants
    lower = interval.lower()
    return _INTERVAL_MS.get(lower, 3_600_000)


def _vibe_interval(interval: str) -> str:
    """Normalize a selene interval to vibe's expected form.

    Vibe's ``fetch_market_data`` expects ``1d`` / ``1h`` / ``4h`` /
    ``1wk`` / ``1mo``. Selene already uses the same lower-case form
    for the common cases; we just pass through.
    """
    return interval.lower()


def _ms_to_date(ms: int) -> str:
    """Convert millisecond epoch to ``YYYY-MM-DD`` for vibe."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class VibeLoaderSource:
    """ExchangeSource-compatible adapter for a single vibe loader.

    One instance wraps one vibe loader id (e.g. ``"akshare"``). The
    exchange namespace is ``"vibe:<loader_id>"`` so callers dispatch
    via ``fetch_klines_rest(exchange="vibe:akshare", ...)``.
    """

    def __init__(self, loader_id: str, market_type: MarketType = "spot") -> None:
        self._loader_id = loader_id
        self._market_type: MarketType = market_type
        self._config = ExchangeSourceConfig(
            exchange=f"vibe:{loader_id}",
            market_type=market_type,
        )
        self._is_running = False

    # ------------------------------------------------------------------
    # ExchangeSource protocol — identity
    # ------------------------------------------------------------------

    @property
    def config(self) -> ExchangeSourceConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def loader_id(self) -> str:
        return self._loader_id

    # ------------------------------------------------------------------
    # ExchangeSource protocol — lifecycle (no-ops; pull-only REST)
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._is_running = True
        logger.debug("VibeLoaderSource '%s' started (no-op)", self._loader_id)

    async def stop(self) -> None:
        self._is_running = False
        logger.debug("VibeLoaderSource '%s' stopped (no-op)", self._loader_id)

    # ------------------------------------------------------------------
    # ExchangeSource protocol — subscription (no-ops; pull-only)
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        symbol: str,
        *,
        ticker: bool = True,
        kline: bool = True,
        depth: bool = True,
        trade: bool = True,
        kline_interval: str | None = None,
    ) -> None:
        # Vibe loaders do not stream — subscription is a no-op.
        return

    async def unsubscribe(self, symbol: str) -> None:
        return

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return None

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return None

    def get_klines(self, symbol: str, limit: int = 100) -> list[KlineEvent]:
        return []

    def get_orderbook(self, symbol: str) -> OrderBookL2Snapshot | None:
        return None

    def get_symbols(self) -> list[str]:
        return []

    def on_ticker(self, callback: Callable[[TickerEvent], Any]) -> None:
        return

    def on_kline(self, callback: Callable[[KlineEvent], Any]) -> None:
        return

    def on_orderbook(self, callback: Callable[[OrderBookL2Snapshot], Any]) -> None:
        return

    # ------------------------------------------------------------------
    # REST klines — the only real method
    # ------------------------------------------------------------------

    async def fetch_klines_rest(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 200,
        end_time: int | None = None,
        start_time: int | None = None,
    ) -> list[KlineEvent]:
        """Fetch klines from the wrapped vibe loader.

        Translates ``limit``/``end_time``/``start_time`` to vibe's
        ``start``/``end`` date strings and converts the returned
        ``pd.DataFrame`` into a list of :class:`KlineEvent`.

        Args:
            symbol: ticker, e.g. ``"AAPL"`` / ``"600519.SH"`` /
                ``"BTCUSDT"``.
            interval: ``"1d"`` / ``"1h"`` / ``"4h"`` / ``"1wk"`` /
                ``"1mo"``. Many vibe loaders are daily-only.
            limit: max candles to return (post-filter).
            end_time: ms epoch; if given, fetches candles *before* it.
            start_time: ms epoch; if given, fetches candles *after* it.
        """
        # Build date range. If neither bound provided, default to a
        # generous lookback so the loader actually returns something.
        now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        ivl_ms = _interval_to_ms(interval)
        if start_time is not None and end_time is not None:
            start_str = _ms_to_date(start_time)
            end_str = _ms_to_date(end_time)
        elif start_time is not None:
            start_str = _ms_to_date(start_time)
            end_str = _ms_to_date(now_ms)
        elif end_time is not None:
            end_str = _ms_to_date(end_time)
            # Look back ~limit candles from end_time
            lookback_ms = max(limit, 1) * ivl_ms
            start_str = _ms_to_date(end_time - lookback_ms)
        else:
            end_str = _ms_to_date(now_ms)
            lookback_ms = max(limit, 1) * ivl_ms
            start_str = _ms_to_date(now_ms - lookback_ms)

        try:
            df = await asyncio.to_thread(
                _call_vibe_fetch,
                self._loader_id,
                symbol,
                _vibe_interval(interval),
                start_str,
                end_str,
            )
        except Exception:
            logger.warning(
                "VibeLoaderSource '%s' fetch_klines failed for %s (%s)",
                self._loader_id, symbol, interval,
                exc_info=True,
            )
            return []

        if df is None:
            return []

        rows = _dataframe_to_kline_events(
            df,
            exchange=self._config.exchange,
            market_type=self._market_type,
            symbol=symbol,
            interval=interval,
        )
        # Apply start/end filtering (vibe returns inclusive date range,
        # which can overshoot either bound by up to one day).
        if start_time is not None:
            rows = [k for k in rows if k.timestamp >= start_time]
        if end_time is not None:
            rows = [k for k in rows if k.timestamp <= end_time]
        # Apply limit (most recent first per selene convention is not
        # required here — callers sort if needed; we keep chronological
        # order and cap to limit).
        if len(rows) > limit:
            rows = rows[-limit:]
        return rows


# ---------------------------------------------------------------------------
# Module-level helpers (kept pure / testable without the class)
# ---------------------------------------------------------------------------


def _call_vibe_fetch(
    loader_id: str,
    symbol: str,
    interval: str,
    start: str,
    end: str,
) -> pd.DataFrame | None:
    """Lazy-import and invoke vibe's ``fetch_klines_via_vibe``.

    Kept as a module-level function so tests can monkeypatch
    ``selene_space_market.sources.vibe_adapter._call_vibe_fetch`` to
    avoid hitting the network.
    """
    from subsystems.vibe.bridge.datasources.preset import fetch_klines_via_vibe

    result = fetch_klines_via_vibe(
        symbol=symbol,
        interval=interval,
        start=start,
        end=end,
        loader=loader_id,
    )
    if result is None:
        return None
    if isinstance(result, pd.DataFrame):
        return result
    # Some loaders return dict[str, DataFrame]; unwrap first entry
    if isinstance(result, dict):
        if symbol in result and isinstance(result[symbol], pd.DataFrame):
            return result[symbol]
        for value in result.values():
            if isinstance(value, pd.DataFrame):
                return value
    return None


def _dataframe_to_kline_events(
    df: pd.DataFrame,
    *,
    exchange: str,
    market_type: MarketType,
    symbol: str,
    interval: str,
) -> list[KlineEvent]:
    """Convert a vibe OHLCV DataFrame to a list of KlineEvent."""
    if df is None or df.empty:
        return []

    ivl_ms = _interval_to_ms(interval)
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    out: list[KlineEvent] = []

    # Vibe DataFrames use DatetimeIndex (named 'trade_date' for daily).
    # Reset index to a column so we can iterate uniformly.
    if isinstance(df.index, pd.DatetimeIndex):
        df_reset = df.reset_index()
    else:
        df_reset = df.copy()
        # Heuristic: find a date-like column
        date_col = None
        for candidate in ("trade_date", "date", "datetime", "timestamp"):
            if candidate in df_reset.columns:
                date_col = candidate
                break
        if date_col is None and df_reset.columns.size > 0:
            # Fallback: assume first column holds the date
            date_col = str(df_reset.columns[0])
        if date_col is not None:
            df_reset = df_reset.rename(columns={date_col: "_vibe_dt"})

    # Find the date column name in the reset frame
    dt_col: str | None = None
    for candidate in ("_vibe_dt", "trade_date", "date", "datetime", "timestamp", "index"):
        if candidate in df_reset.columns:
            dt_col = candidate
            break
    if dt_col is None:
        # DatetimeIndex reset produced a column named 'index'
        if "index" in df_reset.columns:
            dt_col = "index"

    if dt_col is None:
        return []

    # Normalize column names to lower-case
    cols = {str(c).lower(): c for c in df_reset.columns}
    o_col = cols.get("open")
    h_col = cols.get("high")
    l_col = cols.get("low")
    c_col = cols.get("close")
    v_col = cols.get("volume")

    if not all([o_col, h_col, l_col, c_col]):
        return []

    for _, row in df_reset.iterrows():
        try:
            dt_val = row[dt_col]
            ts = _to_ms_epoch(dt_val)
            if ts is None:
                continue
            open_v = float(row[o_col])
            high_v = float(row[h_col])
            low_v = float(row[l_col])
            close_v = float(row[c_col])
            vol = float(row[v_col]) if v_col is not None else 0.0
            candle_end = ts + ivl_ms
            is_closed = now_ms >= candle_end
            out.append(
                KlineEvent(
                    exchange=exchange,
                    market_type=market_type,
                    symbol=symbol,
                    timestamp=ts,
                    interval=interval,
                    open=open_v,
                    high=high_v,
                    low=low_v,
                    close=close_v,
                    volume=vol,
                    closed=is_closed,
                )
            )
        except (ValueError, TypeError, KeyError):
            continue
    return out


def _to_ms_epoch(value: Any) -> int | None:
    """Coerce a date/datetime/str/number to millisecond epoch."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # If it's already in ms, use as-is; if in seconds, multiply.
        v = int(value)
        return v if v > 10**12 else v * 1000
    if isinstance(value, (pd.Timestamp, datetime)):
        ts = pd.Timestamp(value).to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int(ts.timestamp() * 1000)
    if isinstance(value, str):
        try:
            parsed = pd.to_datetime(value, utc=True)
            return int(parsed.timestamp() * 1000)
        except Exception:
            return None
    return None
