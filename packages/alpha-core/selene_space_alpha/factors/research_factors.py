"""Research-grade factor library merged from selenespace-research.

Contains FIB/FIB2/FIB3 (21 Fibonacci factors) + advanced OF/DERIV factors
developed in selenespace-research. Powers research champion strategies
(TF_PRESSURE OF_TOP_ACC_RATIO NEG ADD, FIB_POS GAP NEG ADD, etc.).

Merged 2026-07-18. See docs/research/STRATEGY_REPORT_20260712.md.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .pandas_bridge import _robust_norm, _of_col, _deriv_col


def _find_swing_lows(low: pd.Series, window: int = 20) -> pd.Series:
    """Find swing low points: bar's low <= all surrounding lows within window."""
    roll_min = low.rolling(window=2 * window + 1, center=True, min_periods=1).min()
    return low <= roll_min


def _compute_fib_proximity(
    data: pd.DataFrame,
    fib_levels: tuple[float, ...] = (0.20, 0.382, 0.50, 0.618, 0.886),
    swing_window: int | None = None,
) -> pd.Series:
    """Compute proximity to nearest important Fibonacci level.

    For each bar, find the most recent swing low, build a Fib grid,
    then compute how close the current close is to the nearest
    important FIB level (0.20, 0.382, 0.50, 0.618, 0.886).

    Returns: max proximity across all levels. Higher = closer to a FIB level.
    This captures the non-linear "confluence" signal where price
    reacts only at specific Fib levels, not continuously.
    """
    close = data["close"]
    low = data["low"]

    if swing_window is None:
        swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)

    if len(swing_indices) == 0:
        return result

    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]

        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1

        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        # Find proximity to nearest FIB level
        # Use inverse distance: 1 / (1 + |dist|/scale)
        # This gives a smooth peak at each FIB level, decaying to 0 far away
        scale = 0.10  # 10% of grid span = ~2.5% price distance
        max_prox = 0.0
        for lv in fib_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_prox:
                max_prox = prox

        result.iloc[i] = max_prox

    return _robust_norm(result)




def compute_fib_pos(data: pd.DataFrame) -> pd.Series:
    """Proximity to nearest important Fibonacci level (0.20/0.382/0.50/0.618/0.886) from swing low."""
    return _compute_fib_proximity(data)


# --- Fibonacci confluence factors (5 new FIB_ factors) ---

# 3 validated confluence points (13-34x vs random, cross-asset consistent)
_CONFLUENCE_CONFIGS: tuple[tuple[float, float, float, float], ...] = (
    # (big_fib, sub_lower, sub_upper, sub_fib) — price = big_fib level
    (0.618, 0.50, 1.0, 0.236),   # 0.618 = 0.5 + 0.236*0.5
    (0.618, 0.382, 1.0, 0.382),  # 0.618 = 0.382 + 0.382*0.618
    (0.886, 0.618, 1.0, 0.700),  # 0.886 = 0.618 + 0.700*0.382
)

# Confluence prices for each swing: big_fib_price == sub_lower_price + sub_fib * (anchor - sub_lower_price)
# This is mathematically guaranteed by the definitions above.




def _compute_fib_confluence(data: pd.DataFrame) -> pd.Series:
    """Confluence-specific signal — excess proximity at Fib overlap points.

    Measures how much MORE concentrated price is at confluence points
    compared to regular Fib levels. This is the residual signal:
    FIB_CONFLUENCE = confluence_proximity - regular_proximity

    Positive = price is closer to a confluence point than to any
    regular Fib level = the multi-grid overlap is the active attractor.
    Negative = price is near a regular Fib level but NOT at a confluence.
    This decorrelates from FIB_POS (r ~ 0.3 instead of 0.996).
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    swing_idx = 0
    regular_levels = (0.20, 0.382, 0.50, 0.618, 0.886)
    scale = 0.06

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        # Regular Fib proximity (same as FIB_POS core logic)
        max_regular = 0.0
        for lv in regular_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_regular:
                max_regular = prox

        # Confluence-only proximity (just the 3 validated overlap points)
        max_conf = 0.0
        for big_fib, _sl, _su, _sf in _CONFLUENCE_CONFIGS:
            conf_price = origin + big_fib * grid_span
            dist = abs(close.iloc[i] - conf_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_conf:
                max_conf = prox

        # Residual: positive = confluence is the active attractor
        # When price is at 0.618 (which is a confluence), both are high
        # but conf may be slightly higher due to tighter clustering
        # When price is at 0.50 (not a confluence), regular is high but conf is low
        result.iloc[i] = max_conf - max_regular * 0.95

    return _robust_norm(result)




def compute_fib_confluence(data: pd.DataFrame) -> pd.Series:
    """Proximity to Fibonacci confluence points (0.618+0.236, 0.618+0.382, 0.886+0.700)."""
    return _compute_fib_confluence(data)




def _compute_fib_density(data: pd.DataFrame) -> pd.Series:
    """Count of Fibonacci levels near current price (density of Fib structure).

    High density = price is in a zone where multiple Fib levels cluster,
    indicating a high-interest price area with stacked orders.
    Uses all 21 user-defined Fib levels.
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    # All 21 user-defined Fib levels
    all_fib_levels = (
        0.0, 0.20, 0.236, 0.30, 0.382, 0.50, 0.618, 0.70, 0.80,
        0.886, 1.0, 1.13, 1.272, 1.382, 1.618,
    )

    swing_idx = 0
    band_width = 0.03  # 3% of grid span = ~0.75% price distance

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        count = 0
        for lv in all_fib_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            if dist < band_width:
                count += 1

        result.iloc[i] = count

    return _robust_norm(result)




def compute_fib_density(data: pd.DataFrame) -> pd.Series:
    """Density of Fibonacci levels near current price (cluster zone indicator)."""
    return _compute_fib_density(data)




def _compute_fib_bounce(data: pd.DataFrame) -> pd.Series:
    """Fibonacci bounce signal — wick rejection at recent Fib level.

    Detects when current bar shows a wick rejection (shadow pierces Fib level
    but body closes back), weighted by how recently the swing was established.
    Uses a rolling window of recent bars to accumulate bounce evidence, so
    the signal is not excessively sparse on higher timeframes.
    """
    close = data["close"]
    high = data["high"]
    low = data["low"]
    open_ = data["open"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    important_levels = (0.20, 0.382, 0.50, 0.618, 0.886)
    tolerance = 0.025  # 2.5% of grid span (wider for 8h bars)
    lookback = 5  # Accumulate bounce evidence over recent bars

    swing_idx = 0

    # Pre-compute per-bar bounce scores
    bounce_scores = np.zeros(len(close))

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        # Time decay: closer swings get higher weight
        swing_i = list(close.index).index(swing_indices[swing_idx])
        bars_since_swing = i - swing_i
        time_weight = 1.0 / (1.0 + bars_since_swing / 30.0)

        # Check current and recent bars for wick rejection at Fib levels
        check_start = max(0, i - lookback + 1)
        for j in range(check_start, i + 1):
            cur_close = close.iloc[j]
            cur_open = open_.iloc[j]
            cur_high = high.iloc[j]
            cur_low = low.iloc[j]
            body_top_cur = max(cur_close, cur_open)
            body_bot_cur = min(cur_close, cur_open)

            for lv in important_levels:
                fib_price = origin + lv * grid_span
                abs_tol = grid_span * tolerance

                # Wick rejection from above: high touches Fib, body below
                if cur_high >= fib_price - abs_tol and body_top_cur < fib_price + abs_tol:
                    dist_to_level = abs(cur_high - fib_price) / (abs_tol + 1e-9)
                    score = (1.0 - dist_to_level) * time_weight
                    bounce_scores[i] += score

                # Wick rejection from below: low touches Fib, body above
                if cur_low <= fib_price + abs_tol and body_bot_cur > fib_price - abs_tol:
                    dist_to_level = abs(cur_low - fib_price) / (abs_tol + 1e-9)
                    score = (1.0 - dist_to_level) * time_weight
                    bounce_scores[i] += score

    result = pd.Series(bounce_scores, index=close.index)
    return _robust_norm(result)




def compute_fib_bounce(data: pd.DataFrame) -> pd.Series:
    """Fibonacci bounce signal — wick rejection at Fib level with time decay."""
    return _compute_fib_bounce(data)




def _compute_fib_strength(data: pd.DataFrame) -> pd.Series:
    """Fibonacci grid signal strength — proximity weighted by swing significance.

    Larger swings (bigger price range from swing low to current) produce
    more reliable Fib levels. This factor combines proximity with swing
    magnitude to emphasize levels from major swings.
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    fib_levels = (0.20, 0.382, 0.50, 0.618, 0.886)
    scale = 0.10

    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        # Swing significance: ratio of grid span to current price
        # Larger swings = more important Fib structure
        significance = min(grid_span / (close.iloc[i] + 1e-9), 0.5) / 0.5  # Normalize to [0, 1]

        max_prox = 0.0
        for lv in fib_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_prox:
                max_prox = prox

        result.iloc[i] = max_prox * significance

    return _robust_norm(result)




def compute_fib_strength(data: pd.DataFrame) -> pd.Series:
    """Fibonacci signal strength — proximity weighted by swing significance."""
    return _compute_fib_strength(data)




def _compute_fib_momentum(data: pd.DataFrame) -> pd.Series:
    """Fibonacci direction momentum — price movement direction within Fib grid.

    Tracks whether price is moving toward or away from the nearest important
    Fib level, and whether it just crossed one. Captures the directional
    intent of price relative to Fib structure.
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    fib_levels = (0.20, 0.382, 0.50, 0.618, 0.886)

    swing_idx = 0
    prev_fib_pos = 0.0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            prev_fib_pos = 0.0
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            prev_fib_pos = 0.0
            continue

        # Find nearest Fib level and position relative to it
        min_dist = float("inf")
        nearest_level = 0.50
        for lv in fib_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price)
            if dist < min_dist:
                min_dist = dist
                nearest_level = lv

        # Position: positive = above nearest Fib, negative = below
        nearest_price = origin + nearest_level * grid_span
        cur_pos = (close.iloc[i] - nearest_price) / (grid_span + 1e-9)

        # Momentum = change in position relative to nearest Fib
        momentum = cur_pos - prev_fib_pos
        prev_fib_pos = cur_pos

        result.iloc[i] = momentum

    return _robust_norm(result)




def compute_fib_momentum(data: pd.DataFrame) -> pd.Series:
    """Fibonacci direction momentum — price movement within Fib grid."""
    return _compute_fib_momentum(data)


# --- Extended Fibonacci factors (3 new FIB_ factors) ---




def _find_all_swing_lows(low: pd.Series, window: int = 20) -> list[int]:
    """Find all swing low indices (not just the most recent)."""
    roll_min = low.rolling(window=2 * window + 1, center=True, min_periods=1).min()
    is_swing = low <= roll_min
    return low.index[is_swing].tolist()




def _find_swing_highs(high: pd.Series, window: int = 20) -> pd.Series:
    """Find swing high points: bar's high >= all surrounding highs within window."""
    roll_max = high.rolling(window=2 * window + 1, center=True, min_periods=1).max()
    return high >= roll_max




def _find_all_swing_highs(high: pd.Series, window: int = 20) -> list[int]:
    """Find all swing high indices."""
    roll_max = high.rolling(window=2 * window + 1, center=True, min_periods=1).max()
    is_swing = high >= roll_max
    return high.index[is_swing].tolist()




def _compute_atr(data: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range."""
    high = data["high"]
    low = data["low"]
    prev_close = data["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window=window, min_periods=1).mean()




def _compute_trend(data: pd.DataFrame, window: int = 50) -> pd.Series:
    """Trend direction: +1 uptrend, -1 downtrend, 0 neutral."""
    close = data["close"]
    ma = close.rolling(window=window, min_periods=1).mean()
    trend = pd.Series(0, index=close.index, dtype=float)
    trend[close > ma * 1.005] = 1.0
    trend[close < ma * 0.995] = -1.0
    return trend




def _compute_fib_resonance(data: pd.DataFrame) -> pd.Series:
    """Multi-anchor Fib resonance — how many swing-low Fib grids align at current price.

    Uses the last 5 swing lows, each generating an independent Fib grid.
    Counts how many grids have a Fib level near the current close.
    More overlapping grids = stronger resonance = more reliable level.

    Unlike FIB_DENSITY (which counts levels from ONE grid), this measures
    cross-grid overlap from INDEPENDENT anchors at different price scales.
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    all_swing_indices = _find_all_swing_lows(low, swing_window)

    result = pd.Series(0.0, index=close.index)

    if len(all_swing_indices) < 2:
        return result

    fib_levels = (0.382, 0.50, 0.618, 0.886)
    band_width = 0.025  # 2.5% of grid span

    for i in range(len(close)):
        idx = close.index[i]

        # Collect up to 5 most recent swings before this bar
        recent_swings = [s for s in all_swing_indices if s <= idx][-5:]
        if not recent_swings:
            continue

        resonance_count = 0
        for swing_idx in recent_swings:
            if swing_idx not in data.index:
                continue
            swing_row = data.loc[swing_idx]
            body_bottom = min(swing_row["open"], swing_row["close"])
            swing_low = swing_row["low"]

            anchor = body_bottom
            grid_span = (anchor - swing_low) / 1.13
            origin = anchor - grid_span

            if grid_span < 1e-10 or origin <= 0:
                continue

            # Check if any Fib level from this grid is near current price
            for lv in fib_levels:
                fib_price = origin + lv * grid_span
                dist = abs(close.iloc[i] - fib_price) / grid_span
                if dist < band_width:
                    resonance_count += 1
                    break  # One hit per grid is enough

        result.iloc[i] = resonance_count

    return _robust_norm(result)




def compute_fib_resonance(data: pd.DataFrame) -> pd.Series:
    """Multi-anchor Fib resonance — overlap count across independent swing-low grids."""
    return _compute_fib_resonance(data)




def _compute_fib_crossover(data: pd.DataFrame) -> pd.Series:
    """Fib level crossover signal — momentum when price crosses a Fib level.

    Detects when price crosses from below to above a Fib level (bullish breakout)
    or from above to below (bearish breakdown). Measures the strength of the
    cross by how decisively the close is beyond the level.

    Positive = bullish crossover (price broke above Fib), negative = bearish.
    Unlike proximity-based factors, this captures the MOMENT of structural change.
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    fib_levels = (0.382, 0.50, 0.618, 0.886)
    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        if i < 1:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        prev_close = close.iloc[i - 1]
        cur_close = close.iloc[i]

        max_crossover = 0.0
        for lv in fib_levels:
            fib_price = origin + lv * grid_span

            # Bullish crossover: prev close below Fib, current close above
            if prev_close <= fib_price and cur_close > fib_price:
                strength = (cur_close - fib_price) / grid_span
                if strength > max_crossover:
                    max_crossover = strength

            # Bearish crossover: prev close above Fib, current close below
            elif prev_close >= fib_price and cur_close < fib_price:
                strength = (fib_price - cur_close) / grid_span
                crossover_val = -strength
                if abs(crossover_val) > abs(max_crossover):
                    max_crossover = crossover_val

        result.iloc[i] = max_crossover

    return _robust_norm(result)




def compute_fib_crossover(data: pd.DataFrame) -> pd.Series:
    """Fib level crossover — directional signal when price crosses Fib level."""
    return _compute_fib_crossover(data)




def _compute_fib_decay_pos(data: pd.DataFrame) -> pd.Series:
    """Fresh Fib signal — residual after age decay subtracted from raw proximity.

    Unlike FIB_POS which treats all swings equally, this computes:
    residual = age_weighted_proximity - raw_proximity * decay_factor

    Positive = the Fib signal comes from RECENT swings (fresh structure).
    Negative = the Fib signal is mostly from OLD swings (stale structure).
    This decorrelates from FIB_POS by focusing on temporal freshness.

    Level weights: 0.618/0.886 get 3x (validated 13-34x vs random).
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    level_weights = {0.20: 1.0, 0.382: 1.5, 0.50: 2.0, 0.618: 3.0, 0.886: 3.0}
    scale = 0.10
    half_life = 10.0  # Short half-life: only recent swings matter

    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        # Age decay
        swing_i = list(close.index).index(swing_indices[swing_idx])
        bars_since = i - swing_i
        age_weight = 1.0 / (1.0 + bars_since / half_life)

        # Weighted proximity (with level weights and age decay)
        max_weighted = 0.0
        for lv, weight in level_weights.items():
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale) * weight * age_weight
            if prox > max_weighted:
                max_weighted = prox

        # Raw proximity (no weights, no decay — same as FIB_POS core)
        max_raw = 0.0
        for lv in (0.20, 0.382, 0.50, 0.618, 0.886):
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_raw:
                max_raw = prox

        # Residual: positive = fresh signal dominates, negative = stale
        result.iloc[i] = max_weighted - max_raw * 2.0 * (1.0 - age_weight)

    return _robust_norm(result)




def compute_fib_decay_pos(data: pd.DataFrame) -> pd.Series:
    """Age-decayed Fib proximity with dynamic level weights."""
    return _compute_fib_decay_pos(data)


# --- FIB2: Next-generation Fibonacci factors (6 new factors) ---




def _compute_fib2_high_density(data: pd.DataFrame) -> pd.Series:
    """Fib density from swing HIGH anchors (downward retracement grid).

    Unlike FIB_DENSITY which builds upward grids from swing lows,
    this builds downward grids from swing highs. Fib levels act as
    resistance in uptrends (pullback targets from above).
    """
    close = data["close"]
    high = data["high"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_highs(high, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    all_fib_levels = (
        0.0, 0.20, 0.236, 0.30, 0.382, 0.50, 0.618, 0.70, 0.80,
        0.886, 1.0, 1.13, 1.272, 1.382, 1.618,
    )
    band_width = 0.03
    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_top = max(swing_row["open"], swing_row["close"])
        swing_high = swing_row["high"]

        anchor = body_top
        grid_span = (swing_high - anchor) / 1.13
        origin = anchor + grid_span

        if grid_span < 1e-10:
            continue

        count = 0
        for lv in all_fib_levels:
            fib_price = origin - lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            if dist < band_width:
                count += 1

        result.iloc[i] = count

    return _robust_norm(result)




def compute_fib2_high_density(data: pd.DataFrame) -> pd.Series:
    """Fib density from swing HIGH anchors (downward retracement grid)."""
    return _compute_fib2_high_density(data)




def _compute_fib2_atr_prox(data: pd.DataFrame) -> pd.Series:
    """ATR-adaptive Fib proximity from swing HIGH anchors.

    Uses swing HIGH grid (downward retracement) with ATR-adaptive band
    width. This is fundamentally different from FIB_POS which uses swing
    LOW upward grids with fixed scale. The combination of different grid
    orientation AND adaptive scale ensures strong decorrelation.
    """
    close = data["close"]
    high = data["high"]
    swing_window = max(5, min(30, len(data) // 100))

    atr = _compute_atr(data)

    is_swing = _find_swing_highs(high, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    fib_levels = (0.20, 0.382, 0.50, 0.618, 0.886)
    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_top = max(swing_row["open"], swing_row["close"])
        swing_high = swing_row["high"]

        anchor = body_top
        grid_span = (swing_high - anchor) / 1.13
        origin = anchor + grid_span

        if grid_span < 1e-10:
            continue

        atr_pct = atr.iloc[i] / (close.iloc[i] + 1e-9)
        scale = max(0.03, min(0.20, atr_pct * 2.0))

        max_prox = 0.0
        for lv in fib_levels:
            fib_price = origin - lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_prox:
                max_prox = prox

        result.iloc[i] = max_prox

    return _robust_norm(result)




def compute_fib2_atr_prox(data: pd.DataFrame) -> pd.Series:
    """ATR-adaptive Fib proximity (volatility regime-aware)."""
    return _compute_fib2_atr_prox(data)




def _compute_fib2_trend_bounce(data: pd.DataFrame) -> pd.Series:
    """Direction-aware Fib bounce — only count bounces that align with trend.

    Uptrend: only count bounces off swing-low Fib SUPPORT (wick below, body above).
    Downtrend: only count bounces off swing-high Fib RESISTANCE (wick above, body below).
    No trend: result = 0.
    """
    close = data["close"]
    high = data["high"]
    low = data["low"]
    open_ = data["open"]
    swing_window = max(5, min(30, len(data) // 100))

    trend = _compute_trend(data)

    is_swing_low = _find_swing_lows(low, swing_window)
    swing_low_indices = close.index[is_swing_low].tolist()
    is_swing_high = _find_swing_highs(high, swing_window)
    swing_high_indices = close.index[is_swing_high].tolist()

    result = pd.Series(0.0, index=close.index)

    important_levels = (0.20, 0.382, 0.50, 0.618, 0.886)
    tolerance = 0.025
    lookback = 5

    swing_low_idx = 0
    swing_high_idx = 0

    for i in range(len(close)):
        cur_trend = trend.iloc[i]
        if cur_trend == 0.0:
            continue

        idx = close.index[i]

        if cur_trend > 0:
            # Uptrend: use swing-low support grid
            while swing_low_idx < len(swing_low_indices) - 1 and swing_low_indices[swing_low_idx + 1] <= idx:
                swing_low_idx += 1
            if swing_low_indices[swing_low_idx] > idx:
                continue

            swing_row = data.loc[swing_low_indices[swing_low_idx]]
            body_bottom = min(swing_row["open"], swing_row["close"])
            swing_low_val = swing_row["low"]
            anchor = body_bottom
            grid_span = (anchor - swing_low_val) / 1.13
            origin = anchor - grid_span

            if grid_span < 1e-10 or origin <= 0:
                continue

            swing_i = list(close.index).index(swing_low_indices[swing_low_idx])
            bars_since = i - swing_i
            time_weight = 1.0 / (1.0 + bars_since / 30.0)

            check_start = max(0, i - lookback + 1)
            for j in range(check_start, i + 1):
                cur_open = open_.iloc[j]
                cur_close = close.iloc[j]
                body_top_cur = max(cur_close, cur_open)
                body_bot_cur = min(cur_close, cur_open)

                for lv in important_levels:
                    fib_price = origin + lv * grid_span
                    abs_tol = grid_span * tolerance

                    # Support bounce: low touches Fib, body above
                    if low.iloc[j] <= fib_price + abs_tol and body_bot_cur > fib_price - abs_tol:
                        dist_to_level = abs(low.iloc[j] - fib_price) / (abs_tol + 1e-9)
                        score = (1.0 - dist_to_level) * time_weight
                        result.iloc[i] += score
        else:
            # Downtrend: use swing-high resistance grid
            while swing_high_idx < len(swing_high_indices) - 1 and swing_high_indices[swing_high_idx + 1] <= idx:
                swing_high_idx += 1
            if swing_high_indices[swing_high_idx] > idx:
                continue

            swing_row = data.loc[swing_high_indices[swing_high_idx]]
            body_top = max(swing_row["open"], swing_row["close"])
            swing_high_val = swing_row["high"]
            anchor = body_top
            grid_span = (swing_high_val - anchor) / 1.13
            origin = anchor + grid_span

            if grid_span < 1e-10:
                continue

            swing_i = list(close.index).index(swing_high_indices[swing_high_idx])
            bars_since = i - swing_i
            time_weight = 1.0 / (1.0 + bars_since / 30.0)

            check_start = max(0, i - lookback + 1)
            for j in range(check_start, i + 1):
                cur_open = open_.iloc[j]
                cur_close = close.iloc[j]
                body_top_cur = max(cur_close, cur_open)
                body_bot_cur = min(cur_close, cur_open)

                for lv in important_levels:
                    fib_price = origin - lv * grid_span
                    abs_tol = grid_span * tolerance

                    # Resistance bounce: high touches Fib, body below
                    if high.iloc[j] >= fib_price - abs_tol and body_top_cur < fib_price + abs_tol:
                        dist_to_level = abs(high.iloc[j] - fib_price) / (abs_tol + 1e-9)
                        score = (1.0 - dist_to_level) * time_weight
                        result.iloc[i] -= score  # Negative for bearish

    return _robust_norm(result)




def compute_fib2_trend_bounce(data: pd.DataFrame) -> pd.Series:
    """Direction-aware Fib bounce (trend-aligned only)."""
    return _compute_fib2_trend_bounce(data)




def _compute_fib2_multi_grid(data: pd.DataFrame) -> pd.Series:
    """Weighted multi-swing Fib grid overlap (continuous proximity).

    Uses BOTH swing lows and swing highs, each building independent Fib
    grids. Unlike FIB_RESONANCE (binary hit per grid, low-only), this
    uses continuous proximity from both orientations weighted by
    swing_size and freshness. Including swing-high grids ensures
    decorrelation from FIB_POS which only uses swing-low grids.
    """
    close = data["close"]
    low = data["low"]
    high = data["high"]
    swing_window = max(5, min(30, len(data) // 100))

    all_swing_lows = _find_all_swing_lows(low, swing_window)
    all_swing_highs = _find_all_swing_highs(high, swing_window)

    result = pd.Series(0.0, index=close.index)
    if len(all_swing_lows) + len(all_swing_highs) < 2:
        return result

    fib_levels = (0.382, 0.50, 0.618, 0.886)
    scale = 0.08

    for i in range(len(close)):
        idx = close.index[i]

        # Collect recent swing lows and highs
        recent_lows = [s for s in all_swing_lows if s <= idx][-3:]
        recent_highs = [s for s in all_swing_highs if s <= idx][-3:]

        total_weight = 0.0
        weighted_sum = 0.0

        # Swing-low grids (upward)
        for swing_idx_pos in recent_lows:
            if swing_idx_pos not in data.index:
                continue
            swing_row = data.loc[swing_idx_pos]
            body_bottom = min(swing_row["open"], swing_row["close"])
            swing_low = swing_row["low"]

            anchor = body_bottom
            grid_span = (anchor - swing_low) / 1.13
            origin = anchor - grid_span

            if grid_span < 1e-10 or origin <= 0:
                continue

            swing_size = min(grid_span / (close.iloc[i] + 1e-9), 0.5) / 0.5
            swing_i = list(close.index).index(swing_idx_pos)
            bars_since = i - swing_i
            freshness = 1.0 / (1.0 + bars_since / 20.0)
            weight = swing_size * freshness

            max_prox = 0.0
            for lv in fib_levels:
                fib_price = origin + lv * grid_span
                dist = abs(close.iloc[i] - fib_price) / grid_span
                prox = 1.0 / (1.0 + dist / scale)
                if prox > max_prox:
                    max_prox = prox

            weighted_sum += max_prox * weight
            total_weight += weight

        # Swing-high grids (downward)
        for swing_idx_pos in recent_highs:
            if swing_idx_pos not in data.index:
                continue
            swing_row = data.loc[swing_idx_pos]
            body_top = max(swing_row["open"], swing_row["close"])
            swing_high = swing_row["high"]

            anchor = body_top
            grid_span = (swing_high - anchor) / 1.13
            origin = anchor + grid_span

            if grid_span < 1e-10:
                continue

            swing_size = min(grid_span / (close.iloc[i] + 1e-9), 0.5) / 0.5
            swing_i = list(close.index).index(swing_idx_pos)
            bars_since = i - swing_i
            freshness = 1.0 / (1.0 + bars_since / 20.0)
            weight = swing_size * freshness

            max_prox = 0.0
            for lv in fib_levels:
                fib_price = origin - lv * grid_span
                dist = abs(close.iloc[i] - fib_price) / grid_span
                prox = 1.0 / (1.0 + dist / scale)
                if prox > max_prox:
                    max_prox = prox

            weighted_sum += max_prox * weight
            total_weight += weight

        if total_weight > 1e-9:
            result.iloc[i] = weighted_sum / total_weight

    return _robust_norm(result)




def compute_fib2_multi_grid(data: pd.DataFrame) -> pd.Series:
    """Weighted multi-swing Fib grid overlap (continuous proximity)."""
    return _compute_fib2_multi_grid(data)




def _compute_fib2_breakout_confirm(data: pd.DataFrame) -> pd.Series:
    """Confirmed Fib breakout — price crossed Fib level AND is holding.

    Detects when price crossed a Fib level in the last N bars AND
    current close is still beyond that level (no immediate rejection).
    Unlike FIB_CROSSOVER which captures the moment of crossing (often
    false breakouts), this filters for sustained breaks.
    """
    close = data["close"]
    low = data["low"]
    high = data["high"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    fib_levels = (0.382, 0.50, 0.618, 0.886)
    confirm_window = 3
    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        if i < confirm_window + 1:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        max_score = 0.0
        for lv in fib_levels:
            fib_price = origin + lv * grid_span

            # Look back for cross
            for j in range(max(1, i - confirm_window), i + 1):
                prev_c = close.iloc[j - 1]
                cur_c = close.iloc[j]

                # Bullish breakout: prev below, cur crossed above
                if prev_c <= fib_price and cur_c > fib_price:
                    # Confirm: current bar still above, no rejection
                    if close.iloc[i] > fib_price:
                        lowest_since = low.iloc[j:i + 1].min()
                        if lowest_since > fib_price * 0.995:
                            score = (close.iloc[i] - fib_price) / grid_span
                            if score > max_score:
                                max_score = score

                # Bearish breakout: prev above, cur crossed below
                elif prev_c >= fib_price and cur_c < fib_price:
                    if close.iloc[i] < fib_price:
                        highest_since = high.iloc[j:i + 1].max()
                        if highest_since < fib_price * 1.005:
                            score = -(fib_price - close.iloc[i]) / grid_span
                            if abs(score) > abs(max_score):
                                max_score = score

        result.iloc[i] = max_score

    return _robust_norm(result)




def compute_fib2_breakout_confirm(data: pd.DataFrame) -> pd.Series:
    """Confirmed Fib breakout (crossed AND holding)."""
    return _compute_fib2_breakout_confirm(data)




def _compute_fib2_level_momentum(data: pd.DataFrame) -> pd.Series:
    """Fib proximity rate of change (approach velocity).

    Computes the derivative of Fib proximity: positive = price
    is approaching a Fib level (attractor strengthening), negative
    = price is moving away. Decorrelates from FIB_POS because
    position and velocity are independent dimensions.
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    fib_levels = (0.20, 0.382, 0.50, 0.618, 0.886)
    scale = 0.10
    swing_idx = 0
    prev_prox = 0.0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            prev_prox = 0.0
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            prev_prox = 0.0
            continue

        max_prox = 0.0
        for lv in fib_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_prox:
                max_prox = prox

        result.iloc[i] = max_prox - prev_prox
        prev_prox = max_prox

    return _robust_norm(result)




def compute_fib2_level_momentum(data: pd.DataFrame) -> pd.Series:
    """Fib proximity rate of change (approach velocity)."""
    return _compute_fib2_level_momentum(data)


# --- FIB3: Touch-reaction prediction factors (6 new factors) ---
# These predict WHAT HAPPENS when price touches a Fib level,
# rather than just measuring proximity or velocity.




def _compute_fib3_time_at_level(data: pd.DataFrame) -> pd.Series:
    """Time-at-level ratio — fraction of recent bars spent near a Fib level.

    Price lingering near a Fib level indicates the level is "accepted" by
    the market as a valid support/resistance. Quick touches and bounces
    produce low values; extended consolidation at a level produces high values.

    This is a TIME dimension signal, decorrelated from FIB_POS (SPACE dimension).
    A price can be near a Fib level briefly (high FIB_POS, low TIME_AT_LEVEL)
    or hovering there for many bars (high FIB_POS, high TIME_AT_LEVEL).
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    important_levels = (0.382, 0.50, 0.618, 0.886)
    band_width = 0.03
    lookback = 20

    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        near_count = 0
        check_start = max(0, i - lookback + 1)
        for j in range(check_start, i + 1):
            for lv in important_levels:
                fib_price = origin + lv * grid_span
                dist = abs(close.iloc[j] - fib_price) / grid_span
                if dist < band_width:
                    near_count += 1
                    break

        result.iloc[i] = near_count / (i - check_start + 1)

    return _robust_norm(result)




def compute_fib3_time_at_level(data: pd.DataFrame) -> pd.Series:
    """Time-at-level ratio — fraction of recent bars near a Fib level."""
    return _compute_fib3_time_at_level(data)




def _compute_fib3_acceleration(data: pd.DataFrame) -> pd.Series:
    """Fib proximity acceleration — residual second derivative after removing velocity.

    FIB_POS = position (how close to Fib level)
    FIB2_LEVEL_MOMENTUM = velocity (approaching or receding)
    FIB3_ACCELERATION = residual acceleration (curvature not explained by velocity)

    Raw second derivative (prox[i] - 2*prox[i-1] + prox[i-2]) is correlated
    with velocity because a steady approach produces constant velocity but
    near-zero acceleration. To decorrelate, we compute the residual:
    residual_accel = raw_accel - beta * velocity

    where beta is the regression coefficient of accel on velocity.
    This isolates the "surprise" curvature — acceleration that can't be
    predicted from velocity alone.

    Positive residual = approach is accelerating MORE than expected (magnet strengthening).
    Negative residual = approach is decelerating MORE than expected (magnet weakening).
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    fib_levels = (0.20, 0.382, 0.50, 0.618, 0.886)
    scale = 0.10

    swing_idx = 0
    prev_prox = 0.0
    prev_prev_prox = 0.0

    raw_accels = np.zeros(len(close))
    velocities = np.zeros(len(close))

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            prev_prev_prox = prev_prox
            prev_prox = 0.0
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            prev_prev_prox = prev_prox
            prev_prox = 0.0
            continue

        max_prox = 0.0
        for lv in fib_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_prox:
                max_prox = prox

        raw_accels[i] = max_prox - 2.0 * prev_prox + prev_prev_prox
        velocities[i] = max_prox - prev_prox

        prev_prev_prox = prev_prox
        prev_prox = max_prox

    # Compute residual: remove linear dependence on velocity
    valid = np.abs(velocities) > 1e-9
    if valid.sum() > 10:
        cov = np.cov(raw_accels[valid], velocities[valid])
        var_v = cov[1, 1] + 1e-9
        beta = cov[0, 1] / var_v
        residual = raw_accels - beta * velocities
    else:
        residual = raw_accels

    return _robust_norm(pd.Series(residual, index=close.index))




def compute_fib3_acceleration(data: pd.DataFrame) -> pd.Series:
    """Fib proximity acceleration (second derivative of proximity)."""
    return _compute_fib3_acceleration(data)




def _compute_fib3_volume_profile(data: pd.DataFrame) -> pd.Series:
    """Volume excess at Fib levels — volume concentration vs time concentration.

    Measures whether Fib levels attract DISPROPORTIONATELY more volume
    than would be expected from the time spent near them.

    vol_excess = (vol_at_fib / total_vol) - (bars_at_fib / total_bars)

    Positive = Fib levels attract more volume than their time share
    (institutional interest at these levels).
    Negative = Fib levels have less volume than expected
    (levels are being ignored by the market).
    Zero = volume is distributed proportionally to time.

    This decorrelates from FIB3_TIME_AT_LEVEL by measuring the EXCESS
    volume, not just the volume fraction. A level can have high time
    but low excess (price lingers but no one trades there) or low time
    but high excess (price touches briefly but huge volume fires).
    """
    close = data["close"]
    low = data["low"]
    volume = data["volume"] if "volume" in data.columns else None
    swing_window = max(5, min(30, len(data) // 100))

    if volume is None:
        return pd.Series(0.0, index=close.index)

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    important_levels = (0.382, 0.50, 0.618, 0.886)
    band_width = 0.03
    lookback = 20

    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        vol_at_fib = 0.0
        total_vol = 0.0
        bars_at_fib = 0
        total_bars = 0
        check_start = max(0, i - lookback + 1)

        for j in range(check_start, i + 1):
            bar_vol = volume.iloc[j]
            total_vol += bar_vol
            total_bars += 1
            for lv in important_levels:
                fib_price = origin + lv * grid_span
                dist = abs(close.iloc[j] - fib_price) / grid_span
                if dist < band_width:
                    vol_at_fib += bar_vol
                    bars_at_fib += 1
                    break

        # Excess: volume share minus time share
        if total_vol > 1e-9 and total_bars > 0:
            vol_share = vol_at_fib / total_vol
            time_share = bars_at_fib / total_bars
            result.iloc[i] = vol_share - time_share

    return _robust_norm(result)




def compute_fib3_volume_profile(data: pd.DataFrame) -> pd.Series:
    """Volume concentration at Fib levels (volume-weighted proximity)."""
    return _compute_fib3_volume_profile(data)




def _compute_fib3_reaction_regime(data: pd.DataFrame) -> pd.Series:
    """Historical reaction probability at current Fib level.

    Tracks what happened the last N times price touched a Fib level:
    - Did it bounce (reverse)?
    - Did it break through (continue)?
    - Did it slice through (no reaction)?

    Returns the bounce probability: high value = this Fib level has
    historically been a strong reaction point (bounces more often than breaks).
    Low value = this level tends to be sliced through.

    This is a STATISTICAL dimension — it measures the historical behavior
    at Fib levels, not the current price position. A level can be far away
    but have a high reaction rate (strong magnet), or nearby but with low
    reaction rate (weak level).
    """
    close = data["close"]
    high = data["high"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    important_levels = (0.382, 0.50, 0.618, 0.886)
    touch_band = 0.025
    reaction_window = 3
    history_window = 50

    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        # Find nearest Fib level to current price
        min_dist = float("inf")
        nearest_fib_price = close.iloc[i]
        for lv in important_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            if dist < min_dist:
                min_dist = dist
                nearest_fib_price = fib_price

        # Scan history for touches of this Fib level
        bounces = 0
        breaks = 0
        search_start = max(0, i - history_window)

        for j in range(search_start, i):
            abs_tol = grid_span * touch_band
            touched = (low.iloc[j] <= nearest_fib_price + abs_tol and
                       high.iloc[j] >= nearest_fib_price - abs_tol)

            if not touched:
                continue

            # Check reaction in next bars
            if j + reaction_window >= len(close):
                continue

            # Was price above or below the level when it touched?
            was_above = close.iloc[j] > nearest_fib_price

            # Check where price went after
            future_close = close.iloc[j + reaction_window]
            if was_above:
                # Price was above, touched from above
                if future_close > nearest_fib_price:
                    bounces += 1  # Stayed above = bounce (support held)
                else:
                    breaks += 1  # Went below = break (support failed)
            else:
                # Price was below, touched from below
                if future_close < nearest_fib_price:
                    bounces += 1  # Stayed below = bounce (resistance held)
                else:
                    breaks += 1  # Went above = break (resistance failed)

        total = bounces + breaks
        if total >= 2:
            result.iloc[i] = bounces / total

    return _robust_norm(result)




def compute_fib3_reaction_regime(data: pd.DataFrame) -> pd.Series:
    """Historical reaction probability at current Fib level (bounce rate)."""
    return _compute_fib3_reaction_regime(data)




def _compute_fib3_magnet_strength(data: pd.DataFrame) -> pd.Series:
    """Fib level gravitational convergence — price trajectory curvature toward level.

    Measures whether price is being "bent" toward a Fib level by computing
    the angular change in price trajectory. Unlike velocity (FIB2_LEVEL_MOMENTUM)
    which measures linear approach speed, this measures CURVATURE — whether
    the price path is curving toward the level (converging) or curving away
    (diverging) or moving in a straight line (no magnet effect).

    Computed as: cross product of (price direction) × (direction to nearest Fib)
    - Positive = price is curving TOWARD the Fib level (magnet active)
    - Negative = price is curving AWAY from the Fib level (magnet repelling)
    - Zero = price moving in a straight line (no magnet effect)

    This is fundamentally different from FIB2_LEVEL_MOMENTUM (velocity):
    - Velocity = how fast approaching (1D scalar)
    - Curvature = whether trajectory is bending toward level (2D geometric)

    A price can be approaching fast but in a straight line (high velocity, zero
    curvature) or approaching slowly but with strong curvature (low velocity,
    high curvature = magnet pulling).
    """
    close = data["close"]
    low = data["low"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    fib_levels = (0.20, 0.382, 0.50, 0.618, 0.886)

    swing_idx = 0

    for i in range(len(close)):
        if i < 2:
            continue

        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        # Find nearest Fib level
        min_dist = float("inf")
        nearest_fib_price = close.iloc[i]
        for lv in fib_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            if dist < min_dist:
                min_dist = dist
                nearest_fib_price = fib_price

        # Price velocity vector (2-bar)
        price_dx = close.iloc[i] - close.iloc[i - 2]

        # Direction to nearest Fib level
        to_fib_dx = nearest_fib_price - close.iloc[i]

        # Cross product (2D): measures curvature toward Fib level
        # Positive = price moving in same direction as Fib level (converging)
        # Negative = price moving opposite to Fib level (diverging)
        # Scale by distance so far levels have weaker signal
        if min_dist > 0.01:
            curvature = (price_dx * to_fib_dx) / (grid_span * grid_span + 1e-9)
            # Normalize by distance: close levels with convergence = strong magnet
            result.iloc[i] = curvature / (min_dist + 0.1)
        else:
            # Very close to level — check if price is being held there
            # Small oscillation around level = strong magnet
            oscillation = abs(close.iloc[i] - close.iloc[i - 1]) / (grid_span + 1e-9)
            if oscillation < 0.02:
                result.iloc[i] = 1.0  # Held at level = strong magnet
            else:
                result.iloc[i] = 0.0

    return _robust_norm(result)




def compute_fib3_magnet_strength(data: pd.DataFrame) -> pd.Series:
    """Fib level magnet strength (approach speed / distance ratio)."""
    return _compute_fib3_magnet_strength(data)




def _compute_fib3_rejection_ratio(data: pd.DataFrame) -> pd.Series:
    """Post-touch rejection quality — residual wick rejection after removing proximity effect.

    When price touches a Fib level, the subsequent candle's shape reveals
    the market's reaction quality:
    - Long wick away from level + small body = strong rejection (bounce)
    - Small wick + large body through level = weak rejection (breakout)

    To decorrelate from FIB_POS (proximity), this computes the RESIDUAL
    rejection quality: actual rejection minus expected rejection based on
    proximity. Near a Fib level, some rejection is expected just from
    random mean-reversion. The residual captures ABOVE-EXPECTED rejection,
    which indicates genuine market structure reaction.

    High value = stronger rejection than expected (genuine Fib reaction).
    Low value = weaker rejection than expected (level being sliced through).
    """
    close = data["close"]
    high = data["high"]
    low = data["low"]
    open_ = data["open"]
    swing_window = max(5, min(30, len(data) // 100))

    is_swing = _find_swing_lows(low, swing_window)
    swing_indices = close.index[is_swing].tolist()

    result = pd.Series(0.0, index=close.index)
    if len(swing_indices) == 0:
        return result

    important_levels = (0.382, 0.50, 0.618, 0.886)
    touch_band = 0.025
    lookback = 5
    scale = 0.10

    swing_idx = 0

    for i in range(len(close)):
        idx = close.index[i]
        while swing_idx < len(swing_indices) - 1 and swing_indices[swing_idx + 1] <= idx:
            swing_idx += 1
        if swing_indices[swing_idx] > idx:
            continue

        swing_row = data.loc[swing_indices[swing_idx]]
        body_bottom = min(swing_row["open"], swing_row["close"])
        swing_low = swing_row["low"]

        anchor = body_bottom
        grid_span = (anchor - swing_low) / 1.13
        origin = anchor - grid_span

        if grid_span < 1e-10 or origin <= 0:
            continue

        # Compute proximity to nearest Fib level
        max_prox = 0.0
        for lv in important_levels:
            fib_price = origin + lv * grid_span
            dist = abs(close.iloc[i] - fib_price) / grid_span
            prox = 1.0 / (1.0 + dist / scale)
            if prox > max_prox:
                max_prox = prox

        # Check if any recent bar touched a Fib level
        best_rejection = 0.0
        check_start = max(0, i - lookback + 1)

        for j in range(check_start, i + 1):
            abs_tol = grid_span * touch_band
            for lv in important_levels:
                fib_price = origin + lv * grid_span

                touched_above = (high.iloc[j] >= fib_price - abs_tol and
                                 close.iloc[j] < fib_price)
                touched_below = (low.iloc[j] <= fib_price + abs_tol and
                                 close.iloc[j] > fib_price)

                if not (touched_above or touched_below):
                    continue

                bar_range = high.iloc[j] - low.iloc[j] + 1e-9

                if touched_above:
                    upper_wick = high.iloc[j] - max(open_.iloc[j], close.iloc[j])
                    rejection = upper_wick / bar_range
                else:
                    lower_wick = min(open_.iloc[j], close.iloc[j]) - low.iloc[j]
                    rejection = lower_wick / bar_range

                if rejection > best_rejection:
                    best_rejection = rejection

        # Residual: actual rejection minus expected (proximity-based)
        # Near a level, some rejection is expected from mean-reversion
        # The residual captures genuine Fib structure reaction
        expected_rejection = max_prox * 0.5
        result.iloc[i] = best_rejection - expected_rejection

    return _robust_norm(result)




def compute_fib3_rejection_ratio(data: pd.DataFrame) -> pd.Series:
    """Post-touch rejection ratio (wick-to-body after Fib touch)."""
    return _compute_fib3_rejection_ratio(data)


# --- Derivatives factors (6) ---
# These require merged DataFrame with DERIV_ columns from derivatives.db.
# Columns: DERIV_funding_rate, DERIV_open_interest




def compute_deriv_funding_rate(data: pd.DataFrame) -> pd.Series:
    """Current funding rate — extreme values signal crowded positioning.

    Positive funding = longs pay shorts (bullish crowded).
    Negative funding = shorts pay longs (bearish crowded).
    Extreme absolute values often precede reversals.
    """
    col = _deriv_col(data, "funding_rate")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(data[col].fillna(0.0))




def compute_deriv_funding_z(data: pd.DataFrame) -> pd.Series:
    """Funding rate z-score — deviation from rolling mean.

    Captures when funding is abnormally high/low vs recent history.
    More robust than raw rate for cross-asset comparison.
    """
    col = _deriv_col(data, "funding_rate")
    if col is None:
        return pd.Series(0.0, index=data.index)
    rate = data[col].fillna(0.0)
    window = 21  # ~7 days at 8h intervals
    rolling_mean = rate.rolling(window=window, min_periods=5).mean()
    rolling_std = rate.rolling(window=window, min_periods=5).std() + 1e-9
    z = (rate - rolling_mean) / rolling_std
    return _robust_norm(z.fillna(0.0).clip(-5, 5))




def compute_deriv_liq_imbalance(data: pd.DataFrame) -> pd.Series:
    """Liquidation imbalance — long vs short liquidation ratio.

    One-sided liquidation often precedes reversal:
    - High long liquidations → longs getting washed out → potential bottom
    - High short liquidations → shorts getting squeezed → potential top
    Requires DERIV_liq_long and DERIV_liq_short columns.
    """
    long_col = _deriv_col(data, "liq_long")
    short_col = _deriv_col(data, "liq_short")
    if long_col is None or short_col is None:
        return pd.Series(0.0, index=data.index)
    long_liq = data[long_col].fillna(0.0)
    short_liq = data[short_col].fillna(0.0)
    total = long_liq + short_liq + 1e-9
    imbalance = (short_liq - long_liq) / total
    return _robust_norm(imbalance)




def compute_deriv_liq_spike(data: pd.DataFrame) -> pd.Series:
    """Liquidation volume surge — spike detection like FOMO for liquidations.

    Sudden spike in liquidations signals forced deleveraging,
    often marking capitulation points. Similar logic to FOMO
    factor but applied to liquidation data.
    """
    total_col = _deriv_col(data, "liq_total")
    if total_col is None:
        return pd.Series(0.0, index=data.index)
    total_liq = data[total_col].fillna(0.0)
    prev = total_liq.shift(1)
    chg = (total_liq - prev) / (prev + 1.0)
    acc = chg - chg.shift(1)
    return _robust_norm(acc.clip(-5.0, 5.0))




def compute_deriv_oi_change(data: pd.DataFrame) -> pd.Series:
    """Open interest change rate — OI growth/decline signal.

    OI + price rising together = new money entering longs = real breakout.
    OI + price falling = new money entering shorts = real breakdown.
    OI declining = positions closing = move may be exhausting.
    """
    col = _deriv_col(data, "open_interest")
    if col is None:
        return pd.Series(0.0, index=data.index)
    oi = data[col].fillna(0.0)
    prev = oi.shift(1)
    change = (oi - prev) / (prev + 1e-9)
    return _robust_norm(change.fillna(0.0).clip(-5, 5))




def compute_deriv_oi_price_div(data: pd.DataFrame) -> pd.Series:
    """OI-price divergence — detects real vs fake breakouts.

    Computes correlation between OI change and price change over
    rolling window. Positive = OI and price aligned (real move).
    Negative = diverging (potential reversal).
    """
    oi_col = _deriv_col(data, "open_interest")
    if oi_col is None:
        return pd.Series(0.0, index=data.index)
    oi = data[oi_col].fillna(0.0)
    close = data["close"]

    oi_chg = oi.pct_change().fillna(0.0).clip(-5, 5)
    price_chg = close.pct_change().fillna(0.0).clip(-5, 5)

    window = 10
    corr = oi_chg.rolling(window=window, min_periods=5).corr(price_chg)
    return _robust_norm(corr.fillna(0.0).clip(-1, 1))




def compute_deriv_oi_z(data: pd.DataFrame) -> pd.Series:
    """Open interest z-score — abnormal OI vs recent history.

    High z = OI surging (new positions opening, potential squeeze fuel).
    Low z = OI declining (positions closing, move exhausting).
    """
    col = _deriv_col(data, "open_interest")
    if col is None:
        return pd.Series(0.0, index=data.index)
    oi = data[col].fillna(0.0)
    window = 21
    rolling_mean = oi.rolling(window=window, min_periods=5).mean()
    rolling_std = oi.rolling(window=window, min_periods=5).std() + 1e-9
    z = (oi - rolling_mean) / rolling_std
    return _robust_norm(z.fillna(0.0).clip(-5, 5))




def compute_deriv_oi_funding_div(data: pd.DataFrame) -> pd.Series:
    """OI-funding divergence — smart money positioning signal.

    OI rising + funding negative = shorts entering aggressively (bearish conviction).
    OI rising + funding positive = longs entering aggressively (bullish conviction).
    OI falling + funding extreme = positions being forced out (squeeze ending).
    """
    oi_col = _deriv_col(data, "open_interest")
    fund_col = _deriv_col(data, "funding_rate")
    if oi_col is None or fund_col is None:
        return pd.Series(0.0, index=data.index)
    oi = data[oi_col].fillna(0.0)
    funding = data[fund_col].fillna(0.0)
    oi_chg = oi.pct_change().fillna(0.0).clip(-5, 5)
    oi_sign = np.sign(oi_chg)
    div = oi_sign * funding
    return _robust_norm(div.fillna(0.0).clip(-5, 5))




def compute_deriv_oi_vol_ratio(data: pd.DataFrame) -> pd.Series:
    """OI-to-volume ratio — leverage / commitment ratio.

    High OI/vol = high leverage, overcommitted market (squeeze risk).
    Low OI/vol = low leverage, fresh positioning room.
    Uses rolling normalization to handle scale changes.
    """
    oi_col = _deriv_col(data, "open_interest")
    if oi_col is None:
        return pd.Series(0.0, index=data.index)
    oi = data[oi_col].fillna(0.0)
    vol = data["volume"].fillna(0.0) + 1e-9
    ratio = oi / vol
    return _robust_norm(ratio.fillna(0.0).clip(0, 100))




def compute_deriv_liq_pressure_proxy(data: pd.DataFrame) -> pd.Series:
    """Liquidation pressure proxy — estimated from OI drop + funding direction.

    When OI drops AND funding is positive → longs being liquidated (contrarian buy).
    When OI drops AND funding is negative → shorts being liquidated (contrarian sell).
    Returns positive when longs are being squeezed out (potential bottom).
    """
    oi_col = _deriv_col(data, "open_interest")
    fund_col = _deriv_col(data, "funding_rate")
    if oi_col is None or fund_col is None:
        return pd.Series(0.0, index=data.index)
    oi = data[oi_col].fillna(0.0)
    funding = data[fund_col].fillna(0.0)
    oi_pct = oi.pct_change().fillna(0.0).clip(-5, 5)
    # Negative OI change * sign of funding = liquidation pressure
    pressure = -oi_pct * np.sign(funding)
    return _robust_norm(pressure.fillna(0.0).clip(-5, 5))




def compute_deriv_liq_spike_proxy(data: pd.DataFrame) -> pd.Series:
    """Liquidation spike proxy — triple extreme: funding + taker + OI drop.

    When funding is extreme AND taker ratio is extreme AND OI is dropping,
    a liquidation cascade is likely happening. This is the capitulation signal.
    """
    oi_col = _deriv_col(data, "open_interest")
    fund_col = _deriv_col(data, "funding_rate")
    taker_col = _of_col(data, "taker_ratio")
    if oi_col is None or fund_col is None or taker_col is None:
        return pd.Series(0.0, index=data.index)
    oi = data[oi_col].fillna(0.0)
    funding = data[fund_col].fillna(0.0)
    taker = data[taker_col].fillna(1.0)

    # Z-scores
    fund_z = (funding - funding.rolling(21, min_periods=5).mean()) / (funding.rolling(21, min_periods=5).std() + 1e-9)
    taker_z = (taker - taker.rolling(21, min_periods=5).mean()) / (taker.rolling(21, min_periods=5).std() + 1e-9)
    oi_chg = oi.pct_change().fillna(0.0).clip(-5, 5)

    # Triple extreme: abs(funding_z) * abs(taker_z) * -oi_change
    spike = np.abs(fund_z.fillna(0.0)) * np.abs(taker_z.fillna(0.0)) * (-oi_chg)
    return _robust_norm(spike.fillna(0.0).clip(-5, 5))




def compute_deriv_liq_magnet_proxy(data: pd.DataFrame) -> pd.Series:
    """Liquidation magnet proxy — funding extreme × taker spike.

    When funding is at extreme levels AND taker ratio spikes,
    the market is at a liquidation magnet — price gets pulled toward
    liquidation clusters. This is a more refined version of
    OF_LIQUIDATION_MAGNET that uses z-scores for normalization.
    """
    fund_col = _deriv_col(data, "funding_rate")
    taker_col = _of_col(data, "taker_ratio")
    if fund_col is None or taker_col is None:
        return pd.Series(0.0, index=data.index)
    funding = data[fund_col].fillna(0.0)
    taker = data[taker_col].fillna(1.0)

    fund_z = (funding - funding.rolling(21, min_periods=5).mean()) / (funding.rolling(21, min_periods=5).std() + 1e-9)
    taker_z = (taker - taker.rolling(21, min_periods=5).mean()) / (taker.rolling(21, min_periods=5).std() + 1e-9)

    magnet = fund_z.fillna(0.0) * taker_z.fillna(0.0)
    return _robust_norm(magnet.fillna(0.0).clip(-5, 5))




def compute_deriv_basis(data: pd.DataFrame) -> pd.Series:
    """Spot-futures basis rate — contango vs backwardation.

    Positive basis (contango) = futures above spot → longs paying premium.
    Negative basis (backwardation) = futures below spot → shorts paying premium.
    Extreme backwardation often precedes rallies (shorts covering).
    """
    col = _deriv_col(data, "basis_rate")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(data[col].fillna(0.0))




def compute_deriv_basis_extreme(data: pd.DataFrame) -> pd.Series:
    """Basis rate z-score — extreme contango/backwardation signals.

    Extreme backwardation (very negative z) = market heavily short, potential squeeze.
    Extreme contango (very positive z) = market heavily long, potential dump.
    """
    col = _deriv_col(data, "basis_rate")
    if col is None:
        return pd.Series(0.0, index=data.index)
    basis = data[col].fillna(0.0)
    window = 21
    rolling_mean = basis.rolling(window=window, min_periods=5).mean()
    rolling_std = basis.rolling(window=window, min_periods=5).std() + 1e-9
    z = (basis - rolling_mean) / rolling_std
    return _robust_norm(z.fillna(0.0).clip(-5, 5))


# ---------------------------------------------------------------------------
# Orderflow / Microstructure Factors
# ---------------------------------------------------------------------------




def compute_of_taker_ratio(data: pd.DataFrame) -> pd.Series:
    """Taker buy/sell ratio — aggressive buyer pressure."""
    col = _of_col(data, "taker_ratio")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(np.log(data[col].fillna(1.0) + 1e-9))




def compute_of_taker_spike(data: pd.DataFrame) -> pd.Series:
    """Taker ratio z-score — spike in aggressive buying/selling."""
    col = _of_col(data, "taker_ratio")
    if col is None:
        return pd.Series(0.0, index=data.index)
    ratio = data[col].fillna(1.0)
    window = 21
    rolling_mean = ratio.rolling(window=window, min_periods=5).mean()
    rolling_std = ratio.rolling(window=window, min_periods=5).std() + 1e-9
    z = (ratio - rolling_mean) / rolling_std
    return _robust_norm(z.fillna(0.0).clip(-5, 5))




def compute_of_top_pos_ratio(data: pd.DataFrame) -> pd.Series:
    """Top trader long/short position ratio — smart money positioning."""
    col = _of_col(data, "top_pos_ratio")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(np.log(data[col].fillna(1.0) + 1e-9))




def compute_of_top_pos_change(data: pd.DataFrame) -> pd.Series:
    """Change in top trader position ratio — smart money shifting."""
    col = _of_col(data, "top_pos_ratio")
    if col is None:
        return pd.Series(0.0, index=data.index)
    ratio = data[col].fillna(1.0)
    change = ratio.pct_change(1)
    return _robust_norm(change.fillna(0.0).clip(-5, 5))




def compute_of_top_acc_ratio(data: pd.DataFrame) -> pd.Series:
    """Top trader long/short account ratio — broader smart money view."""
    col = _of_col(data, "top_acc_ratio")
    if col is None:
        return pd.Series(0.0, index=data.index)
    return _robust_norm(np.log(data[col].fillna(1.0) + 1e-9))




def compute_of_funding_impulse(data: pd.DataFrame) -> pd.Series:
    """Funding rate * taker ratio interaction — contrarian signal."""
    fr_col = _deriv_col(data, "funding_rate")
    tr_col = _of_col(data, "taker_ratio")
    if fr_col is None or tr_col is None:
        return pd.Series(0.0, index=data.index)
    fr = data[fr_col].fillna(0.0)
    tr = np.log(data[tr_col].fillna(1.0) + 1e-9)
    impulse = fr * tr
    return _robust_norm(impulse.fillna(0.0).clip(-5, 5))




def compute_of_liquidation_magnet(data: pd.DataFrame) -> pd.Series:
    """High funding + extreme taker ratio → liquidation magnet."""
    fr_col = _deriv_col(data, "funding_rate")
    tr_col = _of_col(data, "taker_ratio")
    if fr_col is None or tr_col is None:
        return pd.Series(0.0, index=data.index)
    fr = data[fr_col].fillna(0.0)
    tr = data[tr_col].fillna(1.0)
    magnet = np.abs(fr) * np.abs(np.log(tr + 1e-9))
    return _robust_norm(magnet.fillna(0.0).clip(-5, 5))




def compute_of_crowd_divergence(data: pd.DataFrame) -> pd.Series:
    """Crowd (taker) vs smart money (top position) divergence."""
    tr_col = _of_col(data, "taker_ratio")
    tp_col = _of_col(data, "top_pos_ratio")
    if tr_col is None or tp_col is None:
        return pd.Series(0.0, index=data.index)
    crowd = np.log(data[tr_col].fillna(1.0) + 1e-9)
    smart = np.log(data[tp_col].fillna(1.0) + 1e-9)
    divergence = crowd - smart
    return _robust_norm(divergence.fillna(0.0).clip(-5, 5))


# --- v13: Global LS Ratio (retail sentiment) ---




_CONFLUENCE_CONFIGS: tuple[tuple[float, float, float, float], ...] = (
    # (big_fib, sub_lower, sub_upper, sub_fib) — price = big_fib level
    (0.618, 0.50, 1.0, 0.236),   # 0.618 = 0.5 + 0.236*0.5
    (0.618, 0.382, 1.0, 0.382),  # 0.618 = 0.382 + 0.382*0.618
    (0.886, 0.618, 1.0, 0.700),  # 0.886 = 0.618 + 0.700*0.382
)

# Confluence prices for each swing: big_fib_price == sub_lower_price + sub_fib * (anchor - sub_lower_price)
# This is mathematically guaranteed by the definitions above.





# ---------------------------------------------------------------------------
# Factor metadata for registration into ALL_FACTORS
# ---------------------------------------------------------------------------

FIB_FACTORS: dict[str, tuple[object, str]] = {
    "fib_pos": (compute_fib_pos, "Proximity to nearest Fibonacci level from swing low"),
    "fib_confluence": (compute_fib_confluence, "Confluence-specific Fib signal (residual)"),
    "fib_density": (compute_fib_density, "Density of Fib levels near current price"),
    "fib_bounce": (compute_fib_bounce, "Fib bounce - wick rejection with time decay"),
    "fib_strength": (compute_fib_strength, "Fib proximity weighted by swing significance"),
    "fib_momentum": (compute_fib_momentum, "Fib direction momentum within grid"),
    "fib_resonance": (compute_fib_resonance, "Multi-anchor Fib resonance across grids"),
    "fib_crossover": (compute_fib_crossover, "Fib level crossover directional signal"),
    "fib_decay_pos": (compute_fib_decay_pos, "Age-decayed Fib proximity with level weights"),
}

FIB2_FACTORS: dict[str, tuple[object, str]] = {
    "fib2_high_density": (compute_fib2_high_density, "Fib density from swing HIGH anchors"),
    "fib2_atr_prox": (compute_fib2_atr_prox, "ATR-adaptive Fib proximity from swing highs"),
    "fib2_trend_bounce": (compute_fib2_trend_bounce, "Direction-aware Fib bounce"),
    "fib2_multi_grid": (compute_fib2_multi_grid, "Multi-grid Fib overlap"),
    "fib2_breakout_confirm": (compute_fib2_breakout_confirm, "Fib breakout confirmation"),
    "fib2_level_momentum": (compute_fib2_level_momentum, "Fib level velocity (approach/recede)"),
}

FIB3_FACTORS: dict[str, tuple[object, str]] = {
    "fib3_time_at_level": (compute_fib3_time_at_level, "Time dimension at Fib level"),
    "fib3_acceleration": (compute_fib3_acceleration, "Fib residual acceleration"),
    "fib3_volume_profile": (compute_fib3_volume_profile, "Volume profile at Fib level"),
    "fib3_reaction_regime": (compute_fib3_reaction_regime, "Fib reaction regime"),
    "fib3_magnet_strength": (compute_fib3_magnet_strength, "Fib magnet strength"),
    "fib3_rejection_ratio": (compute_fib3_rejection_ratio, "Fib rejection ratio"),
}

OF_ADVANCED_FACTORS: dict[str, tuple[object, str]] = {
    "of_taker_ratio": (compute_of_taker_ratio, "Taker buy/sell ratio"),
    "of_taker_spike": (compute_of_taker_spike, "Taker ratio spike"),
    "of_top_pos_ratio": (compute_of_top_pos_ratio, "Top trader position ratio"),
    "of_top_pos_change": (compute_of_top_pos_change, "Top position ratio change"),
    "of_top_acc_ratio": (compute_of_top_acc_ratio, "Top account ratio (smart money)"),
    "of_funding_impulse": (compute_of_funding_impulse, "Funding rate impulse"),
    "of_liquidation_magnet": (compute_of_liquidation_magnet, "Liquidation magnet"),
    "of_crowd_divergence": (compute_of_crowd_divergence, "Crowd divergence"),
}

DERIV_ADVANCED_FACTORS: dict[str, tuple[object, str]] = {
    "deriv_funding_rate": (compute_deriv_funding_rate, "Current funding rate (crowded positioning)"),
    "deriv_funding_z": (compute_deriv_funding_z, "Funding rate z-score"),
    "deriv_liq_imbalance": (compute_deriv_liq_imbalance, "Liquidation long/short imbalance"),
    "deriv_liq_spike": (compute_deriv_liq_spike, "Liquidation spike"),
    "deriv_oi_change": (compute_deriv_oi_change, "Open interest pct change"),
    "deriv_oi_price_div": (compute_deriv_oi_price_div, "OI vs price divergence"),
    "deriv_oi_z": (compute_deriv_oi_z, "OI z-score"),
    "deriv_oi_funding_div": (compute_deriv_oi_funding_div, "OI vs funding divergence"),
    "deriv_oi_vol_ratio": (compute_deriv_oi_vol_ratio, "OI vs volume ratio"),
    "deriv_liq_pressure_proxy": (compute_deriv_liq_pressure_proxy, "Liquidation pressure proxy (v15)"),
    "deriv_liq_spike_proxy": (compute_deriv_liq_spike_proxy, "Liquidation spike proxy (v15)"),
    "deriv_liq_magnet_proxy": (compute_deriv_liq_magnet_proxy, "Liquidation magnet proxy (v15)"),
    "deriv_basis": (compute_deriv_basis, "Basis rate (contango/backwardation, v15)"),
    "deriv_basis_extreme": (compute_deriv_basis_extreme, "Basis rate z-score (v15)"),
}

RESEARCH_FACTORS: dict[str, tuple[object, str]] = {
    **FIB_FACTORS,
    **FIB2_FACTORS,
    **FIB3_FACTORS,
    **OF_ADVANCED_FACTORS,
    **DERIV_ADVANCED_FACTORS,
}
