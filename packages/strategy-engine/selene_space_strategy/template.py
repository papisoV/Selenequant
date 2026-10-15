"""Bar-by-bar backtesting scaffold built on the Template Method pattern.

A concrete strategy decides *what* to do on each closed bar by implementing
:meth:`BacktestStrategyTemplate.get_signal`; this base class turns those
decisions into Signals (sizing, cooldown, ATR protective brackets) and
delegates execution to :class:`selene_space_backtest.engine.BacktestEngine`,
whose loop fills at the NEXT bar's open — no same-bar-close lookahead.

The template keeps a lightweight shadow book (position/capital/Kelly history)
so ``get_signal`` implementations can inspect ``self._position`` and Kelly
sizing keeps working; authoritative fills, costs, curves, and metrics all
come from the engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from selene_space_backtest.commissions import PercentageCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import FixedSlippage
from selene_space_strategy.indicators.mixin import IndicatorMixIn
from selene_space_types.market import KlineEvent
from selene_space_types.routing import MarketDomain
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

_MS_PER_DAY = 86_400_000.0


class _ApyCarryModel:
    """Legacy-parity carry: ``funding_rate_apy`` charged pro-rata per bar.

    Always a cost to the holder regardless of side, matching the old
    in-loop deduction the engine now owns.
    """

    def __init__(self, apy: float, periods_per_year: int) -> None:
        self._charge = apy / max(1, periods_per_year)

    def apply_funding(
        self, position_notional: float, position_side: str, timestamp: int
    ) -> float:
        return position_notional * self._charge


class SignalType(Enum):
    """Action vocabulary the backtest loop understands.

    BUY opens a long (long-only mode) or a long from flat (two-sided mode);
    SELL closes a long, or opens a short when two-sided trading is enabled;
    CLOSE_LONG / CLOSE_SHORT flatten explicitly; HOLD does nothing.
    """

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"


@dataclass
class BacktestResult:
    """Everything a backtest run reports: curves, fills, and summary stats."""

    signals: list[Signal] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    drawdown_curve: list[float] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)

    # Metrics
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0

    # Extended
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    expectancy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Flatten to a JSON-friendly dictionary."""
        return {
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "total_trades": self.total_trades,
            "calmar_ratio": self.calmar_ratio,
            "sortino_ratio": self.sortino_ratio,
            "expectancy": self.expectancy,
            "equity_curve": self.equity_curve,
            "trades": self.trades,
        }


class BacktestStrategyTemplate(IndicatorMixIn, ABC):
    """Base class for strategies that reduce to one decision per bar.

    Subclasses implement :meth:`get_signal` and may tune sizing/stops through
    constructor options; the full simulate-record-report pipeline lives here::

        class MyRSIStrategy(BacktestStrategyTemplate):
            def get_signal(self, data: pd.DataFrame) -> SignalType:
                rsi = self.rsi(data, period=14).series()
                if rsi.iloc[-1] < 30:
                    return SignalType.BUY
                if rsi.iloc[-1] > 70:
                    return SignalType.SELL
                return SignalType.HOLD

        result = MyRSIStrategy(symbol="BTC/USDT").run_backtest(events)
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        # Position sizing
        position_size_pct: float = 1.0,
        max_position_pct: float = 1.0,
        use_kelly_sizing: bool = False,
        kelly_win_rate: float = 0.5,
        kelly_win_loss_ratio: float = 2.0,
        # Signal cooldown
        cooldown_bars: int = 0,
        # ATR dynamic stops
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        use_trailing_stop: bool = False,
        trailing_atr_mult: float = 2.5,
        # ATR three-layer engine
        atr_initial_mult: float = 1.5,
        atr_breakeven_mult: float = 2.0,
        # Trading cost realism (Phase 1)
        slippage_bps: float = 1.5,
        taker_fee_rate: float | None = None,
        funding_rate_apy: float = 0.0,
    ) -> None:
        """See subclass-facing option groups above (sizing / cooldown / stops / costs)."""
        self.symbol = symbol
        self.initial_capital = initial_capital
        self.leverage = leverage
        self.fee_rate = fee_rate
        self.max_bars = max_bars
        self.long_only = long_only

        # Cost model knobs. ``taker_fee_rate`` wins over ``fee_rate`` when set;
        # slippage is charged per fill side in basis points.
        self.slippage_bps = float(slippage_bps)
        self.taker_fee_rate = (
            float(taker_fee_rate) if taker_fee_rate is not None else None
        )
        self.funding_rate_apy = float(funding_rate_apy)

        # Sizing
        self._position_size_pct = position_size_pct
        self._max_position_pct = max_position_pct
        self._use_kelly_sizing = use_kelly_sizing
        self._kelly_win_rate = kelly_win_rate
        self._kelly_win_loss_ratio = kelly_win_loss_ratio
        self._trade_history_for_kelly: list[dict[str, Any]] = []

        # Cooldown bookkeeping
        self._cooldown_bars = cooldown_bars
        self._last_signal_bar: int = -999999
        self._bar_index: int = 0

        # Rolling market data + account state
        self._bars: list[dict] = []
        self._position: str = "flat"  # "flat" | "long" | "short"
        self._entry_price: float = 0.0
        self._entry_time: int = 0
        self._capital: float = initial_capital
        self._equity: float = initial_capital
        self._position_fraction_at_entry: float = 1.0
        # Shadow fills deferred to the NEXT bar's open, mirroring the
        # engine's queued-order execution (see on_kline).
        self._pending_fills: list[tuple[str, str]] = []

        # Protective-exit configuration
        self._use_atr_stops = use_atr_stops
        self._atr_stop_period = atr_stop_period
        self._atr_sl_mult = atr_sl_mult
        self._atr_tp_mult = atr_tp_mult
        self._use_trailing_stop = use_trailing_stop
        self._trailing_atr_mult = trailing_atr_mult
        self._atr_initial_mult = atr_initial_mult
        self._atr_breakeven_mult = atr_breakeven_mult
        self._atr_stop_layer: int = 0
        self._atr_sl_price: float = 0.0
        self._atr_tp_price: float = 0.0
        self._trailing_stop_price: float = 0.0
        self._highest_since_entry: float = 0.0
        self._lowest_since_entry: float = float("inf")

        # Outputs
        self._signals: list[Signal] = []
        self._equity_curve: list[float] = []
        self._trades: list[dict[str, Any]] = []

    @abstractmethod
    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Decide the intended action for the latest bar of ``data``."""
        ...

    def get_signal_strength(self, data: pd.DataFrame, signal_type: SignalType) -> float:
        """Conviction of the emitted signal in [0, 1]; override for dynamics."""
        return 1.0

    _FEATURE_SCHEMA = 1

    def _decision_features(self, data: pd.DataFrame) -> dict[str, Any]:
        """Point-in-time snapshot of decision context for the latest bar.

        Captured on entry signals only, riding the Signal through to the
        trade record so completed trades can later be attributed to market
        state without replaying data. Everything here reads only the closed
        bars handed in — callers guarantee no future rows exist.

        Override to add strategy-specific features; call ``super()`` and
        merge to keep the shared baseline. Values must be JSON-serializable;
        ``None`` marks a not-yet-computable feature (warm-up window).
        """
        features: dict[str, Any] = {"feat_schema": self._FEATURE_SCHEMA}
        closes = data["close"].astype(float)

        window = closes.iloc[-120:]
        if len(closes) >= 21:
            features["deg_ang_21"] = self._regress_angle(closes.iloc[-21:])
        else:
            features["deg_ang_21"] = None
        features["deg_ang_60"] = (
            self._regress_angle(closes.iloc[-60:]) if len(closes) >= 10 else None
        )
        features["price_rank_60"] = self._rolling_rank(closes, 60)
        features["price_rank_120"] = self._rolling_rank(closes, 120)

        atr_w = self.atr(data, period=14)
        atr_val = atr_w.last()
        close = closes.iloc[-1]
        features["atr14_pct"] = (
            atr_val / close if atr_val is not None and close > 0 else None
        )
        if atr_val is not None and close > 0:
            hist = atr_w.series.iloc[-120:].dropna()
            features["atr_pctile_120"] = (
                float((hist < atr_val).mean()) if len(hist) >= 10 else None
            )
        else:
            features["atr_pctile_120"] = None

        if len(closes) >= 30:
            ret = closes.pct_change()
            vol_short = ret.iloc[-5:].std(ddof=0)
            vol_long = ret.iloc[-30:].std(ddof=0)
            features["vol_ratio"] = (
                float(vol_short / vol_long) if vol_long and vol_long > 0 else None
            )
        else:
            features["vol_ratio"] = None

        if len(window) >= 2:
            features["dist_high_120"] = float(close / window.max() - 1.0)
            features["dist_low_120"] = float(close / window.min() - 1.0)
        else:
            features["dist_high_120"] = None
            features["dist_low_120"] = None
        return features

    @staticmethod
    def _regress_angle(window: pd.Series) -> float:
        """Slope of the OLS fit over log prices, expressed in degrees."""
        log_p = np.log(window.to_numpy(dtype=float))
        x = np.arange(len(log_p), dtype=float)
        slope = float(np.polyfit(x, log_p, 1)[0])
        return float(np.degrees(np.arctan(slope)))

    @staticmethod
    def _rolling_rank(closes: pd.Series, period: int) -> float | None:
        """Close's percentile rank (0-1) inside the last ``period`` bars."""
        window = closes.iloc[-period:]
        if len(window) < 2:
            return None
        return float((window < window.iloc[-1]).mean())

    def get_parameters(self) -> dict[str, Any]:
        """Expose constructor-relevant parameters for logs and UIs."""
        return {
            "symbol": self.symbol,
            "initial_capital": self.initial_capital,
            "leverage": self.leverage,
            "fee_rate": self.fee_rate,
            "long_only": self.long_only,
            "position_size_pct": self._position_size_pct,
            "use_kelly_sizing": self._use_kelly_sizing,
            "cooldown_bars": self._cooldown_bars,
            "use_atr_stops": self._use_atr_stops,
            "atr_stop_period": self._atr_stop_period,
            "atr_sl_mult": self._atr_sl_mult,
            "atr_tp_mult": self._atr_tp_mult,
            "use_trailing_stop": self._use_trailing_stop,
            "trailing_atr_mult": self._trailing_atr_mult,
            "slippage_bps": self.slippage_bps,
            "taker_fee_rate": self.taker_fee_rate,
            "funding_rate_apy": self.funding_rate_apy,
        }

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def _kelly_size(self) -> float:
        """Half-Kelly fraction estimated from realized trade history.

        Falls back to the configured priors until 10 trades have accumulated.
        """
        history = self._trade_history_for_kelly
        if len(history) >= 10:
            winner_pnls = [t["pnl"] for t in history if t["pnl"] > 0]
            loser_pnls = [t["pnl"] for t in history if t["pnl"] <= 0]
            p = len(winner_pnls) / len(history)
            if winner_pnls and loser_pnls:
                mean_win = sum(winner_pnls) / len(winner_pnls)
                mean_loss = abs(sum(loser_pnls)) / len(loser_pnls)
                b = (
                    mean_win / mean_loss
                    if mean_loss > 0
                    else self._kelly_win_loss_ratio
                )
            else:
                p = self._kelly_win_rate
                b = self._kelly_win_loss_ratio
        else:
            p = self._kelly_win_rate
            b = self._kelly_win_loss_ratio
        edge = (p * b - (1 - p)) / b if b > 0 else 0.0
        return max(0.0, edge * 0.5)  # half-Kelly cushion

    def _compute_position_fraction(self) -> float:
        """Capital fraction for the next deployment, capped by the hard limit."""
        if self._use_kelly_sizing:
            size = self._kelly_size() * self._position_size_pct
        else:
            size = self._position_size_pct
        return min(size, self._max_position_pct)

    # ------------------------------------------------------------------
    # Costs
    # ------------------------------------------------------------------

    def _effective_fee_rate(self) -> float:
        """Commission rate applied to PnL: explicit taker rate, else ``fee_rate``."""
        return self.taker_fee_rate if self.taker_fee_rate is not None else self.fee_rate

    def _calculate_pnl(
        self, exit_price: float, direction: str, exit_ts: float | None = None
    ) -> float:
        """Realize a round trip into ``_capital`` and return the net PnL.

        Cost model per fill: adverse slippage of ``slippage_bps`` basis points
        plus commission on both legs' notionals. When ``funding_rate_apy`` is
        active, a pro-rata carry charge accrues over the holding window.

        Note: the notional legs intentionally reuse the long-side slippage
        orientation regardless of direction (historical convention kept for
        numerical continuity).
        """
        if self._entry_price <= 0:
            return 0.0

        frac = self._position_fraction_at_entry
        qty = (self._capital * frac) / self._entry_price
        slip = self.slippage_bps / 10_000.0

        if direction == "long":
            pay_price = self._entry_price * (1.0 + slip)
            recv_price = exit_price * (1.0 - slip)
        else:
            pay_price = exit_price * (1.0 + slip)
            recv_price = self._entry_price * (1.0 - slip)
        gross = (recv_price - pay_price) * qty * self.leverage

        entry_leg = self._entry_price * (1.0 + slip) * qty
        exit_leg = exit_price * (1.0 - slip) * qty
        commission = (entry_leg + exit_leg) * self._effective_fee_rate()

        carry = 0.0
        if self.funding_rate_apy > 0 and exit_ts is not None:
            held_days = max(0.0, (exit_ts - self._entry_time) / _MS_PER_DAY)
            carry = entry_leg * self.funding_rate_apy * (held_days / 365.0)

        net = gross - commission - carry
        self._capital += net
        return net

    def _record_trade(
        self,
        price: float,
        timestamp: int,
        action: str,
        pnl: float,
    ) -> None:
        """Append one completed round trip to the trade log."""
        entry = {
            "entry_price": self._entry_price,
            "exit_price": price,
            "entry_time": self._entry_time,
            "exit_time": timestamp,
            "action": action,
            "pnl": pnl,
            "capital_after": self._capital,
        }
        self._trades.append(entry)
        self._trade_history_for_kelly.append(entry)

    # ------------------------------------------------------------------
    # Protective exits
    # ------------------------------------------------------------------

    @staticmethod
    def _tail_atr(rows: list[dict], period: int) -> float | None:
        """Last ATR value over the trailing ``2 * period`` rows, or None if undefined."""
        from selene_space_strategy.indicators.atr import ATR

        series = ATR(period).calculate(pd.DataFrame(rows[-period * 2 :]))
        tip = series.iloc[-1]
        return None if pd.isna(tip) else float(tip)

    def _calculate_atr_stop_levels(
        self, entry_price: float, direction: str
    ) -> tuple[float, float]:
        """Volatility-scaled (stop, target) pair with a three-layer stop ratchet.

        Layers: 1 = initial band around entry, 2 = ratchets only toward profit,
        3 = locked to breakeven once favorable excursion covers its threshold.
        With insufficient bar history the pair degrades to fixed +/-2%/4%
        brackets around entry.
        """
        from selene_space_strategy.indicators.atr import ATR

        if len(self._bars) < self._atr_stop_period:
            if direction == "long":
                return entry_price * 0.98, entry_price * 1.04
            return entry_price * 1.02, entry_price * 0.96

        frame = pd.DataFrame(self._bars[-self._atr_stop_period * 2 :])
        atr_tip = ATR(self._atr_stop_period).calculate(frame).iloc[-1]
        atr_val = (
            entry_price * 0.01 if pd.isna(atr_tip) or atr_tip <= 0 else float(atr_tip)
        )

        if direction == "long":
            tp_price = entry_price + atr_val * self._atr_tp_mult
            opening_stop = entry_price - self._atr_initial_mult * atr_val
            extreme = (
                self._highest_since_entry
                if self._highest_since_entry > 0
                else entry_price
            )
            excursion = extreme - entry_price
            if excursion >= self._atr_breakeven_mult * atr_val:
                self._atr_stop_layer = 3
                sl_price = entry_price
            elif self._atr_sl_price > 0:
                ratcheted = extreme - self._atr_initial_mult * atr_val
                sl_price = max(self._atr_sl_price, ratcheted)
                self._atr_stop_layer = 2 if sl_price > opening_stop else 1
            else:
                sl_price = opening_stop
                self._atr_stop_layer = 1
        else:
            tp_price = entry_price - atr_val * self._atr_tp_mult
            opening_stop = entry_price + self._atr_initial_mult * atr_val
            extreme = (
                self._lowest_since_entry
                if self._lowest_since_entry < float("inf")
                else entry_price
            )
            excursion = entry_price - extreme
            if excursion >= self._atr_breakeven_mult * atr_val:
                self._atr_stop_layer = 3
                sl_price = entry_price
            elif self._atr_sl_price > 0:
                ratcheted = extreme + self._atr_initial_mult * atr_val
                sl_price = min(self._atr_sl_price, ratcheted)
                self._atr_stop_layer = 2 if sl_price < opening_stop else 1
            else:
                sl_price = opening_stop
                self._atr_stop_layer = 1

        return sl_price, tp_price

    def _disarm_protective_state(self) -> None:
        """Clear every bracket/tracker left over from a closed position."""
        self._atr_sl_price = 0.0
        self._atr_tp_price = 0.0
        self._atr_stop_layer = 0
        self._trailing_stop_price = 0.0
        self._highest_since_entry = 0.0
        self._lowest_since_entry = float("inf")

    def _protective_exit_reason(self, event: KlineEvent) -> str | None:
        """Bracket verdict for an open position, evaluated on bar close.

        Precedence per side: hard ATR stop, then ATR target, then trail.
        """
        px = event.close
        if self._position == "long":
            if self._atr_sl_price > 0 and px <= self._atr_sl_price:
                return "ATR stop loss"
            if self._atr_tp_price > 0 and px >= self._atr_tp_price:
                return "ATR take profit"
            if (
                self._use_trailing_stop
                and self._trailing_stop_price > 0
                and px <= self._trailing_stop_price
            ):
                return "Trailing stop"
        elif self._position == "short":
            if self._atr_sl_price > 0 and px >= self._atr_sl_price:
                return "ATR stop loss"
            if self._atr_tp_price > 0 and px <= self._atr_tp_price:
                return "ATR take profit"
            if (
                self._use_trailing_stop
                and self._trailing_stop_price > 0
                and px >= self._trailing_stop_price
            ):
                return "Trailing stop"
        return None

    # ------------------------------------------------------------------
    # Live (event-at-a-time) interface
    # ------------------------------------------------------------------

    def on_kline(self, event: KlineEvent) -> Signal | None:
        """Engine-facing per-bar hook: shadow-book upkeep plus one decision.

        Order mirrors the legacy loop exactly — mark-to-market on history up
        to the previous bar, buffer the bar, honor protective brackets first
        (an exit consumes the bar), otherwise consult :meth:`get_signal`.
        Emitted orders fill at the NEXT bar's open inside the engine.
        """
        if not event.closed:
            return None

        for kind, direction in self._pending_fills:
            if kind == "open":
                self._arm_position(
                    direction,
                    event.open,
                    event.timestamp,
                    self._compute_position_fraction(),
                )
            else:
                self._settle_position(direction, event.open, event.timestamp)
        self._pending_fills.clear()

        self._update_equity(event.close)

        self._bar_index += 1
        self._bars.append(
            {
                "timestamp": event.timestamp,
                "open": event.open,
                "high": event.high,
                "low": event.low,
                "close": event.close,
                "volume": event.volume,
            }
        )
        if len(self._bars) > self.max_bars:
            del self._bars[: len(self._bars) - self.max_bars]

        if len(self._bars) < 2:
            return None

        if self._position != "flat" and self._use_atr_stops:
            verdict = self._protective_exit_reason(event)
            if verdict is not None:
                direction = self._position
                self._pending_fills.append(("close", direction))
                self._position = "flat"
                self._entry_price = 0.0
                self._disarm_protective_state()
                return self._emit(
                    Signal(
                        strategy_id=f"template-{self.symbol}",
                        symbol=self.symbol,
                        side=OrderSide.SELL if direction == "long" else OrderSide.BUY,
                        strength=1.0,
                        timestamp=event.timestamp,
                        reason=verdict,
                    )
                )

        decision = self.get_signal(pd.DataFrame(self._bars))
        signal = self._process_signal(event, decision)
        return self._emit(signal)

    def _emit(self, signal: Signal | None) -> Signal | None:
        """Record an outbound signal for the final report, then pass it through."""
        if signal is not None:
            self._signals.append(signal)
        return signal

    # ------------------------------------------------------------------
    # Batch backtest
    # ------------------------------------------------------------------

    def _prepare_fresh_run(self) -> None:
        """Zero every piece of mutable run state (keeps sizing config intact)."""
        self._bars.clear()
        self._position = "flat"
        self._entry_price = 0.0
        self._capital = self.initial_capital
        self._equity = self.initial_capital
        self._signals.clear()
        self._equity_curve.clear()
        self._trades.clear()
        self._trade_history_for_kelly.clear()
        self._bar_index = 0
        self._last_signal_bar = -999999
        self._pending_fills.clear()
        self._disarm_protective_state()

    def run_backtest(self, events: list[KlineEvent]) -> BacktestResult:
        """Simulate the event stream via :class:`BacktestEngine` and report.

        This template is a pure signal source: the engine owns fills (next
        bar open), costs, curves, and metrics. The strategy's own sizing /
        leverage intent travels through ``Signal.quantity``; its shadow
        book stays in sync for ``get_signal`` implementations that inspect
        position state.
        """
        self._prepare_fresh_run()

        closed = [e for e in events if e.closed]
        if not closed:
            return BacktestResult()

        engine = BacktestEngine(
            BacktestConfig(
                initial_cash=self.initial_capital,
                commission=PercentageCommission(rate=self._effective_fee_rate()),
                slippage=FixedSlippage(bps=self.slippage_bps),
                position_sizing="signal_quantity",
                symbol=self.symbol,
                interval=events[0].interval if events else "",
                funding_model=(
                    _ApyCarryModel(
                        self.funding_rate_apy,
                        self._periods_per_year(events, self.symbol),
                    )
                    if self.funding_rate_apy > 0
                    else None
                ),
                lookahead_guard=False,
            )
        )
        engine_result = engine.run(self, events)
        return self._translate(engine_result)

    @staticmethod
    def _periods_per_year(events: list[KlineEvent], symbol: str = "") -> int:
        from selene_space_backtest.metrics import infer_annualization_factor
        from selene_space_types.calendars import (
            annualization_factor,
            resolve_market_domain,
        )

        if not events:
            return 365
        first = events[0]
        domain = resolve_market_domain(
            first.market_type, symbol or first.symbol, first.exchange
        )
        if domain is MarketDomain.CRYPTO:
            return int(infer_annualization_factor(first.interval))
        return int(annualization_factor(domain, first.interval))

    def _translate(self, result: Any) -> BacktestResult:
        """Pydantic engine report → flat dataclass, preserving every legacy key."""
        metrics = result.metrics
        flat = BacktestResult(
            signals=self._signals,
            equity_curve=[float(p) for p in result.equity_curve],
            trades=[
                {
                    "entry_price": float(t["entry_price"]),
                    "exit_price": float(t["exit_price"]),
                    "entry_time": t["entry_time"],
                    "exit_time": t["exit_time"],
                    "action": f"close_{t['side']}",
                    "pnl": float(t["pnl"]),
                }
                for t in result.trades
            ],
            total_trades=result.trades_count,
            total_return=float(metrics.total_return),
            annual_return=float(metrics.annual_return),
            sharpe_ratio=float(metrics.sharpe_ratio),
            max_drawdown=float(metrics.max_drawdown),
            win_rate=float(metrics.win_rate),
            profit_factor=float(metrics.profit_factor),
            calmar_ratio=float(getattr(metrics, "calmar_ratio", 0.0)),
            sortino_ratio=float(getattr(metrics, "sortino_ratio", 0.0)),
            expectancy=float(getattr(metrics, "expectancy", 0.0)),
        )

        curve = flat.equity_curve
        peak = self.initial_capital
        deepest = 0.0
        for point in curve:
            if point > peak:
                peak = point
            dip = (peak - point) / peak
            flat.drawdown_curve.append(-dip)
            if dip > deepest:
                deepest = dip
        flat.max_drawdown = -deepest

        return flat

    # ------------------------------------------------------------------
    # Signal execution
    # ------------------------------------------------------------------

    def _cooldown_blocks(self, signal_type: SignalType) -> bool:
        """True when an entry-style signal lands inside the quiet window."""
        if self._cooldown_bars <= 0:
            return False
        if signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            return False
        return (self._bar_index - self._last_signal_bar) < self._cooldown_bars

    def _resolve_intent(
        self, signal_type: SignalType
    ) -> tuple[str, OrderSide, str] | None:
        """Map (signal, mode, current position) to a fillable intent.

        Returns ``(kind, order_side, reason)`` where ``kind`` is one of
        enter_long / enter_short / exit_long / exit_short, or None when the
        combination is a no-op.
        """
        if self.long_only:
            if signal_type is SignalType.BUY and self._position == "flat":
                return ("enter_long", OrderSide.BUY, "Long entry")
            if (
                signal_type in (SignalType.SELL, SignalType.CLOSE_LONG)
                and self._position == "long"
            ):
                return ("exit_long", OrderSide.SELL, "Close long")
            return None

        if signal_type is SignalType.BUY:
            if self._position == "flat":
                return ("enter_long", OrderSide.BUY, "Long entry")
            if self._position == "short":
                return ("exit_short", OrderSide.BUY, "Close short")
        elif signal_type is SignalType.SELL:
            if self._position == "flat":
                return ("enter_short", OrderSide.SELL, "Short entry")
            if self._position == "long":
                return ("exit_long", OrderSide.SELL, "Close long")
        elif signal_type is SignalType.CLOSE_LONG and self._position == "long":
            return ("exit_long", OrderSide.SELL, "Close long")
        elif signal_type is SignalType.CLOSE_SHORT and self._position == "short":
            return ("exit_short", OrderSide.BUY, "Close short")
        return None

    def _arm_position(
        self, direction: str, price: float, opened_at: int, fraction: float
    ) -> None:
        """Open a position and, when enabled, install its protective brackets."""
        self._position = direction
        self._entry_price = price
        self._entry_time = opened_at
        self._position_fraction_at_entry = fraction
        if self._use_atr_stops:
            self._atr_sl_price, self._atr_tp_price = self._calculate_atr_stop_levels(
                price, direction
            )
            self._highest_since_entry = price
            self._lowest_since_entry = price
            self._trailing_stop_price = 0.0

    def _settle_position(self, direction: str, price: float, closed_at: int) -> None:
        """Flatten via ``_calculate_pnl``, log the round trip, clear brackets."""
        realized = self._calculate_pnl(price, direction, exit_ts=closed_at)
        self._record_trade(price, closed_at, f"close_{direction}", realized)
        self._disarm_protective_state()
        self._position = "flat"
        self._entry_price = 0.0

    def _process_signal(
        self, event: KlineEvent, signal_type: SignalType
    ) -> Signal | None:
        """Turn an intended action into state mutation plus an outbound Signal."""
        if signal_type is SignalType.HOLD:
            return None
        if self._cooldown_blocks(signal_type):
            return None

        planned = self._resolve_intent(signal_type)
        if planned is None:
            return None

        kind, side, reason = planned
        price = event.close

        if kind == "enter_long":
            self._pending_fills.append(("open", "long"))
            self._position = "long"
        elif kind == "enter_short":
            self._pending_fills.append(("open", "short"))
            self._position = "short"
        else:
            direction = "long" if kind == "exit_long" else "short"
            self._pending_fills.append(("close", direction))
            self._position = "flat"

        self._last_signal_bar = self._bar_index

        frame = pd.DataFrame(self._bars) if self._bars else pd.DataFrame()
        strength = max(0.0, min(1.0, self.get_signal_strength(frame, signal_type)))

        features = None
        if kind in ("enter_long", "enter_short"):
            try:
                features = self._decision_features(frame)
            except Exception:  # snapshot is forensic metadata; never block the trade
                features = None

        fill_fraction = self._compute_position_fraction()
        notional = self._capital * fill_fraction * self.leverage if price > 0 else 0.0
        quantity = notional / price if price > 0 else None

        return Signal(
            strategy_id=f"template-{self.symbol}",
            symbol=self.symbol,
            side=side,
            strength=strength,
            timestamp=event.timestamp,
            reason=reason,
            quantity=quantity,
            features=features,
        )

    # ------------------------------------------------------------------
    # Mark-to-market
    # ------------------------------------------------------------------

    def _update_equity(self, mark_price: float) -> None:
        """Revalue the open position and refresh trail/extremum trackers.

        Called before the current bar joins the buffer, so volatility lookups
        here deliberately see history up to the *previous* bar.
        """
        frac = self._position_fraction_at_entry
        if self._position == "flat":
            self._equity = self._capital
        else:
            drift = (mark_price - self._entry_price) / self._entry_price
            if self._position == "short":
                drift = -drift
            self._equity = self._capital * (1 + drift * self.leverage * frac)

        if self._position == "long":
            self._highest_since_entry = max(self._highest_since_entry, mark_price)
        elif self._position == "short":
            self._lowest_since_entry = min(self._lowest_since_entry, mark_price)

        if self._position == "flat" or not self._use_trailing_stop or not self._bars:
            return

        atr_now = self._tail_atr(self._bars, self._atr_stop_period)
        if atr_now is None or atr_now <= 0:
            return

        if self._position == "long":
            candidate = self._highest_since_entry - atr_now * self._trailing_atr_mult
            self._trailing_stop_price = max(self._trailing_stop_price, candidate)
        elif self._position == "short":
            candidate = self._lowest_since_entry + atr_now * self._trailing_atr_mult
            if self._trailing_stop_price > 0:
                self._trailing_stop_price = min(self._trailing_stop_price, candidate)
            else:
                self._trailing_stop_price = candidate

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _calculate_metrics(self) -> BacktestResult:
        """Summarize the run: returns, drawdowns, trade quality, risk ratios."""
        report = BacktestResult(
            signals=self._signals,
            equity_curve=self._equity_curve,
            trades=self._trades,
            total_trades=len(self._trades),
        )

        curve = self._equity_curve
        if len(curve) < 2:
            return report

        report.total_return = (curve[-1] - self.initial_capital) / self.initial_capital

        if self._trades:
            elapsed_days = max(
                1,
                (self._trades[-1]["exit_time"] - self._trades[0]["entry_time"])
                / (1000 * 60 * 60 * 24),
            )
            report.annual_return = report.total_return * (365 / elapsed_days)

        peak = self.initial_capital
        deepest = 0.0
        for point in curve:
            if point > peak:
                peak = point
            dip = (peak - point) / peak
            report.drawdown_curve.append(-dip)
            if dip > deepest:
                deepest = dip
        report.max_drawdown = -deepest

        winners = [t for t in self._trades if t["pnl"] > 0]
        losers = [t for t in self._trades if t["pnl"] <= 0]
        report.win_rate = len(winners) / len(self._trades) if self._trades else 0.0

        gross_win = sum(t["pnl"] for t in winners)
        gross_loss = abs(sum(t["pnl"] for t in losers))
        if gross_loss > 0:
            report.profit_factor = gross_win / gross_loss
        elif gross_win > 0:
            report.profit_factor = float("inf")
        else:
            report.profit_factor = 0.0

        bar_returns = pd.Series(curve).pct_change().dropna()
        if bar_returns.std() > 0:
            report.sharpe_ratio = bar_returns.mean() / bar_returns.std() * (252**0.5)

        if report.max_drawdown != 0:
            report.calmar_ratio = report.annual_return / abs(report.max_drawdown)
        if self._trades:
            mean_win = gross_win / len(winners) if winners else 0
            mean_loss = gross_loss / len(losers) if losers else 0
            report.expectancy = (
                report.win_rate * mean_win - (1 - report.win_rate) * mean_loss
            )

        return report

    def reset(self) -> None:
        """Return the strategy to its just-constructed state."""
        self._bars.clear()
        self._position = "flat"
        self._entry_price = 0.0
        self._position_fraction_at_entry = 1.0
        self._capital = self.initial_capital
        self._equity = self.initial_capital
        self._signals.clear()
        self._equity_curve.clear()
        self._trades.clear()
        self._trade_history_for_kelly.clear()
        self._bar_index = 0
        self._last_signal_bar = -999999
        self._disarm_protective_state()
