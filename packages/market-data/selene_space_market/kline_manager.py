"""Local-first K-line access with remote backfill.

``KlineDataManager`` sits between the UI/backtests and the exchange REST
layer. Every read goes through a source policy:

* ``local``  — SQLite only, never touches the network.
* ``remote`` — force an exchange fetch, then persist it.
* ``auto``   — serve SQLite when it holds at least ``min_candles``,
               otherwise fall through to remote.

Beyond plain reads it owns three maintenance paths: incremental tail sync,
backwards pagination for deep history, and hole detection/repair inside a
partition (the only mechanism that can fix gaps in the *middle* of a series).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from selene_space_market.kline_db import KlineDB
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


@dataclass
class SweepReport:
    """Outcome of one gap-sweep round across the scanned partitions."""

    partitions_scanned: int = 0
    holes_found: int = 0
    holes_filled: int = 0
    holes_permanent: int = 0
    partitions_advanced: int = 0
    pages_fetched: int = 0
    quality: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class KlineDataManager:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            # Single source of truth (repo-root data/klines.db, env override).
            from selene_space_core.kline_paths import default_klines_db_path

            db_path = default_klines_db_path()
        self._db = KlineDB(db_path)
        self._service: MarketDataService | None = None

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
                fresh_tail = [
                    k for k in pages if latest_ts is None or k.timestamp > latest_ts
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
    ) -> list[KlineEvent]:
        """Collect candles strictly below ``end_ts``, walking down toward ``stop_ts``.

        Loop exits on empty page or non-advancing cursor — never on
        ``len(page) < _PAGE``, because spot pages cap under the batch size and
        would cut deep-history fills short. ``max_pages`` bounds the walk
        (``None`` = unbounded); the caller keeps whatever was collected when the
        budget runs out.
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
            except Exception:
                logger.warning(
                    "backward page failed %s @%s", symbol, cursor, exc_info=True
                )
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
        max_partitions: int = 50,
        page_budget: int = 6,
        quality_checker: object | None = None,
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
        """
        report = SweepReport()
        partitions = [
            p for p in self._db.list_partitions() if (p[0], p[1]) in allowed_sources
        ]
        if max_partitions and len(partitions) > max_partitions:
            report.warnings.append(
                f"partition budget {max_partitions} < {len(partitions)} in-domain; truncated"
            )
            partitions = partitions[:max_partitions]

        for exchange, market_type, symbol, interval in partitions:
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
            except Exception:
                report.warnings.append(f"sweep failed {symbol} {interval}")
                logger.warning(
                    "sweep partition failed %s %s", symbol, interval, exc_info=True
                )
        return report

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
        holes = self._db.find_gaps(
            exchange, market_type, symbol, interval, step_ms, since_ts=None
        )
        service = await self._get_service()
        now_ms = _utcnow_ms()
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
                )
            except Exception:
                pages = []
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
                    exchange, market_type, symbol, interval, step_ms, since_ts=hole_left
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

        # Watermark advances only when no non-permanent hole remains.
        if not self._db.list_holes(
            exchange, market_type, symbol, interval, only_open=True
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
        """Interval string to milliseconds ("M" approximated as 30d)."""
        unit_ms = {
            "m": 60_000,
            "h": 3_600_000,
            "d": _MS_PER_DAY,
            "w": 604_800_000,
            "M": 2_592_000_000,
        }
        value = int(interval[:-1]) if len(interval) > 1 else 1
        return value * unit_ms.get(interval[-1], 60_000)

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
