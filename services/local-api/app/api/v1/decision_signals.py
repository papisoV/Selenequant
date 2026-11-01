# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); upstream exposed the signal write
# surface off an LLM-produced signal object. Selene has no such producer, so
# the endpoint demands an explicit 8-state action instead of inventing one.
"""决策信号路由 —— 持久化决策信号的写入面 + 读面 + 命中率统计(v028/📋8)。

与 P5 前向验证的分工:P5 评的是 v009 分析记录、落 research graph;本路由管的是
**决策信号**(``decision_signals``)及其 out of sample 兑现情况
(``decision_signal_outcomes``),读面为主,写面刻意只有一个显式入口。

**为什么写入要显式给 action**:Selene 目前没有产出八态 action 的生产者
(``action_taxonomy`` 仅被本包 import;LLM 侧只有 long/short/hold)。从
long/short/hold 到八态(buy? add?sell? reduce?)是**产品决策**,不该由吸收批次
发明,故本路由只接受调用方显式给出的 action,不猜。生产者落地后直接复用本入口。

错误约定(同 ``research_graph.py``):领域错误(ValueError/KeyError)→ 400/404;
其余 → 500(完整异常只进服务端日志)。成功路径统一 ``APIResponse`` 信封。

outcome 的**计算**不在此路由 —— 它由后台的
:class:`~app.core.decision_signal_outcome.DecisionSignalOutcomeService` 批量跑
(daily-cycle 之后),本路由只读结果并触发一次。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/decision-signals", tags=["decision-signals"])

# local-api 进程内可导入 storage 包(不依赖 pip install -e)。
sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "packages" / "storage"))

from selene_space_storage.repositories.decision_signals import (  # noqa: E402
    DEFAULT_STATS_STATUSES,
    DecisionSignalRepository,
)

# 八态 action(📋1 词汇层)。放这里做入参校验:库层 CHECK 是最后一道防线,
# 但 400 比 IntegrityError 更能告诉调用方"哪个词不合法"。
_ACTIONS = ("buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert")
_SIGNAL_STATUSES = ("active", "expired", "invalidated", "closed", "archived")


class DecisionSignalCreate(BaseModel):
    """写一条决策信号。``action`` 必填且必须来自八态词表 —— 本路由不猜。"""

    stock_code: str = Field(min_length=1)
    action: str = Field(min_length=1)
    market: str = Field(default="cn", min_length=1)
    source_type: str = Field(default="manual", min_length=1)
    trigger_source: str = Field(default="manual", min_length=1)
    stock_name: Optional[str] = None
    source_agent: Optional[str] = None
    source_report_id: Optional[str] = None
    trace_id: Optional[str] = None
    decision_profile: Optional[str] = None
    market_phase: Optional[str] = None
    action_label: Optional[str] = None
    confidence: Optional[float] = None
    score: Optional[int] = None
    horizon: Optional[str] = None
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    invalidation: Optional[str] = None
    watch_conditions: Optional[str] = None
    reason: Optional[str] = None
    risk_summary: Optional[str] = None
    catalyst_summary: Optional[str] = None
    plan_quality: str = "unknown"
    expires_at: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class StatusUpdate(BaseModel):
    status: str


def get_signals_db() -> Any:
    """FastAPI dependency —— 懒取 app 共享的 AsyncSQLiteManager(可被测试 overrides)。"""
    from app.core.dependencies import get_db_manager

    db = get_db_manager()
    if db is None:
        raise HTTPException(status_code=503, detail="数据库未就绪")
    return db


def _http_500(context: str, exc: Exception) -> HTTPException:
    logger.exception("decision signals api error: %s", context)
    return HTTPException(status_code=500, detail=f"{context}失败,请查看服务端日志")


def _validate_action(action: str) -> str:
    text = str(action or "").strip().lower()
    if text not in _ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown action: {action!r} (expected one of {', '.join(_ACTIONS)})",
        )
    return text


@router.post("")
async def create_signal(
    payload: DecisionSignalCreate, request: Request, db: Any = Depends(get_signals_db)
):
    """写入一条决策信号。同 identity 重放返回既有行(``created=False``)。"""
    try:
        action = _validate_action(payload.action)
        fields = payload.model_dump(exclude={"metadata"})
        fields["action"] = action
        # 端点只列信号级字段;metadata 落 metadata_json。
        fields.pop("metadata", None)
        if payload.metadata is not None:
            fields["metadata_json"] = payload.metadata
        repo = DecisionSignalRepository(db)
        signal_id, created = await repo.create_if_absent(fields)
        return APIResponse(
            data={"signal_id": signal_id, "created": created},
            meta=build_response_meta(request),
        )
    except HTTPException:
        raise
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise _http_500("决策信号创建", e)


@router.get("")
async def list_signals(
    request: Request,
    stock_code: Optional[str] = None,
    market: Optional[str] = None,
    action: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: Any = Depends(get_signals_db),
):
    try:
        repo = DecisionSignalRepository(db)
        signals = await repo.list_signals(
            stock_code=stock_code,
            market=market,
            action=action,
            status=status,
            limit=max(1, min(int(limit), 200)),
        )
        return APIResponse(
            data={"signals": signals}, meta=build_response_meta(request)
        )
    except Exception as e:
        raise _http_500("决策信号列表", e)


@router.get("/skill-weights")
async def get_skill_weights(
    request: Request,
    skill_id: Optional[str] = None,
    horizon: Optional[str] = None,
    engine_version: Optional[str] = None,
    db: Any = Depends(get_signals_db),
):
    """每个 Skill(``source_agent``)的有界表现因子(📋3)。

    因子住 ``[1/1.2, 1.2]``;样本不足或证据不可用一律回中性 ``1.0``。没有
    ``source_agent`` 的判定无法归因,不参与(不会摊给全体)。
    """
    from app.core.decision_signal_outcome import DecisionSignalOutcomeService
    from selene_space_backtest.signal_outcome import SIGNAL_OUTCOME_ENGINE_VERSION

    try:
        service = DecisionSignalOutcomeService(db, fetch_daily=lambda _symbol: [])
        weights = await service.skill_weights(
            skill_ids=[skill_id] if skill_id else None,
            horizons=[horizon] if horizon else None,
            engine_version=engine_version or SIGNAL_OUTCOME_ENGINE_VERSION,
        )
        return APIResponse(data=weights, meta=build_response_meta(request))
    except Exception as e:
        raise _http_500("Skill 权重统计", e)


@router.get("/stats")
async def get_stats(
    request: Request,
    engine_version: Optional[str] = None,
    db: Any = Depends(get_signals_db),
):
    """命中率聚合(默认口径排除 archived;unable 不进分母)。"""
    from app.core.decision_signal_outcome import _aggregate
    from selene_space_backtest.signal_outcome import SIGNAL_OUTCOME_ENGINE_VERSION

    try:
        repo = DecisionSignalRepository(db)
        rows = await repo.stats_rows(
            engine_version=engine_version or SIGNAL_OUTCOME_ENGINE_VERSION
        )
        stats = _aggregate(rows)
        stats["engine_version"] = engine_version or SIGNAL_OUTCOME_ENGINE_VERSION
        stats["stats_statuses"] = list(DEFAULT_STATS_STATUSES)
        return APIResponse(data=stats, meta=build_response_meta(request))
    except Exception as e:
        raise _http_500("决策信号统计", e)


@router.get("/{signal_id}")
async def get_signal(
    signal_id: int, request: Request, db: Any = Depends(get_signals_db)
):
    try:
        repo = DecisionSignalRepository(db)
        signal = await repo.get(int(signal_id))
        if signal is None:
            raise HTTPException(status_code=404, detail=f"decision signal not found: {signal_id}")
        outcomes = await repo.list_outcomes(int(signal_id))
        return APIResponse(
            data={"signal": signal, "outcomes": outcomes},
            meta=build_response_meta(request),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("决策信号查询", e)


@router.post("/{signal_id}/status")
async def update_status(
    signal_id: int,
    payload: StatusUpdate,
    request: Request,
    db: Any = Depends(get_signals_db),
):
    """推进信号生命周期(expired / invalidated / closed / archived)。"""
    status = str(payload.status or "").strip().lower()
    if status not in _SIGNAL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"unknown status: {payload.status!r} (expected one of {', '.join(_SIGNAL_STATUSES)})",
        )
    try:
        repo = DecisionSignalRepository(db)
        changed = await repo.set_status(int(signal_id), status)
        if not changed:
            raise HTTPException(
                status_code=404, detail=f"decision signal not found: {signal_id}"
            )
        return APIResponse(
            data={"signal_id": int(signal_id), "status": status},
            meta=build_response_meta(request),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("决策信号状态更新", e)


__all__ = ["router"]
