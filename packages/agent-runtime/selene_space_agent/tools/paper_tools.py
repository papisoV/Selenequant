"""Paper trading tools — account creation, order placement, positions, PnL.

Wraps duck-typed paper-engine managers.  Position fields arrive as
``Decimal`` from shared-types; numerics pass through coercion before
formatting so output stays LLM-readable.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult

_SAFE = RiskLevel.SAFE
_RESTRICTED = RiskLevel.RESTRICTED
_SIDES = frozenset(("buy", "sell"))
_ORDER_KINDS = ("market", "limit")


def _f(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if value is None:
        return 0.0
    return float(value)


def _schema(
    props: dict[str, tuple[str, str]], needed: tuple[str, ...]
) -> dict[str, Any]:
    """Build a JSON-Schema object from (type, description) pairs."""
    return {
        "type": "object",
        "properties": {
            key: {"type": ptype, "description": desc}
            for key, (ptype, desc) in props.items()
        },
        "required": list(needed),
    }


def _side_of(position: Any) -> str:
    side = position.side
    return str(getattr(side, "value", side))


def _mark_price(position: Any, market_service: Any) -> float:
    """Prefer live ticker price; fall back to the position's own mark."""
    if market_service is not None:
        try:
            ticker = market_service.get_ticker(position.symbol)
        except Exception:
            ticker = None
        last = getattr(ticker, "last_price", None) if ticker is not None else None
        if last is not None:
            return _f(last)
    return _f(getattr(position, "current_price", 0.0))


def _floating_pnl(position: Any, last: float) -> float:
    entry = _f(position.avg_entry_price)
    qty = _f(position.quantity)
    direction = 1.0 if _side_of(position) == "buy" else -1.0
    return direction * (last - entry) * (qty / max(entry, 1e-9))


class PaperCreateAccountTool(BaseTool):
    def __init__(self, account_manager: Any) -> None:
        self._accounts = account_manager

    @property
    def name(self) -> str:
        return "paper_create_account"

    @property
    def description(self) -> str:
        return (
            "Open a new simulated trading account backed by virtual USDT, "
            "returning its account id for later orders and queries."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return _schema(
            {
                "name": ("string", "Human-readable label for the new account"),
                "initial_balance": (
                    "number",
                    "Starting USDT balance (defaults to 10000)",
                ),
            },
            ("name",),
        )

    @property
    def risk_level(self) -> RiskLevel:
        return _SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            return ToolResult(output="", error="Missing required parameter: name")

        requested = args.get("initial_balance")
        try:
            starting = _f(requested) if requested is not None else 10_000.0
        except (TypeError, ValueError):
            return ToolResult(
                output="", error=f"Invalid initial_balance: {requested!r}"
            )
        if starting < 0:
            return ToolResult(output="", error="initial_balance must be >= 0")

        account = self._accounts.create_account(
            name=name.strip(), initial_balance=starting
        )
        return ToolResult(
            output=(
                f"Account '{account.name}' created (id: {account.id}); "
                f"balance = ${_f(account.current_balance):,.2f}"
            )
        )


class PaperPlaceOrderTool(BaseTool):
    def __init__(self, order_manager: Any, overtrading_detector: Any = None) -> None:
        self._orders = order_manager
        self._overtrading = overtrading_detector

    @property
    def name(self) -> str:
        return "paper_place_order"

    @property
    def description(self) -> str:
        return (
            "Submit a simulated market or limit order against a paper account; "
            "limit orders must carry a positive price. Requires the user's "
            "explicit confirmation (user_confirmed=true) before execution."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return _schema(
            {
                "account_id": ("string", "Account to trade on"),
                "symbol": ("string", "Trading pair, e.g. BTC/USDT"),
                "side": ("string", "buy or sell"),
                "order_type": ("string", "market or limit"),
                "quantity": ("number", "Order size in USDT notional"),
                "price": ("number", "Limit price (limit orders only)"),
                "user_confirmed": (
                    "boolean",
                    "Set true only after showing the user the full order "
                    "parameters and receiving their explicit confirmation",
                ),
            },
            (
                "account_id",
                "symbol",
                "side",
                "order_type",
                "quantity",
                "user_confirmed",
            ),
        )

    @property
    def risk_level(self) -> RiskLevel:
        return _RESTRICTED

    _CONFIRMATION_PROMPT = (
        "需要用户确认后才能下单。请先向用户展示订单参数"
        "（账户/交易对/方向/数量/类型），获得明确同意后携带 "
        "user_confirmed=true 重新调用。"
    )

    def execute(self, args: dict[str, Any]) -> ToolResult:
        if args.get("user_confirmed") is not True:
            return ToolResult(output="", error=self._CONFIRMATION_PROMPT)

        account_id = args.get("account_id") or ""
        symbol = args.get("symbol") or ""
        side = str(args.get("side") or "").strip().lower()
        kind = str(args.get("order_type") or "").strip().lower()

        bad: list[str] = []
        if not account_id:
            bad.append("account_id")
        if not symbol:
            bad.append("symbol")
        if side not in _SIDES:
            bad.append("side (must be buy|sell)")
        if kind not in _ORDER_KINDS:
            bad.append("order_type (must be market|limit)")
        if bad:
            return ToolResult(
                output="", error=f"Invalid or missing parameters: {'; '.join(bad)}"
            )

        try:
            quantity = _f(args.get("quantity"))
        except (TypeError, ValueError):
            quantity = 0.0
        if quantity <= 0:
            return ToolResult(output="", error="quantity must be a positive number")

        if self._overtrading is not None:
            try:
                alerts = self._overtrading.evaluate(symbol, side)
            except Exception:
                alerts = []
            if alerts:
                return ToolResult(
                    output="",
                    error="Order blocked by overtrading guard: "
                    + " | ".join(alert.message for alert in alerts),
                )

        limit_price = None
        if kind == "limit":
            try:
                limit_price = _f(args.get("price"))
            except (TypeError, ValueError):
                limit_price = 0.0
            if limit_price <= 0:
                return ToolResult(
                    output="", error="limit orders require a positive price"
                )

        placed = self._orders.place_order(
            account_id,
            symbol=symbol,
            side=side,
            order_type=kind,
            quantity=quantity,
            price=limit_price,
        )
        if self._overtrading is not None:
            try:
                self._overtrading.record_order(symbol, side)
            except Exception:
                pass
        status = str(getattr(placed.status, "value", placed.status))
        return ToolResult(
            output=(
                f"{kind} {side} {quantity:,.2f} USDT of {symbol} submitted "
                f"(id: {placed.id}, status: {status})"
            )
        )


class PaperPositionsTool(BaseTool):
    def __init__(self, position_manager: Any) -> None:
        self._positions_mgr = position_manager

    @property
    def name(self) -> str:
        return "paper_positions"

    @property
    def description(self) -> str:
        return "Enumerate every open position currently held by one paper account."

    @property
    def parameters(self) -> dict[str, Any]:
        return _schema(
            {"account_id": ("string", "Account whose positions to enumerate")},
            ("account_id",),
        )

    @property
    def risk_level(self) -> RiskLevel:
        return _SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        account_id = args.get("account_id")
        if not account_id:
            return ToolResult(output="", error="Missing required parameter: account_id")

        holdings = list(self._positions_mgr.get_positions(account_id))
        if not holdings:
            return ToolResult(output="No positions found")

        header = f"{len(holdings)} open position(s) in {account_id}:"
        rows = [
            f"  {_side_of(p).upper():5s} {p.symbol:<12s} qty={_f(p.quantity):,.2f} USDT "
            f"@ entry {_f(p.avg_entry_price):,.2f}"
            for p in holdings
        ]
        return ToolResult(output="\n".join([header, *rows]))


class PaperPnlTool(BaseTool):
    def __init__(self, position_manager: Any, market_service: Any) -> None:
        self._positions_mgr = position_manager
        self._market = market_service

    @property
    def name(self) -> str:
        return "paper_pnl"

    @property
    def description(self) -> str:
        return (
            "Compute unrealized PnL for every open position of an account "
            "(live prices where available) plus the combined total."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return _schema(
            {"account_id": ("string", "Account whose PnL to compute")},
            ("account_id",),
        )

    @property
    def risk_level(self) -> RiskLevel:
        return _SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        account_id = args.get("account_id")
        if not account_id:
            return ToolResult(output="", error="Missing required parameter: account_id")

        holdings = list(self._positions_mgr.get_positions(account_id))
        if not holdings:
            return ToolResult(output="No positions found")

        total = 0.0
        rows: list[str] = [f"PnL breakdown for {account_id}:"]
        for p in holdings:
            last = _mark_price(p, self._market)
            pnl = _floating_pnl(p, last)
            total += pnl
            tag = "LONG" if _side_of(p) == "buy" else "SHORT"
            rows.append(
                f"  {tag} {p.symbol}: entry {_f(p.avg_entry_price):,.2f} → "
                f"last {last:,.2f}, unrealized {pnl:+,.2f} USDT"
            )
        rows.append(f"Combined unrealized PnL: {total:+,.2f} USDT")
        return ToolResult(output="\n".join(rows))
