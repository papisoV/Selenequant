"""Multi-strategy ensemble backtest — lightweight voting system for preset strategies.

Runs multiple BacktestStrategyTemplate strategies on the same data,
collects their signals per bar, and fuses via weighted voting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate
from selene_space_types.market import KlineEvent
from selene_space_types.trading import OrderSide


@dataclass
class EnsembleBacktestResult:
    """Result from ensemble backtest."""
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    total_trades: int = 0
    calmar_ratio: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)
    # Ensemble-specific
    signal_agreements: int = 0  # How many times strategies agreed
    signal_disagreements: int = 0  # How many times strategies disagreed
    avg_agreement_ratio: float = 0.0  # Average agreement ratio per signal


class StrategyEnsembleBacktest:
    """Lightweight ensemble backtest for preset strategies.

    Runs multiple strategies on the same data, fuses signals via weighted voting,
    and tracks combined portfolio performance.

    Fusion logic:
    - Each strategy emits BUY/SELL/HOLD per bar
    - Weighted vote: sum(weights[i] for strategies saying BUY) vs sum for SELL
    - If buy_weight > sell_weight + threshold → BUY
    - If sell_weight > buy_weight + threshold → SELL
    - Otherwise → HOLD
    """

    def __init__(
        self,
        strategies: dict[str, BacktestStrategyTemplate],
        weights: dict[str, float] | None = None,
        min_agreement: int = 2,
        position_size_pct: float = 0.3,
        fee_rate: float = 0.0005,
        leverage: int = 1,
        long_only: bool = True,
    ) -> None:
        """Initialize ensemble.

        Args:
            strategies: Dict of strategy_name -> strategy_instance.
            weights: Optional weight per strategy (default: equal weight).
            min_agreement: Minimum number of strategies that must agree to trade.
            position_size_pct: Fraction of capital per trade.
            fee_rate: Trading fee rate.
            leverage: Leverage multiplier.
            long_only: If True, SELL signals close long positions (no shorting).
        """
        self.strategies = strategies
        n = len(strategies)
        self.weights = weights or {name: 1.0 / n for name in strategies}
        self.min_agreement = min_agreement
        self.position_size_pct = position_size_pct
        self.fee_rate = fee_rate
        self.leverage = leverage
        self.long_only = long_only

    def run_backtest(
        self,
        events: list[KlineEvent],
        initial_capital: float = 100000.0,
    ) -> EnsembleBacktestResult:
        """Run ensemble backtest on historical data.

        Each strategy processes every bar independently. The ensemble
        fuses their signals and manages a single combined portfolio.
        """
        # Reset all strategies
        for s in self.strategies.values():
            s.reset()

        capital = initial_capital
        equity = initial_capital
        position = "flat"
        entry_price = 0.0
        position_fraction = self.position_size_pct

        equity_curve: list[float] = []
        trades: list[dict[str, Any]] = []
        agreements = 0
        disagreements = 0
        total_signals = 0

        for event in events:
            if not event.closed:
                continue

            # Update equity for open position
            if position == "flat":
                equity = capital
            elif position == "long":
                pnl_pct = (event.close - entry_price) / entry_price
                equity = capital * (1 + pnl_pct * self.leverage * position_fraction)
            elif position == "short":
                pnl_pct = (entry_price - event.close) / entry_price
                equity = capital * (1 + pnl_pct * self.leverage * position_fraction)

            # Collect signals from all strategies
            buy_weight = 0.0
            sell_weight = 0.0
            buy_count = 0
            sell_count = 0

            for name, strat in self.strategies.items():
                sig = strat.on_kline(event)
                if sig is not None:
                    if sig.side == OrderSide.BUY:
                        buy_weight += self.weights.get(name, 0) * sig.strength
                        buy_count += 1
                    elif sig.side == OrderSide.SELL:
                        sell_weight += self.weights.get(name, 0) * sig.strength
                        sell_count += 1

            # Fuse signals
            # Entry requires min_agreement, exit is easier (any sell closes long in long_only)
            action = None
            if buy_count + sell_count > 0:
                total_signals += 1

                # Entry: need min_agreement strategies to agree
                if buy_count >= self.min_agreement and buy_weight > sell_weight:
                    action = "buy"
                # Exit: in long_only mode, any sell_weight > buy_weight closes long
                elif self.long_only and position == "long" and sell_weight > buy_weight and sell_count >= 1:
                    action = "sell"
                # Exit in bidirectional: need min_agreement for new position
                elif not self.long_only and sell_count >= self.min_agreement and sell_weight > buy_weight:
                    action = "sell"

                if action:
                    dominant = max(buy_count, sell_count)
                    total = buy_count + sell_count
                    if total > 0 and dominant / total > 0.7:
                        agreements += 1
                    else:
                        disagreements += 1
                else:
                    disagreements += 1

            # Execute action
            if action == "buy" and position == "flat":
                position = "long"
                entry_price = event.close
            elif action == "buy" and position == "short" and not self.long_only:
                # Close short and open long
                pnl_pct = (entry_price - event.close) / entry_price
                gross_pnl = capital * pnl_pct * self.leverage * position_fraction
                fee = capital * self.fee_rate * 2 * position_fraction
                net_pnl = gross_pnl - fee
                capital += net_pnl
                trades.append({
                    "entry_price": entry_price, "exit_price": event.close,
                    "direction": "short", "pnl": net_pnl, "capital_after": capital,
                })
                position = "long"
                entry_price = event.close
            elif action == "sell" and position == "long":
                # Close long
                pnl_pct = (event.close - entry_price) / entry_price
                gross_pnl = capital * pnl_pct * self.leverage * position_fraction
                fee = capital * self.fee_rate * 2 * position_fraction
                net_pnl = gross_pnl - fee
                capital += net_pnl
                trades.append({
                    "entry_price": entry_price, "exit_price": event.close,
                    "direction": "long", "pnl": net_pnl, "capital_after": capital,
                })
                position = "flat"
                entry_price = 0.0
            elif action == "sell" and position == "flat" and not self.long_only:
                position = "short"
                entry_price = event.close

            equity_curve.append(equity)

        # Calculate metrics
        result = EnsembleBacktestResult(
            equity_curve=equity_curve,
            trades=trades,
            total_trades=len(trades),
            signal_agreements=agreements,
            signal_disagreements=disagreements,
            avg_agreement_ratio=agreements / max(total_signals, 1),
        )

        if len(equity_curve) < 2:
            return result

        # Total return
        result.total_return = (equity_curve[-1] - initial_capital) / initial_capital

        # Max drawdown
        peak = initial_capital
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
        result.max_drawdown = -max_dd

        # Win rate and profit factor
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        result.win_rate = len(wins) / len(trades) if trades else 0.0
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

        # Sharpe ratio
        if len(equity_curve) > 1:
            returns = pd.Series(equity_curve).pct_change().dropna()
            if returns.std() > 0:
                result.sharpe_ratio = returns.mean() / returns.std() * (252 ** 0.5)

        # Calmar
        if result.max_drawdown != 0:
            result.calmar_ratio = result.total_return / abs(result.max_drawdown)

        return result
