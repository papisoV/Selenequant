"""Telegram signal tools — query tg_events from EventGuard DB.

Exposes LLM-classified TG signals to the agent runtime.  Both tools read
the macro_events.db sqlite directly (raw SQL, no data-access class exists
for this schema).  A missing DB degrades to a friendly ToolResult, never
an exception.
"""

from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from selene_space_types.risk import RiskLevel

from selene_space_agent.tools.base import BaseTool, ToolResult

_DEFAULT_DB = "data/macro_events.db"
_MAX_HOURS = 720


def _f(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    if value is None:
        return 0.0
    return float(value)


def _connect(db_path: str | Path) -> sqlite3.Connection | None:
    path = Path(db_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _query_events(
    db_path: str | Path,
    hours: int,
    limit: int,
    event_type: str | None = None,
) -> list[dict] | None:
    """Return events in the lookback window; None if DB is missing."""
    conn = _connect(db_path)
    if conn is None:
        return None
    try:
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - hours * 3_600_000
        sql = "SELECT * FROM tg_events WHERE timestamp >= ?"
        params: list[Any] = [cutoff]
        if event_type:
            sql += " AND UPPER(event_type) = ?"
            params.append(event_type.upper())
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# TGSignalLookupTool
# ---------------------------------------------------------------------------


class TGSignalLookupTool(BaseTool):
    def __init__(self, db_path: str | Path = _DEFAULT_DB) -> None:
        self._db_path = db_path

    @property
    def name(self) -> str:
        return "tg_signal_lookup"

    @property
    def description(self) -> str:
        return (
            "Look up recent Telegram signal events (security hacks, regulatory "
            "news, whale moves) from the EventGuard database."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": f"Lookback window in hours (default 6, max {_MAX_HOURS})",
                },
                "event_type": {
                    "type": "string",
                    "description": "Filter by type: SECURITY/REGULATORY/WHALE/MACRO/LISTING...",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max events returned (default 20)",
                },
            },
            "required": [],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            hours = min(max(1, int(args.get("hours", 6))), _MAX_HOURS)
        except (TypeError, ValueError):
            hours = 6
        try:
            limit = max(1, int(args.get("limit", 20)))
        except (TypeError, ValueError):
            limit = 20
        event_type = args.get("event_type")

        events = _query_events(self._db_path, hours, limit, event_type)
        if events is None:
            return ToolResult(output=f"TG events DB not found at '{self._db_path}'")
        if not events:
            return ToolResult(output=f"No TG events found in last {hours}h")

        lines = [f"{len(events)} events in last {hours}h:"]
        for ev in events:
            magnitude = _f(ev.get("magnitude"))
            urgency = _f(ev.get("urgency"))
            half_life = _f(ev.get("half_life_h"))
            lines.append(
                f"--- [{ev.get('event_type', '?')}/{str(ev.get('alert_level', '?')).upper()}] "
                f"mag={magnitude:.2f} urgency={urgency:.2f} half_life={half_life:.1f}h"
            )
            lines.append(f"    {ev.get('message', '')}")
        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# TGEventImpactTool — events × positions × rule matrix
# ---------------------------------------------------------------------------

# Rule matrix: (event_type, levels, min_magnitude) → reduce percentage text
_REDUCE_50 = "减仓 50%"
_REDUCE_30 = "减仓 30%"
_WATCH = "关注"


def _action_for(event_type: str, alert_level: str, magnitude: float) -> str | None:
    etype = (event_type or "").upper()
    level = (alert_level or "").lower()
    high_priority = level in ("orange", "red") and magnitude >= 0.7
    if etype == "SECURITY" and high_priority:
        return _REDUCE_50
    if etype == "REGULATORY" and level == "orange" and magnitude >= 0.7:
        return _REDUCE_30
    return None


class TGEventImpactTool(BaseTool):
    def __init__(
        self,
        db_path: str | Path = _DEFAULT_DB,
        position_manager: Any = None,
        market_service: Any = None,
    ) -> None:
        self._db_path = db_path
        self._position_manager = position_manager
        self._market_service = market_service

    @property
    def name(self) -> str:
        return "tg_event_impact"

    @property
    def description(self) -> str:
        return (
            "Assess how recent TG events affect open paper positions using a "
            "rule matrix (SECURITY orange/red ≥0.7 → 减仓 50%, REGULATORY "
            "orange ≥0.7 → 减仓 30%)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account ID to assess"},
                "hours": {
                    "type": "integer",
                    "description": "Lookback window (default 6)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max events considered (default 10)",
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
            hours = min(max(1, int(args.get("hours", 6))), _MAX_HOURS)
        except (TypeError, ValueError):
            hours = 6
        try:
            limit = max(1, int(args.get("limit", 10)))
        except (TypeError, ValueError):
            limit = 10

        positions = (
            list(self._position_manager.get_positions(account_id))
            if self._position_manager is not None
            else []
        )

        events = _query_events(self._db_path, hours, limit)
        db_missing = events is None
        if events is None:
            events = []

        # Nothing at all → nothing to assess.
        if not events and not positions:
            if db_missing:
                return ToolResult(
                    output=f"Nothing to assess: TG events DB not found at '{self._db_path}'"
                )
            return ToolResult(output="Nothing to assess: no recent TG events, 无持仓")

        lines: list[str] = [f"TG 事件影响评估 ({account_id})"]

        # Positions section
        if positions:
            pos_lines = ", ".join(
                f"{p.symbol} ({p.side.value if hasattr(p.side, 'value') else p.side})"  # noqa: E501
                for p in positions
            )
            lines.append(f"当前持仓: {pos_lines}")
        else:
            lines.append("no open positions / 无持仓")

        lines.append("== 事件影响 ==")

        if not events:
            held = ", ".join(p.symbol for p in positions) or "无"
            lines.append(
                f"0 events in last {hours}h — TG 信号静态，持仓不受 TG 影响: {held}"
            )
            lines.append("== 总结 ==")
            lines.append("0 event(s) × 0 position(s)")
            lines.append("高优先级动作: 无")
            return ToolResult(output="\n".join(lines))

        if not positions:
            for ev in events:
                lines.append(
                    f"[{ev.get('event_type', '?')}] {ev.get('message', '')} — 不涉及当前持仓（无持仓）"  # noqa: E501
                )
            lines.append("== 总结 ==")
            lines.append(f"{len(events)} event(s) × 0 position(s)")
            lines.append("高优先级动作: 无")
            return ToolResult(output="\n".join(lines))

        high_actions: list[str] = []
        any_relevant = False

        for ev in events:
            event_type = str(ev.get("event_type", "") or "")
            level = str(ev.get("alert_level", "") or "")
            magnitude = _f(ev.get("magnitude"))
            affected = str(ev.get("affected", "*") or "*").upper()

            matched_positions = []
            for pos in positions:
                base = pos.symbol.split("/")[0].upper()
                if affected == "*" or affected == base or affected in base:
                    matched_positions.append(pos)

            action = _action_for(event_type, level, magnitude)

            header = f"[{event_type}/{level.upper()}] mag={magnitude:.2f} {ev.get('message', '')}"
            if not matched_positions:
                lines.append(f"{header} — 不涉及当前持仓")
                continue

            any_relevant = True
            for pos in matched_positions:
                verdict = action if action else _WATCH
                lines.append(f"{header} → {pos.symbol}: {verdict}")
                if action:
                    high_actions.append(f"{pos.symbol}: {action}")

        if not any_relevant:
            lines.append("所有事件均不涉及当前持仓")

        lines.append("== 总结 ==")
        lines.append(f"{len(events)} event(s) × {len(positions)} position(s)")
        if high_actions:
            lines.extend(f"- {item}" for item in dict.fromkeys(high_actions))
            lines.append("高优先级动作:")
        else:
            lines.append("高优先级动作: 无")

        return ToolResult(output="\n".join(lines))
