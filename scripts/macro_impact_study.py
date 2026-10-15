"""Macro event impact study on crypto prices and strategy performance.

Analyzes how FOMC/CPI/NFP events affect BTC volatility, returns, and V5.2
strategy performance. Also examines VIX regime and DXY trend effects.

Usage:
    python scripts/macro_impact_study.py
    python scripts/macro_impact_study.py --symbol BTC/USDT --interval 4h
    python scripts/macro_impact_study.py --interval 1h --no-strategy
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))

from selene_space_alpha.macro.db import MacroEventDB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("macro_study")

DEFAULT_KLINES_DB = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
DEFAULT_MACRO_DB = PROJECT_ROOT / "data" / "macro_events.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "macro_impact_report.json"

# Event window definitions (hours relative to event time)
EVENT_WINDOWS: dict[str, tuple[float, float]] = {
    "pre_-48h": (-48, -24),
    "pre_-24h": (-24, -2),
    "pre_-2h": (-2, 0),
    "event": (0, 2),
    "post_+2h": (2, 24),
    "post_+24h": (24, 48),
}

VIX_HIGH = 25.0
VIX_LOW = 15.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_klines(
    db_path: str | Path,
    symbol: str = "BTC/USDT",
    interval: str = "4h",
    exchange: str = "binance",
    market_type: str = "futures",
) -> pd.DataFrame:
    """Load klines from SQLite."""
    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? AND market_type=? "
        "ORDER BY timestamp ASC",
        conn,
        params=(symbol, exchange, interval, market_type),
    )
    conn.close()
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    logger.info("Loaded %d klines (%s %s)", len(df), symbol, interval)
    return df


def load_macro_events(macro_db: MacroEventDB, event_types: list[str] | None = None) -> pd.DataFrame:
    """Load macro events from macro_events.db."""
    if event_types:
        dfs = []
        for et in event_types:
            dfs.append(macro_db.get_events(event_type=et))
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    else:
        df = macro_db.get_events()
    if df.empty:
        logger.warning("No macro events loaded")
    else:
        logger.info("Loaded %d macro events", len(df))
    return df


def load_vix_daily(macro_db: MacroEventDB) -> pd.DataFrame:
    """Load VIX daily data."""
    df = macro_db.get_vix_daily()
    if df.empty:
        logger.warning("No VIX data loaded")
    else:
        logger.info("Loaded %d VIX daily rows", len(df))
    return df


def load_dxy_daily(macro_db: MacroEventDB) -> pd.DataFrame:
    """Load DXY daily data."""
    df = macro_db.get_dxy_daily()
    if df.empty:
        logger.warning("No DXY data loaded")
    else:
        logger.info("Loaded %d DXY daily rows", len(df))
    return df


# ---------------------------------------------------------------------------
# Event window tagging
# ---------------------------------------------------------------------------


def tag_klines_with_event_distance(
    klines_df: pd.DataFrame,
    events_df: pd.DataFrame,
) -> pd.DataFrame:
    """Tag each kline with the nearest macro event distance.

    Adds columns:
      - nearest_event_type: str
      - nearest_event_date: str
      - hours_to_event: float
      - event_window: str (one of EVENT_WINDOWS keys, or "normal")
    """
    if events_df.empty or klines_df.empty:
        klines_df["nearest_event_type"] = ""
        klines_df["nearest_event_date"] = ""
        klines_df["hours_to_event"] = np.nan
        klines_df["event_window"] = "normal"
        return klines_df

    df = klines_df.copy()
    event_ts = events_df["timestamp_utc"].values.astype(np.int64)
    event_types = events_df["event_type"].values
    event_dates = events_df["date"].values
    kline_ts = df["timestamp"].values.astype(np.int64)

    # For each kline, find nearest event
    nearest_idx = np.searchsorted(event_ts, kline_ts, side="left")

    # Compare distance to event at nearest_idx and nearest_idx-1
    hours_to_nearest = np.full(len(kline_ts), np.nan)
    nearest_type = np.full(len(kline_ts), "", dtype=object)
    nearest_date = np.full(len(kline_ts), "", dtype=object)

    for i in range(len(kline_ts)):
        candidates = []
        if nearest_idx[i] < len(event_ts):
            dist = (kline_ts[i] - event_ts[nearest_idx[i]]) / 3_600_000.0
            candidates.append((abs(dist), dist, nearest_idx[i]))
        if nearest_idx[i] > 0:
            dist = (kline_ts[i] - event_ts[nearest_idx[i] - 1]) / 3_600_000.0
            candidates.append((abs(dist), dist, nearest_idx[i] - 1))

        if candidates:
            candidates.sort(key=lambda x: x[0])
            _, signed_dist, idx = candidates[0]
            hours_to_nearest[i] = signed_dist
            nearest_type[i] = event_types[idx]
            nearest_date[i] = event_dates[idx]

    df["hours_to_event"] = hours_to_nearest
    df["nearest_event_type"] = nearest_type
    df["nearest_event_date"] = nearest_date

    # Assign window label
    window_labels = np.full(len(df), "normal", dtype=object)
    for i, h in enumerate(hours_to_nearest):
        if np.isnan(h):
            continue
        # Only tag bars within 48h of an event
        for wname, (lo, hi) in EVENT_WINDOWS.items():
            if lo <= h < hi:
                window_labels[i] = wname
                break
        # Also tag bars very close to event (< 48h but not in a named window)
        if abs(h) < 48 and window_labels[i] == "normal":
            if h >= 0:
                window_labels[i] = "post_+24h"
            else:
                window_labels[i] = "pre_-48h"

    df["event_window"] = window_labels
    return df


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def compute_realized_vol(series: pd.Series, window: int = 3) -> pd.Series:
    """Compute rolling realized volatility (std of log returns)."""
    log_ret = np.log(series / series.shift(1)).dropna()
    return log_ret.rolling(window).std()


def _stats_dict(values: np.ndarray) -> dict:
    """Compute summary stats for an array."""
    if len(values) == 0:
        return {"mean": 0, "median": 0, "std": 0, "n": 0}
    return {
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "std": float(np.std(values)),
        "n": int(len(values)),
    }


def analyze_volatility_around_events(klines_tagged: pd.DataFrame) -> dict:
    """Compare realized vol in event windows vs normal windows."""
    df = klines_tagged.copy()
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
    df["realized_vol"] = df["log_ret"].rolling(3).std()

    normal_vols = df.loc[df["event_window"] == "normal", "realized_vol"].dropna().values
    normal_stats = _stats_dict(normal_vols)

    window_stats: dict[str, dict] = {}
    vol_ratios: dict[str, float] = {}

    for wname in EVENT_WINDOWS:
        wvols = df.loc[df["event_window"] == wname, "realized_vol"].dropna().values
        window_stats[wname] = _stats_dict(wvols)
        if normal_stats["mean"] > 0 and len(wvols) > 0:
            vol_ratios[wname] = float(np.mean(wvols) / normal_stats["mean"])
        else:
            vol_ratios[wname] = 0.0

    # Per event type
    per_type: dict[str, dict] = {}
    for etype in df["nearest_event_type"].unique():
        if not etype:
            continue
        etype_df = df[df["nearest_event_type"] == etype]
        type_window_stats: dict[str, dict] = {}
        for wname in EVENT_WINDOWS:
            wvols = etype_df.loc[etype_df["event_window"] == wname, "realized_vol"].dropna().values
            s = _stats_dict(wvols)
            if normal_stats["mean"] > 0 and len(wvols) > 0:
                s["vol_ratio"] = float(np.mean(wvols) / normal_stats["mean"])
            type_window_stats[wname] = s
        per_type[etype] = type_window_stats

    return {
        "event_window_stats": window_stats,
        "normal_stats": normal_stats,
        "vol_ratio": vol_ratios,
        "per_event_type": per_type,
    }


def analyze_returns_around_events(klines_tagged: pd.DataFrame) -> dict:
    """Compare return distribution in event windows vs normal."""
    df = klines_tagged.copy()
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))

    normal_rets = df.loc[df["event_window"] == "normal", "log_ret"].dropna().values
    normal_stats = _stats_dict(normal_rets)
    if len(normal_rets) > 3:
        from scipy.stats import kurtosis, skew
        normal_stats["skew"] = float(skew(normal_rets))
        normal_stats["kurtosis"] = float(kurtosis(normal_rets))

    window_stats: dict[str, dict] = {}
    for wname in EVENT_WINDOWS:
        rets = df.loc[df["event_window"] == wname, "log_ret"].dropna().values
        s = _stats_dict(rets)
        if len(rets) > 3:
            from scipy.stats import kurtosis, skew
            s["skew"] = float(skew(rets))
            s["kurtosis"] = float(kurtosis(rets))
        window_stats[wname] = s

    per_type: dict[str, dict] = {}
    for etype in df["nearest_event_type"].unique():
        if not etype:
            continue
        etype_df = df[df["nearest_event_type"] == etype]
        type_stats: dict[str, dict] = {}
        for wname in EVENT_WINDOWS:
            rets = etype_df.loc[etype_df["event_window"] == wname, "log_ret"].dropna().values
            type_stats[wname] = _stats_dict(rets)
        per_type[etype] = type_stats

    return {
        "event_window_stats": window_stats,
        "normal_stats": normal_stats,
        "per_event_type": per_type,
    }


def analyze_vix_regime_performance(
    klines_df: pd.DataFrame,
    vix_df: pd.DataFrame,
) -> dict:
    """Analyze strategy performance under VIX regimes."""
    if vix_df.empty or klines_df.empty:
        return {"error": "No VIX or kline data available"}

    df = klines_df.copy()
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))

    # Merge: for each kline, find most recent VIX close
    vix_ts = vix_df["timestamp"].values.astype(np.int64)
    vix_close = vix_df["close"].values.astype(np.float64)
    kline_ts = df["timestamp"].values.astype(np.int64)

    # Forward-fill VIX onto klines
    vix_for_kline = np.full(len(kline_ts), np.nan)
    for i, ts in enumerate(kline_ts):
        idx = np.searchsorted(vix_ts, ts, side="right") - 1
        if idx >= 0 and idx < len(vix_ts):
            # Only use VIX from the same day or previous day
            if ts - vix_ts[idx] < 48 * 3_600_000:
                vix_for_kline[i] = vix_close[idx]

    df["vix"] = vix_for_kline

    # Classify regimes
    regimes: dict[str, dict] = {}
    for label, lo, hi in [
        ("high_vix", VIX_HIGH, float("inf")),
        ("mid_vix", VIX_LOW, VIX_HIGH),
        ("low_vix", 0, VIX_LOW),
    ]:
        mask = (df["vix"] >= lo) & (df["vix"] < hi)
        rets = df.loc[mask, "log_ret"].dropna().values
        if len(rets) == 0:
            regimes[label] = {"mean_return": 0, "vol": 0, "sharpe_proxy": 0, "n_bars": 0}
            continue
        mean_r = float(np.mean(rets))
        vol = float(np.std(rets))
        sharpe = mean_r / vol if vol > 0 else 0
        # Annualize: mean * bars_per_year / vol
        regimes[label] = {
            "mean_return": mean_r,
            "vol": vol,
            "sharpe_proxy": sharpe,
            "n_bars": int(len(rets)),
            "vix_range": f"{lo:.0f}-{hi:.0f}" if hi != float("inf") else f">{lo:.0f}",
        }

    return regimes


def analyze_dxy_trend_performance(
    klines_df: pd.DataFrame,
    dxy_df: pd.DataFrame,
) -> dict:
    """Analyze BTC returns when DXY is rising vs falling."""
    if dxy_df.empty or klines_df.empty:
        return {"error": "No DXY or kline data available"}

    df = klines_df.copy()
    df["log_ret"] = np.log(df["close"] / df["close"].shift(1))

    # Merge DXY close onto klines
    dxy_ts = dxy_df["timestamp"].values.astype(np.int64)
    dxy_close = dxy_df["close"].values.astype(np.float64)
    kline_ts = df["timestamp"].values.astype(np.int64)

    dxy_for_kline = np.full(len(kline_ts), np.nan)
    for i, ts in enumerate(kline_ts):
        idx = np.searchsorted(dxy_ts, ts, side="right") - 1
        if idx >= 0 and idx < len(dxy_ts):
            if ts - dxy_ts[idx] < 48 * 3_600_000:
                dxy_for_kline[i] = dxy_close[idx]

    df["dxy"] = dxy_for_kline

    # Compute DXY 5-bar rolling return
    df["dxy_ret_5"] = df["dxy"].pct_change(5)

    # Classify
    rising = df.loc[df["dxy_ret_5"] > 0, "log_ret"].dropna().values
    falling = df.loc[df["dxy_ret_5"] < 0, "log_ret"].dropna().values

    # Rolling 20-bar correlation
    corr = 0.0
    if len(df.dropna(subset=["dxy_ret_5", "log_ret"])) > 20:
        corr = float(df["dxy_ret_5"].rolling(20).corr(df["log_ret"]).dropna().mean())

    return {
        "dxy_rising": {
            "mean_btc_return": float(np.mean(rising)) if len(rising) > 0 else 0,
            "n_bars": int(len(rising)),
            "dxy_change_mean": float(df.loc[df["dxy_ret_5"] > 0, "dxy_ret_5"].mean())
            if len(rising) > 0 else 0,
        },
        "dxy_falling": {
            "mean_btc_return": float(np.mean(falling)) if len(falling) > 0 else 0,
            "n_bars": int(len(falling)),
            "dxy_change_mean": float(df.loc[df["dxy_ret_5"] < 0, "dxy_ret_5"].mean())
            if len(falling) > 0 else 0,
        },
        "correlation": corr,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def generate_report(
    symbol: str,
    interval: str,
    vol_results: dict,
    return_results: dict,
    vix_results: dict,
    dxy_results: dict,
    strategy_results: dict | None,
) -> dict:
    """Assemble full report dict."""
    return {
        "symbol": symbol,
        "interval": interval,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "volatility_analysis": vol_results,
        "return_analysis": return_results,
        "vix_regime": vix_results,
        "dxy_trend": dxy_results,
        "strategy_analysis": strategy_results,
    }


def print_summary(report: dict) -> None:
    """Print human-readable summary."""
    print("\n" + "=" * 60)
    print(f"  Macro Impact Study: {report['symbol']} {report['interval']}")
    print(f"  Generated: {report['generated_at'][:19]}")
    print("=" * 60)

    # Volatility
    vol = report.get("volatility_analysis", {})
    print("\n--- Volatility (event window / normal) ---")
    normal_mean = vol.get("normal_stats", {}).get("mean", 0)
    if normal_mean > 0:
        for wname, ratio in vol.get("vol_ratio", {}).items():
            bar = "+" * int(ratio * 10) if ratio > 0 else ""
            print(f"  {wname:12s}: {ratio:.2f}x  {bar}")
    else:
        print("  No normal-window data available")

    # Per event type
    per_type = vol.get("per_event_type", {})
    if per_type:
        print("\n--- Volatility by event type (event window vol_ratio) ---")
        for etype, windows in per_type.items():
            event_ratio = windows.get("event", {}).get("vol_ratio", 0)
            print(f"  {etype:8s}: event window {event_ratio:.2f}x")

    # Returns
    ret = report.get("return_analysis", {})
    print("\n--- Returns (log return mean) ---")
    normal_ret = ret.get("normal_stats", {}).get("mean", 0)
    print(f"  {'normal':12s}: {normal_ret:.6f}")
    for wname, stats in ret.get("event_window_stats", {}).items():
        print(f"  {wname:12s}: {stats.get('mean', 0):.6f}  (n={stats.get('n', 0)})")

    # VIX regime
    vix = report.get("vix_regime", {})
    if "error" not in vix:
        print("\n--- VIX Regime ---")
        for regime, stats in vix.items():
            print(
                f"  {regime:10s}: ret={stats.get('mean_return', 0):.6f} "
                f"vol={stats.get('vol', 0):.6f} "
                f"sharpe={stats.get('sharpe_proxy', 0):.3f} "
                f"(n={stats.get('n_bars', 0)})"
            )

    # DXY trend
    dxy = report.get("dxy_trend", {})
    if "error" not in dxy:
        print("\n--- DXY Trend ---")
        for direction in ("dxy_rising", "dxy_falling"):
            stats = dxy.get(direction, {})
            print(
                f"  {direction:14s}: btc_ret={stats.get('mean_btc_return', 0):.6f} "
                f"(n={stats.get('n_bars', 0)})"
            )
        print(f"  correlation: {dxy.get('correlation', 0):.4f}")

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Macro event impact study on crypto")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--interval", default="4h", help="K-line interval (1h, 4h, etc.)")
    parser.add_argument("--klines-db", default=str(DEFAULT_KLINES_DB))
    parser.add_argument("--macro-db", default=str(DEFAULT_MACRO_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--no-strategy", action="store_true", help="Skip strategy analysis")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load klines
    klines_df = load_klines(args.klines_db, args.symbol, args.interval)
    if klines_df.empty:
        print(f"ERROR: No klines found for {args.symbol} {args.interval}")
        sys.exit(1)

    # Load macro data
    macro_db = MacroEventDB(args.macro_db)
    with macro_db.connect():
        events_df = load_macro_events(macro_db)
        vix_df = load_vix_daily(macro_db)
        dxy_df = load_dxy_daily(macro_db)

    if events_df.empty:
        print("ERROR: No macro events found. Run macro_calendar_sync.py first.")
        sys.exit(1)

    # Tag klines
    klines_tagged = tag_klines_with_event_distance(klines_df, events_df)

    # Count tagged bars
    window_counts = klines_tagged["event_window"].value_counts().to_dict()
    logger.info("Event window distribution: %s", window_counts)

    # Run analyses
    vol_results = analyze_volatility_around_events(klines_tagged)
    return_results = analyze_returns_around_events(klines_tagged)
    vix_results = analyze_vix_regime_performance(klines_df, vix_df)
    dxy_results = analyze_dxy_trend_performance(klines_df, dxy_df)

    # Strategy analysis (optional)
    strategy_results = None
    if not args.no_strategy:
        logger.info("Strategy analysis skipped (--no-strategy not specified, but not yet implemented)")
        strategy_results = {"status": "not_implemented"}

    # Generate report
    report = generate_report(
        symbol=args.symbol,
        interval=args.interval,
        vol_results=vol_results,
        return_results=return_results,
        vix_results=vix_results,
        dxy_results=dxy_results,
        strategy_results=strategy_results,
    )

    # Save JSON
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("Report saved to %s", output_path)

    # Print summary
    print_summary(report)


if __name__ == "__main__":
    main()
