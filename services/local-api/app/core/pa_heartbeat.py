"""PA heartbeat — daily unattended diagnosis over the favorite watchlist.

The project's own canon is a "continuously running AI quant organization";
the heartbeat is the smallest loop that makes the PA pipeline turn on its
own: after each trading day closes, run the full two-stage diagnosis for
every favorite symbol and land the record in the v009 audit table (A1). That
way decisions keep accumulating — auditable, outside the panel — instead of
existing only while someone is looking at the UI.

Three hard constraints (from ``internal-plan`` §3 T1):

1. **Strictly serial, one symbol at a time.** The shared trading adapter is a
   single ``AsyncBackendToSyncAdapter`` whose private loop is created lazily
   with a check-then-set (``llm_compat.py:110-115``) — a concurrent first call
   can build two loops, leaving the backend's ``AsyncClient`` bound to the
   wrong one ("Event loop is closed"). ``last_usage`` is also a shared mutable
   attribute (``llm_compat.py:76``), so concurrency corrupts token
   attribution. This loop therefore ``await``\\s symbols one by one and never
   ``gather``\\s.

2. **Cost budget gate.** One diagnosis is two real LLM calls (10-15k input
   tokens, 16384 output cap — ``decision_workflow_service.py:181``).
   ``SELENE_PA_HEARTBEAT_DAILY_MAX`` bounds diagnoses per day and every
   attempt logs its token usage. Attempts — not successes — are counted,
   because a call that fails validation has still been paid for.
   *Known limitation:* the counter is in-memory and day-keyed, so a process
   restart resets it; a crash loop could in the worst case re-spend the day's
   budget. Persisting it is deferred until the loop has real runtime.

3. **klines freshness precondition.** klines.db is only written by the startup
   incremental sync (``main.py:237``) and per-symbol when a favorite is added
   (``api/v1/favorites.py:109``) — there is no periodic global resync. The
   watchlist is therefore taken from favorites, which is precisely the input
   the sync set is built from (``main.py:1014-1016``:
   ``all_sub_symbols = default_sym_list + fav_symbols``), and both the db file
   and each symbol's series are verified before diagnosing. Missing data skips
   the symbol and logs — the pipeline is never run against empty or thin bars.
   Beyond presence, the last bar's age is checked against a ``3 x interval``
   tolerance (``_STALENESS_MULTIPLE``): a series whose last bar closed more
   than that ago is skipped as *stale* before any LLM spend — a weeks-old db
   is never silently diagnosed as "current". An unknown interval string
   degrades to a warning rather than blocking the round.

Gate ``SELENE_PA_HEARTBEAT`` defaults to off; disabled means no task is
created and no config is read (zero behavior). Demo mode short-circuits the
whole round: the heartbeat is an unattended writer to the audit table, and
running canned output through it would fill that table with synthesized
records.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from selene_space_core.kline_paths import default_klines_db_path
from typing import Any

from app.core.llm_usage import budget_exceeded
from app.core.trading_input import build_trading_input

logger = logging.getLogger(__name__)

# Daily bars close at 00:00 UTC; diagnosing in the first minute of the new
# day reads the bar that just closed.
_DEFAULT_HOUR_UTC = 0
# 20 diagnoses/day leaves headroom over a typical 5-symbol watchlist while
# still bounding a runaway (e.g. a 30-symbol favorites list, or a restart loop).
_DEFAULT_DAILY_MAX = 20
_DEFAULT_INTERVAL = "1h"
_DEFAULT_BARS = 120
_DEFAULT_MARKET_TYPE = "futures"
# HTF context costs no extra LLM call — it rides along in the same prompt.
_DEFAULT_HTF_INTERVAL = "4h"
# Unset = the pipeline's own default (no 【交易倾向】 section in the Stage-2
# prompt). Set SELENE_PA_HEARTBEAT_TRADING_STANCE to one of the four stances
# to make unattended diagnoses lean the same way the PA panel does.
_DEFAULT_TRADING_STANCE: str | None = None
# A bar is stale when its close time is more than this many intervals ago.
# 3 covers a missed tick + a weekend pause for the 1d cadence, without ever
# treating a 3-week-old db as "current".
_STALENESS_MULTIPLE = 3
# Incremental-resync lookback: Phase A only fetches past the latest local
# stamp, so the days value only matters for a first-ever (empty) partition —
# and the heartbeat never diagnoses a series thinner than 50 bars anyway.
_RESYNC_DAYS = 30
_TICK_SECONDS = 60.0

_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}

#: Intervals the scheduler can bound (staleness table above doubles as the
#: whitelist the settings page validates against — one table, no drift).
SUPPORTED_INTERVALS: tuple[str, ...] = tuple(_INTERVAL_MS)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def heartbeat_enabled() -> bool:
    """True when the heartbeat is switched on in settings or by env.

    Settings是用户面开关(设置页 PA 心跳);env ``SELENE_PA_HEARTBEAT`` 保留
    作为服务器级强制开启,两者任一为真即开。读设置失败按"关"处理 ——
    无人值守的 LLM 写入不该因为一次读盘失败而自己开跑。
    """
    if _env_truthy("SELENE_PA_HEARTBEAT"):
        return True
    return _settings_heartbeat_enabled()


def _settings_heartbeat_enabled(
    settings_loader: Callable[[], Any] | None = None,
) -> bool:
    """从设置里读 ``pa_heartbeat.enabled``;任何异常都按 false。"""
    try:
        if settings_loader is None:
            from app.core.dependencies import get_settings_service

            settings_loader = get_settings_service
        svc = settings_loader()
        if svc is None:
            return False
        section = svc.get_pa_heartbeat()
        return bool(section.get("enabled", False))
    except Exception:
        logger.debug("PA heartbeat settings read failed", exc_info=True)
        return False


def _safe_settings(settings_loader: Callable[[], Any]) -> Any:
    """Resolve the settings service, or None — budgets treat None as no cap."""
    try:
        return settings_loader()
    except Exception:
        logger.debug("PA heartbeat settings service unavailable", exc_info=True)
        return None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "PA heartbeat: %s=%r is not an int — using %d", name, raw, default
        )
        return default


def _coerce_int(value: Any, default: int) -> int:
    """Settings values arrive as JSON; a non-integer keeps the current one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _next_fire_at(
    now: datetime.datetime, hour_utc: int, *, already_fired_today: bool
) -> float:
    """Epoch seconds of the next firing window.

    The scheduler triggers once per UTC day at ``hour_utc`` — not on a rolling
    interval — so the honest answer is the next occurrence of that hour: the
    current one while it is open and has not run yet, today's while it is still
    ahead, otherwise tomorrow's. Deriving it from the tick period or the
    staleness tolerance would print a time the scheduler will not honour.

    The open window runs from ``hour_utc`` to end of day: a tick that lands
    after the hour still fires the day's round, so an unfired day has its
    window open for as long as the day lasts. That case returns a timestamp at
    or before ``now`` — the round fires on the next tick, which a countdown
    renders as "firing now", where "tomorrow" would be a day too pessimistic.
    """
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if already_fired_today:
        target += datetime.timedelta(days=1)
    return target.timestamp()


class PaHeartbeatConfig:
    """Heartbeat cadence and diagnosis parameters."""

    def __init__(
        self,
        hour_utc: int = _DEFAULT_HOUR_UTC,
        daily_max: int = _DEFAULT_DAILY_MAX,
        interval: str = _DEFAULT_INTERVAL,
        bars: int = _DEFAULT_BARS,
        market_type: str = _DEFAULT_MARKET_TYPE,
        htf_interval: str | None = _DEFAULT_HTF_INTERVAL,
        trading_stance: str | None = _DEFAULT_TRADING_STANCE,
        tick_seconds: float = _TICK_SECONDS,
    ) -> None:
        self.hour_utc = hour_utc
        self.daily_max = daily_max
        self.interval = interval
        self.bars = bars
        self.market_type = market_type
        self.htf_interval = htf_interval
        self.trading_stance = trading_stance
        self.tick_seconds = tick_seconds
        # Fields pinned by ``SELENE_PA_HEARTBEAT_*``; the settings overlay must
        # not fight a server-level override.
        self.env_locked: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> PaHeartbeatConfig:
        """Build from ``SELENE_PA_HEARTBEAT_*`` env vars (defaults otherwise)."""
        interval = os.environ.get("SELENE_PA_HEARTBEAT_INTERVAL", "").strip()
        stance = os.environ.get("SELENE_PA_HEARTBEAT_TRADING_STANCE", "").strip()
        env_vars = {
            "hour_utc": "SELENE_PA_HEARTBEAT_HOUR_UTC",
            "daily_max": "SELENE_PA_HEARTBEAT_DAILY_MAX",
            "interval": "SELENE_PA_HEARTBEAT_INTERVAL",
        }
        config = cls(
            hour_utc=_env_int("SELENE_PA_HEARTBEAT_HOUR_UTC", _DEFAULT_HOUR_UTC),
            daily_max=_env_int("SELENE_PA_HEARTBEAT_DAILY_MAX", _DEFAULT_DAILY_MAX),
            interval=interval or _DEFAULT_INTERVAL,
            # Unknown values are passed through: the orchestrator only injects
            # a 交易倾向 section for known keys, so a typo degrades to "no
            # stance" rather than breaking the round.
            trading_stance=stance or _DEFAULT_TRADING_STANCE,
        )
        config.env_locked = frozenset(
            key for key, name in env_vars.items() if os.environ.get(name, "").strip()
        )
        return config


class PaHeartbeatScheduler:
    """Daily asyncio loop that diagnoses the favorite watchlist.

    Structurally a sibling of ``SentinelScheduler`` (same ``start``/``stop``
    lifecycle, same busy guard, same "errors logged but never block"
    posture); the difference is that a round is a serial pass over the
    watchlist rather than independent jobs.
    """

    def __init__(
        self,
        settings_loader: Callable[[], Any],
        service_loader: Callable[[], Any],
        config: PaHeartbeatConfig | None = None,
        klines_db: Path | None = None,
        sync_loader: Callable[[], Any] | None = None,
        enabled_probe: Callable[[], bool] | None = None,
        beat_repo: Any = None,
    ) -> None:
        self._settings_loader = settings_loader
        self._service_loader = service_loader
        self._config = config or PaHeartbeatConfig()
        # The on/off switch, re-read every tick (settings page or env). The
        # scheduler now lives for the whole process, so this check — not
        # startup wiring — is what keeps "off" at zero behaviour. Injectable
        # so tests need not model the settings/env stack.
        self._enabled_probe = enabled_probe or heartbeat_enabled
        self._klines_db = (
            klines_db if klines_db is not None else default_klines_db_path()
        )
        # Lazy loader for a KlineDataManager-like object with
        # ``sync_incremental(exchange, market_type, symbol, interval, days)``.
        # None (or a loader returning None) simply skips the per-symbol resync —
        # the staleness guard still bounds what gets diagnosed.
        self._sync_loader = sync_loader
        # Liveness sink (WorkerHeartbeatRepository). None keeps the loop a pure
        # periodic runner (tests / no db) — the same posture as SentinelScheduler.
        self._beat_repo = beat_repo

        # Busy guard: a round takes minutes, the tick fires every minute.
        self._busy = False
        # Day of the last completed round (set before the round runs so a long
        # round is not re-triggered on every subsequent tick).
        self._last_day: str | None = None
        # Budget accounting, keyed by UTC date.
        self._budget_day: str | None = None
        self._attempts_today = 0

        self._last_round: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._apply_settings()
        self._running = True
        self._task = asyncio.create_task(self._main_loop())
        logger.info(
            "PaHeartbeatScheduler started (daily at %02d:00 UTC, max %d diagnoses/day)",
            self._config.hour_utc,
            self._config.daily_max,
        )

    def _apply_settings(self) -> None:
        """Overlay the settings-page knobs onto the config.

        Re-applied every tick so a saved change takes effect without a
        restart — that is what the settings page promises to own. Keys pinned
        by ``SELENE_PA_HEARTBEAT_*`` are skipped: a server-level override must
        not be silently reverted by whatever the UI last wrote. A read failure
        keeps the current config rather than guessing.
        """
        locked = self._config.env_locked
        try:
            svc = self._settings_loader()
            if svc is None:
                return
            section = svc.get_pa_heartbeat()
        except Exception:
            logger.debug("PA heartbeat settings overlay failed", exc_info=True)
            return
        if not isinstance(section, dict) or not section:
            return
        if "hour_utc" not in locked and "hour_utc" in section:
            self._config.hour_utc = max(
                0, min(23, _coerce_int(section["hour_utc"], self._config.hour_utc))
            )
        if "daily_max" not in locked and "daily_max" in section:
            self._config.daily_max = max(
                0, _coerce_int(section["daily_max"], self._config.daily_max)
            )
        if "interval" not in locked and str(section.get("interval", "")).strip():
            interval = str(section["interval"]).strip()
            if interval in _INTERVAL_MS:
                self._config.interval = interval
            else:
                logger.warning(
                    "PA heartbeat: ignoring unknown interval %r from settings",
                    interval,
                )

    async def stop(self) -> None:
        self._running = False
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        await self._beat(status="stopped")
        logger.info("PaHeartbeatScheduler stopped")

    # -- liveness ----------------------------------------------------------

    async def _beat(self, *, status: str = "running") -> None:
        """Write a liveness row if a repo is injected.

        Failures degrade to a debug log: liveness must never break the loop.
        Metadata advertises what the loop last *accomplished* (its last round),
        not merely that the process is up — the PA probe reads it back.
        """
        if self._beat_repo is None:
            return
        try:
            await self._beat_repo.beat(
                "pa_heartbeat",
                role="pa_heartbeat",
                status=status,
                metadata={"last_round": self._last_round},
            )
        except Exception:
            logger.debug("PA heartbeat liveness write failed", exc_info=True)

    # -- loop --------------------------------------------------------------

    async def _main_loop(self) -> None:
        """1-minute heartbeat that triggers the daily round."""
        while self._running:
            try:
                await self._beat()
                await self._tick()
            except Exception:
                logger.exception("PA heartbeat tick error")
            await asyncio.sleep(self._config.tick_seconds)

    async def _tick(self) -> None:
        # Re-read the knobs each minute: the settings page only writes a file,
        # and a require-restart toggle is exactly the "visible falsehood" this
        # loop exists to avoid — the UI would say on while the loop still
        # holds yesterday's hour and budget.
        self._apply_settings()
        now = datetime.datetime.now(datetime.timezone.utc)
        day = now.strftime("%Y-%m-%d")
        if self._last_day == day:
            return
        # Fire when the configured hour has been reached, not only while the
        # clock is inside it. A tick that lands late (event-loop stall, machine
        # asleep, hour changed in settings) would otherwise skip the whole day.
        if now.hour < self._config.hour_utc:
            return
        if not self._enabled_probe():
            return
        # Claim the day before awaiting: the round runs for minutes, and the
        # tick fires every minute.
        self._last_day = day
        await self._run_round()

    # -- round -------------------------------------------------------------

    async def _run_round(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            await self._round_body()
        except Exception:
            logger.exception("PA heartbeat round failed")
        finally:
            self._busy = False

    async def _round_body(self) -> None:
        if _env_truthy("SELENE_PA_DEMO_MODE"):
            logger.info(
                "PA heartbeat skipped: demo mode is on — canned records would "
                "pollute the audit table"
            )
            return
        # Re-read here as well as in _tick: the toggle can be switched off
        # while the loop already holds a claimed day, and a round is minutes
        # of real LLM spend.
        if not self._enabled_probe():
            logger.info("PA heartbeat skipped: switched off in settings")
            return
        if not self._klines_db.is_file():
            logger.warning(
                "PA heartbeat skipped: klines db not found at %s — sync market data first",
                self._klines_db,
            )
            return

        symbols = self._watchlist()
        if not symbols:
            logger.info("PA heartbeat skipped: no favorite symbols configured")
            return

        svc = self._service_loader()
        if svc is None:
            logger.warning(
                "PA heartbeat skipped: decision-workflow service unavailable"
            )
            return

        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if self._budget_day != today:
            self._budget_day = today
            self._attempts_today = 0

        outcomes: dict[str, int] = {"ok": 0, "skipped": 0, "failed": 0}
        # daily_max == 0 means "no daily cap" (the settings-page default on
        # this machine); only a positive budget stops the loop. Treating 0 as
        # "zero allowed" would turn off the loop behind the user's back.
        capped = self._config.daily_max > 0
        budget_hit = False
        for symbol in symbols:
            if capped and self._attempts_today >= self._config.daily_max:
                budget_hit = True
                logger.warning(
                    "PA heartbeat daily budget exhausted (%d/%d diagnoses) — "
                    "%d symbol(s) not attempted",
                    self._attempts_today,
                    self._config.daily_max,
                    len(symbols) - sum(outcomes.values()),
                )
                break
            # Global token ceiling, checked between symbols rather than inside
            # a diagnosis: one diagnosis is two LLM calls already in flight,
            # so the honest granularity is "do not start another one".
            if budget_exceeded(_safe_settings(self._settings_loader)):
                budget_hit = True
                logger.warning(
                    "PA heartbeat paused: daily LLM token ceiling reached — "
                    "%d symbol(s) not attempted",
                    len(symbols) - sum(outcomes.values()),
                )
                break
            outcomes[await self._diagnose_one(svc, symbol)] += 1

        self._last_round = {
            "day": today,
            "symbols": len(symbols),
            "outcomes": dict(outcomes),
            "attempts_today": self._attempts_today,
            "token_budget_paused": budget_hit,
            "at": time.time(),
        }
        logger.info(
            "PA heartbeat round done: %d ok, %d skipped, %d failed (%d/%d budget used)",
            outcomes["ok"],
            outcomes["skipped"],
            outcomes["failed"],
            self._attempts_today,
            self._config.daily_max,
        )

    async def _resync_one(self, symbol: str) -> None:
        """Bring the symbol's diagnosis partition up to date before reading it.

        Syncs only what the diagnosis reads — ``binance`` on the configured
        ``market_type``, primary + HTF interval — not the four-source sweep
        the add-favorite path does (this is an unattended daily loop, not a
        UI action; extra sources would multiply API load for nothing the
        pipeline consumes). Failures degrade to a warning: the staleness
        guard downstream still bounds what gets diagnosed against stale data.
        """
        if self._sync_loader is None:
            return
        try:
            manager = self._sync_loader()
        except Exception:
            logger.warning(
                "PA heartbeat resync loader failed for %s", symbol, exc_info=True
            )
            return
        if manager is None:
            return
        for interval in (self._config.interval, self._config.htf_interval):
            if not interval:
                continue
            try:
                count = await manager.sync_incremental(
                    "binance",
                    self._config.market_type,
                    symbol,
                    interval,
                    days=_RESYNC_DAYS,
                )
                if count:
                    logger.info(
                        "PA heartbeat resynced %s %s %s: +%d bars",
                        symbol,
                        self._config.market_type,
                        interval,
                        count,
                    )
            except Exception:
                logger.warning(
                    "PA heartbeat resync failed for %s %s %s",
                    symbol,
                    self._config.market_type,
                    interval,
                    exc_info=True,
                )

    async def _diagnose_one(self, svc: Any, symbol: str) -> str:
        """Diagnose one symbol. Never raises — one bad symbol must not abort
        the rest of the round. Returns ``ok`` / ``skipped`` / ``failed``."""
        await self._resync_one(symbol)

        # T3a outcome review runs BEFORE the input build so a decision whose
        # outcome settles this round is visible to *this* round's diagnosis
        # (continuous-learning loop: diagnosis → outcome → next diagnosis).
        # Zero LLM spend, so it sits before the attempt counter and can never
        # consume budget. Best-effort: any miss (nothing to review / still
        # open / lock race) is silent and simply retried by the next round.
        try:
            from app.core.pa_outcome import review_prior_decision

            await review_prior_decision(
                svc,
                symbol,
                self._config.interval,
                market_type=self._config.market_type,
                klines_db=self._klines_db,
            )
        except Exception:  # noqa: BLE001 - never break the diagnosis round
            logger.warning(
                "PA heartbeat outcome review failed for %s", symbol, exc_info=True
            )

        # Settled prior outcomes as a precedent block for the prompt. Fetched
        # after the review above so the just-settled record is included.
        # Best-effort ([] on any failure) — never blocks the diagnosis.
        from app.core.trading_input import fetch_prior_outcomes

        prior_outcomes = await fetch_prior_outcomes(svc, symbol, self._config.interval)

        try:
            # load_bars opens a fresh read-only connection per call, so the
            # worker thread is safe; it still blocks, hence to_thread.
            built = await asyncio.to_thread(
                build_trading_input,
                self._klines_db,
                symbol,
                self._config.interval,
                bars=self._config.bars,
                market_type=self._config.market_type,
                htf_interval=self._config.htf_interval,
                prior_outcomes=prior_outcomes,
            )
        except Exception as exc:
            # Thin or absent series is the expected case here: the symbol is
            # simply not synced yet (load_bars raises RuntimeError).
            logger.warning("PA heartbeat skipped %s: %s", symbol, exc)
            return "skipped"
        # Unpack OUTSIDE the thin-series except: a shape/arity error (e.g. a
        # stale 2-tuple unpack after build_trading_input's contract changed)
        # is a real defect, not a "not synced yet" skip — it must never be
        # swallowed as "skipped" without its own marker (T-K).
        try:
            input_data, htf_summary_data, direction_signal = built
        except (ValueError, TypeError) as exc:
            logger.error(
                "PA heartbeat assembly shape error for %s: %s "
                "(build_trading_input arity mismatch — NOT a sync skip)",
                symbol,
                exc,
                exc_info=True,
            )
            return "failed"

        # Freshness guard: the klines db has no periodic resync, so a db last
        # written weeks ago would otherwise be diagnosed as "current". Bars
        # carry epoch-ms timestamps (snapshot.fetched_at = last bar's ts).
        # Checked BEFORE the attempt counter — a stale skip is free.
        last_bar_ms = (input_data.get("snapshot") or {}).get("fetched_at")
        interval_ms = _INTERVAL_MS.get(self._config.interval)
        if isinstance(last_bar_ms, (int, float)) and interval_ms is not None:
            age_ms = time.time() * 1000 - last_bar_ms
            if age_ms > interval_ms * _STALENESS_MULTIPLE:
                logger.warning(
                    "PA heartbeat skipped %s: stale bars — last %s bar is "
                    "%.1fh old (tolerance %dh); resync market data first",
                    symbol,
                    self._config.interval,
                    age_ms / 3_600_000,
                    interval_ms * _STALENESS_MULTIPLE // 3_600_000,
                )
                return "skipped"
        elif interval_ms is None:
            logger.warning(
                "PA heartbeat freshness check skipped for %s: unknown "
                "interval %r not in staleness table",
                symbol,
                self._config.interval,
            )

        # T3a outcome review already ran above (before the build), so the
        # settled outcome is included in this round's prior_outcomes block.
        input_id = uuid.uuid4().hex
        # Count the attempt, not the success: a call that fails validation
        # has still been paid for.
        self._attempts_today += 1
        try:
            record = await svc.analyze_trading(
                input_data,
                input_id=input_id,
                session_id=uuid.uuid4().hex,
                trading_stance=self._config.trading_stance,
                htf_summary_data=htf_summary_data,
                direction_signal=direction_signal,
            )
        except Exception:
            logger.warning(
                "PA heartbeat diagnosis failed for %s", symbol, exc_info=True
            )
            return "failed"

        usage: dict[str, Any] = dict(getattr(record, "usage_total", None) or {})
        logger.info(
            "PA heartbeat diagnosed %s: input_id=%s prompt_tokens=%s "
            "completion_tokens=%s total_tokens=%s",
            symbol,
            input_id,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )

        # T4 mission sensor: project the diagnosis onto the Mission surface so
        # the four-action decision machine (approve/more_evidence/redirect/
        # park) closes the loop. Local sqlite write — zero LLM, zero budget,
        # after the attempt counter; only successful diagnoses get a mission.
        # Best-effort: a broken repo never aborts the diagnosis round.
        try:
            from app.core.pa_mission_sensor import (
                build_diagnosis,
                sync_diagnosis_to_mission,
            )

            await sync_diagnosis_to_mission(
                symbol=symbol,
                diagnosis=build_diagnosis(
                    input_id=input_id,
                    interval=self._config.interval,
                    market_type=self._config.market_type,
                    trading_stance=self._config.trading_stance,
                ),
            )
        except Exception:  # noqa: BLE001 - never break the diagnosis round
            logger.warning(
                "PA heartbeat mission sensor failed for %s", symbol, exc_info=True
            )
        return "ok"

    def _watchlist(self) -> list[str]:
        """Favorites — the same input the kline sync set is built from.

        Constraint 3 holds by construction as long as the watchlist is
        favorites: ``all_sub_symbols`` (the startup sync's symbol list) is
        ``default_sym_list + fav_symbols``, and adding a favorite syncs that
        symbol immediately. A separate config item would let symbols drift
        outside the synced set.
        """
        settings = self._settings_loader()
        if settings is None:
            logger.warning("PA heartbeat: settings service unavailable")
            return []
        try:
            symbols: list[str] = [str(s) for s in settings.get_favorite_symbols()]
        except Exception:
            logger.warning("PA heartbeat: could not read favorites", exc_info=True)
            return []
        return symbols

    # -- introspection -----------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Current scheduler state for the API / health layer."""
        now = datetime.datetime.now(datetime.timezone.utc)
        return {
            "last_round": self._last_round,
            "attempts_today": self._attempts_today,
            "budget_day": self._budget_day,
            "busy": self._busy,
            "next_fire_at": _next_fire_at(
                now,
                self._config.hour_utc,
                already_fired_today=self._last_day == now.strftime("%Y-%m-%d"),
            ),
            "config": {
                "hour_utc": self._config.hour_utc,
                "daily_max": self._config.daily_max,
                "interval": self._config.interval,
                "htf_interval": self._config.htf_interval,
                "trading_stance": self._config.trading_stance,
            },
        }
