"""Outage-episode bookkeeping for the A-share intraday poll source (S2).

The upstream (``hq.sinajs.cn``, see :mod:`selene_space_market.sources.
astock_intraday_source`) is a *poll* source: each ~3s cycle re-requests the whole
subscription batch. Failures already back off per symbol, but nothing records
*that an outage happened* — so a network gap heals invisibly and the missing
ticks leave no trace. This module supplies that missing memory.

Design constraints (see the S2 plan, ``internal-plan``):

- **Snapshot, not history.** ``hq.sinajs.cn`` returns only the current quote;
  there is no L1 history endpoint, so ticks lost during an outage are gone. The
  tracker therefore *records and marks* holes; it never fabricates data.
- **Total / no-raise.** It runs in the poll loop's hot path; a raise here would
  kill polling for every symbol. All methods are pure dict operations.
- **Clock-injected.** Every call takes ``now_ms`` so the state machine is
  testable without touching ``time``.
- **Volume bracket (Option A′).** The one thing an outage *can* report beyond
  "it happened" is how much traded while it was dark: Sina publishes today's
  *cumulative* traded volume per symbol, so the delta between the last pre-gap
  reading and the first post-gap one reconstructs turnover over the window. That
  is still not the lost ticks — it is a bracket around them, and is labelled as
  such (``volume_before``/``volume_after`` are ``None`` when no symbol could be
  bracketed on both sides).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final

_MS_PER_SECOND: Final = 1000.0


@dataclass(frozen=True)
class OutageEpisode:
    """One bounded outage window for the poll source.

    A snapshot taken at any instant: while the outage is live ``closed_at_ms``
    and ``duration_sec`` are ``None``; once healed they are populated. Frozen so
    a consumer holding an episode (e.g. the DB sink) can never observe it
    mutate underneath it — an open snapshot stays a faithful record of the
    moment it was taken.

    Identity is ``opened_at_ms`` (with ``source``): the sink upserts on that
    pair so the open row and the close row update the *same* record. It is
    deliberately *not* a per-process counter — a counter restarts at 1 in a new
    process, so the first outage after a restart would overwrite the first
    outage before it.

    ``volume_before``/``volume_after`` bracket the traded volume across the gap
    (Option A′): the summed cumulative volume of the symbols already trading
    when the source went dark and of those still trading when it came back.
    Both are ``None`` when no symbol could be observed on *both* sides — a
    missing bracket is reported as missing, never as zero. ``volume_symbols``
    says how many symbols the bracket covers, because a delta over one of eighty
    symbols is a very different claim from a delta over all of them.
    """

    source: str
    opened_at_ms: int
    failed_batches: int
    symbols_affected: frozenset[str]
    closed_at_ms: int | None = None
    volume_before: float | None = None
    volume_after: float | None = None
    volume_symbols: int = 0

    @property
    def is_open(self) -> bool:
        return self.closed_at_ms is None

    @property
    def duration_sec(self) -> float | None:
        """Wall-clock length of the outage; ``None`` while still open."""
        if self.closed_at_ms is None:
            return None
        return (self.closed_at_ms - self.opened_at_ms) / _MS_PER_SECOND

    @property
    def volume_delta(self) -> float | None:
        """Traded volume across the gap, or ``None`` when unbracketed."""
        if self.volume_before is None or self.volume_after is None:
            return None
        return self.volume_after - self.volume_before


class OutageTracker:
    """Accumulates batch failures into a single open :class:`OutageEpisode`.

    At most one episode is open at a time — a source is either reachable or it
    is not, and every symbol in a batch shares the connection's fate. The first
    failure opens the episode; while it is open, further failures extend it and
    return ``None`` (the sink persists at *open* only, so a restart mid-outage
    leaves a visible open row rather than a silent one). The first *healthy*
    success closes it and returns the final episode.
    """

    def __init__(self, *, source: str = "astock_intraday") -> None:
        self._source = source
        self._open: OutageEpisode | None = None
        # Last cumulative volume seen per symbol, refreshed on every healthy
        # fetch — the "before" side a future gap's delta is measured from.
        self._volume: dict[str, float] = {}
        # The same map, frozen at the instant the open episode began.
        self._before: dict[str, float] = {}

    def open_episode(self) -> OutageEpisode | None:
        """The currently-open episode, or ``None`` when the source is healthy.

        Returns an immutable snapshot — the tracker keeps no reference the
        caller could mutate.
        """
        return self._open

    def record_batch_failure(
        self, chunk: list[str], now_ms: int
    ) -> OutageEpisode | None:
        """Record a failed batch fetch.

        Returns the newly-opened episode on the *first* failure of an outage
        (the sink's cue to persist), else ``None``.
        """
        if self._open is None:
            # Bracket the pre-gap side now: the last volume seen for each symbol
            # going dark. A symbol with no entry was never observed, so it gets
            # no bracket — an unknown "before" cannot be invented later.
            self._before = {
                symbol: self._volume[symbol]
                for symbol in chunk
                if symbol in self._volume
            }
            self._open = OutageEpisode(
                source=self._source,
                opened_at_ms=now_ms,
                failed_batches=1,
                symbols_affected=frozenset(chunk),
                volume_before=sum(self._before.values()) if self._before else None,
                volume_symbols=len(self._before),
            )
            return self._open
        # Extend the open episode: same opening instant, wider coverage. A
        # symbol first seen in a *later* failed batch is still bracketed against
        # its pre-gap volume — it has not succeeded since the outage began, so
        # the map still holds that value.
        for symbol in chunk:
            if symbol in self._volume and symbol not in self._before:
                self._before[symbol] = self._volume[symbol]
        self._open = replace(
            self._open,
            failed_batches=self._open.failed_batches + 1,
            symbols_affected=self._open.symbols_affected | frozenset(chunk),
            # Keep the live snapshot's bracket coherent with the map; the close
            # row recomputes it from `self._before` anyway.
            volume_before=sum(self._before.values()) if self._before else None,
            volume_symbols=len(self._before),
        )
        return None

    def record_batch_success(
        self,
        chunk: list[str],
        now_ms: int,
        *,
        healthy: bool,
        volumes: Mapping[str, float] | None = None,
    ) -> OutageEpisode | None:
        """Record a successful batch fetch.

        ``healthy`` must be the caller's payload verdict (``tickers or seen``),
        *not* bare transport success: a 200 whose body yields no payload is a
        format break, not recovery, and must not close the episode. On a
        genuine close, returns the finalised episode; otherwise ``None``.

        ``volumes`` is the cumulative traded volume the batch just reported, per
        symbol. It brackets the post-gap side and refreshes the running map; it
        is optional so a caller with no payload (``healthy=False``) need not
        build one.
        """
        closed: OutageEpisode | None = None
        if self._open is not None and healthy:
            incoming = volumes or {}
            # A symbol counts only if it was seen on *both* sides and its
            # counter did not go backwards. It can go backwards: field 8 is
            # *today's* cumulative volume, so an outage that straddles the
            # session boundary leaves yesterday's total on the "before" side and
            # a small fresh number on the "after" side. Volume cannot be
            # un-traded, so a negative difference is a rolled-over counter, not
            # negative turnover — exclude it rather than report the nonsense.
            pairs = {
                symbol: (before, incoming[symbol])
                for symbol, before in self._before.items()
                if symbol in incoming and incoming[symbol] >= before
            }
            closed = replace(
                self._open,
                closed_at_ms=now_ms,
                volume_before=sum(before for before, _ in pairs.values())
                if pairs
                else None,
                volume_after=sum(after for _, after in pairs.values())
                if pairs
                else None,
                volume_symbols=len(pairs),
            )
            self._open = None
            self._before = {}
        if healthy:
            # Refresh the running map for the *next* gap. Independent of the
            # bracket above, which reads `self._before` — the snapshot frozen at
            # open — not this map.
            self._volume.update(volumes or {})
        return closed
