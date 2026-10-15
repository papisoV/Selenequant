"""Per-channel monotonic sequence manager + module singleton (protocol §3.2).

The AccountState protocol stamps every WS event with a monotonic ``seq`` so
clients can detect gaps and request a full resync snapshot. This module owns
the counters; the two broadcast chokepoints (``broadcast_trading_event`` /
``broadcast_paper_event``) receive the value as an explicit argument and never
call ``next()`` themselves — single-writer, no double-increment.

seq_key namespaces (independent counters):
  - Paper path: ``paper:{account_id}`` — one counter per account, shared by all
    6 event types that interleave on the single paper channel.
  - Trading path: ``{user_id}:{channel}`` — per-channel counters. Bridges
    currently broadcast with ``user_id="local"`` (N2), so keys are
    ``local:orders`` / ``local:positions`` / ``local:balances``.
"""

from __future__ import annotations

from typing import Any

from selene_space_types.trading import AccountStateSeq

_session: "ChannelSequenceManager | None" = None


class ChannelSequenceManager:
    """Per-key monotonic counters with a process-level session identity."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._counters: dict[str, int] = {}

    def next(self, seq_key: str) -> int:
        """Advance *seq_key*'s counter by 1 and return the new value.

        The increment is a synchronous read-modify-write. The asyncio event
        loop is single-threaded and there is no ``await`` between the read and
        the write, so the operation is atomic within the loop.
        """
        current = self._counters.get(seq_key, 0)
        new = current + 1
        self._counters[seq_key] = new
        return new

    def watermark(
        self,
        *,
        orders: str | None = None,
        positions: str | None = None,
        balances: str | None = None,
    ) -> AccountStateSeq:
        """Return the current counters as an ``AccountStateSeq`` watermark.

        Each channel key is optional — a missing/never-advanced key reads as 0.
        Paper path passes the same ``paper:{account_id}`` key for all three
        channels (single counter replicated); trading path passes distinct
        per-channel keys.
        """
        return AccountStateSeq(
            orders=self._counters.get(orders, 0) if orders else 0,
            positions=self._counters.get(positions, 0) if positions else 0,
            balances=self._counters.get(balances, 0) if balances else 0,
        )


def set_channel_sequence(manager: Any) -> None:
    """Set the module-level ChannelSequenceManager singleton (lifespan wiring)."""
    global _session
    _session = manager


def get_channel_sequence() -> Any:
    """Return the module-level ChannelSequenceManager (or None if not wired)."""
    return _session
