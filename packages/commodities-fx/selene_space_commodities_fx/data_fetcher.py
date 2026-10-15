"""AKShare-based commodities & FX research fetcher.

CN futures K-line (minute + daily) and FX spot quotes. Research-terminal
semantics only — quote/kline data, no trading signals (与红线5 同类决策记录).

Requires [cn] optional dependency: akshare, requests (manual install).
"""

from __future__ import annotations

import time
from datetime import datetime

import pandas as pd

_MINUTE_PERIODS = {"1", "5", "15", "30", "60"}

_DAILY_COL_MAP = {
    "日期": "timestamp",
    "开盘价": "open",
    "最高价": "high",
    "最低价": "low",
    "收盘价": "close",
    "成交量": "volume",
    "持仓量": "hold",
}

_FX_COL_MAP = {
    "货币对": "pair",
    "买报价": "bid",
    "卖报价": "ask",
}


def _fetch_with_retry(func, retries: int = 3, delay: float = 1.0, **kwargs):
    """Fetch with retry + proxy-disabled session. Returns None on failure."""
    import requests

    original_init = requests.Session.__init__

    def _no_proxy_init(self, *args, **kw):
        original_init(self, *args, **kw)
        self.trust_env = False

    requests.Session.__init__ = _no_proxy_init
    try:
        for i in range(retries):
            try:
                return func(**kwargs)
            except Exception:
                if i == retries - 1:
                    return None
                time.sleep(delay * (i + 1))
        return None
    finally:
        requests.Session.__init__ = original_init


def _filter_fmt(v: str) -> str:
    """Normalize a YYYYMMDD range to 'YYYY-MM-DD' to match the timestamp column.

    The timestamp column is always 'YYYY-MM-DD HH:MM:SS'; a lexicographic
    comparison against a raw YYYYMMDD filter would always be False and drop
    every row. YYYYMMDD is still kept for the akshare daily call above.
    """
    s = v.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def get_futures_kline(symbol: str, period: str = "1", start: str = "", end: str = "") -> pd.DataFrame:
    """CN futures K-line. period in 1/5/15/30/60 -> minute; otherwise daily.

    Minute source: ``ak.futures_zh_minute_sina`` -> datetime/open/high/low/close/volume/hold.
    Daily source: ``ak.futures_main_sina`` -> Chinese cols mapped to english.
    """
    import akshare as ak

    if period in _MINUTE_PERIODS:
        df = _fetch_with_retry(ak.futures_zh_minute_sina, symbol=symbol, period=period)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={"datetime": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        if not start:
            start = "20200101"
        if not end:
            end = datetime.now().strftime("%Y%m%d")
        df = _fetch_with_retry(ak.futures_main_sina, symbol=symbol, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns=_DAILY_COL_MAP)
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")

    start_f = _filter_fmt(start)[:10] if start else ""
    end_f = _filter_fmt(end)[:10] if end else ""
    # Compare on the date prefix so a date-only bound includes the whole end day
    # ("2024-01-02 00:00:00"[:10] == "2024-01-02" -> <= is True).
    if start_f:
        df = df[df["timestamp"].str[:10] >= start_f]
    if end_f:
        df = df[df["timestamp"].str[:10] <= end_f]
    df["symbol"] = symbol
    keep = [c for c in ("timestamp", "open", "high", "low", "close", "volume", "hold", "symbol") if c in df.columns]
    return df[keep]


def get_fx_quotes() -> pd.DataFrame:
    """FX spot quotes: ``ak.fx_pair_quote`` (international) + ``ak.fx_spot_quote`` (incl USD/CNY)."""
    import akshare as ak

    frames = []
    for fn in (ak.fx_pair_quote, ak.fx_spot_quote):
        df = _fetch_with_retry(fn)
        if df is None or df.empty:
            continue
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns=_FX_COL_MAP)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    keep = [c for c in ("pair", "bid", "ask") if c in out.columns]
    return out[keep]
