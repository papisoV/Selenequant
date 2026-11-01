"""Dependency injection for service instances used by API routes.

Each service is created once at startup (via lifespan) and injected into
route handlers through FastAPI's ``Depends`` mechanism.  During testing the
instances can be overridden via ``app.dependency_overrides``.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from selene_space_core.diagnostics.health import HealthCheckResult, HealthRegistry
from selene_space_types.common import Pagination, ResponseMeta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Authentication infrastructure
# ---------------------------------------------------------------------------

# Local-first trust model: requests from localhost are trusted as admin.
# Remote requests are rejected. Future: add JWT for multi-user deployments.

_bearer_scheme = HTTPBearer(auto_error=False)


_UUID_RE = re.compile(r"^[0-9a-fA-F-]{1,40}$")


class AuthenticatedUser(BaseModel):
    """Represents an authenticated user."""

    id: str
    role: str = "user"


async def require_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """Return authenticated user.

    Local-first trust model: requests from localhost are trusted as admin.
    Remote requests require a valid JWT (future: multi-user deployments).
    TestClient uses host "testclient" — treated as local for test compat.
    """
    client = request.client
    if client and client.host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return AuthenticatedUser(id="local", role="admin")

    # Future: JWT validation for non-localhost connections
    raise HTTPException(status_code=401, detail="Authentication required")


async def optional_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthenticatedUser:
    """Return authenticated user, or anonymous for remote unauthenticated."""
    client = request.client
    if client and client.host in ("127.0.0.1", "::1", "localhost", "testclient"):
        return AuthenticatedUser(id="local", role="admin")
    return AuthenticatedUser(id="anonymous", role="anonymous")


# ---------------------------------------------------------------------------
# Singleton holders — populated during lifespan startup
# ---------------------------------------------------------------------------

_health_registry: HealthRegistry | None = None
_market_service: Any | None = None
_paper_account_manager: Any | None = None
_paper_order_manager: Any | None = None
_paper_position_manager: Any | None = None
_risk_engine: Any | None = None
_agent_runtime: Any | None = None
_execution_engine: Any | None = None
_live_engine: Any | None = None
_shadow_engine: Any | None = None
_order_journal: Any | None = None
_live_state_repo: Any | None = None
_risk_monitor: Any | None = None
_execution_mode: str = "paper"
_settings_service: Any | None = None
_key_pair_manager: Any | None = None
_mcp_registry: Any | None = None
_skill_registry: Any | None = None
_strategy_repo: Any | None = None
_db_manager: Any | None = None
_tool_catalog: Any | None = None
_funding_rate_fetcher: Any | None = None
_liquidation_fetcher: Any | None = None
_dex_fetcher: Any | None = None
_chat_session_repo: Any | None = None
_strategy_runner: Any | None = None
_mode_guard: Any | None = None
_onchain_fetcher: Any | None = None
_kline_manager: Any | None = None
_derivatives_db: Any | None = None
_serenity_score_db: Any | None = None


# ---------------------------------------------------------------------------
# Setters — called from lifespan
# ---------------------------------------------------------------------------


def set_health_registry(registry: HealthRegistry) -> None:
    global _health_registry
    _health_registry = registry


def set_market_service(svc: Any) -> None:
    global _market_service
    _market_service = svc


def set_paper_managers(
    accounts: Any,
    orders: Any,
    positions: Any,
) -> None:
    global _paper_account_manager, _paper_order_manager, _paper_position_manager
    _paper_account_manager = accounts
    _paper_order_manager = orders
    _paper_position_manager = positions


def set_risk_engine(engine: Any) -> None:
    global _risk_engine
    _risk_engine = engine


def set_agent_runtime(runtime: Any) -> None:
    global _agent_runtime
    _agent_runtime = runtime


def set_execution_engine(engine: Any) -> None:
    global _execution_engine
    _execution_engine = engine


def set_live_engine(engine: Any) -> None:
    global _live_engine
    _live_engine = engine


def set_shadow_engine(engine: Any) -> None:
    global _shadow_engine
    _shadow_engine = engine


def set_order_journal(journal: Any) -> None:
    global _order_journal
    _order_journal = journal


def set_live_state_repo(repo: Any) -> None:
    global _live_state_repo
    _live_state_repo = repo


def set_risk_monitor(monitor: Any) -> None:
    global _risk_monitor
    _risk_monitor = monitor


def set_execution_mode(mode: str) -> None:
    global _execution_mode
    _execution_mode = mode


def set_settings_service(service: Any) -> None:
    global _settings_service
    _settings_service = service


def set_key_pair_manager(manager: Any) -> None:
    global _key_pair_manager
    _key_pair_manager = manager


def set_mcp_registry(registry: Any) -> None:
    """Set the McpRegistry instance (called from lifespan)."""
    global _mcp_registry
    _mcp_registry = registry


def set_skill_registry(registry: Any) -> None:
    """Set the SkillRegistry instance (called from lifespan)."""
    global _skill_registry
    _skill_registry = registry


def set_strategy_repo(repo: Any) -> None:
    """Set the StrategyRepository instance (called from lifespan)."""
    global _strategy_repo
    _strategy_repo = repo


def set_db_manager(db: Any) -> None:
    """Set the AsyncSQLiteManager instance (called from lifespan)."""
    global _db_manager
    _db_manager = db


def set_tool_catalog(catalog: Any) -> None:
    """Set the ToolCatalog instance (called from lifespan)."""
    global _tool_catalog
    _tool_catalog = catalog


def set_funding_rate_fetcher(fetcher: Any) -> None:
    """Set the FundingRateFetcher instance (called from lifespan)."""
    global _funding_rate_fetcher
    _funding_rate_fetcher = fetcher


def set_liquidation_fetcher(fetcher: Any) -> None:
    """Set the LiquidationFetcher instance (called from lifespan)."""
    global _liquidation_fetcher
    _liquidation_fetcher = fetcher


def set_dex_fetcher(fetcher: Any) -> None:
    """Set the DexFetcher instance (called from lifespan)."""
    global _dex_fetcher
    _dex_fetcher = fetcher


def set_chat_session_repo(repo: Any) -> None:
    """Set the ChatSessionRepository instance (called from lifespan)."""
    global _chat_session_repo
    _chat_session_repo = repo


def set_strategy_runner(runner: Any) -> None:
    """Set the StrategyRunner instance (called from lifespan)."""
    global _strategy_runner
    _strategy_runner = runner


def set_mode_guard(guard: Any) -> None:
    """Set the ExecutionModeGuard instance (called from lifespan + set_mode)."""
    global _mode_guard
    _mode_guard = guard


def get_mode_guard() -> Any:
    """Return the ExecutionModeGuard instance (or None if not wired)."""
    return _mode_guard


def set_onchain_fetcher(fetcher: Any) -> None:
    """Set the OnchainFetcher instance (called from lifespan)."""
    global _onchain_fetcher
    _onchain_fetcher = fetcher


def set_kline_manager(manager: Any) -> None:
    """Set the KlineDataManager instance (called from lifespan)."""
    global _kline_manager
    _kline_manager = manager


def set_derivatives_db(db: Any) -> None:
    """Set the DerivativesDB instance (called from lifespan)."""
    global _derivatives_db
    _derivatives_db = db


def set_serenity_score_db(db: Any) -> None:
    """Set the SerenityScoreDB instance (called from lifespan)."""
    global _serenity_score_db
    _serenity_score_db = db


def build_response_meta(
    request: Request,
    pagination: Pagination | None = None,
) -> ResponseMeta:
    """Build API response metadata with request correlation id."""
    request_id = getattr(request.state, "request_id", None)
    return ResponseMeta(request_id=request_id, pagination=pagination)


# ---------------------------------------------------------------------------
# FastAPI dependency callables
# ---------------------------------------------------------------------------


def _local_api_health_probe() -> Any:
    """Probe that always reports the local API process as healthy.

    Registered into the HealthRegistry so /health has at least one check even
    before any subsystem wires its own.
    """

    async def _probe() -> HealthCheckResult:
        return HealthCheckResult(name="local_api", status="healthy", latency_ms=0.0)

    return _probe


def get_health_registry() -> HealthRegistry:
    """Return the global HealthRegistry instance."""
    global _health_registry
    if _health_registry is None:
        registry = HealthRegistry()
        registry.register_check("local_api", _local_api_health_probe())
        _health_registry = registry
    return _health_registry


def worker_heartbeats_health(
    repo: Any,
    now_ms: int | None = None,
    stale_secs: int = 45,
    *,
    exclude_roles: tuple[str, ...] = (),
):
    """Build a health probe that reports stale long-running workers as degraded.

    Returns an async callable matching ``HealthRegistry.register_check``'s
    signature.  With no stale workers the probe reports ``healthy``; with
    stale workers it reports ``degraded`` and attaches the stale rows
    (worker id, role, heartbeat age) as detail.

    ``exclude_roles`` drops rows whose ``role`` is listed before the stale
    verdict — for workers (e.g. the PA heartbeat) whose cadence is slower than
    ``stale_secs`` and which therefore carry their own probe; without it they
    would flag this generic probe degraded between beats.
    """

    async def _probe() -> HealthCheckResult:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        stale = await repo.list_stale(now, stale_secs=stale_secs)
        if exclude_roles:
            stale = [r for r in stale if r.get("role") not in exclude_roles]
        if not stale:
            return HealthCheckResult(
                name="worker_heartbeats", status="healthy", latency_ms=0.0
            )
        detail_rows = []
        for row in stale:
            detail_rows.append(
                {
                    "worker_id": row.get("worker_id"),
                    "role": row.get("role"),
                    "heartbeat_at": row.get("heartbeat_at"),
                }
            )
        # 透明性批3-12: 心跳异常 → 系统事件(FeedBar 可见);fire-and-forget
        try:
            from app.api.v1.events import record_system_event

            await record_system_event(
                "heartbeat_anomaly",
                "health",
                f"Worker 心跳失联 {len(stale)} 个({', '.join(str(r.get('worker_id')) for r in stale[:3])})",
                severity="warning",
                payload={"stale": detail_rows},
            )
        except Exception:
            pass
        return HealthCheckResult(
            name="worker_heartbeats",
            status="degraded",
            latency_ms=0.0,
            detail={"stale": detail_rows},
        )

    return _probe


def pa_heartbeat_health(
    repo: Any,
    now_ms: int | None = None,
    stale_secs: int = 180,
):
    """Health probe for the PA heartbeat loop, keyed to its own cadence.

    The PA loop beats once per tick (default 60s), so the generic 45s window
    would flag a healthy loop stale between beats.  This probe reads only the
    ``pa_heartbeat`` row and reports:

    - ``healthy`` + ``last_success_at`` when the row is fresh (or the worker
      cleanly stopped / never started — neither is a failure);
    - ``degraded`` when a ``running`` row has not been refreshed within
      ``stale_secs`` (the loop stalled or died).

    The detail answers "what did it last accomplish", not merely "is the
    process alive": scan counts and the last round's timestamp ride along.
    """

    async def _probe() -> HealthCheckResult:
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        rows = [r for r in await repo.list() if r.get("worker_id") == "pa_heartbeat"]
        if not rows:
            # Never started (loop disabled) — nothing is expected to be live.
            return HealthCheckResult(
                name="pa_heartbeat",
                status="healthy",
                latency_ms=0.0,
                detail={"state": "not_started"},
            )
        row = rows[0]
        meta = row.get("metadata_json") or {}
        detail: dict[str, Any] = {"state": row.get("status")}
        last_round = meta.get("last_round")
        if isinstance(last_round, dict):
            detail["last_success_at"] = last_round.get("at")
            detail["last_round"] = last_round
        if row.get("status") == "running":
            hb_at = _parse_iso_ms(row.get("heartbeat_at"))
            if hb_at is not None and now - hb_at > stale_secs * 1000:
                detail["heartbeat_at"] = row.get("heartbeat_at")
                detail["age_secs"] = round((now - hb_at) / 1000.0, 1)
                return HealthCheckResult(
                    name="pa_heartbeat",
                    status="degraded",
                    latency_ms=0.0,
                    detail=detail,
                )
        return HealthCheckResult(
            name="pa_heartbeat", status="healthy", latency_ms=0.0, detail=detail
        )

    return _probe


def _parse_iso_ms(value: Any) -> int | None:
    """Parse an ISO-8601 UTC string (as written by the heartbeat repo) to epoch ms."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def get_market_service() -> Any:
    """Return the MarketDataService instance (or None if unavailable)."""
    return _market_service


def get_paper_account_manager() -> Any:
    """Return the PaperAccountManager instance (or None)."""
    return _paper_account_manager


def get_paper_order_manager() -> Any:
    """Return the PaperOrderManager instance (or None)."""
    return _paper_order_manager


def get_paper_position_manager() -> Any:
    """Return the PaperPositionManager instance (or None)."""
    return _paper_position_manager


def get_risk_engine() -> Any:
    """Return the RiskEngine instance (or None)."""
    return _risk_engine


def get_agent_runtime() -> Any:
    """Return the AgentRuntime instance (or None)."""
    return _agent_runtime


def get_execution_engine() -> Any:
    """Return the current ExecutionEngine (Paper or Live)."""
    return _execution_engine


def get_live_engine() -> Any:
    """Return the LiveExecutionEngine (or None if not configured)."""
    return _live_engine


def get_shadow_engine() -> Any:
    """Return the ShadowExecutionEngine (or None if not configured)."""
    return _shadow_engine


def get_order_journal() -> Any:
    """Return the OrderJournal (or None)."""
    return _order_journal


def get_live_state_repo() -> Any:
    """Return the LiveStateRepository (or None)."""
    return _live_state_repo


def get_risk_monitor() -> Any:
    """Return the ContinuousRiskMonitor (or None)."""
    return _risk_monitor


def get_execution_mode() -> str:
    """Return the current execution mode ('paper' or 'live')."""
    return _execution_mode


def get_settings_service() -> Any:
    """Return the SettingsService instance (or None).

    Before lifespan wires one, build a throwaway instance so settings reads
    work during early startup; once wired, ``_settings_service`` wins.
    """
    if _settings_service is not None:
        return _settings_service

    from app.core.settings_service import SettingsService
    from selene_space_security.secrets import SecretManager

    return SettingsService(
        secret_manager=SecretManager(),
        key_pair_manager=_key_pair_manager,
    )


def get_key_pair_manager() -> Any:
    """Return the KeyPairManager instance (or None)."""
    return _key_pair_manager


def get_mcp_registry() -> Any:
    """Return the McpRegistry instance (or None)."""
    return _mcp_registry


def get_skill_registry() -> Any:
    """Return the SkillRegistry instance (or None)."""
    return _skill_registry


def get_strategy_repo() -> Any:
    """Return the StrategyRepository instance (or None)."""
    return _strategy_repo


def get_db_manager() -> Any:
    """Return the AsyncSQLiteManager instance (or None)."""
    return _db_manager


def get_tool_catalog() -> Any:
    """Return the ToolCatalog instance (or None)."""
    return _tool_catalog


def get_funding_rate_fetcher() -> Any:
    """Return the FundingRateFetcher instance (or None)."""
    return _funding_rate_fetcher


def get_liquidation_fetcher() -> Any:
    """Return the LiquidationFetcher instance (or None)."""
    return _liquidation_fetcher


def get_dex_fetcher() -> Any:
    """Return the DexFetcher instance (or None)."""
    return _dex_fetcher


def get_chat_session_repo() -> Any:
    """Return the ChatSessionRepository instance (or None)."""
    return _chat_session_repo


def get_strategy_runner() -> Any:
    """Return the StrategyRunner instance (or None)."""
    return _strategy_runner


def get_onchain_fetcher() -> Any:
    """Return the OnchainFetcher instance (or None)."""
    return _onchain_fetcher


def get_kline_manager() -> Any:
    """Return the KlineDataManager instance (or None)."""
    return _kline_manager


def get_derivatives_db() -> Any:
    """Return the DerivativesDB instance (or None)."""
    return _derivatives_db


def get_serenity_score_db() -> Any:
    """Return the SerenityScoreDB instance (or None)."""
    return _serenity_score_db


# ---------------------------------------------------------------------------
# Macro data service (v008)
# ---------------------------------------------------------------------------

_macrodata_service: Any | None = None


def set_macrodata_service(svc: Any) -> None:
    """Set the MacroDataService instance (called during lifespan startup)."""
    global _macrodata_service
    _macrodata_service = svc


def get_macrodata_service() -> Any:
    """Return the MacroDataService instance (or None)."""
    return _macrodata_service


# ---------------------------------------------------------------------------
# Analytics service (v009)
# ---------------------------------------------------------------------------

_analytics_service: Any | None = None


def set_analytics_service(svc: Any) -> None:
    """Set the AnalyticsService instance (called during lifespan startup)."""
    global _analytics_service
    _analytics_service = svc


def get_analytics_service() -> Any:
    """Return the AnalyticsService instance (or None)."""
    return _analytics_service


# ---------------------------------------------------------------------------
# Optimize service (v010)
# ---------------------------------------------------------------------------

_optimize_service: Any | None = None


def set_optimize_service(svc: Any) -> None:
    """Set the OptimizeService instance (called during lifespan startup)."""
    global _optimize_service
    _optimize_service = svc


def get_optimize_service() -> Any:
    """Return the OptimizeService instance (or None)."""
    return _optimize_service


# v011 — batch4-agentstudio
_agentstudio_service: Any | None = None


def set_agentstudio_service(svc: Any) -> None:
    """Set the AgentStudioService instance (called during lifespan startup)."""
    global _agentstudio_service
    _agentstudio_service = svc


def get_agentstudio_service() -> Any:
    """Return the AgentStudioService instance (or None)."""
    return _agentstudio_service


# v012 — batch5-renintel
_renintel_service: Any | None = None


def set_renintel_service(svc: Any) -> None:
    """Set the RenIntelService instance (called during lifespan startup)."""
    global _renintel_service
    _renintel_service = svc


def get_renintel_service() -> Any:
    """Return the RenIntelService instance (or None)."""
    return _renintel_service


# v014 — batch6c-daily-cycle
# No setter/getter needed for DailyTradingCycleWorkflow: the workflow is
# stateless and instantiated per-request in app/api/v1/daily_cycle.py.
# The endpoint uses get_renintel_service() / get_agentstudio_service() to
# wire IC Decision providers (persona / rd_loop) at runtime. Documented here
# per the repository layout convention.


# v017 — tiered-trailing-frontend
# Tracker holder + setter/getter. lifespan 启动时从 SettingsService
# 读 risk.tiered_* 构造 TieredTrailingConfig,实例化 tracker 注入 LiveDecisionPipeline.
_tiered_trailing_tracker: Any | None = None


def set_tiered_trailing_tracker(tracker: Any) -> None:
    """Set the TieredTrailingStopTracker instance (called from lifespan)."""
    global _tiered_trailing_tracker
    _tiered_trailing_tracker = tracker


def get_tiered_trailing_tracker() -> Any:
    """Return the TieredTrailingStopTracker (or None if not wired)."""
    return _tiered_trailing_tracker


_decision_workflow_service: Any = None


def set_decision_workflow_service(svc: Any) -> None:
    """Set the DecisionWorkflowService instance (called from lifespan)."""
    global _decision_workflow_service
    _decision_workflow_service = svc


def get_decision_workflow_service() -> Any:
    """Return the DecisionWorkflowService instance (or None if not wired)."""
    return _decision_workflow_service


# market-analysis (US-008) — 延迟构建，首次请求时才解析 LLM 配置 + db
_market_analysis_service: Any = None


def set_market_analysis_service(svc: Any) -> None:
    """Set the MarketAnalysisService instance (tests / lifespan)."""
    global _market_analysis_service
    _market_analysis_service = svc


def get_market_analysis_service() -> Any:
    """Return the MarketAnalysisService, lazily built on first use."""
    global _market_analysis_service
    if _market_analysis_service is None:
        from app.core.market_analysis_service import MarketAnalysisService

        _market_analysis_service = MarketAnalysisService(
            db_loader=get_db_manager,
            settings_loader=get_settings_service,
        )
    return _market_analysis_service


# funding-reversal watchdog (T1) — 供 lifespan 装配 FundingReversalMonitor
_funding_reversal_monitor: Any = None


def set_funding_reversal_monitor(monitor: Any) -> None:
    """Set the FundingReversalMonitor instance (called from lifespan)."""
    global _funding_reversal_monitor
    _funding_reversal_monitor = monitor


def get_funding_reversal_monitor() -> Any:
    """Return the FundingReversalMonitor instance (or None if not wired)."""
    return _funding_reversal_monitor


# Serenity pool persistence (v023 / F1) — appended at end per convergence protocol
_serenity_pool_db: Any = None


def set_serenity_pool_db(db: Any) -> None:
    """Set the SerenityPoolDB instance (called from lifespan / tests)."""
    global _serenity_pool_db
    _serenity_pool_db = db


def get_serenity_pool_db() -> Any:
    """Return the SerenityPoolDB instance (or None if not wired)."""
    return _serenity_pool_db


# Experiment repository (v010 / research version-control) — appended at end
_experiment_repo: Any = None


def set_experiment_repo(repo: Any) -> None:
    """Set the ExperimentRepository instance (called from lifespan)."""
    global _experiment_repo
    _experiment_repo = repo


def get_experiment_repo() -> Any:
    """Return the ExperimentRepository instance (or None if not wired)."""
    return _experiment_repo


# PaperPlan repository (v011 / paper deployment tracking) — appended at end
_paper_plan_repo: Any = None


def set_paper_plan_repo(repo: Any) -> None:
    """Set the PaperPlanRepository instance (called from lifespan)."""
    global _paper_plan_repo
    _paper_plan_repo = repo


def get_paper_plan_repo() -> Any:
    """Return the PaperPlanRepository instance (or None if not wired)."""
    return _paper_plan_repo


# Episode repository (v016 / strategy experience ledger) — appended at end
_episode_repo: Any = None


def set_episode_repo(repo: Any) -> None:
    """Set the EpisodeRepository instance (called from lifespan)."""
    global _episode_repo
    _episode_repo = repo


def get_episode_repo() -> Any:
    """Return the EpisodeRepository instance (or None if not wired)."""
    return _episode_repo


# Forward-validation recorder (阶段 C / P5) — appended at end.
# 一个无参 async callable:跑一轮前向验证,返回 counters dict。由 lifespan 在
# db_manager 就绪后装配;None = daily-cycle 的 PostMarketPhase 跳过该 stage。
# 装配体见 app.core.forward_validation_recorder(它同时 import storage 与
# backtest-engine,只有本装配层能同时看见两者 —— 方案 §4)。
_outcome_recorder: Any = None


def set_outcome_recorder(recorder: Any) -> None:
    """Set the P5 forward-validation recorder callable (called from lifespan)."""
    global _outcome_recorder
    _outcome_recorder = recorder


def get_outcome_recorder() -> Any:
    """Return the P5 forward-validation recorder callable (or None if not wired)."""
    return _outcome_recorder


# A-share anomaly decision consumer (S4: observe + suggest) — appended at end.
# lifespan wires it onto the market bus after the anomaly detector; route
# handlers read it back via the getter. None = not wired (crypto-only
# deployment or wiring failed at startup).
_anomaly_decision_consumer: Any = None


def set_anomaly_decision_consumer(consumer: Any) -> None:
    """Set the AnomalyDecisionConsumer instance (called from lifespan)."""
    global _anomaly_decision_consumer
    _anomaly_decision_consumer = consumer


def get_anomaly_decision_consumer() -> Any:
    """Return the AnomalyDecisionConsumer instance (or None if not wired)."""
    return _anomaly_decision_consumer
