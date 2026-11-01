"""Local-first K-line access with remote backfill.

``KlineDataManager`` sits between the UI/backtests and the exchange REST
layer. Every read goes through a source policy:

* ``local``  — SQLite only, never touches the network.
* ``remote`` — force an exchange fetch, then persist it.
* ``auto``   — serve SQLite when it holds at least ``min_candles``,
               otherwise fall through to remote.

Beyond plain reads it owns four maintenance paths: incremental tail sync,
backwards pagination for deep history, hole detection/repair inside a
partition (the only mechanism that can fix gaps in the *middle* of a series),
and forward tail advance driven from the periodic sweep.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from selene_space_core.session_clock import bar_payload_differs
from selene_space_market.kline_db import GapPredicate, KlineDB
from selene_space_market.service import MarketDataService
from selene_space_types.market import KlineEvent

logger = logging.getLogger(__name__)

SourceMode = Literal["auto", "local", "remote"]

_MS_PER_DAY = 86_400_000
_PAGE = 1500
_PAGE_PAUSE_S = 0.3
# Tail-sync page budget: 6 pages ≈ 9,000 bars covers the largest observed
# downtime without pinning the 20s startup-sync timeout; the sweeper catches
# any remainder.
_TAIL_PAGE_BUDGET = 6
# A sweep only re-fetches a tail that is at least this many bars behind. One
# bar is still forming at any instant, so an exact "latest == now-1" check
# would issue a REST call for every partition every round.
_TAIL_STALE_BARS = 2
# Forward pages per partition per sweep round. 8 × 1,500 = 12,000 bars, which
# covers the worst observed outage (spot 1m: ~90k bars is the record — those
# need repeated rounds, not a single sweep that runs for hours).
_TAIL_ADVANCE_PAGES = 8
# How long a "nothing newer than cursor X" answer is trusted. A delisted symbol
# (or a source lagging behind real time) answers the same thing every round, so
# without a memo it costs one REST call per partition per sweep forever. The
# memo is keyed to the cursor it was recorded at: the moment local data moves,
# the next round probes again, so a genuine recovery is never delayed.
_TAIL_PROBE_TTL_MS = 3_600_000
# The sweep loop reports progress through this cadence rather than at the end:
# a whole-store round over ~900 partitions with hundreds of stale tails is
# minutes-to-hours of wall clock, and a silent loop is indistinguishable from a
# hung one (and leaves no evidence of how far a killed run got).
_SWEEP_PROGRESS_EVERY = 100
# Bars written by a REST read while still forming are never rewritten: both
# tail paths start strictly after the newest local stamp, so a partial bar that
# is no longer the newest row keeps its truncated close and volume forever.
# Measured on the live store: 3,345 such rows across 897 partitions, up to 63
# days old, with closes off by ~$140 and volume understated up to 20×. Each
# round re-fetches a bounded number of them per partition so a partition with
# a long history of partials converges over a few rounds instead of pinning
# the sweep on REST.
_SEAL_BARS_PER_PARTITION = 40
_SEAL_PAGES_PER_PARTITION = 4
_SEAL_PARTITION_BUDGET = 200
# Sources whose REST adapter honours a forward ``start_time`` cursor. OKX's
# adapter narrows by ``start_time`` only *after* fetching the newest page, so a
# forward request for an old bar returns recent bars and can never heal one —
# sealing there would be an eternal no-op with a REST cost per round. Its stale
# partials are left visible rather than silently retried.
_SEAL_FORWARD_SOURCES: frozenset[tuple[str, str]] = frozenset(
    {("binance", "spot"), ("binance", "futures")}
)


def _is_restatement(stored: dict[str, object] | None, incoming: KlineEvent) -> bool:
    """Whether ``incoming`` is a new version of a bar the store already holds.

    Only meaningful for the boundary bar (the one whose timestamp equals the
    partition's latest). A bar is restated in place when any of its numbers
    moves, or when it flips from a forming snapshot to the session's close —
    that flip is the normal daily path, and it carries no timestamp change.

    ``stored`` is ``None`` when the bar is not in the store at all, which cannot
    happen for the latest timestamp but is handled as "nothing to compare"
    rather than raising inside a sync.
    """
    if stored is None:
        return False
    return bool(bar_payload_differs(stored, incoming.model_dump()))


@dataclass
class SweepReport:
    """Outcome of one gap-sweep round across the scanned partitions."""

    partitions_scanned: int = 0
    holes_found: int = 0
    holes_filled: int = 0
    holes_permanent: int = 0
    holes_pruned: int = 0
    partitions_advanced: int = 0
    pages_fetched: int = 0
    stale_open_sealed: int = 0
    # Every transport failure this round (holes, tail advance, seal). A
    # non-zero count means part of the store was never examined.
    transport_errors: int = 0
    # The subset that left a hole unverified. Only this one blocks the
    # watermark: a failed tail fetch says nothing about the interior, which is
    # what ``verified_through`` vouches for.
    hole_transport_errors: int = 0
    quality: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class KlineTransportError(RuntimeError):
    """A kline fetch failed to reach the exchange.

    Distinct from an empty response on purpose. An empty page is the exchange
    answering "nothing here"; a transport error means the question was never
    asked. Only the first may feed the hole ledger or the tail-probe memo.
    """


class KlineDataManager:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            # Single source of truth (repo-root data/klines.db, env override).
            from selene_space_core.kline_paths import default_klines_db_path

            db_path = default_klines_db_path()
        self._db = KlineDB(db_path)
        self._service: MarketDataService | None = None
        # Round-robin cursor for the partition budget: slicing a list every
        # round would starve everything past `max_partitions` forever.
        self._partition_cursor = 0

    async def _get_service(self) -> MarketDataService:
        if self._service is not None:
            return self._service
        logger.warning(
            "KlineDataManager: no service injected; using bare MarketDataService"
        )
        self._service = MarketDataService()
        return self._service

    def set_service(self, service: MarketDataService) -> None:
        """Wire in the app-wide configured service (sources + proxy)."""
        self._service = service

    # -- reads -------------------------------------------------------------

    async def get_klines(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        source: SourceMode = "auto",
        min_candles: int = 10,
    ) -> pd.DataFrame:
        if source == "local":
            return self._read_local(
                exchange, market_type, symbol, interval, start_date, end_date
            )

        if source == "remote":
            return await self._fetch_remote(
                exchange, market_type, symbol, interval, start_date, end_date
            )

        local_df = self._read_local(
            exchange, market_type, symbol, interval, start_date, end_date
        )
        if len(local_df) >= min_candles:
            logger.debug(
                "local hit (%d candles) %s %s", len(local_df), symbol, interval
            )
            return local_df
        logger.info(
            "local short (%d < %d) — remote fallback for %s %s",
            len(local_df),
            min_candles,
            symbol,
            interval,
        )
        return await self._fetch_remote(
            exchange, market_type, symbol, interval, start_date, end_date
        )

    def _read_local(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> pd.DataFrame:
        return self._db.get_klines(
            exchange, market_type, symbol, interval, start_date, end_date
        )

    async def _fetch_remote(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> pd.DataFrame:
        service = await self._get_service()

        limit = _PAGE
        if start_date and end_date:
            span_ms = int((end_date - start_date).total_seconds() * 1000)
            limit = min(span_ms // self.interval_ms(interval) + 100, _PAGE)

        events = await service.fetch_klines_rest(
            symbol=symbol,
            exchange=exchange,
            market_type=market_type,
            interval=interval,
            limit=limit,
        )
        if not events:
            logger.warning(
                "remote returned nothing for %s/%s %s", exchange, market_type, symbol
            )
            return pd.DataFrame()

        self._db.put_klines(events)

        frame = pd.DataFrame(
            [
                {
                    "timestamp": e.timestamp,
                    "open": e.open,
                    "high": e.high,
                    "low": e.low,
                    "close": e.close,
                    "volume": e.volume,
                }
                for e in events
            ]
        )
        if start_date is not None:
            frame = frame[frame["timestamp"] >= int(start_date.timestamp() * 1000)]
        if end_date is not None:
            frame = frame[frame["timestamp"] <= int(end_date.timestamp() * 1000)]
        return frame

    # -- incremental tail sync -------------------------------------------------

    async def sync_incremental(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        days: int = 365,
    ) -> int:
        """Bring one partition up to date.

        Phase A catches the tail up to now (everything past the latest local
        stamp). Phase B walks backwards from the oldest local stamp toward
        ``days`` ago when local history is shallower than requested. Returns
        candles persisted.
        """
        latest_ts = self._db.get_latest_timestamp(
            exchange, market_type, symbol, interval
        )
        target_start_ts = _utcnow_ms() - days * _MS_PER_DAY
        service = await self._get_service()

        # Phase A — recent tail, paginated. A single page only reaches back
        # _PAGE bars, so any downtime longer than that would leave a permanent
        # hole; walk backwards instead, bounded by a page budget so a very old
        # partition can't pin the sync (the sweeper picks up any remainder).
        fresh_tail: list[KlineEvent] = []
        try:
            pages = await self.paginate_backwards(
                service,
                symbol=symbol,
                exchange=exchange,
                market_type=market_type,
                interval=interval,
                end_ts=_utcnow_ms(),
                stop_ts=latest_ts,
                max_pages=_TAIL_PAGE_BUDGET,
            )
            if pages:
                # A daily bar is restated under a stable session-date timestamp:
                # the intraday snapshot and the post-close close are the SAME
                # key, so "strictly newer timestamp" throws away precisely the
                # row that turns a half-formed bar into the session's close —
                # and, since nothing ever revisits it afterwards, that snapshot
                # became permanent. Compare the stored payload for the one
                # boundary bar; bars below it are interior holes, which the gap
                # sweeper owns (and checking each would cost a query apiece).
                stored_last = (
                    self._db.get_bar(exchange, market_type, symbol, interval, latest_ts)
                    if latest_ts is not None
                    else None
                )
                fresh_tail = [
                    k
                    for k in pages
                    if latest_ts is None
                    or k.timestamp > latest_ts
                    or (k.timestamp == latest_ts and _is_restatement(stored_last, k))
                ]
        except Exception:
            logger.warning("tail fetch failed %s %s", symbol, interval, exc_info=True)

        # Phase B — deepen history if the earliest stamp is newer than target
        older_history: list[KlineEvent] = []
        oldest_ts = self._db.get_earliest_timestamp(
            exchange, market_type, symbol, interval
        )

        anchor: int | None = None
        if oldest_ts and oldest_ts > target_start_ts:
            anchor = oldest_ts - 1
        elif latest_ts is None and fresh_tail:
            oldest_fresh = min(k.timestamp for k in fresh_tail)
            if oldest_fresh > target_start_ts:
                anchor = oldest_fresh - 1

        if anchor is not None and anchor > target_start_ts:
            older_history = await self.paginate_backwards(
                service,
                symbol=symbol,
                exchange=exchange,
                market_type=market_type,
                interval=interval,
                end_ts=anchor,
                stop_ts=target_start_ts,
            )

        combined = [*older_history, *fresh_tail]
        if not combined:
            return 0

        unique: dict[int, KlineEvent] = {}
        for event in combined:
            unique.setdefault(event.timestamp, event)

        written = self._db.put_klines(list(unique.values()))
        logger.info(
            "sync_incremental %s %s +%d (tail=%d history=%d)",
            symbol,
            interval,
            written,
            len(fresh_tail),
            len(older_history),
        )
        return written

    # -- backwards pagination ----------------------------------------------------

    async def paginate_backwards(
        self,
        service: MarketDataService,
        *,
        symbol: str,
        exchange: str,
        market_type: str,
        interval: str,
        end_ts: int,
        stop_ts: int | None,
        max_pages: int | None = None,
        raise_on_transport_error: bool = False,
    ) -> list[KlineEvent]:
        """Collect candles strictly below ``end_ts``, walking down toward ``stop_ts``.

        Loop exits on empty page or non-advancing cursor — never on
        ``len(page) < _PAGE``, because spot pages cap under the batch size and
        would cut deep-history fills short. ``max_pages`` bounds the walk
        (``None`` = unbounded); the caller keeps whatever was collected when the
        budget runs out.

        A raising fetch means the question was never asked, which is not the
        same answer as an empty page. Callers that draw a lasting conclusion
        from "the exchange has nothing here" must pass
        ``raise_on_transport_error=True`` and handle :class:`KlineTransportError`
        — see :meth:`_sweep_partition`, where the two answers used to be
        indistinguishable and a multi-round outage made every real hole
        permanent.
        """
        collected: list[KlineEvent] = []
        cursor = end_ts
        pages = 0
        while stop_ts is None or cursor > stop_ts:
            if max_pages is not None and pages >= max_pages:
                logger.warning(
                    "paginate_backwards budget exhausted %s %s (%d pages)",
                    symbol,
                    interval,
                    pages,
                )
                break
            try:
                page = await service.fetch_klines_rest(
                    symbol=symbol,
                    exchange=exchange,
                    market_type=market_type,
                    interval=interval,
                    limit=_PAGE,
                    end_time=cursor,
                )
            except Exception as exc:
                logger.warning(
                    "backward page failed %s @%s", symbol, cursor, exc_info=True
                )
                if raise_on_transport_error:
                    raise KlineTransportError(
                        f"{exchange}/{market_type} {symbol} {interval} "
                        f"@ {cursor}: {exc}"
                    ) from exc
                break
            if not page:
                break
            collected.extend(page)
            pages += 1
            page_min = min(k.timestamp for k in page)
            if page_min >= cursor:
                break  # cursor stalled; bail out rather than spin
            cursor = page_min - 1
            await asyncio.sleep(_PAGE_PAUSE_S)
        return collected

    # legacy alias kept because tests reach this helper directly
    async def _paginate_backwards(self, service, **kwargs: object) -> list[KlineEvent]:
        return await self.paginate_backwards(service, **kwargs)  # type: ignore[arg-type]

    # -- mid-series hole repair -----------------------------------------------

    async def repair_gaps(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        window_days: int = 3,
    ) -> int:
        """Detect and backfill interior holes in the recent window.

        Tail appends and full-window refetches can both leave holes in the
        middle of a partition untouched. Here each gap (consecutive stamps
        more than one interval apart) is re-fetched exactly via backwards
        pagination bounded by its two edges.
        """
        step_ms = self.interval_ms(interval)
        since_dt = datetime.fromtimestamp(
            (_utcnow_ms() - window_days * _MS_PER_DAY) / 1000, tz=timezone.utc
        )

        frame = self._db.get_klines(
            exchange, market_type, symbol, interval, since_dt, None
        )
        if frame is None or len(frame) < 2:
            return 0

        stamps = frame["timestamp"].tolist()
        holes = [
            (left, right)
            for left, right in zip(stamps, stamps[1:], strict=False)
            if right - left > 2 * step_ms
        ]
        if not holes:
            return 0

        logger.info(
            "repair_gaps %s %s: %d hole(s) in last %dd",
            symbol,
            interval,
            len(holes),
            window_days,
        )
        service = await self._get_service()

        written_total = 0
        for hole_start, hole_end in holes:
            try:
                # pass hole_end itself: exchanges treat end_time inclusively on
                # open_time, and hole_start < ts < hole_end filtering happens after
                pages = await self.paginate_backwards(
                    service,
                    symbol=symbol,
                    exchange=exchange,
                    market_type=market_type,
                    interval=interval,
                    end_ts=hole_end,
                    stop_ts=hole_start,
                )
            except Exception:
                logger.warning(
                    "hole fill failed %s [%d,%d]",
                    symbol,
                    hole_start,
                    hole_end,
                    exc_info=True,
                )
                continue

            interior = [k for k in pages if hole_start < k.timestamp < hole_end]
            if interior:
                written_total += self._db.put_klines(interior)
                logger.info(
                    "hole [%d,%d] filled with %d", hole_start, hole_end, len(interior)
                )

        return written_total

    # -- ws disconnect recovery ----------------------------------------------

    async def on_ws_disconnect(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        last_received_ts: int | None,
    ) -> int:
        """Refetch everything after the last pre-disconnect candle."""
        service = await self._get_service()
        try:
            fetched = await service.fetch_klines_rest(
                symbol=symbol,
                exchange=exchange,
                market_type=market_type,
                interval=interval,
                limit=_PAGE,
            )
        except Exception:
            logger.warning("disconnect recovery fetch failed %s", symbol, exc_info=True)
            return 0

        recovery = (
            [
                k
                for k in fetched
                if last_received_ts is None or k.timestamp > last_received_ts
            ]
            if fetched
            else []
        )
        if not recovery:
            return 0

        written = self._db.put_klines(recovery)
        logger.info("ws recovery %s %s: +%d", symbol, interval, written)
        return written

    # -- gap sweep ------------------------------------------------------------

    async def sweep_once(
        self,
        *,
        allowed_sources: set[tuple[str, str]],
        max_partitions: int | None = None,
        page_budget: int = 6,
        quality_checker: object | None = None,
        advance_tail: bool = True,
        tail_page_budget: int = _TAIL_ADVANCE_PAGES,
    ) -> SweepReport:
        """One detection-and-repair round over the scannable partitions.

        Only partitions whose (exchange, market_type) is in ``allowed_sources``
        are scanned — partitions with no registered REST source can't be
        repaired and (for non-24/7 markets) have legitimate weekend/holiday
        gaps, so scanning them would only churn out forever-failing fetches and
        false holes. For each in-domain partition every interior hole is walked
        back; an unfillable hole accrues attempts and eventually goes permanent
        (stops consuming budget, stays visible in health). The watermark
        advances only when no non-permanent hole remains.

        ``advance_tail`` additionally extends the tail of every stale partition
        to now (see :meth:`_advance_tail`). It is the only thing in the process
        that keeps partitions current: the startup sync covers just the
        subscribed interval set, and the hole repair by construction can never
        reach a bar past the newest local one.

        ``max_partitions`` is ``None`` (scan everything) by default. The
        store holds ~900 in-domain partitions; a full detection pass costs
        ~0.3s of count pre-checks, so a small default used to silently drop
        most of the store — BTC/ETH/SOL sat past position 50 in the listing
        and were never scanned.
        """
        report = SweepReport()
        partitions = sorted(
            p for p in self._db.list_partitions() if (p[0], p[1]) in allowed_sources
        )
        if max_partitions and len(partitions) > max_partitions:
            total = len(partitions)
            report.warnings.append(
                f"partition budget {max_partitions} < {total} in-domain; "
                "rotating window"
            )
            start = self._partition_cursor % total
            rotated = partitions[start:] + partitions[:start]
            partitions = rotated[:max_partitions]
            self._partition_cursor = (start + max_partitions) % total
        else:
            self._partition_cursor = 0

        # A tail stalled behind an open hole resumes as soon as that hole goes
        # permanent (round 3), even in the same sweep. The pre-check below is a
        # single indexed count and keeps a fully-current store REST-silent.
        stalled_tails = [
            p
            for p in partitions
            if self._tail_behind(
                p[0],
                p[1],
                p[2],
                p[3],
                self.interval_ms(p[3]),
                _utcnow_ms(),
                _TAIL_STALE_BARS,
            )
        ]

        for index, (exchange, market_type, symbol, interval) in enumerate(
            partitions, 1
        ):
            try:
                step_ms = self.interval_ms(interval)
                await self._sweep_partition(
                    report,
                    exchange=exchange,
                    market_type=market_type,
                    symbol=symbol,
                    interval=interval,
                    step_ms=step_ms,
                    page_budget=page_budget,
                    quality_checker=quality_checker,
                )
                if advance_tail:
                    await self._advance_tail(
                        report,
                        exchange=exchange,
                        market_type=market_type,
                        symbol=symbol,
                        interval=interval,
                        step_ms=step_ms,
                        page_budget=tail_page_budget,
                        include=(exchange, market_type, symbol, interval)
                        in stalled_tails,
                    )
            except Exception:
                report.warnings.append(f"sweep failed {symbol} {interval}")
                logger.warning(
                    "sweep partition failed %s %s", symbol, interval, exc_info=True
                )
            if index % _SWEEP_PROGRESS_EVERY == 0:
                logger.info(
                    "sweep progress %d/%d: holes_found=%d filled=%d permanent=%d "
                    "advanced=%d pages=%d",
                    index,
                    len(partitions),
                    report.holes_found,
                    report.holes_filled,
                    report.holes_permanent,
                    report.partitions_advanced,
                    report.pages_fetched,
                )
        await self._seal_stale_open_bars(report, allowed_sources=allowed_sources)
        return report

    def _gap_predicate(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        step_ms: int,
        stamps: list[int],
    ) -> GapPredicate | None:
        """Hole rule for one partition; ``None`` keeps the fixed-step scan.

        Equity markets trade in sessions, so the fixed-step rule calls every
        overnight/weekend/holiday closure a hole. The session-aware predicate
        needs the label series to learn where within a session the bars sit.
        Crypto (24/7) keeps the original rule verbatim.
        """
        from selene_space_market.kline_calendar import gap_predicate

        return gap_predicate(
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            interval=interval,
            step_ms=step_ms,
            stamps=stamps,
        )

    def _tail_behind(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        step_ms: int,
        now_ms: int,
        stale_bars: int,
    ) -> bool:
        latest_ts = self._db.get_latest_timestamp(
            exchange, market_type, symbol, interval
        )
        return latest_ts is not None and now_ms - latest_ts > stale_bars * step_ms

    async def _seal_stale_open_bars(
        self,
        report: SweepReport,
        *,
        allowed_sources: set[tuple[str, str]] | None,
    ) -> None:
        """Re-fetch bars that were stored while still forming.

        Both tail paths start strictly after the newest local stamp
        (``_advance_tail`` at ``cursor+1``, ``sync_incremental`` filtering
        ``timestamp > latest``), and the hole repair only looks between bars.
        Nothing in the process ever asks for an already-stored bar again, so a
        partial bar that stops being the newest row is frozen at whatever the
        exchange had returned mid-candle.

        Each partition is repaired oldest-first, one exchange page at a time,
        bounded by ``_SEAL_PAGES_PER_PARTITION``; the budget per partition caps
        how much history a single partition with years of partials can demand.
        Only rows the exchange now reports as closed are written back, so a bar
        that is still forming keeps its live flag. Venues whose REST adapter
        cannot seek forward (OKX) are skipped — see ``_SEAL_FORWARD_SOURCES``.
        """
        now_ms = _utcnow_ms()
        open_rows = self._db.list_open_bars(allowed_sources=allowed_sources)
        by_partition: dict[tuple[str, str, str, str], list[int]] = {}
        for exchange, market_type, symbol, interval, ts in open_rows:
            step_ms = self.interval_ms(interval)
            if now_ms - ts < step_ms:
                continue  # genuinely still forming
            by_partition.setdefault(
                (exchange, market_type, symbol, interval), []
            ).append(ts)
        if not by_partition:
            return

        service = await self._get_service()
        for index, ((exchange, market_type, symbol, interval), stamps) in enumerate(
            sorted(by_partition.items())
        ):
            if index >= _SEAL_PARTITION_BUDGET:
                report.warnings.append(
                    f"stale-open seal budget {_SEAL_PARTITION_BUDGET} partitions "
                    f"reached; {len(by_partition) - index} left for next round"
                )
                break
            step_ms = self.interval_ms(interval)
            forward = (exchange, market_type) in _SEAL_FORWARD_SOURCES
            wanted = set(stamps)
            healed: list[KlineEvent] = []
            remaining = sorted(stamps)[:_SEAL_BARS_PER_PARTITION]
            pages = 0
            while remaining and pages < _SEAL_PAGES_PER_PARTITION:
                pages += 1
                if forward:
                    # Forward: start at the oldest wanted stamp and cover one
                    # exchange page of them; the next fetch resumes after it.
                    anchor = remaining[0]
                    cover = [ts for ts in remaining if ts < anchor + _PAGE * step_ms]
                    kwargs: dict[str, object] = {
                        "start_time": anchor,
                        "limit": min((cover[-1] - anchor) // step_ms + 2, _PAGE),
                    }
                else:
                    # OKX reaches a stamp only through ``end_time`` (backwards).
                    # Work down from the newest wanted stamp, one page at a time.
                    anchor = remaining[-1]
                    cover = [ts for ts in remaining if ts >= anchor - _PAGE * step_ms]
                    kwargs = {"end_time": anchor + step_ms, "limit": _PAGE}
                try:
                    batch = await service.fetch_klines_rest(
                        symbol=symbol,
                        exchange=exchange,
                        market_type=market_type,
                        interval=interval,
                        **kwargs,  # type: ignore[arg-type]
                    )
                except Exception:
                    logger.warning(
                        "stale-open seal fetch failed %s %s",
                        symbol,
                        interval,
                        exc_info=True,
                    )
                    report.transport_errors += 1
                    break
                report.pages_fetched += 1
                healed.extend(k for k in batch if k.timestamp in wanted and k.closed)
                if not batch:
                    break
                # Drop from the todo only what this page actually covered. Using
                # the requested limit instead would over-claim on spot, whose
                # page cap (1,000) is below _PAGE (1,500).
                if forward:
                    landed = max(k.timestamp for k in batch)
                    remaining = [ts for ts in remaining if ts > landed]
                else:
                    landed = min(k.timestamp for k in batch)
                    remaining = [ts for ts in remaining if ts < landed]
                await asyncio.sleep(_PAGE_PAUSE_S)
            if not healed:
                continue
            self._db.put_klines(healed)
            report.stale_open_sealed += len(healed)

        if report.stale_open_sealed:
            logger.info(
                "sealed %d stale open bar(s) across %d partition(s)",
                report.stale_open_sealed,
                len(by_partition),
            )

    async def _sweep_partition(
        self,
        report: SweepReport,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        step_ms: int,
        page_budget: int,
        quality_checker: object | None,
    ) -> None:
        report.partitions_scanned += 1
        from selene_space_market.kline_calendar import inspectable_domain

        stamps: list[int] = []
        if inspectable_domain(exchange, market_type, symbol) is not None:
            stamps = self._db.list_timestamps(exchange, market_type, symbol, interval)
        is_hole = self._gap_predicate(
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            interval=interval,
            step_ms=step_ms,
            stamps=stamps,
        )
        holes = self._db.find_gaps(
            exchange,
            market_type,
            symbol,
            interval,
            step_ms,
            since_ts=None,
            is_hole=is_hole,
        )
        service = await self._get_service()
        now_ms = _utcnow_ms()
        # Ledger rows describe jumps as the gap rule saw them when recorded.
        # When the rule changes (session-aware daily detection), rows for jumps
        # it no longer reports would otherwise stay permanent forever and keep
        # health degraded; prune before consulting the ledger.
        report.holes_pruned += self._db.prune_stale_holes(
            exchange, market_type, symbol, interval, holes
        )
        # Holes the sweeper already gave up on (>=3 failed attempts) must not
        # consume REST budget again — PM3: permanent means no auto-retry. The
        # klines table still shows them as gaps, so they resurface from
        # find_gaps() every round; only the ledger distinguishes them.
        permanent = {
            (h.hole_left_ts, h.hole_right_ts)
            for h in self._db.list_holes(
                exchange, market_type, symbol, interval, only_open=False
            )
            if h.permanent
        }
        transport_errors_before = report.hole_transport_errors
        for hole_left, hole_right in holes:
            if (hole_left, hole_right) in permanent:
                report.holes_permanent += 1
                continue
            report.holes_found += 1
            try:
                pages = await self.paginate_backwards(
                    service,
                    symbol=symbol,
                    exchange=exchange,
                    market_type=market_type,
                    interval=interval,
                    end_ts=hole_right,
                    stop_ts=hole_left,
                    max_pages=page_budget,
                    raise_on_transport_error=True,
                )
            except KlineTransportError:
                # The exchange was never asked. Counting this as a failed fill
                # would drive the hole toward permanent on nothing but a dead
                # connection — production shows every open hole reaching
                # attempts=2 during a 13h outage, one round from being
                # blacklisted. Leave the ledger untouched and retry next round.
                report.transport_errors += 1
                report.hole_transport_errors += 1
                logger.warning(
                    "hole repair skipped (transport) %s %s [%d,%d]",
                    symbol,
                    interval,
                    hole_left,
                    hole_right,
                )
                continue
            interior = [k for k in pages if hole_left < k.timestamp < hole_right]
            if not interior:
                state = self._db.record_hole_attempt(
                    exchange,
                    market_type,
                    symbol,
                    interval,
                    hole_left,
                    hole_right,
                    now_ms=now_ms,
                )
                if state.permanent:
                    report.holes_permanent += 1
                continue
            report.pages_fetched += 1
            self._db.put_klines(interior)
            # Re-check: a partial fill leaves the hole open for the next round.
            remaining = [
                (left, right)
                for left, right in self._db.find_gaps(
                    exchange,
                    market_type,
                    symbol,
                    interval,
                    step_ms,
                    since_ts=hole_left,
                    is_hole=is_hole,
                )
                if left >= hole_left and right <= hole_right
            ]
            if remaining:
                self._db.record_hole_attempt(
                    exchange,
                    market_type,
                    symbol,
                    interval,
                    hole_left,
                    hole_right,
                    now_ms=now_ms,
                )
            else:
                self._db.clear_hole(
                    exchange, market_type, symbol, interval, hole_left, hole_right
                )
                report.holes_filled += 1

        # Watermark advances only when no non-permanent hole remains *and* this
        # round actually asked the exchange about every hole it saw. A transport
        # failure leaves no ledger row, so without this guard the store would
        # report "no open holes" and advance the watermark across a gap nobody
        # could verify.
        if (
            report.hole_transport_errors == transport_errors_before
            and not self._db.list_holes(
                exchange, market_type, symbol, interval, only_open=True
            )
        ):
            max_ts = self._db.get_latest_timestamp(
                exchange, market_type, symbol, interval
            )
            if max_ts is not None:
                self._db.set_verified_through(
                    exchange, market_type, symbol, interval, max_ts
                )
                report.partitions_advanced += 1

        if quality_checker is not None:
            self._run_quality(
                report,
                quality_checker,
                exchange,
                market_type,
                symbol,
                interval,
                step_ms,
            )

    async def _advance_tail(
        self,
        report: SweepReport,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        step_ms: int,
        page_budget: int,
        include: bool = False,
    ) -> None:
        """Extend a stale partition toward now, one forward page at a time.

        Holes are only ever found *between* existing bars, so nothing here can
        replace this: a partition whose last local bar is months old stays
        months old no matter how many repaired holes it gets. Every price the
        chart, the backtest and the strategy runner read past that point is a
        silent miss.

        Skipped while an open hole remains: filling above a known gap would
        extend a series whose interior is already known-broken, and the
        watermark invariant (which the hole repair enforces) is about the
        contiguous prefix. ``page_budget`` bounds REST work per round; a
        partition deeper behind than that picks up where it left off next
        round.
        """
        now_ms = _utcnow_ms()
        if not include:
            return
        latest_ts = self._db.get_latest_timestamp(
            exchange, market_type, symbol, interval
        )
        if latest_ts is None:
            return
        if now_ms - latest_ts <= _TAIL_STALE_BARS * step_ms:
            return  # already current — no REST at all
        # The ledger can lag the data: a hole filled by the sync, a backfill or
        # the seal leaves its row behind, and that row would block this tail
        # forever. Reconcile against the store before deciding.
        self._db.prune_filled_holes(exchange, market_type, symbol, interval, step_ms)
        open_holes = self._db.list_holes(
            exchange, market_type, symbol, interval, only_open=True
        )
        if open_holes:
            return

        # A previous round already asked "anything past X?" and got nothing.
        # Trust that answer until local data moves past X or the TTL lapses;
        # otherwise a delisted symbol costs one REST call per sweep forever.
        probed_cursor, probed_at = self._db.get_tail_probe(
            exchange, market_type, symbol, interval
        )
        if (
            probed_cursor is not None
            and probed_at is not None
            and latest_ts <= probed_cursor
            and now_ms - probed_at < _TAIL_PROBE_TTL_MS
        ):
            return

        service = await self._get_service()
        cursor = latest_ts
        newest = latest_ts
        pages = 0
        while pages < page_budget:
            # start_time is inclusive on every exchange this store follows, so
            # cursor+1 avoids re-fetching the bar we already hold.
            try:
                batch = await service.fetch_klines_rest(
                    symbol=symbol,
                    exchange=exchange,
                    market_type=market_type,
                    interval=interval,
                    limit=_PAGE,
                    start_time=cursor + 1,
                )
            except Exception:
                logger.warning(
                    "tail advance fetch failed %s %s", symbol, interval, exc_info=True
                )
                # Count it so the round's snapshot exposes an unreachable
                # exchange instead of reading as "nothing to do".
                report.transport_errors += 1
                break
            pages += 1
            report.pages_fetched += 1
            fresh = [k for k in batch if k.timestamp > cursor]
            if not fresh:
                # Source has nothing past the cursor (delisted / no bars yet).
                # Remember where it stopped so later rounds stay REST-silent.
                self._db.set_tail_probe(
                    exchange,
                    market_type,
                    symbol,
                    interval,
                    cursor_ts=cursor,
                    now_ms=now_ms,
                )
                break
            advance = max(k.timestamp for k in fresh)
            if advance <= cursor:
                # Non-advancing source; bail rather than spin, and memo it so
                # the next round does not repeat the same page fetch.
                self._db.set_tail_probe(
                    exchange,
                    market_type,
                    symbol,
                    interval,
                    cursor_ts=cursor,
                    now_ms=now_ms,
                )
                break
            self._db.put_klines(fresh)
            cursor = advance
            newest = max(newest, advance)
            await asyncio.sleep(_PAGE_PAUSE_S)
            if now_ms - cursor <= _TAIL_STALE_BARS * step_ms:
                break  # caught up
        else:
            report.warnings.append(
                f"tail advance budget {page_budget} pages reached for "
                f"{symbol} {interval}; continuing next round"
            )

        if newest > latest_ts:
            # Data moved, so any memo left by an earlier empty probe describes
            # a cursor the partition has left behind. Drop it rather than let a
            # future round re-derive that from a comparison.
            self._db.clear_tail_probe(exchange, market_type, symbol, interval)
            logger.info(
                "tail advance %s %s +%d bar(s) (%.1f pages)",
                symbol,
                interval,
                int((newest - latest_ts) // step_ms),
                pages,
            )

    def _run_quality(
        self,
        report: SweepReport,
        checker: object,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        step_ms: int,
    ) -> None:
        """Validate one partition; the checker accumulates state keyed by
        symbol only, so it must be reset between partitions."""
        reset = getattr(checker, "reset", None)
        if callable(reset):
            reset()
        validate = getattr(checker, "validate_series", None)
        if not callable(validate):
            return
        frame = self._db.get_klines(exchange, market_type, symbol, interval, None, None)
        if frame is None or frame.empty:
            return
        events = [
            KlineEvent(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                timestamp=int(row.timestamp),
                interval=interval,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                closed=bool(row.closed),
            )
            for row in frame.itertuples()
        ]
        result = validate(events, step_ms)
        report.quality[f"{exchange}:{market_type}:{symbol}:{interval}"] = float(
            result.quality_score
        )

    # -- utilities -----------------------------------------------------------

    @staticmethod
    def interval_ms(interval: str) -> int:
        """Interval string to milliseconds ("M" approximated as 30d).

        Sub-minute units ("1s") and bare milliseconds ("500ms") are supported.
        Before 2026-10-15 "s" was absent from the unit table, so a "1s"
        interval fell through to the 60_000 default and was silently treated
        as one minute.
        """
        body = interval.strip()
        if body.endswith("ms"):
            return int(body[:-2])
        unit_ms = {
            "s": 1_000,
            "m": 60_000,
            "h": 3_600_000,
            "d": _MS_PER_DAY,
            "w": 604_800_000,
            "M": 2_592_000_000,
        }
        value = int(body[:-1]) if len(body) > 1 else 1
        return value * unit_ms.get(body[-1], 60_000)

    # legacy alias used by existing tests
    def _interval_to_ms(self, interval: str) -> int:
        return self.interval_ms(interval)

    def get_stats(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> dict:
        return {
            "count": self._db.count(exchange, market_type, symbol, interval),
            "latest_ts": self._db.get_latest_timestamp(
                exchange, market_type, symbol, interval
            ),
            "earliest_ts": self._db.get_earliest_timestamp(
                exchange, market_type, symbol, interval
            ),
            "has_data": self._db.has_data(exchange, market_type, symbol, interval),
        }


def _utcnow_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
