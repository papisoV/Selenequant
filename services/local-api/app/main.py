"""SeleneQuant Local API — FastAPI entrypoint with lifespan management."""

# load_dotenv must run before any module that reads env at import time
# (12 routers read os.environ at module level). This pushes all imports
# below the call, so E402 is intentional here.
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Notional (price * qty) above which a live fill triggers a trade alert.
# Tunable via env var LARGE_FILL_NOTIONAL_THRESHOLD (default 10_000 USDT).
import os as _os

_LARGE_FILL_NOTIONAL_THRESHOLD: float = float(
    _os.environ.get("LARGE_FILL_NOTIONAL_THRESHOLD", "10000")
)

# Live account id — single source of truth shared by the S0 fills-ledger write
# side (record_fill), T2 daily-loss gate, and T3 fee monitor.
LIVE_ACCOUNT_ID = "live"


def _record_live_fill(fills_ledger, account_id: str):
    """Build the live-fill ledger write callback (fail-open).

    Registered on LiveExecutionEngine.on_fill so every live fill lands in the
    ledger. record_fill failures are swallowed (fail-open): LiveEngine._fire_fill
    dispatches callbacks sequentially with no try/except, so a raising callback
    would abort later callbacks (e.g. WS bridge broadcast).
    """

    def _on_fill(fill) -> None:
        try:
            fills_ledger.record_fill(fill, account_id=account_id)
        except Exception:
            logger.exception("live fill ledger write failed (fail-open)")

    return _on_fill


from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.account import router as account_router
from app.api.v1.admin import router as admin_router
from app.api.v1.agent import router as agent_router
from app.api.v1.alpha import router as alpha_router
from app.api.v1.auth import router as auth_router
from app.api.v1.backtests import router as backtests_router
from app.core.billing_auth import BillingGateMiddleware, billing_mode_enabled
from app.api.v1.beta_play import router as beta_play_router
from app.api.v1.chat_sessions import router as chat_sessions_router
from app.api.v1.cvd import router as cvd_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.decision_signals import router as decision_signals_router
from app.api.v1.decision_workflow import router as decision_workflow_router
from app.api.v1.derivatives import router as derivatives_router
from app.api.v1.experiments import router as experiments_router
from app.api.v1.options import router as options_router
from app.api.v1.heatmap_api import router as heatmap_router
from app.api.v1.liqmap import router as liqmap_router
from app.api.v1.liqratio import router as liqratio_router
from app.api.v1.tof import router as tof_router
from app.api.v1.agg_orderbook import router as agg_orderbook_router
from app.api.v1.gex import router as gex_router
from app.api.v1.signals import router as signals_router
from app.api.v1.kf_index import router as kf_index_router
from app.api.v1.dex import router as dex_router
from app.api.v1.dex_trading import router as dex_trading_router
from app.api.v1.grid import router as grid_router
from app.api.v1.favorites import router as favorites_router
from app.api.v1.health import router as health_router
from app.api.v1.hyperliquid import router as hyperliquid_router
from app.api.v1.intelligence import router as intelligence_router
from app.api.v1.liquidations import router as liquidations_router
from app.api.v1.markets import router as markets_router
from app.api.v1.marketplace import router as marketplace_router
from app.api.v1.mcp import router as mcp_router
from app.api.v1.memory import router as memory_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.onchain import router as onchain_router
from app.api.v1.optimizations import router as optimizations_router
from app.api.v1.optimizations import strategies_router as optimizable_params_router
from app.api.v1.paper import router as paper_router
from app.api.v1.paper_plans import paper_plans_router, experiment_plans_router
from app.api.v1.polymarket import router as polymarket_router
from app.api.v1.qevolve import router as qevolve_router
from app.api.v1.live import router as live_router
from app.api.v1.daily_cycle import router as daily_cycle_router
from app.api.v1.macrodata import router as macrodata_router
from app.api.v1.analytics import router as analytics_router
from app.api.v1.optimize import router as optimize_router
from app.api.v1.agentstudio import router as agentstudio_router
from app.api.v1.renintel import router as renintel_router
from app.api.v1.shadow_profile import router as shadow_profile_router
from app.api.v1.vibe_channels import router as vibe_channels_router
from app.api.v1.regime import router as regime_router
from app.api.v1.portfolio import router as portfolio_router
from app.api.v1.research import router as research_router
from app.api.v1.research_missions import router as research_missions_router
from app.api.v1.research_graph import router as research_graph_router
from app.api.v1.serenity import router as serenity_router
from app.api.v1.settings import router as settings_router
from app.api.v1.setup_backtest import router as setup_backtest_router
from app.api.v1.skills_api import router as skills_router
from app.api.v1.stock_profile import router as stock_profile_router
from app.api.v1.strategies import router as strategies_router
from app.api.v1.trading import router as trading_router
from app.api.v1.us_radar import router as us_radar_router
from app.api.v1.vchart import router as vchart_router
from app.api.v1.widgets import router as widgets_router
from app.api.v1.ws import router as ws_router
from app.api.v1.market_analysis import router as market_analysis_router
from app.mcp.server import router as mcp_channel_router
from app.api.v1.news import (
    router as news_router,
)  # news-ingest (L3 吸收, 末尾追加不重排)
from app.api.v1.fund_ingest import (
    router as fund_ingest_router,
)  # fund-ingest (stock BSD-3 吸收, 末尾追加不重排)
from app.core.dependencies import (
    set_agent_runtime,
    set_db_manager,
    set_dex_fetcher,
    set_execution_engine,
    set_execution_mode,
    set_funding_rate_fetcher,
    set_health_registry,
    set_key_pair_manager,
    set_liquidation_fetcher,
    set_market_service,
    set_mcp_registry,
    set_onchain_fetcher,
    set_paper_managers,
    set_risk_engine,
    set_settings_service,
    set_skill_registry,
    set_strategy_repo,
    set_strategy_runner,
    set_tool_catalog,
)
from app.middleware.error_handler import install_error_handlers
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIDMiddleware
from selene_space_core.diagnostics.health import HealthCheckResult, HealthRegistry
from selene_space_core.kline_paths import default_klines_db_path, repo_root
from selene_space_core.logging import RedactingFilter, RedactingFormatter

# Secrets must be scrubbed from every emitted record: vendors that authenticate
# via query param leave the key in the URL, and httpx quotes the full URL into
# its error message, so an unhandled provider error would log the key verbatim
# (macrodata FRED/BLS). Two layers are needed: the filter scrubs msg/args, the
# formatter scrubs the FINAL rendered string (a traceback from `exc_info=True`
# is rendered at format time, after the filter). The structlog redactor only
# covers structlog-emitted events, not the stdlib `logging` path used here.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s: %(message)s")
_redacting_filter = RedactingFilter()
logging.getLogger().addFilter(_redacting_filter)
for _h in logging.getLogger().handlers:
    _h.setFormatter(RedactingFormatter("%(levelname)s:%(name)s: %(message)s"))
    _h.addFilter(_redacting_filter)
# Console-only logging made production behavior unverifiable (axis C final
# check had no log to grep). RotatingFileHandler caps at 5x10MB.
_log_dir = Path.home() / ".selenespace" / "logs"
try:
    _log_dir.mkdir(parents=True, exist_ok=True)
    _fh = logging.handlers.RotatingFileHandler(
        _log_dir / "local-api.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    _fh.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s:%(name)s: %(message)s")
    )
    _fh.addFilter(_redacting_filter)
    logging.getLogger().addHandler(_fh)
except OSError:
    pass  # console logging still works
logger = logging.getLogger(__name__)

# US-004: ensure Bug C / StopManager critical INFO logs surface even if
# a third-party loguru InterceptHandler raises the default level. These
# loggers must emit INFO: stop_registered, stop_triggered, StopManager wired,
# REST reconciliation messages.
for _logger_name in (
    "app.main",
    "selene_space_exchange.execution.live_engine",
    "selene_space_exchange.execution.stop_manager",
    "selene_space_exchange.execution.live_state",
):
    logging.getLogger(_logger_name).setLevel(logging.INFO)


async def _seed_template_strategies(
    strategies: dict,
    repo: object,
) -> None:
    """Seed template strategies from YAML files if not already in store."""
    import pathlib

    try:
        import yaml
    except ImportError:
        try:
            from selene_space_strategy.models import EngineStrategyConfig  # noqa: F401,F811
        except ImportError:
            return
        # PyYAML not available — skip seeding
        logger.debug("PyYAML not installed, skipping template seeding")
        return

    from selene_space_types.strategy import StrategyConfig

    templates_dir = (
        pathlib.Path(__file__).resolve().parents[3]
        / "packages"
        / "strategy-engine"
        / "selene_space_strategy"
        / "templates"
    )
    if not templates_dir.is_dir():
        logger.debug("Templates directory not found: %s", templates_dir)
        return

    seeded = 0
    # Top-level files first, then the nested theme folders, so an id carried by
    # both resolves to the same file it always did; ``continue`` below makes the
    # first occurrence win.
    yaml_paths = sorted(templates_dir.glob("*.yaml")) + sorted(
        path for path in templates_dir.rglob("*.yaml") if path.parent != templates_dir
    )
    for yaml_path in yaml_paths:
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            if not raw or not isinstance(raw, dict):
                continue
            template_id = raw.get("id", f"template-{yaml_path.stem}")
            if template_id in strategies:
                continue

            config = StrategyConfig(
                id=template_id,
                name=raw.get("name", yaml_path.stem),
                type=raw.get("type", "custom"),
                description=raw.get("description", ""),
                symbols=raw.get("symbols", ["BTC/USDT"]),
                interval=raw.get("interval", "1h"),
                parameters=raw.get("parameters", {}),
                entry_rules=raw.get("entry_rules", {}),
                exit_rules=raw.get("exit_rules", {}),
                risk_params=raw.get("risk_params", {}),
                tags=raw.get("tags", ["template"]),
                source="template",
            )
            strategies[template_id] = config
            if repo is not None and hasattr(repo, "save"):
                await repo.save(config)
            seeded += 1
        except Exception:
            logger.debug("Failed to seed template %s", yaml_path.name, exc_info=True)

    if seeded:
        logger.info("Seeded %d template strategies from YAML files", seeded)


# ── Market-domain routing for symbol → data source ──────────────────────────
# Both the subscription loop and the kline sync used to iterate a **hardcoded
# crypto list** ([("binance","spot"),("binance","futures"),…]) and fire every
# symbol at all of them. A US ticker (AAPL) or A-share code (600519.SH) in the
# user's favorites therefore produced a stream of 400 Bad Request from Binance,
# while the equity sources that were registered for exactly those symbols never
# received a subscription.
#
# The routing now lives in app.core.source_routing so this module and the
# runtime favorites sync (api.v1.favorites) share one implementation instead of
# drifting apart — the runtime path was still hardcoded to the crypto venues.
# The aliases keep the long-standing local names so the call sites below read
# unchanged.
from app.core.source_routing import (
    intervals_for,
    source_domain as _source_domain,
    sources_for_symbol as _sources_for_symbol,
)


async def _kline_startup_sync(
    manager,  # KlineDataManager — imported lazily in _init_engines_and_runtime
    default_symbols: str,
    intervals: list[str],
    sources: list[tuple[str, str]] | None = None,
) -> None:
    """Background task: incrementally sync K-lines to SQLite on startup.

    Runs off the main startup path so it can't block readiness. Each
    (symbol, source, interval) sync is bounded by a hard timeout; on
    consecutive failures the whole sync backs off exponentially (cap 60s)
    instead of hammering a hung exchange and stalling the event loop.

    ``sources`` defaults to the crypto venues; callers that know the registered
    sources should pass them so equity symbols are routed to equity feeds.
    """
    await asyncio.sleep(10)  # wait for service to stabilize
    symbols = [s.strip() for s in default_symbols.split(",") if s.strip()]
    if sources is None:
        sources = [("binance", "futures"), ("binance", "spot")]
    total = 0
    consecutive_failures = 0
    for sym in symbols:
        # Route per symbol: an equity ticker must not be synced from a crypto
        # venue (that is where the AAPL 400s came from).
        sym_sources = _sources_for_symbol(sym, sources)
        if not sym_sources:
            logger.info("No registered source serves %r; skipping kline sync", sym)
            continue
        for ex, mt in sym_sources:
            for ivl in intervals_for(ex, mt, intervals):
                try:
                    async with asyncio.timeout(20.0):
                        count = await manager.sync_incremental(
                            ex, mt, sym, ivl, days=365
                        )
                    if count:
                        logger.info(
                            "Kline sync: %s %s/%s %s +%d", sym, ex, mt, ivl, count
                        )
                        total += count
                    consecutive_failures = 0
                except asyncio.TimeoutError:
                    logger.warning(
                        "Kline sync timed out: %s %s/%s %s (>=20s)", sym, ex, mt, ivl
                    )
                    consecutive_failures += 1
                except Exception:
                    logger.warning(
                        "Kline sync failed: %s %s/%s %s",
                        sym,
                        ex,
                        mt,
                        ivl,
                        exc_info=True,
                    )
                    consecutive_failures += 1
                # Back off on consecutive failures: 5s -> 10s -> 20s -> 40s (cap 60s)
                backoff = (
                    min(5.0 * (2**consecutive_failures), 60.0)
                    if consecutive_failures
                    else 0.5
                )
                await asyncio.sleep(backoff)
    logger.info("Kline startup sync complete, +%d candles total", total)


# Options chain poll cadence; matches DeribitOptionsFetcher._CACHE_TTL so each
# tick repopulates from the 5-min cache instead of hammering the API.
_OPTIONS_POLL_INTERVAL = 300.0


def _iv_snapshot_daily(currency: str, summaries: Any) -> None:
    """Persist one IV term-structure snapshot per (utc_date, currency).

    Best-effort: any failure logs and moves on — snapshotting must never
    break the options refresh loop. See app.core.iv_term_snapshot.
    """
    try:
        from app.core.iv_term_snapshot import append_snapshot, build_snapshot

        snapshot = build_snapshot(currency, summaries)
        if snapshot is not None and append_snapshot(snapshot):
            logger.info(
                "IV term snapshot appended: %s (%d expiries)",
                currency,
                len(snapshot["expiries"]),
            )
    except Exception:
        logger.warning("IV term snapshot failed for %s", currency, exc_info=True)


async def _options_poll_loop(state: dict) -> None:
    """Periodically refresh the Deribit options chain stores (ADR-1).

    The poller is the single writer of the ``_options_*_store`` stores, always
    through ``MarketDataService.fill_options_data``. The interval matches the
    fetcher TTL (``_CACHE_TTL`` = 300s) so each tick repopulates from the
    5-min cache or the network.
    """
    market_svc = state.get("market_svc")
    options_fetcher = state.get("options_fetcher")
    if market_svc is None or options_fetcher is None:
        logger.warning("options poller skipped: market_svc/options_fetcher unavailable")
        return
    consecutive_failures = 0
    while True:
        try:
            for currency in ("BTC", "ETH"):
                async with asyncio.timeout(35.0):
                    chain = await options_fetcher.fetch_chain(currency)
                if chain is not None:
                    market_svc.fill_options_data(
                        chain.instruments, chain.tickers, chain.summaries
                    )
                    _iv_snapshot_daily(currency, chain.summaries)
                    logger.info(
                        "Options chain refreshed: %s (%d inst, %d summaries)",
                        currency,
                        len(chain.instruments),
                        len(chain.summaries),
                    )
            consecutive_failures = 0
        except asyncio.TimeoutError:
            logger.warning("Options chain poll timed out (>=35s)")
            consecutive_failures += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Options chain poll failed")
            consecutive_failures += 1
        # Back off on consecutive failures: 60s -> 120s -> 240s (cap 600s)
        interval = min(_OPTIONS_POLL_INTERVAL * (2**consecutive_failures), 600.0)
        await asyncio.sleep(interval)


# Gap-sweep cadence: detection+repair is cheap (a per-partition count pre-check)
# so a short period keeps holes short-lived without meaningful load.
_KLINE_SWEEP_INTERVAL = 1800.0


async def _kline_gap_sweep_loop(state: dict) -> None:
    """Periodically detect and repair interior kline holes (L2).

    Detection walks every partition that has a registered REST source (a
    partition with no source can neither be repaired nor, for non-24/7 markets,
    be judged on gap structure), so crypto holes older than any rolling window
    are still found. The first round scans the whole store and surfaces any
    pre-existing holes. Each round refreshes the ``kline_quality`` health
    snapshot.
    """
    manager = state.get("kline_manager")
    market_svc = state.get("market_svc")
    if manager is None:
        logger.warning("kline gap sweep skipped: kline_manager unavailable")
        return
    quality_checker = state.get("kline_quality_checker")
    consecutive_failures = 0
    while True:
        try:
            allowed: set[tuple[str, str]] = set()
            if market_svc is not None:
                allowed = {(ex, mt) for (ex, mt) in market_svc.sources}
            report = await manager.sweep_once(
                allowed_sources=allowed, quality_checker=quality_checker
            )
            state["kline_quality_snapshot"] = {
                "scanned": report.partitions_scanned,
                "holes_found": report.holes_found,
                "holes_filled": report.holes_filled,
                "holes_permanent": report.holes_permanent,
                "advanced": report.partitions_advanced,
                "quality": report.quality,
                "warnings": report.warnings,
            }
            if report.holes_found:
                logger.info(
                    "kline sweep: %d partition(s), %d hole(s) found, %d filled, %d permanent",
                    report.partitions_scanned,
                    report.holes_found,
                    report.holes_filled,
                    report.holes_permanent,
                )
            consecutive_failures = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("kline gap sweep failed", exc_info=True)
            consecutive_failures += 1
        interval = min(
            _KLINE_SWEEP_INTERVAL * (2**consecutive_failures), 4 * _KLINE_SWEEP_INTERVAL
        )
        await asyncio.sleep(interval)


# A-share daily refresh cadence: one bar per trading day, so a handful of
# checks per session keeps the detector's baseline current without polling a
# public quote endpoint harder than a human would.
_ASTOCK_DAILY_SYNC_INTERVAL = 1800.0


async def _astock_daily_sync_loop(state: dict, symbols: list[str]) -> None:
    """Periodically refresh A-share daily bars into the live ("astock","spot") partition.

    Nothing else keeps that partition current: the gap sweeper only repairs
    *interior* holes (its watermark is the latest existing bar, so it never
    extends a tail), and ``pa_heartbeat`` explicitly documents that there is
    no periodic global resync. Without this loop the anomaly detector's
    N-day/volume baseline is read from the frozen qfq import and fires level
    signals off prices that no longer exist. Each symbol is walked through the
    existing ``sync_incremental`` path (same REST source, same ``put_klines``
    write the startup sync uses); failures back off exponentially.
    """
    manager = state.get("kline_manager")
    if manager is None:
        logger.warning("A-share daily sync skipped: kline_manager unavailable")
        return
    if not symbols:
        logger.info("A-share daily sync: no reachable A-share symbols; loop idle")
        return
    consecutive_failures = 0
    while True:
        try:
            for sym in symbols:
                async with asyncio.timeout(20.0):
                    count = await manager.sync_incremental(
                        "astock", "spot", sym, "1d", days=365
                    )
                if count:
                    logger.info("A-share daily sync: %s +%d bar(s)", sym, count)
            consecutive_failures = 0
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("A-share daily sync failed", exc_info=True)
            consecutive_failures += 1
        interval = min(
            _ASTOCK_DAILY_SYNC_INTERVAL * (2**consecutive_failures),
            4 * _ASTOCK_DAILY_SYNC_INTERVAL,
        )
        await asyncio.sleep(interval)


# Backoff step for the snapshot universe seed. A module constant (not an
# inline literal) so tests can patch it, matching _ASTOCK_DAILY_SYNC_INTERVAL.
_ASTOCK_SNAPSHOT_SEED_BACKOFF_SEC = 30.0


async def _astock_snapshot_seed_loop(state: dict) -> None:
    """Seed the whole-market snapshot source's poll universe, then stop.

    The source registers in ``_init_market_data`` with an *empty* universe (it
    never touches ashare, which owns the universe list). Without this seed it
    polls nothing and the whole A-share cross-section stays empty — a
    registered-but-inert source, the "部署了≠在跑" failure. The universe lives
    behind ``get_a_share_list`` (7-day cache), which is an ashare import and
    therefore a composition-layer concern.

    Runs as a background task, not inline: the first call may fetch from
    akshare and the lifespan health gate is 60s (2026-10-07 lesson). Retries
    with backoff because a cold cache + offline box would otherwise leave the
    source permanently idle.
    """
    source = state.get("astock_snapshot_source")
    if source is None:
        return
    consecutive_failures = 0
    while True:
        try:
            from selene_space_ashare.data_fetcher import get_a_share_list

            universe = await asyncio.to_thread(get_a_share_list)
            codes = [str(c).strip() for c in universe["code"].tolist()]
            codes = [c for c in codes if c.isdigit() and len(c) == 6]
            if not codes:
                raise ValueError("universe is empty")
            source.set_universe(codes)
            logger.info("A-share snapshot universe seeded: %d code(s)", len(codes))
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            consecutive_failures += 1
            logger.warning(
                "A-share snapshot universe seed failed (attempt %d)",
                consecutive_failures,
                exc_info=True,
            )
        await asyncio.sleep(
            min(_ASTOCK_SNAPSHOT_SEED_BACKOFF_SEC * consecutive_failures, 300.0)
        )


# Backoff step for the ladder warm-up. A module constant (not an inline
# literal) so tests can patch it, matching _ASTOCK_SNAPSHOT_SEED_BACKOFF_SEC.
_ASTOCK_LADDER_WARMUP_BACKOFF_SEC = 30.0


async def _astock_ladder_warmup_loop(state: dict) -> None:
    """Warm the live ladder panel once, then stop.

    The first ``/ashare/phase`` request pays the whole endpoint walk — measured
    2026-10-15 on a cold process with warm disk: **18.5s** for 20 sessions
    (66.3s when the on-disk 10jqka caches are bypassed). The frontend aborts at
    ``NEXT_PUBLIC_FETCH_TIMEOUT_MS`` (12s default), so without this the board
    renders empty on the first load after every restart and only fills on a
    retry. Warming in the background moves that cost off the request path.

    Skipped when the snapshot source is off: the live panel is only consumed by
    the two live endpoints, which fall back to the backfill panel anyway.
    Best-effort — a failure just means the first request pays the cost, so it
    logs and retries with backoff rather than failing startup.
    """
    if state.get("astock_snapshot_source") is None:
        return
    consecutive_failures = 0
    while True:
        try:
            from selene_space_ashare import ladder_live

            await asyncio.to_thread(ladder_live.live_ladder_history)
            logger.info("A-share live ladder panel warmed")
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            consecutive_failures += 1
            logger.warning(
                "A-share live ladder warm-up failed (attempt %d)",
                consecutive_failures,
                exc_info=True,
            )
        await asyncio.sleep(
            min(_ASTOCK_LADDER_WARMUP_BACKOFF_SEC * consecutive_failures, 300.0)
        )


async def _shutdown_lifespan(state: dict) -> None:
    """Shutdown logic extracted from lifespan().

    Receives a state dict with the keys:
    agent_runtime_instance, mcp_registry_instance, _autosave_task,
    _kline_sync_task, strategy_runner_instance, paper_engine, paper_state,
    market_svc, funding_fetcher, liquidation_fetcher, options_fetcher,
    dex_fetcher, onchain_fetcher, serenity_fetcher, db_manager,
    _binance_user_data_clients.

    Mirrors the order and try/except shape of the original finally block
    so behavior is byte-identical.
    """
    agent_runtime_instance = state.get("agent_runtime_instance")
    mcp_registry_instance = state.get("mcp_registry_instance")
    _autosave_task = state.get("_autosave_task")
    _kline_sync_task = state.get("_kline_sync_task")
    _options_poll_task = state.get("_options_poll_task")
    strategy_runner_instance = state.get("strategy_runner_instance")
    paper_engine = state.get("paper_engine")
    paper_state = state.get("paper_state")
    market_svc = state.get("market_svc")
    funding_fetcher = state.get("funding_fetcher")
    liquidation_fetcher = state.get("liquidation_fetcher")
    options_fetcher = state.get("options_fetcher")
    dex_fetcher = state.get("dex_fetcher")
    onchain_fetcher = state.get("onchain_fetcher")
    serenity_fetcher = state.get("serenity_fetcher")
    db_manager = state.get("db_manager")
    _binance_user_data_clients = state.get("_binance_user_data_clients", [])

    # --- Stop TG signal poller ---
    try:
        from app.api.v1.ws import stop_tg_signal_poller

        await stop_tg_signal_poller()
    except Exception:
        logger.debug("TG signal poller stop skipped", exc_info=True)
    # --- Shutdown SentinelScheduler (H10) ---
    # The grid sentinel scheduler runs background asyncio tasks (1-min tick +
    # 30s OOR check). If we don't shut it down, those tasks linger after app
    # shutdown and can fire on a half-torn-down event loop.
    try:
        from app.api.v1.grid import _scheduler as _grid_scheduler

        if _grid_scheduler is not None:
            await _grid_scheduler.stop()
            logger.info("SentinelScheduler stopped on shutdown (H10)")
    except Exception:
        logger.debug("SentinelScheduler stop skipped", exc_info=True)
    # --- Shutdown ---
    set_tool_catalog(None)
    set_agent_runtime(None)
    if agent_runtime_instance is not None:
        try:
            llm_provider = getattr(agent_runtime_instance, "_llm", None)
            if llm_provider is not None and hasattr(llm_provider, "close"):
                await llm_provider.close()
        except Exception:
            logger.warning("Error closing agent LLM provider", exc_info=True)

    # Clean up MCP
    if mcp_registry_instance is not None:
        try:
            await mcp_registry_instance.stop()
        except Exception:
            logger.warning("Error stopping MCP registry", exc_info=True)
    set_mcp_registry(None)
    set_skill_registry(None)

    # FX15: Safe shutdown — guard against uninitialized variables
    if _autosave_task is not None:
        try:
            _autosave_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel paper autosave task on shutdown",
                exc_info=True,
            )
    if _kline_sync_task is not None:
        try:
            _kline_sync_task.cancel()
        except Exception:
            logger.debug("Could not cancel kline sync task on shutdown", exc_info=True)
    # 后台默认订阅 task（2026-10-07 从 lifespan 内联循环改为 task）
    _subscribe_task = state.get("_subscribe_task")
    if _subscribe_task is not None:
        try:
            _subscribe_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel default-subscribe task on shutdown", exc_info=True
            )
    if _options_poll_task is not None:
        try:
            _options_poll_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel options poll task on shutdown", exc_info=True
            )
    # --- Stop kline gap sweeper (L2) — 末尾追加 ---
    _kline_sweep_task = state.get("_kline_sweep_task")
    if _kline_sweep_task is not None:
        try:
            _kline_sweep_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel kline gap sweep task on shutdown", exc_info=True
            )
    # --- Stop A-share daily sync — 末尾追加 ---
    _astock_daily_sync_task = state.get("_astock_daily_sync_task")
    if _astock_daily_sync_task is not None:
        try:
            _astock_daily_sync_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel A-share daily sync task on shutdown", exc_info=True
            )
    # --- Stop A-share snapshot universe seed — 末尾追加 ---
    _astock_snapshot_seed_task = state.get("_astock_snapshot_seed_task")
    if _astock_snapshot_seed_task is not None:
        try:
            _astock_snapshot_seed_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel A-share snapshot seed task on shutdown",
                exc_info=True,
            )
    # --- Cancel news-ingest 3 loops (binance-quant L3 吸收) — 末尾追加 ---
    for _news_task in state.get("_news_ingest_tasks") or []:
        try:
            _news_task.cancel()
        except Exception:
            logger.debug("Could not cancel news-ingest task on shutdown", exc_info=True)
    # --- Cancel fund-ingest alert loop (stock BSD-3 吸收) — 末尾追加 ---
    _fi_alert_task = state.get("_fund_ingest_alert_task")
    if _fi_alert_task is not None:
        try:
            _fi_alert_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel fund-ingest alert task on shutdown", exc_info=True
            )
    # --- Cancel PA price alert loop (S7) — 末尾追加 ---
    _pa_alert_task = state.get("_pa_trade_alert_task")
    if _pa_alert_task is not None:
        try:
            _pa_alert_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel PA price alert task on shutdown", exc_info=True
            )
    # --- Stop PA heartbeat loop (T1) — 末尾追加 ---
    # Stopped here (before the DB handles below close) so an in-flight round
    # is not left writing to a torn-down database.
    _pa_heartbeat = state.get("_pa_heartbeat")
    if _pa_heartbeat is not None:
        try:
            await _pa_heartbeat.stop()
        except Exception:
            logger.debug("Could not stop PA heartbeat on shutdown", exc_info=True)
    # --- Cancel ashare style warmup task (batch6 US-2) — 末尾追加 ---
    _ashare_warmup_task = state.get("_ashare_warmup_task")
    if _ashare_warmup_task is not None:
        try:
            _ashare_warmup_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel ashare style warmup task on shutdown", exc_info=True
            )
    # --- Cancel A-share live ladder warm-up — 末尾追加 ---
    _ladder_warmup_task = state.get("_astock_ladder_warmup_task")
    if _ladder_warmup_task is not None:
        try:
            _ladder_warmup_task.cancel()
        except Exception:
            logger.debug(
                "Could not cancel ashare ladder warm-up task on shutdown",
                exc_info=True,
            )
    # Stop strategy runner before paper engine
    # Stop Serenity score cache first
    if strategy_runner_instance is not None:
        try:
            _serenity_cache = getattr(
                strategy_runner_instance, "_serenity_filter", None
            )
            if _serenity_cache is not None:
                await _serenity_cache.stop()
        except Exception:
            logger.debug(
                "Error stopping Serenity score cache on shutdown", exc_info=True
            )
    if strategy_runner_instance is not None:
        try:
            await strategy_runner_instance.stop()
        except Exception:
            logger.warning("Error stopping strategy runner", exc_info=True)
    set_strategy_runner(None)

    if paper_engine is not None and paper_state is not None:
        try:
            paper_state.save_state(
                paper_engine._account_mgr,
                paper_engine._order_mgr,
                paper_engine._position_mgr,
                fills=paper_engine._fills,
            )
            logger.info("Paper state saved on shutdown")
        except Exception:
            logger.warning("Failed to save paper state on shutdown", exc_info=True)

    if paper_engine is not None:
        await paper_engine.stop()
    set_execution_engine(None)
    set_paper_managers(None, None, None)

    # Stop ContinuousRiskMonitor
    from app.core.dependencies import get_risk_monitor, set_risk_monitor

    _risk_monitor_instance = get_risk_monitor()
    if _risk_monitor_instance is not None:
        try:
            await _risk_monitor_instance.stop()
            logger.info("ContinuousRiskMonitor stopped")
        except Exception:
            logger.debug("Error stopping risk monitor on shutdown", exc_info=True)
    set_risk_monitor(None)

    # Stop FundingReversalMonitor
    from app.core.dependencies import (
        get_funding_reversal_monitor,
        set_funding_reversal_monitor,
    )

    _fr_monitor_instance = get_funding_reversal_monitor()
    if _fr_monitor_instance is not None:
        try:
            await _fr_monitor_instance.stop()
            logger.info("FundingReversalMonitor stopped")
        except Exception:
            logger.debug(
                "Error stopping funding reversal monitor on shutdown", exc_info=True
            )
    set_funding_reversal_monitor(None)

    # Stop FeeRateMonitor
    _fee_monitor_instance = getattr(app.state, "fee_monitor", None)
    if _fee_monitor_instance is not None:
        try:
            await _fee_monitor_instance.stop()
            logger.info("FeeRateMonitor stopped")
        except Exception:
            logger.debug("Error stopping fee monitor on shutdown", exc_info=True)

    # Stop LiveEngine
    from app.core.dependencies import get_live_engine, set_live_engine

    _live_engine_inst = get_live_engine()
    if _live_engine_inst is not None:
        try:
            await _live_engine_inst.stop()
            logger.info("Live execution engine stopped")
        except Exception:
            logger.debug("Error stopping live engine on shutdown", exc_info=True)
    set_live_engine(None)

    # Stop BinanceUserDataClient instances (private WS)
    for _udc in _binance_user_data_clients:
        try:
            await _udc.close()
            logger.info("BinanceUserDataClient closed")
        except Exception:
            logger.debug(
                "Error closing BinanceUserDataClient on shutdown", exc_info=True
            )
    _binance_user_data_clients.clear()

    # Stop ShadowEngine
    from app.core.dependencies import get_shadow_engine, set_shadow_engine

    _shadow_engine_inst = get_shadow_engine()
    if _shadow_engine_inst is not None:
        try:
            await _shadow_engine_inst.stop()
            logger.info("Shadow execution engine stopped")
        except Exception:
            logger.debug("Error stopping shadow engine on shutdown", exc_info=True)
    set_shadow_engine(None)
    from app.core.dependencies import set_order_journal, set_live_state_repo

    set_order_journal(None)
    set_live_state_repo(None)

    set_risk_engine(None)
    set_settings_service(None)
    set_key_pair_manager(None)
    if market_svc is not None:
        await market_svc.stop()
    set_market_service(None)

    if funding_fetcher is not None:
        try:
            await funding_fetcher.close()
        except Exception:
            logger.debug(
                "Error closing funding rate fetcher on shutdown",
                exc_info=True,
            )
    set_funding_rate_fetcher(None)

    if liquidation_fetcher is not None:
        try:
            await liquidation_fetcher.close()
        except Exception:
            logger.debug(
                "Error closing liquidation fetcher on shutdown",
                exc_info=True,
            )
    set_liquidation_fetcher(None)

    # Close options fetcher
    if options_fetcher is not None:
        try:
            await options_fetcher.close()
        except Exception:
            logger.debug("Error closing options fetcher on shutdown", exc_info=True)

    if dex_fetcher is not None:
        try:
            await dex_fetcher.close()
        except Exception:
            logger.debug(
                "Error closing DEX fetcher on shutdown",
                exc_info=True,
            )
    set_dex_fetcher(None)

    if onchain_fetcher is not None:
        try:
            await onchain_fetcher.close()
        except Exception:
            logger.debug(
                "Error closing onchain fetcher on shutdown",
                exc_info=True,
            )
    set_onchain_fetcher(None)

    # Close Serenity fetcher
    if serenity_fetcher is not None:
        try:
            await serenity_fetcher.close()
        except Exception:
            logger.debug("Error closing Serenity fetcher on shutdown", exc_info=True)
    from app.api.v1.serenity import (
        set_serenity_fetcher,
        set_serenity_pool_scanner,
        set_serenity_workflow,
    )

    set_serenity_fetcher(None)
    set_serenity_workflow(None)
    set_serenity_pool_scanner(None)

    # Close Serenity score DB
    from app.core.dependencies import get_serenity_score_db, set_serenity_score_db

    score_db = get_serenity_score_db()
    if score_db is not None:
        try:
            score_db.close()
        except Exception:
            logger.debug("Error closing Serenity score DB", exc_info=True)
    set_serenity_score_db(None)

    # Close Serenity pool DB
    from app.core.dependencies import get_serenity_pool_db, set_serenity_pool_db

    pool_db = get_serenity_pool_db()
    if pool_db is not None:
        try:
            pool_db.close()
        except Exception:
            logger.debug("Error closing Serenity pool DB", exc_info=True)
    set_serenity_pool_db(None)

    # Close derivatives DB
    from app.core.dependencies import get_derivatives_db, set_derivatives_db

    deriv_db = get_derivatives_db()
    if deriv_db is not None:
        try:
            deriv_db.close()
        except Exception:
            logger.debug("Error closing derivatives DB", exc_info=True)
    set_derivatives_db(None)

    # Close storage
    from app.core.dependencies import set_chat_session_repo

    set_chat_session_repo(None)
    set_strategy_repo(None)
    # Flush + stop the system_logs drain before the DB goes away.
    _db_log_manager = state.get("_db_log_manager")
    if _db_log_manager is not None:
        try:
            await _db_log_manager.stop()
        except Exception:
            logger.debug("Error stopping db log handler", exc_info=True)
        logging.getLogger().removeHandler(_db_log_manager.handler)
    if db_manager is not None:
        try:
            await db_manager.close()
        except Exception:
            logger.warning("Error closing database", exc_info=True)
    set_db_manager(None)
    # Drop the P5 recorder too — it closes over db_manager, so leaving it set
    # would let a late post_market call touch a closed db.
    from app.core.dependencies import set_outcome_recorder

    set_outcome_recorder(None)

    logger.info("SeleneQuant Local API shutdown complete")


def _init_sys_path() -> None:
    """Ensure current project's alpha-core takes priority over any stale pip install.

    Inserts packages/alpha-core at sys.path[0] and purges any cached
    selene_space_alpha modules so the next import re-resolves to ours.
    """
    import sys as _sys

    _alpha_core_path = str(
        Path(__file__).resolve().parents[3] / "packages" / "alpha-core"
    )
    if _alpha_core_path not in _sys.path:
        _sys.path.insert(0, _alpha_core_path)
    for _mod_name in list(_sys.modules.keys()):
        if _mod_name.startswith("selene_space_alpha"):
            del _sys.modules[_mod_name]


def _init_crypto_and_settings(state: dict) -> None:
    """Initialize KeyPairManager, SettingsService, and HealthRegistry.

    Wires the three singletons into their dependency holders and seeds
    a 'local_api' health check. Stashes settings_service into state for
    later use (proxy detection + favorite symbols).
    """
    from app.core.crypto import KeyPairManager
    from app.core.settings_service import SettingsService
    from selene_space_security.secrets import SecretManager

    async def _local_api_health() -> HealthCheckResult:
        return HealthCheckResult(name="local_api", status="healthy", latency_ms=0.0)

    key_pair_manager = KeyPairManager()
    set_key_pair_manager(key_pair_manager)

    settings_service = SettingsService(
        secret_manager=SecretManager(),
        key_pair_manager=key_pair_manager,
    )
    set_settings_service(settings_service)

    registry = HealthRegistry()
    registry.register_check("local_api", _local_api_health)
    set_health_registry(registry)

    state["settings_service"] = settings_service


async def _init_storage(state: dict) -> None:
    """Initialize SQLite storage, migrations, and strategy repo.

    Runs ALL_MIGRATIONS, connects AsyncSQLiteManager, and seeds the
    in-memory _strategies store from persisted rows. On ImportError
    (selene_space_storage not installed) logs and falls back to in-memory
    store. Stashes db_manager into state for shutdown.
    """
    db_manager = None
    strategy_repo = None
    try:
        from selene_space_storage import (
            ALL_MIGRATIONS,
            AsyncSQLiteManager,
            MigrationRunner,
            StrategyRepository,
        )

        migration_runner = MigrationRunner(ALL_MIGRATIONS)
        db_manager = AsyncSQLiteManager(migration_runner=migration_runner)
        await db_manager.connect()
        set_db_manager(db_manager)

        strategy_repo = StrategyRepository(db_manager)
        set_strategy_repo(strategy_repo)

        from app.core.dependencies import set_chat_session_repo
        from selene_space_storage.repositories.chat_sessions import (
            ChatSessionRepository,
        )

        chat_session_repo = ChatSessionRepository(db_manager)
        set_chat_session_repo(chat_session_repo)

        from app.core.dependencies import set_experiment_repo
        from selene_space_storage.repositories.experiments import ExperimentRepository

        experiment_repo = ExperimentRepository(db_manager)
        set_experiment_repo(experiment_repo)

        from app.core.dependencies import set_paper_plan_repo
        from selene_space_storage.repositories.paper_plans import PaperPlanRepository

        paper_plan_repo = PaperPlanRepository(db_manager)
        set_paper_plan_repo(paper_plan_repo)

        from app.core.billing_auth import set_billing_repo
        from selene_space_storage.repositories.billing import BillingRepository

        billing_repo = BillingRepository(db_manager)
        set_billing_repo(billing_repo)

        from app.core.dependencies import set_episode_repo
        from selene_space_storage.repositories.episodes import EpisodeRepository

        episode_repo = EpisodeRepository(db_manager)
        set_episode_repo(episode_repo)

        from app.core.audit import set_audit_repo
        from selene_space_storage.repositories.audit_logs import AuditLogRepository

        audit_repo = AuditLogRepository(db_manager)
        set_audit_repo(audit_repo)

        # P5 forward-validation recorder (阶段 C): a zero-arg async callable
        # the daily-cycle PostMarketPhase invokes. Built here (the only layer
        # that sees both storage and backtest-engine); A-share daily bars come
        # from FetcherManager (Phase B). Wired best-effort — a failure to
        # assemble leaves the stage dormant rather than blocking startup.
        try:
            from app.core.dependencies import set_outcome_recorder
            from app.core.forward_validation_recorder import ForwardValidationRecorder
            from selene_space_market.fetcher_manager import (
                FetcherManager,
                SinaDailyFetcher,
                TencentDailyFetcher,
            )

            _fm = FetcherManager()
            _fm.register(SinaDailyFetcher())
            _fm.register(TencentDailyFetcher())
            _recorder = ForwardValidationRecorder(
                db_manager, fetch_daily=_fm.fetch_daily
            )
            set_outcome_recorder(_recorder.run)
        except Exception:
            logger.warning("forward-validation recorder not wired", exc_info=True)
            set_outcome_recorder(None)

        # Worker heartbeats: register liveness probe against the same db.
        from app.core.dependencies import get_health_registry, worker_heartbeats_health
        from selene_space_storage.repositories.worker_heartbeats import (
            WorkerHeartbeatRepository,
        )

        heartbeat_repo = WorkerHeartbeatRepository(db_manager)
        get_health_registry().register_check(
            "worker_heartbeats", worker_heartbeats_health(heartbeat_repo)
        )

        # Pre-load persisted strategies into in-memory store used by routes
        from app.api.v1.strategies import _strategies

        saved = await strategy_repo.list(limit=1000)
        for s in saved:
            _strategies[s.id] = s
        logger.info("Storage initialized, loaded %d persisted strategies", len(saved))

        # FX02: Seed template strategies from YAML files if not already present
        await _seed_template_strategies(_strategies, strategy_repo)

        # Recover orphaned backtest tasks from a previous process: pending/running
        # rows are marked failed("server restart") so in-flight work is surfaced.
        from app.api.v1.backtests import _recover_orphaned_tasks

        await _recover_orphaned_tasks()
    except ImportError:
        logger.info(
            "selene_space_storage not installed, using in-memory strategy store"
        )
    except Exception:
        logger.warning("Failed to initialize storage", exc_info=True)

    state["db_manager"] = db_manager

    # Feed the system_logs panel (v008 table): INFO+ records drain to DB.
    # Attached after storage init so init-time logs still reach the panel.
    if db_manager is not None:
        from app.core.db_log_handler import attach_db_log_handler
        from app.core.dependencies import get_db_manager

        state["_db_log_manager"] = attach_db_log_handler(get_db_manager)


def _init_market_data(state: dict) -> None:
    """Initialize MarketDataService with Binance/OKX sources and all fetchers.

    Resolves kline intervals from env (SELENE_KLINE_INTERVALS >
    SELENE_DEFAULT_INTERVAL > "30m,1h"), resolves proxy URL (env > persisted
    setting > Windows registry auto-detect), registers 4 sources (binance
    spot/futures + okx spot/futures), and constructs the 5 fetchers
    (funding / liquidation / options / dex / onchain).

    Stashes market_svc + all fetchers + kline_intervals + proxy_url into
    state for the engines init step and shutdown.
    """
    from selene_space_market import BinanceSource, MarketDataService, OKXSource

    settings_service = state["settings_service"]

    # Multi-interval: SELENE_KLINE_INTERVALS takes priority over SELENE_DEFAULT_INTERVAL
    _intervals_raw = (
        os.environ.get("SELENE_KLINE_INTERVALS")
        or os.environ.get("SELENE_DEFAULT_INTERVAL")
        or "30m,1h"
    )
    kline_intervals = [i.strip() for i in _intervals_raw.split(",") if i.strip()]
    if not kline_intervals:
        kline_intervals = ["30m", "1h"]
    default_interval = kline_intervals[0]
    logger.info("Kline intervals: %s (primary: %s)", kline_intervals, default_interval)

    # Proxy priority: env var > persisted setting > Windows registry auto-detect
    saved_settings = settings_service._load_non_sensitive()
    saved_proxy = saved_settings.get("network", {}).get("proxy_url", "")
    proxy_url = (
        os.environ.get("SELENE_PROXY_URL")
        or os.environ.get("https_proxy")
        or (saved_proxy if saved_proxy else None)
    )
    if not proxy_url:
        from selene_space_exchange.exchanges.polymarket.client import detect_local_proxy

        proxy_url = detect_local_proxy()
    if proxy_url:
        logger.info("Using WebSocket proxy: %s", proxy_url)

    market_svc = MarketDataService(large_trade_threshold_usd=10_000.0)

    # Binance Spot
    market_svc.register_source(
        BinanceSource(
            market_type="spot",
            ws_url=os.environ.get("SELENE_BINANCE_WS_URL", None),
            rest_url=os.environ.get("SELENE_BINANCE_REST_URL", None),
            proxy_url=proxy_url,
            kline_intervals=kline_intervals,
        )
    )

    # Binance USDT-M Futures
    market_svc.register_source(
        BinanceSource(
            market_type="futures",
            ws_url=os.environ.get("SELENE_BINANCE_FUTURES_WS_URL", None),
            rest_url=os.environ.get("SELENE_BINANCE_FUTURES_REST_URL", None),
            proxy_url=proxy_url,
            kline_intervals=kline_intervals,
        )
    )

    # OKX Spot
    market_svc.register_source(
        OKXSource(
            market_type="spot",
            proxy_url=proxy_url,
            kline_intervals=kline_intervals,
        )
    )

    # OKX Futures (Perpetual Swap)
    market_svc.register_source(
        OKXSource(
            market_type="futures",
            proxy_url=proxy_url,
            kline_intervals=kline_intervals,
        )
    )

    # A-share daily bars (poll-based, no WS). Opt-out via env so existing
    # crypto-only deployments are unaffected.
    from selene_space_market import AstockSource

    if os.environ.get("SELENE_ASTOCK_SOURCE", "1") != "0":
        # AstockSource duck-types ExchangeSource's kline-facing surface only.
        # (a-side kept `# type: ignore[arg-type]` because astock_source.py is a
        # red-line file; main-side cast achieves the same without touching it.)
        from selene_space_market import ExchangeSource

        market_svc.register_source(cast("ExchangeSource", AstockSource()))

    # A-share intraday L1 snapshots (Sina poll, ~3s in-session). Registers as
    # ("astock", "intraday") so it coexists with the daily ("astock", "spot")
    # source above. Opt-out via env for crypto-only deployments.
    from selene_space_market import AstockIntradaySource

    if os.environ.get("SELENE_ASTOCK_INTRADAY_SOURCE", "1") != "0":
        market_svc.register_source(AstockIntradaySource())

    # A-share whole-market snapshot (Sina poll, one cross-section per cycle).
    # Registers as ("astock","market_snapshot") so it coexists with the daily
    # ("astock","spot") and subscription ("astock","intraday") keys above.
    # PULL-ONLY BY CONTRACT: register_source wires on_ticker -> event_bus.publish
    # unconditionally, but this source's on_ticker is a no-op sink — publishing
    # a ~5500-row cross-section every few seconds would flood the shared bus the
    # S3a anomaly detector reads. Its results are read via get_market_rows().
    # Opt-out via env for crypto-only deployments.
    from selene_space_market import AstockMarketSnapshotSource

    state["astock_snapshot_source"] = None
    if os.environ.get("SELENE_ASTOCK_MARKET_SNAPSHOT", "1") != "0":
        _snapshot_src = AstockMarketSnapshotSource()
        market_svc.register_source(_snapshot_src)
        # Stashed for _init_engines_and_runtime, which seeds its poll universe
        # (that needs the ashare package, which market-data must not import).
        state["astock_snapshot_source"] = _snapshot_src

    # US stock bars via Yahoo v8 chart (poll-based). Opt-out via env so
    # crypto-only deployments are unaffected.
    from selene_space_market import USStockSource

    if os.environ.get("SELENE_US_STOCK_SOURCE", "1") != "0":
        market_svc.register_source(USStockSource(proxy_url=proxy_url))

    # --- Funding Rate Fetcher (bulk REST, all exchanges) ---
    from selene_space_market.funding_rate_fetcher import FundingRateFetcher

    funding_fetcher = FundingRateFetcher(proxy_url=proxy_url)
    set_funding_rate_fetcher(funding_fetcher)

    # --- Liquidation Fetcher (CoinGlass API) ---
    from selene_space_market.liquidation_fetcher import LiquidationFetcher

    liquidation_fetcher = LiquidationFetcher(proxy_url=proxy_url)
    set_liquidation_fetcher(liquidation_fetcher)

    # --- Options Fetcher (Deribit API) ---
    from selene_space_market.options_fetcher import DeribitOptionsFetcher

    options_fetcher = DeribitOptionsFetcher(proxy_url=proxy_url)

    # --- DEX Fetcher (DexScreener API) ---
    from selene_space_market.dex_fetcher import DexFetcher

    dex_fetcher = DexFetcher(proxy_url=proxy_url)
    set_dex_fetcher(dex_fetcher)

    # --- Onchain Fetcher (Glassnode API) ---
    from selene_space_market.onchain_fetcher import OnchainFetcher

    onchain_fetcher = OnchainFetcher(proxy_url=proxy_url)
    set_onchain_fetcher(onchain_fetcher)

    # --- Serenity Fetcher (DeFi Llama + GitHub) ---
    # NOTE: Initialized later, after DerivativesDB and KlineManager are available.

    state["market_svc"] = market_svc
    state["funding_fetcher"] = funding_fetcher
    state["liquidation_fetcher"] = liquidation_fetcher
    state["options_fetcher"] = options_fetcher
    state["dex_fetcher"] = dex_fetcher
    state["onchain_fetcher"] = onchain_fetcher
    state["kline_intervals"] = kline_intervals
    state["proxy_url"] = proxy_url
    state["saved_settings"] = saved_settings


async def _init_engines_and_runtime(state: dict) -> None:
    """Run the full engines + runtime startup (formerly the lifespan try-body).

    Mirrors the original try block L721-1944 verbatim. On success, syncs all
    objects that shutdown needs into state. On exception, the surrounding
    lifespan finally still runs _shutdown_lifespan(state) and state was
    pre-seeded with None sentinels for every key, so partial init is safe.
    """
    market_svc = state["market_svc"]
    options_fetcher = state["options_fetcher"]
    proxy_url = state["proxy_url"]
    kline_intervals = state["kline_intervals"]
    saved_settings = state["saved_settings"]
    settings_service = state["settings_service"]
    _binance_user_data_clients: list[Any] = []
    risk_engine_instance: Any = None
    await market_svc.start()
    set_market_service(market_svc)

    # Wire options fetcher into engines
    market_svc._options_fetcher = options_fetcher
    market_svc.signal_engine.set_options_fetcher(options_fetcher)
    market_svc.kf_index_engine.set_options_fetcher(options_fetcher)

    # Options chain poller: periodic Deribit refresh (ADR-1 single writer via
    # fill_options_data). Interval matches the fetcher TTL (_CACHE_TTL = 300s);
    # cancelled in _shutdown_lifespan.
    state["_options_poll_task"] = asyncio.create_task(
        _options_poll_loop(state), name="options-poller"
    )

    # Seed default KF indices
    from selene_space_types.derivatives import KFIndexConfig

    market_svc.kf_index_engine.create_index(
        KFIndexConfig(
            id="kf-momentum",
            name="KF Momentum Index",
            description="CVD + TOF + LiqMap weighted momentum signal",
            factors={"cvd": 0.40, "tof": 0.30, "liqmap": 0.20, "funding": 0.10},
        )
    )
    market_svc.kf_index_engine.create_index(
        KFIndexConfig(
            id="kf-risk",
            name="KF Risk Index",
            description="Risk-focused: TOF + GEX + Book imbalance",
            factors={"tof": 0.40, "gex": 0.30, "book_imbalance": 0.20, "funding": 0.10},
        )
    )

    # Bridge EventBus → WS broadcast so that incoming exchange events
    # are pushed to connected WebSocket clients in real time.
    _bridge_market_events(market_svc)

    # Subscribe default symbols on ALL sources so large-trade and
    # liquidation monitors see data from every exchange.
    #
    # 2026-10-07 启动性能修复：这段原先**内联 await** 在 lifespan 里（yield 之前），
    # 且每源睡 2s、每 symbol 睡 5s —— 3 个默认 symbol 就是 3×(4×2+5)=39s 纯等待，
    # 收藏越多越慢。实测离线环境整个 lifespan 要 100s，而前端健康门 60s 就判失败，
    # 用户看到的就是"起不来"。
    # 改为后台 task：lifespan 立即就绪，订阅在后台按同样的节流节奏慢慢做。
    # 节流本身保留（Binance testnet WS 1008 限流是真实约束，见下）。
    default_symbols = (
        os.environ.get("SELENE_DEFAULT_SYMBOLS") or "BTC/USDT,ETH/USDT,SOL/USDT"
    )
    # Merge favorites with default symbols
    fav_symbols = settings_service.get_favorite_symbols()
    default_sym_list = [s.strip() for s in default_symbols.split(",") if s.strip()]
    all_sub_symbols = list(dict.fromkeys(default_sym_list + fav_symbols))

    async def _subscribe_default_symbols() -> None:
        """后台订阅默认/收藏 symbol（不在启动关键路径上）。

        **按市场域路由**：每个 symbol 只订阅与其市场匹配的源。原先这里写死
        `[("binance","spot"),("binance","futures"),("okx","spot"),("okx","futures")]`
        并对每个 symbol 全部订阅 —— 收藏里一个美股代码（AAPL）或 A 股代码
        （600519.SH）就会被塞给币安，换来一串 400 Bad Request（实测日志）。
        `detect_market_domain` 早已写好这个判断（crypto / us_stock / ashare），
        只是订阅路径从没调用它，`USStockSource`/`AstockSource` 虽然注册了却
        永远收不到订阅。现在按 domain 过滤：符号属于哪个市场，就只发往该市场的源。

        Rate-limit guard: Binance testnet WS subscribes triggered 1008 policy
        violations when 12 subscribes fired within 3.6s (3 sym x 4 sources x
        0.3s). Spread subscribes out: 2s between sources, 5s between symbols.
        On failure, back off an extra 3s so the next attempt lands outside the
        rate-limit window rather than inside it.
        """
        from selene_space_types.routing import detect_market_domain

        # Route each symbol to the sources that serve its market (see
        # _sources_for_symbol). Built from what is actually registered, so a
        # source added at registration time is picked up automatically.
        available = list(market_svc.sources)

        for sym in all_sub_symbols:
            if not sym:
                continue
            targets = _sources_for_symbol(sym, available)
            if not targets:
                # No source for this market in this deployment — say so once,
                # in plain language, instead of firing doomed requests at every
                # crypto venue and logging a 400 per venue.
                logger.warning(
                    "No %s data source registered; skipping subscription for %r "
                    "(add the source or remove the symbol from favorites)",
                    detect_market_domain(sym).value,
                    sym,
                )
                continue
            for ex, mt in targets:
                try:
                    await market_svc.add_symbol(sym, exchange=ex, market_type=mt)
                    logger.info(
                        "Subscribed %s on %s/%s (%s)",
                        sym,
                        ex,
                        mt,
                        _source_domain(ex, mt),
                    )
                except Exception:
                    logger.warning(
                        "Failed to subscribe %s on %s/%s (may be unreachable)",
                        sym,
                        ex,
                        mt,
                        exc_info=True,
                    )
                    await asyncio.sleep(3.0)
                    continue
                await asyncio.sleep(2.0)
            await asyncio.sleep(5.0)
        logger.info("Default symbol subscription pass complete")

    if all_sub_symbols:
        state["_subscribe_task"] = asyncio.create_task(
            _subscribe_default_symbols(), name="subscribe-default-symbols"
        )

    # Register market health check
    async def _market_health() -> HealthCheckResult:
        running = market_svc.is_running
        source_status = {}
        for (ex, mt), src in market_svc.sources.items():
            source_status[f"{ex}/{mt}"] = {
                "running": src.is_running,
                "symbols": len(src.get_symbols()),
            }
        return HealthCheckResult(
            name="market_data",
            status="healthy" if running else "degraded",
            latency_ms=0.0,
            detail={"running": running, "sources": source_status},
        )

    from app.core.dependencies import get_health_registry

    get_health_registry().register_check("market_data", _market_health)

    # --- K-line Incremental Sync (persist to SQLite) ---
    from selene_space_market.kline_manager import KlineDataManager
    from selene_space_market.quality import KlineQualityChecker
    from app.core.dependencies import set_kline_manager

    # Canonical repo-root DB. A bare relative "data/klines.db" would land under
    # services/local-api/ (the process CWD) and fork from the readers, which
    # honour SELENE_KLINES_DB — the split this helper removes (#kline-paths).
    _klines_db = default_klines_db_path()
    if _klines_db != repo_root() / "data" / "klines.db":
        logger.warning(
            "K-lines DB overridden by SELENE_KLINES_DB -> %s (repo-root default bypassed)",
            _klines_db,
        )
    logger.info("K-line store: %s", _klines_db)
    _kline_mgr = KlineDataManager(db_path=_klines_db)
    _kline_mgr.set_service(market_svc)  # inject service with sources + proxy
    set_kline_manager(_kline_mgr)
    state["kline_manager"] = _kline_mgr
    # One checker instance, reset per partition by the sweeper (its state is
    # keyed by symbol only, so reuse without reset would fabricate gaps).
    state["kline_quality_checker"] = KlineQualityChecker()
    state["kline_quality_snapshot"] = {}

    # --- A-share intraday anomaly detector (S3a) — 末尾追加 ---
    # Consumes ("astock","intraday") L1 snapshots off the shared TickerEvent
    # bus and republishes AnomalyEvent, deriving prev_close / N-day baseline
    # from the kline store created just above. Same opt-out env as the source:
    # no source registered, no detector. The price-limit core and the session
    # calendar are imported here (composition layer) on purpose — market-data
    # must not depend on either package.
    #
    # Only A-share symbols this deployment can actually reach: add_symbol is
    # never called for a code absent from SELENE_DEFAULT_SYMBOLS ∪ favorites
    # (the default set is pure crypto), so an empty list is the visible
    # starvation signal the wiring logs at startup. Computed once here so both
    # the detector and the periodic daily sync loop below share the same set.
    from selene_space_types.routing import detect_market_domain

    _astock_reachable = [
        s for s in all_sub_symbols if detect_market_domain(s).value == "ashare"
    ]
    state["astock_anomaly_detector"] = None
    if (
        os.environ.get("SELENE_ASTOCK_INTRADAY_SOURCE", "1") != "0"
        and market_svc.get_source("astock", "intraday") is not None
    ):
        try:
            from app.astock_anomaly_wiring import (
                astock_session_open,
                wire_astock_anomaly_detector,
            )

            state["astock_anomaly_detector"] = wire_astock_anomaly_detector(
                market_svc,
                _kline_mgr._db,
                is_session_open=astock_session_open,
                astock_symbols=_astock_reachable,
            )
        except Exception:
            logger.warning(
                "A-share anomaly detector wiring failed; continuing without it",
                exc_info=True,
            )

    # --- A-share intraday outage ledger (S2) — 末尾追加 ---
    # Attach the gap sink now that the durable store exists: the source is
    # registered earlier (before _kline_mgr), so this wires up after the fact.
    # Episodes open/close in klines.db's own l1_snapshot_gaps table — the same
    # KlineDB instance the anomaly detector reads.
    from app.astock_anomaly_wiring import wire_astock_intraday_gap_sink

    wire_astock_intraday_gap_sink(market_svc, _kline_mgr._db)

    # --- A-share anomaly decision consumer (S4) — 末尾追加 ---
    # Records each AnomalyEvent into the production decision ledger. The
    # HashChainLedger instance is constructed HERE (not inside the live-engine
    # branch below) so it exists in every deployment mode — paper included —
    # and the LiveDecisionPipeline block further down reuses this same
    # instance: ONE ledger, ONE file (~/.selenespace/audit/live_decisions.jsonl),
    # ONE hash chain (plan s4-plan-v2.md §3.4). SELENE_ANOMALY_MODE accepts
    # "observe" (default) and "suggest"; in suggest a fresh anomaly is parked
    # as a pending proposal and a human verdict records an ``anomaly_approval``
    # entry with executes_order=False — a confirmation, never an order. No
    # execution path is touched either way; "auto" is still a startup
    # ValueError by design (an ungated A-share order path).
    from selene_space_paper.audit_ledger import HashChainLedger

    _shared_decision_ledger = HashChainLedger(
        Path.home() / ".selenespace" / "audit" / "live_decisions.jsonl"
    )
    # Resolve SELENE_ANOMALY_MODE unconditionally and *outside* any try/except:
    # an illegal value must abort startup (ValueError) on every deployment, not
    # only where an A-share source happens to be attached — otherwise
    # SELENE_ANOMALY_MODE=auto on a crypto-only box would start silently and
    # read as "armed", the legal-but-inert-enum trap this v1 exists to close.
    from app.astock_anomaly_decision import (
        ANOMALY_MODE_ENV,
        resolve_anomaly_mode,
    )

    _anomaly_mode = resolve_anomaly_mode(os.environ.get(ANOMALY_MODE_ENV))
    state["astock_anomaly_decision_consumer"] = None
    if (
        os.environ.get("SELENE_ASTOCK_INTRADAY_SOURCE", "1") != "0"
        and market_svc.get_source("astock", "intraday") is not None
    ):
        try:
            from app.astock_anomaly_wiring import wire_astock_anomaly_consumer

            from app.core.dependencies import (
                set_anomaly_decision_consumer as _set_anomaly_consumer,
            )

            _anomaly_consumer = wire_astock_anomaly_consumer(
                market_svc,
                _shared_decision_ledger,
                capital_base=100_000.0,
                quantity_pct=0.1,
                mode=_anomaly_mode,
            )
            state["astock_anomaly_decision_consumer"] = _anomaly_consumer
            _set_anomaly_consumer(_anomaly_consumer)
            logger.info(
                "A-share anomaly decision consumer wired (mode=%s)", _anomaly_mode
            )
        except Exception:
            logger.warning(
                "A-share anomaly decision consumer wiring failed; "
                "continuing without it",
                exc_info=True,
            )

    _kline_sync_task = asyncio.create_task(
        _kline_startup_sync(
            _kline_mgr,
            ",".join(all_sub_symbols),
            kline_intervals,
            sources=list(market_svc.sources),
        )
    )

    # --- A-share daily partition refresh — 末尾追加 ---
    # Keeps ("astock","spot","1d") current so the detector's N-day/volume
    # baseline is not read from the frozen qfq import. Opt-out matches the
    # daily source it feeds: no AstockSource, nothing to keep fresh.
    if os.environ.get("SELENE_ASTOCK_SOURCE", "1") != "0":
        state["_astock_daily_sync_task"] = asyncio.create_task(
            _astock_daily_sync_loop(state, _astock_reachable),
            name="astock-daily-sync",
        )
    else:
        state["_astock_daily_sync_task"] = None

    # --- A-share snapshot universe seed — 末尾追加 ---
    # The snapshot source registered with an empty universe; seed it in the
    # background so a cold akshare fetch cannot blow the 60s health gate.
    if state.get("astock_snapshot_source") is not None:
        state["_astock_snapshot_seed_task"] = asyncio.create_task(
            _astock_snapshot_seed_loop(state), name="astock-snapshot-seed"
        )
    else:
        state["_astock_snapshot_seed_task"] = None

    # --- A-share live ladder warm-up — 末尾追加 ---
    # The first /ashare/phase request pays an 18.5s endpoint walk (cold process,
    # warm disk), past the frontend's 12s abort. Warm in the background so the
    # board is populated on first load. No source, no live panel to warm.
    if state.get("astock_snapshot_source") is not None:
        state["_astock_ladder_warmup_task"] = asyncio.create_task(
            _astock_ladder_warmup_loop(state), name="astock-ladder-warmup"
        )
    else:
        state["_astock_ladder_warmup_task"] = None

    # --- K-line gap sweep (L2): periodic detect+repair over sourced partitions ---
    state["_kline_sweep_task"] = asyncio.create_task(
        _kline_gap_sweep_loop(state), name="kline-gap-sweeper"
    )

    async def _kline_health() -> HealthCheckResult:
        snap = state.get("kline_quality_snapshot") or {}
        quality = snap.get("quality") or {}
        worst = min(quality.values()) if quality else 1.0
        open_holes = int(snap.get("holes_found", 0)) - int(snap.get("holes_filled", 0))
        status = "healthy"
        if snap.get("holes_permanent"):
            status = "degraded"
        return HealthCheckResult(
            name="kline_quality",
            status=status,
            latency_ms=0.0,
            detail={
                "scanned": snap.get("scanned", 0),
                "open_holes": max(open_holes, 0),
                "permanent_holes": snap.get("holes_permanent", 0),
                "worst_quality_score": worst,
                "warnings": snap.get("warnings", []),
            },
        )

    get_health_registry().register_check("kline_quality", _kline_health)

    # --- Derivatives DB (persist funding rate, OI, taker ratio) ---
    from selene_space_market.derivatives_db import DerivativesDB
    from app.core.dependencies import set_derivatives_db

    _deriv_db = DerivativesDB(db_path="data/derivatives.db")
    _deriv_db.open()
    set_derivatives_db(_deriv_db)

    # --- Serenity Fetcher (DeFi Llama + GitHub + local DB enrichment) ---
    from app.api.v1.serenity import set_serenity_fetcher, set_serenity_workflow

    try:
        from selene_space_alpha.serenity import (
            SerenityFetcher,
            SerenityScorer,
            SerenityWorkflow,
        )

        _kline_db = _kline_mgr._db if _kline_mgr else None
        serenity_fetcher = SerenityFetcher(
            proxy_url=proxy_url,
            derivatives_db=_deriv_db,
            kline_db=_kline_db,
        )
        serenity_scorer = SerenityScorer()
        serenity_workflow = SerenityWorkflow(serenity_fetcher, serenity_scorer)
        set_serenity_fetcher(serenity_fetcher)
        set_serenity_workflow(serenity_workflow)
        logger.info(
            "Serenity fundamental analysis initialized (with local DB enrichment)"
        )
    except Exception:
        logger.warning("Failed to initialize Serenity service", exc_info=True)
        serenity_fetcher = None

    # --- Serenity Score DB (persist scores) ---
    try:
        from selene_space_alpha.serenity.score_db import SerenityScoreDB
        from app.core.dependencies import set_serenity_score_db

        _serenity_score_db = SerenityScoreDB()
        _serenity_score_db.open()
        set_serenity_score_db(_serenity_score_db)
        logger.info("Serenity score persistence initialized")
    except Exception:
        logger.warning("Failed to initialize Serenity score DB", exc_info=True)

    # Rehydrate in-memory score cache from score_db so /scores survives restart
    try:
        from app.api.v1.serenity import bootstrap_score_cache

        n_bootstrapped = bootstrap_score_cache()
        if n_bootstrapped:
            logger.info("Serenity score cache bootstrapped: %d symbols", n_bootstrapped)
    except Exception:
        logger.warning("Serenity score cache bootstrap failed", exc_info=True)

    # --- Serenity Pool DB (persist pool scans, shares serenity_scores.db) ---
    try:
        from selene_space_alpha.serenity.pool_db import SerenityPoolDB
        from app.core.dependencies import set_serenity_pool_db

        _serenity_pool_db = SerenityPoolDB()
        _serenity_pool_db.open()
        set_serenity_pool_db(_serenity_pool_db)
        logger.info("Serenity pool persistence initialized")
    except Exception:
        logger.warning("Failed to initialize Serenity pool DB", exc_info=True)

    # Rehydrate in-memory pool cache from pool_db so /pool/{theme} survives restart
    try:
        from app.api.v1.serenity import bootstrap_pool_cache

        n_pool = bootstrap_pool_cache()
        if n_pool:
            logger.info("Serenity pool cache bootstrapped: %d themes", n_pool)
    except Exception:
        logger.warning("Serenity pool cache bootstrap failed", exc_info=True)

    # --- Serenity Theme Pool Scanner (reuses the fetcher + scorer above) ---
    if serenity_fetcher is not None:
        try:
            from app.api.v1.serenity import set_serenity_pool_scanner
            from selene_space_alpha.serenity.pool import SerenityPoolScanner
            from app.core.dependencies import (
                get_serenity_pool_db,
                get_serenity_score_db,
            )

            set_serenity_pool_scanner(
                SerenityPoolScanner(
                    fetcher=serenity_fetcher,
                    scorer=serenity_scorer,
                    score_db=get_serenity_score_db(),
                    pool_db=get_serenity_pool_db(),
                )
            )
            logger.info("Serenity theme pool scanner initialized")
        except Exception:
            logger.warning("Failed to initialize Serenity pool scanner", exc_info=True)

    # --- Execution Engine (Paper by default) ---
    from selene_space_paper.paper_execution import PaperExecutionEngine

    paper_engine = PaperExecutionEngine(
        initial_balance=float(os.environ.get("SELENE_PAPER_STARTING_BALANCE", "10000")),
    )

    from selene_space_paper.state import PaperState

    paper_state = PaperState()
    try:
        fills, _meta = paper_state.load_state(
            paper_engine._account_mgr,
            paper_engine._order_mgr,
            paper_engine._position_mgr,
        )
        if fills:
            paper_engine._fills = fills
            logger.info("Restored %d fills from paper state", len(fills))
    except Exception:
        logger.warning("Failed to load paper state, starting fresh", exc_info=True)

    await paper_engine.start()
    set_execution_engine(paper_engine)
    set_execution_mode("paper")

    # Unify paper managers so /paper REST and /trading WS share the same state
    set_paper_managers(
        paper_engine._account_mgr,
        paper_engine._order_mgr,
        paper_engine._position_mgr,
    )

    # Auto-save paper state periodically
    async def _paper_autosave() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                paper_state.save_state(
                    paper_engine._account_mgr,
                    paper_engine._order_mgr,
                    paper_engine._position_mgr,
                    fills=paper_engine._fills,
                )
            except Exception:
                logger.debug("Paper state autosave failed", exc_info=True)

    _autosave_task = asyncio.create_task(_paper_autosave(), name="paper-autosave")

    # Bridge price ticks from MarketDataService to PaperExecutionEngine
    _bridge_price_to_paper(market_svc, paper_engine)

    _bridge_paper_engine_events(paper_engine)

    # --- Strategy Runner (continuous strategy execution) ---
    strategy_runner_instance = None
    try:
        from app.core.strategy_runner import StrategyRunner as _SR

        # --- Shadow Execution Engine (dry-run, always available) ---
        # Created BEFORE StrategyRunner setup so the L607 routing check
        # can see shadow_engine in locals(). Was previously created at
        # L688 (after runner init), causing execution_mode=shadow to
        # silently fall back to paper.
        from selene_space_exchange.execution.shadow_engine import ShadowExecutionEngine
        from selene_space_exchange.execution.order_journal import (
            OrderJournal as _OrderJournal,
        )
        from selene_space_exchange.execution.live_state import (
            LiveStateRepository as _LiveStateRepository,
        )
        from app.core.dependencies import set_shadow_engine as _set_shadow_engine

        shadow_engine = ShadowExecutionEngine(
            journal=_OrderJournal(), state_repo=_LiveStateRepository()
        )
        # Phase 5.5-P0: Wire FillsLedger + EquityTracker into shadow engine
        # (same pattern as LiveExecutionEngine — main.py assembles them after construction).
        # Shadow uses its own instances so simulated fills don't pollute real risk data.
        try:
            from selene_space_risk.fills_ledger import FillsLedger as _ShadowFillsLedger
            from selene_space_risk.equity_tracker import (
                EquityTracker as _ShadowEquityTracker,
            )

            _shadow_fills = _ShadowFillsLedger()
            _shadow_equity = _ShadowEquityTracker(account_id="shadow")
            shadow_engine.set_fills_ledger(_shadow_fills)
            shadow_engine.set_equity_tracker(_shadow_equity)
        except Exception as _shadow_obs_err:
            logger.warning(
                "Shadow observability wiring failed (non-fatal): %s", _shadow_obs_err
            )
        _set_shadow_engine(shadow_engine)
        await shadow_engine.start()
        _bridge_execution_events(shadow_engine)
        _bridge_price_to_shadow(market_svc, shadow_engine)
        # Phase 2 fix: wire KillSwitch into shadow_engine (parity with LiveEngine L1419).
        # KillSwitch() is a singleton — same instance used at L1011 + L1419 + L1446.
        try:
            from selene_space_risk.kill_switch import KillSwitch as _ShadowKillSwitch

            shadow_engine.set_kill_switch(_ShadowKillSwitch())
            # Side-effect marker file for verdict script (separate process can't see in-memory state)
            try:
                import json as _json
                from pathlib import Path as _Path

                # uvicorn cwd is services/local-api/, but verdict runs from repo root.
                # Resolve marker path relative to this file (app/main.py) → repo root.
                _repo_root = _Path(__file__).resolve().parents[3]
                _ks_marker = _repo_root / "data" / "stage_b" / "killswitch_wired.json"
                _ks_marker.parent.mkdir(parents=True, exist_ok=True)
                _ks_marker.write_text(
                    _json.dumps(
                        {
                            "wired": True,
                            "engine_type": type(shadow_engine).__name__,
                            "killswitch_type": type(_ShadowKillSwitch()).__name__,
                            "wired_at": int(time.time() * 1000),
                        }
                    )
                )
            except Exception as _ks_marker_err:
                logger.warning(
                    "KillSwitch marker file write failed: %s", _ks_marker_err
                )
        except Exception as _shadow_ks_err:
            logger.warning(
                "Shadow KillSwitch wiring failed (non-fatal): %s", _shadow_ks_err
            )
        logger.info("ShadowExecutionEngine started (dry-run mode)")

        # ExecutionMode: paper (default), shadow, live. Read from
        # settings.general.execution_mode (or SELENE_EXECUTION_MODE env).
        # - paper: strategy orders route to PaperExecutionEngine (simulated
        #   fills against real market data, no real API calls).
        # - shadow: strategy orders route to ShadowExecutionEngine
        #   (dry-run against real ticks, separate journal/state).
        # - live: strategy orders route to LiveExecutionEngine (real
        #   orders on the real exchange). Requires exchange credentials
        #   + testnet=False validated.
        execution_mode = str(
            saved_settings.get("general", {}).get("execution_mode", "paper")
        ).lower()
        env_mode = os.getenv("SELENE_EXECUTION_MODE", "").strip().lower()
        if env_mode in ("paper", "shadow", "live"):
            execution_mode = env_mode

        # Sync resolved mode to the global so StrategyRunner's
        # mode_provider (get_execution_mode) returns the correct value
        # at signal time. Without this, _execution_mode stays "paper"
        # (set earlier) and all signals route to PaperEngine even when
        # SELENE_EXECUTION_MODE=shadow.
        set_execution_mode(execution_mode)

        if execution_mode == "shadow" and locals().get("shadow_engine") is not None:
            target_engine = shadow_engine
            logger.info("StrategyRunner routing to SHADOW engine (dry-run)")
        else:
            target_engine = paper_engine
            if execution_mode not in ("paper", "live"):
                logger.warning(
                    "execution_mode=%s requested but shadow engine unavailable, "
                    "falling back to paper",
                    execution_mode,
                )
            elif execution_mode == "live":
                logger.info(
                    "execution_mode=live: StrategyRunner starts on paper, "
                    "will rebind to live engine once it initializes"
                )
            else:
                logger.info("StrategyRunner routing to PAPER engine")

        strategy_runner_instance = _SR(
            paper_engine=target_engine,
            market_service=market_svc,
            # live_engine / shadow_engine / mode_provider are wired later
            # via set_engines() once the live/shadow engines are built.
        )
        await strategy_runner_instance.start()
        set_strategy_runner(strategy_runner_instance)

        # Bridge strategy signals to WebSocket broadcast
        _bridge_strategy_runner_signals(strategy_runner_instance)

        # Wire EventGuard into StrategyRunner (before Serenity which may block)
        try:
            _macro_db_path = os.environ.get(
                "SELENE_MACRO_DB",
                str(Path(__file__).resolve().parents[1] / "data" / "macro_events.db"),
            )
            logger.info(
                "EventGuard: checking macro_events.db at %s (exists=%s)",
                _macro_db_path,
                Path(_macro_db_path).exists(),
            )
            if Path(_macro_db_path).exists():
                from selene_space_alpha.event_guard import EventGuard as _EG

                _event_guard = _EG(macro_db_path=_macro_db_path)
                strategy_runner_instance.set_event_guard(_event_guard)
                logger.info("EventGuard successfully enabled")
            else:
                logger.info("EventGuard: macro_events.db not found, not enabled")
        except Exception as e:
            logger.warning("EventGuard FAILED: %s", e, exc_info=True)

        # Wire Serenity filter into StrategyRunner
        _serenity_filter_mode = os.environ.get("SELENE_SERENITY_FILTER", "soft")
        if serenity_fetcher is not None and _serenity_filter_mode != "off":
            try:
                from selene_space_alpha.serenity.score_cache import SerenityScoreCache

                _serenity_symbols = [s.split("/")[0] for s in all_sub_symbols if s]
                _serenity_cache = SerenityScoreCache(
                    serenity_workflow, symbols=_serenity_symbols
                )
                strategy_runner_instance.set_serenity_filter(
                    _serenity_cache, mode=_serenity_filter_mode
                )
                from app.api.v1.serenity import set_serenity_cache

                set_serenity_cache(_serenity_cache)
                await _serenity_cache.start(refresh_interval=300)
                # Theme-pool rank gate (B1): default off; opt in via env.
                _pool_filter_mode = os.environ.get("SELENE_SERENITY_POOL_FILTER", "off")
                if _pool_filter_mode != "off":
                    try:
                        from app.core.dependencies import get_serenity_pool_db

                        _pool_db = get_serenity_pool_db()
                        if _pool_db is not None:
                            _serenity_cache.set_pool_filter(
                                {s.theme: s for s in _pool_db.get_all_latest()},
                                mode=_pool_filter_mode,
                                top_n=int(
                                    os.environ.get("SELENE_SERENITY_POOL_TOP_N", "7")
                                ),
                            )
                            logger.info(
                                "Serenity pool rank gate enabled: mode=%s top_n=%s",
                                _pool_filter_mode,
                                os.environ.get("SELENE_SERENITY_POOL_TOP_N", "7"),
                            )
                    except Exception:
                        logger.warning(
                            "Failed to wire Serenity pool rank gate", exc_info=True
                        )
            except Exception:
                logger.warning(
                    "Failed to initialize Serenity score cache for strategy filter",
                    exc_info=True,
                )

        logger.info(
            "Strategy runner started with %d restored deployments",
            len(strategy_runner_instance.active_deployments),
        )
    except Exception:
        logger.warning("Failed to initialize strategy runner", exc_info=True)

    # --- Live Execution Engine (Real Trading) ---
    from app.core.dependencies import (
        set_live_engine,
        set_order_journal,
        set_live_state_repo,
        set_risk_monitor,
    )

    # Shared infra: OrderJournal + LiveStateRepository for LiveEngine
    from selene_space_exchange.execution.order_journal import OrderJournal
    from selene_space_exchange.execution.live_state import LiveStateRepository

    order_journal = OrderJournal()
    live_state_repo = LiveStateRepository()
    set_order_journal(order_journal)
    set_live_state_repo(live_state_repo)
    logger.info("OrderJournal + LiveStateRepository initialized")

    # Macro data service (FRED/BIS/BLS macro indicators)
    from selene_space_macrodata.service import MacroDataService
    from app.core.dependencies import set_macrodata_service

    macrodata_service = MacroDataService()
    set_macrodata_service(macrodata_service)

    # Analytics service (quantstats/pyportfolioopt/skfolio/statsmodels)
    from selene_space_alpha.analytics.service import AnalyticsService
    from app.core.dependencies import set_analytics_service

    analytics_service = AnalyticsService()
    set_analytics_service(analytics_service)

    # Optimize service (SB3 RL + scipy portfolio)
    from selene_space_optimize.service import OptimizeService
    from app.core.dependencies import set_optimize_service

    optimize_service = OptimizeService()
    set_optimize_service(optimize_service)

    # AgentStudio service (7 R&D loops + prompt library)
    from selene_space_agentstudio.service import AgentStudioService
    from app.core.dependencies import set_agentstudio_service

    agentstudio_service = AgentStudioService()
    set_agentstudio_service(agentstudio_service)

    # RenIntel service (5 Renaissance libraries)
    from selene_space_renintel.service import RenIntelService
    from app.core.dependencies import set_renintel_service

    renintel_service = RenIntelService()
    set_renintel_service(renintel_service)

    # Decision-workflow service: canned trees + real-LLM trading pipeline
    # (settings_loader feeds analyze_trading's lazy LLM-config resolution).
    from app.core.decision_workflow_service import DecisionWorkflowService
    from app.core.dependencies import (
        get_db_manager,
        get_settings_service,
        set_decision_workflow_service,
    )

    decision_workflow_service = DecisionWorkflowService(
        settings_loader=get_settings_service,
        db_loader=get_db_manager,
    )
    set_decision_workflow_service(decision_workflow_service)

    # --- Startup Reconciliation (deferred — runs after live engine init if client available) ---

    # --- Live Engine (only if exchange credentials configured) ---
    try:
        exchange_provider = saved_settings.get("exchange", {}).get(
            "provider", "binance"
        )

        from selene_space_security.secrets import SecretManager, SecretRef, SecretSource

        # Env var fallback ONLY for non-secret provider/testnet config (headless
        # Docker, CI, locked keychain). Credentials never fall back to env.
        # Must resolve testnet_flag BEFORE building SecretRef ids so we can
        # pick mainnet_api_key vs testnet_api_key (multi-env key support).
        (
            exchange_provider,
            testnet_flag,
            _env_api_key_ignored,
            _env_api_secret_ignored,
            _env_passphrase_ignored,
        ) = _resolve_exchange_env_overrides(saved_settings)
        # Explicitly discard env credential values — only provider/testnet used.
        # C1: exchange API key/secret MUST come from keyring only — no plaintext
        # env fallback. Rotating committed .env.bak.testnet secrets is the only
        # real remediation; this gate prevents future plaintext leaks. If keyring
        # is unavailable (headless CI, Docker without keyring), startup fails
        # loudly with KeyError instead of silently using a leaked env var.
        _key_env = "testnet" if testnet_flag else "mainnet"
        sm = SecretManager()
        api_key_ref = SecretRef(
            source=SecretSource.KEYRING,
            provider="selenespace.exchange",
            id=f"{_key_env}_api_key",
        )
        api_secret_ref = SecretRef(
            source=SecretSource.KEYRING,
            provider="selenespace.exchange",
            id=f"{_key_env}_api_secret",
        )
        passphrase_ref = SecretRef(
            source=SecretSource.KEYRING,
            provider="selenespace.exchange",
            id=f"{_key_env}_passphrase",
        )

        if not testnet_flag:
            logger.warning(
                "Exchange testnet=FALSE — connecting to PRODUCTION mainnet. "
                "Ensure you have validated on testnet first."
            )
        else:
            logger.info("Exchange testnet=TRUE — connecting to testnet/sandbox.")

        api_key: str | None = None
        api_secret: str | None = None
        passphrase: str | None = None
        try:
            if await sm.exists(api_key_ref):
                api_key = (await sm.resolve(api_key_ref)).use()
            if await sm.exists(api_secret_ref):
                api_secret = (await sm.resolve(api_secret_ref)).use()
            if await sm.exists(passphrase_ref):
                passphrase = (await sm.resolve(passphrase_ref)).use()
        except Exception as e:
            logger.error("Failed to resolve exchange secrets from keyring: %s", e)
            raise KeyError(
                "Exchange API key/secret must be stored in OS keyring "
                "(run `selenespace setup`). Plaintext .env fallback removed (C1)."
            ) from e

        if api_key is None or api_secret is None:
            # C1 fail-closed relaxed for local dev: warn + skip live engine
            # instead of crashing startup. Paper/shadow/observing still work.
            # Set SELENE_STRICT_CREDS=1 to restore fail-closed behavior.
            _strict = os.environ.get("SELENE_STRICT_CREDS", "0").strip() in {
                "1",
                "true",
                "True",
                "yes",
            }
            if _strict:
                raise KeyError(
                    f"Exchange API key/secret not found in keyring for {_key_env} "
                    f"(provider=selenespace.exchange, id={_key_env}_api_key/{_key_env}_api_secret). "
                    "Run `selenespace setup` to store credentials. "
                    "Plaintext .env fallback removed (C1)."
                )
            logger.warning(
                "Exchange API key/secret not found in keyring for %s — "
                "skipping LiveExecutionEngine init (paper/shadow still work). "
                "Run `selenespace setup` to enable live trading. "
                "Set SELENE_STRICT_CREDS=1 to restore fail-closed.",
                _key_env,
            )
            # Skip the rest of live-engine init (creds/client dependent).
            # Outer `except Exception` swallows this; subsequent code has None-guards.
            raise RuntimeError(
                "skip live engine: missing exchange credentials (dev mode)"
            )

        passphrase = passphrase or ""

        if api_key and api_secret:
            from pydantic import SecretStr

            from selene_space_exchange.base.auth import ExchangeCredentials

            creds = ExchangeCredentials(
                api_key=SecretStr(api_key),
                api_secret=SecretStr(api_secret),
                passphrase=SecretStr(passphrase) if passphrase else None,
            )

            client: Any = None
            # Per-exchange shared rate limiters. Same limiter is reused
            # across the exchange's REST client + trading adapter so they
            # share the API quota; different exchanges stay independent
            # (distinct keys/IPs, distinct quota envelopes).
            from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter

            _binance_limiter = SlidingWindowRateLimiter(
                calls_per_window=1200, window_ms=60_000
            )
            _okx_limiter = SlidingWindowRateLimiter(
                calls_per_window=60, window_ms=2_000
            )

            # market_type: "spot" (default) or "futures". Drives which
            # REST client + adapter is constructed. Binance/OKX both have
            # distinct spot vs futures (SWAP/perp) endpoints, rate limits,
            # and (for futures) margin-mode / leverage / position-mode
            # semantics handled by their respective Futures adapters.
            market_type = str(
                saved_settings.get("exchange", {}).get("market_type", "spot")
            ).lower()
            env_market = os.getenv("SELENE_EXCHANGE_MARKET_TYPE", "").strip().lower()
            if env_market in ("spot", "futures"):
                market_type = env_market

            client = None
            if exchange_provider == "binance":
                if market_type == "futures":
                    from selene_space_exchange.exchanges.binance.futures_client import (
                        BinanceFuturesRESTClient,
                    )
                    from selene_space_exchange.trading import (
                        BinanceFuturesTradingAdapter,
                    )

                    bc: Any = BinanceFuturesRESTClient(
                        credentials=creds,
                        testnet=testnet_flag,
                        rate_limiter=_binance_limiter,
                    )
                    client = BinanceFuturesTradingAdapter(bc)
                    logger.info(
                        "Binance FUTURES adapter constructed (testnet=%s)", testnet_flag
                    )
                else:
                    from selene_space_exchange.exchanges.binance.rest_client import (
                        BinanceRESTClient,
                    )
                    from selene_space_exchange.trading import BinanceTradingAdapter

                    bc = BinanceRESTClient(
                        credentials=creds,
                        testnet=testnet_flag,
                        rate_limiter=_binance_limiter,
                    )
                    client = BinanceTradingAdapter(bc)
                    logger.info(
                        "Binance SPOT adapter constructed (testnet=%s)", testnet_flag
                    )
            elif exchange_provider == "okx":
                from selene_space_exchange.exchanges.okx.rest_client import (
                    OKXRESTClient,
                )
                from selene_space_exchange.trading import OKXTradingAdapter

                oc = OKXRESTClient(
                    credentials=creds, demo=testnet_flag, rate_limiter=_okx_limiter
                )
                # OKXTradingAdapter accepts market_type to switch between
                # SPOT (tdMode=cash) and SWAP/perp (tdMode=cross) orders.
                client = OKXTradingAdapter(oc, market_type=market_type)
                logger.info(
                    "OKX %s adapter constructed (demo=%s)",
                    market_type.upper(),
                    testnet_flag,
                )

            if client is not None:
                # Phase 6-P0 Amendment 4: Verify API key permissions before
                # constructing LiveExecutionEngine. Behavior is mode-driven:
                #
                # - SELENE_ENABLE_FUTURES=0 (default): spot-only safety gate.
                #   enableFutures/enableWithdrawals/enableMargin/enableVanillaOptions
                #   must ALL be False. Any True → refuse to start.
                #
                # - SELENE_ENABLE_FUTURES=1: futures-whitelist gate. Requires
                #   enableFutures=True AND all other dangerous perms False.
                #   A key with futures=True but also withdraw=True is still
                #   refused — the whitelist is strict, not an escape hatch.
                #
                #   Rationale: keeps spot-only safety design intact (default
                #   behavior unchanged), opens an explicit opt-in path for
                #   futures live trading without weakening the gate.
                _enable_futures = os.environ.get(
                    "SELENE_ENABLE_FUTURES", "0"
                ).strip() in {
                    "1",
                    "true",
                    "True",
                    "yes",
                }
                try:
                    if hasattr(client, "get_api_restrictions"):
                        restrictions = await client.get_api_restrictions()
                        _dangerous_perms = {
                            "enableWithdrawals": restrictions.get(
                                "enableWithdrawals", False
                            ),
                            "enableFutures": restrictions.get("enableFutures", False),
                            "enableMargin": restrictions.get("enableMargin", False),
                            "enableVanillaOptions": restrictions.get(
                                "enableVanillaOptions", False
                            ),
                        }
                        if _enable_futures:
                            # Futures whitelist: must have futures=True, others False.
                            _has_futures = _dangerous_perms["enableFutures"]
                            _other_violations = [
                                k
                                for k, v in _dangerous_perms.items()
                                if k != "enableFutures" and v
                            ]
                            if not _has_futures or _other_violations:
                                _msg = (
                                    f"Futures whitelist gate failed: enableFutures={_has_futures}, "
                                    f"other dangerous perms True={_other_violations}. "
                                    f"Re-issue the key with ONLY futures trade permission "
                                    f"(no withdraw/margin/options)."
                                )
                                logger.error(_msg)
                                raise RuntimeError(_msg)
                            logger.info(
                                "Futures whitelist gate passed: "
                                "API key has futures trade permission only"
                            )
                        else:
                            # Spot-only safety gate (default): any dangerous perm True → refuse.
                            _violations = [k for k, v in _dangerous_perms.items() if v]
                            if _violations:
                                _msg = (
                                    f"API key has dangerous permissions: {_violations}. "
                                    f"Refusing to start LiveExecutionEngine. "
                                    f"Re-issue the key with ONLY spot trade permission, "
                                    f"or set SELENE_ENABLE_FUTURES=1 for futures whitelist."
                                )
                                logger.error(_msg)
                                raise RuntimeError(_msg)
                            logger.info(
                                "Phase 6-P0 Amendment 4: API key permissions OK (spot trade only)"
                            )
                except RuntimeError:
                    raise
                except Exception as _amend4_err:
                    # testnet may not support /sapi/v1/account/apiRestrictions.
                    # Default-deny: if we cannot verify permissions, refuse to
                    # start rather than silently allow (defense in depth).
                    logger.error(
                        "Amendment 4 API restrictions check failed (default-deny): %s",
                        _amend4_err,
                    )
                    raise RuntimeError(
                        f"Cannot verify API key permissions: {_amend4_err}. "
                        f"Refusing to start LiveExecutionEngine (default-deny)."
                    )

                # Phase 6-P0 Amendment 5: Check MIN_NOTIONAL for BTCUSDT.
                # If the configured per-order USD cap is below the exchange minimum,
                # log a warning (non-blocking — order will just be rejected by exchange).
                try:
                    if hasattr(client, "get_min_notional"):
                        _min_notional = await client.get_min_notional("BTCUSDT")
                        if _min_notional is not None and _min_notional > 50.0:
                            logger.warning(
                                "Amendment 5: BTCUSDT MIN_NOTIONAL=$%s exceeds the $50 "
                                "Phase 6 cap. Mainnet orders below $%s will be rejected.",
                                _min_notional,
                                _min_notional,
                            )
                        else:
                            logger.info(
                                "Amendment 5: BTCUSDT MIN_NOTIONAL=$%s (OK for $50 cap)",
                                _min_notional,
                            )
                except Exception as _amend5_err:
                    logger.warning(
                        "Amendment 5 MIN_NOTIONAL check skipped: %s", _amend5_err
                    )

                from selene_space_exchange.execution.live_engine import (
                    LiveExecutionEngine,
                )

                live_engine_instance = LiveExecutionEngine(
                    trading_client=client,
                    reconciliation_interval_s=60.0,
                    journal=order_journal,
                    state_repo=live_state_repo,
                )
                set_live_engine(live_engine_instance)
                asyncio.create_task(live_engine_instance.start())
                _bridge_execution_events(live_engine_instance)
                logger.info(
                    "Live execution engine initialized with %s adapter",
                    exchange_provider,
                )

                # Register KillSwitch auto-cancel callback: when KillSwitch
                # activates, cancel all open orders on every registered
                # trading client. Structure supports multi-exchange; today
                # we register one client per startup (binance xor okx),
                # but the list is ready for P1-3 multi-venue kill switch.
                from selene_space_risk.kill_switch import KillSwitch

                kill_switch = KillSwitch()  # singleton
                _kill_switch_clients: list = []

                def _on_kill_switch_activate() -> None:
                    """Cancel all open orders on every registered client + alert.

                    P1-3 scope-aware: queries active_scopes() to report which
                    scope (SYSTEM/ACCOUNT/STRATEGY) triggered the activation.
                    Today every client's cancel_all_open_orders fires because
                    TradingClient API is venue-level, not account-scoped. When
                    account-scoped cancel is added, filter clients by matching
                    the ACCOUNT scope_id against the client's account_id.
                    """
                    active = kill_switch.active_scopes()
                    scope_summary = (
                        ", ".join(f"{s.value}={sid or '*'}" for s, sid in active)
                        or "unknown"
                    )
                    for c in list(_kill_switch_clients):
                        asyncio.ensure_future(c.cancel_all_open_orders())
                    # Fire-and-forget trade alert so operators are paged.
                    try:
                        from app.core.notification_dispatch import dispatch

                        dispatch(
                            "kill_switch_activated",
                            f"KILL SWITCH ACTIVATED [{scope_summary}] — cancelling all open orders "
                            f"on {len(_kill_switch_clients)} client(s). reason={kill_switch.reason}",
                        )
                    except Exception:
                        logger.warning(
                            "send_trade_alert failed on kill switch", exc_info=True
                        )

                _kill_switch_clients.append(client)
                kill_switch.on_activate(_on_kill_switch_activate)
                logger.info(
                    "KillSwitch registered: auto-cancel-all-orders + alert on %d client(s)",
                    len(_kill_switch_clients),
                )

                # Startup reconciliation with real exchange client
                try:
                    from selene_space_exchange.execution.startup_reconciliation import (
                        StartupReconciler,
                    )

                    reconciler = StartupReconciler()
                    # Bounded timeout: if Binance API is unreachable (dead SOCKS proxy,
                    # network down), fail fast instead of blocking lifespan indefinitely.
                    report = await asyncio.wait_for(
                        reconciler.reconcile(
                            trading_client=client,
                            state_repo=live_state_repo,
                            journal=order_journal,
                        ),
                        timeout=10.0,
                    )
                    if report.discrepancies:
                        logger.warning(
                            "Live startup reconciliation: %d discrepancies",
                            len(report.discrepancies),
                        )
                    else:
                        logger.info("Live startup reconciliation clean")
                except asyncio.TimeoutError:
                    logger.warning(
                        "Live startup reconciliation timed out after 10s (non-fatal)"
                    )
                except Exception:
                    logger.warning(
                        "Live startup reconciliation failed (non-fatal)", exc_info=True
                    )
    except KeyError:
        # C1: keyring credential resolution failure must fail-closed — do NOT
        # swallow KeyError from the keyring-only loader. Startup aborts so the
        # operator knows secrets are missing instead of silently running paper.
        logger.error(
            "Exchange credentials missing from keyring (C1 fail-closed)", exc_info=True
        )
        raise
    except RuntimeError as _skip_err:
        # Dev-mode skip: missing exchange credentials with SELENE_STRICT_CREDS unset.
        # LiveExecutionEngine stays None; paper/shadow/observing still work.
        if "skip live engine" in str(_skip_err):
            logger.info("Live engine skipped (dev mode, no exchange credentials)")
        else:
            logger.error("Failed to initialize live execution engine", exc_info=True)
            raise
    except Exception:
        logger.error("Failed to initialize live execution engine", exc_info=True)

    # --- Binance User Data WebSocket (private order/position/balance stream) ---
    # Wire BinanceUserDataClient → LiveExecutionEngine.handle_* handlers.
    # This gives real-time (<1s) order/position/balance updates instead of
    # the 60s REST polling fallback.

    def _record_ws_reconnect(_app) -> None:
        """Phase 5 observability: record private WS reconnect on WsHealthMonitor."""
        _monitor = getattr(_app.state, "ws_health_monitor", None)
        if _monitor is not None:
            try:
                _monitor.record_reconnect()
            except Exception:
                logger.debug("ws_health_monitor.record_reconnect failed", exc_info=True)

    from app.core.dependencies import get_live_engine as _get_live_engine_for_ws

    if _get_live_engine_for_ws() is not None and exchange_provider == "binance":
        try:
            from selene_space_exchange.exchanges.binance.user_data_client import (
                BinanceUserDataClient,
            )

            # Reuse the api_key/api_secret resolved above (still in scope)
            if api_key and api_secret:
                live_engine_ref = _get_live_engine_for_ws()
                # Match the REST client's testnet toggle — private WS URL
                # must point at the same network or session.logon fails.
                from selene_space_exchange.exchanges.binance.user_data_client import (
                    BINANCE_WS_API_TESTNET_URL,
                    BINANCE_WS_API_URL as _BINANCE_WS_API_URL,
                )

                _ws_url = (
                    BINANCE_WS_API_TESTNET_URL if testnet_flag else _BINANCE_WS_API_URL
                )
                _binance_user_data_client = BinanceUserDataClient(
                    api_key=api_key,
                    api_secret=api_secret,
                    url=_ws_url,
                    testnet=testnet_flag,
                    on_order_update=live_engine_ref.handle_exchange_order_update,
                    on_balance_update=live_engine_ref.handle_balance_update,
                    on_reconnect=lambda: _record_ws_reconnect(app),
                )

                async def _start_binance_user_data() -> None:
                    try:
                        await _binance_user_data_client.connect()
                        # streams arg is ignored by the WS API path;
                        # subscribe() does session.logon + userDataStream.subscribe.
                        await _binance_user_data_client.subscribe([])
                    except Exception:
                        logger.warning(
                            "BinanceUserDataClient start failed", exc_info=True
                        )

                asyncio.create_task(_start_binance_user_data())
                _binance_user_data_clients.append(_binance_user_data_client)
                logger.info("BinanceUserDataClient starting (private WS)")
            else:
                logger.info("BinanceUserDataClient skipped (no API keys)")
        except Exception:
            logger.warning(
                "Failed to start BinanceUserDataClient (non-fatal)", exc_info=True
            )

    # If execution_mode=live and the live engine came up, wire it into
    # the StrategyRunner via set_engines(). The runner started with
    # live_engine=None (safe default) while waiting for live init.
    # We also wire the shadow_engine and mode_provider here so the
    # runner can dispatch via _resolve_mode() + _select_engine().
    if execution_mode == "live" and strategy_runner_instance is not None:
        from app.core.dependencies import get_live_engine as _get_live_engine_for_rebind

        _le_for_rebind = _get_live_engine_for_rebind()
        if _le_for_rebind is not None:
            strategy_runner_instance.set_engines(
                live_engine=_le_for_rebind,
                shadow_engine=shadow_engine,
                mode_provider=lambda: execution_mode,
            )
            logger.warning(
                "StrategyRunner wired to LIVE engine — real orders will be placed!"
            )
        else:
            logger.warning(
                "execution_mode=live but LiveExecutionEngine is None; "
                "StrategyRunner stays on paper. Check exchange credentials."
            )

    # --- Wire execution engines into StrategyRunner ---
    # The runner is created early (before live/shadow engines exist), so
    # we inject them now via set_engines(). The mode_provider callable lets
    # the runner read the current execution mode on each signal.
    if strategy_runner_instance is not None:
        try:
            from app.core.dependencies import (
                get_execution_mode as _get_mode,
                get_live_engine as _get_live_engine,
            )

            _le = _get_live_engine()
            # set_engines() wires live/shadow engines + mode_provider into
            # the runner. The runner uses _resolve_mode() + _select_engine()
            # to dispatch orders at signal time. EventGuard is wired
            # separately via set_event_guard. live_pipeline is wired later
            # once LiveDecisionPipeline is built.
            strategy_runner_instance.set_engines(
                live_engine=_le,
                shadow_engine=shadow_engine,
                mode_provider=_get_mode,
            )
            logger.info(
                "StrategyRunner wired: live_engine=%s shadow_engine=%s",
                "yes" if _le is not None else "no",
                "yes" if shadow_engine is not None else "no",
            )

            # Wire LiveDecisionPipeline (Phase 2 safety net: dedup / throttle /
            # risk pre-check / position limits / kill-switch gate). Runs for
            # any non-paper mode; in CONFIRM it complements OrderConfirmationManager.
            if _le is not None and risk_engine_instance is not None:
                try:
                    from selene_space_paper.live_decision_pipeline import (
                        LiveDecisionPipeline,
                    )
                    from selene_space_paper.audit_ledger import HashChainLedger
                    from selene_space_paper.trailing_stop import (
                        TrailingStopConfig,
                        TrailingStopTracker,
                    )
                    from selene_space_paper.tiered_trailing_stop import (
                        TieredTrailingConfig,
                        TieredTrailingStopTracker,
                    )
                    from selene_space_risk.kill_switch import KillSwitch

                    # Trailing-stop tracker: ratchets peak on winning longs,
                    # emits a SELL through the pipeline when drawdown from
                    # peak exceeds trail_drop (after activation_gain reached).
                    _trailing_stop_tracker = TrailingStopTracker(
                        config=TrailingStopConfig(
                            activation_gain=0.05,
                            trail_drop=0.03,
                            initial_stop_pct=0.05,
                            min_emit_interval_seconds=30.0,
                        )
                    )

                    def _regime_provider(symbol: str) -> list[float]:
                        """Return recent close prices for regime detection.

                        Tries the in-memory kline buffer for the configured
                        exchange/market_type. Returns [] on any failure —
                        the pipeline treats empty as "no regime info".
                        """
                        try:
                            # Normalize "BTC/USDT" → "BTCUSDT" for Binance,
                            # but keep original for OKX. Try both forms.
                            candidates = [
                                symbol,
                                symbol.replace("/", "-"),
                                symbol.replace("/", ""),
                            ]
                            for _sym in candidates:
                                for _ex, _mt in getattr(
                                    market_svc, "sources", {}
                                ).keys() or [("binance", "spot")]:
                                    klines = market_svc.get_klines(
                                        _sym, exchange=_ex, market_type=_mt, limit=120
                                    )
                                    if klines:
                                        return [float(k.close) for k in klines]
                        except Exception:
                            return []
                        return []

                    def _risk_context_provider() -> dict:
                        """Supply context for the vol-scaled Kelly rule.

                        Pulls recent close prices from market_svc and computes
                        pct_change → returns series. trade_history is left
                        empty for now (rule falls back to no-op when below
                        min_trades_for_estimation, which is safe).

                        total_equity comes from the live engine's in-memory
                        balance cache (sync snapshot of WS-pushed balances).
                        Falls back to a 10000 USDT placeholder if no live
                        engine is wired or no balances have been received.
                        """
                        # Resolve real total_equity from live engine balance cache.
                        try:
                            from app.core.dependencies import get_live_engine as _get_le

                            _le_inst = _get_le()
                            if _le_inst is not None and hasattr(
                                _le_inst, "get_cached_balances"
                            ):
                                balances = _le_inst.get_cached_balances()
                                usdt_total = 0.0
                                for b in balances:
                                    try:
                                        asset_upper = (b.asset or "").upper()
                                    except AttributeError:
                                        continue
                                    if asset_upper in ("USDT", "USDC", "BUSD"):
                                        usdt_total += b.free + b.locked
                                if usdt_total > 0:
                                    _equity = usdt_total
                                else:
                                    _equity = 10_000.0
                            else:
                                _equity = 10_000.0
                        except Exception:
                            _equity = 10_000.0

                        # ATM IV for the IvHvBubbleRule. Advisory data — on
                        # any failure the key stays None and the rule
                        # degrades to a no-op (its check() returns None when
                        # iv_annualized is missing).
                        iv_annualized: float | None = None
                        try:
                            _iv = market_svc.get_atm_iv(currency="BTC")
                            if _iv is not None:
                                iv_annualized = _iv
                        except Exception:
                            pass

                        try:
                            # Use BTC/USDT as a market-wide vol proxy when
                            # the per-symbol lookup fails; the rule is
                            # advisory and tolerates a missing returns series.
                            for _sym in ("BTC/USDT", "BTCUSDT", "BTC-USDT"):
                                for _ex, _mt in getattr(
                                    market_svc, "sources", {}
                                ).keys() or [("binance", "spot")]:
                                    klines = market_svc.get_klines(
                                        _sym, exchange=_ex, market_type=_mt, limit=60
                                    )
                                    if klines and len(klines) >= 2:
                                        closes = [float(k.close) for k in klines]
                                        returns = [
                                            (closes[i] - closes[i - 1]) / closes[i - 1]
                                            for i in range(1, len(closes))
                                            if closes[i - 1] > 0
                                        ]
                                        return {
                                            "total_equity": _equity,
                                            "trade_history": [],
                                            "returns": returns,
                                            "iv_annualized": iv_annualized,
                                        }
                        except Exception:
                            pass
                        return {
                            "total_equity": _equity,
                            "trade_history": [],
                            "returns": [],
                            "iv_annualized": iv_annualized,
                        }

                    # Build pipeline base config with regime block flags.
                    # TRANSITION (high vol) blocks new entries by default —
                    # almost no strategy benefits from entering in chop.
                    # BEAR block is opt-in via env var, since short / inverse
                    # strategies may want to trade bear markets.
                    import os as _os

                    from selene_space_paper.decision_pipeline import (
                        PipelineConfig as _PipelineConfig,
                    )

                    _block_on_bear = _os.environ.get(
                        "SELENE_BLOCK_BEAR", "0"
                    ).lower() in ("1", "true", "yes")
                    _pipeline_base_cfg = _PipelineConfig(
                        block_on_transition=True,
                        block_on_bear=_block_on_bear,
                        regime_lookback=60,
                    )

                    # v017-frontend: 构造 tiered trailing tracker 从 settings
                    from app.core.dependencies import (
                        set_tiered_trailing_tracker,
                    )

                    _settings_data = await settings_service.get_settings(user_id=None)
                    _risk_cfg = _settings_data.get("risk", {})
                    _tiered_cfg = TieredTrailingConfig(
                        hard_stop_pct=float(
                            _risk_cfg.get("tiered_hard_stop_pct", 0.02)
                        ),
                        low_tier_threshold=float(
                            _risk_cfg.get("tiered_low_tier_threshold", 0.015)
                        ),
                        first_tier_threshold=float(
                            _risk_cfg.get("tiered_first_tier_threshold", 0.03)
                        ),
                        second_tier_threshold=float(
                            _risk_cfg.get("tiered_second_tier_threshold", 0.06)
                        ),
                        low_tier_drop_pct=float(
                            _risk_cfg.get("tiered_low_tier_drop_pct", 0.5)
                        ),
                        first_tier_drop_pct=float(
                            _risk_cfg.get("tiered_first_tier_drop_pct", 0.30)
                        ),
                        second_tier_drop_pct=float(
                            _risk_cfg.get("tiered_second_tier_drop_pct", 0.20)
                        ),
                        min_emit_interval_seconds=float(
                            _risk_cfg.get("tiered_min_emit_interval_seconds", 30.0)
                        ),
                    )
                    _tiered_tracker = TieredTrailingStopTracker(_tiered_cfg)
                    set_tiered_trailing_tracker(_tiered_tracker)
                    logger.info(
                        "Tiered trailing tracker wired: hard_stop=%.3f t1=%.3f t2=%.3f t3=%.3f",
                        _tiered_cfg.hard_stop_pct,
                        _tiered_cfg.low_tier_threshold,
                        _tiered_cfg.first_tier_threshold,
                        _tiered_cfg.second_tier_threshold,
                    )

                    _live_pipeline = LiveDecisionPipeline(
                        risk_engine=risk_engine_instance,
                        order_manager=_le,
                        # S4 v1: same shared instance the anomaly decision
                        # consumer writes (constructed earlier in lifespan) —
                        # one ledger file, one hash chain.
                        audit_ledger=_shared_decision_ledger,
                        kill_switch=KillSwitch(),  # singleton — same instance _ks refers to
                        config=_pipeline_base_cfg,
                        risk_context_provider=_risk_context_provider,
                        regime_provider=_regime_provider,
                        trailing_stop=_trailing_stop_tracker,
                        tiered_trailing_stop=_tiered_tracker,
                    )
                    strategy_runner_instance.set_engines(live_pipeline=_live_pipeline)
                    logger.info(
                        "StrategyRunner wired: live_pipeline=yes "
                        "(regime=on, trailing_stop=on, vol_scaled_kelly=on, "
                        "block_transition=on, block_bear=%s)",
                        "on" if _block_on_bear else "off",
                    )
                except Exception:
                    logger.warning(
                        "Failed to wire LiveDecisionPipeline into StrategyRunner",
                        exc_info=True,
                    )

            # Wire ExecutionModeGuard singleton. The runner calls guard.check()
            # on LIMITED_AUTO / FULL_AUTO signals to enforce hard notional caps.
            # The mode_provider above is the source of truth for the *current*
            # mode; we sync guard.mode whenever /trading/mode is switched.
            try:
                from selene_space_exchange.execution.mode_guard import (
                    ExecutionMode as _ExMode,
                    ExecutionModeGuard as _ExGuard,
                )
                from app.core.dependencies import set_mode_guard as _set_mode_guard

                _mode_str_init = _get_mode() or "paper"
                _mode_map = {
                    "paper": _ExMode.PAPER,
                    "shadow": _ExMode.SHADOW,
                    "confirm": _ExMode.CONFIRM,
                    "limited_auto": _ExMode.LIMITED_AUTO,
                    "full_auto": _ExMode.FULL_AUTO,
                }
                _guard = _ExGuard(mode=_mode_map.get(_mode_str_init, _ExMode.PAPER))
                _set_mode_guard(_guard)
                logger.info(
                    "ExecutionModeGuard wired: mode=%s",
                    _mode_str_init,
                )
            except Exception:
                logger.warning(
                    "Failed to wire ExecutionModeGuard (non-fatal)",
                    exc_info=True,
                )
        except Exception:
            logger.warning(
                "Failed to wire execution engines into StrategyRunner",
                exc_info=True,
            )

    # --- Risk Engine ---
    try:
        from selene_space_risk.engine import RiskEngine
        from selene_space_risk.rules import create_default_rules
        from selene_space_risk.advanced_rules import (
            IvHvBubbleRule,
            KellyPositionSizingRule,
            VolatilityScaledKellyRule,
        )

        risk_rules = create_default_rules()
        # Swap the default Kelly rule for the vol-scaled variant. The
        # vol-scaled rule layers inverse-volatility position scaling on
        # top of half-Kelly, shrinking exposure in turbulent markets and
        # growing it (up to 1.5x) in quiet ones. Falls back to plain
        # half-Kelly when no returns series is supplied in risk context.
        risk_rules = [
            VolatilityScaledKellyRule() if isinstance(r, KellyPositionSizingRule) else r
            for r in risk_rules
        ]
        # Opt-in activation of the IV/HV bubble rule (kept out of
        # create_advanced_rules() to preserve its seven-rule contract).
        risk_rules.append(IvHvBubbleRule())

        # Apply risk params from saved settings if available
        risk_settings = saved_settings.get("risk", {})
        if risk_settings:
            for rule in risk_rules:
                rid = rule.rule_id
                if rid == "max_position" and risk_settings.get("max_position_pct"):
                    rule._max_pct = float(risk_settings["max_position_pct"])
                elif rid == "max_single_risk" and risk_settings.get("single_risk_pct"):
                    rule._max_pct = float(risk_settings["single_risk_pct"])
                elif rid == "daily_loss_limit" and risk_settings.get(
                    "daily_loss_limit_pct"
                ):
                    rule._max_pct = float(risk_settings["daily_loss_limit_pct"])
                elif rid == "cooldown" and risk_settings.get("cooldown_seconds"):
                    rule._cooldown = float(risk_settings["cooldown_seconds"])

        risk_engine_instance = RiskEngine(rules=risk_rules)
        set_risk_engine(risk_engine_instance)
        logger.info("Risk engine initialized with %d rules", len(risk_rules))

        # Wire risk_engine + kill_switch back into the LiveExecutionEngine
        # (assembled earlier in lifespan). This closes the pre-trade risk
        # gate: place_order now checks kill_switch.is_active + pre_check
        # before touching journal or REST. Without this, live trading
        # runs "裸跑" (zero risk checks on the order path).
        try:
            from app.core.dependencies import get_live_engine as _get_live_engine
            from selene_space_exchange.execution.ws_health_monitor import (
                WsHealthMonitor,
            )

            _ws_health_monitor = WsHealthMonitor()
            _le = _get_live_engine()

            # S0-3 (v4.1): unconditional live fills ledger — defined in ALL modes
            # (incl. paper/shadow where _le is None). On failure: critical log +
            # degrade to None so the ContinuousRiskMonitor / T2/T3 references no-op
            # instead of a swallowed second NameError (double-silent).
            from selene_space_risk.fills_ledger import FillsLedger as _LiveFillsLedger

            _live_fills = None
            try:
                _live_fills = _LiveFillsLedger()
            except Exception as _live_fills_err:
                logger.critical(
                    "Live FillsLedger init FAILED — risk/fee monitors degraded (fills_ledger=None): %s",
                    _live_fills_err,
                )

            if _le is not None:
                _le.set_risk_engine(risk_engine_instance)
                _le.set_kill_switch(KillSwitch())  # singleton
                _le.set_ws_health_monitor(
                    _ws_health_monitor
                )  # Phase 3: Binance 1008 降级
                logger.info(
                    "Pre-trade risk gate + WS health monitor wired into LiveExecutionEngine"
                )

                # S0-3 (v4.1): live fills ledger read + write side. on_fill is
                # append-semantics (:960-961) — single registration ONLY; T2/T3 must
                # not re-register (triple-registration → 3x DB writes per fill).
                if _live_fills is not None:
                    _le.set_fills_ledger(_live_fills)
                    _le.on_fill(_record_live_fill(_live_fills, LIVE_ACCOUNT_ID))

                # Bug C: inject StopManager + bridge ticker for stop-loss evaluation.
                # v3 BLOCK A fix: wired AFTER set_kill_switch so _execute_stop_close's
                # fallback (if self._kill_switch is not None) is reachable.
                try:
                    from selene_space_exchange.execution.stop_manager import StopManager

                    _le.set_stop_manager(StopManager())
                    # bridge price ticks to LiveEngine for stop-trigger evaluation
                    _market_svc = getattr(app.state, "market_data_service", None)
                    if _market_svc is not None:
                        _bridge_price_to_live(_market_svc, _le)
                    logger.info(
                        "StopManager wired into LiveExecutionEngine (Bug C integration)"
                    )
                except Exception:
                    logger.warning(
                        "Failed to wire StopManager into LiveEngine (non-fatal)",
                        exc_info=True,
                    )
            # Stash the monitor on app state so the market data bridge can record heartbeats
            app.state.ws_health_monitor = _ws_health_monitor
            app.state.fills_ledger = _live_fills
        except Exception:
            logger.warning(
                "Failed to wire risk gate into LiveEngine (non-fatal)", exc_info=True
            )

        # ContinuousRiskMonitor — periodic portfolio risk checks + KillSwitch linkage
        try:
            from selene_space_risk.kill_switch import KillSwitch
            from selene_space_risk.monitor import ContinuousRiskMonitor
            from selene_space_risk.flatten_hook import FlattenHook

            _ks = KillSwitch()  # singleton
            from app.core.dependencies import get_live_engine as _get_live_engine

            # T2: flatten callback channel — one shared FlattenHook for the
            # pre-trade CRITICAL path (engine) and the monitor cycle. The closure
            # no-ops when the dual-leg executor / perp venue isn't wired.
            from app.core.risk_assembly import build_flatten_closure
            from app.core.dependencies import (
                get_strategy_runner as _get_strategy_runner,
            )

            _flatten_hook = FlattenHook()
            _flatten_hook.register(
                build_flatten_closure(
                    strategy_runner=_get_strategy_runner(),
                    dual_leg_executor=getattr(app.state, "dual_leg_executor", None),
                    market_svc=getattr(app.state, "market_data_service", None),
                    perp_venue=getattr(app.state, "dual_leg_executor_venue", None),
                )
            )
            risk_monitor = ContinuousRiskMonitor(
                risk_engine=risk_engine_instance,
                kill_switch=_ks,
                execution_engine=_get_live_engine(),
                flatten_hook=_flatten_hook,
                fills_ledger=_live_fills,
                account_id=LIVE_ACCOUNT_ID,
                flatten_on_liquidation=os.environ.get(
                    "SELENE_FLATTEN_ON_LIQUIDATION", "1"
                )
                != "0",
                daily_loss_flatten_pct=float(
                    os.environ.get("SELENE_DAILY_LOSS_FLATTEN_PCT", "0.01")
                ),
            )
            await risk_monitor.start()
            set_risk_monitor(risk_monitor)
            app.state.flatten_hook = _flatten_hook

            # T1: funding-reversal watchdog (#3) — independent poll loop watching
            # the worst funding_arb pair. flatten reuses the shared FlattenHook.
            from app.core.risk_assembly import build_funding_reversal_monitor
            from app.core.dependencies import (
                get_funding_rate_fetcher,
                set_funding_reversal_monitor,
            )

            _fr_monitor = build_funding_reversal_monitor(
                strategy_runner=_get_strategy_runner(),
                funding_fetcher=get_funding_rate_fetcher(),
                kill_switch=_ks,
                flatten=_flatten_hook.fire,
                exit_threshold=float(
                    os.environ.get("SELENE_FUNDING_EXIT_THRESHOLD", "0.0001")
                ),
                poll_interval_s=float(os.environ.get("SELENE_FUNDING_POLL_S", "60")),
            )
            _fr_monitor.start()
            set_funding_reversal_monitor(_fr_monitor)
            app.state.funding_reversal_monitor = _fr_monitor
            logger.info(
                "FundingReversalMonitor started (exit_threshold=%s)",
                _fr_monitor._exit_threshold,
            )

            # T3: fee-rate monitor (#12) — VIP-tier anomaly watch. Alert-only by
            # default; the kill switch is armed only via SELENE_FEE_KILL_ON_ANOMALY=1.
            if os.environ.get("SELENE_FEE_MONITOR_ENABLED", "1") != "0":
                from app.core.risk_assembly import build_fee_monitor

                _fee_monitor = build_fee_monitor(
                    fills_ledger=_live_fills,
                    kill_switch=_ks,
                    account_id=LIVE_ACCOUNT_ID,
                    expected_taker_rate=float(
                        os.environ.get("SELENE_FEE_EXPECTED_TAKER_RATE", "0.0005")
                    ),
                    anomaly_threshold_pct=float(
                        os.environ.get("SELENE_FEE_ANOMALY_THRESHOLD_PCT", "0.5")
                    ),
                    enable_kill_on_anomaly=os.environ.get(
                        "SELENE_FEE_KILL_ON_ANOMALY", "0"
                    )
                    == "1",
                    confirmations=int(os.environ.get("SELENE_FEE_CONFIRMATIONS", "3")),
                    poll_interval_s=float(os.environ.get("SELENE_FEE_POLL_S", "3600")),
                )
                _fee_monitor.start()
                app.state.fee_monitor = _fee_monitor
                logger.info(
                    "FeeRateMonitor started (expected_taker=%s threshold_pct=%s)",
                    _fee_monitor._expected,
                    _fee_monitor._threshold_pct,
                )

            logger.info("ContinuousRiskMonitor started (30s/15s intervals)")
        except Exception:
            logger.warning(
                "Failed to start ContinuousRiskMonitor (non-fatal)", exc_info=True
            )
    except ImportError:
        logger.info("selene_space_risk not installed, risk engine disabled")
    except Exception:
        logger.warning("Failed to initialize risk engine", exc_info=True)

    logger.info(
        "SeleneQuant Local API started with multi-source MarketDataService + PaperExecutionEngine"
    )

    # --- Skills Registry ---
    skill_registry_instance = None
    try:
        from selene_space_agent.skills import SkillRegistry, SkillsConfig
        from selene_space_agent.skills.config import BUNDLED_SKILLS_DIR

        # Three disjoint skill sources, merged by registry priority
        # (bundled > user > extra): the repo-maintained set under skills/, the
        # user's ~/.selenespace/skills, and the package-shipped bundled_skills/.
        skill_registry_instance = SkillRegistry(
            config=SkillsConfig(extra_dirs=[str(BUNDLED_SKILLS_DIR)]),
            bundled_dir=Path(__file__).resolve().parents[3] / "skills",
            user_dir=Path.home() / ".selenespace" / "skills",
        )
        skill_registry_instance.load()
        set_skill_registry(skill_registry_instance)
        skill_count = len(skill_registry_instance.list_skills())
        logger.info("Skills registry loaded with %d skills", skill_count)
    except Exception:
        logger.warning("Failed to initialize Skills registry", exc_info=True)

    # FX09: Create a single ToolCatalog shared by MCP and Agent
    from selene_space_agent import ToolCatalog as _TC

    try:
        from selene_space_security.tool_policy import ToolPolicyEngine

        _policy_engine = ToolPolicyEngine()
        logger.info("ToolPolicyEngine loaded — tool gating active")
    except Exception:
        _policy_engine = None
        logger.warning("ToolPolicyEngine not available — all tools allowed")

    tool_catalog = _TC(policy_engine=_policy_engine)

    # --- MCP Registry (uses the shared tool_catalog) ---
    try:
        from selene_space_agent.mcp import McpRegistry
        from selene_space_agent.mcp.config import load_mcp_config

        mcp_config = load_mcp_config()
        if mcp_config.servers:
            mcp_registry_instance = McpRegistry()
            await mcp_registry_instance.start(mcp_config, tool_catalog)
            set_mcp_registry(mcp_registry_instance)
            logger.info(
                "MCP registry started with %d servers",
                len(mcp_registry_instance.list_servers()),
            )
        else:
            logger.info("No MCP servers configured, skipping MCP initialization")
    except ImportError:
        logger.info("MCP SDK not installed, MCP support disabled")
    except Exception:
        logger.warning("Failed to initialize MCP registry", exc_info=True)

    # --- Component Registry (Open Core architecture) ---
    from selene_space_agent.implementations import (
        BasicContextManager as BasicCtxMgr,
    )
    from selene_space_agent.implementations import (
        FixedModelRouter,
        KeywordMemoryBackend,
        RuleBasedFeedback,
        SingleAgentRunner,
    )
    from selene_space_agent.registry import ComponentRegistry

    component_registry = ComponentRegistry()
    component_registry.register("memory", KeywordMemoryBackend())
    component_registry.register("orchestrator", SingleAgentRunner())
    component_registry.register("model_router", FixedModelRouter())
    component_registry.register("context_engine", BasicCtxMgr())
    component_registry.register("feedback_engine", RuleBasedFeedback())
    logger.info(
        "Component registry initialized: %s",
        list(component_registry.list_registered().keys()),
    )

    # --- Register agent tools into the shared catalog ---
    _register_agent_tools(
        tool_catalog,
        market_service=market_svc,
        account_manager=paper_engine._account_mgr,
        order_manager=paper_engine._order_mgr,
        position_manager=paper_engine._position_mgr,
        risk_engine=risk_engine_instance,
    )
    set_tool_catalog(tool_catalog)
    logger.info(
        "Tool catalog initialized with %d tools: %s",
        len(tool_catalog),
        tool_catalog.tool_names(),
    )

    # --- Startup tool-reference audit (roles allowed_tools + skills requires_tools) ---
    try:
        from selene_space_agent.team.startup_check import check_tool_references

        registered = set(tool_catalog.tool_names())
        skills = []
        if skill_registry_instance is not None:
            skills = skill_registry_instance.list_skills()
        for problem in check_tool_references(registered, skills):
            logger.error("startup tool-reference check: %s", problem)
    except Exception:
        logger.debug("tool-reference audit skipped", exc_info=True)

    # --- Agent Runtime (wired to saved LLM settings) ---
    try:
        agent_runtime_instance = await _build_agent_runtime(
            settings_service, tool_catalog
        )
        if agent_runtime_instance is not None:
            set_agent_runtime(agent_runtime_instance)
            logger.info("Agent runtime initialized with saved LLM settings")
        else:
            logger.info("No LLM API key configured, agent runtime uses mock")
    except Exception:
        logger.warning("Failed to initialize agent runtime", exc_info=True)

    # --- TG signal poller — bridge tg_events DB → /ws/intelligence intel:events ---
    try:
        from app.api.v1.ws import start_tg_signal_poller

        start_tg_signal_poller()
        logger.info("TG signal poller started (bridges tg_events → intel:events WS)")
    except Exception:
        logger.warning("Failed to start TG signal poller", exc_info=True)

    # --- news-ingest 3 loops (binance-quant L3 吸收) — 末尾追加 ---
    # SELENE_NI_ENABLED 门控(默认关);init 失败只禁用 news 模块,绝不 crash 启动
    try:
        from app.api.v1.news import _get_store as _news_get_store

        import os as _os_news

        if _os_news.getenv("SELENE_NI_ENABLED", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            from selene_space_news_ingest.anomaly_detector import detector_loop
            from selene_space_news_ingest.news_collector import (
                poll_loop as _news_collect,
            )
            from selene_space_news_ingest.news_tagger import tagger_loop

            _news_get_store()  # 触发 DB init(失败走 except,禁用模块)
            _news_tasks = [
                asyncio.create_task(_news_collect(), name="news-collector"),
                asyncio.create_task(tagger_loop(), name="news-tagger"),
                asyncio.create_task(detector_loop(), name="news-detector"),
            ]
            state["_news_ingest_tasks"] = _news_tasks
            logger.info(
                "news-ingest started: %d loops (collector/tagger/detector)",
                len(_news_tasks),
            )
        else:
            logger.info("news-ingest disabled (SELENE_NI_ENABLED not set)")
    except Exception:
        logger.warning("Failed to start news-ingest loops", exc_info=True)

    # --- fund-ingest premium alert loop (stock BSD-3 吸收) — 末尾追加 ---
    # SELENE_FI_ALERT_ENABLED 门控(默认关);失败只禁用 fund-ingest 告警,不 crash 启动
    try:
        import os as _os_fi

        if _os_fi.getenv("SELENE_FI_ALERT_ENABLED", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            from selene_space_fund_ingest.premium_alert import (
                alert_loop as _fi_alert,
            )

            _fi_task = asyncio.create_task(
                _fi_alert(), name="fund-ingest-premium-alert"
            )
            state["_fund_ingest_alert_task"] = _fi_task
            logger.info("fund-ingest premium alert loop started")
        else:
            logger.info(
                "fund-ingest alert loop disabled (SELENE_FI_ALERT_ENABLED not set)"
            )
    except Exception:
        logger.warning("Failed to start fund-ingest alert loop", exc_info=True)

    # --- PA price alert loop (S7) — 末尾追加 ---
    # SELENE_PA_TRADE_ALERTS 门控(默认关);失败只禁用 PA 到价提醒,不 crash 启动
    try:
        import os as _os_pa_alert

        if _os_pa_alert.getenv("SELENE_PA_TRADE_ALERTS", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):

            async def _pa_trade_alert_loop(app: FastAPI) -> None:
                """Poll WS-cached tickers against PA price alerts every 30s."""
                from app.core.dependencies import get_market_service
                from app.core.price_alert_service import get_price_alert_service

                alert_svc = get_price_alert_service()
                while True:
                    await asyncio.sleep(30)
                    try:
                        market_svc = get_market_service()
                        if market_svc is None:
                            continue  # market data not up yet — skip round

                        # 价格源选型: get_ticker 同步读 WS 缓存,零额外请求;
                        # KlineDataManager 查库太重,轮询场景不合适。
                        # 局限(advisory ②): 本 loop 不自动订阅。futures 的
                        # ticker 由 source 的 REST 轮询任务覆盖 _watched 集
                        # (add_symbol 会加入);规则通常由 panel 创建,panel 必
                        # 先经 REST ticker 触发 _ensure_subscribed,故实用窗口内
                        # 缓存是热的。冷 symbol 不触发,规则诚实保留。若未来要
                        # 支持"闭面板也提醒",需在此加懒订阅 add_symbol。
                        def _read_price(
                            symbol: str, _interval: str, _mt: str
                        ) -> float | None:
                            ticker = market_svc.get_ticker(
                                symbol, exchange="binance", market_type="futures"
                            )
                            return ticker.last_price if ticker is not None else None

                        alerts = await alert_svc.check_alerts(_read_price)
                        for hit in alerts:
                            logger.info(
                                "PA price alert fired: %s %s level=%s price=%s",
                                hit["rule"]["symbol"],
                                hit["rule"]["interval"],
                                hit["level"],
                                hit["price"],
                            )
                    except Exception:
                        logger.warning("PA price alert round failed", exc_info=True)

            _pa_alert_task = asyncio.create_task(
                _pa_trade_alert_loop(app), name="pa-trade-alert"
            )
            state["_pa_trade_alert_task"] = _pa_alert_task
            logger.info("PA price alert loop started (30s poll)")
        else:
            logger.info("PA price alert loop disabled (SELENE_PA_TRADE_ALERTS not set)")
    except Exception:
        logger.warning("Failed to start PA price alert loop", exc_info=True)

    # --- PA heartbeat loop (T1) — 末尾追加 ---
    # SELENE_PA_HEARTBEAT 门控(默认关);失败只禁用 PA 心跳,不 crash 启动
    try:
        from app.core.pa_heartbeat import (
            PaHeartbeatConfig,
            PaHeartbeatScheduler,
            heartbeat_enabled,
        )

        if heartbeat_enabled():
            from app.core.dependencies import (
                get_decision_workflow_service,
                get_kline_manager,
                get_settings_service,
            )

            _pa_heartbeat = PaHeartbeatScheduler(
                settings_loader=get_settings_service,
                service_loader=get_decision_workflow_service,
                config=PaHeartbeatConfig.from_env(),
                sync_loader=get_kline_manager,
            )
            await _pa_heartbeat.start()
            state["_pa_heartbeat"] = _pa_heartbeat
            app.state._pa_heartbeat = (
                _pa_heartbeat  # GET /heartbeat/status 经 request.app.state 读
            )
            logger.info(
                "PA heartbeat loop started (daily watchlist diagnosis, %d/day cap)",
                _pa_heartbeat.status()["config"]["daily_max"],
            )
        else:
            logger.info("PA heartbeat disabled (SELENE_PA_HEARTBEAT not set)")
    except Exception:
        logger.warning("Failed to start PA heartbeat loop", exc_info=True)

    # --- A-share style-report warmup (batch6 US-2) — 末尾追加 ---
    # Default on; SELENE_SKIP_WARMUP=1 to skip (tests / cold-start probes).
    # Failure only disables the warmup — never crashes startup.
    try:

        async def _ashare_style_warmup() -> None:
            """Pre-build the style report so the first GET is cache-hits fast.

            Uses DEFAULT_REPORT_PARAMS from app.api.v1.ashare as the single
            source of truth. warmup=True tells the service to skip the
            negative cache on an empty build, so a temporarily-empty kline
            cache at boot doesn't poison the first real request.
            """
            from app.api.v1.ashare import (
                DEFAULT_REPORT_PARAMS as _drp,
                _hs300_codes,
            )
            from app.services.ashare_style_report import get_style_report as _get

            try:
                codes = await _hs300_codes()
                _t0 = time.perf_counter()
                report = await _get(
                    codes,
                    _drp["start_date"],
                    _drp["end_date"],
                    _drp["forward_col"],
                    _drp["lookback"],
                    warmup=True,
                )
                _elapsed = time.perf_counter() - _t0
                if report is not None:
                    logger.info(
                        "ashare style warmup completed in %.1fs (%d symbols)",
                        _elapsed,
                        len(codes),
                    )
                # empty report already logged by the service (warmup branch)
            except Exception as exc:
                logger.warning("ashare style warmup failed: %s", exc, exc_info=True)

        if os.environ.get("SELENE_SKIP_WARMUP", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            logger.info("ashare style warmup skipped (SELENE_SKIP_WARMUP)")
        else:
            _ashare_task = asyncio.create_task(
                _ashare_style_warmup(), name="ashare-style-warmup"
            )
            state["_ashare_warmup_task"] = _ashare_task
            logger.info("ashare style warmup scheduled")
    except Exception:
        logger.warning("Failed to start ashare style warmup", exc_info=True)

    # Sync locally-created objects into state for shutdown consumption.
    # Keys absent here (e.g. on early exception) remain at their pre-seeded
    # None sentinels set in lifespan().
    for _k in (
        "paper_engine",
        "paper_state",
        "_autosave_task",
        "_kline_sync_task",
        "strategy_runner_instance",
        "mcp_registry_instance",
        "agent_runtime_instance",
        "serenity_fetcher",
        "_binance_user_data_clients",
        "risk_engine_instance",
        "market_svc",
        "funding_fetcher",
        "liquidation_fetcher",
        "options_fetcher",
        "dex_fetcher",
        "onchain_fetcher",
        "db_manager",
    ):
        if _k in locals():
            state[_k] = locals()[_k]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: startup and shutdown hooks."""
    _init_sys_path()

    # Wire the per-channel sequence manager (protocol §3.2). The process-level
    # session_id changes on restart so clients detect a "rectified rebuild".
    import uuid

    from app.core.sequence import ChannelSequenceManager, set_channel_sequence

    set_channel_sequence(ChannelSequenceManager(session_id=str(uuid.uuid4())))

    _state: dict = {}
    _init_crypto_and_settings(_state)

    await _init_storage(_state)

    _init_market_data(_state)

    # Pre-init sentinels for shutdown safety (keys absent in _state until init runs)
    _state.setdefault("paper_engine", None)
    _state.setdefault("paper_state", None)
    _state.setdefault("_autosave_task", None)
    _state.setdefault("_kline_sync_task", None)
    _state.setdefault("_options_poll_task", None)
    _state.setdefault("_subscribe_task", None)  # 默认 symbol 后台订阅(末尾追加)
    _state.setdefault("_news_ingest_tasks", None)  # news-ingest 哨兵(末尾追加)
    _state.setdefault("_fund_ingest_alert_task", None)  # fund-ingest 哨兵(末尾追加)
    _state.setdefault("_pa_trade_alert_task", None)  # PA 到价提醒哨兵(末尾追加)
    _state.setdefault("_ashare_warmup_task", None)  # ashare 风格预热哨兵(末尾追加)
    _state.setdefault("strategy_runner_instance", None)
    _state.setdefault("mcp_registry_instance", None)
    _state.setdefault("agent_runtime_instance", None)
    _state.setdefault("serenity_fetcher", None)
    _state.setdefault("_binance_user_data_clients", [])
    _state.setdefault("risk_engine_instance", None)
    _state.setdefault("_kline_sweep_task", None)  # kline gap sweeper 哨兵(末尾追加)
    _state.setdefault("kline_manager", None)  # kline gap sweeper 依赖(末尾追加)
    _state.setdefault("kline_quality_checker", None)  # kline quality 哨兵(末尾追加)
    _state.setdefault("kline_quality_snapshot", {})  # kline health 快照(末尾追加)
    _state.setdefault("astock_anomaly_detector", None)  # A股异动检测器哨兵(末尾追加)
    _state.setdefault("_astock_daily_sync_task", None)  # A股日线刷新哨兵(末尾追加)
    _state.setdefault("astock_snapshot_source", None)  # 全市场快照源哨兵(末尾追加)
    _state.setdefault(
        "_astock_snapshot_seed_task", None
    )  # 快照universe播种哨兵(末尾追加)

    try:
        await _init_engines_and_runtime(_state)

        # MCP 工具注册(Phase 0):catalog 此时已由 _init_engines_and_runtime 填好。
        # 只读子集暴露给外部 MCP 客户端;失败降级不崩(后端照常起)。
        _mcp_lifespan_cm = None
        try:
            from app.core.dependencies import get_tool_catalog
            from selene_space_agent.mcp import server as _mcp_server

            _catalog = get_tool_catalog()
            if _catalog is not None:
                _n = _mcp_server.register_tools(_catalog)
                logger.info(
                    "MCP: %d read-only tools exposed at %s", _n, _mcp_server.MOUNT_PATH
                )
                # FastMCP 的 session manager 要求其 lifespan 被执行,否则每个请求
                # 500(Task group is not initialized)。父 app 已有自己的 lifespan,
                # 故必须在此嵌套链上它。
                if _mcp_server.MCP_SUBAPP is not None:
                    _mcp_lifespan_cm = _mcp_server.MCP_SUBAPP.lifespan(app)
                    await _mcp_lifespan_cm.__aenter__()
        except Exception:
            logger.warning("MCP tool registration skipped", exc_info=True)
            _mcp_lifespan_cm = None

        try:
            yield
        finally:
            if _mcp_lifespan_cm is not None:
                try:
                    await _mcp_lifespan_cm.__aexit__(None, None, None)
                except Exception:
                    logger.debug("MCP lifespan teardown failed", exc_info=True)

    finally:
        await _shutdown_lifespan(_state)


async def _deploy_strategy_callback(strategy_id: str, account_id: str) -> str:
    """Callback for StrategyDeployTool: deploy strategy to paper runner.

    The StrategyRunner handles account creation, rule validation,
    duplicate prevention, and historical kline warmup internally.
    """
    from app.api.v1.strategies import (
        _get_strategy,
        _make_deployment,
        _persist_save,
        _save_deployment,
        _strategies,
        _strategy_deployments,
    )
    from app.core.dependencies import get_strategy_runner
    from app.api.v1.events import record_event

    config = await _get_strategy(strategy_id, user_id="local")
    if config is None:
        return f"Strategy '{strategy_id}' not found"

    has_rules = bool(config.entry_rules) or bool(config.exit_rules)
    if not has_rules:
        return (
            "ERROR: Cannot deploy strategy — entry_rules and exit_rules are empty. "
            "Save the strategy with complete rules first using save_strategy_version."
        )

    existing_running = next(
        (
            d
            for d in _strategy_deployments
            if d.strategy_id == strategy_id and d.status == "running"
        ),
        None,
    )
    if existing_running:
        return (
            f"Strategy '{config.name}' is already deployed (deployment: "
            f"{existing_running.id}, account: {existing_running.account_id})"
        )

    runner = get_strategy_runner()
    if runner is None:
        return "Strategy runner not available"

    deployment_id = f"dep-{strategy_id[:8]}-{int(time.time())}"
    err = await runner.deploy(
        deployment_id=deployment_id,
        strategy_config=config.model_dump(),
        account_id=account_id,
    )
    if err:
        return f"Deploy failed: {err}"

    slot_status = runner.get_slot_status(deployment_id)
    actual_account_id = slot_status["account_id"] if slot_status else account_id

    try:
        # Canonical constructor: it supplies the required mode/created_at. A
        # bare StrategyDeployment(...) omits both and raises ValidationError,
        # which the except below would swallow — silently skipping the persist.
        # The callback drives the paper runner (see docstring), so mode="paper".
        dep = _make_deployment(
            deployment_id, strategy_id, config.version, actual_account_id, "paper"
        )
        await _save_deployment(dep)
        updated = config.model_copy(update={"lifecycle_state": "running"})
        _strategies[strategy_id] = updated
        await _persist_save(updated, user_id="local")
    except Exception:
        logger.warning(
            "Post-deploy bookkeeping failed for %s (deployment still running)",
            deployment_id,
            exc_info=True,
        )

    bar_count = slot_status.get("bar_count", 0) if slot_status else 0

    try:
        from selene_space_agent.memory.decision_outcomes import DecisionOutcomeMemory

        DecisionOutcomeMemory().record_pending(
            deployment_id=deployment_id,
            strategy_id=strategy_id,
            symbols=list(config.symbols),
            entry_date=time.strftime("%Y-%m-%d"),
            decision_text=(
                f"Deployed '{config.name}' ({strategy_id}) on {config.symbols[0]} "
                f"{config.interval}, dedicated account {actual_account_id}."
            ),
        )
    except Exception:
        logger.debug("decision outcome pending record failed", exc_info=True)

    # P0-3:让 AI 的写操作在事件流里留下痕迹。
    await record_event(
        kind="ai_action",
        title=f"AI 部署策略 {config.name}",
        body=f"{config.symbols[0]} {config.interval} → 账户 {actual_account_id},"
        f"预热 {bar_count} 根K线",
        source="agent",
        symbol=config.symbols[0] if config.symbols else None,
        payload={
            "strategy_id": strategy_id,
            "account_id": actual_account_id,
            "deployment_id": deployment_id,
        },
    )

    return (
        f"Strategy '{config.name}' deployed to dedicated account {actual_account_id}. "
        f"It will now automatically trade based on live {config.symbols[0]} "
        f"{config.interval} klines. "
        f"Historical warmup: {bar_count} bars preloaded."
    )


async def _stop_strategy_callback(strategy_id: str) -> str:
    """Callback for StrategyStopTool: stop running deployments for a strategy."""
    from app.api.v1.strategies import _strategy_deployments
    from app.core.dependencies import get_strategy_runner
    from app.api.v1.events import record_event

    runner = get_strategy_runner()
    if runner is None:
        return "Strategy runner not available"

    stopped = 0
    for dep_id in list(runner.active_deployments):
        slot = runner.get_slot_status(dep_id)
        if slot and slot.get("strategy_id") == strategy_id:
            # Use async variant to cancel + await in-flight slot tasks (H12).
            await runner.stop_deployment_async(dep_id)
            stopped += 1

    for dep in _strategy_deployments:
        if dep.strategy_id == strategy_id and dep.status == "running":
            dep.status = "stopped"

    if stopped == 0:
        return f"No active deployments found for strategy {strategy_id}"

    # P0-3:让 AI 的写操作在事件流里留下痕迹。
    await record_event(
        kind="ai_action",
        title=f"AI 停止策略部署 ×{stopped}",
        body=f"策略 {strategy_id} 的 {stopped} 个运行中部属已停止",
        source="agent",
        severity="warn",
        payload={"strategy_id": strategy_id, "stopped": stopped},
    )
    return f"Stopped {stopped} deployment(s) for strategy {strategy_id}"


async def _save_strategy_version_callback(
    strategy_id: str,
    config: dict,
    changelog: str,
) -> str:
    """Callback for StrategySaveVersionTool: update strategy & create version snapshot."""
    from app.api.v1.strategies import (
        _get_strategy,
        _persist_save,
        _save_version_snapshot,
        _strategies,
    )
    from app.api.v1.events import record_event

    logger.info(
        "save_strategy_version_callback invoked: strategy_id=%s, config_keys=%s",
        strategy_id,
        list(config.keys()) if config else "NONE",
    )

    existing = await _get_strategy(strategy_id)
    if existing is None:
        return f"Strategy '{strategy_id}' not found"

    if not config or not isinstance(config, dict):
        return (
            "ERROR: 'config' parameter is empty or invalid. "
            "You MUST provide a complete config dict with entry_rules, "
            "exit_rules, risk_params, name, symbols, interval, etc."
        )

    _PARSED_TO_RAW = {
        "parsed_entry_rules": "entry_rules",
        "parsed_exit_rules": "exit_rules",
        "parsed_risk_params": "risk_params",
    }
    for parsed_key, raw_key in _PARSED_TO_RAW.items():
        parsed_val = config.pop(parsed_key, None)
        if parsed_val is None:
            continue
        if hasattr(parsed_val, "model_dump"):
            parsed_val = parsed_val.model_dump()
        raw_val = config.get(raw_key)
        raw_is_empty = raw_val is None or raw_val == {} or raw_val == []
        if raw_is_empty and parsed_val:
            config[raw_key] = parsed_val

    known_fields = set(existing.model_fields.keys())
    update_fields = {
        k: v for k, v in config.items() if v is not None and k in known_fields
    }

    entry = update_fields.get("entry_rules") or existing.entry_rules
    exit_ = update_fields.get("exit_rules") or existing.exit_rules
    risk = update_fields.get("risk_params") or existing.risk_params
    has_rules = bool(entry) or bool(exit_) or bool(risk)

    if not has_rules:
        return (
            "ERROR: Cannot save strategy — entry_rules, exit_rules, and "
            "risk_params are ALL empty. You MUST provide at least entry_rules "
            "and exit_rules with actual strategy logic (conditions, indicators, "
            "operators). Call save_strategy_version again with the COMPLETE "
            "config including the rules you generated."
        )

    new_version = existing.version + 1
    update_fields["version"] = new_version
    updated = existing.model_copy(update=update_fields)
    _strategies[strategy_id] = updated
    await _persist_save(updated)
    await _save_version_snapshot(updated, changelog or "AI-generated update")

    logger.info(
        "save_strategy_version_callback done: name=%s, version=%d, entry_rules=%s, exit_rules=%s, risk_params=%s",
        updated.name,
        new_version,
        bool(updated.entry_rules),
        bool(updated.exit_rules),
        bool(updated.risk_params),
    )

    # P0-3:让 AI 的写操作在事件流里留下痕迹(此前 AI 回调不写事件流)。
    await record_event(
        kind="ai_action",
        title=f"AI 保存策略版本 v{new_version}",
        body=f"{updated.name} — 规则:entry={bool(updated.entry_rules)}, "
        f"exit={bool(updated.exit_rules)}, risk={bool(updated.risk_params)}",
        source="agent",
        payload={"strategy_id": strategy_id, "version": new_version},
    )

    return (
        f"Strategy '{updated.name}' saved as version {new_version} "
        f"(ID: {strategy_id}). Rules: entry={bool(updated.entry_rules)}, "
        f"exit={bool(updated.exit_rules)}, risk={bool(updated.risk_params)}"
    )


def _register_agent_tools(
    catalog: object,
    *,
    market_service: object | None = None,
    account_manager: object | None = None,
    order_manager: object | None = None,
    position_manager: object | None = None,
    risk_engine: object | None = None,
) -> None:
    """Populate a ToolCatalog with all available agent tools."""
    from selene_space_agent import ToolCatalog

    tc: ToolCatalog = catalog  # type: ignore[assignment]

    if market_service is not None:
        from selene_space_agent.tools.market_tools import (
            MarketKlineTool,
            MarketOrderbookTool,
            MarketSnapshotTool,
            MarketTickerTool,
        )

        tc.register(MarketTickerTool(market_service))
        tc.register(MarketKlineTool(market_service))
        tc.register(MarketOrderbookTool(market_service))

    # A1 restored (979f564 lost it in the 14:42 revert) — snapshot reads
    # klines.db directly, independent of the live market service.
    try:
        from selene_space_agent.tools.market_tools import MarketSnapshotTool

        tc.register(MarketSnapshotTool(str(default_klines_db_path())))
    except ImportError as e:
        logger.warning("MarketSnapshotTool registration skipped: %s", e)

    if account_manager is not None:
        from selene_space_agent.tools.paper_tools import PaperCreateAccountTool

        tc.register(PaperCreateAccountTool(account_manager))

    if order_manager is not None:
        from selene_space_agent.tools.paper_tools import PaperPlaceOrderTool
        from selene_space_security.guardrails.overtrading import OvertradingDetector

        _overtrading_detector = OvertradingDetector()
        tc.register(
            PaperPlaceOrderTool(
                order_manager, overtrading_detector=_overtrading_detector
            )
        )

    if position_manager is not None:
        from selene_space_agent.tools.paper_tools import PaperPositionsTool

        tc.register(PaperPositionsTool(position_manager))

    if position_manager is not None and market_service is not None:
        from selene_space_agent.tools.paper_tools import PaperPnlTool

        tc.register(PaperPnlTool(position_manager, market_service))

        from selene_space_agent.tools.explain_tools import ExplainPnlTool

        tc.register(ExplainPnlTool(position_manager, market_service))

    # Strategy validate tool has no external dependencies
    try:
        from selene_space_agent.tools.strategy_tools import (
            BacktestResultTool,
            BacktestRunTool,
            StrategyDeployTool,
            StrategyExplainTool,
            StrategyGenerateTool,
            StrategyGetContractTool,
            StrategySaveVersionTool,
            StrategyStopTool,
            StrategyValidateTool,
        )

        tc.register(StrategyGetContractTool())
        tc.register(StrategyValidateTool())
        tc.register(BacktestResultTool())
        tc.register(StrategyGenerateTool())
        tc.register(StrategyExplainTool())
        tc.register(StrategySaveVersionTool(save_fn=_save_strategy_version_callback))
        tc.register(StrategyDeployTool(deploy_fn=_deploy_strategy_callback))
        tc.register(StrategyStopTool(stop_fn=_stop_strategy_callback))

        # BacktestRunTool requires a BacktestEngine instance
        try:
            from selene_space_backtest.engine import BacktestEngine

            bt_engine = BacktestEngine()
            # Optional: pass backtest repo for persistence
            backtest_repo = None
            db = None
            try:
                from app.core.dependencies import get_db_manager

                db = get_db_manager()
                if db is not None:
                    from selene_space_storage.repositories.backtests import (
                        BacktestRepository,
                    )

                    backtest_repo = BacktestRepository(db)
            except Exception:
                logger.debug(
                    "Optional backtest repository not wired for BacktestRunTool",
                    exc_info=True,
                )
            tc.register(
                BacktestRunTool(
                    bt_engine,
                    backtest_repo=backtest_repo,
                    market_service=market_service,
                )
            )
        except ImportError:
            logger.debug(
                "BacktestRunTool not available (missing selene_space_backtest)"
            )
    except ImportError:
        logger.debug("Strategy tools not available (missing selene_space_strategy)")

    if risk_engine is not None:
        from selene_space_agent.tools.risk_tools import RiskCheckTool, RiskReportTool

        tc.register(RiskCheckTool(risk_engine))
        tc.register(RiskReportTool(risk_engine))

    # TG signal lookup tool — reads tg_events from EventGuard DB.
    # Path comes from SELENE_TG_MACRO_DB env or defaults to data/macro_events.db.
    try:
        from selene_space_agent.tools.tg_tools import (
            TGEventImpactTool,
            TGSignalLookupTool,
        )

        tg_db_path = os.environ.get("SELENE_TG_MACRO_DB", "data/macro_events.db")
        tc.register(TGSignalLookupTool(db_path=tg_db_path))
        if position_manager is not None:
            tc.register(
                TGEventImpactTool(
                    db_path=tg_db_path,
                    position_manager=position_manager,
                    market_service=market_service,
                )
            )
    except ImportError:
        logger.debug(
            "TGSignalLookupTool not available (missing selene_space_alpha.event_guard)"
        )

    # Event-Kline attribution tool — historical (event_type, alert_level) × forward-return stats.
    # Read-only: queries tg_events + klines.db, independent try block so ImportError
    # doesn't break TG tools above.
    try:
        from selene_space_agent.tools.event_attribution import EventKlineAttributionTool

        macro_db_path = os.environ.get("SELENE_TG_MACRO_DB", "data/macro_events.db")
        klines_db_path = str(default_klines_db_path())
        tc.register(
            EventKlineAttributionTool(
                macro_db_path=macro_db_path,
                klines_db_path=klines_db_path,
            )
        )
    except ImportError as e:
        logger.warning("EventKlineAttributionTool registration skipped: %s", e)

    # Risk pressure check tool — aggregates macro/vix/tg alerts from EventGuard.
    # Read-only: queries get_active_alerts + get_vix_regime only, does NOT call
    # get_position_multiplier (that's the live strategy_runner path, separate concern).
    try:
        from selene_space_alpha.event_guard import EventGuard
        from selene_space_agent.tools.risk_tools import RiskPressureCheckTool

        tg_db_path = os.environ.get("SELENE_TG_MACRO_DB", "data/macro_events.db")
        event_guard = EventGuard(
            macro_db_path=tg_db_path,
            enable_vix=True,
            enable_tg=True,
        )
        tc.register(RiskPressureCheckTool(event_guard))
    except ImportError:
        logger.debug(
            "RiskPressureCheckTool not available (missing selene_space_alpha.event_guard)"
        )

    # PTC tool — agent writes Python to analyze local data (klines.db / storage DB)
    # in a restricted subprocess. Deny-by-default import whitelist + read-only path
    # guards; mistake-prevention, not an adversarial sandbox (see tool docstring).
    try:
        from selene_space_agent.tools.code_exec import PythonExecTool
        from selene_space_storage.sqlite import DEFAULT_DB_PATH

        klines_db_path = str(default_klines_db_path())
        tc.register(
            PythonExecTool(
                klines_db_path=klines_db_path,
                storage_db_path=str(DEFAULT_DB_PATH),
            )
        )
    except ImportError:
        logger.debug(
            "PythonExecTool not available (missing selene_space_agent.tools.code_exec)"
        )

    # read_skill — fetch half of lazy skill loading. Lazy skills (frontmatter
    # lazy_load: true) show only their description in the system prompt; the
    # agent pulls the methodology body here when it actually needs it.
    try:
        from app.core.dependencies import get_skill_registry
        from selene_space_agent.tools.read_skill import ReadSkillTool

        tc.register(ReadSkillTool(get_skill_registry))
    except ImportError:
        logger.debug(
            "ReadSkillTool not available (missing selene_space_agent.tools.read_skill)"
        )


async def _build_agent_runtime(
    settings_service: object,
    tool_catalog: Any | None = None,
    *,
    user_id: str | None = None,
) -> object | None:
    """Build an AgentRuntime from persisted LLM settings, or return None."""
    from app.core.settings_service import SettingsService
    from selene_space_security.secrets import (
        SecretManager,
        SecretRef,
        SecretResolutionError,
        SecretSource,
    )

    svc: SettingsService = settings_service  # type: ignore[assignment]
    settings = svc._load_non_sensitive(user_id=user_id)
    llm_section = settings.get("llm", {})

    sm = SecretManager()
    llm_prov = svc._kr_provider("selenespace.llm", user_id)
    smart_prov = svc._kr_provider("selenespace.llm.smart", user_id)
    net_prov = svc._kr_provider("selenespace.network", user_id)

    # Try keyring first, fall back to .env environment variables
    ref = SecretRef(source=SecretSource.KEYRING, provider=llm_prov, id="api_key")
    api_key = None
    try:
        resolved = await sm.resolve(ref)
        api_key = resolved.use()
    except SecretResolutionError:
        # Fallback to .env
        api_key = os.environ.get("SELENE_LLM_API_KEY")
        if api_key:
            logger.info("Using LLM API key from .env fallback (keyring not configured)")

    if not api_key:
        logger.warning(
            "No LLM API key found in keyring or .env, agent runtime disabled"
        )
        return None

    _keyring_fields = {"base_url": "", "model": "", "provider": ""}
    for field in _keyring_fields:
        try:
            r = await sm.resolve(
                SecretRef(source=SecretSource.KEYRING, provider=llm_prov, id=field)
            )
            _keyring_fields[field] = r.use() or ""
        except SecretResolutionError:
            # Fallback to .env
            env_key = f"SELENE_LLM_{field.upper()}"
            env_val = os.environ.get(env_key)
            if env_val:
                _keyring_fields[field] = env_val
                logger.debug("Using LLM %s from .env fallback", field)

    llm_section["base_url"] = (
        _keyring_fields["base_url"]
        or llm_section.get("base_url", "")
        or os.environ.get("SELENE_LLM_BASE_URL", "")
    )
    llm_section["model"] = (
        _keyring_fields["model"]
        or llm_section.get("model", "")
        or os.environ.get("SELENE_LLM_MODEL", "")
    )
    llm_section["provider"] = (
        _keyring_fields["provider"]
        or llm_section.get("provider", "")
        or os.environ.get("SELENE_LLM_PROVIDER", "")
    )

    _smart_fields = ("strategy", "analysis", "quick")
    smart_models_kr: dict[str, str] = {}
    for sf in _smart_fields:
        try:
            r = await sm.resolve(
                SecretRef(source=SecretSource.KEYRING, provider=smart_prov, id=sf)
            )
            val = r.use() or ""
            if val:
                smart_models_kr[sf] = val
        except SecretResolutionError:
            pass
    if smart_models_kr:
        existing = llm_section.get("smart_models")
        if isinstance(existing, dict):
            existing.update(smart_models_kr)
        else:
            llm_section["smart_models"] = smart_models_kr

    proxy_url = ""
    try:
        r = await sm.resolve(
            SecretRef(source=SecretSource.KEYRING, provider=net_prov, id="proxy_url")
        )
        proxy_url = r.use() or ""
    except SecretResolutionError:
        pass

    from selene_space_agent import AgentRuntime, ToolCatalog
    from selene_space_agent.context.manager import ContextManager
    from selene_space_agent.prompt_builder import AgentContext
    from selene_space_llm.base import LLMConfig
    from selene_space_llm.openai_compat import OpenAICompatProvider

    smart_mode = llm_section.get("smart_mode", False)
    if isinstance(smart_mode, str):
        smart_mode = smart_mode.lower() == "true"

    default_model = llm_section.get("model") or ""
    if smart_mode:
        smart_models = llm_section.get("smart_models")
        if isinstance(smart_models, dict):
            model = smart_models.get("analysis", default_model)
        else:
            model = default_model
    else:
        model = default_model

    logger.info(
        "Building agent runtime: model=%s, base_url=%s",
        model,
        llm_section.get("base_url", "")[:40],
    )

    config = LLMConfig(
        model=model,
        api_key=api_key,
        base_url=llm_section.get("base_url") or None,
    )

    proxy_url = proxy_url or settings.get("network", {}).get("proxy_url", "")
    transport = None
    if proxy_url:
        import httpx

        transport = httpx.AsyncHTTPTransport(proxy=proxy_url)

    # Load LLM timeout from environment (default 120 seconds)
    llm_timeout = float(os.getenv("SELENE_LLM_TIMEOUT_SECONDS", "120"))

    client = None
    if transport:
        import httpx

        client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=llm_timeout, write=10.0, pool=10.0),
        )

    llm_provider = OpenAICompatProvider(config, timeout=llm_timeout, client=client)

    if tool_catalog is None:
        tool_catalog = ToolCatalog()

    context_manager = ContextManager()
    prompt_context = AgentContext(
        available_tools=tool_catalog.get_tool_definitions(),  # type: ignore[union-attr]
    )

    # FX01: Inject skills prompt into agent context (fixed: pass skills list)
    try:
        from app.core.dependencies import get_skill_registry
        from selene_space_agent.skills.prompt import format_skills_for_prompt

        skill_registry = get_skill_registry()
        if skill_registry is not None:
            skills_list = skill_registry.list_skills()
            if skills_list:
                skills_text = format_skills_for_prompt(skills_list)
                if skills_text:
                    prompt_context.skills_prompt = skills_text
                    logger.info(
                        "Injected %d skills into agent context", len(skills_list)
                    )
    except Exception:
        logger.debug("Failed to load skills prompt", exc_info=True)

    memory_recall = None
    failure_memory = None
    try:
        from selene_space_agent.memory.strategy_failures import StrategyFailureMemory
        from selene_space_agent.memory.research_memory import ResearchFindingsMemory
        from selene_space_agent.memory.decision_outcomes import DecisionOutcomeMemory

        failure_memory = StrategyFailureMemory()
        research_recall = ResearchFindingsMemory().recall
        failure_recall = failure_memory.recall
        decision_recall = DecisionOutcomeMemory().recall

        def memory_recall(message: str) -> str:
            parts = []
            research = research_recall(message)
            if research:
                parts.append(f"[研究结论]\n{research}")
            failures = failure_recall(message)
            if failures:
                parts.append(f"[历史策略失败]\n{failures}")
            decisions = decision_recall(message)
            if decisions:
                parts.append(f"[历史决策教训]\n{decisions}")
            return "\n\n".join(parts)

        logger.info(
            "Research findings + strategy failure + decision outcome memory wired into agent runtime"
        )
    except Exception:
        logger.debug("Memory recall wiring unavailable", exc_info=True)

    if failure_memory is not None and tool_catalog is not None:
        validate_tool = tool_catalog.get("strategy_validate")
        if validate_tool is not None:
            setattr(validate_tool, "_failure_memory", failure_memory)

    return AgentRuntime(  # type: ignore[no-any-return]
        llm=llm_provider,
        tool_catalog=tool_catalog,  # type: ignore[arg-type]
        context_manager=context_manager,
        prompt_context=prompt_context,
        memory_recall=memory_recall,
    )


def _bridge_market_events(market_svc: object) -> None:
    """Bridge MarketDataService EventBus events to WebSocket broadcast.

    Orderbook snapshots are throttled to avoid saturating the event loop
    with expensive ``model_dump()`` serialisation of 2000-level books at
    ~120 updates/s.  Other event types are lightweight and forwarded
    immediately.
    """
    import asyncio
    import time as _time

    from app.api.v1.ws import _market_manager, broadcast_market_event
    from selene_space_types.derivatives import (
        FundingRateEvent,
        LargeOrderEvent,
        LargeTradeEvent,
        LiquidationEvent,
        LiquidationStats,
    )
    from selene_space_types.market import (
        AnomalyEvent,
        KlineEvent,
        OrderBookL2Snapshot,
        TickerEvent,
    )

    _ORDERBOOK_BROADCAST_INTERVAL = 0.25  # max 4 broadcasts/s per symbol
    _last_ob_broadcast: dict[str, float] = {}
    _pending_ob: dict[str, OrderBookL2Snapshot] = {}
    _ob_flush_running = False

    async def _flush_pending_orderbooks() -> None:
        """Periodically flush throttled orderbook snapshots."""
        nonlocal _ob_flush_running
        _ob_flush_running = True
        try:
            while True:
                await asyncio.sleep(_ORDERBOOK_BROADCAST_INTERVAL)
                if not _pending_ob:
                    continue
                if _market_manager.active_count == 0:
                    _pending_ob.clear()
                    continue
                batch = dict(_pending_ob)
                _pending_ob.clear()
                now = _time.monotonic()
                for key, snap in batch.items():
                    _last_ob_broadcast[key] = now
                    asyncio.ensure_future(
                        broadcast_market_event(
                            snap.symbol,
                            "depth",
                            snap.model_dump(mode="json"),
                        )
                    )
        except asyncio.CancelledError:
            pass
        finally:
            _ob_flush_running = False

    def _on_ticker(event: TickerEvent) -> None:
        # Phase 3: market data heartbeat — any ticker means WS is alive.
        _monitor = getattr(app.state, "ws_health_monitor", None)
        if _monitor is not None:
            _monitor.record_heartbeat()
        if _market_manager.active_count == 0:
            return
        asyncio.ensure_future(
            broadcast_market_event(
                event.symbol,
                "ticker",
                event.model_dump(mode="json"),
            )
        )

    def _on_kline(event: KlineEvent) -> None:
        _monitor = getattr(app.state, "ws_health_monitor", None)
        if _monitor is not None:
            _monitor.record_heartbeat()
        if _market_manager.active_count == 0:
            return
        # DEBUG: Log kline broadcast
        logger.info(
            "Broadcast kline: %s %s %s close=%s",
            event.symbol,
            event.interval,
            event.market_type,
            event.close,
        )
        asyncio.ensure_future(
            broadcast_market_event(
                event.symbol,
                "kline",
                event.model_dump(mode="json"),
            )
        )

    def _on_orderbook(event: OrderBookL2Snapshot) -> None:
        _monitor = getattr(app.state, "ws_health_monitor", None)
        if _monitor is not None:
            _monitor.record_heartbeat()
        nonlocal _ob_flush_running
        if _market_manager.active_count == 0:
            return
        key = f"{event.exchange}:{event.market_type}:{event.symbol}"
        _pending_ob[key] = event
        if not _ob_flush_running:
            asyncio.ensure_future(_flush_pending_orderbooks())

    # 无订阅者时直接丢弃:这些事件每秒可来上百条,而 model_dump() 是纯 CPU
    # 序列化,ensure_future() 又会建 Task —— 两者都在事件循环上跑,即使没人
    # 订阅也全速空转,把 HTTP 端点饿成秒级尾延迟(2026-10-13 实测 2–12s)。
    # 与 _on_ticker/_on_kline/_on_orderbook 的 active_count 守卫保持一致。
    async def _broadcast_model_event(event_type: str, symbol: str, event: Any) -> None:
        """序列化延到协程内做,避免堵住上游行情摄取路径。"""
        await broadcast_market_event(symbol, event_type, event.model_dump(mode="json"))

    def _on_large_trade(event: LargeTradeEvent) -> None:
        if _market_manager.active_count == 0:
            return
        asyncio.ensure_future(
            _broadcast_model_event("large_trade", event.symbol, event)
        )

    def _on_large_order(event: LargeOrderEvent) -> None:
        if _market_manager.active_count == 0:
            return
        asyncio.ensure_future(
            _broadcast_model_event("large_order", event.symbol, event)
        )

    def _on_liquidation(event: LiquidationEvent) -> None:
        if _market_manager.active_count == 0:
            return
        asyncio.ensure_future(
            _broadcast_model_event("liquidation", event.symbol, event)
        )

    def _on_liquidation_stats(event: LiquidationStats) -> None:
        if _market_manager.active_count == 0:
            return
        asyncio.ensure_future(_broadcast_model_event("liquidation_stats", "ALL", event))

    def _on_funding_rate(event: FundingRateEvent) -> None:
        if _market_manager.active_count == 0:
            return
        asyncio.ensure_future(
            _broadcast_model_event("funding_rate", event.symbol, event)
        )

    def _on_anomaly(event: AnomalyEvent) -> None:
        # A-share intraday anomalies (S3a). Not gated on active_count: the
        # feed bar is one consumer, but the same Channel is how an operator
        # first sees a limit-up, and broadcast is already a no-op with no
        # subscribers. market_type travels on the event so the channel matches
        # the source ("intraday"), not the bridge default.
        asyncio.ensure_future(
            broadcast_market_event(
                event.symbol,
                "anomaly",
                event.model_dump(mode="json"),
            )
        )

    from selene_space_market import MarketDataService as _MDS

    svc: _MDS = market_svc  # type: ignore[assignment]
    svc.on_ticker(_on_ticker)
    svc.on_kline(_on_kline)
    svc.on_orderbook(_on_orderbook)
    svc.on_large_trade(_on_large_trade)
    svc.on_large_order(_on_large_order)
    svc.on_liquidation(_on_liquidation)
    svc.on_liquidation_stats(_on_liquidation_stats)
    svc.on_funding_rate(_on_funding_rate)
    svc.on_anomaly(_on_anomaly)


def _bridge_price_to_paper(market_svc: object, paper_engine: object) -> None:
    """Forward real-time ticker prices to PaperExecutionEngine for fill simulation."""
    import asyncio

    from selene_space_market import MarketDataService as _MDS
    from selene_space_paper.paper_execution import PaperExecutionEngine as _PE
    from selene_space_types.market import TickerEvent

    svc: _MDS = market_svc  # type: ignore[assignment]
    engine: _PE = paper_engine  # type: ignore[assignment]

    def _on_ticker_for_paper(event: TickerEvent) -> None:
        asyncio.ensure_future(engine.on_price_tick(event.symbol, event.last_price))

    svc.on_ticker(_on_ticker_for_paper)


def _bridge_price_to_shadow(market_svc: object, shadow_engine: object) -> None:
    """Forward real-time ticker prices to ShadowExecutionEngine for fill simulation.

    Shadow engine mirrors LiveExecutionEngine logic without real API calls.
    Its ``on_price_tick`` drains pending market orders and triggers limit/stop
    orders against the real tick. Without this bridge, shadow market orders
    would never fill — defeating the dry-run validation purpose.
    """
    import asyncio

    from selene_space_exchange.execution.shadow_engine import (
        ShadowExecutionEngine as _SE,
    )
    from selene_space_market import MarketDataService as _MDS
    from selene_space_types.market import TickerEvent

    svc: _MDS = market_svc  # type: ignore[assignment]
    engine: _SE = shadow_engine  # type: ignore[assignment]

    def _on_ticker_for_shadow(event: TickerEvent) -> None:
        asyncio.ensure_future(engine.on_price_tick(event.symbol, event.last_price))

    svc.on_ticker(_on_ticker_for_shadow)


def _bridge_price_to_live(market_svc: object, live_engine: object) -> None:
    """Forward real-time ticker prices to LiveExecutionEngine for stop-trigger evaluation (Bug C).

    Live engine's ``on_price_tick`` evaluates active StopManager stops against
    the real tick and fires reduce_only MARKET close when breached. Without
    this bridge, local stop-loss is never evaluated — capital is unprotected
    except by KillSwitch 15% drawdown fallback.
    """
    import asyncio

    from selene_space_exchange.execution.live_engine import LiveExecutionEngine as _LE
    from selene_space_market import MarketDataService as _MDS
    from selene_space_types.market import TickerEvent

    svc: _MDS = market_svc  # type: ignore[assignment]
    engine: _LE = live_engine  # type: ignore[assignment]

    def _on_ticker_for_live(event: TickerEvent) -> None:
        asyncio.ensure_future(engine.on_price_tick(event.symbol, event.last_price))

    svc.on_ticker(_on_ticker_for_live)


def _resolve_exchange_env_overrides(
    saved_settings: dict,
    *,
    env: dict[str, str] | None = None,
) -> tuple[str, bool, str, str, str]:
    """Resolve exchange provider/testnet/credentials with env var fallback.

    Precedence: keyring (already resolved upstream and passed in via
    saved_settings/api_key/secret/passphrase) > env var > empty.

    Returns (provider, testnet_flag, api_key, api_secret, passphrase).
    Default testnet=True (safe) unless explicitly overridden to False.
    """
    import os

    env_map = env if env is not None else dict(os.environ)

    provider = str(saved_settings.get("exchange", {}).get("provider", "binance"))
    env_provider = env_map.get("SELENE_EXCHANGE_PROVIDER", "").strip().lower()
    if env_provider in ("binance", "okx"):
        provider = env_provider

    testnet_flag = bool(saved_settings.get("exchange", {}).get("testnet", True))
    env_testnet = env_map.get("SELENE_EXCHANGE_TESTNET", "").strip().lower()
    if env_testnet in ("0", "false", "no", "off"):
        testnet_flag = False
    elif env_testnet in ("1", "true", "yes", "on"):
        testnet_flag = True

    # Phase 6 Bug E fallback (commit e49bc09): SELENE_EXCHANGE_* → BINANCE_MAINNET_*
    # Workaround for .env.mainnet using BINANCE_MAINNET_* naming. Non-breaking —
    # SELENE_EXCHANGE_* takes precedence; falls back only when empty.
    # TECH DEBT (E-B-fallback-delete): delete this fallback after renaming
    # .env.mainnet vars to SELENE_EXCHANGE_*. See
    # scripts/issues/mainnet-retest-followup.md → Open Tech Debt table.
    # Removing the fallback without renaming .env.mainnet reintroduces 401.
    api_key = env_map.get("SELENE_EXCHANGE_API_KEY") or env_map.get(
        "BINANCE_MAINNET_API_KEY", ""
    )
    api_secret = env_map.get("SELENE_EXCHANGE_API_SECRET") or env_map.get(
        "BINANCE_MAINNET_API_SECRET", ""
    )
    passphrase = env_map.get("SELENE_EXCHANGE_PASSPHRASE") or env_map.get(
        "BINANCE_MAINNET_PASSPHRASE", ""
    )

    return provider, testnet_flag, api_key, api_secret, passphrase


def _bridge_execution_events(engine: Any) -> None:
    """Bridge ExecutionEngine callbacks to the trading WebSocket broadcast.

    Also writes audit events for every order/fill/position/balance update
    so there's a tamper-evident trail of all trading activity.
    """
    import asyncio

    from app.api.v1.ws import broadcast_trading_event
    from app.core.audit import audit_event
    from app.core.sequence import get_channel_sequence
    from selene_space_types.trading import BalanceUpdate, Fill, Order, Position

    seq_mgr = get_channel_sequence()
    session_id = seq_mgr.session_id if seq_mgr is not None else ""

    def _on_order(order: Order) -> None:
        seq = seq_mgr.next("local:orders") if seq_mgr is not None else None
        asyncio.ensure_future(
            broadcast_trading_event(
                "orders",
                "order_update",
                order.model_dump(mode="json"),
                seq=seq,
                session_id=session_id,
            )
        )
        asyncio.ensure_future(
            audit_event(
                event_type="trade.order_update",
                actor="execution_engine",
                action=f"{order.side.value}:{order.type.value}",
                resource=f"{order.symbol}:{order.id}",
                outcome="success",
                severity="info",
                details={
                    "status": order.status.value,
                    "quantity": order.quantity,
                    "filled_quantity": order.filled_quantity,
                    "avg_fill_price": order.avg_fill_price,
                    "exchange": getattr(engine, "exchange", "unknown"),
                    "sequence_id": seq,
                    "session_id": session_id,
                },
            )
        )

    def _on_fill(fill: Fill) -> None:
        seq = seq_mgr.next("local:orders") if seq_mgr is not None else None
        asyncio.ensure_future(
            broadcast_trading_event(
                "orders",
                "fill",
                fill.model_dump(mode="json"),
                seq=seq,
                session_id=session_id,
            )
        )
        asyncio.ensure_future(
            audit_event(
                event_type="trade.fill",
                actor="execution_engine",
                action="fill",
                resource=f"{fill.symbol}:{fill.order_id}",
                outcome="success",
                severity="info",
                details={
                    "price": fill.price,
                    "quantity": fill.quantity,
                    "fee": fill.fee,
                    "fee_currency": fill.fee_currency,
                    "exchange": getattr(engine, "exchange", "unknown"),
                    "sequence_id": seq,
                    "session_id": session_id,
                },
            )
        )
        # Large-fill trade alert — fire-and-forget, never blocks the bridge.
        try:
            notional = abs(fill.price) * abs(fill.quantity)
            if notional >= _LARGE_FILL_NOTIONAL_THRESHOLD:
                from app.core.notification_dispatch import dispatch

                dispatch(
                    "order_filled",
                    f"Large fill: {fill.symbol} "
                    f"px={fill.price} qty={fill.quantity} notional={notional:.2f}",
                )
        except Exception:
            logger.debug("Large-fill alert failed", exc_info=True)
        asyncio.ensure_future(_paper_snapshot_after_fill(engine, fill))

    def _on_position(pos: Position) -> None:
        seq = seq_mgr.next("local:positions") if seq_mgr is not None else None
        asyncio.ensure_future(
            broadcast_trading_event(
                "positions",
                "position_update",
                pos.model_dump(mode="json"),
                seq=seq,
                session_id=session_id,
            )
        )
        asyncio.ensure_future(
            audit_event(
                event_type="trade.position_update",
                actor="execution_engine",
                action="position_update",
                resource=f"{pos.symbol}:{pos.pos_side.value}",
                outcome="success",
                severity="info",
                details={
                    "quantity": pos.quantity,
                    "avg_entry_price": pos.avg_entry_price,
                    "unrealized_pnl": getattr(pos, "unrealized_pnl", None),
                    "exchange": getattr(engine, "exchange", "unknown"),
                    "sequence_id": seq,
                    "session_id": session_id,
                },
            )
        )

    def _on_balance(balances: list[BalanceUpdate]) -> None:
        seq = seq_mgr.next("local:balances") if seq_mgr is not None else None
        asyncio.ensure_future(
            broadcast_trading_event(
                "balances",
                "balance_update",
                [b.model_dump(mode="json") for b in balances],  # type: ignore[arg-type]
                seq=seq,
                session_id=session_id,
            )
        )
        for bal in balances:
            asyncio.ensure_future(
                audit_event(
                    event_type="trade.balance_update",
                    actor="execution_engine",
                    action="balance_update",
                    resource=bal.asset,
                    outcome="success",
                    severity="info",
                    details={
                        "free": bal.free,
                        "locked": bal.locked,
                        "exchange": getattr(engine, "exchange", "unknown"),
                        "sequence_id": seq,
                        "session_id": session_id,
                    },
                )
            )

    engine.on_order_update(_on_order)  # type: ignore[union-attr]
    engine.on_fill(_on_fill)  # type: ignore[union-attr]
    engine.on_position_update(_on_position)  # type: ignore[union-attr]
    engine.on_balance_update(_on_balance)  # type: ignore[union-attr]


def _bridge_paper_engine_events(engine: Any) -> None:
    """Bridge PaperExecutionEngine callbacks to the paper WebSocket channel.

    Broadcasts order_update, fill, position_update, balance_update, and
    account_snapshot events so the frontend can operate without polling.

    Also writes audit events (actor="paper_engine") for every order/fill/
    position/balance update so paper trading has the same tamper-evident
    trail as live execution.
    """
    import asyncio

    from app.api.v1.ws import broadcast_paper_event
    from app.core.audit import audit_event
    from app.core.sequence import get_channel_sequence
    from selene_space_types.trading import BalanceUpdate, Fill, Order, Position

    seq_mgr = get_channel_sequence()
    session_id = seq_mgr.session_id if seq_mgr is not None else ""

    def _resolve_account_for_order(order_id: str) -> str | None:
        try:
            return engine._get_order_account(order_id)  # type: ignore[no-any-return]
        except Exception:
            return None

    def _on_order(order: Order) -> None:
        aid = _resolve_account_for_order(order.id)
        # P1: next() must live in the same branch as the broadcast. When
        # aid is None there is no WS event, so no seq is burned.
        seq = None
        if aid:
            seq = seq_mgr.next(f"paper:{aid}") if seq_mgr is not None else None
            asyncio.ensure_future(
                broadcast_paper_event(
                    aid,
                    "order_update",
                    order.model_dump(mode="json"),
                    seq=seq,
                    session_id=session_id,
                )
            )
        asyncio.ensure_future(
            audit_event(
                event_type="paper.order_update",
                actor="paper_engine",
                action=f"{order.side.value}:{order.type.value}",
                resource=f"{order.symbol}:{order.id}",
                outcome="success",
                severity="info",
                details={
                    "account_id": aid,
                    "status": order.status.value,
                    "quantity": order.quantity,
                    "filled_quantity": order.filled_quantity,
                    "avg_fill_price": order.avg_fill_price,
                    "exchange": "paper",
                    "sequence_id": seq,
                    "session_id": session_id,
                },
            )
        )

    def _on_fill(fill: Fill) -> None:
        aid = _resolve_account_for_order(fill.order_id)
        # P1: next() lives inside `if aid:` — aid=None consumes no seq.
        seq = None
        if aid:
            seq = seq_mgr.next(f"paper:{aid}") if seq_mgr is not None else None
            asyncio.ensure_future(
                broadcast_paper_event(
                    aid,
                    "fill",
                    fill.model_dump(mode="json"),
                    seq=seq,
                    session_id=session_id,
                )
            )
            asyncio.ensure_future(_paper_snapshot_after_fill(engine, fill, aid))
        asyncio.ensure_future(
            audit_event(
                event_type="paper.fill",
                actor="paper_engine",
                action="fill",
                resource=f"{fill.symbol}:{fill.order_id}",
                outcome="success",
                severity="info",
                details={
                    "account_id": aid,
                    "price": fill.price,
                    "quantity": fill.quantity,
                    "fee": fill.fee,
                    "fee_currency": fill.fee_currency,
                    "exchange": "paper",
                    "sequence_id": seq,
                    "session_id": session_id,
                },
            )
        )

    def _on_position_scoped(account_id: str, pos: Position) -> None:
        seq = seq_mgr.next(f"paper:{account_id}") if seq_mgr is not None else None
        asyncio.ensure_future(
            broadcast_paper_event(
                account_id,
                "position_update",
                pos.model_dump(mode="json"),
                seq=seq,
                session_id=session_id,
            )
        )
        asyncio.ensure_future(
            audit_event(
                event_type="paper.position_update",
                actor="paper_engine",
                action="position_update",
                resource=f"{pos.symbol}:{pos.pos_side.value}",
                outcome="success",
                severity="info",
                details={
                    "account_id": account_id,
                    "quantity": pos.quantity,
                    "avg_entry_price": pos.avg_entry_price,
                    "unrealized_pnl": getattr(pos, "unrealized_pnl", None),
                    "exchange": "paper",
                    "sequence_id": seq,
                    "session_id": session_id,
                },
            )
        )

    def _on_balance_scoped(account_id: str, balances: list[BalanceUpdate]) -> None:
        seq = seq_mgr.next(f"paper:{account_id}") if seq_mgr is not None else None
        asyncio.ensure_future(
            broadcast_paper_event(
                account_id,
                "balance_update",
                [b.model_dump(mode="json") for b in balances],  # type: ignore[arg-type]
                seq=seq,
                session_id=session_id,
            )
        )
        for bal in balances:
            asyncio.ensure_future(
                audit_event(
                    event_type="paper.balance_update",
                    actor="paper_engine",
                    action="balance_update",
                    resource=bal.asset,
                    outcome="success",
                    severity="info",
                    details={
                        "account_id": account_id,
                        "free": bal.free,
                        "locked": bal.locked,
                        "exchange": "paper",
                        "sequence_id": seq,
                        "session_id": session_id,
                    },
                )
            )

    engine.on_order_update(_on_order)  # type: ignore[union-attr]
    engine.on_fill(_on_fill)  # type: ignore[union-attr]
    engine.on_position_update_scoped(_on_position_scoped)  # type: ignore[union-attr]
    engine.on_balance_update_scoped(_on_balance_scoped)  # type: ignore[union-attr]


def _bridge_strategy_runner_signals(runner: object) -> None:
    """Bridge StrategyRunner signal events to the strategy WebSocket broadcast.

    This enables the frontend to receive real-time strategy signals when
    deployed strategies emit buy/sell signals based on live market data.
    """
    import asyncio

    from app.api.v1.ws import broadcast_strategy_signal

    def _on_strategy_signal(deployment_id: str, signal: object) -> None:
        """Handle strategy signal from StrategyRunner and broadcast to WS clients."""
        try:
            # Extract signal details
            signal_dict = (
                signal.model_dump(mode="json") if hasattr(signal, "model_dump") else {}
            )
            side = signal_dict.get("side", "unknown")
            if hasattr(side, "value"):
                side = side.value

            signal_data = {
                "deployment_id": deployment_id,
                "strategy_id": signal_dict.get("strategy_id", ""),
                "symbol": signal_dict.get("symbol", ""),
                "side": side,
                "strength": signal_dict.get("strength", 1.0),
                "reason": signal_dict.get("reason", ""),
                "timestamp": signal_dict.get(
                    "timestamp", int(__import__("time").time() * 1000)
                ),
            }

            asyncio.ensure_future(broadcast_strategy_signal(signal_data))
            logger.info(
                "Broadcast strategy signal: strategy=%s symbol=%s side=%s reason=%s",
                signal_data["strategy_id"],
                signal_data["symbol"],
                side,
                signal_data["reason"],
            )
        except Exception:
            logger.warning("Failed to broadcast strategy signal", exc_info=True)

    # Register callback on StrategyRunner
    if hasattr(runner, "on_signal"):
        runner.on_signal(_on_strategy_signal)
    else:
        logger.debug("StrategyRunner does not support on_signal callback")


async def _paper_snapshot_after_fill(
    engine: object, fill: object, account_id: str | None = None
) -> None:
    """After a fill, broadcast account_snapshot only to the affected account."""
    try:
        from app.api.v1.paper import _record_equity
        from app.api.v1.ws import broadcast_paper_event
        from app.core.dependencies import (
            get_paper_account_manager,
            get_paper_position_manager,
        )
        from app.core.sequence import get_channel_sequence

        acct_mgr = get_paper_account_manager()
        pos_mgr = get_paper_position_manager()
        if not acct_mgr or not pos_mgr:
            return

        seq_mgr = get_channel_sequence()
        session_id = seq_mgr.session_id if seq_mgr is not None else ""

        if account_id:
            target_accounts = [acct_mgr.get_account(account_id)]
            target_accounts = [a for a in target_accounts if a is not None]
        else:
            target_accounts = acct_mgr.list_accounts()

        for account in target_accounts:
            unrealized = sum(
                p.unrealized_pnl for p in pos_mgr.get_open_positions(account.id)
            )
            wallet_bal = (
                float(account.initial_balance)
                + float(account.total_realized_pnl)
                - float(account.total_fee)
            )
            equity = wallet_bal + unrealized

            snapshot = account.model_dump(mode="json")
            # H19: funding fields are Decimal — coerce to float for JSON broadcast.
            for _k in (
                "initial_balance",
                "current_balance",
                "equity",
                "total_realized_pnl",
                "total_fee",
            ):
                if _k in snapshot and snapshot[_k] is not None:
                    snapshot[_k] = float(snapshot[_k])
            snapshot["equity"] = equity
            snapshot["balance"] = float(account.current_balance)
            snapshot["unrealized_pnl"] = unrealized
            snapshot["realized_pnl"] = float(account.total_realized_pnl)

            positions = [
                p.model_dump(mode="json")
                for p in pos_mgr.get_open_positions(account.id)
            ]
            snapshot["positions"] = positions

            # P2: each WS event takes its OWN seq — never reuse the fill's seq
            # (otherwise every fill would create a paper-channel fake gap).
            seq_snap = (
                seq_mgr.next(f"paper:{account.id}") if seq_mgr is not None else None
            )
            await broadcast_paper_event(
                account.id,
                "account_snapshot",
                snapshot,
                seq=seq_snap,
                session_id=session_id,
            )

            try:
                await _record_equity(account.id, equity)
                # Critic §6.1: the equity_point seq is taken INSIDE this try,
                # right before its broadcast — a _record_equity failure must
                # not burn a seq (that would create a paper-channel fake gap).
                seq_eq = (
                    seq_mgr.next(f"paper:{account.id}") if seq_mgr is not None else None
                )
                await broadcast_paper_event(
                    account.id,
                    "equity_point",
                    {
                        "timestamp": int(__import__("time").time() * 1000),
                        "equity": equity,
                    },
                    seq=seq_eq,
                    session_id=session_id,
                )
            except Exception:
                logger.debug(
                    "Equity record/broadcast failed for %s", account.id, exc_info=True
                )
    except Exception:
        logger.debug("Paper snapshot after fill failed", exc_info=True)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    import os

    is_production = os.environ.get("SELENE_ENV") == "production"

    # C2 fix: triple-signal production guard. Refuse to start if
    # SELENE_AUTH_DISABLED=1 is set AND any of:
    #   - SELENE_ENV == "production"
    #   - SELENE_DEPLOY == "prod"
    #   - non-localhost bind (SELENE_HOST not in 127.0.0.1/localhost/::1)
    # The bypass is for localhost dev only; production deploys must never
    # have it on. Missing-signal case is caught by the bind check — if the
    # server is bound to a non-loopback interface, it's exposed.
    _auth_disabled = os.environ.get("SELENE_AUTH_DISABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if _auth_disabled:
        _env_prod = os.environ.get("SELENE_ENV", "").strip().lower() == "production"
        _deploy_prod = os.environ.get("SELENE_DEPLOY", "").strip().lower() == "prod"
        _bind_host = os.environ.get("SELENE_HOST", os.environ.get("HOST", "127.0.0.1"))
        _non_localhost = _bind_host not in ("127.0.0.1", "localhost", "::1", "")
        if _env_prod or _deploy_prod or _non_localhost:
            raise RuntimeError(
                "Refusing to start: SELENE_AUTH_DISABLED=1 is set but this looks like a "
                "production / non-localhost deploy. Set SELENE_AUTH_DISABLED=0 (or unset) "
                "for production. Signals: SELENE_ENV=production={}, SELENE_DEPLOY=prod={}, "
                "bind_host={} (non-localhost={}). The audited bypass is for localhost dev only.".format(
                    _env_prod, _deploy_prod, _bind_host, _non_localhost
                )
            )

    app = FastAPI(
        title="SeleneQuant Local API",
        description="Local-first crypto quantitative trading platform API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_production else "/api/v1/docs",
        redoc_url=None if is_production else "/api/v1/redoc",
        openapi_url=None if is_production else "/api/v1/openapi.json",
    )

    # Middleware — added inner-first: Starlette's add_middleware() inserts at
    # the front, so the LAST call ends up outermost. Execution order here is
    # RequestID → BillingGate → RateLimit.
    #
    # RateLimit sits innermost on purpose: it meters authenticated traffic per
    # user, and the identity it reads (scope["state"]["user_id"]) is written by
    # BillingGateMiddleware. Outermost would see an empty state and fall back to
    # IP bucketing — which, behind the deploy reverse proxy, is one bucket for
    # every user. See rate_limit._bucket_key.
    #
    # The limiter is mounted only in billing mode (i.e. a shared/remote
    # deployment). Local runs bind 127.0.0.1 (dev.py / *.bat) and the deploy
    # RUNBOOK keeps 8088 off the public internet, so there is no one for it to
    # protect against — except the single local user it throttles. A page load
    # fans out to ~91 requests (69 of them the idle-time preload), so at the
    # local quota a first visit already spends 57% of the minute's budget and
    # the next navigation starts collecting 429s, which the frontend answers
    # with a 60s backoff.
    if billing_mode_enabled():
        app.add_middleware(RateLimitMiddleware)

    # Billing gate as pure ASGI middleware (NOT app-level dependencies=):
    # FastAPI 0.138.x cannot solve Request-param dependencies in websocket
    # scope — app-level dependencies=[Depends(billing_auth_guard)] made every
    # /api/v1/ws/* handshake 500. See BillingGateMiddleware docstring.
    app.add_middleware(BillingGateMiddleware)

    app.add_middleware(RequestIDMiddleware)

    # Security headers
    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=()"
        )
        return response

    # CORS — allow desktop frontend
    # 3001..3010:并行副本 dev 端口段(SeleneQuant 前端跑 3001;Next dev 端口被占时自动 +1)
    _cors_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        *(f"http://localhost:{p}" for p in range(3001, 3011)),
        *(f"http://127.0.0.1:{p}" for p in range(3001, 3011)),
        "tauri://localhost",
        "https://tauri.localhost",
        "http://tauri.localhost",
        "http://tauri.localhost:8088",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Session-ID", "X-Request-ID"],
        max_age=3600,
    )

    # Error handlers (must be installed before routers for catch-all to work)
    install_error_handlers(app)

    # Routers
    app.include_router(account_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(agent_router, prefix="/api/v1")
    app.include_router(agentstudio_router, prefix="/api/v1")
    app.include_router(agg_orderbook_router, prefix="/api/v1")
    app.include_router(alpha_router, prefix="/api/v1")
    app.include_router(analytics_router, prefix="/api/v1")
    try:
        from app.api.v1.ashare_deep import router as ashare_deep_router

        app.include_router(ashare_deep_router, prefix="/api/v1")
    except ImportError:
        pass

    try:
        from app.api.v1.ashare import router as ashare_router

        app.include_router(ashare_router, prefix="/api/v1")
    except ImportError:
        pass

    app.include_router(backtests_router, prefix="/api/v1")
    app.include_router(beta_play_router, prefix="/api/v1")
    try:
        from app.api.v1.chart import router as chart_router

        app.include_router(chart_router, prefix="/api/v1")
    except ImportError:
        pass

    app.include_router(chat_sessions_router, prefix="/api/v1")
    try:
        from app.api.v1.commodities_fx import router as commodities_fx_router

        app.include_router(commodities_fx_router, prefix="/api/v1")
    except ImportError:
        pass

    app.include_router(cvd_router, prefix="/api/v1")
    app.include_router(daily_cycle_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(decision_signals_router, prefix="/api/v1")
    app.include_router(decision_workflow_router, prefix="/api/v1")
    app.include_router(derivatives_router, prefix="/api/v1")
    app.include_router(dex_router, prefix="/api/v1")
    app.include_router(dex_trading_router, prefix="/api/v1")
    try:
        from app.api.v1.events import router as events_router

        app.include_router(events_router, prefix="/api/v1")
    except ImportError:
        pass
    app.include_router(experiment_plans_router, prefix="/api/v1")
    app.include_router(experiments_router, prefix="/api/v1")
    app.include_router(favorites_router, prefix="/api/v1")
    try:
        app.include_router(fund_ingest_router, prefix="/api/v1")
    except ImportError:
        pass

    app.include_router(gex_router, prefix="/api/v1")
    app.include_router(grid_router, prefix="/api/v1")
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(heatmap_router, prefix="/api/v1")
    app.include_router(hyperliquid_router, prefix="/api/v1")
    app.include_router(intelligence_router, prefix="/api/v1")
    app.include_router(kf_index_router, prefix="/api/v1")
    app.include_router(liqmap_router, prefix="/api/v1")
    app.include_router(liqratio_router, prefix="/api/v1")
    app.include_router(liquidations_router, prefix="/api/v1")
    app.include_router(live_router, prefix="/api/v1")
    app.include_router(macrodata_router, prefix="/api/v1")
    app.include_router(market_analysis_router, prefix="/api/v1")
    app.include_router(marketplace_router, prefix="/api/v1")
    app.include_router(markets_router, prefix="/api/v1")
    # MCP outbound channel — 末尾追加 (P2-5 mcp-channel, 不重排)
    app.include_router(mcp_channel_router, prefix="/api/v1")

    app.include_router(mcp_router, prefix="/api/v1")
    app.include_router(memory_router, prefix="/api/v1")
    try:
        from app.api.v1.navigation import router as navigation_router

        app.include_router(navigation_router, prefix="/api/v1")
    except ImportError:
        pass

    try:
        app.include_router(news_router, prefix="/api/v1")
    except ImportError:
        pass

    app.include_router(notifications_router, prefix="/api/v1")
    app.include_router(onchain_router, prefix="/api/v1")
    app.include_router(optimizable_params_router, prefix="/api/v1")
    app.include_router(optimizations_router, prefix="/api/v1")
    app.include_router(optimize_router, prefix="/api/v1")
    app.include_router(options_router, prefix="/api/v1")
    app.include_router(paper_plans_router, prefix="/api/v1")
    app.include_router(paper_router, prefix="/api/v1")
    app.include_router(polymarket_router, prefix="/api/v1")
    app.include_router(portfolio_router, prefix="/api/v1")
    app.include_router(qevolve_router, prefix="/api/v1")
    app.include_router(regime_router, prefix="/api/v1")
    app.include_router(renintel_router, prefix="/api/v1")
    try:
        app.include_router(research_graph_router, prefix="/api/v1")
    except ImportError:
        pass
    try:
        app.include_router(research_missions_router, prefix="/api/v1")
    except ImportError:
        pass
    app.include_router(research_router, prefix="/api/v1")
    app.include_router(serenity_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    app.include_router(setup_backtest_router, prefix="/api/v1")
    app.include_router(shadow_profile_router, prefix="/api/v1/paper")
    app.include_router(signals_router, prefix="/api/v1")
    app.include_router(skills_router, prefix="/api/v1")
    try:
        from app.api.v1.stock import router as stock_router

        app.include_router(stock_router, prefix="/api/v1")
    except ImportError:
        pass
    app.include_router(stock_profile_router, prefix="/api/v1")
    app.include_router(strategies_router, prefix="/api/v1")

    try:
        from app.api.v1.system_logs import router as system_logs_router

        app.include_router(system_logs_router, prefix="/api/v1")
    except ImportError:
        pass

    try:
        from app.api.v1.taiwan import router as taiwan_router

        app.include_router(taiwan_router, prefix="/api/v1")
    except ImportError:
        pass

    app.include_router(tof_router, prefix="/api/v1")
    app.include_router(trading_router, prefix="/api/v1")
    app.include_router(us_radar_router, prefix="/api/v1")
    app.include_router(vchart_router, prefix="/api/v1")
    app.include_router(vibe_channels_router, prefix="/api/v1")
    app.include_router(widgets_router, prefix="/api/v1")
    app.include_router(ws_router)

    # auth/billing (monetize Phase 1.3) — 末尾追加,不重排
    app.include_router(auth_router, prefix="/api/v1")
    # episodes — 末尾追加 (S7 Episode 生命周期, 不重排)
    from app.api.v1.episodes import router as episodes_router

    app.include_router(episodes_router, prefix="/api/v1")

    # ashare_watch (A股看盘面:涨停池/连板梯队/情绪周期/板块排名) — 末尾追加,不重排
    try:
        from app.api.v1.ashare_watch import router as ashare_watch_router

        app.include_router(ashare_watch_router, prefix="/api/v1")
    except ImportError:
        pass

    # anomaly (A股异动 suggest 档:待批提案列表 + 裁定) — 末尾追加,不重排
    # plain import on purpose: a silently-skipped router would make the whole
    # suggest tier invisible (an endpoint that never mounts reads as "no
    # proposals pending"), so a broken import must fail startup loudly.
    from app.api.v1.anomaly import router as anomaly_router

    app.include_router(anomaly_router, prefix="/api/v1")

    # agent_reasoning_recent 已通过 agent_router (上方已 include) 暴露 /agent/reasoning/recent

    # MCP server 挂载(Phase 0):把只读工具暴露给外部 MCP 客户端。
    # 工具在 lifespan 里注册(catalog 那时才就绪);这里只挂 ASGI 子应用。
    # 失败降级 —— 后端必须照常起。
    try:
        from selene_space_agent.mcp import server as _mcp_server

        _mcp_subapp = _mcp_server.build_subapp()
        if _mcp_subapp is not None:
            app.mount(_mcp_server.MOUNT_PATH, _mcp_subapp)
            logger.info("MCP sub-app mounted at %s", _mcp_server.MOUNT_PATH)
    except Exception:
        logger.warning("MCP server mount skipped", exc_info=True)

    return app


app = create_app()
