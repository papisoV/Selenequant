"""Explain tools — PnL explanation for paper accounts.

Formats an account-level profit/loss narrative from the position
manager and live tickers, suitable for direct LLM consumption.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult


def _f(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if value is None:
        return 0.0
    return float(value)


class ExplainPnlTool(BaseTool):
    """Explain why an account is up or down, position by position."""

    def __init__(self, position_manager: Any, market_service: Any) -> None:
        self._position_manager = position_manager
        self._market_service = market_service

    @property
    def name(self) -> str:
        return "explain_pnl"

    @property
    def description(self) -> str:
        return (
            "Explain the current PnL of a paper trading account: per-position "
            "entry vs last price, unrealized results, and overall summary."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "account_id": {
                    "type": "string",
                    "description": "Account ID to explain",
                },
            },
            "required": ["account_id"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        account_id = args.get("account_id")
        if not account_id:
            return ToolResult(output="", error="Missing required parameter: account_id")

        try:
            positions = list(self._position_manager.get_positions(account_id))
        except Exception as exc:
            return ToolResult(output="", error=f"Failed to fetch positions: {exc}")

        if not positions:
            return ToolResult(
                output=f"No open positions for account {account_id} — nothing to explain."
            )

        lines = [f"PnL explanation for {account_id} ({len(positions)} position(s)):"]
        total = 0.0
        for pos in positions:
            side = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            entry = _f(pos.avg_entry_price)

            ticker = (
                self._market_service.get_ticker(pos.symbol)
                if self._market_service is not None
                else None
            )
            last = (
                _f(ticker.last_price) if ticker is not None else _f(pos.current_price)
            )

            direction = 1.0 if side == "buy" else -1.0
            move_pct = (last - entry) / entry * 100.0 if entry else 0.0
            pnl = direction * (last - entry) * (_f(pos.quantity) / max(entry, 1e-9))
            total += pnl

            word = "up" if direction * (last - entry) >= 0 else "down"
            lines.append(
                f"  {pos.symbol} ({'LONG' if side == 'buy' else 'SHORT'}): "
                f"entered at {entry:,.2f}, now {last:,.2f} ({move_pct:+.2f}%) — "
                f"{word} {abs(pnl):,.2f} USDT"
            )

        verdict = "in profit" if total >= 0 else "at a loss"
        lines.append(f"Overall: {verdict} by {abs(total):,.2f} USDT")
        return ToolResult(output="\n".join(lines))
