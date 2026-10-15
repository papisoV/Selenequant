"""A-share style monitoring report service (batch5).

In-process cache + lazy build. The 300-symbol factor panel takes ~60s to
build, so it must never run inside the request path synchronously — all
computation goes through asyncio.to_thread. Cache is invalidated only by
POST /style-report/refresh or process restart (no TTL: a TTL-expired first
request would eat the full rebuild). Empty-data results are negatively
cached so repeated GETs don't re-run a doomed 60s build.

Note: _build intentionally does NOT call style_monitor.full_style_report —
that helper recomputes style_spread_by_day internally but only returns the
tail-10 slice; calling it here and then re-deriving full series would run
each style's daily spread twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import pandas as pd

from selene_space_ashare.factor_eval import orthogonalize_greedy
from selene_space_ashare.factor_report import build_factor_panel
from selene_space_ashare.fundamentals_pit import FUND_COLS
from selene_space_ashare.style_monitor import (
    STYLE_FACTORS,
    style_regime_summary,
    style_spread_by_day,
)

logger = logging.getLogger(__name__)

_EMPTY = object()  # negative-cache sentinel: "build determined there is no data"


_cache: dict[str, Any] = {}
_lock = asyncio.Lock()


def _cache_key(
    codes: list[str],
    start_date: str,
    end_date: str,
    forward_col: str,
    lookback: int,
) -> str:
    fingerprint = hashlib.sha1(
        ",".join(sorted({c.strip() for c in codes if c.strip()})).encode("utf-8")
    ).hexdigest()[:12]
    return f"{start_date}:{end_date}:{forward_col}:{lookback}:{fingerprint}"


def _orthogonal_section(panel: "pd.DataFrame") -> dict[str, Any] | None:
    """Greedy residual orthogonalization over all numeric non-fwd panel columns."""
    skip = {
        "date",
        "symbol",
        "close",
        "open",
        "high",
        "low",
        "volume",
        "amount",
        "turnover",
        "code",
        *FUND_COLS,
    }
    factors = [
        c
        for c in panel.columns
        if c not in skip
        and not c.startswith("fwd_")
        and panel[c].notna().any()
        and pd.api.types.is_numeric_dtype(panel[c])
    ]
    if len(factors) < 3:
        return None
    try:
        result = orthogonalize_greedy(panel, factors, forward_col="fwd_5d")
    except Exception as exc:
        logger.warning("orthogonalize_greedy failed: %s", exc)
        return None
    return {
        "kept": result["kept"],
        "dropped": result["dropped"],
        "order": result["order"],
        "params": result["params"],
    }


def _build(
    codes: list[str],
    start_date: str,
    end_date: str,
    forward_col: str,
    lookback: int,
) -> dict[str, Any]:
    panel = build_factor_panel(codes, start_date=start_date, end_date=end_date)
    styles: dict[str, Any] = {}
    for style, direction in STYLE_FACTORS.items():
        if style not in panel.columns or not panel[style].notna().any():
            styles[style] = {"regime": "factor_absent"}
            continue
        by_day = style_spread_by_day(panel, style, forward_col, direction=direction)
        summary = style_regime_summary(by_day, lookback=lookback)
        styles[style] = {
            **summary,
            "spread_series": [
                {"date": str(d)[:10], "spread": round(float(v), 6)}
                for d, v in by_day.dropna().items()
            ],
        }
    return {
        "forward_col": forward_col,
        "lookback": lookback,
        "n_symbols": int(panel["symbol"].nunique()),
        "n_days": int(panel["date"].nunique()),
        "styles": styles,
        "orthogonal": _orthogonal_section(panel),
    }


async def get_style_report(
    codes: list[str],
    start_date: str,
    end_date: str,
    forward_col: str,
    lookback: int,
    warmup: bool = False,
) -> dict[str, Any] | None:
    """Cached report, or None when the negative cache holds an empty result.

    warmup=True (startup preload): when the build comes back empty, skip the
    _EMPTY negative-cache write — a warmup hitting a temporarily empty kline
    cache must not poison the cache for the first real request.
    """
    key = _cache_key(codes, start_date, end_date, forward_col, lookback)
    if key in _cache:
        cached = _cache[key]
        return None if cached is _EMPTY else cached
    async with _lock:
        if key in _cache:
            cached = _cache[key]
            return None if cached is _EMPTY else cached
        result = await asyncio.to_thread(
            _build, codes, start_date, end_date, forward_col, lookback
        )
    if not result["styles"] or all(
        info.get("regime") == "factor_absent" for info in result["styles"].values()
    ):
        if warmup:
            logger.warning(
                "ashare style warmup returned empty report; negative cache skipped"
            )
        else:
            _cache[key] = _EMPTY
        return None
    _cache[key] = result
    return result


async def refresh_style_report(
    codes: list[str],
    start_date: str,
    end_date: str,
    forward_col: str,
    lookback: int,
) -> dict[str, Any] | None:
    """Drop the cached entry and rebuild through the same to_thread path."""
    key = _cache_key(codes, start_date, end_date, forward_col, lookback)
    _cache.pop(key, None)
    return await get_style_report(codes, start_date, end_date, forward_col, lookback)
