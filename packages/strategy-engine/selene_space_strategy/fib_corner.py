"""Fibonacci-grid corner state — causal (no-lookahead) implementation.

Promotes the corner-state logic that lived in the ``scratch/tmp/e24{3,6,9}*.py``
research scripts into the repo, carrying two corrections those scripts needed:

1. **Causal zigzag.** The scripts advanced the active leg on the pivot's own
   price-extreme index (``pivot_idx <= i``). A pivot is only *knowable* once
   price has retraced ``th`` away from it — the confirmation bar. Reading the
   grid off the extreme index lets the strategy see a pivot up to
   ``confirm - pivot`` bars before it exists, which is lookahead. This module
   advances on the confirmation index.

2. **Resolved-prior requirement.** The corner filter reads the previous test's
   outcome on the same level (the ``prior HOLD`` leg). This module requires
   that outcome to have resolved (``exit_bar < i``) before it is consumed, so
   the filter only ever reads the past.

The grid: from the last two confirmed pivots a Fibonacci family is laid out;
a "touch" is a close within ``TOL`` of an interior level. The corner state is
{touch on a level with a held prior test} AND {recent range narrow} AND
{participation high}.

Exports the offline event builder (the train/test cuts are computed from its
output) and :class:`CornerExecutionStrategy`, a ``StrategyRunner`` that replays
a precomputed event set through the real backtest engine with a parameterised
execution style (market vs resting-limit entry / exit).
"""

from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np

from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

# Fibonacci family used by the trader's hand-drawn grids. 0 and 1 are the leg
# ends; the interior levels are the tradable lines.
FIB_LEVELS: tuple[float, ...] = (
    0.0,
    0.2,
    0.236,
    0.3,
    0.382,
    0.5,
    0.618,
    0.7,
    0.786,
    0.886,
    1.0,
)

# How close (fractional, of price) a close must sit to a line to be a touch.
TOUCH_TOL = 0.003

# Fallback staleness ceiling between bars when the series' own cadence is
# unavailable. 1m bars => 10 minutes, matching the original research constant.
DEFAULT_GAP_MS = 10 * 60 * 1000


def causal_zigzag(
    highs: np.ndarray, lows: np.ndarray, threshold: float
) -> list[tuple[int, float, str, int]]:
    """Confirmed zigzag pivots as ``(pivot_idx, price, kind, confirm_idx)``.

    ``kind`` is ``"H"``/``"L"``; ``confirm_idx`` is the bar on which price had
    retraced ``threshold`` from the pivot — the first bar the pivot is knowable.
    """
    n = len(highs)
    pivots: list[tuple[int, float, str, int]] = []
    if n == 0:
        return pivots

    state: str | None = None
    ext_h, ext_h_i = float(highs[0]), 0
    ext_l, ext_l_i = float(lows[0]), 0

    for i in range(1, n):
        hi, lo = float(highs[i]), float(lows[i])
        if state is None:
            if hi > ext_h:
                ext_h, ext_h_i = hi, i
            if lo < ext_l:
                ext_l, ext_l_i = lo, i
            if lo <= ext_h * (1 - threshold) and ext_h_i <= i:
                pivots.append((ext_h_i, ext_h, "H", i))
                state = "down"
                ext_l, ext_l_i = lo, i
            elif hi >= ext_l * (1 + threshold) and ext_l_i <= i:
                pivots.append((ext_l_i, ext_l, "L", i))
                state = "up"
                ext_h, ext_h_i = hi, i
        elif state == "up":
            if hi > ext_h:
                ext_h, ext_h_i = hi, i
            if lo <= ext_h * (1 - threshold):
                pivots.append((ext_h_i, ext_h, "H", i))
                state = "down"
                ext_l, ext_l_i = lo, i
        else:  # state == "down"
            if lo < ext_l:
                ext_l, ext_l_i = lo, i
            if hi >= ext_l * (1 + threshold):
                pivots.append((ext_l_i, ext_l, "L", i))
                state = "up"
                ext_h, ext_h_i = hi, i
    return pivots


def build_events(
    ts: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    taker_buy: np.ndarray,
    *,
    threshold: float = 0.015,
    cooldown: int = 8,
    horizon: int = 180,
    ret_break: float = 0.5,
    ret_fail: float = 0.5,
    gap_ms: float | None = None,
    touch_mode: str = "fib",
    rsi: np.ndarray | None = None,
) -> list[dict[str, Any]]:
    """Build corner-touch events from OHLCV arrays.

    ``touch_mode``:
      * ``"fib"`` — a touch is a close landing within ``TOUCH_TOL`` of an
        interior fib level of the active leg.
      * ``"rsi"`` — a touch is an RSI(``rsi``) neutral-line (50) cross in the
        trade direction, subject to the same active-leg and cooldown gates.
        Used to test whether entering on the oscillator's earlier reclaim beats
        waiting for the price touch.

    Each event carries the fib leg, the touch bar, the barrier resolution
    (``out`` = 0 broke to target / 1 broke to stop), and the three corner axes
    (``gap``, ``part``, ``vstate``) all evaluated from bars at or before the
    touch. Nothing here reads a bar after ``i`` except the forward barrier
    scan, whose result is the label ``out`` / ``exit_bar`` and is never fed
    back into the entry decision.
    """
    ts = np.asarray(ts, dtype=float)
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float)
    closes = np.asarray(closes, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    taker_buy = np.asarray(taker_buy, dtype=float)

    n = len(closes)
    if n == 0:
        return []

    if gap_ms is None:
        deltas = np.diff(ts)
        typical = float(np.median(deltas)) if deltas.size else float(DEFAULT_GAP_MS)
        gap_ms = max(float(DEFAULT_GAP_MS), 10.0 * typical)

    pivots = causal_zigzag(highs, lows, threshold)
    pivot_px = [p[0] for p in pivots]
    confirm = [p[3] for p in pivots]

    events: list[dict[str, Any]] = []
    last_touch = -(10**9)
    first_usable = (max(pivot_px[:2]) if len(pivot_px) >= 2 else 200) + 6

    for i in range(first_usable, max(first_usable, n - horizon - 1)):
        # Index of the most recent pivot *confirmed* by this bar.
        pi = int(np.searchsorted(confirm, i, side="right")) - 1
        if pi < 1:
            continue

        lo = min(pivots[pi - 1][1], pivots[pi][1])
        hi = max(pivots[pi - 1][1], pivots[pi][1])
        if hi <= lo:
            continue

        levels = np.array([lo + (1 - f) * (hi - lo) for f in FIB_LEVELS])

        if touch_mode == "fib":
            k = int(np.argmin(np.abs(levels - closes[i])))
            if k in (0, len(FIB_LEVELS) - 1):
                continue
            if abs(closes[i] - levels[k]) / closes[i] > TOUCH_TOL:
                continue
            if i - last_touch < cooldown:
                continue
            line = float(levels[k])
            cd = bool(closes[i - 1] > line)
        elif touch_mode == "rsi":
            if rsi is None:
                raise ValueError("touch_mode='rsi' requires the rsi array")
            if not (np.isfinite(rsi[i]) and np.isfinite(rsi[i - 1])):
                continue
            up = rsi[i - 1] <= 50.0 < rsi[i]
            down = rsi[i - 1] >= 50.0 > rsi[i]
            if not (up or down):
                continue
            if i - last_touch < cooldown:
                continue
            k = int(np.argmin(np.abs(levels - closes[i])))
            if k in (0, len(FIB_LEVELS) - 1):
                continue
            line = float(levels[k])
            cd = up
        else:  # pragma: no cover - guarded by callers
            raise ValueError(f"unknown touch_mode {touch_mode!r}")

        last_touch = i

        gap = abs(levels[k + 1] - levels[k])

        # How long price has been loitering within one gap of the line.
        j0 = i
        while j0 > 0 and abs(closes[j0] - line) <= gap:
            if ts[j0] - ts[j0 - 1] > gap_ms:
                break
            j0 -= 1
        if i - j0 < 1 or ts[j0 + 1] - ts[j0] > gap_ms:
            continue

        leg = slice(j0, i + 1)
        leg_range = float(highs[leg].max() - lows[leg].min())
        if leg_range <= 0:
            continue

        vol = volumes[leg]
        delta = np.where(
            vol > 0, 2.0 * taker_buy[leg] / np.maximum(vol, 1e-9) - 1.0, 0.0
        )
        part = float(np.sum(np.abs(delta) * vol)) / leg_range

        window = slice(max(0, i - 60), i + 1)
        vstate = float(highs[window].max() - lows[window].min()) / max(gap, 1e-9)

        break_px = line - ret_fail * gap if cd else line + ret_fail * gap
        target_px = line + ret_break * gap if cd else line - ret_break * gap

        out: int | None = None
        exit_bar: int | None = None
        for j in range(i + 1, min(i + horizon, n)):
            if ts[j] - ts[j - 1] > gap_ms:
                break
            if (closes[j] < break_px) if cd else (closes[j] > break_px):
                out, exit_bar = 1, j
                break
            if (closes[j] > target_px) if cd else (closes[j] < target_px):
                out, exit_bar = 0, j
                break
        if out is None or exit_bar is None:
            continue

        events.append(
            {
                "pi": pi,
                "k": k,
                "i": int(i),
                "exit_bar": int(exit_bar),
                "ts": int(ts[i]),
                "out": out,
                "part": part,
                "gap": float(gap),
                "price": float(closes[i]),
                "cd": int(cd),
                "vstate": vstate,
                "line": line,
                "target": float(target_px),
                "break": float(break_px),
            }
        )
    return events


def annotate_prior(
    events: Iterable[dict[str, Any]], *, max_prior_gap_bars: int = 240
) -> list[dict[str, Any]]:
    """Attach the previous same-level test's axes and outcome to each event.

    ``psame`` is the prior test's ``out``; the corner filter only fires when
    that is 0 (the prior test held). The prior must have *resolved* before this
    touch (``exit_bar < i``) or it is dropped to ``None`` — otherwise the
    filter would read an outcome that was still in the future at the touch.
    """
    events = list(events)
    by_level: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for ev in events:
        by_level.setdefault((ev["pi"], ev["k"]), []).append(ev)

    for ev in events:
        ev["psame"] = None
        ev["ppart"] = float("nan")
        ev["pvstate"] = float("nan")
        ev["pgap"] = float("nan")

    for bucket in by_level.values():
        bucket.sort(key=lambda e: e["i"])
        for j, ev in enumerate(bucket):
            if j == 0:
                continue
            prior = bucket[j - 1]
            if prior["exit_bar"] >= ev["i"]:
                continue  # prior still live at this touch -> not usable
            ev["psame"] = prior["out"]
            ev["ppart"] = prior["part"]
            ev["pvstate"] = prior["vstate"]
            ev["pgap"] = (ev["ts"] - prior["ts"]) / 60000.0
    return events


def participation_cut(
    train_events: list[dict[str, Any]], *, pct: float = 50.0
) -> float:
    """Participation cut point learned on the training slice only."""
    parts = [e["ppart"] for e in train_events if math.isfinite(e["ppart"])]
    return float(np.percentile(parts, pct)) if parts else -math.inf


def vstate_cut(train_events: list[dict[str, Any]], *, pct: float = 50.0) -> float:
    """Narrow-range cut point learned on the training slice only."""
    vstates = [e["pvstate"] for e in train_events if math.isfinite(e["pvstate"])]
    return float(np.percentile(vstates, pct)) if vstates else math.inf


def normalise_participation(events: list[dict[str, Any]], median_part: float) -> None:
    """Rescale ``part``/``ppart`` so the high-participation cut is scale-free."""
    scale = median_part if median_part > 0 else 1.0
    for ev in events:
        ev["part"] /= scale
        if math.isfinite(ev["ppart"]):
            ev["ppart"] /= scale


def train_cuts(
    train_base_events: list[dict[str, Any]],
    train_prior_events: list[dict[str, Any]],
    *,
    pct: float = 50.0,
) -> dict[str, float]:
    """Corner axes cut points learned on the training slice only.

    ``part`` is normalised by the train median (which the caller must apply to
    *every* event via :func:`normalise_participation`, train slice included), so
    the participation percentile here is taken on the same scale the filter
    will later see. The narrow-range cut is scale-free (a ratio) and needs no
    normalisation.
    """
    med_part = (
        float(np.median([e["part"] for e in train_base_events]))
        if train_base_events
        else 1.0
    )
    return {
        "med_part": med_part,
        "part_cut": participation_cut(train_prior_events, pct=pct),
        "vstate_cut": vstate_cut(train_prior_events, pct=pct),
    }


def corner_filter(
    events: Iterable[dict[str, Any]],
    cuts: dict[str, float],
    *,
    max_prior_gap_bars: float = 240.0,
) -> list[dict[str, Any]]:
    """The corner state: held prior test ∧ narrow range ∧ high participation.

    All three reads are of the **prior** test on the same level, and the prior
    must have resolved before this touch (:func:`annotate_prior` guarantees
    that), so nothing here looks forward.
    """
    kept: list[dict[str, Any]] = []
    for ev in events:
        if not math.isfinite(ev["pvstate"]):
            continue
        if ev["psame"] != 0:
            continue
        if ev["pvstate"] >= cuts["vstate_cut"]:
            continue
        if ev["ppart"] < cuts["part_cut"]:
            continue
        if not (ev["pgap"] <= max_prior_gap_bars):
            continue
        kept.append(ev)
    return kept


def simulate_arm(
    events: list[dict[str, Any]],
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    entry_mode: str,
    exit_mode: str,
    maker_rate: float = 0.0002,
    taker_rate: float = 0.0005,
    horizon: int = 180,
) -> list[dict[str, Any]]:
    """Per-trade economics of one execution arm, all causal.

    ``entry_mode`` / ``exit_mode`` are ``"market"`` or ``"limit"``:

    * market entry fills at the next bar's open (taker); limit entry rests on
      the line and fills at the first bar whose range reaches it (maker), at
      the better of line and open — the broker's own LIMIT rule.
    * market exit fills at the open after the barrier bar (taker); limit exit
      rests a take-profit on the target and fills when the target's range is
      reached (maker), falling back to a market exit when the barrier resolves
      first.

    The barrier/target are re-derived from the **entry bar** (not the touch
    bar), so a late limit fill does not get to keep a barrier that was only
    knowable earlier — the causal-clean discipline E264g established. Fees are
    charged on each leg's notional and reported in bp of entry price.

    Because the backtest engine has no order-cancel, a resting limit that never
    fills would linger in its queue forever; this simulator is therefore the
    authoritative limit-fill model, and the engine (see
    :class:`CornerExecutionStrategy`) is used for the market arms only.
    """
    open_ = np.asarray(open_, dtype=float)
    high = np.asarray(high, dtype=float)
    low = np.asarray(low, dtype=float)
    close = np.asarray(close, dtype=float)
    n = len(close)

    trades: list[dict[str, Any]] = []
    for ev in events:
        i = ev["i"]
        line = ev["line"]
        gap = ev["gap"]
        cd = bool(ev["cd"])
        sgn = 1.0 if cd else -1.0

        if entry_mode == "market":
            if i + 1 >= n:
                continue
            entry_bar = i + 1
            entry_px = float(open_[entry_bar])
            entry_fee = taker_rate
        elif entry_mode == "limit":
            entry_bar = -1
            entry_px = 0.0
            for j in range(i + 1, min(i + 1 + horizon, n)):
                if cd and low[j] <= line:
                    entry_bar, entry_px = j, min(line, float(open_[j]))
                    break
                if (not cd) and high[j] >= line:
                    entry_bar, entry_px = j, max(line, float(open_[j]))
                    break
            if entry_bar < 0:
                continue  # order never touched within the horizon
            entry_fee = maker_rate
        else:  # pragma: no cover - guarded by callers
            raise ValueError(f"unknown entry_mode {entry_mode!r}")

        target = line + 0.5 * gap if cd else line - 0.5 * gap
        stop = line - 0.5 * gap if cd else line + 0.5 * gap

        # One forward loop settles the target-vs-stop race for both exit modes.
        # Both barriers are tested on the intrabar range (high/low); when a
        # single bar straddles both, bar data cannot order them, so the stop
        # wins (conservative). Exit-mode "limit" keeps a resting take-profit on
        # the target and fills it at the target (maker); "market" pays taker.
        exit_bar = -1
        exit_px = 0.0
        exit_fee = taker_rate
        for j in range(entry_bar, min(entry_bar + horizon, n)):
            target_hit = (high[j] >= target) if cd else (low[j] <= target)
            stop_hit = (low[j] <= stop) if cd else (high[j] >= stop)
            if stop_hit:
                exit_bar = min(j + 1, n - 1)
                exit_px = float(open_[exit_bar])
                exit_fee = taker_rate
                break
            if target_hit:
                if exit_mode == "limit":
                    exit_bar, exit_px, exit_fee = j, target, maker_rate
                else:
                    exit_bar = min(j + 1, n - 1)
                    exit_px = float(open_[exit_bar])
                    exit_fee = taker_rate
                break
        if exit_bar < 0:
            exit_bar = min(entry_bar + horizon - 1, n - 1)
            exit_px = float(open_[exit_bar])
            exit_fee = taker_rate
        if exit_bar < entry_bar:
            continue

        gross_bp = sgn * (exit_px - entry_px) / entry_px * 1e4
        cost_bp = (entry_fee + exit_fee * exit_px / entry_px) * 1e4
        trades.append(
            {
                "entry_bar": int(entry_bar),
                "exit_bar": int(exit_bar),
                "entry_px": entry_px,
                "exit_px": exit_px,
                "gross_bp": float(gross_bp),
                "cost_bp": float(cost_bp),
                "net_bp": float(gross_bp - cost_bp),
                "gap": float(gap),
                "cd": int(cd),
            }
        )
    return trades


def market_entry_bars(
    trades: list[dict[str, Any]],
) -> tuple[dict[int, OrderSide], dict[int, Any]]:
    """Split a market-arm trade list into ``{bar: side}`` entry / exit maps."""
    entries: dict[int, OrderSide] = {}
    exits: dict[int, Any] = {}
    for tr in trades:
        side = OrderSide.BUY if tr["cd"] else OrderSide.SELL
        entries[tr["entry_bar"]] = side
        exits[tr["exit_bar"]] = True
    return entries, exits


class CornerExecutionStrategy:
    """Replay a market-order corner event set through the real backtest engine.

    Implements the ``StrategyRunner`` protocol (``on_kline`` / ``reset``). One
    position at a time: while a position is held the runner emits nothing, so
    overlapping events resolve to whichever opened first. Entry and exit are
    plain market :class:`Signal`\\ s — the engine fills them at the next bar's
    open, which is exactly the ``entry_mode="market"`` arm of
    :func:`simulate_arm`. Limit arms are not expressible here: the engine has
    no order-cancel, so a resting limit that never fills would linger.
    """

    def __init__(
        self,
        *,
        symbol: str,
        interval: str,
        entries: dict[int, OrderSide],
        exits: dict[int, Any],
        notional: float = 1000.0,
    ) -> None:
        self._symbol = symbol
        self._interval = interval
        self._entries = entries
        self._exits = exits
        self._notional = float(notional)
        self._i = -1
        self._in_position = False
        self._pending = False

    def reset(self) -> None:
        self._i = -1
        self._in_position = False
        self._pending = False

    def on_kline(self, event: KlineEvent) -> Signal | None:
        self._i += 1
        i = self._i

        if i in self._exits and (self._in_position or self._pending):
            side = (
                OrderSide.SELL
                if self._position_side == OrderSide.BUY
                else OrderSide.BUY
            )
            self._in_position = False
            self._pending = False
            return self._emit(event, side)

        if i in self._entries and not self._in_position and not self._pending:
            self._position_side = self._entries[i]
            self._pending = True
            return self._emit(event, self._position_side)

        return None

    def _emit(self, event: KlineEvent, side: OrderSide) -> Signal:
        quantity = self._notional / float(event.close) if event.close > 0 else 0.0
        return Signal(
            strategy_id="fib_corner",
            symbol=self._symbol,
            side=side,
            strength=1.0,
            timestamp=event.timestamp,
            reason="corner",
            quantity=quantity,
        )

    _position_side: OrderSide = OrderSide.BUY
