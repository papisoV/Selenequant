"""SerenityScoreCache — background-refreshed in-memory cache of Serenity scores.

Provides low-latency access to the latest Serenity fundamental scores
for use as a strategy filter in the StrategyRunner signal execution path.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .pool import SerenityPoolScan

logger = logging.getLogger(__name__)

# Score direction thresholds (same as workflow.py)
_BULLISH_THRESHOLD = 0.6
_BEARISH_THRESHOLD = 0.4


class SerenityScoreCache:
    """Background-refreshed cache of latest Serenity scores per symbol.

    Usage:
        cache = SerenityScoreCache(workflow, symbols=["BTC", "ETH", "SOL"])
        await cache.start(refresh_interval=300)  # 5-minute refresh

        direction = cache.get_direction("BTC")   # 1=bullish, -1=bearish, 0=neutral
        strength = cache.get_strength("BTC")     # 0..1
    """

    def __init__(
        self,
        workflow: Any,
        symbols: list[str] | None = None,
    ) -> None:
        self._workflow = workflow
        self._symbols: list[str] = [s.upper() for s in (symbols or [])]
        self._scores: dict[str, dict[str, Any]] = {}
        self._last_refresh: float = 0.0
        self._task: asyncio.Task | None = None
        self._running = False
        # Theme-pool rank gate (set via set_pool_filter; "off" by default).
        self._pool_rank: dict[str, tuple[int, int]] = {}  # symbol -> (best_rank, size)
        self._pool_mode: str = "off"
        self._pool_top_n: int = 7

    @property
    def is_running(self) -> bool:
        return self._running

    def get_direction(self, symbol: str) -> int:
        """Get Serenity signal direction for a symbol.

        Returns:
            1 if bullish (score > 0.6), -1 if bearish (score < 0.4), 0 if neutral.
            Returns 0 if no score available (unknown symbols pass through).
        """
        sym = symbol.upper()
        # Strip quote currency (e.g., BTC/USDT -> BTC)
        base = sym.split("/")[0] if "/" in sym else sym
        score_data = self._scores.get(base)
        if score_data is None:
            return 0
        # Hard pool filter: outside every pool's top-N, Serenity loses veto power.
        if self._pool_mode == "hard" and not self._in_pool_top(base):
            return 0
        ns = score_data.get("normalized_score", 0.5)
        if ns > _BULLISH_THRESHOLD:
            return 1
        if ns < _BEARISH_THRESHOLD:
            return -1
        return 0

    def get_strength(self, symbol: str) -> float:
        """Get Serenity signal strength for a symbol (0..1).

        Strength is the distance from neutral (0.5), scaled to 0..1.
        Returns 0.0 if no score available.
        """
        sym = symbol.upper()
        base = sym.split("/")[0] if "/" in sym else sym
        score_data = self._scores.get(base)
        if score_data is None:
            return 0.0
        ns = score_data.get("normalized_score", 0.5)
        strength = abs(ns - 0.5) * 2.0
        # Soft pool filter: scale strength by theme-pool rank; out-of-pool neutral.
        if self._pool_mode == "soft":
            rank = self.get_pool_rank(base)
            strength *= rank if rank is not None else 0.5
        return strength

    def get_score(self, symbol: str) -> dict[str, Any] | None:
        """Get the full cached score dict for a symbol, or None."""
        sym = symbol.upper()
        base = sym.split("/")[0] if "/" in sym else sym
        return self._scores.get(base)

    def set_symbols(self, symbols: list[str]) -> None:
        """Update the list of tracked symbols."""
        self._symbols = [s.upper() for s in symbols]

    def set_pool_filter(
        self,
        scans: dict[str, SerenityPoolScan] | None,
        mode: str = "soft",
        top_n: int = 7,
    ) -> None:
        """Enable/disable the theme-pool rank gate.

        Builds a best-position rank map: each symbol gets the highest rank
        (smallest rank number) across all theme pools. ``mode`` is "off"
        (default, no-op), "soft" (scale strength by pool rank), or "hard"
        (direction 0 for symbols outside every pool's top-N). Passing None or
        mode="off" leaves get_direction/get_strength byte-identical to a
        filter-less cache.
        """
        if scans is None or mode == "off":
            self._pool_mode = "off"
            self._pool_rank = {}
            self._pool_top_n = 7
            return
        best: dict[str, tuple[int, int]] = {}
        for scan in scans.values():
            for rank, cand in enumerate(scan.ranked, start=1):
                sym = cand.symbol.upper()
                if sym not in best or rank < best[sym][0]:
                    best[sym] = (rank, len(scan.ranked))
        self._pool_rank = best
        self._pool_mode = mode
        self._pool_top_n = int(top_n)

    def get_pool_rank(self, symbol: str) -> float | None:
        """Serenity theme-pool rank for a symbol: 1.0 at top of pool, ~0 at bottom.

        Best-position across themes; None if the symbol is in no pool.
        """
        sym = symbol.upper()
        base = sym.split("/")[0] if "/" in sym else sym
        entry = self._pool_rank.get(base)
        if entry is None:
            return None
        rank, size = entry
        return 1.0 - (rank - 1.0) / size

    def _in_pool_top(self, base: str) -> bool:
        entry = self._pool_rank.get(base)
        return entry is not None and entry[0] <= self._pool_top_n

    async def start(self, refresh_interval: int = 300) -> None:
        """Start background refresh loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._refresh_loop(refresh_interval), name="serenity-score-cache"
        )
        logger.info(
            "SerenityScoreCache started, %d symbols, refresh every %ds",
            len(self._symbols),
            refresh_interval,
        )

    async def stop(self) -> None:
        """Stop background refresh."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("SerenityScoreCache stopped")

    async def refresh_once(self) -> int:
        """Run a single refresh cycle. Returns number of symbols updated."""
        updated = 0
        for symbol in self._symbols:
            try:
                report = await self._workflow.quick_scan(symbol)
                if report is not None and report.tier1 is not None:
                    self._scores[symbol] = {
                        "normalized_score": report.tier1.normalized_score,
                        "tier": report.tier1.tier,
                        "direction": report.signal_direction,
                        "strength": report.signal_strength,
                        "timestamp": time.time(),
                    }
                    updated += 1
            except Exception:
                logger.debug("Serenity refresh failed for %s", symbol, exc_info=True)
        self._last_refresh = time.time()
        return updated

    async def _refresh_loop(self, interval: int) -> None:
        """Background loop: refresh scores at the given interval."""
        # Initial refresh
        try:
            count = await self.refresh_once()
            logger.info("SerenityScoreCache initial refresh: %d/%d symbols", count, len(self._symbols))
        except Exception:
            logger.warning("SerenityScoreCache initial refresh failed", exc_info=True)

        while self._running:
            try:
                await asyncio.sleep(interval)
                if not self._running:
                    break
                count = await self.refresh_once()
                logger.debug("SerenityScoreCache refresh: %d/%d symbols", count, len(self._scores))
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("SerenityScoreCache refresh error", exc_info=True)
