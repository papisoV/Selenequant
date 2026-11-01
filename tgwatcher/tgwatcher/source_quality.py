"""Source quality tracker — accumulates outcome feedback into per-chat stats.

Skeleton module: stats are 0/empty until outcomes flow in. Selene (the
downstream consumer) is not yet integrated, so the signal_outcomes table
is empty and this tracker returns zero stats in production today.

Design:
- In-memory only. Lost on restart. Acceptable because outcomes can be
  re-accumulated from the persisted signal_outcomes table on demand
  (not implemented yet — would be a future enhancement).
- Thread-safe. Outcome POST handler runs in Flask request thread;
  multiple concurrent reports could happen.
- Per-chat aggregation. Per-sender omitted for now because outcomes
  don't always carry sender info (SignalOutcome has no sender_id field).

What this tracks (per chat_id):
- outcome_count: number of outcomes reported
- avg_magnitude_pct: mean |magnitude_pct| across outcomes (signal strength vs actual)
- direction_distribution: {bearish, neutral, bullish} counts of actual_direction
- last_outcome_at: ISO timestamp of most recent outcome

What this does NOT track (deferred — needs factor join):
- Calibration: did actual_direction match the signal's predicted direction?
- Confidence correlation: do high-confidence signals predict larger magnitude_pct?
- Per-sender accuracy: which sources produce actionable signals?

These deferred metrics require joining outcome → signal_factor. Build them
in a follow-up once outcomes are flowing.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class SourceQualityTracker:
    """In-memory per-chat outcome stats. Skeleton — see module docstring."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # chat_id -> stats dict
        self._stats: dict[int, dict[str, Any]] = {}

    def accumulate(self, outcome: dict[str, Any]) -> None:
        """Ingest one outcome report. Called after storage.save_signal_outcome.

        Args:
            outcome: The saved outcome dict. Must contain chat_id. Recognized
                fields: actual_direction (-1/0/+1), magnitude_pct (float),
                reported_at (datetime or ISO string).
        """
        chat_id = outcome.get("chat_id")
        if chat_id is None:
            logger.debug("Outcome missing chat_id, skipping tracker accumulate")
            return

        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            logger.debug("Outcome chat_id not int-able: %r, skipping", chat_id)
            return

        actual_dir = outcome.get("actual_direction")
        mag_pct = outcome.get("magnitude_pct")
        reported_at = outcome.get("reported_at")

        with self._lock:
            s = self._stats.setdefault(chat_id, {
                "outcome_count": 0,
                "magnitude_sum": 0.0,
                "magnitude_n": 0,
                "direction": {"-1": 0, "0": 0, "1": 0},
                "last_outcome_at": None,
            })

            s["outcome_count"] += 1

            # Direction distribution
            if actual_dir in (-1, 0, 1):
                s["direction"][str(actual_dir)] += 1

            # Magnitude stats — accumulate sum and count so we can compute mean.
            if mag_pct is not None:
                try:
                    s["magnitude_sum"] += abs(float(mag_pct))
                    s["magnitude_n"] += 1
                except (TypeError, ValueError):
                    pass

            # Last outcome timestamp — normalize to ISO string.
            iso = self._to_iso(reported_at)
            if iso:
                s["last_outcome_at"] = iso

        logger.debug(
            "Tracker accumulated outcome for chat %d (total=%d)",
            chat_id, s["outcome_count"],
        )

    def stats(self, chat_id: int | None = None) -> dict[str, Any]:
        """Return stats for one chat, or all chats if chat_id is None.

        Returns a dict shaped for JSON serialization. Empty dict if no data.
        """
        with self._lock:
            if chat_id is not None:
                try:
                    chat_id = int(chat_id)
                except (TypeError, ValueError):
                    return {}
                s = self._stats.get(chat_id)
                return self._format_one(chat_id, s) if s else {}
            return {
                str(cid): self._format_one(cid, s)
                for cid, s in self._stats.items()
            }

    def to_dict(self) -> dict[str, Any]:
        """Public snapshot for the /api/signals/source-quality endpoint."""
        return {
            "tracked_chats": len(self._stats),
            "total_outcomes": sum(s["outcome_count"] for s in self._stats.values()),
            "per_chat": self.stats(),
        }

    def reset(self) -> None:
        """Clear all accumulated stats. Mainly for tests."""
        with self._lock:
            self._stats.clear()

    # ============================================================
    # Helpers
    # ============================================================

    @staticmethod
    def _to_iso(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        if isinstance(value, str):
            return value
        return None

    @staticmethod
    def _format_one(chat_id: int, s: dict[str, Any]) -> dict[str, Any]:
        """Format internal stats dict for external consumption."""
        mag_n = s["magnitude_n"]
        avg_mag = (s["magnitude_sum"] / mag_n) if mag_n > 0 else 0.0
        return {
            "chat_id": chat_id,
            "outcome_count": s["outcome_count"],
            "avg_magnitude_pct": round(avg_mag, 4),
            "direction_distribution": dict(s["direction"]),
            "last_outcome_at": s["last_outcome_at"],
        }
