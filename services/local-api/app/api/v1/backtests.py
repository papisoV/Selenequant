"""Backtest endpoints.

POST /backtests returns 202 with a task_id since backtests can be long-running.
Results are stored in-memory for v0.1 (replaced by storage repo later).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.billing_auth import require_active_entitlement
from app.core.dependencies import (
    AuthenticatedUser,
    build_response_meta,
    get_db_manager,
    get_market_service,
    optional_user,
)
from selene_space_core.kline_paths import default_klines_db_path
from selene_space_types.common import APIResponse, Pagination
from selene_space_types.errors import NotFoundError
from selene_space_types.strategy import BacktestResult, StrategyConfig, StrategyType

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.WARNING, format="%(levelname)s:%(name)s:%(message)s"
    )

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _ok(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


# ---------------------------------------------------------------------------
# Task state
# ---------------------------------------------------------------------------


class BacktestTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BacktestTask(BaseModel):
    task_id: str
    strategy_id: str
    strategy_name: str = ""
    symbol: str = ""
    interval: str = "1h"
    status: BacktestTaskStatus = BacktestTaskStatus.PENDING
    result: BacktestResult | None = None
    user_id: str = "local"
    error: str | None = None
    created_at: int = Field(default_factory=lambda: int(time.time() * 1000))
    # US-105: engine 主循环 progress_cb 写这里(GIL 下 int 赋值原子,读侧容忍过期);
    # 0 = 未开始/未知,终态后端点不再透传
    progress: int = 0


_MAX_TASKS = 1000
_MAX_CONCURRENT_PER_USER = 10
_tasks: dict[str, BacktestTask] = {}

_result_owners: dict[str, str] = {}


def _evict_oldest_tasks() -> None:
    """Remove oldest tasks when exceeding limit."""
    while len(_tasks) > _MAX_TASKS:
        oldest_key = next(iter(_tasks))
        _tasks.pop(oldest_key, None)


def _get_task_repo():
    """Lazily build the persistent task repository (None when DB unavailable).

    The in-memory ``_tasks`` dict stays the hot-path cache; SQLite
    ``backtest_tasks`` is the durable source of truth behind it.
    """
    db = get_db_manager()
    if db is None:
        return None
    from selene_space_storage.repositories.backtest_tasks import (
        BacktestTaskRepository,
    )

    return BacktestTaskRepository(db)


def _task_row_to_frontend_dict(row) -> dict[str, Any]:
    """Convert a persisted task row to the same frontend shape as in-memory tasks."""
    return {
        "id": row.task_id,
        "task_id": row.task_id,
        "strategy_id": row.strategy_id,
        "strategy_name": row.strategy_name,
        "symbol": row.symbol,
        "interval": row.interval,
        "status": row.status,
        "created_at": row.created_at,
        "error": row.error,
    }


async def _hydrate_strategy_names(items: list[dict[str, Any]]) -> None:
    """Fill placeholder strategy_name (empty, or the raw id echoed back) from the
    strategies table, in one batch query.

    ``_task_row_to_frontend_dict`` carries whatever the task row stored — which is
    often "" for rows created before the name was resolved — and
    ``_result_to_frontend_dict`` falls back to ``strategy_id``. Both leave the
    list showing raw ids like ``bb_v3`` instead of the human name the strategies
    table already holds. Mutates ``items`` in place.
    """
    targets = {
        str(it["strategy_id"])
        for it in items
        if it.get("strategy_id")
        and (
            not it.get("strategy_name")
            or it.get("strategy_name") == it.get("strategy_id")
        )
    }
    if not targets:
        return
    db = get_db_manager()
    if db is None:
        return
    try:
        placeholders = ",".join("?" * len(targets))
        rows = await db.execute(
            f"SELECT id, name FROM strategies WHERE id IN ({placeholders})",
            tuple(targets),
        )
    except Exception:
        logger.debug("Strategy name hydration failed", exc_info=True)
        return
    names = {r["id"]: r["name"] for r in rows if r["name"]}
    for it in items:
        name = names.get(it.get("strategy_id"))
        if name:
            it["strategy_name"] = name


async def _recover_orphaned_tasks() -> None:
    """Lifespan startup: fail any pending/running tasks left by a previous process.

    In-flight backtests cannot be resumed safely (they may already have placed
    orders), so they are marked failed so the user knows to re-submit.
    """
    repo = _get_task_repo()
    if repo is None:
        return
    try:
        rows = await repo.list_active_for_recovery()
        for row in rows:
            await repo.mark_failed(row.task_id, "server restart")
            logger.warning(
                "Recovered orphaned backtest task %s -> failed (server restart)",
                row.task_id,
            )
    except Exception:
        logger.debug("Orphaned backtest task recovery failed", exc_info=True)


def _get_results_store() -> dict[str, BacktestResult]:
    """Return the unified backtest results store (single source of truth)."""
    from selene_space_agent.tools.strategy_tools import get_results_store

    return cast(dict[str, BacktestResult], get_results_store())


def _find_alpha_archive(alpha_name: str) -> dict[str, Any] | None:
    """Load a saved alpha formula JSON from ``data/strategies/alpha_v*``.

    CWD-relative on purpose: the API runs from ``services/local-api``, which is
    where ``alpha.deploy_formula`` writes its ``alpha_v_deploy`` mirror. Both
    sides must agree on that base or the ``alpha:{id}`` backtest can't see a
    formula the deploy endpoint just reported as deployed.
    """
    strategies_dir = Path("data/strategies")
    if not strategies_dir.exists():
        return None
    for vdir in strategies_dir.glob("alpha_v*"):
        if not vdir.is_dir():
            continue
        target = vdir / f"{alpha_name}.json"
        if target.exists():
            with open(target) as fh:
                return cast("dict[str, Any] | None", json.load(fh))
        for jf in vdir.glob("*.json"):
            if jf.stem == alpha_name or alpha_name in jf.stem:
                with open(jf) as fh:
                    return cast("dict[str, Any] | None", json.load(fh))
    return None


# ---------------------------------------------------------------------------
# Request body — accepts both frontend and canonical field names
# ---------------------------------------------------------------------------


class RunBacktestRequest(BaseModel):
    """Body for POST /backtests."""

    strategy_id: str = Field(..., description="Strategy to backtest")
    # Kline data source parameters
    symbol: str | None = Field(
        None, description="Trading pair (e.g. BTC/USDT), overrides strategy symbol"
    )
    interval: str | None = Field(
        None,
        description="Kline interval (e.g. 1h, 4h, 1d), overrides strategy interval",
    )
    exchange: str = Field("binance", description="Exchange: binance or okx")
    market_type: str = Field("spot", description="Market type: spot or futures")
    # Date range
    start_date: str | None = Field(None, description="Start date ISO-8601 (optional)")
    end_date: str | None = Field(None, description="End date ISO-8601 (optional)")
    # Capital and fees
    initial_capital: float = Field(
        10_000.0, gt=0, alias="initial_capital", description="Starting capital"
    )
    initial_cash: float | None = Field(
        None, gt=0, description="Starting capital (alias)"
    )
    commission_rate: float = Field(0.001, ge=0, description="Commission rate")
    slippage_bps: float | None = Field(
        None,
        ge=0,
        description="Slippage in basis points; omit = engine default (Realistic)",
    )
    # Optional overrides
    data_path: str | None = Field(None, description="Optional parquet data path")
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="Override strategy parameters"
    )

    model_config = {"populate_by_name": True}

    @property
    def effective_cash(self) -> float:
        return (
            self.initial_cash if self.initial_cash is not None else self.initial_capital
        )


# ---------------------------------------------------------------------------
# Background runner — wired to real BacktestEngine
# ---------------------------------------------------------------------------


# Preset strategy module mapping — all BacktestStrategyTemplate subclasses
_PRESET_STRATEGY_MODULES: dict[str, str] = {
    "bb_v3": "selene_space_strategy.templates.presets.bb_v3:BBV3Strategy",
    "bb_atr_trend": "selene_space_strategy.templates.presets.bb_atr_trend:BBATRTrendStrategy",
    "dca_grid": "selene_space_strategy.templates.presets.dca_grid:DCAGridStrategy",
    "dmi_delta": "selene_space_strategy.templates.presets.dmi_delta:DMIDeltaStrategy",
    "ema_triple_cross": "selene_space_strategy.templates.presets.ema_triple_cross:EMATripleCrossStrategy",
    "keltner_v2": "selene_space_strategy.templates.presets.keltner_v2:KeltnerChannelV2Strategy",
    "ma_extended_runs": "selene_space_strategy.templates.presets.ma_extended_runs:MAExtendedRunsStrategy",
    "p_signal": "selene_space_strategy.templates.presets.p_signal:PSignalStrategy",
    "pyramid_ma": "selene_space_strategy.templates.presets.pyramid_ma:PyramidMAStrategy",
    "rsi_ma_delta": "selene_space_strategy.templates.presets.rsi_ma_delta:RSIMADeltaStrategy",
    "stoch_supertrend": "selene_space_strategy.templates.presets.stoch_supertrend:STOCHSupertrendStrategy",
    "tema_channel": "selene_space_strategy.templates.presets.tema_channel:TEMAChannelStrategy",
    "vw_ema_cross": "selene_space_strategy.templates.presets.vw_ema_cross:VWEMACrossoverStrategy",
    "vwap_fibo_bands": "selene_space_strategy.templates.presets.vwap_fibo_bands:VWAPFiboBandsStrategy",
    "calendar_effect": "selene_space_strategy.templates.presets.calendar_effect:CalendarEffectStrategy",
    "intraday_seasonality": "selene_space_strategy.templates.presets.intraday_seasonality:IntradaySeasonalityStrategy",
    "rebalance_premium": "selene_space_strategy.templates.presets.rebalance_premium:RebalancePremiumStrategy",
    "tsmom_vol_target": "selene_space_strategy.templates.presets.tsmom_vol_target:TsmomVolTargetStrategy",
    "cross_sectional_momentum": "selene_space_strategy.templates.presets.cross_sectional_momentum:CrossSectionalMomentumStrategy",
    "january_barometer": "selene_space_strategy.templates.presets.january_barometer:JanuaryBarometerStrategy",
    # Selene A/B 升级 v4 新增预设 (2026-08-16, commit 328ef46)
    "rsi_momentum_continuation": "selene_space_strategy.templates.presets.rsi_momentum_continuation:RSIMomentumContinuationStrategy",
    "er_state_machine": "selene_space_strategy.templates.presets.er_state_machine:ERStateMachineStrategy",
    "chop_guard": "selene_space_strategy.templates.presets.chop_guard:ChopGuardStrategy",
    "reversion_cost_guard": "selene_space_strategy.templates.presets.reversion_cost_guard:ReversionCostGuardStrategy",
}


def _create_python_template_strategy(
    strategy_type: str,
    parameters: dict,
    body: RunBacktestRequest,
    symbol: str,
):
    """Create a Python template strategy instance for backtesting.

    Python template strategies use BacktestStrategyTemplate pattern instead of
    YAML rules. All presets in _PRESET_STRATEGY_MODULES are instantiated dynamically.
    """
    module_path = _PRESET_STRATEGY_MODULES.get(strategy_type)
    if module_path is None:
        raise ValueError(f"Unknown Python template strategy: {strategy_type}")

    module_name, class_name = module_path.rsplit(":", 1)
    import importlib

    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)

    # Get default params from the class __init__ signature, then merge with overrides
    import inspect

    sig = inspect.signature(cls.__init__)
    defaults: dict[str, Any] = {}
    for pname, param in sig.parameters.items():
        if pname in (
            "self",
            "symbol",
            "initial_capital",
            "leverage",
            "fee_rate",
            "max_bars",
            "long_only",
        ):
            continue
        if param.default is not inspect.Parameter.empty:
            defaults[pname] = param.default

    # Merge: defaults → parameter overrides
    merged_params = {**defaults, **(parameters or {})}

    # All BacktestStrategyTemplate subclasses share the same base constructor signature
    return cls(
        symbol=symbol,
        initial_capital=body.effective_cash,
        **merged_params,
    )


# ---------------------------------------------------------------------------
# QD (QuantDinger) exported script strategies
# ---------------------------------------------------------------------------

#: QuantDinger reports results over months of history. The single-call REST
#: cap (1500 bars) would silently shorten the run into a different
#: experiment, so the qd branch pages for depth instead.
QD_KLINES_TOTAL = 6200


def _qd_strategy_config(strategy_id: str) -> StrategyConfig:
    """Build the config for a ``qd:`` export from the source's declaration.

    Symbol and interval come from the strategy source rather than the
    request: a QuantDinger export binds one instrument in its
    ``initialize`` hook, so running it against anything else would not be
    the strategy that was exported. Unknown ids raise ``ValueError``, which
    the caller turns into a failed task.
    """
    from selene_space_strategy.qd.catalog import qd_entry

    entry = qd_entry(strategy_id)
    symbol = str(entry.get("symbol") or "")
    params = entry.get("params")
    return StrategyConfig(
        id=str(entry["id"]),
        name=str(entry["name"]),
        type="custom",
        description=f"QuantDinger export: {entry['file']}",
        symbols=[symbol] if symbol else [],
        interval=str(entry.get("interval") or "1h"),
        parameters=dict(params) if isinstance(params, dict) else {},
    )


def _bridge_strategy_config(strategy_id: str) -> StrategyConfig:
    """Build the config for a ``bridge:`` mined artifact.

    Artifacts live in SELENE_ARTIFACT_DIR as ``{name}.py`` with a docstring
    header declaring the formula; the strategy repository never sees them,
    so synthesize the config the rest of the run path expects. The artifact
    must exist — a missing file fails here, before any execution.
    """
    from selene_space_alpha.artifact_runtime import load_artifact_strategy

    name = strategy_id[len("bridge:") :]
    load_artifact_strategy(name)  # fail fast: missing/malformed artifact
    return StrategyConfig(
        id=strategy_id,
        name=f"Bridge: {name}",
        type="custom",
        description=f"Mined artifact: {name}.py",
        symbols=[],
        interval="1h",
        parameters={},
    )


def _qd_venue(strategy_id: str) -> tuple[str, str]:
    """The ``(exchange, market_type)`` a ``qd:`` export declares.

    QuantDinger's crypto exports are OKX perpetuals, and the source states
    it (``Crypto:XLM/USDT@okx:swap``), so the request body need not.
    """
    from selene_space_strategy.qd.catalog import qd_entry

    entry = qd_entry(strategy_id)
    return (
        str(entry.get("exchange") or "okx"),
        str(entry.get("market_type") or "futures"),
    )


async def _load_qd_kline_data(
    strategy_id: str, symbol: str, interval: str
) -> pd.DataFrame:
    """Deep kline history for a ``qd:`` export, from the venue it declares."""
    market_svc = get_market_service()
    if market_svc is None:
        raise FileNotFoundError(
            f"Market service unavailable; cannot load {symbol} klines for {strategy_id}"
        )
    exchange, market_type = _qd_venue(strategy_id)
    klines = await market_svc.fetch_klines_batch(
        symbol=symbol,
        exchange=exchange,
        market_type=market_type,
        interval=interval,
        total=QD_KLINES_TOTAL,
    )
    if not klines:
        raise FileNotFoundError(
            f"No klines for {symbol} on {exchange}/{market_type} ({interval})"
        )
    logger.info(
        "Loaded %d klines for %s (%s/%s %s)",
        len(klines),
        strategy_id,
        exchange,
        market_type,
        interval,
    )
    return pd.DataFrame(
        [
            {
                "timestamp": k.timestamp,
                "open": k.open,
                "high": k.high,
                "low": k.low,
                "close": k.close,
                "volume": k.volume,
                "symbol": k.symbol,
            }
            for k in klines
        ]
    )


async def _record_episode(
    task: BacktestTask, body: RunBacktestRequest, result: Any
) -> None:
    """Record one RECORDED episode row for a completed API backtest.

    Covers only this API path — agent tools / paper / live engines do not
    route through here. Episode failure must never fail the backtest.
    """
    try:
        db = get_db_manager()
        if db is None:
            return
        from selene_space_storage.repositories.episodes import EpisodeRepository

        sid = body.strategy_id
        kind = (
            "bridge"
            if sid.startswith("bridge:")
            else "alpha"
            if sid.startswith("alpha:")
            else "qd"
            if sid.startswith("qd:")
            else "yaml"
        )
        await EpisodeRepository(db).create(
            strategy_kind=kind,
            strategy_id=sid,
            symbol=task.symbol,
            interval=task.interval,
            strategy_name=task.strategy_name,
            exchange=body.exchange,
            market_type=body.market_type,
            params=body.parameters,
            backtest_id=task.task_id,
            metrics=result.metrics.model_dump(),
        )
    except Exception:
        logger.warning(
            "Failed to record episode for backtest %s", task.task_id, exc_info=True
        )


async def _run_backtest(task: BacktestTask, body: RunBacktestRequest) -> None:
    """Execute backtest in background using the real BacktestEngine."""
    task.status = BacktestTaskStatus.RUNNING
    task_repo = _get_task_repo()
    if task_repo is not None:
        try:
            await task_repo.mark_running(task.task_id, int(time.time() * 1000))
        except Exception:
            logger.debug(
                "Failed to mark task %s running in DB", task.task_id, exc_info=True
            )
    logger.warning(
        "[RUN] Starting backtest %s for strategy_id=%s", task.task_id, body.strategy_id
    )
    try:
        from app.api.v1.strategies import _get_strategy
        from selene_space_backtest.commissions import PercentageCommission
        from selene_space_backtest.engine import BacktestConfig, BacktestEngine
        from selene_space_strategy.compiler import compile as compile_strategy
        from selene_space_strategy.models import EngineStrategyConfig
        from selene_space_strategy.runtime import StrategyRuntime
        from selene_space_strategy.factory import STRATEGY_TEMPLATES

        config = await _get_strategy(body.strategy_id, user_id=task.user_id)

        # A "virtual" config is a stub BacktestRepository.save() backfills to
        # satisfy the backtests.strategy_id FK for ids that live outside the
        # strategies table (template ids, qd:/bridge:). Its symbols are empty,
        # so treating it as the real strategy skips the template/QD synthesis
        # below and the run dies on "must specify at least one symbol". Drop it
        # so the id is resolved from its real source instead.
        if config is not None and config.source == "virtual":
            config = None

        # QD exports are not in the strategy repository: the backup library on
        # disk is their registry, and each source binds its own instrument.
        # Synthesize the config the rest of this function expects.
        if config is None and body.strategy_id.startswith("qd:"):
            config = _qd_strategy_config(body.strategy_id)

        # bridge: artifacts are likewise outside the strategy repository.
        if config is None and body.strategy_id.startswith("bridge:"):
            config = _bridge_strategy_config(body.strategy_id)

        # FX13: Support template ID backtest (scanner/optimizer use template IDs directly)
        # If strategy not found, check if it's a valid template ID
        template_id = (
            body.strategy_id
        )  # Preserve template ID for STRATEGY_TEMPLATES lookup
        template_meta = STRATEGY_TEMPLATES.get(template_id)

        if config is None and template_meta is not None:
            # Template ID not in cache, create on-the-fly config
            logger.info(
                "Backtest %s: template '%s' not in cache, creating on-the-fly config",
                task.task_id,
                template_id,
            )
            # Build config from template metadata
            # type must be a valid StrategyType enum value, use "custom" for templates
            config = StrategyConfig(
                id=template_id,
                name=template_meta.name,
                type=StrategyType.CUSTOM,
                description=template_meta.description,
                symbols=body.symbol and [body.symbol] or template_meta.symbols,
                interval=body.interval or template_meta.interval,
                parameters={**template_meta.default_params},
                risk_params=template_meta.risk_params,
            )
            # The catalog key (``sma_cross``) is not the id its rule config is
            # seeded under (``template-sma-cross``), so the config above carries
            # no entry rules and the run would complete with zero trades. Pull
            # them from the seeded twin; it is absent for a catalog row with no
            # implementation, and the no-entry-rules guard below catches that.
            seeded = await _get_strategy(template_meta.id, user_id=task.user_id)
            if seeded is not None:
                config = config.model_copy(
                    update={
                        "entry_rules": seeded.entry_rules,
                        "exit_rules": seeded.exit_rules,
                    }
                )
        elif config is not None and template_meta is not None:
            # Template ID found in cache, but merge template default params if missing
            if not config.parameters:
                logger.info(
                    "Backtest %s: template '%s' found in cache but parameters empty, merging defaults",
                    task.task_id,
                    template_id,
                )
                config = config.model_copy(
                    update={"parameters": {**template_meta.default_params}}
                )

        if config is None:
            raise ValueError(f"Strategy '{body.strategy_id}' not found")

        task.strategy_name = config.name
        # Use request parameters if provided, otherwise fallback to strategy config
        task.symbol = body.symbol or (config.symbols[0] if config.symbols else "")
        task.interval = body.interval or config.interval or "1h"

        # FX12: Detect Python template strategies and use BacktestStrategyTemplate
        # Template strategies like ma_extended_runs use BacktestStrategyTemplate pattern
        # which requires different handling than YAML rule-based strategies

        # Check multiple sources for template ID:
        # 1. Direct template ID in strategy_id (e.g., "bb_v3")
        # 2. Template ID stored in source field (e.g., "template:bb_v3")
        # 3. Strategy parameters that match known Python template strategies
        original_template_id = None
        if template_id in STRATEGY_TEMPLATES:
            original_template_id = template_id
        elif config.source and config.source.startswith("template:"):
            original_template_id = config.source.split(":", 1)[1]

        # Check if parameters match a known Python template strategy
        if original_template_id is None and config.parameters:
            # Detect by parameter signature (unique params for each strategy)
            param_keys = set(config.parameters.keys())
            if {"bb_len", "buy_mult", "sell_mult"}.issubset(param_keys):
                original_template_id = "bb_v3"
            elif {"ma_len", "deviation", "breakthrough_lookback"}.issubset(param_keys):
                original_template_id = "ma_extended_runs"
            elif {"fast_ma", "slow_ma", "grid_spacing"}.issubset(param_keys):
                original_template_id = "dca_grid"

        effective_template_id = original_template_id
        strategy_type = effective_template_id or config.type
        template_meta = STRATEGY_TEMPLATES.get(strategy_type)
        uses_python_template = (
            template_meta is not None and strategy_type in _PRESET_STRATEGY_MODULES
        )

        strategy = None  # Will be set below based on strategy type

        logger.warning(
            "[DEBUG] uses_python_template=%s, strategy_type=%s, effective_template_id=%s",
            uses_python_template,
            strategy_type,
            effective_template_id,
        )

        if uses_python_template:
            # Python template strategy path - use BacktestStrategyTemplate
            logger.info(
                "Backtest %s: using Python template strategy %s",
                task.task_id,
                strategy_type,
            )
            strategy = _create_python_template_strategy(
                strategy_type,
                config.parameters,
                body,
                task.symbol,
            )
        elif body.strategy_id.startswith("alpha:"):
            # Alpha formula strategy path - load from data/strategies/
            from selene_space_alpha.strategy_bridge import (
                AlphaFormulaStrategy,
                resolve_artifact_vocab,
            )

            alpha_name = body.strategy_id.split(":", 1)[1]
            logger.info(
                "Backtest %s: using alpha formula strategy %s",
                task.task_id,
                alpha_name,
            )

            strategy_data = _find_alpha_archive(alpha_name)
            if strategy_data is None:
                raise ValueError(
                    f"Alpha strategy '{alpha_name}' not found in data/strategies/"
                )

            formula = strategy_data.get("formula") or strategy_data.get(
                "best_formula", {}
            )
            tokens = formula.get("tokens", [])
            if not tokens:
                raise ValueError(f"Alpha strategy '{alpha_name}' has no formula tokens")

            buy_th = formula.get("buy_th", config.parameters.get("buy_th", 0.57))
            sell_th = formula.get("sell_th", config.parameters.get("sell_th", 0.41))

            # Override from request parameters
            buy_th = body.parameters.get("buy_th", buy_th)
            sell_th = body.parameters.get("sell_th", sell_th)

            # Prefer the artifact's own vocab block (token indices are stable
            # only against the vocab it was mined with); fall back to the
            # legacy version-tag resolution for old archives without one.
            vocab_block = (strategy_data.get("vocab") or {}).get("feature_names")
            if vocab_block:
                vocab = resolve_artifact_vocab(strategy_data)
            else:
                from selene_space_alpha.vocab import FormulaVocab
                from app.api.v1.alpha import _pandas_vocab_for_version

                feature_names, op_names = _pandas_vocab_for_version(
                    strategy_data.get("version")
                )
                vocab = FormulaVocab(
                    feature_names=feature_names,
                    operator_names=op_names,
                )

            strategy = AlphaFormulaStrategy(
                formula_tokens=tokens,
                vocab=vocab,
                buy_threshold=buy_th,
                sell_threshold=sell_th,
                name=alpha_name,
            )
            task.strategy_name = f"Alpha: {strategy.formula_readable}"
        elif body.strategy_id.startswith("qd:"):
            # QuantDinger exported script strategy — the source runs as-is
            # through the cleanroom script-runtime adapter (no translation).
            from selene_space_strategy.qd import QDStrategyRuntime, compile_qd_strategy
            from selene_space_strategy.qd.catalog import resolve_qd_source

            source_path = resolve_qd_source(body.strategy_id)
            qd_compiled = compile_qd_strategy(
                source_path.read_text(encoding="utf-8"),
                source_name=source_path.stem.split("_", 1)[0],
            )
            strategy = QDStrategyRuntime(
                qd_compiled, symbol=task.symbol, interval=task.interval
            )
            logger.info(
                "Backtest %s: using QD script strategy %s (%s)",
                task.task_id,
                body.strategy_id,
                source_path.name,
            )
        elif body.strategy_id.startswith("bridge:"):
            # Mined artifact — docstring header declares the formula as
            # names; artifact_runtime resolves them to selene tokens.
            from selene_space_alpha.artifact_bridge import UnsupportedArtifact
            from selene_space_alpha.artifact_runtime import load_artifact_strategy

            artifact_name = body.strategy_id[len("bridge:") :]
            try:
                buy_th = config.parameters.get("buy_th")
                sell_th = config.parameters.get("sell_th")
                buy_th = body.parameters.get("buy_th", buy_th)
                sell_th = body.parameters.get("sell_th", sell_th)
                strategy = load_artifact_strategy(
                    artifact_name,
                    buy_th=buy_th,
                    sell_th=sell_th,
                )
            except UnsupportedArtifact as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            task.strategy_name = f"Bridge: {artifact_name}"
        else:
            # YAML rule-based strategy path - use StrategyRuntime
            engine_config = EngineStrategyConfig.model_validate(config.model_dump())

            # FX11: Parse string-format entry/exit rules into ConditionRule objects.
            from selene_space_strategy.rule_parser import (
                parse_entry_rules,
                parse_exit_rules,
            )

            if (
                not engine_config.parsed_entry_rules.long
                and not engine_config.parsed_entry_rules.short
            ):
                if engine_config.entry_rules:
                    parsed_entry = parse_entry_rules(engine_config.entry_rules)
                    engine_config = engine_config.model_copy(
                        update={"parsed_entry_rules": parsed_entry}
                    )
            if (
                not engine_config.parsed_exit_rules.close_long
                and not engine_config.parsed_exit_rules.close_short
            ):
                if engine_config.exit_rules:
                    parsed_exit = parse_exit_rules(engine_config.exit_rules)
                    engine_config = engine_config.model_copy(
                        update={"parsed_exit_rules": parsed_exit}
                    )

            # No entry conditions means the strategy can never open a position.
            # ``compile`` accepts that, and the runtime's entry test is
            # ``conditions and all(...)`` — an empty list is falsy — so the run
            # would complete with zero trades and no error, which is
            # indistinguishable from a legitimately flat result. This is how a
            # catalog row with no implementation reaches the user as a flat
            # line, so fail loudly instead.
            if (
                not engine_config.parsed_entry_rules.long
                and not engine_config.parsed_entry_rules.short
            ):
                raise ValueError(
                    f"Strategy '{body.strategy_id}' has no entry rules, so it "
                    "cannot be backtested. This usually means the template is "
                    "listed but has no implementation."
                )

            if (
                engine_config.risk_params
                and engine_config.parsed_risk_params.stop_loss_pct is None
            ):
                from selene_space_strategy.models import RiskParams

                try:
                    parsed_rp = RiskParams.model_validate(engine_config.risk_params)
                    engine_config = engine_config.model_copy(
                        update={"parsed_risk_params": parsed_rp}
                    )
                except Exception:
                    pass

            logger.info(
                "Backtest %s: parsed %d long + %d short entry rules, %d close_long + %d close_short exit rules",
                task.task_id,
                len(engine_config.parsed_entry_rules.long),
                len(engine_config.parsed_entry_rules.short),
                len(engine_config.parsed_exit_rules.close_long),
                len(engine_config.parsed_exit_rules.close_short),
            )

            # P8: Apply parameter overrides from request body
            if body.parameters:
                merged = {**engine_config.parameters, **body.parameters}
                engine_config = engine_config.model_copy(update={"parameters": merged})

            compiled = compile_strategy(engine_config)
            # FX06: Pass strategy direction to runtime
            from selene_space_types.strategy import StrategyDirection

            direction = getattr(config, "direction", StrategyDirection.LONG_ONLY)
            strategy = StrategyRuntime(compiled, direction=direction)

        # Pass body with exchange/market_type/symbol/interval overrides
        if body.strategy_id.startswith("qd:"):
            kline_df = await _load_qd_kline_data(
                body.strategy_id, task.symbol, task.interval
            )
        else:
            kline_df = await _load_kline_data(config, body)

        # FX10: Apply date range filter with proper error reporting
        if body.start_date or body.end_date:
            if "timestamp" in kline_df.columns:
                if body.start_date:
                    try:
                        start_ts = int(pd.Timestamp(body.start_date).timestamp() * 1000)
                        kline_df = kline_df[kline_df["timestamp"] >= start_ts]
                    except (ValueError, TypeError) as exc:
                        raise ValueError(
                            f"Invalid start_date format '{body.start_date}': {exc}"
                        ) from exc
                if body.end_date:
                    try:
                        end_ts = int(pd.Timestamp(body.end_date).timestamp() * 1000)
                        kline_df = kline_df[kline_df["timestamp"] <= end_ts]
                    except (ValueError, TypeError) as exc:
                        raise ValueError(
                            f"Invalid end_date format '{body.end_date}': {exc}"
                        ) from exc

        # A QD export assumes 0.05% slippage and emits target weights, which
        # its runtime has already converted to quantities; same-side adds are
        # how it pyramids. Every other strategy keeps the engine defaults.
        from selene_space_backtest.slippage import FixedSlippage, RealisticSlippage

        qd_engine_overrides: dict[str, Any] = {}
        slippage_model: FixedSlippage | None
        if body.strategy_id.startswith("qd:"):
            qd_engine_overrides = {
                "position_sizing": "signal_quantity",
                "allow_pyramiding": True,
            }
            slippage_model = FixedSlippage(bps=5.0)
        else:
            slippage_model = (
                FixedSlippage(bps=body.slippage_bps)
                if body.slippage_bps is not None
                else None  # BacktestConfig default = RealisticSlippage
            )
        bt_config = BacktestConfig(
            initial_cash=body.effective_cash,
            commission=PercentageCommission(rate=body.commission_rate),
            slippage=slippage_model or RealisticSlippage(),
            strategy_id=config.id,
            symbol=task.symbol,
            interval=task.interval,
            **qd_engine_overrides,
        )
        engine = BacktestEngine(config=bt_config)

        # US-105: 引擎每 ~5% 吐 (done,total),闭包写内存 task.progress,
        # GET /backtests/{task_id} 轮询读 —— 不落库,GIL 下 int 赋值原子。
        def _on_progress(done: int, total: int) -> None:
            task.progress = int(done / total * 100) if total else 0

        bt_config.progress_cb = _on_progress

        # Full-series fast path: causal formulas score identically from one
        # vectorized pass (prepare_full_series self-verifies and declines on
        # any mismatch), turning bar-by-bar VM recompute into O(1) lookups.
        if hasattr(strategy, "prepare_full_series") and isinstance(
            kline_df, pd.DataFrame
        ):
            try:
                if strategy.prepare_full_series(kline_df):
                    logger.info(
                        "Backtest %s: full-series fast path armed (%d bars)",
                        task.task_id,
                        len(kline_df),
                    )
            except Exception:
                logger.warning(
                    "Backtest %s: fast-path prepare failed, using bar-by-bar",
                    task.task_id,
                    exc_info=True,
                )

        logger.warning(
            "[CRITICAL] About to run backtest: strategy_type=%s, uses_python_template=%s, kline_df_shape=%s, initial_cash=%s",
            strategy_type if "strategy_type" in dir() else "N/A",
            uses_python_template if "uses_python_template" in dir() else "N/A",
            kline_df.shape if hasattr(kline_df, "shape") else len(kline_df),
            bt_config.initial_cash,
        )
        logger.warning(
            "[CRITICAL] Strategy object: %s, has run_backtest=%s, has on_kline=%s",
            type(strategy).__name__ if strategy else "None",
            hasattr(strategy, "run_backtest") if strategy else False,
            hasattr(strategy, "on_kline") if strategy else False,
        )
        result = await asyncio.to_thread(engine.run, strategy, kline_df)
        logger.warning(
            "[DEBUG] Backtest done: result type=%s, trades_count=%s, metrics=%s",
            type(result).__name__,
            result.trades_count,
            result.metrics,
        )

        # FX05: Unify task_id and result.id so frontend can always look up by task_id
        result.id = task.task_id

        # Execution assumptions echo — detail dialog assumption strip renders these.
        # fill_rule verified: engine queues on signal-bar close, fills next bar open.
        # exchange/market_type: trade-review K-line refetch source (spot/futures prices differ).
        result.config_snapshot = {
            "initial_capital": body.effective_cash,
            "commission_rate": body.commission_rate,
            "slippage_bps": body.slippage_bps,
            "fill_rule": "next_bar_open",
            "exchange": body.exchange,
            "market_type": body.market_type,
        }

        task.result = result
        task.status = BacktestTaskStatus.COMPLETED

        # Store in unified results cache
        from selene_space_agent.tools.strategy_tools import _evict_oldest_results

        _get_results_store()[result.id] = result
        _result_owners[result.id] = task.user_id
        _evict_oldest_results()

        db = get_db_manager()
        if db is not None:
            try:
                from selene_space_storage.repositories.backtests import (
                    BacktestRepository,
                )

                result_repo = BacktestRepository(db)
                await result_repo.save(result, user_id=task.user_id)
            except Exception:
                logger.warning("Failed to persist backtest result to DB", exc_info=True)
        else:
            logger.warning("No DB manager available — backtest result only in memory")

        # Persist terminal state: running -> completed with the result id.
        # The result row's id is the task id (set earlier as result.id = task.task_id),
        # so result_id links back to the persisted backtests row.
        if task_repo is not None:
            try:
                await task_repo.mark_completed(task.task_id, result.id)
            except Exception:
                logger.debug(
                    "Failed to mark task %s completed in DB",
                    task.task_id,
                    exc_info=True,
                )

        # Episode ledger: one row per completed API backtest. Failure here
        # must never fail the backtest itself.
        await _record_episode(task, body, result)

        # 透明性批2-6: 实验完成 → 该策略开放 mission 的 mission_activity
        from app.api.v1.research_missions import notify_experiment_completed

        await notify_experiment_completed(
            "strategy",
            body.strategy_id,
            task_id=task.task_id,
            experiment_kind="backtest",
            summary={
                "symbol": task.symbol,
                "interval": task.interval,
                "total_return": result.metrics.total_return,
                "sharpe_ratio": result.metrics.sharpe_ratio,
                "total_trades": result.metrics.total_trades,
                "win_rate": result.metrics.win_rate,
            },
        )

        # 透明性批3-12: 系统事件复活 events_recent → FeedBar 系统事件 tab
        from app.api.v1.events import record_system_event

        await record_system_event(
            "task_completed",
            "backtest",
            f"回测完成 {task.symbol} {task.interval} ({body.strategy_id})",
            severity="info",
            symbol=task.symbol,
            payload={
                "task_id": task.task_id,
                "total_return": result.metrics.total_return,
                "sharpe_ratio": result.metrics.sharpe_ratio,
                "total_trades": result.metrics.total_trades,
            },
        )

        logger.info(
            "Backtest %s completed: trades=%d, return=%.4f",
            task.task_id,
            result.trades_count,
            result.metrics.total_return,
        )

    except Exception as exc:
        import traceback

        logger.warning("[ERROR] Backtest failed: %s", exc)
        traceback.print_exc()
        logger.warning("Backtest %s failed: %s", task.task_id, exc, exc_info=True)
        task.error = str(exc)
        task.status = BacktestTaskStatus.FAILED
        if task_repo is not None:
            try:
                await task_repo.mark_failed(task.task_id, str(exc))
            except Exception:
                logger.debug(
                    "Failed to mark task %s failed in DB", task.task_id, exc_info=True
                )


async def _load_kline_data(
    config: Any,
    body: RunBacktestRequest,
) -> pd.DataFrame:
    """Load kline data from local SQLite, explicit path, market service, or demo data.

    Priority:
    1. data_path (explicit file) -> 2. Local SQLite -> 3. REST API -> 4. Demo data
    """
    # -------------------------------------------------------------------------
    # 1. Explicit data_path (unchanged)
    # -------------------------------------------------------------------------
    if body.data_path:
        path = Path(body.data_path)
        if not path.is_absolute():
            path = Path.cwd() / path
        safe_root = Path.home() / ".selenespace" / "data"
        demo_root = Path.cwd() / "demo" / "data"
        try:
            resolved = path.resolve()
            if not (
                str(resolved).startswith(str(safe_root.resolve()))
                or str(resolved).startswith(str(demo_root.resolve()))
            ):
                raise PermissionError(
                    f"data_path must be under {safe_root} or {demo_root}"
                )
        except (OSError, ValueError) as exc:
            raise PermissionError(f"Invalid data_path: {exc}") from exc
        if path.exists() and path.suffix == ".parquet":
            return pd.read_parquet(path)
        raise FileNotFoundError(f"Data file not found: {body.data_path}")

    # -------------------------------------------------------------------------
    # 2. Local SQLite (NEW: priority before REST API)
    # -------------------------------------------------------------------------
    symbol = body.symbol or (config.symbols[0] if config.symbols else "BTC/USDT")
    interval = body.interval or config.interval or "1h"
    exchange = body.exchange or "binance"
    market_type = body.market_type or "futures"

    # Try local SQLite first
    try:
        from selene_space_market.kline_db import KlineDB

        db = KlineDB(db_path=str(default_klines_db_path()))
        start_date = None
        end_date = None
        if body.start_date:
            start_date = datetime.strptime(body.start_date, "%Y%m%d")
        if body.end_date:
            end_date = datetime.strptime(body.end_date, "%Y%m%d")

        # Check if local DB has enough data for this partition
        if db.has_data(exchange, market_type, symbol, interval, min_candles=10):
            df = db.get_klines(
                exchange,
                market_type,
                symbol,
                interval,
                start_date=start_date,
                end_date=end_date,
            )
            if len(df) >= 2:
                logger.info(
                    "Loaded %d klines from local SQLite for %s %s (source=local)",
                    len(df),
                    symbol,
                    interval,
                )
                return df
            logger.debug(
                "Local DB has %d klines for %s %s, need more (falling back to REST)",
                len(df),
                symbol,
                interval,
            )
    except ImportError:
        logger.debug("KlineDB not available (module not installed)")
    except Exception as exc:
        logger.debug("Local SQLite check failed: %s", exc)

    # -------------------------------------------------------------------------
    # 3. REST API (fallback when SQLite has no data)
    # -------------------------------------------------------------------------
    market_svc = get_market_service()
    if market_svc is not None:
        try:
            klines = None

            # If specific exchange/market_type is requested, try that first
            if body.exchange and body.market_type:
                try:
                    klines = await market_svc.fetch_klines_rest(
                        symbol=symbol,
                        exchange=body.exchange,
                        market_type=body.market_type,
                        interval=interval,
                        limit=1500,
                    )
                    if klines:
                        logger.info(
                            "Fetched %d klines from %s/%s for %s (interval=%s)",
                            len(klines),
                            body.exchange,
                            body.market_type,
                            symbol,
                            interval,
                        )
                except Exception as src_err:
                    logger.warning(
                        "Failed to fetch from %s/%s: %s",
                        body.exchange,
                        body.market_type,
                        src_err,
                    )

            # If no specific source or failed, try all sources
            if not klines:
                sources_to_try = [
                    ("binance", "spot"),
                    ("binance", "futures"),
                    ("okx", "spot"),
                    ("okx", "futures"),
                ]
                for ex, mt in sources_to_try:
                    try:
                        klines = await market_svc.fetch_klines_rest(
                            symbol=symbol,
                            exchange=ex,
                            market_type=mt,
                            interval=interval,
                            limit=1500,
                        )
                        if klines:
                            logger.info(
                                "Fetched %d klines from %s/%s for %s (interval=%s)",
                                len(klines),
                                ex,
                                mt,
                                symbol,
                                interval,
                            )
                            break
                    except Exception as src_err:
                        logger.debug("Failed to fetch from %s/%s: %s", ex, mt, src_err)
                        continue

            if klines:
                # Save fetched klines to local SQLite for future offline use
                try:
                    from selene_space_market.kline_db import KlineDB

                    db = KlineDB(db_path=str(default_klines_db_path()))
                    db.put_klines(klines)
                    logger.info(
                        "Fetched %d klines via REST for %s %s and saved to local SQLite",
                        len(klines),
                        symbol,
                        interval,
                    )
                except Exception as db_err:
                    logger.debug("Failed to write to local SQLite: %s", db_err)

                rows = [
                    {
                        "timestamp": k.timestamp,
                        "open": k.open,
                        "high": k.high,
                        "low": k.low,
                        "close": k.close,
                        "volume": k.volume,
                        "symbol": k.symbol,
                    }
                    for k in klines
                ]
                return pd.DataFrame(rows)
            else:
                logger.warning(
                    "No klines fetched from any source for %s (interval=%s)",
                    symbol,
                    interval,
                )
        except Exception:
            logger.warning(
                "REST kline fetch failed, falling back to demo data", exc_info=True
            )

    demo_paths = [
        Path("demo/data/btc_usdt_1h_90d.parquet"),
        Path("demo/data/eth_usdt_1h_90d.parquet"),
    ]
    symbol_lower = symbol.lower().replace("/", "_")
    for dp in demo_paths:
        if symbol_lower in dp.name and dp.exists():
            return pd.read_parquet(dp)
    for dp in demo_paths:
        if dp.exists():
            return pd.read_parquet(dp)

    raise FileNotFoundError(
        f"No kline data available for {symbol}. Provide data_path or ensure the exchange is reachable."
    )


def _resolve_strategy_symbol_interval(strategy_id: str) -> tuple[str, str]:
    """Best-effort lookup of symbol/interval from strategy cache."""
    from app.api.v1.strategies import _strategies

    config = _strategies.get(strategy_id)
    if config is not None:
        symbol = config.symbols[0] if config.symbols else ""
        return symbol, config.interval or "1h"
    return "", "1h"


def _hurst_interpretation(value: float) -> str:
    if value < 0.5:
        return "均值回归特性 (适合配对交易)"
    if value > 0.5:
        return "趋势持续特性"
    return "随机游走"


def _classify_regime(annual_return: float, volatility: float) -> str:
    """按年化收益+波动分档(与 regime 端点同一套阈值)。"""
    if annual_return > 0.15 and volatility < 0.25:
        return "bull"
    if annual_return < -0.15 and volatility < 0.35:
        return "bear"
    if volatility > 0.35:
        return "transition"
    return "sideways"


def _last_valid(series: Any, pd: Any) -> float | None:
    """序列末值; 空或 NaN 则为 None。"""
    if len(series) == 0:
        return None
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)


def _compute_custom_metrics(
    result: BacktestResult, equity_curve: list[float]
) -> dict[str, Any]:
    """Compute custom metrics from strategy engine.

    Returns statistical and volume indicators computed from backtest data.
    """
    import numpy as np

    custom_metrics: dict[str, Any] = {}

    if len(equity_curve) < 20:
        return custom_metrics

    try:
        import pandas as pd
        from selene_space_strategy.indicators.statistical import (
            HurstExponent,
            ZScoreIndicator,
        )

        eq_df = pd.DataFrame({"close": equity_curve})
        hurst_val = _last_valid(HurstExponent(max_lag=20).calculate(eq_df), pd)
        if hurst_val is not None:
            custom_metrics["hurst"] = hurst_val
            custom_metrics.setdefault("interpretations", {})["hurst"] = (
                _hurst_interpretation(hurst_val)
            )

        zscore_val = _last_valid(ZScoreIndicator(window=20).calculate(eq_df), pd)
        if zscore_val is not None:
            custom_metrics["zscore"] = zscore_val

        # Detect market regime from equity curve returns
        returns = pd.Series(equity_curve).pct_change().dropna()
        if len(returns) >= 20:
            custom_metrics["regime"] = _classify_regime(
                returns.mean() * 252, returns.std() * np.sqrt(252)
            )

    except Exception:
        logger.debug("Failed to compute custom metrics", exc_info=True)

    return custom_metrics


async def _ensure_strategies_loaded(user_id: str = "local") -> None:
    """Pre-warm _strategies cache from DB for a specific user."""
    from app.api.v1.strategies import _strategies

    try:
        from app.core.dependencies import get_strategy_repo

        repo = get_strategy_repo()
        if repo is not None:
            uid = user_id if user_id != "local" else None
            configs = await repo.list(limit=10000, offset=0, user_id=uid)
            for config in configs:
                _strategies[config.id] = config
    except Exception:
        logger.debug("Failed to pre-warm strategies cache", exc_info=True)


def _derive_drawdown_curve(eq: list[float]) -> list[float]:
    """没存 drawdown 时从净值序列现算(peak-to-trough)。"""
    if len(eq) < 2:
        return []
    import numpy as np

    arr = np.asarray(eq, dtype=np.float64)
    peak = np.maximum.accumulate(arr)
    return [round(v, 8) for v in ((arr - peak) / peak).tolist()]


def _derive_buy_hold_curve(eq: list[float]) -> list[float]:
    """没存 benchmark 时按起止点线性插值出买入持有曲线。"""
    if len(eq) < 2:
        return []
    start_val, end_val = eq[0], eq[-1]
    ratio = end_val / start_val if start_val else 1.0
    span = len(eq) - 1
    return [
        round(start_val * (1 + (ratio - 1) * (i / span)), 2) for i in range(len(eq))
    ]


def _first_trade_symbol(trades: list[Any]) -> str | None:
    for trade in trades:
        sym = (
            trade.get("symbol")
            if isinstance(trade, dict)
            else getattr(trade, "symbol", None)
        )
        if sym:
            return str(sym)
    return None


def _curve_timestamps(result: BacktestResult, n_points: int) -> list[int]:
    """按起止日期在净值点数上均匀铺时间戳(ms); 缺日期或时长非正则为空。"""
    if n_points <= 0 or not result.start_date or not result.end_date:
        return []
    start_ts = int(result.start_date.timestamp() * 1000)
    end_ts = int(result.end_date.timestamp() * 1000)
    if end_ts <= start_ts:
        return []
    step = (end_ts - start_ts) / (n_points - 1) if n_points > 1 else 0
    return [int(start_ts + i * step) for i in range(n_points)]


#: 前端直接透传的指标字段(键名 == BacktestMetrics 属性名)。
_METRIC_FIELDS = (
    "total_return",
    "annual_return",
    "sharpe_ratio",
    "max_drawdown",
    "win_rate",
    "profit_factor",
    "total_trades",
    "calmar_ratio",
    "sortino_ratio",
    "expectancy",
    "recovery_factor",
)


def _result_to_frontend_dict(
    result: BacktestResult, *, light: bool = False
) -> dict[str, Any]:
    """Convert persisted/shared backtest result to frontend-compatible dict.

    ``light=True`` drops the curve/trade/config payload for list rows — the
    table renders only scalars, and shipping every row's curves is what pushed
    the listing past the client abort. Detail callers use the default (full).
    """
    m = result.metrics
    eq = result.equity_curve

    drawdown_curve = result.drawdown_curve or _derive_drawdown_curve(eq)
    buy_hold_curve = result.buy_hold_curve or _derive_buy_hold_curve(eq)

    symbol = result.symbol
    interval = result.interval

    if not symbol or not interval:
        fallback_sym, fallback_int = _resolve_strategy_symbol_interval(
            result.strategy_id
        )
        symbol = symbol or fallback_sym
        interval = interval or fallback_int

    if not symbol and result.trades:
        symbol = _first_trade_symbol(result.trades)

    base: dict[str, Any] = {
        "id": result.id,
        "task_id": result.id,
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "strategy_name": result.strategy_id,
        "symbol": symbol,
        "interval": interval,
        "status": "completed",
        "created_at": result.created_at,
        "start_date": result.start_date.isoformat() if result.start_date else None,
        "end_date": result.end_date.isoformat() if result.end_date else None,
        **{field: getattr(m, field) for field in _METRIC_FIELDS},
    }
    if light:
        return base
    timestamps = _curve_timestamps(result, len(eq))
    base.update(
        {
            "timestamps": timestamps,
            "equity_curve": eq,
            "drawdown_curve": drawdown_curve,
            "buy_hold_curve": buy_hold_curve,
            "exposure_curve": result.exposure_curve,
            "trades": result.trades,
            "lookahead_warnings": result.lookahead_warnings,
            "config_snapshot": result.config_snapshot,
            "result": {
                **result.model_dump(mode="json"),
                "timestamps": timestamps,
            },
            "trades_count_debug": result.trades_count,
        }
    )
    return base


def _summary_to_frontend_dict(summary: dict[str, Any]) -> dict[str, Any]:
    """List-row dict from a light repository summary (no curve/trade payload).

    Mirrors the scalar subset of :func:`_result_to_frontend_dict` so list rows
    keep the same field names the table reads, without decoding the whole
    table's curve JSON on every listing request.
    """
    m = summary.get("metrics") or {}
    symbol = summary.get("symbol") or ""
    interval = summary.get("interval") or "1h"
    if not symbol or not interval:
        fallback_sym, fallback_int = _resolve_strategy_symbol_interval(
            summary["strategy_id"]
        )
        symbol = symbol or fallback_sym
        interval = interval or fallback_int
    return {
        "id": summary["id"],
        "task_id": summary["id"],
        "strategy_id": summary["strategy_id"],
        "strategy_version": summary["strategy_version"],
        "strategy_name": summary["strategy_id"],
        "symbol": symbol,
        "interval": interval,
        "status": "completed",
        "created_at": summary["created_at"],
        "start_date": summary["start_date"],
        "end_date": summary["end_date"],
        **{field: m.get(field) for field in _METRIC_FIELDS},
        "trades_count_debug": summary.get("trades_count"),
    }


def _task_to_frontend_dict(task: BacktestTask) -> dict[str, Any]:
    """Convert task to frontend-compatible dict with flattened metrics."""
    data: dict[str, Any] = {
        "id": task.task_id,
        "task_id": task.task_id,
        "strategy_id": task.strategy_id,
        "strategy_name": task.strategy_name,
        "symbol": task.symbol,
        "interval": task.interval,
        "status": task.status.value,
        "created_at": task.created_at,
        "error": task.error,
    }
    # US-105: 进度只在非终态有意义(0-100);completed/failed 不透传避免前端读脏值
    if task.status not in (BacktestTaskStatus.COMPLETED, BacktestTaskStatus.FAILED):
        data["progress"] = task.progress
    if task.result is not None:
        result_data = _result_to_frontend_dict(task.result)
        result_data["id"] = task.task_id
        result_data["task_id"] = task.task_id
        if task.strategy_name:
            result_data["strategy_name"] = task.strategy_name
        if task.symbol:
            result_data["symbol"] = task.symbol
        if task.interval:
            result_data["interval"] = task.interval
        data.update(result_data)
    return data


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=202)
async def start_backtest(
    request: Request,
    body: RunBacktestRequest,
    user: AuthenticatedUser = Depends(optional_user),
    _ent: None = Depends(require_active_entitlement),
) -> APIResponse[dict[str, Any]]:
    """Start a backtest (async).  Returns 202 with a task_id."""
    # Concurrency cap: in-memory cache + SQLite dual count, take the conservative max.
    active_count = sum(
        1
        for t in _tasks.values()
        if t.user_id == user.id
        and t.status in (BacktestTaskStatus.PENDING, BacktestTaskStatus.RUNNING)
    )
    repo = _get_task_repo()
    if repo is not None:
        try:
            active_count = max(active_count, await repo.count_active(user.id))
        except Exception:
            logger.debug("Failed to count active tasks in DB", exc_info=True)
    if active_count >= _MAX_CONCURRENT_PER_USER:
        raise HTTPException(
            429, f"Too many active backtests ({_MAX_CONCURRENT_PER_USER} max)"
        )

    task_id = f"bt-{uuid.uuid4().hex[:8]}"
    task = BacktestTask(
        task_id=task_id,
        strategy_id=body.strategy_id,
        user_id=user.id,
        # Seed from the request: the runner later overwrites these with the
        # strategy-config fallbacks, but until then (and if the run dies before
        # reaching that point) the list must show what was actually asked for
        # rather than the dataclass defaults.
        symbol=body.symbol or "",
        interval=body.interval or "",
    )
    _tasks[task_id] = task
    _evict_oldest_tasks()

    # Persist a pending row first (durable truth), then launch the background task.
    if repo is not None:
        try:
            from selene_space_storage.repositories.backtest_tasks import BacktestTaskRow

            # Carry the requested market coordinates onto the durable row. The
            # row is what the list falls back to for pending/running/failed
            # tasks, and it outlives the process; dropping symbol/interval here
            # leaves it claiming the column defaults (symbol="", interval="1h"),
            # so a 4h run is listed as 1h forever.
            await repo.create(
                BacktestTaskRow(
                    task_id=task_id,
                    user_id=user.id,
                    strategy_id=body.strategy_id,
                    symbol=body.symbol or "",
                    interval=body.interval or "",
                    created_at=task.created_at,
                )
            )
        except Exception:
            logger.warning(
                "Failed to persist backtest task %s to DB (in-memory only)",
                task_id,
                exc_info=True,
            )

    asyncio.create_task(_run_backtest(task, body))
    logger.warning(
        "[POST] Created backtest task %s for strategy_id=%s", task_id, body.strategy_id
    )

    return _ok(request, {"task_id": task_id, "status": task.status.value})


@router.get("/{task_id}")
async def get_backtest(
    task_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get backtest task status and result (if completed)."""
    await _ensure_strategies_loaded(user.id)
    task = _tasks.get(task_id)
    if task is not None:
        if user.id != "local" and task.user_id != user.id:
            raise NotFoundError(f"Backtest task '{task_id}' not found")
        return _ok(request, _task_to_frontend_dict(task))

    # Check unified results store
    cached = _get_results_store().get(task_id)
    if cached is not None:
        owner = _result_owners.get(task_id)
        if user.id != "local" and (owner is None or owner != user.id):
            raise NotFoundError(f"Backtest task '{task_id}' not found")
        return _ok(request, _result_to_frontend_dict(cached))

    # Fall back to the persistent result row FIRST — it carries the metrics,
    # trades and curves. For a completed task ``backtest_tasks.result_id`` equals
    # the ``backtests.id`` (set in _run_backtest), so the rich row is keyed by the
    # same id; consulting the thin task table first would shadow it with a
    # 9-field stub and the detail view would render blank.
    db = get_db_manager()
    if db is not None:
        try:
            from selene_space_storage.repositories.backtests import BacktestRepository

            persisted = await BacktestRepository(db).get(task_id, user_id=user.id)
            if persisted is not None:
                payload = _result_to_frontend_dict(persisted)
                await _hydrate_strategy_names([payload])
                return _ok(request, payload)
        except Exception:
            logger.debug("Persisted backtest lookup failed", exc_info=True)

    # Then the durable task row (covers pending/running/failed, and completed
    # tasks whose result row is missing).
    repo = _get_task_repo()
    if repo is not None:
        try:
            row = await repo.get(task_id, user_id=user.id)
            if row is not None:
                payload = _task_row_to_frontend_dict(row)
                await _hydrate_strategy_names([payload])
                return _ok(request, payload)
        except Exception:
            logger.debug("Persisted task lookup failed", exc_info=True)

    raise NotFoundError(f"Backtest task '{task_id}' not found")


async def _collect_persisted_task_rows(
    task_repo: Any,
    items: list[dict[str, Any]],
    seen_ids: set[str],
    strategy_id: str | None,
    user_id: str,
) -> None:
    """把 DB 里的历史任务行并入结果(去重)。"""
    if task_repo is None:
        return
    try:
        for row in await task_repo.list_by_user(user_id, limit=1000, offset=0):
            if strategy_id is not None and row.strategy_id != strategy_id:
                continue
            if row.task_id in seen_ids:
                continue
            items.append(_task_row_to_frontend_dict(row))
            seen_ids.add(row.task_id)
    except Exception:
        logger.debug("Persisted task list lookup failed", exc_info=True)


async def _collect_persisted_backtests(
    items: list[dict[str, Any]],
    seen_ids: set[str],
    strategy_id: str | None,
    user_id: str,
) -> None:
    """把持久化回测结果并入列表(去重)。用轻量列举:列表只渲染标量字段,
    曲线/成交明细留给详情端点按需加载(全表解码曾把本端点推过前端 12s 超时)。"""
    db = get_db_manager()
    if db is None:
        return
    try:
        from selene_space_storage.repositories.backtests import BacktestRepository

        repo = BacktestRepository(db)
        summaries = await repo.list_summaries(
            strategy_id=strategy_id, limit=1000, offset=0, user_id=user_id
        )
        for summary in summaries:
            if summary["id"] in seen_ids:
                continue
            items.append(_summary_to_frontend_dict(summary))
            seen_ids.add(summary["id"])
    except Exception:
        logger.debug("Persisted backtest list lookup failed", exc_info=True)


@router.get("")
async def list_backtests(
    request: Request,
    strategy_id: str | None = Query(None, description="Filter by strategy ID"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List backtest tasks and persisted results, optionally filtered by strategy_id."""
    await _ensure_strategies_loaded(user.id)
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    tasks = list(_tasks.values())
    if user.id != "local":
        tasks = [t for t in tasks if t.user_id == user.id]
    if strategy_id is not None:
        tasks = [t for t in tasks if t.strategy_id == strategy_id]
    for task in tasks:
        payload = _task_to_frontend_dict(task)
        items.append(payload)
        seen_ids.add(str(payload["id"]))

    for result in _get_results_store().values():
        if strategy_id is not None and result.strategy_id != strategy_id:
            continue
        if result.id in seen_ids:
            continue
        owner = _result_owners.get(result.id)
        if user.id != "local" and (owner is None or owner != user.id):
            continue
        items.append(_result_to_frontend_dict(result, light=True))
        seen_ids.add(result.id)

    # Persisted result rows BEFORE task rows: they share the same id for a
    # completed task (result_id == task_id), and the rich result row must win
    # the seen_ids dedup or the list shows a blank strategy_name/sharpe stub.
    await _collect_persisted_backtests(items, seen_ids, strategy_id, user.id)
    await _collect_persisted_task_rows(
        _get_task_repo(), items, seen_ids, strategy_id, user.id
    )

    await _hydrate_strategy_names(items)

    items.sort(key=lambda item: int(item.get("created_at", 0)), reverse=True)
    total = len(items)
    page = items[offset : offset + limit]

    return APIResponse(
        data=page,
        meta=build_response_meta(
            request,
            pagination=Pagination(offset=offset, limit=limit, total=total),
        ),
        error=None,
    )


def _require_backtest_owner(backtest_id: str, user_id: str) -> None:
    """越权访问(任务或结果归属他人)则 404, 不泄露存在性。"""
    task = _tasks.get(backtest_id)
    if task is not None and user_id != "local" and task.user_id != user_id:
        raise NotFoundError(f"Backtest '{backtest_id}' not found")
    owner = _result_owners.get(backtest_id)
    if owner is not None and user_id != "local" and owner != user_id:
        raise NotFoundError(f"Backtest '{backtest_id}' not found")


async def _delete_backtest_record(backtest_id: str, user_id: str) -> None:
    """删内存(任务/归属/结果缓存) + 尽力删 DB。

    两张表都要清:``backtests`` 存的是已完成结果,``backtest_tasks`` 存的是任务
    状态行(含 failed 行)。只删前者时,失败记录会从任务表重新并回列表——用户
    点了删除却仍看得见,是"删不掉"的直接来源。
    """
    _tasks.pop(backtest_id, None)
    _result_owners.pop(backtest_id, None)
    _get_results_store().pop(backtest_id, None)

    db = get_db_manager()
    if db is None:
        return
    try:
        from selene_space_storage.repositories.backtests import BacktestRepository

        await BacktestRepository(db).delete(backtest_id, user_id=user_id)
    except Exception:
        logger.debug("Persisted backtest delete failed", exc_info=True)

    task_repo = _get_task_repo()
    if task_repo is None:
        return
    try:
        await task_repo.delete(backtest_id)
    except Exception:
        logger.debug("Persisted backtest task delete failed", exc_info=True)


@router.delete("/{backtest_id}")
async def delete_backtest(
    backtest_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Delete a backtest by ID (in-memory + persisted)."""
    _require_backtest_owner(backtest_id, user.id)
    await _delete_backtest_record(backtest_id, user.id)
    return APIResponse(data={"deleted": backtest_id})
