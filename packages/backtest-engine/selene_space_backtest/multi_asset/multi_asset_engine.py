"""Multi-asset backtest engine — merge-sorted kline loop.

Processes klines from multiple symbols in chronological order, allocates
capital via an AllocationModel, and tracks portfolio equity across symbols.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from selene_space_backtest.metrics import compute_metrics, infer_annualization_factor
from selene_space_backtest.multi_asset.allocation_model import AllocationModel
from selene_space_backtest.multi_asset.portfolio_broker import MultiAssetPortfolio
from selene_space_backtest.slippage import RealisticSlippage, SlippageModel
from selene_space_types.calendars import annualization_factor as _annualization_for
from selene_space_types.calendars import resolve_market_domain
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import BacktestResult


@dataclass(frozen=True)
class MultiAssetBacktestConfig:
    """Configuration for a multi-asset backtest run.

    Attributes:
        initial_cash: Starting cash balance.
        symbols: Symbols to include. If empty, inferred from data.
        allocation: Portfolio allocation model. If None, no rebalancing.
        slippage: Slippage model applied to fills.
        rebalance_on_start: If True, allocate and buy at the first bar.
        rebalance_interval: If set (>0), re-apply allocation every N bars.
        commission_rate: Fee fraction applied to buy/sell notional.
    """

    initial_cash: float = 10_000.0
    symbols: list[str] = field(default_factory=list)
    allocation: AllocationModel | None = None
    slippage: SlippageModel = field(default_factory=RealisticSlippage)
    rebalance_on_start: bool = False
    rebalance_interval: int | None = None
    commission_rate: float = 0.001


class MultiAssetEngine:
    """Event-driven multi-asset backtest engine.

    Groups klines by timestamp, applies allocation at each bar, and tracks
    a multi-symbol portfolio.

    Args:
        config: Multi-asset backtest configuration.
    """

    def __init__(self, config: MultiAssetBacktestConfig | None = None) -> None:
        self._config = config or MultiAssetBacktestConfig()

    def run(self, klines: list[KlineEvent]) -> BacktestResult:
        if not klines:
            raise ValueError("No kline data provided for backtest.")

        # Sort by timestamp
        klines = sorted(klines, key=lambda k: k.timestamp)

        # Resolve symbols
        symbols = self._config.symbols or list({k.symbol for k in klines})

        # Init portfolio
        portfolio = MultiAssetPortfolio(initial_cash=self._config.initial_cash)

        # Group klines by timestamp
        from collections import defaultdict

        ts_groups: dict[int, list[KlineEvent]] = defaultdict(list)
        for k in klines:
            ts_groups[k.timestamp].append(k)

        timestamps = sorted(ts_groups.keys())

        # Allocate on first bar if configured
        if (
            self._config.rebalance_on_start
            and self._config.allocation is not None
            and timestamps
        ):
            self._apply_allocation(portfolio, ts_groups[timestamps[0]], symbols)

        # Main loop
        rebalance_interval = self._config.rebalance_interval or 0
        for idx, ts in enumerate(timestamps):
            group = ts_groups[ts]
            prices: dict[str, float] = {}
            for k in group:
                prices[k.symbol] = k.close
            if rebalance_interval > 0 and idx > 0 and idx % rebalance_interval == 0:
                self._apply_allocation(portfolio, group, symbols, prices)
            portfolio.mark_to_market(prices)

        # Build result
        equity_curve = portfolio.equity_curve
        interval = klines[0].interval if klines else "1d"
        # ponytail: single-domain assumption — mixed-domain portfolios (OQ-4)
        # route by klines[0]; split per-symbol factors when a real use case exists.
        _first = klines[0]
        _domain = resolve_market_domain(
            _first.market_type, _first.symbol, _first.exchange
        )
        ann_factor = (
            _annualization_for(_domain, interval)
            if klines
            else infer_annualization_factor(interval)
        )

        trades: list[dict] = []
        metrics = compute_metrics(equity_curve, trades, annualization_factor=ann_factor)

        # Drawdown curve
        drawdown_curve: list[float] = []
        if len(equity_curve) >= 2:
            import numpy as np

            eq = np.asarray(equity_curve, dtype=np.float64)
            peak = np.maximum.accumulate(eq)
            dd = ((eq - peak) / peak).tolist()
            drawdown_curve = [round(v, 8) for v in dd]

        # Buy & Hold curves
        buy_hold_curves: dict[str, list[float]] = {}
        for sym in symbols:
            sym_klines = [k for k in klines if k.symbol == sym]
            if sym_klines and equity_curve:
                first_close = sym_klines[0].close
                if first_close > 0:
                    weight = 1.0 / len(symbols)
                    allocated = self._config.initial_cash * weight
                    buy_hold_curves[sym] = [
                        round(allocated * (k.close / first_close), 2)
                        for k in sym_klines
                    ]

        start_dt = datetime.fromtimestamp(klines[0].timestamp / 1000, tz=UTC)
        end_dt = datetime.fromtimestamp(klines[-1].timestamp / 1000, tz=UTC)

        result_id = f"mbt-{uuid.uuid4().hex[:8]}"

        return BacktestResult(
            id=result_id,
            strategy_id="multi_asset",
            strategy_version=1,
            symbol=",".join(symbols),
            interval=interval,
            start_date=start_dt,
            end_date=end_dt,
            metrics=metrics,
            equity_curve=equity_curve,
            drawdown_curve=drawdown_curve,
            buy_hold_curve=[],  # multi-asset doesn't have a single buy-hold
            trades=trades,
            trades_count=len(trades),
            created_at=int(time.time() * 1000),
        )

    def _apply_allocation(
        self,
        portfolio: MultiAssetPortfolio,
        group: list[KlineEvent],
        symbols: list[str],
        prices: dict[str, float] | None = None,
    ) -> None:
        """Rebalance the portfolio toward the allocation model's target weights.

        Compares each symbol's target quantity against its current position and
        buys/sells the delta, so over-allocated positions are reduced rather than
        stacked on top of (which would double-deploy capital). Sells are executed
        before buys so freed cash funds the purchases regardless of symbol order.
        """
        allocation = self._config.allocation
        if allocation is None:
            return

        weights = allocation.compute_weights(
            symbols, portfolio.total_equity, prices or {}
        )
        slippage_model = self._config.slippage
        from typing import cast

        from selene_space_types.trading import Order, OrderSide
        from selene_space_backtest.broker import _slippaged_price

        # Resolve each symbol's target delta once; symbol order is arbitrary here.
        deltas: list[tuple[str, float, KlineEvent]] = []
        for sym in symbols:
            kline = next((k for k in group if k.symbol == sym), None)
            if kline is None:
                continue
            price = kline.close
            if price <= 0:
                continue
            target_qty = portfolio.total_equity * weights.get(sym, 0.0) / price
            delta = target_qty - portfolio.get_position(sym)
            if delta != 0:
                deltas.append((sym, delta, kline))

        # Sell over-allocated first so the freed cash covers the buys below.
        for sym, delta, kline in deltas:
            if delta > 0:
                continue
            qty = -delta
            price = kline.close
            fill_price = _slippaged_price(
                slippage_model,
                price,
                cast(Order, _FakeOrder(sym, OrderSide.SELL, quantity=qty)),
                kline,
            )
            fee = fill_price * qty * self._config.commission_rate
            portfolio.apply_sell(sym, quantity=qty, price=fill_price, fee=fee)

        # Then buy the under-allocated shortfall.
        for sym, delta, kline in deltas:
            if delta < 0:
                continue
            qty = delta
            price = kline.close
            fill_price = _slippaged_price(
                slippage_model,
                price,
                cast(Order, _FakeOrder(sym, OrderSide.BUY, quantity=qty)),
                kline,
            )
            fee = fill_price * qty * self._config.commission_rate
            portfolio.apply_buy(sym, quantity=qty, price=fill_price, fee=fee)


class _FakeOrder:
    """Minimal order-like object for slippage model compatibility."""

    __slots__ = ("symbol", "side", "quantity", "price")

    def __init__(
        self, symbol: str, side, quantity: float = 1.0, price: float = 0.0
    ) -> None:
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.price = price
