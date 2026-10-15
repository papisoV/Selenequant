"""Event-loop backtester: next-bar-open fills with risk-based sizing."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace as _dc_replace
from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd

from selene_space_backtest.broker import SimulatedBroker
from selene_space_backtest.commissions import (
    CommissionModel,
    PercentageCommission,
)
from selene_space_backtest.funding import BorrowFeeModel, FundingRateModel
from selene_space_backtest.metrics import compute_metrics
from selene_space_backtest.portfolio import Portfolio
from selene_space_backtest.protocols import StrategyRunner
from selene_space_backtest.risk_management import RiskManager
from selene_space_backtest.slippage import (
    RealisticSlippage,
    SlippageModel,
)
from selene_space_types.agent import TradeIntent
from selene_space_types.calendars import annualization_factor as _annualization_for
from selene_space_types.calendars import resolve_market_domain
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import BacktestResult
from selene_space_types.trading import Order, OrderSide, OrderType

_ATR_PERIOD = 14
_MAX_LOOKAHEAD_SAMPLES = 5
_FILL_EPSILON = (
    1e-9  # position-remaining dust threshold (broker uses 1e-12 on order clips)
)


class BacktestError(Exception):
    """A backtest could not start (missing or malformed inputs)."""


@dataclass(frozen=True)
class _PositionEntry:
    """Aggregated state of the open position in one symbol."""

    side: OrderSide
    entry_price: Decimal
    entry_ts: int
    entry_fee: Decimal
    quantity: Decimal = Decimal("0")
    # Point-in-time decision snapshot captured when the opening signal was
    # made; rides the entry into the trade record. Partial closes inherit it
    # via _dc_replace; same-side adds keep the ORIGINAL entry's snapshot
    # (entry_ts semantics: the moment this position was decided).
    decision_ctx: dict | None = None


@dataclass
class BacktestConfig:
    initial_cash: float = 10_000.0
    commission: CommissionModel = field(
        default_factory=lambda: PercentageCommission(rate=0.001)
    )
    slippage: SlippageModel = field(default_factory=RealisticSlippage)
    risk_per_trade: float = 0.01
    stop_loss_atr_mult: float = 2.0
    position_sizing: str = "risk_based"
    strategy_id: str = ""
    symbol: str = ""
    interval: str = ""
    funding_model: FundingRateModel | None = None
    borrow_fee_model: BorrowFeeModel | None = None
    allow_pyramiding: bool = False
    benchmark_symbol: str | None = None
    lookahead_guard: bool = True
    lookahead_sharpe_threshold: float = 3.0
    lookahead_min_trades: int = 10
    risk_managers: list[RiskManager] | None = None
    risk_cooldown_bars: int = 0
    # US-105: 每 ~5% 步长回调 (done_bars, total_bars);收尾必调 (total, total)。
    # 副作用通道 —— 回调抛错被吞成 warning,绝不影响回测本身。None = 不报进度。
    progress_cb: Callable[[int, int], None] | None = None


def _klines_from_dataframe(df: pd.DataFrame) -> list[KlineEvent]:
    """Convert an OHLCV frame row-wise into closed KlineEvents."""
    events: list[KlineEvent] = []
    for idx, row in df.iterrows():
        ts_raw = row.get("timestamp", None)
        if ts_raw is None:
            if isinstance(idx, (int, float)):
                ts_raw = int(idx)
            else:
                try:
                    ts_raw = int(pd.Timestamp(idx).timestamp() * 1000)
                except Exception:
                    ts_raw = len(events)
        events.append(
            KlineEvent(
                exchange="backtest",
                symbol=str(row.get("symbol", "BACKTEST")),
                timestamp=int(ts_raw),
                interval=str(row.get("interval", "")),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0)),
                closed=True,
            )
        )
    return events


def _merge_multi_symbol_data(
    data: dict[str, pd.DataFrame | list[KlineEvent]],
) -> list[KlineEvent]:
    """Flatten per-symbol payloads into one chronology sorted by timestamp."""
    streams: dict[str, list[KlineEvent]] = {}
    for symbol, payload in data.items():
        if isinstance(payload, pd.DataFrame):
            bars = _klines_from_dataframe(payload)
            for bar in bars:
                bar.symbol = symbol
        else:
            bars = [bar.model_copy(update={"symbol": symbol}) for bar in payload]
        streams[symbol] = sorted(bars, key=lambda bar: bar.timestamp)

    timeline: list[KlineEvent] = []
    for symbol in sorted(streams):
        timeline.extend(streams[symbol])
    timeline.sort(key=lambda bar: bar.timestamp)
    return timeline


class BacktestEngine:
    def __init__(self, config: BacktestConfig | None = None) -> None:
        self._config = config or BacktestConfig()

    def _compute_position_size(
        self,
        portfolio: Portfolio,
        kline: KlineEvent,
        price: float,
        atr_cache: dict[str, float],
    ) -> float:
        """Size so the ATR stop costs ``risk_per_trade`` of current cash.

        Reads ATR from the PREVIOUS bar: the engine updates ATR only
        after this sizing step each bar. Falls back to the bar's raw
        range when no smoothed value exists yet. Any quantity is capped
        at 95% deployable cash.
        """
        if self._config.position_sizing == "risk_based":
            risk_budget = portfolio.cash * self._config.risk_per_trade
            atr = atr_cache.get(kline.symbol, kline.high - kline.low)
            stop_span = atr * self._config.stop_loss_atr_mult
            if stop_span <= 0:
                stop_span = price * 0.01
            sized = risk_budget / stop_span
            return float(min(sized, portfolio.cash / price * 0.95))
        return portfolio.cash / price * 0.95

    @staticmethod
    def _update_atr(
        symbol: str,
        true_range: float,
        atr_cache: dict[str, float],
        tr_buffer: dict[str, list[float]],
        period: int = _ATR_PERIOD,
    ) -> None:
        """Wilder recursion seeded by a simple mean of the first TRs."""
        buf = tr_buffer.setdefault(symbol, [])
        buf.append(true_range)
        collected = len(buf)
        if collected < period:
            atr_cache[symbol] = sum(buf) / collected
        elif collected == period:
            atr_cache[symbol] = sum(buf) / period
        else:
            previous = atr_cache[symbol]
            atr_cache[symbol] = (previous * (period - 1) + true_range) / period

    @staticmethod
    def check_overfit_gate(report_dict: dict) -> bool:
        """False when PBO > 0.5, DSR < 0.3, or the report already failed."""
        pbo = report_dict.get("pbo")
        dsr = report_dict.get("deflated_sharpe_ratio")
        passed = report_dict.get("passed", True)
        return not (
            (pbo is not None and pbo > 0.5)
            or (dsr is not None and dsr < 0.3)
            or not passed
        )

    def run(
        self,
        strategy: StrategyRunner,
        data: pd.DataFrame
        | list[KlineEvent]
        | dict[str, pd.DataFrame | list[KlineEvent]],
    ) -> BacktestResult:
        started_ns = time.perf_counter()

        klines = self._normalize_data(data)
        if not klines:
            raise BacktestError("No kline data provided for backtest")

        config = self._config
        broker = SimulatedBroker(
            slippage=config.slippage,
            commission=config.commission,
        )
        portfolio = Portfolio(initial_cash=config.initial_cash)
        if hasattr(strategy, "_portfolio"):
            # 脚本运行时适配器(QDStrategyRuntime)需要读真实账本做目标权重对账;
            # 其它策略无此属性不受影响。(与 alphamining 引擎同契约)
            strategy._portfolio = portfolio

        trades: list[dict] = []
        open_positions: dict[str, _PositionEntry] = {}
        atr_values: dict[str, float] = {}
        tr_history: dict[str, list[float]] = {}
        prior_closes: dict[str, float] = {}
        exposure_curve: list[float] = []
        queued_orders: list[Order] = []
        risk_state: dict = {}
        cooldown_remaining: dict[str, int] = {}
        # Opening-signal decision snapshots keyed by order id: stored when the
        # open order is enqueued, consumed into the _PositionEntry on fill.
        # Entries die with their order (matched, dropped, or superseded), so a
        # failed/unfilled open never leaks a stale snapshot into another trade.
        pending_ctx: dict[str, dict | None] = {}

        strategy_id = config.strategy_id or "backtest"
        strategy_version = int(getattr(getattr(strategy, "config", None), "version", 1))

        lookahead_warnings: list[str] = []
        guard_on = config.lookahead_guard
        future_signal_count = 0

        first_kline = klines[0]
        start_iso = datetime.fromtimestamp(
            first_kline.timestamp / 1000, tz=UTC
        ).isoformat()

        # US-105 进度: ~5% 步长(短带至少收尾一次),收尾必报满。
        progress_cb = config.progress_cb
        total_bars = len(klines)
        next_progress_mark = 0
        if progress_cb is not None and total_bars > 0:
            progress_step = max(1, total_bars // 20)

        for bar_index, kline in enumerate(klines, start=1):
            # (1) Match queued orders for this symbol against the bar.
            still_queued: list[Order] = []
            for order in queued_orders:
                if order.symbol != kline.symbol:
                    still_queued.append(order)
                    continue
                # QD _feasible_delta 同位语义:开多/加多的 BUY 按可用现金
                # cap(实际成交价+每单位费用),现金永不为负;残单留队次根
                # 重试。SELL 与平空 BUY 不 cap(有卖出所得或持仓对冲)。
                max_cost: float | None = None
                if order.side == OrderSide.BUY:
                    held = portfolio.get_position_quantity(kline.symbol)
                    if held >= 0.0:
                        max_cost = max(portfolio.cash, 0.0)
                fill = broker.execute(order, kline, max_cost=max_cost)
                if fill is None:
                    still_queued.append(order)
                    continue
                outcome = self._book_fill(
                    order,
                    fill,
                    kline,
                    portfolio,
                    open_positions,
                    trades,
                    pending_ctx.get(order.id),
                )
                if outcome is False:
                    # stays queued; keep its snapshot for the retry bar
                    still_queued.append(order)
                else:
                    pending_ctx.pop(order.id, None)
            queued_orders = still_queued

            # (2) Consult the strategy; validate and translate its signal.
            signal = strategy.on_kline(kline)
            if guard_on and signal is not None and signal.timestamp > kline.timestamp:
                future_signal_count += 1
                if future_signal_count <= _MAX_LOOKAHEAD_SAMPLES:
                    lookahead_warnings.append(
                        f"Signal timestamp {signal.timestamp} > kline timestamp {kline.timestamp} "
                        f"for {kline.symbol} @ {kline.timestamp} (strategy used future data)"
                    )
            if signal is not None:
                queued_orders.extend(
                    self._orders_from_signal(
                        signal,
                        kline,
                        portfolio,
                        atr_values,
                        config,
                        cooldown_remaining,
                        pending_ctx,
                    )
                )

            # (3) Accrue funding / borrow on whatever stays open.
            entry = open_positions.get(kline.symbol)
            if entry is not None:
                if config.funding_model is not None:
                    direction = "long" if entry.side == OrderSide.BUY else "short"
                    funding_amount = config.funding_model.apply_funding(
                        float(entry.quantity) * float(kline.close),
                        direction,
                        kline.timestamp,
                    )
                    if funding_amount != 0.0:
                        portfolio.apply_funding(kline.symbol, funding_amount)
                if config.borrow_fee_model is not None and entry.side == OrderSide.SELL:
                    borrow_amount = config.borrow_fee_model.apply_borrow_fee(
                        float(entry.quantity) * float(kline.close),
                        kline.timestamp,
                    )
                    if borrow_amount != 0.0:
                        portfolio.apply_borrow_fee(kline.symbol, borrow_amount)

            # (4) Roll ATR forward AFTER any sizing read above.
            previous_close = prior_closes.get(kline.symbol)
            high, low = float(kline.high), float(kline.low)
            if previous_close is None:
                true_range = high - low
            else:
                true_range = max(
                    high - low, abs(high - previous_close), abs(low - previous_close)
                )
            self._update_atr(kline.symbol, true_range, atr_values, tr_history)
            prior_closes[kline.symbol] = float(kline.close)

            # (5) Mark the curve at this bar's close.
            portfolio.update_equity(kline.symbol, float(kline.close))
            # ponytail: 敞口只按当前 bar 标的计,多标的输入不聚合 —— 与
            # update_equity 的单标的标记口径一致,扩多标的先改 equity 口径
            units = portfolio.get_position_quantity(kline.symbol)
            market_value = units * float(kline.close)
            equity_now = market_value + portfolio.cash
            exposure_curve.append(market_value / equity_now if equity_now > 0 else 0.0)

            # (6) Backtest risk management: liquidation targets via normal order pipe.
            # risk_managers 每 bar 产出要清仓的 symbol;转成 close order 进
            # queued_orders,下一根开盘正常撮合(会计一致)。清仓后进入 cooldown,
            # 抑制策略重建仓(防"风控割肉→立即接飞刀")。
            self._tick_risk(
                config,
                kline,
                portfolio,
                open_positions,
                queued_orders,
                risk_state,
                cooldown_remaining,
            )

            if progress_cb is not None and bar_index >= next_progress_mark:
                try:
                    progress_cb(bar_index, total_bars)
                except Exception:
                    progress_cb = None  # 坏 sink 只丢进度,不绑架回测
                else:
                    next_progress_mark = bar_index + progress_step

        if progress_cb is not None and total_bars > 0:
            try:
                progress_cb(total_bars, total_bars)  # 收尾报满
            except Exception:
                pass

        end_kline = klines[-1]
        end_iso = datetime.fromtimestamp(end_kline.timestamp / 1000, tz=UTC).isoformat()

        equity_curve = portfolio.get_equity_curve()
        bar_interval = config.interval or first_kline.interval or ""
        _domain = resolve_market_domain(
            first_kline.market_type,
            config.symbol or first_kline.symbol,
            first_kline.exchange,
        )
        metrics = compute_metrics(
            equity_curve,
            trades,
            annualization_factor=_annualization_for(_domain, bar_interval),
        )
        metrics.funding_paid = float(sum(portfolio.accrued_funding().values()))
        metrics.borrow_fee_paid = float(sum(portfolio.borrow_fee_accrued().values()))

        if guard_on:
            metrics.sharpe_ratio = self._apply_sharpe_heuristic(
                metrics, trades, lookahead_warnings, config
            )

        result = BacktestResult(
            id=f"bt-{uuid.uuid4().hex[:12]}",
            strategy_id=strategy_id,
            symbol=config.symbol or first_kline.symbol,
            interval=config.interval or first_kline.interval,
            start_date=datetime.fromisoformat(start_iso),
            end_date=datetime.fromisoformat(end_iso),
            metrics=metrics,
            equity_curve=equity_curve,
            buy_hold_curve=self._build_buy_hold_curve(
                klines, config.benchmark_symbol, config.initial_cash
            ),
            drawdown_curve=self._drawdown_series(equity_curve),
            exposure_curve=exposure_curve,
            trades=trades,
            trades_count=len(trades),
            created_at=int(time.time() * 1000),
            lookahead_warnings=lookahead_warnings,
            strategy_version=strategy_version,
        )
        elapsed_ms = (time.perf_counter() - started_ns) * 1000
        assert elapsed_ms >= 0.0
        return result

    def _normalize_data(
        self,
        data: pd.DataFrame
        | list[KlineEvent]
        | dict[str, pd.DataFrame | list[KlineEvent]],
    ) -> list[KlineEvent]:
        """Accept frame / event-list / per-symbol mapping uniformly."""
        if isinstance(data, dict):
            return _merge_multi_symbol_data(data)
        if isinstance(data, pd.DataFrame):
            return _klines_from_dataframe(data)
        return list(data)

    def _tick_risk(
        self,
        config: BacktestConfig,
        kline: KlineEvent,
        portfolio: Portfolio,
        open_positions: dict[str, _PositionEntry],
        queued_orders: list[Order],
        risk_state: dict,
        cooldown_remaining: dict[str, int],
    ) -> None:
        """Per-bar risk pass: decay cooldown, then consult the managers.

        Each manager sees every symbol it cares about; only the current
        bar's symbol has a fresh price, so cross-symbol managers naturally
        degrade to their stored peaks for other symbols (the engine is
        single-symbol anyway).
        """
        for symbol, remaining in list(cooldown_remaining.items()):
            if remaining <= 1:
                cooldown_remaining.pop(symbol, None)
            else:
                cooldown_remaining[symbol] = remaining - 1
        if not config.risk_managers:
            return
        positions = {**portfolio.positions}
        if not positions:
            return
        prices: dict[str, float] = {kline.symbol: float(kline.close)}
        for mgr in config.risk_managers:
            for target in mgr.manage(positions, prices, risk_state):
                if target.symbol in cooldown_remaining:
                    continue
                entry = open_positions.get(target.symbol)
                if entry is None:
                    continue
                close_side = (
                    OrderSide.SELL if entry.side == OrderSide.BUY else OrderSide.BUY
                )
                queued_orders.append(
                    self._close_order(kline, close_side, float(entry.quantity))
                )
                cooldown_remaining[target.symbol] = config.risk_cooldown_bars

    def _orders_from_signal(
        self,
        signal,
        kline: KlineEvent,
        portfolio: Portfolio,
        atr_values: dict[str, float],
        config: BacktestConfig,
        cooldown_remaining: dict[str, int],
        pending_ctx: dict[str, dict | None],
    ) -> list[Order]:
        """Translate one signal into zero or more orders to enqueue.

        鸭子类型二分(契约见 protocols.py):带 ``order_type`` 属性的意图
        对象走 typed 直译,订单继承意图的类型/限价/止损价——SL 出场保持
        STOP 语义不退化成市价;其余按既有枚举信号默认市价单。
        """
        intent_type = self._intent_order_type(signal)
        close_side, close_qty = self._closing_target(signal, portfolio, kline)
        if close_qty > 0:
            return [
                self._typed_close(kline, close_side, close_qty, signal, intent_type)
            ]

        opening = portfolio.get_position_quantity(kline.symbol) == 0
        if opening and kline.symbol in cooldown_remaining:
            # 风控清仓后冷却期内禁止重建仓,避免"割肉后立即接飞刀"。
            return []
        adding = (
            config.allow_pyramiding
            and not opening
            and (
                (
                    portfolio.get_position_quantity(kline.symbol) > 0
                    and signal.side == OrderSide.BUY
                )
                or (
                    portfolio.get_position_quantity(kline.symbol) < 0
                    and signal.side == OrderSide.SELL
                )
            )
        )
        if not opening and not adding:
            return []

        if intent_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and getattr(
            signal, "price", None
        ) in (None, 0):
            return []

        reference_price = float(kline.close)
        if config.position_sizing == "signal_quantity":
            declared = getattr(signal, "quantity", None)
            if declared is None or declared <= 0:
                return []
            qty = float(declared)
        else:
            qty = self._compute_position_size(
                portfolio, kline, reference_price, atr_values
            )
        if qty <= 0:
            return []
        orders = [
            self._typed_open(
                kline, signal.side, qty, signal, intent_type or OrderType.MARKET
            )
        ]
        features = getattr(signal, "features", None)
        if features:
            pending_ctx[orders[0].id] = features
        return orders

    @staticmethod
    def _intent_order_type(signal) -> OrderType | None:
        """TradeIntent 形状判定:有 order_type 属性即走 typed 分派。

        P1-4:显式 ``isinstance(TradeIntent)`` 优先,鸭子 getattr 保留为
        兼容旧形状对象的回退 —— 行为不变,只加类型收口。
        """
        if isinstance(signal, TradeIntent):
            raw: object = signal.order_type
        else:
            raw = getattr(signal, "order_type", None)
        if raw is None:
            return None
        return raw if isinstance(raw, OrderType) else OrderType(str(raw))

    @staticmethod
    def _intent_stop_price(signal) -> Decimal | None:
        if isinstance(signal, TradeIntent):
            params = signal.risk_params or {}
        else:
            params = getattr(signal, "risk_params", None) or {}
        stop = params.get("stop_price")
        return Decimal(str(stop)) if stop else None

    def _closing_target(
        self, signal, portfolio: Portfolio, kline: KlineEvent
    ) -> tuple[OrderSide, float]:
        """Exit/reduce quantity for an exit signal on a held position.

        QD adapter signals (strategy_id ``qd:*``) carry a true reduce size in
        ``signal.quantity`` (order_target_percent delta); honor it as a partial
        close. Other strategies use quantity only as a target size and expect a
        held position to close fully, so quantity is ignored for them.
        """
        qd_declared = (
            str(getattr(signal, "strategy_id", "")).startswith("qd:")
            and getattr(signal, "quantity", None) is not None
            and float(signal.quantity) > 0
        )
        held = portfolio.get_position_quantity(kline.symbol)
        if held > 0 and signal.side == OrderSide.SELL:
            qty = min(float(signal.quantity), held) if qd_declared else held
            return OrderSide.SELL, qty
        if held < 0 and signal.side == OrderSide.BUY:
            qty = min(float(signal.quantity), abs(held)) if qd_declared else abs(held)
            return OrderSide.BUY, qty
        return OrderSide.SELL, 0.0

    def _typed_close(
        self,
        kline: KlineEvent,
        side: OrderSide,
        quantity: float,
        signal,
        intent_type: OrderType | None,
    ) -> Order:
        """平仓单继承意图类型/价格/止损价(SL 出场不能退化成市价)。"""
        order = self._close_order(kline, side, quantity)
        self._apply_intent_fields(order, signal, intent_type)
        return order

    def _typed_open(
        self,
        kline: KlineEvent,
        side: OrderSide,
        quantity: float,
        signal,
        intent_type: OrderType,
    ) -> Order:
        order = self._open_order(kline, side, quantity)
        self._apply_intent_fields(order, signal, intent_type)
        return order

    @staticmethod
    def _apply_intent_fields(
        order: Order, signal, intent_type: OrderType | None
    ) -> None:
        if intent_type is not None:
            order.type = intent_type
            price = (
                signal.price
                if isinstance(signal, TradeIntent)
                else getattr(signal, "price", None)
            )
            if price:
                order.price = Decimal(str(price))
            stop = BacktestEngine._intent_stop_price(signal)
            if stop is not None:
                order.stop_price = stop

    def _book_fill(
        self,
        order: Order,
        fill,
        kline: KlineEvent,
        portfolio: Portfolio,
        open_positions: dict[str, _PositionEntry],
        trades: list[dict],
        decision_ctx: dict | None = None,
    ) -> bool:
        """Apply one fill to cash and update/retire the position entry.

        Returns True when consumed; False keeps the order queued (only
        possible when the fill did not close out the matching logic).
        """
        entry = open_positions.get(kline.symbol)

        # 部分平仓:按比例缩减持仓簿,不能 pop —— pop 后残留量会让下一次
        # 全平掉进开仓分支,凭空造出反向仓(src8 幻影空头事故)。
        if (
            order.side == OrderSide.SELL
            and entry is not None
            and entry.side == OrderSide.BUY
        ):
            held = float(entry.quantity)
            closing = min(float(fill.quantity), held)
            remaining = held - closing
            portfolio.apply_fill(fill, OrderSide.SELL)
            if remaining <= _FILL_EPSILON:
                pnl = (
                    (float(fill.price) - float(entry.entry_price))
                    * float(fill.quantity)
                    - float(entry.entry_fee)
                    - float(fill.fee)
                )
                trades.append(
                    self._trade_record("long", "buy", "sell", entry, fill, kline, pnl)
                )
                open_positions.pop(kline.symbol, None)
                return True
            close_fee = float(entry.entry_fee) * closing / held
            pnl = (
                (float(fill.price) - float(entry.entry_price)) * closing
                - close_fee
                - float(fill.fee)
            )
            trades.append(
                self._trade_record(
                    "long",
                    "buy",
                    "sell",
                    _dc_replace(
                        entry,
                        quantity=Decimal(str(closing)),
                        entry_fee=Decimal(str(close_fee)),
                    ),
                    fill,
                    kline,
                    pnl,
                )
            )
            open_positions[kline.symbol] = _dc_replace(
                entry,
                quantity=Decimal(str(remaining)),
                entry_fee=Decimal(str(float(entry.entry_fee) - close_fee)),
            )
            return True

        if (
            order.side == OrderSide.BUY
            and entry is not None
            and entry.side == OrderSide.SELL
        ):
            held = float(entry.quantity)
            closing = min(float(fill.quantity), held)
            remaining = held - closing
            portfolio.apply_fill(fill, OrderSide.BUY)
            if remaining <= _FILL_EPSILON:
                pnl = (
                    (float(entry.entry_price) - float(fill.price))
                    * float(fill.quantity)
                    - float(entry.entry_fee)
                    - float(fill.fee)
                )
                trades.append(
                    self._trade_record("short", "sell", "buy", entry, fill, kline, pnl)
                )
                open_positions.pop(kline.symbol, None)
                return True
            close_fee = float(entry.entry_fee) * closing / held
            pnl = (
                (float(entry.entry_price) - float(fill.price)) * closing
                - close_fee
                - float(fill.fee)
            )
            trades.append(
                self._trade_record(
                    "short",
                    "sell",
                    "buy",
                    _dc_replace(
                        entry,
                        quantity=Decimal(str(closing)),
                        entry_fee=Decimal(str(close_fee)),
                    ),
                    fill,
                    kline,
                    pnl,
                )
            )
            open_positions[kline.symbol] = _dc_replace(
                entry,
                quantity=Decimal(str(remaining)),
                entry_fee=Decimal(str(float(entry.entry_fee) - close_fee)),
            )
            return True

        if order.side == OrderSide.BUY:
            portfolio.apply_fill(fill, OrderSide.BUY)
            open_positions[kline.symbol] = self._merged_entry(
                entry,
                fill,
                kline,
                OrderSide.BUY,
                open_positions,
                decision_ctx,
            )
            return True

        portfolio.apply_fill(fill, OrderSide.SELL)
        open_positions[kline.symbol] = self._merged_entry(
            entry,
            fill,
            kline,
            OrderSide.SELL,
            open_positions,
            decision_ctx,
        )
        return True

    @staticmethod
    def _merged_entry(
        existing: _PositionEntry | None,
        fill,
        kline: KlineEvent,
        side: OrderSide,
        open_positions: dict[str, _PositionEntry],
        decision_ctx: dict | None = None,
    ) -> _PositionEntry:
        """Extend a same-side position with volume-weighted averaging."""
        if existing is not None and existing.side == side:
            combined_qty = existing.quantity + fill.quantity
            weighted_price = (
                existing.entry_price * existing.quantity + fill.price * fill.quantity
            ) / combined_qty
            return _PositionEntry(
                side=side,
                entry_price=weighted_price,
                entry_ts=existing.entry_ts,
                entry_fee=existing.entry_fee + fill.fee,
                quantity=combined_qty,
                decision_ctx=existing.decision_ctx,
            )
        del open_positions  # kept for call-site symmetry; unused here
        return _PositionEntry(
            side=side,
            entry_price=fill.price,
            entry_ts=kline.timestamp,
            entry_fee=fill.fee,
            quantity=fill.quantity,
            decision_ctx=decision_ctx,
        )

    def _apply_sharpe_heuristic(
        self,
        metrics: object,
        trades: list[dict],
        warnings: list[str],
        config: BacktestConfig,
    ) -> float:
        """Append a bias warning when Sharpe looks too good; pass-through."""
        sharpe = getattr(metrics, "sharpe_ratio", 0.0)
        if (
            trades
            and len(trades) >= config.lookahead_min_trades
            and sharpe > config.lookahead_sharpe_threshold
        ):
            warnings.append(
                f"Sharpe ratio {sharpe:.2f} exceeds threshold {config.lookahead_sharpe_threshold} "
                f"with {len(trades)} trades — possible lookahead bias"
            )
        return sharpe

    @staticmethod
    def _make_order(kline: KlineEvent, side: OrderSide, quantity: float) -> Order:
        ts = kline.timestamp
        return Order(
            id=f"ord-{uuid.uuid4().hex[:8]}",
            symbol=kline.symbol,
            side=side,
            type=OrderType.MARKET,
            quantity=Decimal(str(quantity)),
            created_at=ts,
            updated_at=ts,
        )

    @classmethod
    def _close_order(cls, kline: KlineEvent, side: OrderSide, quantity: float) -> Order:
        return cls._make_order(kline, side, quantity)

    @classmethod
    def _open_order(cls, kline: KlineEvent, side: OrderSide, quantity: float) -> Order:
        return cls._make_order(kline, side, quantity)

    @staticmethod
    def _trade_record(
        side_label: str,
        entry_action: str,
        exit_action: str,
        entry: _PositionEntry,
        fill: object,
        kline: KlineEvent,
        pnl: float,
    ) -> dict:
        # Decimal fields (H19) must not leak into the trade blotter: the dict
        # is stored via plain json.dumps (BacktestRepository.save) which cannot
        # serialize Decimal. 分账: pnl 已扣双边佣金; gross/entry/exit 独立字段供假设条对账
        entry_fee = float(entry.entry_fee)
        exit_fee = float(getattr(fill, "fee"))
        return {
            "symbol": kline.symbol,
            "side": side_label,
            "entry_action": entry_action,
            "exit_action": exit_action,
            "entry_price": float(entry.entry_price),
            "exit_price": float(getattr(fill, "price")),
            "quantity": float(getattr(fill, "quantity")),
            "pnl": pnl,
            "gross_profit": pnl + entry_fee + exit_fee,
            "entry_commission": entry_fee,
            "exit_commission": exit_fee,
            "entry_time": entry.entry_ts,
            "exit_time": kline.timestamp,
            "decision_ctx": entry.decision_ctx or {},
        }

    @staticmethod
    def _build_buy_hold_curve(
        klines: list[KlineEvent], benchmark_symbol: str | None, initial_cash: float
    ) -> list[float]:
        if not benchmark_symbol:
            return []
        benchmark_bars = [bar for bar in klines if bar.symbol == benchmark_symbol]
        if not benchmark_bars:
            return []
        units_held = initial_cash / float(benchmark_bars[0].open)
        return [units_held * float(bar.close) for bar in benchmark_bars]

    @staticmethod
    def _drawdown_series(equity_curve: list[float]) -> list[float]:
        series: list[float] = []
        running_peak = float("-inf")
        for point in equity_curve:
            running_peak = max(running_peak, point)
            depth = (point - running_peak) / running_peak if running_peak > 0 else 0.0
            series.append(depth)
        return series
