# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: the upstream logic lived
# as a static method on DSA's ``BacktestEngine`` (``_classify_signal_outcome``)
# and a service-side action→direction helper, both coupled to DSA's ORM rows and
# ``DailyBarLike`` objects. Carried over here as free pure functions over plain
# dicts, so the evaluation is replayable offline and unit-testable in isolation —
# the same shape as the sibling :mod:`selene_space_backtest.forward_validation`.
# The one intentional difference from that sibling: this module keeps DSA's
# ``not_up`` direction (see below), which ``forward_validation`` does not model.
"""Forward outcome of a persisted decision signal, judged by direction.

DSA separates a signal's *action* (eight states: buy / add / hold / reduce /
sell / watch / avoid / alert) from the *direction* that action commits to, and
scores the realised return against that direction:

  - ``up``       — the action wants price up (buy / add)
  - ``not_down`` — the action wants "not down" (hold); a flat-to-up tape wins
  - ``not_up``   — the action wants "not up" (reduce / sell / avoid); a
                   flat-to-down tape wins
  - ``None``     — non-directional (watch / alert): not judgeable, never scored

The ``not_up`` leg is the reason this module exists rather than reusing
:func:`selene_space_backtest.forward_validation.classify_outcome`: that function
models up / down / flat / not_down only, so a ``not_up`` direction falls through
to its ``flat`` branch and scores a *successful* sell call (price fell) as a
loss. A defensive call scored as a false negative is worse than no score at all.

Three-state honesty: an evaluation either completes (``completed``, a verdict is
produced) or is *not judgeable* (``unable``, with a machine-readable
``unable_reason``). A window that has not filled is NEVER scored as a completed
verdict — mirroring the honest-refusal discipline of ``forward_validation``.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

EVAL_COMPLETED = "completed"
EVAL_UNABLE = "unable"

OUTCOME_HIT = "hit"
OUTCOME_MISS = "miss"
OUTCOME_NEUTRAL = "neutral"

DEFAULT_NEUTRAL_BAND_PCT = 2.0

# Horizon label -> forward window in trading days. Mirrors DSA's
# ``SUPPORTED_OUTCOME_HORIZONS``; a horizon outside this map is not judgeable
# (``unsupported_horizon``) rather than being silently coerced.
SIGNAL_OUTCOME_HORIZONS: Mapping[str, int] = {
    "1d": 1,
    "3d": 3,
    "5d": 5,
    "10d": 10,
}

# Bumping this invalidates prior outcomes in the unique key
# (signal_id, horizon, engine_version), so a scoring change can be replayed
# without overwriting history under the old version.
SIGNAL_OUTCOME_ENGINE_VERSION = "decision-signal-v1"

# An ``unable`` verdict is not final when the cause is missing/insufficient
# data: a later run, once the market data has arrived, must re-attempt it.
# Reasons absent from this set are terminal (retrying cannot change them).
RETRYABLE_UNABLE_REASONS = frozenset(
    {
        "missing_anchor_price",
        "invalid_anchor_price",
        "insufficient_forward_bars",
        "missing_end_close",
        "invalid_end_close",
    }
)

_UP = "up"
_NOT_DOWN = "not_down"
_NOT_UP = "not_up"

_DIRECTIONAL_OUTCOMES = frozenset({OUTCOME_HIT, OUTCOME_MISS, OUTCOME_NEUTRAL})


def direction_for_action(action: Any) -> str | None:
    """Map an eight-state action to the direction it commits to, or None.

    ``None`` means *non-directional* (watch / alert) or an unrecognised token:
    the signal is not judgeable and must never be scored. This is deliberately
    NOT the same mapping as ``action_taxonomy.ACTION_DIRECTION``, which
    describes Selene's long/short execution vocabulary — e.g. ``hold`` is
    ``not_down`` here but ``neutral`` there, and ``avoid`` is ``not_up`` here
    but ``neutral`` there. Outcome direction is its own vocabulary.
    """
    text = str(action or "").strip().lower()
    if text in {"buy", "add"}:
        return _UP
    if text == "hold":
        return _NOT_DOWN
    if text in {"reduce", "sell", "avoid"}:
        return _NOT_UP
    return None


def classify_signal_outcome(
    *,
    stock_return_pct: float | None,
    direction_expected: str,
    neutral_band_pct: float,
) -> tuple[str | None, bool | None]:
    """Map (forward return %, expected direction) to (outcome, direction_correct).

    Inside the neutral band on an ``up`` / ``not_down`` call the verdict is
    ``neutral`` and correctness is ``None``: an unexercised judgment is neither
    right nor wrong. The ``not_up`` leg has no neutral band — DSA scores any
    return at or below the band as a hit — and that asymmetry is carried over
    verbatim rather than "tidied up", so both sides agree on the same calls.
    """
    if stock_return_pct is None:
        return None, None

    band = abs(float(neutral_band_pct))
    r = float(stock_return_pct)

    if direction_expected == _UP:
        if r >= band:
            return OUTCOME_HIT, True
        if r <= -band:
            return OUTCOME_MISS, False
        return OUTCOME_NEUTRAL, None

    if direction_expected == _NOT_DOWN:
        if r >= 0:
            return OUTCOME_HIT, True
        if r <= -band:
            return OUTCOME_MISS, False
        return OUTCOME_NEUTRAL, None

    if direction_expected == _NOT_UP:
        if r <= band:
            return OUTCOME_HIT, True
        return OUTCOME_MISS, False

    return None, None


def evaluate_signal(
    *,
    action: Any,
    horizon: Any,
    start_price: Any,
    forward_bars: Sequence[Mapping[str, Any]],
    neutral_band_pct: float = DEFAULT_NEUTRAL_BAND_PCT,
) -> dict[str, Any]:
    """Evaluate one signal/horizon against the bars after its anchor.

    Composes the two steps above behind one call so the caller cannot skip the
    direction or horizon gate. The returned dict always carries ``eval_status``;
    an ``unable`` result also carries ``unable_reason`` and never fabricates an
    outcome. ``forward_bars`` are plain mappings (``close`` / ``high`` / ``low``)
    ascending by date, the first ``window_days`` of which are judged.
    """
    direction = direction_for_action(action)
    if direction is None:
        return _unable("non_directional_action")

    base = {"direction_expected": direction}
    window_days = SIGNAL_OUTCOME_HORIZONS.get(str(horizon or "").strip().lower())
    if window_days is None:
        return {**base, **_unable("unsupported_horizon")}

    base = {
        "direction_expected": direction,
        "horizon": horizon,
        "eval_window_days": window_days,
        "engine_version": SIGNAL_OUTCOME_ENGINE_VERSION,
    }

    start = _as_float(start_price)
    if start is None:
        return {**base, **_unable("missing_anchor_price")}
    if start <= 0:
        return {**base, **_unable("invalid_anchor_price"), "start_price": start}

    bars = [b for b in forward_bars if isinstance(b, Mapping)]
    if len(bars) < window_days:
        return {
            **base,
            **_unable("insufficient_forward_bars"),
            "start_price": start,
            "bars_available": len(bars),
        }

    window = bars[:window_days]
    end_close = _as_float(window[-1].get("close"))
    if end_close is None:
        return {**base, **_unable("invalid_end_close"), "start_price": start}

    highs = [v for v in (_as_float(b.get("high")) for b in window) if v is not None]
    lows = [v for v in (_as_float(b.get("low")) for b in window) if v is not None]
    stock_return_pct = (end_close - start) / start * 100.0
    outcome, direction_correct = classify_signal_outcome(
        stock_return_pct=stock_return_pct,
        direction_expected=direction,
        neutral_band_pct=neutral_band_pct,
    )

    return {
        **base,
        "eval_status": EVAL_COMPLETED,
        "outcome": outcome,
        "direction_correct": direction_correct,
        "neutral_band_pct": float(neutral_band_pct),
        "start_price": start,
        "end_close": end_close,
        "stock_return_pct": stock_return_pct,
        "max_high": max(highs) if highs else None,
        "min_low": min(lows) if lows else None,
    }


def _unable(reason: str) -> dict[str, Any]:
    return {"eval_status": EVAL_UNABLE, "unable_reason": reason}


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "DEFAULT_NEUTRAL_BAND_PCT",
    "EVAL_COMPLETED",
    "EVAL_UNABLE",
    "OUTCOME_HIT",
    "OUTCOME_MISS",
    "OUTCOME_NEUTRAL",
    "RETRYABLE_UNABLE_REASONS",
    "SIGNAL_OUTCOME_ENGINE_VERSION",
    "SIGNAL_OUTCOME_HORIZONS",
    "classify_signal_outcome",
    "direction_for_action",
    "evaluate_signal",
]
