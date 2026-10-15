"""Unified derivatives data loader for backtesting.

Merges klines.db + derivatives.db + orderflow.db into a single DataFrame
with all columns needed for factor computation. Replaces the duplicated
merge logic in shared_backtest.py and optuna scripts.

Adapted from selenespace-research for master's DB schema (DerivativesDB/OrderflowDB
with exchange + interval columns).
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
KLINES_DB = PROJECT_ROOT / "data" / "klines.db"
DERIVATIVES_DB = PROJECT_ROOT / "data" / "derivatives.db"
ORDERFLOW_DB = PROJECT_ROOT / "data" / "orderflow.db"


def _coverage_pct(series: pd.Series, total: int) -> float:
    """Percentage of non-NaN, non-zero values."""
    if total == 0:
        return 0.0
    valid = series.notna() & (series != 0)
    return float(valid.sum()) / total


def load_4h_with_derivatives(
    symbol: str = "BTC/USDT",
    *,
    klines_db: str | Path | None = None,
    derivatives_db: str | Path | None = None,
    orderflow_db: str | Path | None = None,
    base_interval: str = "4h",
    aux_interval: str = "1d",
    exchange: str = "binance",
    market_type: str = "futures",
) -> tuple[pd.DataFrame, dict]:
    """Load 4h klines + 1d auxiliary + derivatives + orderflow for a symbol.

    Returns
    -------
    df : pd.DataFrame
        Merged DataFrame with all columns.
    coverage : dict
        Per-column coverage stats: {col: {"pct": float, "start": str, "end": str}}.
    """
    klines_path = Path(klines_db) if klines_db else KLINES_DB
    deriv_path = Path(derivatives_db) if derivatives_db else DERIVATIVES_DB
    of_path = Path(orderflow_db) if orderflow_db else ORDERFLOW_DB

    coverage: dict[str, dict] = {}

    # --- 1. Load 4h klines ---
    conn = sqlite3.connect(str(klines_path))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? AND market_type=? "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, base_interval, market_type),
    )
    conn.close()

    if df.empty:
        raise ValueError(f"No {base_interval} klines for {symbol}")

    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # --- 2. Load 1d auxiliary klines ---
    conn = sqlite3.connect(str(klines_path))
    df1d = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? AND market_type=? "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, aux_interval, market_type),
    )
    conn.close()

    if not df1d.empty:
        for c in ["open", "high", "low", "close", "volume"]:
            df1d[c] = pd.to_numeric(df1d[c], errors="coerce")
        df1d["day_ts"] = (df1d["timestamp"] // 86400000) * 86400000
        df["day_ts"] = (df["timestamp"] // 86400000) * 86400000
        df1d_r = df1d[["day_ts", "open", "high", "low", "close", "volume"]].rename(
            columns={"open": "TF1D_open", "high": "TF1D_high", "low": "TF1D_low",
                     "close": "TF1D_close", "volume": "TF1D_volume"})
        df = df.merge(df1d_r, on="day_ts", how="left")
        df[["TF1D_open", "TF1D_high", "TF1D_low", "TF1D_close", "TF1D_volume"]] = (
            df[["TF1D_open", "TF1D_high", "TF1D_low", "TF1D_close", "TF1D_volume"]]
            .ffill().fillna(0.0))
        df = df.drop(columns=["day_ts"])

    # --- 3. Merge derivatives (funding rate, taker ratio, OI) ---
    if deriv_path.exists():
        dconn = sqlite3.connect(str(deriv_path))

        # Funding rate (timestamps may not align with 4h klines — use merge_asof)
        fund = pd.read_sql_query(
            "SELECT timestamp, funding_rate FROM funding_rates "
            "WHERE exchange=? AND symbol=? ORDER BY timestamp",
            dconn, params=(exchange, symbol))
        if not fund.empty:
            fund["funding_rate"] = pd.to_numeric(fund["funding_rate"], errors="coerce")
            fund = fund.rename(columns={"funding_rate": "DERIV_FUNDING_RATE"})
            fund["timestamp"] = pd.to_numeric(fund["timestamp"])
            df = df.sort_values("timestamp")
            fund = fund.sort_values("timestamp")
            df = pd.merge_asof(df, fund, on="timestamp", direction="backward")
            df["DERIV_FUNDING_RATE"] = df["DERIV_FUNDING_RATE"].ffill().fillna(0.0)
            n = len(df)
            coverage["DERIV_FUNDING_RATE"] = {
                "pct": _coverage_pct(df["DERIV_FUNDING_RATE"], n),
                "start": datetime.fromtimestamp(fund["timestamp"].iloc[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                "end": datetime.fromtimestamp(fund["timestamp"].iloc[-1]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
            }

        # Taker ratio (from derivatives.db — master schema has exchange + interval)
        # Timestamps may not align with 4h klines — use merge_asof
        for interval in ["4h", "1d", "1h"]:
            try:
                taker = pd.read_sql_query(
                    "SELECT timestamp, buy_vol, sell_vol, buy_sell_ratio FROM taker_ratio "
                    "WHERE exchange=? AND symbol=? AND interval=? ORDER BY timestamp",
                    dconn, params=(exchange, symbol, interval))
                if not taker.empty:
                    taker["timestamp"] = pd.to_numeric(taker["timestamp"])
                    taker["buy_vol"] = pd.to_numeric(taker["buy_vol"], errors="coerce")
                    taker["sell_vol"] = pd.to_numeric(taker["sell_vol"], errors="coerce")
                    taker["buy_sell_ratio"] = pd.to_numeric(taker["buy_sell_ratio"], errors="coerce")
                    taker["OF_TAKER_RATIO"] = taker["buy_sell_ratio"]
                    taker["OF_buy_vol"] = taker["buy_vol"]
                    taker["OF_sell_vol"] = taker["sell_vol"]
                    taker = taker[["timestamp", "OF_TAKER_RATIO", "OF_buy_vol", "OF_sell_vol"]].sort_values("timestamp")
                    df = df.sort_values("timestamp")
                    df = pd.merge_asof(df, taker, on="timestamp", direction="backward")
                    df["OF_TAKER_RATIO"] = df["OF_TAKER_RATIO"].ffill().fillna(1.0)
                    df["OF_buy_vol"] = df["OF_buy_vol"].ffill().fillna(0.0)
                    df["OF_sell_vol"] = df["OF_sell_vol"].ffill().fillna(0.0)
                    break
            except Exception:
                continue

        # Open interest (prefer 1d for coverage, then 1h for detail)
        # Timestamps may not align — use merge_asof
        for interval in ["1d", "1h", "4h"]:
            oi = pd.read_sql_query(
                "SELECT timestamp, open_interest FROM open_interest "
                "WHERE exchange=? AND symbol=? AND interval=? ORDER BY timestamp",
                dconn, params=(exchange, symbol, interval))
            if not oi.empty:
                oi["timestamp"] = pd.to_numeric(oi["timestamp"])
                oi["open_interest"] = pd.to_numeric(oi["open_interest"], errors="coerce")
                oi = oi.rename(columns={"open_interest": "DERIV_OPEN_INTEREST"})
                oi = oi.sort_values("timestamp")
                df = df.sort_values("timestamp")
                df = pd.merge_asof(df, oi, on="timestamp", direction="backward")
                df["DERIV_OPEN_INTEREST"] = df["DERIV_OPEN_INTEREST"].ffill().fillna(0.0)
                coverage["DERIV_OPEN_INTEREST"] = {
                    "pct": _coverage_pct(df["DERIV_OPEN_INTEREST"], len(df)),
                    "start": datetime.fromtimestamp(oi["timestamp"].iloc[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "end": datetime.fromtimestamp(oi["timestamp"].iloc[-1]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "interval": interval,
                }
                break

        dconn.close()
    else:
        logger.warning("derivatives.db not found at %s", deriv_path)

    # --- 4. Merge orderflow (top trader ratios, global LS, basis) ---
    # Master OrderflowDB uses exchange + interval columns; symbol is in BTC/USDT format
    if of_path.exists():
        ofconn = sqlite3.connect(str(of_path))
        df = df.sort_values("timestamp").reset_index(drop=True)
        df["_of_ts"] = df["timestamp"]

        # Top position ratio
        for interval in ["4h", "1d", "1h"]:
            try:
                top_pos = pd.read_sql_query(
                    "SELECT timestamp, long_short_ratio FROM top_position_ratio "
                    "WHERE exchange=? AND symbol=? AND interval=? ORDER BY timestamp ASC",
                    ofconn, params=(exchange, symbol, interval))
                if not top_pos.empty:
                    top_pos["timestamp"] = pd.to_numeric(top_pos["timestamp"])
                    top_pos["_of_ts"] = top_pos["timestamp"]
                    top_pos = top_pos.sort_values("_of_ts")
                    df = pd.merge_asof(
                        df, top_pos[["_of_ts", "long_short_ratio"]].rename(
                            columns={"long_short_ratio": "OF_top_pos_ratio"}),
                        on="_of_ts", direction="backward")
                    df["OF_top_pos_ratio"] = df["OF_top_pos_ratio"].ffill().fillna(1.0)
                    break
            except Exception:
                continue
        else:
            df["OF_top_pos_ratio"] = 1.0

        # Top account ratio
        for interval in ["4h", "1d", "1h"]:
            try:
                top_acc = pd.read_sql_query(
                    "SELECT timestamp, long_short_ratio FROM top_account_ratio "
                    "WHERE exchange=? AND symbol=? AND interval=? ORDER BY timestamp ASC",
                    ofconn, params=(exchange, symbol, interval))
                if not top_acc.empty:
                    top_acc["timestamp"] = pd.to_numeric(top_acc["timestamp"])
                    top_acc["_of_ts"] = top_acc["timestamp"]
                    top_acc = top_acc.sort_values("_of_ts")
                    df = pd.merge_asof(
                        df, top_acc[["_of_ts", "long_short_ratio"]].rename(
                            columns={"long_short_ratio": "OF_top_acc_ratio"}),
                        on="_of_ts", direction="backward")
                    df["OF_top_acc_ratio"] = df["OF_top_acc_ratio"].ffill().fillna(1.0)
                    break
            except Exception:
                continue
        else:
            df["OF_top_acc_ratio"] = 1.0

        # Global LS ratio
        for interval in ["4h", "1d", "1h"]:
            try:
                gls = pd.read_sql_query(
                    "SELECT timestamp, long_short_ratio FROM global_ls_ratio "
                    "WHERE exchange=? AND symbol=? AND interval=? ORDER BY timestamp ASC",
                    ofconn, params=(exchange, symbol, interval))
                if not gls.empty:
                    gls["timestamp"] = pd.to_numeric(gls["timestamp"])
                    gls["_of_ts"] = gls["timestamp"]
                    gls = gls.sort_values("_of_ts")
                    df = pd.merge_asof(
                        df, gls[["_of_ts", "long_short_ratio"]].rename(
                            columns={"long_short_ratio": "OF_global_ls_ratio"}),
                        on="_of_ts", direction="backward")
                    df["OF_global_ls_ratio"] = df["OF_global_ls_ratio"].ffill().fillna(1.0)
                    break
            except Exception:
                continue
        else:
            df["OF_global_ls_ratio"] = 1.0

        # Basis (may not exist in older DBs)
        try:
            basis = pd.read_sql_query(
                "SELECT timestamp, basis_rate, basis, index_price, futures_price FROM basis "
                "WHERE exchange=? AND symbol=? AND interval=? ORDER BY timestamp ASC",
                ofconn, params=(exchange, symbol, base_interval))
            if not basis.empty:
                basis["timestamp"] = pd.to_numeric(basis["timestamp"])
                basis["_of_ts"] = basis["timestamp"]
                basis = basis.sort_values("_of_ts")
                basis_cols = basis[["_of_ts", "basis_rate"]].rename(
                    columns={"basis_rate": "DERIV_basis_rate"})
                df = pd.merge_asof(df, basis_cols, on="_of_ts", direction="backward")
                df["DERIV_basis_rate"] = df["DERIV_basis_rate"].ffill().fillna(0.0)
                coverage["DERIV_BASIS"] = {
                    "pct": _coverage_pct(df["DERIV_basis_rate"], len(df)),
                    "start": datetime.fromtimestamp(basis["timestamp"].iloc[0]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "end": datetime.fromtimestamp(basis["timestamp"].iloc[-1]/1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                }
        except Exception:
            df["DERIV_basis_rate"] = 0.0

        ofconn.close()
        df = df.drop(columns=["_of_ts"])
    else:
        df["OF_top_pos_ratio"] = 1.0
        df["OF_top_acc_ratio"] = 1.0
        df["OF_global_ls_ratio"] = 1.0
        df["DERIV_basis_rate"] = 0.0
        logger.warning("orderflow.db not found at %s", of_path)

    # --- 5. Log coverage warnings ---
    for col, info in coverage.items():
        if info["pct"] < 0.5:
            logger.warning(
                "Low coverage for %s: %.0f%% (%s ~ %s)",
                col, info["pct"] * 100, info.get("start", "?"), info.get("end", "?"),
            )

    return df.reset_index(drop=True), coverage
