"""Kline-stream interpreter: turns closed candles plus compiled rules into Signals."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pandas as pd

from selene_space_strategy.compiler import CompiledCondition, CompiledStrategy
from selene_space_strategy.models import RiskParams
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal, StrategyDirection
from selene_space_types.trading import OrderSide

if TYPE_CHECKING:
    from selene_space_strategy.indicators.base import Indicator


@dataclass
class _RuntimeState:
    """Isolated bookkeeping for a single symbol.

    Each symbol seen on the stream gets its own candle buffer and virtual
    position, so interleaved multi-symbol feeds never cross-contaminate.
    """

    bars: list[dict] = field(default_factory=list)
    position: str = "flat"  # "flat" | "long" | "short"
    entry_price: float = 0.0


def _indicator_frame(state_bars: list[dict]) -> pd.DataFrame:
    """Wrap the buffered candles in a fresh DataFrame for indicator math."""
    return pd.DataFrame(state_bars)


def _attach_indicator_columns(
    df: pd.DataFrame,
    indicators: Mapping[str, Indicator],
) -> pd.DataFrame:
    """Materialize every compiled indicator as a DataFrame column.

    Multi-output families (MACD, Bollinger Bands) expose ``calculate_full``
    and populate their three derived columns in one shot; single-output
    indicators assign directly. Columns already present are left untouched.
    """
    for column, indicator in indicators.items():
        if column in df.columns:
            continue
        if hasattr(indicator, "calculate_full") and "macd" in column:
            bundle = indicator.calculate_full(df)
            stem = column
            df[stem] = bundle.macd_line
            df[stem.replace("macd", "macd_signal", 1)] = bundle.signal_line
            df[stem.replace("macd", "macd_histogram", 1)] = bundle.histogram
        elif hasattr(indicator, "calculate_full") and "bbands" in column:
            bundle = indicator.calculate_full(df)
            stem = column
            df[stem] = bundle.middle
            df[stem.replace("bbands", "bbands_upper", 1)] = bundle.upper
            df[stem.replace("bbands", "bbands_middle", 1)] = bundle.middle
            df[stem.replace("bbands", "bbands_lower", 1)] = bundle.lower
        else:
            df[column] = indicator.calculate(df)
    return df


class StrategyRuntime:
    """Evaluate a CompiledStrategy against a live kline stream.

    The runtime buffers closed candles per symbol, recomputes indicator
    columns over that buffer, and fires :class:`Signal` objects whenever the
    compiled entry/exit rule sets are satisfied. Risk brackets (stop-loss /
    take-profit) are checked before rule evaluation so protective exits win
    over discretionary ones on the same bar.

    Args:
        compiled: A CompiledStrategy produced by the compiler.
        max_bars: Candle-buffer ceiling applied per symbol.
        risk_params: Optional bracket overrides; defaults to the compiled
            strategy's parsed risk params.
        direction: Gate deciding whether long and/or short legs are allowed.
    """

    def __init__(
        self,
        compiled: CompiledStrategy,
        max_bars: int = 500,
        risk_params: RiskParams | None = None,
        direction: StrategyDirection = StrategyDirection.LONG_ONLY,
    ) -> None:
        self._compiled = compiled
        self._max_bars = max_bars
        self._states: dict[str, _RuntimeState] = {}
        self._last_symbol: str | None = None  # backward-compat properties read this
        self._risk_params = risk_params or compiled.config.parsed_risk_params
        self._direction = direction

    @property
    def config(self) -> CompiledStrategy:
        """The compiled strategy this runtime interprets."""
        return self._compiled

    @property
    def position(self) -> str:
        """Position of whichever symbol was seen most recently."""
        if self._last_symbol is None:
            return "flat"
        return self._states[self._last_symbol].position

    @property
    def bar_count(self) -> int:
        """Buffered bar count for whichever symbol was seen most recently."""
        if self._last_symbol is None:
            return 0
        return len(self._states[self._last_symbol].bars)

    def _get_state(self, symbol: str) -> _RuntimeState:
        """Return (creating on first touch) the state bucket for ``symbol``."""
        bucket = self._states.get(symbol)
        if bucket is None:
            bucket = _RuntimeState()
            self._states[symbol] = bucket
        self._last_symbol = symbol
        return bucket

    def on_kline(self, event: KlineEvent) -> Signal | None:
        """Consume one kline; return a Signal when an action is warranted.

        Unclosed candles are ignored outright. The very first candle of a
        symbol only seeds the buffer.
        """
        if not event.closed:
            return None

        bucket = self._get_state(event.symbol)
        bucket.bars.append(
            {
                "timestamp": event.timestamp,
                "open": event.open,
                "high": event.high,
                "low": event.low,
                "close": event.close,
                "volume": event.volume,
            }
        )
        overflow = len(bucket.bars) - self._max_bars
        if overflow > 0:
            del bucket.bars[:overflow]

        if len(bucket.bars) < 2:
            return None

        # Protective brackets are pure price checks and take precedence.
        if bucket.position in ("long", "short"):
            bracket_hit = self._check_risk(event, bucket.position, bucket)
            if bracket_hit is not None:
                return bracket_hit

        frame = _attach_indicator_columns(
            _indicator_frame(bucket.bars), self._compiled.indicators
        )

        # Wait until every indicator has a value on the newest row.
        newest = len(frame) - 1
        for column in self._compiled.indicators:
            if pd.isna(frame[column].iloc[newest]):
                return None

        return self._decide(event, frame, bucket)

    # ------------------------------------------------------------------
    # Decision layer
    # ------------------------------------------------------------------

    def _decide(
        self, event: KlineEvent, frame: pd.DataFrame, bucket: _RuntimeState
    ) -> Signal | None:
        """Apply the direction gate and the matching rule set for the position."""
        # ``BOTH`` means both legs; leaving it out of both sets made a strategy
        # that declared it trade nothing at all, which reads as "no signal" and
        # never as a configuration error. ``funding_rate_arb.yaml`` is the
        # shipped template that declares it.
        # ``==`` rather than ``is``: ``StrategyDirection`` is a ``str`` enum and
        # the constructor does not coerce, so a caller that passes the bare
        # string ``"both"`` must still trade both legs. Identity would silently
        # drop it and reproduce the very bug this gate fixes.
        both = self._direction == StrategyDirection.BOTH
        longs_permitted = both or self._direction in (
            StrategyDirection.LONG_ONLY,
            StrategyDirection.NEUTRAL,
        )
        shorts_permitted = both or self._direction in (
            StrategyDirection.SHORT_ONLY,
            StrategyDirection.NEUTRAL,
        )

        if bucket.position == "flat":
            if (
                longs_permitted
                and self._compiled.long_entry_conditions
                and self._all_conditions_met(
                    frame, self._compiled.long_entry_conditions
                )
            ):
                bucket.position = "long"
                bucket.entry_price = event.close
                return self._make_signal(
                    event, OrderSide.BUY, "Long entry conditions met"
                )
            if (
                shorts_permitted
                and self._compiled.short_entry_conditions
                and self._all_conditions_met(
                    frame, self._compiled.short_entry_conditions
                )
            ):
                bucket.position = "short"
                bucket.entry_price = event.close
                return self._make_signal(
                    event, OrderSide.SELL, "Short entry conditions met"
                )
            return None

        if bucket.position == "long":
            if self._compiled.close_long_conditions and self._all_conditions_met(
                frame, self._compiled.close_long_conditions
            ):
                bucket.position = "flat"
                return self._make_signal(
                    event, OrderSide.SELL, "Close long conditions met"
                )
            return None

        if self._compiled.close_short_conditions and self._all_conditions_met(
            frame, self._compiled.close_short_conditions
        ):
            bucket.position = "flat"
            return self._make_signal(event, OrderSide.BUY, "Close short conditions met")
        return None

    def _all_conditions_met(
        self, df: pd.DataFrame, conditions: list[CompiledCondition]
    ) -> bool:
        """True only when every condition in ``conditions`` holds right now."""
        return all(self._evaluate_condition(df, cond) for cond in conditions)

    def _check_risk(
        self,
        event: KlineEvent,
        direction: str,
        state: _RuntimeState,
    ) -> Signal | None:
        """Enforce stop-loss / take-profit brackets for an open position.

        Stops trigger on the intra-bar extreme (low for longs, high for
        shorts — pessimistic fill); targets require the close to clear them.
        """
        rp = self._risk_params
        if rp is None or state.entry_price <= 0:
            return None

        if direction == "long":
            if rp.stop_loss_pct is not None:
                stop_level = state.entry_price * (1 - rp.stop_loss_pct)
                if event.low <= stop_level:
                    state.position = "flat"
                    loss_frac = (stop_level - state.entry_price) / state.entry_price
                    return self._make_signal(
                        event,
                        OrderSide.SELL,
                        f"Stop loss triggered ({loss_frac:.2%}, low={event.low:.2f})",
                    )
            gain_frac = (event.close - state.entry_price) / state.entry_price
            if rp.take_profit_pct is not None and gain_frac >= rp.take_profit_pct:
                state.position = "flat"
                return self._make_signal(
                    event, OrderSide.SELL, f"Take profit triggered ({gain_frac:.2%})"
                )

        elif direction == "short":
            if rp.stop_loss_pct is not None:
                stop_level = state.entry_price * (1 + rp.stop_loss_pct)
                if event.high >= stop_level:
                    state.position = "flat"
                    loss_frac = (state.entry_price - stop_level) / state.entry_price
                    return self._make_signal(
                        event,
                        OrderSide.BUY,
                        f"Stop loss triggered ({loss_frac:.2%}, high={event.high:.2f})",
                    )
            gain_frac = (state.entry_price - event.close) / state.entry_price
            if rp.take_profit_pct is not None and gain_frac >= rp.take_profit_pct:
                state.position = "flat"
                return self._make_signal(
                    event, OrderSide.BUY, f"Take profit triggered ({gain_frac:.2%})"
                )

        return None

    # ------------------------------------------------------------------
    # Condition evaluation
    # ------------------------------------------------------------------

    def _rhs_values(
        self, df: pd.DataFrame, cond: CompiledCondition, last: int
    ) -> tuple[float, float] | None:
        """Resolve the comparator side to (now, prev) values, or None if unusable."""
        if cond.comparator_indicator is not None:
            col = cond.comparator_column_name
            return cast(float, df[col].iloc[last]), cast(float, df[col].iloc[last - 1])
        if cond.comparator_value is not None:
            return cond.comparator_value, cond.comparator_value
        return None

    def _evaluate_condition(self, df: pd.DataFrame, cond: CompiledCondition) -> bool:
        """Dispatch one compiled condition against the latest two rows."""
        last = len(df) - 1
        if last < 1:
            return False

        left_now = cast(float, df[cond.column_name].iloc[last])
        left_prev = cast(float, df[cond.column_name].iloc[last - 1])
        rhs = self._rhs_values(df, cond, last)
        if rhs is None:
            return False
        right_now, right_prev = rhs

        if (
            pd.isna(left_now)
            or pd.isna(left_prev)
            or pd.isna(right_now)
            or pd.isna(right_prev)
        ):
            return False

        op = cond.operator

        if op == "crosses_above":
            return left_prev <= right_prev and left_now > right_now
        if op == "crosses_below":
            return left_prev >= right_prev and left_now < right_now
        if op == "greater_than":
            return left_now > right_now
        if op == "less_than":
            return left_now < right_now
        if op in ("equal", "equals"):
            return left_now == right_now
        if op == "greater_than_or_equal":
            return left_now >= right_now
        if op == "less_than_or_equal":
            return left_now <= right_now

        # Zone/level operators need indicator state or raw price access.
        if op == "zone_active":
            return self._evaluate_zone_active(df, cond, last)
        if op == "exists":
            return left_now > 0
        if op == "near":
            return self._evaluate_near(df, cond, last)
        if op == "within_range":
            return self._evaluate_within_range(df, cond, last)
        if op == "reaches":
            return self._evaluate_reaches(df, cond, last)
        return False

    def _comparator_dict(self, cond: CompiledCondition) -> dict[str, object]:
        """The raw comparator mapping when present, else an empty dict."""
        raw = cond.raw.get("comparator", {})
        return raw if isinstance(raw, dict) else {}

    def _zone_owner(self, cond: CompiledCondition) -> object | None:
        """Indicator instance owning the zone referenced by ``cond``, if any."""
        return self._compiled.indicators.get(cond.column_name)

    def _evaluate_zone_active(
        self, df: pd.DataFrame, cond: CompiledCondition, last: int
    ) -> bool:
        """Price currently sits inside a live FVG/OB zone of the wanted type."""
        indicator = self._zone_owner(cond)
        if indicator is None or not hasattr(indicator, "get_active_zones"):
            return False

        comparator = self._comparator_dict(cond)
        wanted_type = comparator.get("type", "bullish")
        placement = comparator.get("price_position", "within_zone")

        price = cast(float, df["close"].iloc[last])
        for zone in indicator.get_active_zones(price):
            if zone.type != wanted_type:
                continue
            if placement == "within_zone" and zone.lower <= price <= zone.upper:
                return True
            if placement == "above_zone" and price > zone.upper:
                return True
            if placement == "below_zone" and price < zone.lower:
                return True
        return False

    def _evaluate_near(
        self, df: pd.DataFrame, cond: CompiledCondition, last: int
    ) -> bool:
        """Price within a relative tolerance of a target level."""
        price = cast(float, df["close"].iloc[last])
        rhs = self._rhs_values(df, cond, last)
        if rhs is None:
            return False
        target = rhs[0]
        if target == 0:
            return False
        tolerance = cast(float, self._comparator_dict(cond).get("tolerance", 0.1))
        return abs(price - target) / target <= tolerance

    def _evaluate_within_range(
        self, df: pd.DataFrame, cond: CompiledCondition, last: int
    ) -> bool:
        """Price inside the (possibly partial) span of a matching zone."""
        indicator = self._zone_owner(cond)
        if indicator is None:
            return False

        comparator = self._comparator_dict(cond)
        wanted_type = comparator.get("type", "bullish")
        zone_pct = comparator.get("zone_pct", 1.0)

        price = cast(float, df["close"].iloc[last])
        if not hasattr(indicator, "get_active_zones"):
            return False
        for zone in indicator.get_active_zones(price):
            if zone.type != wanted_type:
                continue
            span = zone.upper - zone.lower
            usable_ceiling = zone.lower + span * zone_pct
            if zone.lower <= price <= usable_ceiling:
                return True
        return False

    def _evaluate_reaches(
        self, df: pd.DataFrame, cond: CompiledCondition, last: int
    ) -> bool:
        """Bar wick touched the target level from the appropriate side."""
        close = cast(float, df["close"].iloc[last])
        rhs = self._rhs_values(df, cond, last)
        if rhs is None:
            return False
        target = rhs[0]
        if target > close:
            return cast(float, df["high"].iloc[last]) >= target
        return cast(float, df["low"].iloc[last]) <= target

    def _make_signal(self, event: KlineEvent, side: OrderSide, reason: str) -> Signal:
        """Bundle the event context into an outbound Signal."""
        return Signal(
            strategy_id=self._compiled.config.id,
            symbol=event.symbol,
            side=side,
            strength=1.0,
            timestamp=int(time.time() * 1000),
            reason=reason,
        )

    def reset(self) -> None:
        """Drop every per-symbol bucket (full cold restart)."""
        self._states.clear()
        self._last_symbol = None
