"""PnL attribution — explain profit and loss by strategy, period, event, cost."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class PnLAttributionResult:
    """Aggregated attribution views over a set of trades."""

    total_pnl: float = 0.0
    by_strategy: dict[str, float] = field(default_factory=dict)
    by_period: dict[str, float] = field(default_factory=dict)
    by_event: list[dict[str, Any]] = field(default_factory=list)
    by_cost: dict[str, float] = field(default_factory=dict)
    period_start: str = ""
    period_end: str = ""


def _ms_to_datetime(ms: Any) -> datetime:
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC)


def _week_key(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


class PnLAttributionEngine:
    """Aggregate trade-level PnL into strategy / period / event / cost views."""

    _COST_KEYS = ("commissions", "slippage", "fees")
    _TRADE_COST_FIELDS = {
        "commissions": "commission",
        "slippage": "slippage",
        "fees": "fees",
    }

    def explain(
        self,
        trades: list[dict[str, Any]],
        period: tuple[str, str] | None = None,
    ) -> PnLAttributionResult:
        scoped = self._filter_by_period(trades, period)

        result = PnLAttributionResult()
        if not scoped:
            return result

        result.total_pnl = sum(float(t.get("pnl", 0.0)) for t in scoped)
        result.by_strategy = self._aggregate_by_strategy(scoped)
        result.by_period = self._aggregate_by_period(scoped)
        result.by_event = self._split_events(scoped)
        result.by_cost = self._aggregate_costs(scoped)

        exit_times = [
            _ms_to_datetime(t["exit_time"]) for t in scoped if t.get("exit_time")
        ]
        entry_times = [
            _ms_to_datetime(t["entry_time"]) for t in scoped if t.get("entry_time")
        ]
        if entry_times:
            result.period_start = min(entry_times).date().isoformat()
        if exit_times:
            result.period_end = max(exit_times).date().isoformat()

        return result

    @staticmethod
    def _filter_by_period(
        trades: list[dict[str, Any]],
        period: tuple[str, str] | None,
    ) -> list[dict[str, Any]]:
        if not period:
            return list(trades)
        start_str, end_str = period
        start_date = datetime.fromisoformat(start_str).date()
        end_date = datetime.fromisoformat(end_str).date()
        scoped: list[dict[str, Any]] = []
        for trade in trades:
            entry_date = _ms_to_datetime(trade.get("entry_time")).date()
            if start_date <= entry_date <= end_date:
                scoped.append(trade)
        return scoped

    @staticmethod
    def _aggregate_by_strategy(trades: list[dict[str, Any]]) -> dict[str, float]:
        buckets: dict[str, float] = {}
        for trade in trades:
            key = str(trade.get("strategy_id", "unknown"))
            buckets[key] = buckets.get(key, 0.0) + float(trade.get("pnl", 0.0))
        return buckets

    @staticmethod
    def _aggregate_by_period(trades: list[dict[str, Any]]) -> dict[str, float]:
        buckets: dict[str, float] = {}
        for trade in trades:
            ts = trade.get("entry_time") or trade.get("exit_time")
            if ts is None:
                continue
            key = _week_key(_ms_to_datetime(ts))
            buckets[key] = buckets.get(key, 0.0) + float(trade.get("pnl", 0.0))
        return buckets

    @staticmethod
    def _split_events(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for trade in trades:
            pnl = float(trade.get("pnl", 0.0))
            events.append(
                {
                    "type": "win" if pnl >= 0 else "loss",
                    "pnl": pnl,
                    "strategy_id": trade.get("strategy_id"),
                    "symbol": trade.get("symbol"),
                    "exit_time": trade.get("exit_time"),
                }
            )
        return events

    def _aggregate_costs(self, trades: list[dict[str, Any]]) -> dict[str, float]:
        totals = {name: 0.0 for name in self._COST_KEYS}
        for trade in trades:
            for cost_name, field_name in self._TRADE_COST_FIELDS.items():
                value = trade.get(field_name)
                if value is not None:
                    totals[cost_name] += float(value)
        return totals

    def generate_narrative(self, result: PnLAttributionResult) -> str:
        lines = [
            "# PnL Report",
            "",
            f"Total PnL: {result.total_pnl:+,.2f}",
            "",
        ]
        if result.by_strategy:
            lines.append("## By Strategy")
            lines.append("")
            for strategy_id, pnl in sorted(result.by_strategy.items()):
                lines.append(f"- {strategy_id}: {pnl:+,.2f}")
            lines.append("")
        if result.by_period:
            lines.append("## By Period")
            lines.append("")
            for week, pnl in sorted(result.by_period.items()):
                lines.append(f"- {week}: {pnl:+,.2f}")
            lines.append("")
        wins = [e for e in result.by_event if e.get("type") == "win"]
        losses = [e for e in result.by_event if e.get("type") == "loss"]
        lines.extend(
            [
                "## Events",
                "",
                f"- Wins: {len(wins)} ({sum(e['pnl'] for e in wins):+,.2f})",
                f"- Losses: {len(losses)} ({sum(e['pnl'] for e in losses):+,.2f})",
                "",
            ]
        )
        if result.by_cost:
            lines.append("## Costs:")
            lines.append("")
            for cost_name, amount in result.by_cost.items():
                lines.append(f"- {cost_name}: {amount:,.2f}")
        return "\n".join(lines)
