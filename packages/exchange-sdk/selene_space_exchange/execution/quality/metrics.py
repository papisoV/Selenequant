"""Execution quality analysis — implementation shortfall, VWAP benchmarking, slippage reporting.

Measures how well fills compare to arrival price, market VWAP, and other
benchmarks. Essential for best-execution reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------


def compute_implementation_shortfall(
    arrival_price: float,
    fill_price: float,
    quantity: float,
    side: str = "buy",
) -> dict[str, float]:
    """Compute implementation shortfall per Perold (1988).

    Implementation shortfall = (fill_price - arrival_price) / arrival_price
    expressed in basis points. For sells, the sign is flipped so positive
    always means worse-than-benchmark.

    Returns dict with:
      - shortfall_bps: Total IS in basis points
      - shortfall_notional: IS in notional terms (quote currency)
    """
    if arrival_price <= 0:
        return {"shortfall_bps": 0.0, "shortfall_notional": 0.0}

    price_diff = fill_price - arrival_price
    if side == "sell":
        price_diff = -price_diff  # flip so positive = cost

    shortfall_frac = price_diff / arrival_price
    shortfall_bps = shortfall_frac * 10_000.0
    shortfall_notional = shortfall_frac * quantity * arrival_price

    return {
        "shortfall_bps": round(shortfall_bps, 4),
        "shortfall_notional": round(shortfall_notional, 4),
    }


def compute_arrival_slippage_bps(
    arrival_price: float,
    fill_price: float,
    side: str = "buy",
) -> float:
    """Slippage from arrival price in basis points.

    Positive = worse than arrival (buy above arrival, sell below).
    """
    if arrival_price <= 0:
        return 0.0

    if side == "buy":
        return round((fill_price - arrival_price) / arrival_price * 10_000.0, 4)
    return round((arrival_price - fill_price) / arrival_price * 10_000.0, 4)


# ---------------------------------------------------------------------------
# VWAP benchmark
# ---------------------------------------------------------------------------


def compute_vwap(prices: list[float], volumes: list[float]) -> float:
    """Compute volume-weighted average price."""
    if not prices or not volumes or len(prices) != len(volumes):
        return 0.0

    total_volume = sum(volumes)
    if total_volume <= 0:
        return 0.0

    return sum(p * v for p, v in zip(prices, volumes)) / total_volume


def compute_vwap_benchmark(
    fill_price: float,
    market_vwap: float,
    quantity: float,
    side: str = "buy",
) -> dict[str, float]:
    """Compare fill price against market VWAP.

    Returns dict with:
      - vwap_diff_bps: Fill vs VWAP in basis points
      - vwap_cost_notional: Cost/saving relative to VWAP
      - market_vwap: The VWAP used as benchmark
    """
    if market_vwap <= 0:
        return {
            "vwap_diff_bps": 0.0,
            "vwap_cost_notional": 0.0,
            "market_vwap": market_vwap,
        }

    if side == "buy":
        diff_bps = (fill_price - market_vwap) / market_vwap * 10_000.0
    else:
        diff_bps = (market_vwap - fill_price) / market_vwap * 10_000.0

    notional = diff_bps / 10_000.0 * quantity * market_vwap

    return {
        "vwap_diff_bps": round(diff_bps, 4),
        "vwap_cost_notional": round(notional, 4),
        "market_vwap": round(market_vwap, 8),
    }


# ---------------------------------------------------------------------------
# Slippage report
# ---------------------------------------------------------------------------


@dataclass
class SlippageRecord:
    """A single fill with slippage metrics."""

    symbol: str
    side: str
    quantity: float
    arrival_price: float
    fill_price: float
    vwap_benchmark: float | None = None
    timestamp_ms: int = 0


@dataclass
class SlippageReport:
    """Aggregated slippage statistics over a time period."""

    symbol: str = ""
    total_fills: int = 0
    total_notional: float = 0.0

    avg_slippage_bps: float = 0.0
    max_slippage_bps: float = 0.0
    min_slippage_bps: float = 0.0

    avg_vwap_diff_bps: float = 0.0
    total_shortfall_notional: float = 0.0

    records: list[SlippageRecord] = field(default_factory=list)


def aggregate_slippage_report(
    records: list[SlippageRecord],
    symbol: str = "",
) -> SlippageReport:
    """Aggregate a list of SlippageRecords into a summary report."""
    if not records:
        return SlippageReport(symbol=symbol)

    slippages: list[float] = []
    vwap_diffs: list[float] = []
    total_notional = 0.0
    total_shortfall = 0.0

    for r in records:
        slip = compute_arrival_slippage_bps(r.arrival_price, r.fill_price, r.side)
        slippages.append(slip)
        total_notional += r.quantity * r.fill_price

        shortfall = compute_implementation_shortfall(
            r.arrival_price, r.fill_price, r.quantity, r.side,
        )
        total_shortfall += shortfall["shortfall_notional"]

        if r.vwap_benchmark and r.vwap_benchmark > 0:
            vwap_result = compute_vwap_benchmark(
                r.fill_price, r.vwap_benchmark, r.quantity, r.side,
            )
            vwap_diffs.append(vwap_result["vwap_diff_bps"])

    return SlippageReport(
        symbol=symbol,
        total_fills=len(records),
        total_notional=round(total_notional, 2),
        avg_slippage_bps=round(sum(slippages) / len(slippages), 4) if slippages else 0.0,
        max_slippage_bps=round(max(slippages), 4) if slippages else 0.0,
        min_slippage_bps=round(min(slippages), 4) if slippages else 0.0,
        avg_vwap_diff_bps=round(sum(vwap_diffs) / len(vwap_diffs), 4) if vwap_diffs else 0.0,
        total_shortfall_notional=round(total_shortfall, 4),
        records=records,
    )


def group_slippage_by_symbol(
    records: list[SlippageRecord],
) -> dict[str, SlippageReport]:
    """Group slippage records by symbol and produce per-symbol reports."""
    groups: dict[str, list[SlippageRecord]] = {}
    for r in records:
        groups.setdefault(r.symbol, []).append(r)

    return {
        sym: aggregate_slippage_report(recs, symbol=sym)
        for sym, recs in groups.items()
    }


def group_slippage_by_side(
    records: list[SlippageRecord],
) -> dict[str, SlippageReport]:
    """Group slippage records by side (buy/sell) and produce reports."""
    groups: dict[str, list[SlippageRecord]] = {}
    for r in records:
        groups.setdefault(r.side, []).append(r)

    return {
        side: aggregate_slippage_report(recs, symbol=side)
        for side, recs in groups.items()
    }
