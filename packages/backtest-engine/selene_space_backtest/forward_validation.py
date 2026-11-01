"""Forward validation of a completed analysis verdict against later bars.

Pure functions, no I/O: the caller (recorder) supplies forward bars, so the
evaluation is replayable offline and unit-testable in isolation.

An analysis record carries structured stage2 output. The expected market
direction is derived from ``stage2.decision`` + ``stage2.trade_setup.direction``;
``no_trade`` maps to ``flat`` ("correctly doing nothing" counts as a win while
price stays inside the neutral band). A record whose direction cannot be
derived is *not judgeable* (insufficient), never silently neutral.

Three-state evaluation: ``completed`` (enough bars, verdict produced),
``insufficient_data`` (direction unknown or forward window not yet filled),
``error`` (bad parameters or malformed prices). A short bar window is NEVER
evaluated as completed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Sequence

EVAL_COMPLETED = "completed"
EVAL_INSUFFICIENT = "insufficient_data"
EVAL_ERROR = "error"

DEFAULT_EVAL_WINDOW_DAYS = 10
DEFAULT_NEUTRAL_BAND_PCT = 2.0

_UP = "up"
_DOWN = "down"
_FLAT = "flat"
_NOT_DOWN = "not_down"

_DIRECTIONS = (_UP, _DOWN, _FLAT, _NOT_DOWN)

_ENTRY_DECISIONS = frozenset({"limit", "breakout", "market"})

Direction = str


def expected_direction(record: dict[str, Any]) -> str | None:
    """Derive the direction the analysis committed to, or None if unmappable."""
    stage2 = record.get("stage2") if isinstance(record, dict) else None
    if not isinstance(stage2, dict):
        return None
    decision = stage2.get("decision")
    if decision == "no_trade":
        return _FLAT
    if not isinstance(decision, str) or decision not in _ENTRY_DECISIONS:
        return None
    setup = stage2.get("trade_setup")
    if not isinstance(setup, dict):
        return None
    side = setup.get("direction")
    if side == "long":
        return _UP
    if side == "short":
        return _DOWN
    return None


def classify_outcome(
    *,
    stock_return_pct: float | None,
    direction_expected: str,
    neutral_band_pct: float,
) -> tuple[str | None, bool | None]:
    """Map (return %, expected direction) to (outcome, direction_correct).

    Inside the neutral band the verdict is "neutral" and correctness is None:
    an unexercised judgment is neither right nor wrong.
    """
    if stock_return_pct is None:
        return None, None

    band = abs(float(neutral_band_pct))
    r = float(stock_return_pct)

    if direction_expected == _UP:
        if r >= band:
            return "win", True
        if r <= -band:
            return "loss", False
        return "neutral", None

    if direction_expected == _DOWN:
        if r <= -band:
            return "win", True
        if r >= band:
            return "loss", False
        return "neutral", None

    if direction_expected == _NOT_DOWN:
        if r >= 0:
            return "win", True
        if r <= -band:
            return "loss", False
        return "neutral", None

    # flat: staying inside the band is the win condition
    if abs(r) <= band:
        return "win", True
    return "loss", False


def analysis_close(record: dict[str, Any]) -> float | None:
    """Close of the last bar the analysis saw: the base price of the forward return."""
    klines = _klines_of(record)
    if not klines:
        return None
    return _as_float(klines[-1].get("close"))


def analysis_bar_date(record: dict[str, Any]) -> str | None:
    """UTC date (YYYY-MM-DD) of the anchor bar's timestamp (epoch ms)."""
    klines = _klines_of(record)
    if not klines:
        return None
    raw_ts = klines[-1].get("timestamp")
    if not isinstance(raw_ts, (int, float)) or isinstance(raw_ts, bool):
        return None
    try:
        return datetime.fromtimestamp(float(raw_ts) / 1000.0, tz=UTC).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def evaluate_forward(
    record: dict[str, Any],
    forward_bars: Sequence[dict[str, Any]],
    *,
    eval_window_days: int = DEFAULT_EVAL_WINDOW_DAYS,
    neutral_band_pct: float = DEFAULT_NEUTRAL_BAND_PCT,
) -> dict[str, Any]:
    """Evaluate a record against the bars *after* its anchor bar (ascending).

    Returns a dict that always has ``eval_status``. On ``completed`` it also
    carries ``direction_expected`` / ``direction_correct`` / ``outcome`` /
    ``stock_return_pct`` / ``start_price`` / ``end_close`` / ``max_high`` /
    ``min_low`` / ``bar_window`` (dates of the evaluated bars, in order).
    """
    window_days = int(eval_window_days)
    if window_days <= 0:
        return {"eval_status": EVAL_ERROR, "error": "eval_window_days must be positive"}

    direction = expected_direction(record)
    if direction is None:
        return {
            "eval_status": EVAL_INSUFFICIENT,
            "reason": "no_direction_in_record",
        }

    start_price = analysis_close(record)
    if start_price is None or start_price <= 0:
        return {"eval_status": EVAL_ERROR, "error": "invalid_start_price"}

    bars = list(forward_bars)
    if len(bars) < window_days:
        return {
            "eval_status": EVAL_INSUFFICIENT,
            "reason": "forward_bars_shorter_than_window",
            "eval_window_days": window_days,
            "bars_available": len(bars),
        }

    window = bars[:window_days]
    end_close = _as_float(window[-1].get("close"))
    if end_close is None:
        return {"eval_status": EVAL_ERROR, "error": "invalid_end_close"}

    highs = [v for v in (_as_float(b.get("high")) for b in window) if v is not None]
    lows = [v for v in (_as_float(b.get("low")) for b in window) if v is not None]

    stock_return_pct = (end_close - start_price) / start_price * 100.0
    outcome, direction_correct = classify_outcome(
        stock_return_pct=stock_return_pct,
        direction_expected=direction,
        neutral_band_pct=neutral_band_pct,
    )

    return {
        "eval_status": EVAL_COMPLETED,
        "eval_window_days": window_days,
        "neutral_band_pct": float(neutral_band_pct),
        "direction_expected": direction,
        "direction_correct": direction_correct,
        "outcome": outcome,
        "start_price": start_price,
        "end_close": end_close,
        "stock_return_pct": stock_return_pct,
        "max_high": max(highs) if highs else None,
        "min_low": min(lows) if lows else None,
        # Window bounds come only from observed bar dates; missing date -> "".
        "bar_window": [str(b.get("date") or "") for b in window],
    }


def window_end_of(result: dict[str, Any]) -> str | None:
    """Last bar date of a completed evaluation (tail of the idempotency key)."""
    if result.get("eval_status") != EVAL_COMPLETED:
        return None
    window = result.get("bar_window") or []
    if not window:
        return None
    tail = str(window[-1]).strip()
    return tail or None


def _klines_of(record: dict[str, Any]) -> list[dict[str, Any]]:
    input_data = record.get("input_data")
    if not isinstance(input_data, dict):
        return []
    klines = input_data.get("klines")
    if not isinstance(klines, list):
        return []
    return [k for k in klines if isinstance(k, dict)]


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_EVAL_WINDOW_DAYS",
    "DEFAULT_NEUTRAL_BAND_PCT",
    "EVAL_COMPLETED",
    "EVAL_ERROR",
    "EVAL_INSUFFICIENT",
    "analysis_bar_date",
    "analysis_close",
    "classify_outcome",
    "evaluate_forward",
    "expected_direction",
    "window_end_of",
]
