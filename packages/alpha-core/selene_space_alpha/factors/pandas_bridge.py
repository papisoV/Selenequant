"""Pandas bridge - factor computations as pandas/numpy operations.

Each factor is implemented as a standalone
function operating on pandas DataFrames. These can be registered as
Indicator subclasses in SeleneQuant's indicator registry.
"""

import numpy as np
import pandas as pd


def _robust_norm(series: pd.Series) -> pd.Series:
    """Trailing-window median/MAD z-score — no look-ahead.

    Full-sample normalization was a confirmed future leak (median/MAD of the
    whole series peeks ahead). Fixed 2880-bar window (15m × 30 days); prefix
    invariance only requires the window to be trailing, so min_periods is a
    small warmup floor (short synthetic panels stay live past bar 20).
    INERT verdict on OOS: see the experiment report.
    """
    window, min_periods, clip = 2880, 20, 5.0
    median = series.rolling(window, min_periods=min_periods).median()
    mad = (series - median).abs().rolling(window, min_periods=min_periods).median()
    norm = (series - median) / (mad + 1e-6)
    return norm.clip(-clip, clip).fillna(0.0)


# --- Basic 6 factors ---


def compute_ret(data: pd.DataFrame) -> pd.Series:
    return _robust_norm(np.log(data["close"] / data["close"].shift(1) + 1e-9))


def compute_liq_score(data: pd.DataFrame) -> pd.Series:
    if "liquidity" not in data.columns or "fdv" not in data.columns:
        return pd.Series(0.0, index=data.index)
    ratio = data["liquidity"] / (data["fdv"] + 1e-6)
    return (ratio * 4.0).clip(0.0, 1.0)


def compute_pressure(data: pd.DataFrame) -> pd.Series:
    range_hl = data["high"] - data["low"] + 1e-9
    body = data["close"] - data["open"]
    strength = body / range_hl
    return np.tanh(strength * 3.0)


def compute_fomo(data: pd.DataFrame) -> pd.Series:
    vol = data["volume"]
    vol_prev = vol.shift(1)
    vol_chg = (vol - vol_prev) / (vol_prev + 1.0)
    acc = vol_chg - vol_chg.shift(1)
    return _robust_norm(acc.clip(-5.0, 5.0))


def compute_dev(data: pd.DataFrame) -> pd.Series:
    window = 20
    ma = data["close"].rolling(window=window, min_periods=1).mean()
    dev = (data["close"] - ma) / (ma + 1e-9)
    return _robust_norm(dev)


def compute_log_vol(data: pd.DataFrame) -> pd.Series:
    return _robust_norm(np.log1p(data["volume"]))


# --- Advanced 6 factors ---


def compute_vol_cluster(data: pd.DataFrame, window: int = 10) -> pd.Series:
    ret = np.log(data["close"] / data["close"].shift(1) + 1e-9)
    ret_sq = ret**2
    vol_ma = ret_sq.rolling(window=window, min_periods=1).mean()
    return _robust_norm(np.sqrt(vol_ma + 1e-9))


def compute_momentum_rev(data: pd.DataFrame, window: int = 5) -> pd.Series:
    ret = np.log(data["close"] / data["close"].shift(1) + 1e-9)
    mom = ret.rolling(window=window, min_periods=1).sum()
    mom_prev = mom.shift(1)
    reversal = ((mom * mom_prev) < 0).astype(float)
    return reversal


def compute_rel_strength(data: pd.DataFrame, window: int = 14) -> pd.Series:
    price_diff = data["close"].diff()
    gains = price_diff.clip(lower=0)
    losses = (-price_diff).clip(lower=0)
    avg_gain = gains.rolling(window=window, min_periods=1).mean()
    avg_loss = losses.rolling(window=window, min_periods=1).mean()
    rs = (avg_gain + 1e-9) / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return _robust_norm((rsi - 50) / 50)


def compute_hl_range(data: pd.DataFrame) -> pd.Series:
    return _robust_norm((data["high"] - data["low"]) / (data["close"] + 1e-9))


def compute_close_pos(data: pd.DataFrame) -> pd.Series:
    return (data["close"] - data["low"]) / (data["high"] - data["low"] + 1e-9)


def compute_vol_trend(data: pd.DataFrame) -> pd.Series:
    vol = data["volume"]
    vol_prev = vol.shift(1)
    trend = (vol - vol_prev) / (vol_prev + 1.0)
    return _robust_norm(trend)


# --- V4: Microstructure features (6) ---


def compute_wick_ratio(data: pd.DataFrame) -> pd.Series:
    body_top = np.maximum(data["open"], data["close"])
    body_bot = np.minimum(data["open"], data["close"])
    upper_wick = data["high"] - body_top
    lower_wick = body_bot - data["low"]
    total_range = data["high"] - data["low"] + 1e-9
    return _robust_norm((upper_wick + lower_wick) / total_range)


def compute_body_size(data: pd.DataFrame) -> pd.Series:
    return _robust_norm(abs(data["close"] - data["open"]) / (data["close"] + 1e-9))


def compute_imbalance(data: pd.DataFrame) -> pd.Series:
    hl_range = data["high"] - data["low"] + 1e-9
    return _robust_norm((2.0 * data["close"] - data["high"] - data["low"]) / hl_range)


def compute_vwap_dev(data: pd.DataFrame, window: int = 12) -> pd.Series:
    typical = (data["high"] + data["low"] + data["close"]) / 3.0
    vol = data["volume"].fillna(0.0) + 1e-9
    cum_vp = (typical * vol).rolling(window=window, min_periods=1).sum()
    cum_v = vol.rolling(window=window, min_periods=1).sum()
    vwap = cum_vp / (cum_v + 1e-9)
    return _robust_norm((data["close"] - vwap) / (vwap + 1e-9))


def compute_spread_proxy(data: pd.DataFrame) -> pd.Series:
    return _robust_norm((data["high"] - data["low"]) / (data["volume"] + 1e-9))


def compute_gap(data: pd.DataFrame) -> pd.Series:
    prev_close = data["close"].shift(1)
    return _robust_norm((data["open"] - prev_close) / (prev_close + 1e-9))


# --- V4: Rolling statistical features (4) ---


def compute_rs_ret5(data: pd.DataFrame) -> pd.Series:
    return _robust_norm(data["close"].pct_change(5))


def compute_rs_vol5(data: pd.DataFrame) -> pd.Series:
    return _robust_norm(
        data["close"].pct_change().rolling(5, min_periods=1).std().fillna(0.0)
    )


def compute_rs_skew(data: pd.DataFrame) -> pd.Series:
    return _robust_norm(
        data["close"].pct_change().rolling(20, min_periods=5).skew().fillna(0.0)
    )


def compute_rs_kurt(data: pd.DataFrame) -> pd.Series:
    return _robust_norm(
        data["close"]
        .pct_change()
        .rolling(20, min_periods=5)
        .kurt()
        .clip(-10, 10)
        .fillna(0.0)
    )


# --- V4: Multi-timeframe features (6) ---
# These require a merged DataFrame with TF15_ columns from aux interval.


def _tf_col(data: pd.DataFrame, name: str) -> str | None:
    """Find a multi-timeframe column, accepting any TF*_ prefix.

    Supports TF15_, TF15M_, TF1H_, TF1D_ etc. for different aux intervals.
    """
    for col in data.columns:
        c = str(col)
        if c.startswith("TF") and c.endswith(f"_{name}"):
            return c
    return None


def compute_tf_trend(data: pd.DataFrame) -> pd.Series:
    col = _tf_col(data, "close")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(np.log(data[col] / data[col].shift(4) + 1e-9))


def compute_tf_vol_ratio(data: pd.DataFrame) -> pd.Series:
    vol_col = _tf_col(data, "volume")
    if vol_col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(data["volume"] / (data[vol_col] / 3.0 + 1e-9))


def compute_tf_range_pos(data: pd.DataFrame) -> pd.Series:
    high_col = _tf_col(data, "high")
    low_col = _tf_col(data, "low")
    if high_col is None or low_col is None:
        return pd.Series(0.0, index=data.index)
    tf_range = data[high_col] - data[low_col] + 1e-9
    return (data["close"] - data[low_col]) / tf_range


def compute_tf_pressure(data: pd.DataFrame) -> pd.Series:
    close_col = _tf_col(data, "close")
    if close_col is None:
        return pd.Series(0.0, index=data.index)
    high_col = _tf_col(data, "high")
    low_col = _tf_col(data, "low")
    open_col = _tf_col(data, "open")
    tf_range = data[high_col] - data[low_col] + 1e-9
    strength = (data[close_col] - data[open_col]) / tf_range
    return np.tanh(strength * 3.0)


def compute_tf_dev(data: pd.DataFrame) -> pd.Series:
    col = _tf_col(data, "close")
    if col is None:
        return pd.Series(0.0, index=data.index)
    ma = data[col].rolling(20, min_periods=1).mean()
    return _robust_norm((data[col] - ma) / (ma + 1e-9))


def compute_tf_mom(data: pd.DataFrame) -> pd.Series:
    col = _tf_col(data, "close")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(data[col].rolling(12, min_periods=1).sum().pct_change())


# --- Volume shock features (3) ---
# Volume anomaly detection: when volume spikes far above normal.


def compute_vol_shock(data: pd.DataFrame, short: int = 6, long: int = 48) -> pd.Series:
    """Short/long volume ratio: sudden burst vs 12h baseline.

    short_ma (6 bars = 1.5h on 15m) captures current burst.
    long_ma (48 bars = 12h) captures normal level.
    Differs from LOG_VOL (absolute level) by measuring burst intensity.
    """
    vol = data["volume"].fillna(0.0)
    short_ma = vol.rolling(window=short, min_periods=2).mean() + 1e-9
    long_ma = vol.rolling(window=long, min_periods=5).mean() + 1e-9
    return _robust_norm(short_ma / long_ma)


def compute_vol_shock_z(
    data: pd.DataFrame, short: int = 6, long: int = 48
) -> pd.Series:
    """Volume burst z-score: how many sigma short-ma is above long-ma."""
    vol = data["volume"].fillna(0.0)
    short_ma = vol.rolling(window=short, min_periods=2).mean()
    long_ma = vol.rolling(window=long, min_periods=5).mean()
    long_std = vol.rolling(window=long, min_periods=5).std() + 1e-9
    z = (short_ma - long_ma) / long_std
    return z.clip(-5.0, 5.0)


def compute_vol_surge(data: pd.DataFrame, short: int = 6, long: int = 48) -> pd.Series:
    """Directed volume burst: short/long ratio * sign(return).

    Positive = sudden volume WITH rising price (buying surge).
    Negative = sudden volume WITH falling price (selling surge).
    Differs from PRESSURE (body/range) by requiring volume anomaly.
    """
    vol = data["volume"].fillna(0.0)
    short_ma = vol.rolling(window=short, min_periods=2).mean() + 1e-9
    long_ma = vol.rolling(window=long, min_periods=5).mean() + 1e-9
    burst = short_ma / long_ma
    ret = np.log(data["close"] / data["close"].shift(1) + 1e-9)
    return _robust_norm(burst * np.sign(ret))


# --- Derivatives features (4) ---
# These read from derivatives.db and align to kline timestamps via merge_asof.


def compute_derivatives_factors(
    df_kline: pd.DataFrame,
    symbol: str,
    interval: str,
    db_path: str | None = None,
    exchange: str = "binance",
) -> pd.DataFrame:
    """Load derivatives data from DB and merge into kline DataFrame.

    Returns df_kline with 4 new columns: FUNDING_RATE, FUNDING_RATE_Z,
    OI_CHANGE, LS_RATIO. Missing values filled with NaN.

    ``db_path=None`` resolves the canonical repo-root DB. A bare relative
    default used to resolve against the CWD, so a process started anywhere
    else silently got "no derivatives data" (all four columns NaN).
    """
    from pathlib import Path

    from selene_space_core.derivatives_paths import default_derivatives_db_path
    from selene_space_market.derivatives_db import DerivativesDB

    db_file = Path(db_path) if db_path else default_derivatives_db_path()
    if not db_file.exists():
        df_kline["FUNDING_RATE"] = np.nan
        df_kline["FUNDING_RATE_Z"] = np.nan
        df_kline["OI_CHANGE"] = np.nan
        df_kline["LS_RATIO"] = np.nan
        return df_kline

    db = DerivativesDB(db_file)
    try:
        db.open()
        start_ts = int(df_kline["timestamp"].min()) - 1
        end_ts = int(df_kline["timestamp"].max()) + 1

        # --- Funding Rate (8h bars, backward merge = most recent rate before kline) ---
        df_fr = db.get_funding_rates(exchange, symbol, start_ts, end_ts)
        if not df_fr.empty:
            df_fr = df_fr.sort_values("timestamp")
            df_kline = pd.merge_asof(
                df_kline.sort_values("timestamp"),
                df_fr[["timestamp", "funding_rate"]].rename(
                    columns={"funding_rate": "FUNDING_RATE_RAW"}
                ),
                on="timestamp",
                direction="backward",
            )
            df_kline["FUNDING_RATE"] = df_kline["FUNDING_RATE_RAW"].fillna(0.0)
            # Z-score over 24 periods (8 days)
            fr_series = df_kline["FUNDING_RATE_RAW"]
            rolling_mean = fr_series.rolling(24, min_periods=8).mean()
            rolling_std = fr_series.rolling(24, min_periods=8).std() + 1e-9
            df_kline["FUNDING_RATE_Z"] = (
                (fr_series - rolling_mean) / rolling_std
            ).fillna(0.0)
            df_kline.drop(columns=["FUNDING_RATE_RAW"], inplace=True)
        else:
            df_kline["FUNDING_RATE"] = 0.0
            df_kline["FUNDING_RATE_Z"] = 0.0

        # --- Open Interest (pick closest interval) ---
        oi_interval = _pick_closest_interval(interval)
        df_oi = db.get_open_interest(exchange, symbol, oi_interval, start_ts, end_ts)
        if not df_oi.empty:
            df_oi = df_oi.sort_values("timestamp")
            df_oi["OI_CHANGE"] = df_oi["open_interest"].pct_change().fillna(0.0)
            oi_first_ts = df_oi["timestamp"].min()
            df_kline = pd.merge_asof(
                df_kline.sort_values("timestamp"),
                df_oi[["timestamp", "OI_CHANGE"]],
                on="timestamp",
                direction="backward",
            )
            # Only fill where real data exists; NaN elsewhere (no fake defaults)
            df_kline.loc[df_kline["timestamp"] < oi_first_ts, "OI_CHANGE"] = np.nan
        else:
            df_kline["OI_CHANGE"] = np.nan

        # --- Taker Ratio (LS_RATIO = buy_vol / sell_vol) ---
        taker_interval = _pick_closest_interval(interval)
        df_tr = db.get_taker_ratio(exchange, symbol, taker_interval, start_ts, end_ts)
        if not df_tr.empty:
            df_tr = df_tr.sort_values("timestamp")
            df_tr["LS_RATIO"] = df_tr["buy_vol"] / (df_tr["sell_vol"] + 1e-9)
            tr_first_ts = df_tr["timestamp"].min()
            df_kline = pd.merge_asof(
                df_kline.sort_values("timestamp"),
                df_tr[["timestamp", "LS_RATIO"]],
                on="timestamp",
                direction="backward",
            )
            # Only fill where real data exists; NaN elsewhere
            df_kline.loc[df_kline["timestamp"] < tr_first_ts, "LS_RATIO"] = np.nan
        else:
            df_kline["LS_RATIO"] = np.nan
    finally:
        db.close()

    return df_kline


def _pick_closest_interval(kline_interval: str) -> str:
    """Map kline interval to the best available derivatives data interval."""
    mapping = {
        "15m": "1h",
        "30m": "1h",
        "1h": "1h",
        "4h": "4h",
        "6h": "4h",
        "8h": "4h",
        "1d": "1d",
    }
    return mapping.get(kline_interval, "4h")


def compute_funding_rate(data: pd.DataFrame) -> pd.Series:
    if "FUNDING_RATE" not in data.columns:
        return pd.Series(0.0, index=data.index)
    return data["FUNDING_RATE"].fillna(0.0)


def compute_funding_rate_z(data: pd.DataFrame) -> pd.Series:
    if "FUNDING_RATE_Z" not in data.columns:
        return pd.Series(0.0, index=data.index)
    return data["FUNDING_RATE_Z"].fillna(0.0)


def compute_oi_change(data: pd.DataFrame) -> pd.Series:
    if "OI_CHANGE" not in data.columns:
        return pd.Series(0.0, index=data.index)
    return data["OI_CHANGE"].fillna(0.0)


def compute_ls_ratio(data: pd.DataFrame) -> pd.Series:
    if "LS_RATIO" not in data.columns:
        return pd.Series(0.0, index=data.index)
    # Center around 1.0: ratio > 1 = more buying, < 1 = more selling
    return _robust_norm(data["LS_RATIO"].fillna(1.0) - 1.0)


def _of_col(data: pd.DataFrame, name: str) -> str | None:
    """Find an orderflow column with OF_ prefix in DataFrame."""
    for col in data.columns:
        if str(col) == f"OF_{name}" or str(col) == name:
            return str(col)
    return None


def _deriv_col(data: pd.DataFrame, name: str) -> str | None:
    """Find a derivatives column with DERIV_ prefix in DataFrame."""
    for col in data.columns:
        if str(col) == f"DERIV_{name}" or str(col) == name:
            return str(col)
    return None


# --- Orderflow features (10 new v13 factors) ---


def compute_of_global_ls_ratio(data: pd.DataFrame) -> pd.Series:
    col = _of_col(data, "global_ls_ratio")
    if col is None:
        return pd.Series(0.0, index=data.index)
    series = data[col].fillna(1.0).clip(0.01)
    return _robust_norm(np.log(series))


def compute_of_global_ls_extreme(data: pd.DataFrame) -> pd.Series:
    col = _of_col(data, "global_ls_ratio")
    if col is None:
        return pd.Series(0.0, index=data.index)
    series = data[col].fillna(1.0)
    rolling_mean = series.rolling(21, min_periods=5).mean()
    rolling_std = series.rolling(21, min_periods=5).std() + 1e-9
    z = (series - rolling_mean) / rolling_std
    return z.clip(-5.0, 5.0).fillna(0.0)


def compute_of_smart_crowd_div(data: pd.DataFrame) -> pd.Series:
    top_acc_col = _of_col(data, "top_acc_ratio")
    global_ls_col = _of_col(data, "global_ls_ratio")
    if top_acc_col is None or global_ls_col is None:
        return pd.Series(0.0, index=data.index)
    top_acc = data[top_acc_col].fillna(1.0).clip(0.01)
    global_ls = data[global_ls_col].fillna(1.0).clip(0.01)
    return _robust_norm(np.log(top_acc) - np.log(global_ls))


def compute_of_buy_vol_pressure(data: pd.DataFrame) -> pd.Series:
    col = _of_col(data, "buy_vol")
    if col is None:
        return pd.Series(0.0, index=data.index)
    series = data[col].fillna(0.0)
    rolling_mean = series.rolling(21, min_periods=3).mean() + 1e-9
    return _robust_norm((series / rolling_mean).fillna(0.0))


def compute_of_sell_vol_pressure(data: pd.DataFrame) -> pd.Series:
    col = _of_col(data, "sell_vol")
    if col is None:
        return pd.Series(0.0, index=data.index)
    series = data[col].fillna(0.0)
    rolling_mean = series.rolling(21, min_periods=3).mean() + 1e-9
    return _robust_norm((series / rolling_mean).fillna(0.0))


def compute_of_vol_imbalance(data: pd.DataFrame) -> pd.Series:
    buy_col = _of_col(data, "buy_vol")
    sell_col = _of_col(data, "sell_vol")
    if buy_col is None or sell_col is None:
        return pd.Series(0.0, index=data.index)
    buy = data[buy_col].fillna(0.0)
    sell = data[sell_col].fillna(0.0)
    total = buy + sell + 1e-9
    return _robust_norm((buy - sell) / total)


def compute_of_top_acc_change(data: pd.DataFrame) -> pd.Series:
    col = _of_col(data, "top_acc_ratio")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(data[col].fillna(1.0).pct_change().fillna(0.0))


def compute_of_top_pos_acc_div(data: pd.DataFrame) -> pd.Series:
    pos_col = _of_col(data, "top_pos_ratio")
    acc_col = _of_col(data, "top_acc_ratio")
    if pos_col is None or acc_col is None:
        return pd.Series(0.0, index=data.index)
    pos = data[pos_col].fillna(1.0).clip(0.01)
    acc = data[acc_col].fillna(1.0).clip(0.01)
    return _robust_norm(np.log(pos) - np.log(acc))


def compute_deriv_funding_momentum(data: pd.DataFrame) -> pd.Series:
    col = _deriv_col(data, "funding_rate")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(data[col].fillna(0.0).rolling(3, min_periods=1).sum())


def compute_deriv_funding_extreme(data: pd.DataFrame) -> pd.Series:
    col = _deriv_col(data, "funding_rate")
    if col is None:
        return pd.Series(0.0, index=data.index)
    series = data[col].fillna(0.0)
    rolling_mean = series.rolling(30, min_periods=8).mean()
    rolling_std = series.rolling(30, min_periods=8).std() + 1e-9
    z = (series - rolling_mean) / rolling_std
    return z.clip(-5.0, 5.0).fillna(0.0)


# Backward-compatible aliases (TF15_ naming)
compute_tf15_trend = compute_tf_trend
compute_tf15_vol_ratio = compute_tf_vol_ratio
compute_tf15_range_pos = compute_tf_range_pos
compute_tf15_pressure = compute_tf_pressure
compute_tf15_dev = compute_tf_dev
compute_tf15_mom = compute_tf_mom


# --- Factor metadata for registration ---

BASIC_FACTORS: dict[str, tuple[object, str]] = {
    "meme_ret": (compute_ret, "Log return (robust normalized)"),
    "meme_liq_score": (compute_liq_score, "Liquidity/FDV health score"),
    "meme_pressure": (compute_pressure, "Buy/sell imbalance"),
    "meme_fomo": (compute_fomo, "Volume acceleration (FOMO)"),
    "meme_dev": (compute_dev, "Price deviation from MA"),
    "meme_log_vol": (compute_log_vol, "Log volume (robust normalized)"),
}

ADVANCED_FACTORS: dict[str, tuple[object, str]] = {
    "meme_vol_cluster": (compute_vol_cluster, "Volatility clustering"),
    "meme_momentum_rev": (compute_momentum_rev, "Momentum reversal signal"),
    "meme_rel_strength": (compute_rel_strength, "Relative strength (RSI-derived)"),
    "meme_hl_range": (compute_hl_range, "High-low range ratio"),
    "meme_close_pos": (compute_close_pos, "Close position in range"),
    "meme_vol_trend": (compute_vol_trend, "Volume trend"),
}

HIGH_FREQ_FACTORS: dict[str, tuple[object, str]] = {
    "hf_wick_ratio": (compute_wick_ratio, "Wick-to-range ratio"),
    "hf_body_size": (compute_body_size, "Absolute body size"),
    "hf_imbalance": (compute_imbalance, "Close imbalance in range"),
    "hf_vwap_dev": (compute_vwap_dev, "Deviation from VWAP"),
    "hf_spread_proxy": (compute_spread_proxy, "Spread proxy (range/volume)"),
    "hf_gap": (compute_gap, "Gap from previous close"),
    "hf_rs_ret5": (compute_rs_ret5, "5-bar return"),
    "hf_rs_vol5": (compute_rs_vol5, "5-bar rolling volatility"),
    "hf_rs_skew": (compute_rs_skew, "20-bar return skewness"),
    "hf_rs_kurt": (compute_rs_kurt, "20-bar return kurtosis"),
    # Generic multi-TF factors (auto-detect TF*_ prefix in DataFrame)
    "hf_tf_trend": (compute_tf_trend, "Aux timeframe trend"),
    "hf_tf_vol_ratio": (compute_tf_vol_ratio, "Base vol vs aux avg vol"),
    "hf_tf_range_pos": (compute_tf_range_pos, "Close position in aux range"),
    "hf_tf_pressure": (compute_tf_pressure, "Aux buy/sell pressure"),
    "hf_tf_dev": (compute_tf_dev, "Aux deviation from MA"),
    "hf_tf_mom": (compute_tf_mom, "Aux momentum"),
    # Backward-compatible TF15 aliases
    "hf_tf15_trend": (compute_tf15_trend, "15m timeframe trend (1h)"),
    "hf_tf15_vol_ratio": (compute_tf15_vol_ratio, "5m vol vs 15m avg vol"),
    "hf_tf15_range_pos": (compute_tf15_range_pos, "Close position in 15m range"),
    "hf_tf15_pressure": (compute_tf15_pressure, "15m buy/sell pressure"),
    "hf_tf15_dev": (compute_tf15_dev, "15m deviation from MA"),
    "hf_tf15_mom": (compute_tf15_mom, "15m 3-hour momentum"),
    # v13 Orderflow factors (8)
    "of_global_ls_ratio": (
        compute_of_global_ls_ratio,
        "Global LS ratio (retail sentiment)",
    ),
    "of_global_ls_extreme": (
        compute_of_global_ls_extreme,
        "Global LS z-score (contrarian)",
    ),
    "of_smart_crowd_div": (
        compute_of_smart_crowd_div,
        "Smart money vs retail divergence",
    ),
    "of_buy_vol_pressure": (
        compute_of_buy_vol_pressure,
        "Buying volume vs rolling mean",
    ),
    "of_sell_vol_pressure": (
        compute_of_sell_vol_pressure,
        "Selling volume vs rolling mean",
    ),
    "of_vol_imbalance": (compute_of_vol_imbalance, "Directional volume imbalance"),
    "of_top_acc_change": (compute_of_top_acc_change, "Top account ratio momentum"),
    "of_top_pos_acc_div": (
        compute_of_top_pos_acc_div,
        "Position vs account concentration",
    ),
    # v13 Derivatives factors (2)
    "deriv_funding_momentum": (
        compute_deriv_funding_momentum,
        "3-bar funding rate sum",
    ),
    "deriv_funding_extreme": (
        compute_deriv_funding_extreme,
        "Funding rate z-score (30-bar)",
    ),
}

VOLUME_SHOCK_FACTORS: dict[str, tuple[object, str]] = {
    "vs_vol_shock": (compute_vol_shock, "Volume ratio vs rolling mean"),
    "vs_vol_shock_z": (compute_vol_shock_z, "Volume z-score (48-bar window)"),
    "vs_vol_surge": (compute_vol_surge, "Volume surge * price direction"),
}

DERIVATIVES_FACTORS: dict[str, tuple[object, str]] = {
    "deriv_funding_rate": (compute_funding_rate, "Current funding rate"),
    "deriv_funding_rate_z": (
        compute_funding_rate_z,
        "Funding rate z-score (24-period window)",
    ),
    "deriv_oi_change": (compute_oi_change, "Open interest pct change"),
    "deriv_ls_ratio": (compute_ls_ratio, "Taker buy/sell ratio (normalized)"),
}

# research_factors.py imports from this module — moving these to the top would
# create a circular import, so they stay at module tail (see also ruff.toml).
from ..serenity.signal_bridge import SERENITY_FACTORS  # noqa: E402
from .research_factors import RESEARCH_FACTORS  # noqa: E402
from .awesome_factors import AWESOME_FACTORS  # noqa: E402
from .qd_factors import QD_FACTORS  # noqa: E402
from .btc_bg_factors import BTC_BG_FACTORS  # noqa: E402
from .artifact_factors import ARTIFACT_FACTORS  # noqa: E402

ALL_FACTORS: dict[str, tuple[object, str]] = {
    **BASIC_FACTORS,
    **ADVANCED_FACTORS,
    **HIGH_FREQ_FACTORS,
    **VOLUME_SHOCK_FACTORS,
    **DERIVATIVES_FACTORS,
    **SERENITY_FACTORS,
    **RESEARCH_FACTORS,
    **AWESOME_FACTORS,
    **QD_FACTORS,
    **BTC_BG_FACTORS,
    **ARTIFACT_FACTORS,
}
