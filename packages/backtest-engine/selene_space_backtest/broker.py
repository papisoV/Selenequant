"""Bar-by-bar order matching with partial fills, slippage, and fees."""

from __future__ import annotations

from decimal import Decimal

from selene_space_types.market import KlineEvent
from selene_space_types.trading import Fill, Order, OrderSide, OrderStatus, OrderType

_BASELINE_COMMISSION_QUALNAME = "CommissionModel.calculate_with_type"
_FILL_EPSILON = 1e-12


def _model_overrides(model: object, method_name: str, baseline_qualname: str) -> bool:
    """True when ``model`` supplies its own logic for ``method_name``.

    Comparing against the base-class qualname keeps inherited no-op
    defaults (e.g. CommissionModel.calculate_with_type) from being mistaken
    for real overrides.
    """
    bound = getattr(type(model), method_name, None)
    if bound is None:
        return False
    return bool(bound.__qualname__ != baseline_qualname)


def _normalize_side(side: OrderSide) -> str:
    """Accept enum or raw-string sides; return a lowercase token."""
    value = getattr(side, "value", side)
    return str(value).lower()


def _slippaged_price(
    slippage_model: object,
    base_price: float,
    order: Order,
    kline: KlineEvent,
) -> float:
    """Apply slippage with full volume context.

    Every model shares the unified ``apply(price, side, *, order_notional,
    avg_24h_volume)`` signature, so the context is passed unconditionally.
    The bar's quote notional is ``volume * close``; when the bar looks
    implausibly thin relative to the order — synthetic fixtures often are —
    the context is zeroed so the model falls back to flat bps instead of
    producing absurd fills.
    """
    quantity = max(float(order.quantity), 0.0)
    order_notional = quantity * base_price
    bar_quote_notional = (
        float(kline.volume) * float(kline.close) if float(kline.volume) > 0 else 0.0
    )
    usable_reference = (
        bar_quote_notional if bar_quote_notional >= order_notional > 0.0 else 0.0
    )
    return float(
        slippage_model.apply(  # type: ignore[attr-defined]
            base_price,
            order.side,
            order_notional=order_notional,
            avg_24h_volume=usable_reference,
        )
    )


def _commission_charge(
    commission_model: object,
    executed_price: float,
    executed_quantity: float,
    order: Order,
) -> float:
    """Maker/taker-aware models get the order type; simple ones do not."""
    if _model_overrides(
        commission_model, "calculate_with_type", _BASELINE_COMMISSION_QUALNAME
    ):
        return float(
            commission_model.calculate_with_type(  # type: ignore[attr-defined]
                executed_price,
                executed_quantity,
                order_type=order.type,
            )
        )
    return float(commission_model.calculate(executed_price, executed_quantity))  # type: ignore[attr-defined]


class SimulatedBroker:
    """Fills orders against individual kline bars.

    MARKET/IOC orders execute at the bar open — this is what enforces the
    engine's next-bar-open discipline. LIMIT orders fill whenever the bar
    range reaches the limit price (gap-through included; the open grants
    the better price). STOP_MARKET triggers on range breach with the
    worse of stop/open as base price, and takes slippage explicitly.
    STOP_LIMIT promotes to a plain LIMIT on its trigger bar without
    filling that same bar. A single call may fill only part of the
    outstanding size: ``partial_fill_rate`` scales every attempt, and
    ``volume_participation_rate`` (when below 1.0) additionally caps the
    clip at a fraction of bar volume.

    Decimal boundary: Fill fields are Decimal per shared-types H19;
    internal math and ``avg_fill_price`` remain in plain float.
    """

    def __init__(
        self,
        slippage: object | None = None,
        commission: object | None = None,
        partial_fill_rate: float = 1.0,
        volume_participation_rate: float = 1.0,
    ) -> None:
        if not 0.0 < partial_fill_rate <= 1.0:
            raise ValueError("partial_fill_rate must lie in (0, 1]")
        if not 0.0 <= volume_participation_rate <= 1.0:
            raise ValueError("volume_participation_rate must lie in [0, 1]")
        self._slippage_model = slippage
        self._commission_model = commission
        self._clip_fraction = partial_fill_rate
        self._volume_cap_fraction = volume_participation_rate

    def execute(
        self, order: Order, kline: KlineEvent, max_cost: float | None = None
    ) -> Fill | None:
        """Attempt one fill for ``order`` against ``kline``.

        Returns None when the bar offers no execution price or the
        computed clip is empty; otherwise mutates the order's running
        aggregates and returns the resulting Fill.

        ``max_cost`` caps the fill so cost+fee never exceeds available
        cash (engine passes it for cash-debiting buys — the QD
        ``_feasible_delta`` cash-account guard). Unaffordable residue
        stays queued and retries on later bars.
        """
        if order.type == OrderType.STOP_LIMIT and self._promote_triggered_stop_limit(
            order, kline
        ):
            # 触发根只完成类型改写;LIMIT 腿从下一根起才可成交。
            return None

        execution_price = self._execution_price(order, kline)
        if execution_price is None:
            return None

        outstanding = self._outstanding_quantity(order)
        clip = outstanding * self._clip_fraction
        if self._volume_cap_fraction < 1.0:
            volume_cap = float(kline.volume) * self._volume_cap_fraction
            clip = min(clip, volume_cap)
        if clip <= 0.0:
            return None

        # 滑点不对称:限价族(LIMIT/STOP_LIMIT 转化后的 LIMIT)是被动等
        # 价格走向挂单价的 maker 成交,不加滑点;市价与止损触发是主动吃
        # 流动性,必须承担急迫成本(止损尤其常发生在恐慌行情)。
        liquidity_taking = order.type in (
            OrderType.MARKET,
            OrderType.STOP_MARKET,
            OrderType.IOC,
        )
        filled_price = (
            _slippaged_price(self._slippage_model, execution_price, order, kline)
            if self._slippage_model is not None and liquidity_taking
            else execution_price
        )

        if max_cost is not None:
            # 全部存量佣金模型均为比例制,per-unit 探针即每单位费用;
            # affordable 与 QD _feasible_delta 的 cash_cap 同式
            # (cash_cap = spendable / (fill_price*(1+commission)),
            #  即 fill_price + fill_price*commission = 每单位总成本)。
            per_unit_fee = (
                _commission_charge(self._commission_model, filled_price, 1.0, order)
                if self._commission_model is not None
                else 0.0
            )
            affordable = max_cost / max(filled_price + per_unit_fee, 1e-12)
            clip = min(clip, affordable)
            if clip <= _FILL_EPSILON:
                return None

        fee_amount = (
            _commission_charge(self._commission_model, filled_price, clip, order)
            if self._commission_model is not None
            else 0.0
        )

        self._record_progress(order, filled_price, clip)

        return Fill(
            id=f"fill-{order.id}",
            order_id=order.id,
            price=Decimal(str(filled_price)),
            quantity=Decimal(str(clip)),
            fee=Decimal(str(fee_amount)),
            symbol=kline.symbol,
            timestamp=kline.timestamp,
        )

    @staticmethod
    def _outstanding_quantity(order: Order) -> float:
        already_done = float(order.filled_quantity or 0.0)
        remaining = float(order.quantity) - already_done
        if remaining <= 0.0:
            return float(order.quantity)
        return remaining

    @staticmethod
    def _record_progress(order: Order, price: float, size: float) -> None:
        """Fold one clip into running VWAP, totals, status, timestamp."""
        done_before = float(order.filled_quantity or 0.0)
        done_after = done_before + size
        vwap_before = float(order.avg_fill_price or 0.0)
        if vwap_before > 0.0 and done_before > 0.0:
            order.avg_fill_price = (
                vwap_before * done_before + price * size
            ) / done_after
        else:
            order.avg_fill_price = price

        order.filled_quantity = Decimal(str(done_after))
        fully_done = done_after >= float(order.quantity) - _FILL_EPSILON
        order.status = OrderStatus.FILLED if fully_done else OrderStatus.PARTIAL

    def _execution_price(self, order: Order, kline: KlineEvent) -> float | None:
        """Price this bar grants the order, or None when it cannot trade.

        规则表见 internal-plan:
        - MARKET/IOC:开盘价。
        - LIMIT:触及即成交(不要求落在区间内),成交价向开盘价方向取优
          (跳空穿越保护——整根 bar 越过限价时按开盘价成交,更优价归交易者)。
        - STOP_MARKET:买看 high>=stop、卖看 low<=stop;基准价 = 开盘价与
          止损价中"更差"的一个(正常触发按止损价,跳空吃跳空价);滑点在
          execute 层显式叠加。STOP 单不追溯创建前 bar 已穿越的价。
        - STOP_LIMIT:本层只处理触发后的 LIMIT 腿(引擎把已触发的单据改写
          为 LIMIT 重排队);未触发返回 None。
        """
        bar_open = float(kline.open)
        bar_high = float(kline.high)
        bar_low = float(kline.low)

        if order.type == OrderType.MARKET or order.type == OrderType.IOC:
            return bar_open

        if order.type == OrderType.LIMIT:
            limit_price = float(order.price) if order.price is not None else 0.0
            if limit_price <= 0.0:
                return None
            if _normalize_side(order.side) == "buy":
                if bar_low > limit_price:
                    return None
                return min(limit_price, bar_open)
            if bar_high < limit_price:
                return None
            return max(limit_price, bar_open)

        if order.type == OrderType.STOP_MARKET:
            stop_price = (
                float(order.stop_price) if order.stop_price is not None else 0.0
            )
            if stop_price <= 0.0:
                return None
            if _normalize_side(order.side) == "buy":
                if bar_high < stop_price:
                    return None
                return max(stop_price, bar_open)
            if bar_low > stop_price:
                return None
            return min(stop_price, bar_open)

        if order.type == OrderType.STOP_LIMIT:
            # 两段式保守化:触发判定在此,成交交给转化后的 LIMIT 腿;
            # 同根"先触发再撮合"需要 bar 内先后序假设,bar 数据撑不起,
            # 刻意不做。
            return None

        raise ValueError(f"cannot match order type {order.type!r}")

    def _promote_triggered_stop_limit(self, order: Order, kline: KlineEvent) -> bool:
        """触发腿:STOP_LIMIT 一旦触及 stop 价,原地转普通 LIMIT 单。

        返回 True 表示本根已触发并完成类型改写(引擎下一根起按 LIMIT
        规则撮合);False 表示尚未触发,单据保持原样继续等。
        """
        stop_price = float(order.stop_price) if order.stop_price is not None else 0.0
        if stop_price <= 0.0:
            return False
        triggered = (
            float(kline.high) >= stop_price
            if _normalize_side(order.side) == "buy"
            else float(kline.low) <= stop_price
        )
        if not triggered:
            return False
        order.type = OrderType.LIMIT
        return True
