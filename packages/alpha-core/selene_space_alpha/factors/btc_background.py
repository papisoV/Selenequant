"""BTC relative-strength background series (QD-absorption Phase 2, B2).

Thought-level absorption of the "macro background feature family" idea from
the user's AlphaMaster_QD survey (internal-plan) — cleanroom: design intent only, a
cross-asset context sensor for altcoin research. The MT5-domain original used
DXY/OIL as background; the crypto-domain domain-analogue here is BTC relative
strength — computed purely from local klines.db prices, no external market-cap
feed, so the implementation is necessarily independent (cleanroom).

Why BTC relative strength as dominance proxy: true BTC dominance requires
market-cap data (none locally). BTC/alt-basket relative strength is the
observable price-only analogue — when BTC outperforms the alt basket, capital
is concentrating in BTC (risk-off within crypto); when the basket outperforms,
alt-season-like conditions. This is the MARKETS-page "sensor reading" the
consensus plan maps B2 to (吸收映射表 row B2).

NaN semantics (locked by test): the background series only exists where BOTH
the BTC series and the basket aggregate exist. Missing basket members shrink
the basket (weighted by availability), never fabricate values. A bar with no
usable basket data is NaN, not 0.

Module shape follows the tg_event_overlay_source.py precedent: a loader that
reads its own source (klines.db), aligns to an incoming kline DataFrame's
time axis (as-of join), and exposes plain functions usable as factor
compute functions.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from selene_space_core.kline_paths import default_klines_db_path

#: Default klines.db location (worktree root). Overridable for tests and for
#: callers that keep the DB elsewhere; the B4 registry pins this source.
DEFAULT_KLINES_DB_PATH = default_klines_db_path()

#: BTC is the numeraire asset.
BTC_SYMBOL = "BTC/USDT"

#: Default alt basket — liquid OKX perp/universe members present in klines.db
#: (19 symbols, exchange=okx). Kept explicit rather than "all symbols" so a
#: future symbol addition cannot silently change historical background values.
DEFAULT_BASKET: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "LINK/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "DOT/USDT",
    "AVAX/USDT",
    "ATOM/USDT",
    "NEAR/USDT",
    "UNI/USDT",
    "HBAR/USDT",
)

#: Lookback for the relative-strength momentum ratio.
BG_MOM_WINDOW = 20

#: EMA period smoothing the raw relative-strength ratio.
BG_EMA_SPAN = 10

#: Process-level memo for assembled background series. The T4 validation chain
#: reaches this once per BTC_BG_* feature per candidate — 6-12 rebuilds per run,
#: each ~50s against the 2.3GB klines.db. The series is a pure function of the
#: db file's contents, so an (path, mtime, size) revision key is sound: a db
#: rebuilt with new bars misses and rebuilds. Bounded FIFO — per-case tmp dbs
#: in tests would otherwise grow it without limit.
_BG_CACHE_MAX = 8
_bg_cache: dict[tuple[Any, ...], pd.Series] = {}


def load_close_matrix(
    db_path: str | Path = DEFAULT_KLINES_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_BASKET,
    interval: str = "D1",
    exchange: str = "okx",
) -> pd.DataFrame:
    """Load close prices for all basket symbols on one aligned time axis.

    Returns a DataFrame indexed by UTC datetime with one column per symbol;
    symbols with no data in the DB are omitted (never fabricated). Symbols
    with partial coverage keep NaN for their missing bars.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"klines.db not found: {db_path}")

    frames: dict[str, pd.Series] = {}
    conn = sqlite3.connect(str(db_path))
    try:
        for sym in symbols:
            df = pd.read_sql_query(
                "SELECT timestamp, close FROM klines "
                "WHERE symbol = ? AND interval = ? AND exchange = ? "
                "ORDER BY timestamp",
                conn,
                params=(sym, interval, exchange),
            )
            if df.empty:
                continue
            # timestamp column is milliseconds (UTC) — see US-103 verification.
            idx = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            s = pd.Series(
                pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float),
                index=idx,
            )
            s = s[~s.index.duplicated(keep="last")]
            frames[sym] = s
    finally:
        conn.close()

    if not frames:
        raise ValueError(f"No kline data for any of {symbols} in {db_path}")

    return pd.DataFrame(frames).sort_index()


def btc_relative_strength(
    close_matrix: pd.DataFrame,
    mom_window: int = BG_MOM_WINDOW,
    ema_span: int = BG_EMA_SPAN,
) -> pd.Series:
    """BTC vs alt-basket relative-strength background series.

    Definition (price-only dominance proxy):
      1. per-symbol log return over ``mom_window`` bars
      2. basket = equal-weight mean of member returns (NaN members skipped)
      3. rs = btc_mom - basket_mom  (positive = BTC outperforming)
      4. rs_smooth = EMA(rs, ema_span)

    NaN semantics: a bar is NaN when BTC momentum is NaN there, or when the
    basket has no valid members at that bar. Partial basket coverage is fine —
    the mean skips NaN members.
    """
    if BTC_SYMBOL not in close_matrix.columns:
        raise KeyError(f"{BTC_SYMBOL} missing from close matrix")

    btc = close_matrix[BTC_SYMBOL]
    btc_mom = np.log(btc / btc.shift(mom_window))

    alts = [c for c in close_matrix.columns if c != BTC_SYMBOL]
    if not alts:
        # Degenerate basket: RS is undefined — all NaN, never zeros.
        return pd.Series(np.nan, index=close_matrix.index, name="btc_rs")

    alt_mom = np.log(close_matrix[alts] / close_matrix[alts].shift(mom_window))
    basket_mom = alt_mom.mean(axis=1, skipna=True)
    # Bars where every alt is NaN must stay NaN (mean(skipna) returns 0.0 for
    # all-NaN rows under some pandas versions) — guard explicitly.
    basket_mom[alt_mom.isna().all(axis=1)] = np.nan

    rs = btc_mom - basket_mom
    rs_smooth = rs.ewm(span=ema_span, adjust=False, min_periods=ema_span).mean()
    # pandas ewm skips NaN and carries the last valid EMA forward — that would
    # fabricate background values on bars where the background does not exist.
    # Re-apply the raw NaN mask so missing-background bars stay NaN.
    rs_smooth[rs.isna()] = np.nan
    rs_smooth.name = "btc_rs"
    return rs_smooth


def build_background(
    db_path: str | Path = DEFAULT_KLINES_DB_PATH,
    symbols: tuple[str, ...] = DEFAULT_BASKET,
    interval: str = "D1",
    exchange: str = "okx",
    mom_window: int = BG_MOM_WINDOW,
    ema_span: int = BG_EMA_SPAN,
) -> pd.Series:
    """One-call background builder: klines.db -> BTC relative strength series."""
    db_path = Path(db_path)
    # Cache only real files (tests often pass nonexistent paths for the
    # FileNotFoundError branch); revision key = contents fingerprint.
    key: tuple[Any, ...] | None = None
    if db_path.exists():
        try:
            st = db_path.stat()
            key = (
                str(db_path.resolve()),
                st.st_mtime_ns,
                st.st_size,
                tuple(symbols),
                interval,
                exchange,
                mom_window,
                ema_span,
            )
        except OSError:
            key = None
        if key is not None and key in _bg_cache:
            return _bg_cache[key]

    matrix = load_close_matrix(
        db_path=db_path, symbols=symbols, interval=interval, exchange=exchange
    )
    result = btc_relative_strength(matrix, mom_window=mom_window, ema_span=ema_span)

    if key is not None:
        if len(_bg_cache) >= _BG_CACHE_MAX:
            _bg_cache.pop(next(iter(_bg_cache)))
        _bg_cache[key] = result
    return result
