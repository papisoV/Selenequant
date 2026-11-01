"""Sentinel scheduler — asyncio periodic task runner for grid AI services.

Runs on configurable intervals:
  - Sentinel patrol: inspects all grids, returns risk assessment
  - Market analysis: multi-timeframe BTC market regime report
  - Daily report: PnL attribution and actionable advice
  - Out-of-range monitoring: detects false→true OOR transitions,
    triggers AI advice and push notifications

All AI failures degrade gracefully — errors are logged but never block
the trading loop.

Ported design from an external reference implementation's start() / _tick() / _checkOutOfRange().
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from selene_space_agent.tools.sentinel_tools import (
    GridDailyReportTool,
    GridMarketAnalysisTool,
    GridOutOfRangeAdviceTool,
    GridSentinelTool,
    _EX_NAMES,
)
from selene_space_agent.tools.grid_control_tools import GridChatControlTool

logger = logging.getLogger(__name__)


def _send_grid_notification(text: str) -> None:
    """Fire-and-forget notification via pulse.

    Import here to avoid circular deps at module level.  Falls back to
    ``logging.warning`` if pulse is unavailable.
    """
    try:
        from selene_space_pulse.notify import send_grid_alert

        send_grid_alert(text)
    except ImportError:
        logger.info("[grid-notify] %s", text)


# ---------------------------------------------------------------------------
# SentinelConfig
# ---------------------------------------------------------------------------


class SentinelConfig:
    """Interval configuration (all values in minutes, 0 = disabled)."""

    def __init__(
        self,
        sentinel_min: float = 5.0,
        market_min: float = 30.0,
        report_hour: int = 20,
        oor_check_sec: float = 30.0,
    ) -> None:
        self.sentinel_min = sentinel_min
        self.market_min = market_min
        self.report_hour = report_hour
        self.oor_check_sec = oor_check_sec


# ---------------------------------------------------------------------------
# SentinelScheduler
# ---------------------------------------------------------------------------


class SentinelScheduler:
    """Asyncio-based periodic scheduler for grid AI services.

    Usage::

        scheduler = SentinelScheduler(engines, llm, config)
        await scheduler.start()
        # ... runs until stop() ...
        await scheduler.stop()
    """

    def __init__(
        self,
        engines: dict[str, Any],
        llm: Any,
        config: SentinelConfig | None = None,
        beat_repo: Any = None,
    ) -> None:
        self._engines = engines
        self._llm = llm
        self._config = config or SentinelConfig()
        self._beat_repo = beat_repo

        # Tool instances (reused across invocations)
        self._sentinel_tool = GridSentinelTool(engines, llm)
        self._market_tool = GridMarketAnalysisTool(engines, llm)
        self._report_tool = GridDailyReportTool(engines, llm)
        self._oor_tool = GridOutOfRangeAdviceTool(engines, llm)
        self._chat_tool = GridChatControlTool(engines, llm)

        # Last-run timestamps
        self._last_sentinel_at: float = 0.0
        self._last_market_at: float = 0.0
        self._report_done_day: str | None = None

        # Out-of-range transition tracking
        self._prev_oor: dict[str, bool] = {}

        # Results cache
        self._sentinel_result: dict[str, Any] | None = None
        self._market_result: dict[str, Any] | None = None
        self._report_result: dict[str, Any] | None = None
        self._oor_results: dict[str, dict[str, Any]] = {}

        # Busy guards
        self._busy = {"sentinel": False, "market": False, "report": False}

        # Notification push tracking (avoid spam)
        self._last_push_level: str = "ok"
        self._last_push_at: float = 0.0

        # Internal tasks
        self._main_task: asyncio.Task | None = None
        self._oor_task: asyncio.Task | None = None
        self._running: bool = False

        # Initialize report baseline
        self._report_tool.rebaseline()

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduler loops."""
        if self._running:
            return
        self._running = True
        self._main_task = asyncio.create_task(self._main_loop())
        self._oor_task = asyncio.create_task(self._oor_loop())
        logger.info("SentinelScheduler started")

    async def stop(self) -> None:
        """Stop the scheduler loops."""
        self._running = False
        for task in (self._main_task, self._oor_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._main_task = None
        self._oor_task = None
        logger.info("SentinelScheduler stopped")

    # -- main tick loop (1-min heartbeat) ------------------------------------

    async def _main_loop(self) -> None:
        """1-minute heartbeat that drives sentinel, market, and daily report."""
        while self._running:
            try:
                await self._tick()
            except Exception:
                logger.exception("Sentinel tick error")
            await asyncio.sleep(60)

    async def _beat(self) -> None:
        """Write a liveness row (per tick) if a repo is injected.

        Failures degrade to a debug log — liveness write must never block
        the main loop.  With no repo configured, this is a no-op so the
        scheduler stays a pure periodic runner.
        """
        if self._beat_repo is None:
            return
        try:
            await self._beat_repo.beat(
                "scheduler",
                role="scheduler",
                metadata={
                    "sentinel": self._sentinel_result is not None,
                    "market": self._market_result is not None,
                    "report": self._report_result is not None,
                },
            )
        except Exception:
            logger.debug("liveness write failed", exc_info=True)

    async def _tick(self) -> None:
        await self._beat()
        now = time.time()

        # Sentinel patrol
        if (
            self._config.sentinel_min > 0
            and now - self._last_sentinel_at >= self._config.sentinel_min * 60
        ):
            self._last_sentinel_at = now
            await self._run_sentinel()

        # Market analysis
        last_mkt = max(self._last_market_at, (self._market_result or {}).get("t", 0))
        if (
            self._config.market_min > 0
            and now - last_mkt >= self._config.market_min * 60
        ):
            self._last_market_at = now
            await self._run_market()

        # Daily report
        import datetime

        d = datetime.datetime.now()
        day = d.strftime("%Y-%m-%d")
        if (
            self._config.report_hour >= 0
            and d.hour == self._config.report_hour
            and self._report_done_day != day
        ):
            self._report_done_day = day
            await self._run_report()

    # -- out-of-range loop (30s) ---------------------------------------------

    async def _oor_loop(self) -> None:
        """Fast loop for out-of-range transition detection."""
        while self._running:
            try:
                await self._check_oor()
            except Exception:
                logger.exception("OOR check error")
            await asyncio.sleep(self._config.oor_check_sec)

    async def _check_oor(self) -> None:
        for key in ("de", "ex", "rs"):
            eng = self._engines.get(key)
            if eng is None:
                continue
            s = eng.get_state()
            cur = s.running and s.out_of_range
            prev = self._prev_oor.get(key, False)
            self._prev_oor[key] = cur
            # Only trigger on false→true transition
            if cur and not prev:
                await self._advise_oor(key)

    # -- individual runners --------------------------------------------------

    async def _run_sentinel(self) -> None:
        if self._busy["sentinel"]:
            return
        self._busy["sentinel"] = True
        try:
            result = await self._sentinel_tool.async_execute({})
            if result and not result.error:
                self._sentinel_result = {"t": time.time(), "output": result.output}
                # Push notification on level change
                self._maybe_push_sentinel(result.output)
        except Exception:
            logger.exception("Sentinel run failed")
        finally:
            self._busy["sentinel"] = False

    async def _run_market(self) -> None:
        if self._busy["market"]:
            return
        self._busy["market"] = True
        try:
            # Auto-pick first exchange with real data
            for key in ("ex", "de", "rs"):
                eng = self._engines.get(key)
                if eng is None:
                    continue
                ex = eng._ex
                if getattr(ex, "data_source", None) != "real":
                    continue
                result = await self._market_tool.async_execute({"exchange": key})
                if result and not result.error:
                    self._market_result = {
                        "t": time.time(),
                        "output": result.output,
                        "source": key,
                    }
                break
        except Exception:
            logger.exception("Market analysis run failed")
        finally:
            self._busy["market"] = False

    async def _run_report(self) -> None:
        if self._busy["report"]:
            return
        self._busy["report"] = True
        try:
            result = await self._report_tool.async_execute({})
            if result and not result.error:
                self._report_result = {"t": time.time(), "output": result.output}
                _send_grid_notification("【网格机器人·日报】\n" + result.output[:3900])
        except Exception:
            logger.exception("Daily report run failed")
        finally:
            self._busy["report"] = False

    async def _advise_oor(self, key: str) -> None:
        try:
            result = await self._oor_tool.async_execute({"exchange": key})
            if result and not result.error:
                self._oor_results[key] = {"t": time.time(), "output": result.output}
                eng = self._engines.get(key)
                s = eng.get_state() if eng else None
                price = s.last_price if s else "?"
                market = s.config.display_name if s and s.config else "?"
                _send_grid_notification(
                    f"【网格机器人·出区间】{_EX_NAMES.get(key, key)} {market} "
                    f"价格冲出区间（现价 {price}）。\n{result.output[:3500]}"
                )
        except Exception:
            logger.exception("OOR advice failed for %s", key)

    # -- notification push logic (de-duplicated) -----------------------------

    def _maybe_push_sentinel(self, output: str) -> None:
        """Push notification only when level changes or 30 min cooldown passed."""
        level = "ok"
        lower_output = output.lower()
        if "critical" in lower_output:
            level = "critical"
        elif "warn" in lower_output:
            level = "warn"

        now = time.time()
        if level != "ok" and (
            level != self._last_push_level or now - self._last_push_at > 1800
        ):
            self._last_push_at = now
            self._last_push_level = level
            tag = "严重" if level == "critical" else "注意"
            _send_grid_notification(f"【网格机器人·{tag}】\n{output[:3900]}")
        if level == "ok":
            self._last_push_level = "ok"

    # -- public API ----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return current scheduler state for the API layer."""
        return {
            "running": self._running,
            "sentinel": self._sentinel_result,
            "market": self._market_result,
            "report": self._report_result,
            "oor": self._oor_results,
            "config": {
                "sentinelMin": self._config.sentinel_min,
                "marketMin": self._config.market_min,
                "reportHour": self._config.report_hour,
            },
        }

    @property
    def sentinel_tool(self) -> GridSentinelTool:
        return self._sentinel_tool

    @property
    def market_tool(self) -> GridMarketAnalysisTool:
        return self._market_tool

    @property
    def report_tool(self) -> GridDailyReportTool:
        return self._report_tool

    @property
    def oor_tool(self) -> GridOutOfRangeAdviceTool:
        return self._oor_tool

    @property
    def chat_tool(self) -> GridChatControlTool:
        return self._chat_tool
