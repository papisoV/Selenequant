"""DataFrame <-> KlineEvent conversion adapters.

Bridges the stock package's pandas-based DataFrames with the
``selene_space_types.market.KlineEvent`` model used by the rest of the
SeleneQuant platform.
"""

from __future__ import annotations

import pandas as pd

from selene_space_types.market import KlineEvent

from selene_space_stock.indicators import _normalize_columns


def df_to_kline_events(
    df: pd.DataFrame,
    symbol: str,
    exchange: str,
    interval: str,
    *,
    market_type: str = "spot",
) -> list[KlineEvent]:
    """Convert a stock OHLCV DataFrame to a list of ``KlineEvent``.

    Accepts both Title Case and lowercase column names (normalized
    internally).  The ``timestamp`` field is set to the millisecond epoch
    of each row's date.
    """
    normalized = _normalize_columns(df)
    events: list[KlineEvent] = []
    for _, row in normalized.iterrows():
        date_val = pd.to_datetime(row.get("date", row.get("Date")))
        if pd.isna(date_val):
            continue
        ts_ms = int(date_val.timestamp() * 1000)
        events.append(
            KlineEvent(
                exchange=exchange,
                market_type=market_type,  # type: ignore[arg-type]
                symbol=symbol,
                timestamp=ts_ms,
                interval=interval,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0)),
                closed=True,
            )
        )
    return events


def kline_events_to_df(events: list[KlineEvent]) -> pd.DataFrame:
    """Convert a list of ``KlineEvent`` back to a stock-style DataFrame.

    The resulting DataFrame uses lowercase column names (``date``, ``open``,
    ``high``, ``low``, ``close``, ``volume``) consistent with the
    ``indicators`` module's internal convention.
    """
    if not events:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    rows: list[dict[str, object]] = []
    for event in events:
        # Convert millisecond timestamp to date string
        date_str = pd.to_datetime(event.timestamp, unit="ms").strftime("%Y-%m-%d")
        rows.append(
            {
                "date": date_str,
                "open": event.open,
                "high": event.high,
                "low": event.low,
                "close": event.close,
                "volume": event.volume,
            }
        )
    return pd.DataFrame(rows)
