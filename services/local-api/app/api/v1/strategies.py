"""Strategy CRUD and validation endpoints.

Strategies are stored in-memory and synced to SQLite via ``selene_space_storage``
when the storage layer is available. On startup, persisted strategies are
loaded into ``_strategies`` by the lifespan handler in ``main.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import (
    AuthenticatedUser,
    build_response_meta,
    get_strategy_repo,
    optional_user,
)
from app.core.pro_helpers import require_pro_feature
from selene_space_types.common import APIResponse, Pagination
from selene_space_types.errors import NotFoundError
from selene_space_types.strategy import (
    StrategyConfig,
    StrategyDeployment,
    StrategyType,
    StrategyVersionSnapshot,
)

MAX_STRATEGIES_PER_USER = 200

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _ok(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


#: 无规则策略的部署拒绝文案(paper 与 live 共用)。
_NO_RULES_ERROR = (
    "Cannot deploy: strategy has no trading rules. "
    "entry_rules and exit_rules are both empty. "
    "Use AI to generate strategy logic or add rules manually first."
)


async def _repo_call(
    action: Callable[[Any], Awaitable[Any]], warn: str, *warn_args: Any
) -> None:
    """有仓储才调用; 失败只记警告(persistence 是尽力而为)。"""
    repo = get_strategy_repo()
    if repo is None:
        return
    try:
        await action(repo)
    except Exception:
        logger.warning(warn, *warn_args, exc_info=True)


# ---------------------------------------------------------------------------
# In-memory store — pre-loaded from DB at startup, synced on each mutation
# ---------------------------------------------------------------------------

_strategies: dict[str, StrategyConfig] = {}
_strategy_owners: dict[str, str] = {}
_strategy_versions: dict[str, list[StrategyVersionSnapshot]] = {}
_strategy_deployments: list[StrategyDeployment] = []
_deploy_lock = asyncio.Lock()


async def _persist_save(config: StrategyConfig, *, user_id: str = "local") -> None:
    """Persist strategy to DB if repository is available."""
    await _repo_call(
        lambda repo: repo.save(config, user_id=user_id),
        "Failed to persist strategy %s",
        config.id,
    )


async def _persist_delete(strategy_id: str, *, user_id: str = "local") -> None:
    """Delete strategy from DB if repository is available."""
    await _repo_call(
        lambda repo: repo.delete(strategy_id, user_id=user_id),
        "Failed to delete strategy %s from DB",
        strategy_id,
    )


# ---------------------------------------------------------------------------
# Request / response bodies
# ---------------------------------------------------------------------------


class CreateStrategyRequest(BaseModel):
    """Body for POST /strategies."""

    name: str = Field(..., min_length=1, description="Strategy name")
    type: StrategyType = Field(..., description="Strategy archetype")
    description: str = Field("", description="Optional description")
    symbols: list[str] = Field(..., min_length=1, description="Trading pairs")
    interval: str = Field("1h", description="Kline interval")
    parameters: dict[str, Any] = Field(default_factory=dict)
    entry_rules: dict[str, Any] = Field(default_factory=dict)
    exit_rules: dict[str, Any] = Field(default_factory=dict)
    risk_params: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source: str = Field("user")


class UpdateStrategyRequest(BaseModel):
    """Body for PUT /strategies/{id} — partial update."""

    name: str | None = Field(None, min_length=1)
    description: str | None = Field(None)
    symbols: list[str] | None = Field(None, min_length=1)
    interval: str | None = Field(None)
    parameters: dict[str, Any] | None = Field(None)
    entry_rules: dict[str, Any] | None = Field(None)
    exit_rules: dict[str, Any] | None = Field(None)
    risk_params: dict[str, Any] | None = Field(None)
    tags: list[str] | None = Field(None)
    source: str | None = Field(None)
    version_note: str | None = Field(
        None, description="Custom note for the version snapshot"
    )


class DeployStrategyRequest(BaseModel):
    """Body for POST /strategies/{id}/deploy-paper."""

    account_id: str = Field("paper-default")


class ValidateStrategyRequest(BaseModel):
    """Body for POST /strategies/validate."""

    name: str = Field("untitled", min_length=1)
    type: StrategyType = Field(...)
    symbols: list[str] = Field(..., min_length=1)
    interval: str = Field("1h")
    parameters: dict[str, Any] = Field(default_factory=dict)
    entry_rules: dict[str, Any] = Field(default_factory=dict)
    exit_rules: dict[str, Any] = Field(default_factory=dict)
    risk_params: dict[str, Any] = Field(default_factory=dict)


class CloneStrategyRequest(BaseModel):
    """Body for POST /strategies/{strategy_id}/clone."""

    name: str | None = Field(
        None, min_length=1, description="Name for the cloned strategy"
    )


class DeployLiveRequest(BaseModel):
    """Body for POST /strategies/{strategy_id}/deploy-live."""

    venue: str = Field("binance", description="Trading venue")
    account_id: str | None = Field(None, description="Live account ID")
    ai_gate_enabled: bool = Field(False, description="Enable AI gate")
    bias_guard_enabled: bool = Field(False, description="Enable bias guard")


class DeploymentFlagsRequest(BaseModel):
    """Body for PATCH /strategies/{strategy_id}/deployment/flags."""

    ai_gate_enabled: bool | None = None
    bias_guard_enabled: bool | None = None


# Module-level deployment flags store
_deployment_flags: dict[str, dict[str, bool]] = {}


def _cache_strategy(config: StrategyConfig, user_id: str) -> StrategyConfig:
    """把读到的策略放进内存缓存并登记归属(不覆盖已有归属)。"""
    _strategies[config.id] = config
    _strategy_owners.setdefault(config.id, user_id)
    return config


def _owner_allows(strategy_id: str, user_id: str) -> bool:
    """本机用户放行一切; 其余只放行自己拥有或未登记的。"""
    if user_id == "local":
        return True
    owner = _strategy_owners.get(strategy_id)
    return owner is None or owner == user_id or owner == "local"


async def _get_strategy(
    strategy_id: str, *, user_id: str = "local"
) -> StrategyConfig | None:
    """Load a strategy, preferring the repository when available.

    Non-local users are denied access to strategies they don't own.
    """
    repo = get_strategy_repo()
    if repo is not None:
        try:
            config = await repo.get(strategy_id, user_id=user_id)
            if config is not None:
                return _cache_strategy(config, user_id)
        except Exception:
            logger.debug(
                "Failed to read strategy %s from repository", strategy_id, exc_info=True
            )
    cached = _strategies.get(strategy_id)
    if cached is None or not _owner_allows(strategy_id, user_id):
        return None
    return cached


async def _require_strategy(strategy_id: str, user_id: str) -> StrategyConfig:
    """取策略; 不存在或越权则 404(不泄露存在性)。"""
    config = await _get_strategy(strategy_id, user_id=user_id)
    if config is None:
        raise NotFoundError(f"Strategy '{strategy_id}' not found")
    return config


async def _downgrade_to_confirmed(strategy_id: str, user_id: str) -> None:
    """停部署后把 running 态的策略退回 confirmed。"""
    config = await _get_strategy(strategy_id, user_id=user_id)
    if config is not None and config.lifecycle_state == "running":
        updated = config.model_copy(update={"lifecycle_state": "confirmed"})
        _strategies[strategy_id] = updated
        asyncio.create_task(_persist_save(updated, user_id=user_id))


def _owned_cached(strategy_id: str, user_id: str) -> StrategyConfig:
    """只查内存缓存的 get-or-404(不做仓储回源), 带归属校验。"""
    cached = _strategies.get(strategy_id)
    if cached is None or not _owner_allows(strategy_id, user_id):
        raise NotFoundError(f"Strategy '{strategy_id}' not found")
    return cached


def _register_deployment_owner(account_id: str, user_id: str, note: str) -> None:
    """登记账户归属(仅供该用户可见); 失败只记 debug。"""
    try:
        from app.api.v1.paper import _register_owner

        _register_owner(account_id, user_id)
    except Exception:
        logger.debug(note, exc_info=True)


async def _has_completed_backtest(strategy_id: str, *, user_id: str) -> bool:
    """True when the backtests table holds at least one row for this strategy.

    The backtests table only receives rows from successful backtests (failed
    runs live in backtest_tasks), so row existence == completed-backtest
    evidence. QD publish-gate semantics: no evidence → no live deployment.
    """
    from app.core.dependencies import get_db_manager
    from selene_space_storage.repositories.backtests import BacktestRepository

    db = get_db_manager()
    if db is None:
        return False
    try:
        repo = BacktestRepository(db)
        persisted = await repo.list_by_strategy(strategy_id, limit=1, user_id=user_id)
        return bool(persisted)
    except Exception:
        logger.debug(
            "Backtest evidence lookup failed for %s, failing closed",
            strategy_id,
            exc_info=True,
        )
        return False


def _tag_set(tags: str) -> set[str]:
    """逗号分隔的标签串 → 小写集合(去空白项)。"""
    return {t.strip().lower() for t in tags.split(",") if t.strip()}


def _filter_by_tags(
    configs: list[StrategyConfig], tags: str | None
) -> list[StrategyConfig]:
    """按标签集合过滤(标签大小写不敏感)。"""
    if not tags:
        return configs
    wanted = _tag_set(tags)
    return [s for s in configs if wanted & {t.lower() for t in getattr(s, "tags", [])}]


def _owned_configs(configs: list[StrategyConfig], user_id: str) -> list[StrategyConfig]:
    """过滤出该用户可见的策略(本机用户全见)。"""
    if user_id == "local":
        return configs
    return [
        s for s in configs if _strategy_owners.get(s.id) in (user_id, "local", None)
    ]


async def _repo_list_strategies(
    *, offset: int, limit: int, tag_list: list[str] | None, user_id: str
) -> list[StrategyConfig] | None:
    """仓储读取; 不可用或失败返回 None(调用方回退内存)。"""
    repo = get_strategy_repo()
    if repo is None:
        return None
    try:
        configs = [
            StrategyConfig.model_validate(item)
            for item in await repo.list(
                limit=limit, offset=offset, tags=tag_list, user_id=user_id
            )
        ]
    except Exception:
        logger.debug("Failed to list strategies from repository", exc_info=True)
        return None
    for config in configs:
        _cache_strategy(config, user_id)
    return configs


async def _list_strategies(
    *,
    offset: int,
    limit: int,
    tags: str | None,
    user_id: str = "local",
) -> list[StrategyConfig]:
    """List strategies, preferring repository-backed reads when available."""
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    configs = await _repo_list_strategies(
        offset=offset, limit=limit, tag_list=tag_list, user_id=user_id
    )
    if configs is not None:
        return configs
    cached = _owned_configs(list(_strategies.values()), user_id)
    return _filter_by_tags(cached, tags)[offset : offset + limit]


async def _with_db_repo(
    loader: Callable[[Any], Any], action: Callable[[Any], Awaitable[Any]], warn: str
) -> Any | None:
    """有 ``repo._db`` 才建对应子仓储并调 action; 失败记 debug 返回 None。

    ``loader(db)`` 惰性构造子仓储(推迟 import, 也避开可选依赖)。
    """
    repo = get_strategy_repo()
    if repo is None or not hasattr(repo, "_db"):
        return None
    try:
        return await action(loader(repo._db))
    except Exception:
        logger.debug(warn, exc_info=True)
        return None


def _version_repo(db: Any) -> Any:
    from selene_space_storage.repositories.strategy_versions import (
        StrategyVersionRepository,
    )

    return StrategyVersionRepository(db)


def _deployment_repo(db: Any) -> Any:
    from selene_space_storage.repositories.strategy_versions import (
        StrategyDeploymentRepository,
    )

    return StrategyDeploymentRepository(db)


async def _save_version_snapshot(config: StrategyConfig, note: str) -> None:
    """Persist a version snapshot when storage is available."""
    snapshot = StrategyVersionSnapshot(
        id=_new_id("sv"),
        strategy_id=config.id,
        version=config.version,
        config_snapshot=config.model_dump(mode="json"),
        note=note,
        created_at=_now_ms(),
    )
    _strategy_versions.setdefault(config.id, []).append(snapshot)
    await _with_db_repo(
        _version_repo,
        lambda version_repo: version_repo.save(snapshot),
        f"Failed to persist strategy version snapshot for {config.id}",
    )


async def _list_version_snapshots(strategy_id: str) -> list[StrategyVersionSnapshot]:
    in_memory = list(reversed(_strategy_versions.get(strategy_id, [])))
    persisted = await _with_db_repo(
        _version_repo,
        lambda version_repo: version_repo.list_by_strategy(strategy_id),
        f"Failed to list strategy version snapshots for {strategy_id}",
    )
    return persisted or in_memory


async def _persist_deployment_to_db(deployment: StrategyDeployment) -> None:
    await _with_db_repo(
        _deployment_repo,
        lambda deployment_repo: deployment_repo.save(deployment),
        f"Failed to persist strategy deployment for {deployment.strategy_id}",
    )


async def _save_deployment(deployment: StrategyDeployment) -> None:
    _strategy_deployments.append(deployment)
    await _persist_deployment_to_db(deployment)


def _running_deployment(strategy_id: str) -> StrategyDeployment | None:
    """该策略当前是否已有 running 部署(有则不该重复部署)。"""
    return next(
        (
            d
            for d in _strategy_deployments
            if d.strategy_id == strategy_id and d.status == "running"
        ),
        None,
    )


def _make_deployment(
    deployment_id: str,
    strategy_id: str,
    version: int,
    account_id: str,
    mode: str,
) -> StrategyDeployment:
    """构造一条 running 部署记录。"""
    return StrategyDeployment(
        id=deployment_id,
        strategy_id=strategy_id,
        strategy_version=version,
        account_id=account_id,
        mode=mode,
        status="running",
        created_at=_now_ms(),
    )


def _now_ms() -> int:
    """当前毫秒时间戳。"""
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    """生成带前缀的短 id(如 strat-a1b2c3d4)。"""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


#: 模板卡片直接透传的字段(键名 == 前端契约字段)。
_TEMPLATE_PASSTHROUGH = (
    "name",
    "name_zh",
    "description",
    "description_zh",
    "default_params",
    "risk_params",
    "symbols",
    "interval",
    # False for a catalog row with no implementation: the card must disable the
    # run/deploy actions rather than let the user hit a run that cannot trade.
    "runnable",
)


def _template_card(key: str, meta: Any) -> dict[str, Any]:
    """把工厂模板元数据转成前端卡片。"""
    card = {field: getattr(meta, field) for field in _TEMPLATE_PASSTHROUGH}
    card["id"] = key
    card["category"] = meta.category.value
    return card


def _bt_entry(bt: Any) -> dict[str, Any]:
    """把一条回测结果转成版本页里的精简条目。"""
    metrics = bt.metrics
    return {
        "id": bt.id,
        "total_return": getattr(metrics, "total_return", 0),
        "sharpe_ratio": getattr(metrics, "sharpe_ratio", 0),
        "max_drawdown": getattr(metrics, "max_drawdown", 0),
        "win_rate": getattr(metrics, "win_rate", 0),
        "trades_count": bt.trades_count,
        "created_at": bt.created_at,
    }


def _visible_backtests(strategy_id: str, user_id: str) -> list[Any]:
    """内存结果存储里属于该策略、且当前用户可见的回测。"""
    try:
        from app.api.v1.backtests import _result_owners
        from selene_space_agent.tools.strategy_tools import get_results_store

        store = get_results_store().values()
    except Exception:
        return []
    visible = []
    for bt in store:
        if bt.strategy_id != strategy_id:
            continue
        owner = _result_owners.get(bt.id)
        if user_id != "local" and owner is not None and owner != user_id:
            continue
        visible.append(bt)
    return visible


async def _persisted_backtests(strategy_id: str, user_id: str) -> list[Any]:
    """DB 里该策略的回测(取不到就空)。"""
    try:
        from app.core.dependencies import get_db_manager
        from selene_space_storage.repositories.backtests import BacktestRepository

        db = get_db_manager()
        if db is None:
            return []
        persisted: list[Any] = await BacktestRepository(db).list_by_strategy(
            strategy_id, limit=200, user_id=user_id
        )
        return persisted
    except Exception:
        return []


async def _collect_version_backtests(
    strategy_id: str, user_id: str
) -> dict[int, list[dict[str, Any]]]:
    """按 strategy_version 聚合回测条目(内存 + DB 去重合并)。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for bt in _visible_backtests(strategy_id, user_id):
        grouped.setdefault(bt.strategy_version, []).append(_bt_entry(bt))
    seen = {entry["id"] for entries in grouped.values() for entry in entries}
    for bt in await _persisted_backtests(strategy_id, user_id):
        if bt.id not in seen:
            grouped.setdefault(bt.strategy_version, []).append(_bt_entry(bt))
    return grouped


def _rule_field_kwargs(body: Any) -> dict[str, Any]:
    """请求体里"规则类"字段, 供 StrategyConfig 构造(创建/校验共用)。"""
    return {
        "name": body.name,
        "type": body.type,
        "symbols": body.symbols,
        "interval": body.interval,
        "parameters": body.parameters,
        "entry_rules": body.entry_rules,
        "exit_rules": body.exit_rules,
        "risk_params": body.risk_params,
    }


async def _validate_config(config: StrategyConfig) -> list[str]:
    """跑策略校验器; 不可用则只做 schema 校验。返回错误列表。"""
    errors: list[str] = []
    try:
        from selene_space_strategy.models import EngineStrategyConfig
        from selene_space_strategy.validator import validate

        engine_config = EngineStrategyConfig.model_validate(config.model_dump())
        result = validate(engine_config)
        if not result.valid:
            errors = list(result.errors)
    except ImportError:
        pass
    except Exception as exc:
        errors.append(str(exc))
    return errors


def _config_from_request(strategy_id: str, body: Any) -> StrategyConfig:
    """按创建请求构造持久化配置。"""
    return StrategyConfig(
        id=strategy_id,
        description=body.description,
        tags=body.tags,
        source=body.source,
        **_rule_field_kwargs(body),
    )


def _register_owned(config: StrategyConfig, user_id: str) -> None:
    """登记到内存表并异步落库(不再等待)。"""
    _strategies[config.id] = config
    _strategy_owners[config.id] = user_id
    asyncio.create_task(_persist_save(config, user_id=user_id))


async def _deploy_runner(
    deployment_id: str, config: StrategyConfig, account_id: str, warn: str
) -> tuple[Any, str | None]:
    """调 runner 启动部署; 返回 (runner, 错误串)。"""
    try:
        from app.core.dependencies import get_strategy_runner

        runner = get_strategy_runner()
        if runner is None:
            return None, "Strategy runner not initialized"
        runner_error = await runner.deploy(
            deployment_id=deployment_id,
            strategy_config=config.model_dump(),
            account_id=account_id,
        )
        if runner_error:
            logger.warning(warn, deployment_id, runner_error)
        return runner, runner_error
    except Exception as exc:
        logger.warning(warn, deployment_id, exc)
        return None, str(exc)


def _deployable_guard(config: StrategyConfig) -> None:
    """部署前状态校验: 只允许 draft/confirmed/running。"""
    if config.lifecycle_state not in ("confirmed", "draft", "running"):
        raise NotFoundError(
            "Strategy must be in draft, confirmed, or running state to deploy"
        )


def _has_rules(config: StrategyConfig) -> bool:
    return bool(config.entry_rules) or bool(config.exit_rules)


def _set_deployment_status(strategy_id: str, from_status: str, to_status: str) -> bool:
    """把该策略下处于 from_status 的部署改成 to_status; 返回是否有改动。"""
    changed = False
    for dep in _strategy_deployments:
        if dep.strategy_id == strategy_id and dep.status == from_status:
            dep.status = to_status
            changed = True
    return changed


async def _set_lifecycle_running(
    config: StrategyConfig, strategy_id: str, user_id: str
) -> StrategyConfig:
    """把策略切到 running 态并落库; 已是 running 则原样返回。"""
    if config.lifecycle_state == "running":
        return config
    updated = config.model_copy(update={"lifecycle_state": "running"})
    _strategies[strategy_id] = updated
    asyncio.create_task(_persist_save(updated, user_id=user_id))
    return updated


async def _list_deployments(
    *, account_id: str | None = None
) -> list[StrategyDeployment]:
    repo = get_strategy_repo()
    in_memory = list(reversed(_strategy_deployments))
    if account_id is not None:
        in_memory = [
            deployment
            for deployment in in_memory
            if deployment.account_id == account_id
        ]
    if repo is None or not hasattr(repo, "_db"):
        return in_memory
    try:
        from selene_space_storage.repositories.strategy_versions import (
            StrategyDeploymentRepository,
        )

        deployments: list[StrategyDeployment] = []
        for strategy_id in _strategies.keys():
            deployments.extend(
                await StrategyDeploymentRepository(repo._db).list_by_strategy(
                    strategy_id
                )
            )
        deployments.sort(key=lambda item: item.created_at, reverse=True)
        if account_id is not None:
            deployments = [
                deployment
                for deployment in deployments
                if deployment.account_id == account_id
            ]
        return deployments or in_memory
    except Exception:
        logger.debug("Failed to list strategy deployments", exc_info=True)
        return in_memory


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("")
async def create_strategy(
    request: Request,
    body: CreateStrategyRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Create a new strategy and store it."""
    user_strategy_count = sum(
        1 for owner in _strategy_owners.values() if owner == user.id
    )
    if user_strategy_count >= MAX_STRATEGIES_PER_USER:
        raise HTTPException(429, f"Strategy limit reached ({MAX_STRATEGIES_PER_USER})")

    config = _config_from_request(_new_id("strat"), body)
    _register_owned(config, user.id)
    await _save_version_snapshot(config, "initial create")
    return _ok(request, _strategy_to_dict(config))


def _strategy_to_dict(s: StrategyConfig) -> dict[str, Any]:
    """Convert strategy to dict with frontend-compatible fields."""
    d: dict[str, Any] = s.model_dump()
    d["symbol"] = s.symbols[0] if s.symbols else ""
    return d


@router.get("")
async def list_strategies(
    request: Request,
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(50, ge=1, le=1000, description="Page size"),
    tags: str | None = Query(None, description="Comma-separated tags to filter by"),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List all strategies with pagination and optional tag filtering."""
    repo = get_strategy_repo()
    if repo is not None:
        try:
            total = len(
                await repo.list(
                    limit=10000,
                    offset=0,
                    tags=[t.strip() for t in tags.split(",")] if tags else None,
                    user_id=user.id,
                )
            )
        except Exception:
            total = len(_strategies)
    else:
        all_strategies = list(_strategies.values())
        if tags:
            tag_set = {t.strip().lower() for t in tags.split(",") if t.strip()}
            all_strategies = [
                s
                for s in all_strategies
                if tag_set & {t.lower() for t in getattr(s, "tags", [])}
            ]
        total = len(all_strategies)

    page = await _list_strategies(
        offset=offset, limit=limit, tags=tags, user_id=user.id
    )
    return APIResponse(
        data=[_strategy_to_dict(s) for s in page],
        meta=build_response_meta(
            request,
            pagination=Pagination(offset=offset, limit=limit, total=total),
        ),
        error=None,
    )


@router.get("/community")
async def get_community_strategies(
    request: Request,
    repo: str = Query(
        "selene-space/community-strategies", description="Community strategy repository"
    ),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Browse community strategy templates.

    Not available in local/community mode. Returns a placeholder response.
    """
    return APIResponse(
        data={
            "strategies": [],
            "repo": repo,
            "total": 0,
            "message": "Community strategies not available in local mode",
        }
    )


@router.get("/runner/{deployment_id}/metrics")
async def get_deployment_metrics(
    deployment_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get performance metrics for a strategy deployment.

    Returns stub metrics in the community edition. Full metrics require
    the Pro edition with live/paper execution tracking.
    """
    return _ok(
        request,
        {
            "deployment_id": deployment_id,
            "pnl": 0,
            "trade_count": 0,
            "win_rate": 0,
            "sharpe": 0,
            "uptime_seconds": 0,
        },
    )


@router.post("/import")
async def import_strategy(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Import a strategy from JSON or YAML configuration.

    Accepts JSON body by default. If Content-Type is application/x-yaml,
    parses the body with yaml.safe_load first.
    """
    require_pro_feature("strategy_import")

    raw_body = await request.body()
    content_type = request.headers.get("content-type", "application/json")

    if "yaml" in content_type or "x-yaml" in content_type:
        try:
            config_dict = yaml.safe_load(raw_body)
        except yaml.YAMLError as exc:
            raise HTTPException(400, f"Invalid YAML: {exc}") from exc
    else:
        import json

        try:
            config_dict = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"Invalid JSON: {exc}") from exc

    if not isinstance(config_dict, dict):
        raise HTTPException(400, "Strategy config must be a JSON/YAML object")

    new_id = f"strat-{uuid.uuid4().hex[:8]}"
    config_dict.setdefault("id", new_id)
    config_dict["id"] = new_id
    config_dict.setdefault("version", 1)
    config_dict.setdefault("lifecycle_state", "draft")
    config_dict.setdefault("source", "import")

    try:
        config = StrategyConfig(**config_dict)
    except Exception as exc:
        raise HTTPException(400, f"Invalid strategy config: {exc}") from exc

    _register_owned(config, user.id)
    await _save_version_snapshot(config, "imported")

    return _ok(request, _strategy_to_dict(config))


# FX04: Static routes MUST be defined before parameterized /{strategy_id}
# to prevent FastAPI from matching "validate" or "deployments" as a strategy ID.


@router.get("/factory-templates")
async def list_factory_templates(
    request: Request,
    category: str | None = Query(
        None,
        description="Filter by category: trend, momentum, reversion, breakout, volatility",
    ),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List 18+ pre-built strategy templates from StrategyFactory."""
    try:
        from selene_space_strategy.factory import STRATEGY_TEMPLATES

        templates = [
            _template_card(key, meta)
            for key, meta in STRATEGY_TEMPLATES.items()
            if category is None or meta.category.value == category
        ]
        return _ok(request, templates)
    except Exception:
        logger.exception("factory-templates endpoint failed")
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error={
                "code": "INTERNAL_ERROR",
                "message": "Failed to load strategy templates",
            },
        )


@router.get("/qd-templates")
async def list_qd_templates(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """QuantDinger exports from the local backup library.

    Each export binds its own instrument in ``initialize``, so symbol and
    interval are read-only -- the card shows what the source declares and
    hands the id to ``POST /backtests``. A machine with no library installed
    lists nothing; that is an empty library, not a failure.
    """
    try:
        from selene_space_strategy.qd.catalog import list_qd_entries

        return APIResponse(data=list_qd_entries())
    except Exception:
        logger.exception("qd-templates endpoint failed")
        return APIResponse(
            data=None,
            meta=build_response_meta(request),
            error={"code": "INTERNAL_ERROR", "message": "Failed to load QD strategies"},
        )


@router.post("/factory-templates/{template_id}/create")
async def create_from_factory_template(
    template_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Create a strategy from a factory template.

    This endpoint allows one-click creation of strategies from the 18+
    pre-built templates in StrategyFactory.

    Args:
        template_id: The template identifier (e.g., 'sma_cross', 'rsi_reversal')
    """
    try:
        from selene_space_strategy.factory import StrategyFactory, STRATEGY_TEMPLATES

        meta = STRATEGY_TEMPLATES.get(template_id)
        if meta is None:
            available = list(STRATEGY_TEMPLATES.keys())
            raise HTTPException(
                404, f"Template '{template_id}' not found. Available: {available}"
            )
        # A row with no implementation would be stamped into a strategy whose
        # rules are empty, and every later run would silently end flat. Refuse
        # here so the gap surfaces at creation rather than as a zero-trade
        # backtest the user cannot distinguish from a real one.
        if not meta.runnable:
            raise HTTPException(
                422,
                f"Template '{template_id}' is listed but has no implementation, "
                "so a strategy created from it could never trade.",
            )

        # Create strategy from template
        config = StrategyFactory.create(template_id)

        # Get the seeded template strategy to copy entry_rules/exit_rules
        template_strategy = _strategies.get(meta.id)
        stored_config = StrategyConfig(
            id=_new_id("strat"),
            name=config.name,
            type=config.type,
            description=config.description,
            symbols=config.symbols,
            interval=config.interval,
            parameters=config.parameters,
            entry_rules=template_strategy.entry_rules if template_strategy else {},
            exit_rules=template_strategy.exit_rules if template_strategy else {},
            risk_params=config.risk_params,
            tags=[],
            source=f"template:{template_id}",
        )
        _register_owned(stored_config, user.id)
        await _save_version_snapshot(
            stored_config, f"created from template: {template_id}"
        )

        return APIResponse(data=_strategy_to_dict(stored_config))
    except ImportError as e:
        logger.error("StrategyFactory import failed: %s", e, exc_info=True)
        raise HTTPException(500, f"StrategyFactory not available: {e}")
    except Exception as e:
        logger.error(
            "create_from_factory_template failed for %s: %s",
            template_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            500, f"Failed to create strategy from template '{template_id}': {e}"
        )


@router.post("/validate")
async def validate_strategy(
    request: Request,
    body: ValidateStrategyRequest,
) -> APIResponse[dict[str, Any]]:
    """Validate a strategy configuration without storing it.

    Attempts to load the config through ``selene_space_strategy.validator``
    if available, otherwise performs basic schema validation only.
    """
    config = StrategyConfig(id=_new_id("tmp"), **_rule_field_kwargs(body))
    errors = await _validate_config(config)

    return _ok(
        request,
        {
            "valid": len(errors) == 0,
            "errors": errors,
        },
    )


@router.get("/deployments/list")
async def list_strategy_deployments(
    request: Request,
    account_id: str | None = Query(None),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List strategy deployments, optionally filtered by paper account."""
    from app.core.dependencies import get_strategy_runner

    runner = get_strategy_runner()
    deployments = await _list_deployments(account_id=account_id)
    rows = []
    for deployment in deployments:
        row = deployment.model_dump(mode="json")
        # flags 存在 _deployment_flags(内存,与部署同寿命);list 注入后前端
        # 才能回填 AI Gate/Bias Guard checkbox 真实值(修复 UI 显示假状态)。
        row["flags"] = _deployment_flags.get(
            deployment.id, {"ai_gate_enabled": False, "bias_guard_enabled": False}
        )
        # runtime 健康(US-102):runner 有 slot 才有数;无 runner/无 slot 时
        # 为 null —— 前端据此区分"还在等 runner 起来"和"确实死了"。
        slot = runner.get_slot_status(deployment.id) if runner is not None else None
        row["runtime"] = (
            {
                "bar_count": slot.get("bar_count", 0),
                "signals_emitted": slot.get("signals_emitted", 0),
                "errors": slot.get("errors", 0),
                "last_signal_ts": slot.get("last_signal_ts"),
            }
            if slot is not None
            else None
        )
        rows.append(row)
    return _ok(request, rows)


@router.get("/runner/status")
async def get_runner_status(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get the status of the strategy runner and all active deployments."""
    from app.core.dependencies import get_strategy_runner

    runner = get_strategy_runner()
    if runner is None:
        return _ok(request, {"running": False, "deployments": []})

    slots = [
        status
        for dep_id in runner.active_deployments
        if (status := runner.get_slot_status(dep_id))
    ]

    return _ok(
        request,
        {
            "running": runner.is_running,
            "deployment_count": len(slots),
            "deployments": slots,
        },
    )


@router.get("/runner/{deployment_id}/signals")
async def get_deployment_signals(
    deployment_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get signal history for a specific deployment."""
    from app.core.dependencies import get_strategy_runner

    runner = get_strategy_runner()
    if runner is None:
        raise NotFoundError("Strategy runner not available")

    signals = runner.get_signal_history(deployment_id)
    status = runner.get_slot_status(deployment_id)

    return _ok(
        request,
        {
            "deployment_id": deployment_id,
            "signals": signals,
            "total": len(signals),
            "status": status,
        },
    )


# --- Parameterized routes below ---


@router.get("/{strategy_id}")
async def get_strategy(
    strategy_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get a strategy by ID."""
    config = await _require_strategy(strategy_id, user.id)
    return _ok(request, _strategy_to_dict(config))


@router.put("/{strategy_id}")
async def update_strategy(
    strategy_id: str,
    body: UpdateStrategyRequest,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Update a strategy by ID (partial update)."""
    config = await _require_strategy(strategy_id, user.id)

    version_note = body.version_note
    update_data = body.model_dump(exclude_none=True)
    update_data.pop("version_note", None)
    if not update_data:
        return _ok(request, _strategy_to_dict(config))

    update_data["version"] = config.version + 1
    updated = config.model_copy(update=update_data)
    _register_owned(updated, user.id)
    await _save_version_snapshot(updated, version_note or "manual update")
    return _ok(request, _strategy_to_dict(updated))


@router.get("/{strategy_id}/versions")
async def list_strategy_versions(
    strategy_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List version snapshots with aggregated backtest metrics per version."""
    await _require_strategy(strategy_id, user.id)
    snapshots = await _list_version_snapshots(strategy_id)

    version_backtests = await _collect_version_backtests(strategy_id, user.id)
    enriched = []
    for snap in snapshots:
        entry = snap.model_dump(mode="json")
        entry["backtests"] = version_backtests.get(snap.version, [])
        enriched.append(entry)
    return _ok(request, enriched)


@router.post("/{strategy_id}/confirm")
async def confirm_strategy(
    strategy_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Move a strategy to CONFIRMED state."""
    config = await _require_strategy(strategy_id, user.id)
    updated = config.model_copy(
        update={"lifecycle_state": "confirmed", "version": config.version + 1}
    )
    _strategies[strategy_id] = updated
    asyncio.create_task(_persist_save(updated, user_id=user.id))
    await _save_version_snapshot(updated, "confirmed for paper deployment")
    return _ok(request, _strategy_to_dict(updated))


@router.post("/{strategy_id}/deploy-paper")
async def deploy_strategy_to_paper(
    strategy_id: str,
    body: DeployStrategyRequest,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Create a paper deployment and start continuous strategy execution.

    A dedicated strategy paper account is automatically created for each
    deployment. The StrategyRunner handles account creation, rule validation,
    duplicate prevention, and historical kline warmup internally.
    """
    async with _deploy_lock:
        return await _deploy_strategy_inner(strategy_id, body, request, user)


async def _deploy_strategy_inner(
    strategy_id: str,
    body: DeployStrategyRequest,
    request: Request,
    user: AuthenticatedUser,
) -> APIResponse[dict[str, Any]]:
    config = await _require_strategy(strategy_id, user.id)
    _deployable_guard(config)
    if not _has_rules(config):
        return APIResponse(
            data={
                "deployment": None,
                "strategy": _strategy_to_dict(config),
                "runner_status": "failed",
                "runner_error": _NO_RULES_ERROR,
                "created_account": None,
                "backtest_warning": None,
            }
        )

    backtest_warning: str | None = None
    if not await _has_completed_backtest(strategy_id, user_id=user.id):
        backtest_warning = (
            "该策略从未完成过回测,缺少历史表现证据。建议先跑一次回测再部署。"
        )

    existing_running = _running_deployment(strategy_id)
    if existing_running:
        return _ok(
            request,
            {
                "deployment": existing_running.model_dump(mode="json"),
                "strategy": _strategy_to_dict(config),
                "runner_status": "running",
                "runner_error": None,
                "already_deployed": True,
                "backtest_warning": backtest_warning,
            },
        )

    deployment_id = f"dep-{uuid.uuid4().hex[:8]}"

    # StrategyRunner.deploy handles: account creation, compilation,
    # warmup, and duplicate prevention internally.
    account_id = body.account_id
    runner, runner_error = await _deploy_runner(
        deployment_id,
        config,
        account_id,
        "Failed to start runner for deployment %s: %s",
    )

    if runner_error:
        return _ok(
            request,
            {
                "deployment": None,
                "strategy": _strategy_to_dict(config),
                "runner_status": "failed",
                "runner_error": runner_error,
                "created_account": None,
                "backtest_warning": backtest_warning,
            },
        )

    # Read back the actual account_id chosen by the runner
    slot_status = runner.get_slot_status(deployment_id) if runner else None
    actual_account_id = slot_status["account_id"] if slot_status else account_id

    # Register ownership so the account is visible only to this user
    _register_deployment_owner(
        actual_account_id, user.id, "Failed to register deployment account ownership"
    )

    deployment = _make_deployment(
        deployment_id, strategy_id, config.version, actual_account_id, "paper"
    )
    _strategy_deployments.append(deployment)
    asyncio.create_task(_persist_deployment_to_db(deployment))

    updated = await _set_lifecycle_running(config, strategy_id, user.id)

    return _ok(
        request,
        {
            "deployment": deployment.model_dump(mode="json"),
            "strategy": _strategy_to_dict(updated),
            "runner_status": "running",
            "runner_error": None,
            "created_account": None,
            "bar_count": slot_status.get("bar_count", 0) if slot_status else 0,
            "backtest_warning": backtest_warning,
        },
    )


@router.post("/{strategy_id}/stop-deployment")
async def stop_strategy_deployment(
    strategy_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Stop a running strategy deployment."""
    from app.core.dependencies import get_strategy_runner

    runner = get_strategy_runner()

    stopped_in_runner: list[str] = []
    if runner is not None:
        # Use async variant to cancel + await in-flight slot tasks (H12).
        # stop_by_strategy_id_async returns the same list of deployment ids.
        stopped_in_runner = await runner.stop_by_strategy_id_async(strategy_id)

    _set_deployment_status(strategy_id, "running", "stopped")

    await _downgrade_to_confirmed(strategy_id, user.id)

    logger.info(
        "Stopped strategy %s: %d runner slot(s) removed",
        strategy_id,
        len(stopped_in_runner),
    )

    return _ok(request, {"stopped_deployments": stopped_in_runner})


@router.delete("/{strategy_id}")
async def delete_strategy(
    strategy_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Delete a strategy by ID."""
    _owned_cached(strategy_id, user.id)
    _strategies.pop(strategy_id, None)
    _strategy_owners.pop(strategy_id, None)
    asyncio.create_task(_persist_delete(strategy_id, user_id=user.id))
    return _ok(request, {"deleted": strategy_id})


# ---------------------------------------------------------------------------
# Phase 4: Strategy Deployment Pro
# ---------------------------------------------------------------------------


@router.post("/{strategy_id}/clone")
async def clone_strategy(
    strategy_id: str,
    request: Request,
    body: CloneStrategyRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Deep copy a strategy with a new ID.

    Creates an independent clone of the strategy configuration. The clone
    starts in draft state with version 1.
    """
    config = await _require_strategy(strategy_id, user.id)

    new_id = f"strat-{uuid.uuid4().hex[:8]}"
    clone_data = config.model_dump()
    clone_data["id"] = new_id
    clone_data["name"] = body.name or f"{config.name} (clone)"
    clone_data["version"] = 1
    clone_data["lifecycle_state"] = "draft"
    clone_data["source"] = "clone"

    cloned = StrategyConfig(**clone_data)
    _register_owned(cloned, user.id)
    await _save_version_snapshot(cloned, f"cloned from {strategy_id}")

    return _ok(request, _strategy_to_dict(cloned))


@router.get("/{strategy_id}/export")
async def export_strategy(
    strategy_id: str,
    request: Request,
    format: str = Query("yaml", description="Export format: yaml or json"),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Export a strategy as YAML or JSON string."""
    config = await _require_strategy(strategy_id, user.id)

    config_dict = config.model_dump(mode="json")

    if format.lower() == "yaml":
        content = yaml.dump(config_dict, default_flow_style=False, allow_unicode=True)
    else:
        import json

        content = json.dumps(config_dict, indent=2, ensure_ascii=False)

    return _ok(
        request,
        {"format": format.lower(), "content": content, "strategy_id": strategy_id},
    )


@router.post("/{strategy_id}/deploy-live")
async def deploy_strategy_to_live(
    strategy_id: str,
    request: Request,
    body: DeployLiveRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Deploy a strategy to a live trading venue.

    Checks if a live execution engine is available. In the community edition,
    live trading is not supported and this endpoint returns an error.
    """
    require_pro_feature("deploy_live")

    config = await _require_strategy(strategy_id, user.id)

    from app.core.dependencies import get_live_engine

    live_engine = get_live_engine()
    if live_engine is None:
        return _ok(
            request,
            {
                "deployed": False,
                "mode": "live",
                "strategy_id": strategy_id,
                "venue": body.venue,
                "error": "Live execution engine not available in community edition",
            },
        )

    _deployable_guard(config)
    if not _has_rules(config):
        return _ok(
            request,
            {
                "deployed": False,
                "mode": "live",
                "strategy_id": strategy_id,
                "venue": body.venue,
                "runner_status": "failed",
                "runner_error": _NO_RULES_ERROR,
            },
        )

    # Publish gate (QD semantics): live deployment requires backtest evidence.
    if not await _has_completed_backtest(strategy_id, user_id=user.id):
        return _ok(
            request,
            {
                "deployed": False,
                "mode": "live",
                "strategy_id": strategy_id,
                "venue": body.venue,
                "runner_status": "blocked",
                "runner_error": (
                    "发布门拦截:该策略从未完成过回测,缺少历史表现证据,"
                    "禁止直接部署到实盘。请先运行至少一次回测。"
                ),
            },
        )

    existing_running = _running_deployment(strategy_id)
    if existing_running:
        return _ok(
            request,
            {
                "deployed": True,
                "mode": "live",
                "strategy_id": strategy_id,
                "venue": body.venue,
                "deployment": existing_running.model_dump(mode="json"),
                "runner_status": "running",
                "runner_error": None,
                "already_deployed": True,
            },
        )

    deployment_id = f"dep-live-{uuid.uuid4().hex[:8]}"
    _deployment_flags[deployment_id] = {
        "ai_gate_enabled": body.ai_gate_enabled,
        "bias_guard_enabled": body.bias_guard_enabled,
    }

    # MINOR 1: empty account_id triggers runner's _create_strategy_account path
    account_id = body.account_id or ""
    runner, runner_error = await _deploy_runner(
        deployment_id,
        config,
        account_id,
        "Failed to start runner for live deployment %s: %s",
    )

    if runner_error:
        return _ok(
            request,
            {
                "deployed": False,
                "mode": "live",
                "strategy_id": strategy_id,
                "venue": body.venue,
                "deployment_id": deployment_id,
                "runner_status": "failed",
                "runner_error": runner_error,
            },
        )

    # Read back the actual account_id chosen by the runner
    from app.core.dependencies import get_strategy_runner

    runner = get_strategy_runner()
    slot_status = runner.get_slot_status(deployment_id) if runner else None
    actual_account_id = (
        (slot_status.get("account_id") if slot_status else None)
        or account_id
        or "live-default"
    )

    # Register ownership so the account is visible only to this user
    _register_deployment_owner(
        actual_account_id,
        user.id,
        "Failed to register live deployment account ownership",
    )

    deployment = _make_deployment(
        deployment_id, strategy_id, config.version, actual_account_id, "live"
    )
    _strategy_deployments.append(deployment)
    asyncio.create_task(_persist_deployment_to_db(deployment))

    updated = await _set_lifecycle_running(config, strategy_id, user.id)

    return _ok(
        request,
        {
            "deployed": True,
            "mode": "live",
            "strategy_id": strategy_id,
            "venue": body.venue,
            "deployment_id": deployment_id,
            "account_id": actual_account_id,
            "deployment": deployment.model_dump(mode="json"),
            "strategy": _strategy_to_dict(updated),
            "runner_status": "running",
            "runner_error": None,
            "bar_count": slot_status.get("bar_count", 0) if slot_status else 0,
        },
    )


@router.post("/{strategy_id}/pause-deployment")
async def pause_strategy_deployment(
    strategy_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Pause a running strategy deployment."""
    paused = _set_deployment_status(strategy_id, "running", "paused")
    return _ok(request, {"paused": paused, "strategy_id": strategy_id})


@router.post("/{strategy_id}/resume-deployment")
async def resume_strategy_deployment(
    strategy_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Resume a paused strategy deployment."""
    resumed = _set_deployment_status(strategy_id, "paused", "running")
    return _ok(request, {"resumed": resumed, "strategy_id": strategy_id})


@router.patch("/{strategy_id}/deployment/flags")
async def update_deployment_flags(
    strategy_id: str,
    request: Request,
    body: DeploymentFlagsRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Update deployment flags (ai_gate_enabled, bias_guard_enabled)."""
    await _require_strategy(strategy_id, user.id)

    # Find the latest deployment for this strategy
    deployment = next(
        (d for d in reversed(_strategy_deployments) if d.strategy_id == strategy_id),
        None,
    )
    if deployment is None:
        return _ok(
            request, {"updated": False, "flags": {}, "error": "No deployment found"}
        )

    dep_id = deployment.id
    flags = _deployment_flags.setdefault(
        dep_id, {"ai_gate_enabled": False, "bias_guard_enabled": False}
    )
    update_data = body.model_dump(exclude_none=True)
    flags.update(update_data)

    return _ok(request, {"updated": True, "flags": flags})
