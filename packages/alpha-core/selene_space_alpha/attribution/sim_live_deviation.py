"""Paper-to-Live fill deviation analysis — compare simulated and live execution.

Matches paper (simulated) fills against live fills by symbol, side, and
timestamp proximity, then computes per-symbol deviation metrics (price,
fee, latency, quantity).  Provides aggregation and anomaly detection on
top of the per-symbol deviation reports.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FillRecord:
    """A single fill event from either paper or live execution.

    Attributes:
        source: Origin of the fill — ``"paper"`` or ``"live"``.
        symbol: Trading pair identifier (e.g. ``"BTC/USDT"``).
        side: Trade direction — ``"buy"`` or ``"sell"``.
        price: Execution price.
        quantity: Fill quantity.
        fee: Fee paid for this fill.
        timestamp_ms: Fill timestamp in milliseconds since epoch.
        latency_ms: Round-trip latency in milliseconds, or *None* if
            unavailable.
    """

    source: str
    symbol: str
    side: str
    price: float
    quantity: float
    fee: float
    timestamp_ms: int
    latency_ms: int | None


@dataclass(frozen=True)
class DeviationReport:
    """Per-symbol deviation between paper and live fills.

    Attributes:
        symbol: Trading pair identifier.
        price_deviation_bps: Price deviation in basis points.
            ``(paper_price - live_price) / live_price * 10_000``.
        fee_deviation_pct: Fee deviation in percent.
            ``(paper_fee - live_fee) / live_fee * 100``.
        latency_deviation_ms: Latency difference in milliseconds.
            ``paper_latency - live_latency``.  *NaN* if either side
            lacks latency data.
        quantity_match: Whether paper and live quantities are exactly equal.
        n_comparisons: Number of matched fill pairs used for this report.
    """

    symbol: str
    price_deviation_bps: float
    fee_deviation_pct: float
    latency_deviation_ms: float
    quantity_match: bool
    n_comparisons: int


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def compare_fills(
    paper_fills: list[FillRecord],
    live_fills: list[FillRecord],
    max_time_diff_ms: int = 60_000,
) -> list[DeviationReport]:
    """Match paper fills to live fills and compute per-symbol deviations.

    Matching is performed by grouping fills on ``(symbol, side)`` and then
    pairing each paper fill with the closest live fill by timestamp that
    falls within *max_time_diff_ms*.  Each live fill is matched at most
    once (greedy nearest-match).

    Parameters
    ----------
    paper_fills : list[FillRecord]
        Simulated fill records (``source == "paper"``).
    live_fills : list[FillRecord]
        Live execution fill records (``source == "live"``).
    max_time_diff_ms : int
        Maximum allowed timestamp difference for a match, in milliseconds.
        Default 60 000 ms (1 minute).

    Returns
    -------
    list[DeviationReport]
        One report per symbol that has at least one matched pair, sorted
        by symbol.  Deviation values are averaged across all matched pairs
        for the symbol.  ``quantity_match`` is *True* only when **all**
        matched pairs have equal quantities.
    """
    # Group fills by (symbol, side)
    paper_by_key: dict[tuple[str, str], list[FillRecord]] = {}
    for f in paper_fills:
        key = (f.symbol, f.side)
        paper_by_key.setdefault(key, []).append(f)

    live_by_key: dict[tuple[str, str], list[FillRecord]] = {}
    for f in live_fills:
        key = (f.symbol, f.side)
        live_by_key.setdefault(key, []).append(f)

    # Collect matched pairs per symbol
    matched: dict[str, list[tuple[FillRecord, FillRecord]]] = {}

    for key, p_fills in paper_by_key.items():
        l_fills = live_by_key.get(key, [])
        if not l_fills:
            continue

        # Sort both by timestamp for efficient nearest-match
        p_sorted = sorted(p_fills, key=lambda r: r.timestamp_ms)
        l_sorted = sorted(l_fills, key=lambda r: r.timestamp_ms)

        used_live: set[int] = set()

        for pf in p_sorted:
            best_idx: int | None = None
            best_diff: int = max_time_diff_ms + 1

            for i, lf in enumerate(l_sorted):
                if i in used_live:
                    continue
                diff = abs(pf.timestamp_ms - lf.timestamp_ms)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
                # Since l_sorted is sorted by timestamp, once diff starts
                # increasing past best_diff we can stop early
                if diff > best_diff and best_idx is not None:
                    break

            if best_idx is not None and best_diff <= max_time_diff_ms:
                used_live.add(best_idx)
                matched.setdefault(pf.symbol, []).append((pf, l_sorted[best_idx]))

    if not matched:
        return []

    # Build per-symbol DeviationReport
    reports: list[DeviationReport] = []
    for symbol in sorted(matched):
        pairs = matched[symbol]
        n = len(pairs)

        price_devs: list[float] = []
        fee_devs: list[float] = []
        latency_devs: list[float] = []
        all_qty_match = True

        for pf, lf in pairs:
            # Price deviation in bps
            if lf.price != 0.0:
                price_devs.append((pf.price - lf.price) / lf.price * 10_000)
            else:
                price_devs.append(float("nan"))

            # Fee deviation in percent
            if lf.fee != 0.0:
                fee_devs.append((pf.fee - lf.fee) / lf.fee * 100)
            else:
                fee_devs.append(float("nan"))

            # Latency deviation
            if pf.latency_ms is not None and lf.latency_ms is not None:
                latency_devs.append(float(pf.latency_ms - lf.latency_ms))
            else:
                latency_devs.append(float("nan"))

            # Quantity match
            if pf.quantity != lf.quantity:
                all_qty_match = False

        # Compute averages, handling all-NaN latency gracefully
        price_arr = np.array(price_devs, dtype=float)
        fee_arr = np.array(fee_devs, dtype=float)
        latency_arr = np.array(latency_devs, dtype=float)

        price_mean = float(np.nanmean(price_arr)) if len(price_arr) else 0.0
        fee_mean = float(np.nanmean(fee_arr)) if len(fee_arr) else 0.0
        latency_mean = (
            float(np.nanmean(latency_arr))
            if len(latency_arr) and not np.all(np.isnan(latency_arr))
            else float("nan")
        )

        reports.append(DeviationReport(
            symbol=symbol,
            price_deviation_bps=price_mean,
            fee_deviation_pct=fee_mean,
            latency_deviation_ms=latency_mean,
            quantity_match=all_qty_match,
            n_comparisons=n,
        ))

    return reports


def aggregate_deviations(reports: list[DeviationReport]) -> dict[str, float]:
    """Aggregate deviation metrics across all symbols.

    Parameters
    ----------
    reports : list[DeviationReport]
        Per-symbol deviation reports from :func:`compare_fills`.

    Returns
    -------
    dict[str, float]
        Aggregated statistics:

        - ``price_deviation_bps_mean`` / ``_median`` / ``_std``
        - ``fee_deviation_pct_mean`` / ``_median`` / ``_std``
        - ``latency_deviation_ms_mean`` / ``_median`` / ``_std``
        - ``fill_match_rate`` — fraction of symbols where
          ``quantity_match`` is *True*.

        Returns an empty dict when *reports* is empty.  NaN values are
        excluded from mean/median/std calculations.
    """
    if not reports:
        return {}

    price_bps = np.array([r.price_deviation_bps for r in reports], dtype=float)
    fee_pct = np.array([r.fee_deviation_pct for r in reports], dtype=float)
    latency_ms = np.array([r.latency_deviation_ms for r in reports], dtype=float)

    def _stats(arr: np.ndarray) -> dict[str, float]:
        clean = arr[~np.isnan(arr)]
        if len(clean) == 0:
            return {"mean": float("nan"), "median": float("nan"), "std": float("nan")}
        return {
            "mean": float(np.mean(clean)),
            "median": float(np.median(clean)),
            "std": float(np.std(clean, ddof=0)),
        }

    price_stats = _stats(price_bps)
    fee_stats = _stats(fee_pct)
    latency_stats = _stats(latency_ms)

    fill_match_rate = sum(1 for r in reports if r.quantity_match) / len(reports)

    return {
        "price_deviation_bps_mean": price_stats["mean"],
        "price_deviation_bps_median": price_stats["median"],
        "price_deviation_bps_std": price_stats["std"],
        "fee_deviation_pct_mean": fee_stats["mean"],
        "fee_deviation_pct_median": fee_stats["median"],
        "fee_deviation_pct_std": fee_stats["std"],
        "latency_deviation_ms_mean": latency_stats["mean"],
        "latency_deviation_ms_median": latency_stats["median"],
        "latency_deviation_ms_std": latency_stats["std"],
        "fill_match_rate": fill_match_rate,
    }


def detect_anomalies(
    reports: list[DeviationReport],
    price_threshold_bps: float = 10.0,
    fee_threshold_pct: float = 50.0,
) -> list[DeviationReport]:
    """Flag deviation reports that exceed configurable thresholds.

    A report is flagged as anomalous when **either** the absolute price
    deviation exceeds *price_threshold_bps* **or** the absolute fee
    deviation exceeds *fee_threshold_pct*.

    Parameters
    ----------
    reports : list[DeviationReport]
        Per-symbol deviation reports from :func:`compare_fills`.
    price_threshold_bps : float
        Maximum acceptable absolute price deviation in basis points.
        Default 10 bps.
    fee_threshold_pct : float
        Maximum acceptable absolute fee deviation in percent.
        Default 50 %.

    Returns
    -------
    list[DeviationReport]
        Subset of *reports* where at least one metric exceeds its
        threshold.  NaN values are treated as non-anomalous (they are
        not compared against thresholds).
    """
    anomalous: list[DeviationReport] = []
    for r in reports:
        price_flag = (
            not np.isnan(r.price_deviation_bps)
            and abs(r.price_deviation_bps) > price_threshold_bps
        )
        fee_flag = (
            not np.isnan(r.fee_deviation_pct)
            and abs(r.fee_deviation_pct) > fee_threshold_pct
        )
        if price_flag or fee_flag:
            anomalous.append(r)
    return anomalous
