"""Token usage and cost accounting for agent conversations."""

from __future__ import annotations

import time
from typing import Any


class TokenCostTracker:
    """Accumulate per-session and per-day token usage and cost in USD.

    ``record_usage`` accepts an explicit cost; when omitted a fallback
    price table is used so estimates never silently under-report.
    """

    def __init__(self) -> None:
        # session_id -> {"input": int, "output": int, "calls": int,
        #                "cost": float, "by_tool": {tool: calls}}
        self._sessions: dict[str, dict[str, Any]] = {}
        # day (UTC "YYYY-MM-DD") -> {"calls": int, "cost": float}
        self._days: dict[str, dict[str, Any]] = {}
        self.total_records: int = 0

    def record_usage(
        self,
        session_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None = None,
        tool_name: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        """Account one LLM call into its session and UTC-day buckets.

        Args:
            session_id: Conversation the call belongs to.
            model: Model identifier; feeds the per-model token split.
            input_tokens: Prompt tokens consumed.
            output_tokens: Completion tokens produced.
            cost_usd: Authoritative cost; when ``None`` a fallback price
                table ($0.25/M input, $1.00/M output) estimates it.
            tool_name: Attributing tool call, if this usage came from one.
            timestamp: POSIX seconds override; defaults to now and selects
                the UTC day bucket.
        """
        ts = timestamp if timestamp is not None else time.time()
        if cost_usd is None:
            input_cost = input_tokens / 1_000_000 * 0.25
            output_cost = output_tokens / 1_000_000 * 1.0
            cost_usd = input_cost + output_cost

        session = self._sessions.setdefault(
            session_id,
            {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "call_count": 0,
                "total_cost_usd": 0.0,
                "by_tool": {},
                "by_model": {},
            },
        )
        session["total_input_tokens"] += input_tokens
        session["total_output_tokens"] += output_tokens
        session["call_count"] += 1
        session["total_cost_usd"] += cost_usd
        if tool_name:
            session["by_tool"][tool_name] = session["by_tool"].get(tool_name, 0) + 1
        if model:
            entry = session["by_model"].setdefault(
                model, {"input_tokens": 0, "output_tokens": 0}
            )
            entry["input_tokens"] += input_tokens
            entry["output_tokens"] += output_tokens

        day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        daily = self._days.setdefault(day, {"call_count": 0, "total_cost_usd": 0.0})
        daily["call_count"] += 1
        daily["total_cost_usd"] += cost_usd

        self.total_records += 1

    # Backwards-compatible alias for callers recording bare usage objects.
    record = record_usage

    def get_session_cost(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None:
            return {
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "call_count": 0,
                "by_tool": {},
                "by_model": {},
            }
        result = dict(session)
        result["total_tokens"] = (
            session["total_input_tokens"] + session["total_output_tokens"]
        )
        return result

    def get_daily_cost(self, day: str) -> dict[str, Any]:
        """Return ``{"call_count", "total_cost_usd"}`` for a UTC "YYYY-MM-DD".

        Unknown days yield zeroed counters rather than raising.
        """
        daily = self._days.get(day)
        if daily is None:
            return {"call_count": 0, "total_cost_usd": 0.0}
        return dict(daily)

    def summary(self) -> dict[str, Any]:
        """Aggregate all sessions into one totals dict (never raises)."""
        totals = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "call_count": 0,
            "sessions": len(self._sessions),
        }
        for session in self._sessions.values():
            totals["total_input_tokens"] += session["total_input_tokens"]
            totals["total_output_tokens"] += session["total_output_tokens"]
            totals["total_cost_usd"] += session["total_cost_usd"]
            totals["call_count"] += session["call_count"]
        totals["total_tokens"] = (
            totals["total_input_tokens"] + totals["total_output_tokens"]
        )
        return totals

    def reset(self) -> None:
        self._sessions.clear()
        self._days.clear()
        self.total_records = 0
