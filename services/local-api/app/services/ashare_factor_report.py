"""A-share IC factor report service (US-106).

In-process cache + lazy build, mirroring app/services/ashare_style_report.py
(the F8 pattern): a 300-symbol factor panel takes ~60s to build, so it must
never run inside the request path synchronously — all computation goes through
asyncio.to_thread. No TTL: a TTL-expired first request would eat the full
rebuild. Empty-data results are negatively cached (sentinel _EMPTY) so repeated
calls do not re-run a doomed build.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, cast

import pandas as pd

from selene_space_ashare.factor_eval import calc_factor_ic_series
from selene_space_ashare.factor_report import (
    build_factor_panel,
    generate_factor_report,
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
    neutralize: bool,
    factors: list[str] | None,
) -> str:
    fingerprint = hashlib.sha1(
        ",".join(sorted({c.strip() for c in codes if c.strip()})).encode("utf-8")
    ).hexdigest()[:12]
    fpart = "" if factors is None else ",".join(sorted(factors))
    return (
        f"{start_date}:{end_date}:{forward_col}:{int(neutralize)}:{fpart}:{fingerprint}"
    )


_IC_SERIES_TAIL = 250  # trading days ~ 1y; the panel can span years


def _build_ic_series(
    panel: pd.DataFrame, factors: list[str], forward_col: str
) -> dict[str, Any]:
    """Per-date IC for each factor, tail-capped for the frontend chart.

    generate_factor_report returns only aggregate IC stats, so the daily
    series is computed here rather than in the shared generator (which the
    CLI and the batch7/8/9 research scripts also consume — no contract change).

    The shared date axis is the UNION of every factor's dates, not the first
    factor's: a factor with a longer warm-up (more leading NaN) would otherwise
    truncate the others' earlier points, making the chart depend on the order
    the factors happen to be listed in.
    """
    raw: list[tuple[str, pd.Series]] = []
    for name in factors:
        try:
            ic = calc_factor_ic_series(panel, name, forward_col=forward_col)
        except (KeyError, TypeError) as exc:  # factor absent from this panel
            logger.warning("ic series skipped for %s: %s", name, exc)
            continue
        ic = ic.dropna()
        if not ic.empty:
            raw.append((name, ic))
    if not raw:
        return {"dates": [], "series": []}

    axis = raw[0][1].index
    for _, ic in raw[1:]:
        axis = axis.union(ic.index)
    axis = axis.sort_values()[-_IC_SERIES_TAIL:]
    dates = [str(d) for d in axis]

    series = [
        {
            "name": name,
            "values": [
                None if v is None or v != v else float(v)
                for v in ic.reindex(axis).to_numpy()
            ],
        }
        for name, ic in raw
    ]
    return {"dates": dates, "series": series}


def _build(
    codes: list[str],
    start_date: str,
    end_date: str,
    forward_col: str,
    neutralize: bool,
    factors: list[str] | None,
) -> dict[str, Any]:
    try:
        panel = build_factor_panel(codes, start_date=start_date, end_date=end_date)
    except ValueError as exc:
        # build_factor_panel raises ValueError("no usable kline data...")
        # when every symbol is unusable. That is data absence, not a bad
        # request, so route it through the negative cache -> caller 404s.
        logger.warning("factor report build found no usable data: %s", exc)
        return {}
    report = cast(
        dict[str, Any],
        generate_factor_report(
            panel,
            factors=factors,
            forward_col=forward_col,
            neutralize=neutralize,
        ),
    )
    evaluated = [r["factor"] for r in report.get("ic_report") or [] if r.get("factor")]
    report["ic_series"] = _build_ic_series(panel, evaluated, forward_col)
    return report


async def get_factor_report(
    codes: list[str],
    start_date: str,
    end_date: str,
    forward_col: str,
    neutralize: bool = True,
    factors: list[str] | None = None,
) -> dict[str, Any] | None:
    """Cached IC report, or None when the negative cache holds an empty result."""
    key = _cache_key(codes, start_date, end_date, forward_col, neutralize, factors)
    if key in _cache:
        cached = _cache[key]
        return None if cached is _EMPTY else cached
    async with _lock:
        if key in _cache:
            cached = _cache[key]
            return None if cached is _EMPTY else cached
        result = await asyncio.to_thread(
            _build, codes, start_date, end_date, forward_col, neutralize, factors
        )
    if not result.get("ic_report"):
        _cache[key] = _EMPTY
        return None
    _annotate_min_names(result)
    _cache[key] = result
    return result


def _annotate_min_names(result: dict[str, Any]) -> None:
    """Flag an all-NaN IC report as a sample-size problem.

    calc_ic returns NaN when a cross-section has fewer than 10 usable names
    (factor_eval.py:33), so a universe that is too small yields a report
    full of blanks that looks identical to "factors have no predictive
    power". The panel built fine, so this is not an error — but the caller
    deserves to know why the numbers are missing.
    """
    rows = result.get("ic_report") or []
    ic_vals = [r.get("IC_mean") for r in rows]
    if not ic_vals:
        return
    if all(v is None or v != v for v in ic_vals):
        result["ic_all_nan"] = True
        result["ic_all_nan_reason"] = (
            "每个截面的可用股票数不足 10（calc_ic 门槛），IC 无法计算；"
            "请扩大股票池或放宽 start_date"
        )
    else:
        result["ic_all_nan"] = False


async def refresh_factor_report(
    codes: list[str],
    start_date: str,
    end_date: str,
    forward_col: str,
    neutralize: bool = True,
    factors: list[str] | None = None,
) -> dict[str, Any] | None:
    """Drop the cached entry and rebuild through the same to_thread path."""
    key = _cache_key(codes, start_date, end_date, forward_col, neutralize, factors)
    _cache.pop(key, None)
    return await get_factor_report(
        codes, start_date, end_date, forward_col, neutralize, factors
    )
