"""
Limit-up sentiment factors for A-share stocks.

Unique A-share alpha sources from 同花顺 limit-up data:
- LIMIT_UP_BREADTH: 涨停广度 (涨停数/全市场股票数)
- SEAL_RATE: 封板率 (封住涨停的比例)
- OPEN_COUNT: 炸板数 (打开涨停的次数)
- CONSECUTIVE_BOARDS: 连板高度 (最高连板数)
- LIMIT_UP_RATIO: 涨跌停比 (涨停数/跌停数)
- REASON_CLUSTER: 涨停原因聚类 (最热门概念)

Requires [cn] optional dependencies (via ths_data).
"""

import numpy as np
import pandas as pd


def calc_limit_up_breadth(
    stats: dict,
    total_stocks: int = 5000,
) -> float:
    """Limit-up breadth: fraction of stocks hitting limit-up."""
    lu_today = stats.get("limit_up_today", 0)
    if total_stocks <= 0:
        return np.nan
    return lu_today / total_stocks


def calc_seal_rate(stats: dict) -> float:
    """Seal rate: fraction of limit-ups that stayed sealed."""
    return stats.get("seal_rate", np.nan)


def calc_limit_up_ratio(stats: dict) -> float:
    """Limit-up / limit-down ratio. >1 = bullish bias."""
    lu = stats.get("limit_up_today", 0)
    ld = stats.get("limit_down_today", 0)
    if ld <= 0:
        return lu if lu > 0 else np.nan
    return lu / ld


def calc_max_consecutive_boards(
    cont_df: pd.DataFrame,
) -> int:
    """Max consecutive boards across all stocks."""
    if cont_df.empty or "consecutive_boards" not in cont_df.columns:
        return 0
    return int(cont_df["consecutive_boards"].max())


def calc_reason_cluster(
    pool_df: pd.DataFrame,
    top_n: int = 3,
) -> list[tuple[str, int]]:
    """Most common limit-up reasons (concept clusters)."""
    if pool_df.empty or "reason_type" not in pool_df.columns:
        return []

    reasons = []
    for r in pool_df["reason_type"].dropna():
        for part in str(r).split("+"):
            part = part.strip()
            if part:
                reasons.append(part)

    if not reasons:
        return []

    counts = pd.Series(reasons).value_counts()
    return [(r, int(c)) for r, c in counts.head(top_n).items()]


def build_sentiment_panel(
    start_date: str = "20240101",
    end_date: str = "20250705",
) -> pd.DataFrame:
    """Build daily sentiment factor panel from limit-up data.

    Returns DataFrame with [date, limit_up_breadth, seal_rate,
    open_count, limit_up_ratio, max_boards, limit_up_count, limit_down_count]
    """
    import time
    from datetime import datetime, timedelta

    from selene_space_ashare.ths_data import (
        fetch_limit_up_stats,
        fetch_continuous_limit_up,
    )

    start = datetime.strptime(start_date, "%Y%m%d")
    end = datetime.strptime(end_date, "%Y%m%d")

    rows = []
    current = start
    while current <= end:
        date_str = current.strftime("%Y%m%d")

        stats = fetch_limit_up_stats(date_str)
        if not stats:
            current += timedelta(days=1)
            continue

        cont_df = fetch_continuous_limit_up()

        row = {
            "date": date_str,
            "limit_up_breadth": calc_limit_up_breadth(stats),
            "seal_rate": calc_seal_rate(stats),
            "open_count": stats.get("open_count", 0),
            "limit_up_ratio": calc_limit_up_ratio(stats),
            "max_boards": calc_max_consecutive_boards(cont_df),
            "limit_up_count": stats.get("limit_up_today", 0),
            "limit_down_count": stats.get("limit_down_today", 0),
        }
        rows.append(row)

        current += timedelta(days=1)
        if current.weekday() >= 5:
            current += timedelta(days=2)

        time.sleep(0.3)

    return pd.DataFrame(rows)


def calc_stock_limit_up_factor(
    code: str,
    pool_df: pd.DataFrame,
) -> dict:
    """Calculate limit-up related factors for a single stock.

    Returns dict with:
    - is_limit_up: 1 if stock is in limit-up pool
    - seal_strength: seal_amount / float_market_cap (if limit-up)
    - open_count: number of times limit-up was broken
    - consecutive_boards: from continuous limit-up data
    """
    stock = pool_df[pool_df["code"] == code]
    if stock.empty:
        return {
            "is_limit_up": 0,
            "seal_strength": np.nan,
            "open_count": np.nan,
            "consecutive_boards": 0,
        }

    r = stock.iloc[0]
    seal_amount = r.get("seal_amount", np.nan)
    float_cap = r.get("float_market_cap", np.nan)
    seal_strength = seal_amount / float_cap if pd.notna(seal_amount) and pd.notna(float_cap) and float_cap > 0 else np.nan

    return {
        "is_limit_up": 1,
        "seal_strength": seal_strength,
        "open_count": r.get("open_count", 0) or 0,
        "consecutive_boards": 0,
    }
