"""PaperPlan API — 把实验的 setup 登记为可跟踪的 paper 模拟计划。

阶段5"记录到 paper 计划"路径（paper-engine 无自动入场触发/SL-TP 退出
循环 → 降级为记录式跟踪，不做自动下单）。

端点（全部返回 bare dict/list，前端 BARE_ENDPOINT_PATTERNS 匹配）:
- POST /api/v1/experiments/{id}/paper-plan      —— 由实验 setup_params 部署一条计划
- GET  /api/v1/experiments/{id}/paper-plans      —— 某实验部署的全部计划
- GET  /api/v1/paper-plans?status=&limit=&offset= —— 计划列表(newest first)
- POST /api/v1/paper-plans/{plan_id}/transition   —— 状态迁移
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import get_experiment_repo, get_paper_plan_repo

# 实验子资源:挂 /experiments 前缀,走既有 BARE 模式
experiment_plans_router = APIRouter(prefix="/experiments", tags=["paper-plans"])

# 计划资源本体
paper_plans_router = APIRouter(prefix="/paper-plans", tags=["paper-plans"])


class DeployPaperPlanBody(BaseModel):
    """部署计划的可选覆盖项；缺省时从 experiment.setup_params 派生。"""

    account_id: str | None = None
    quantity: float | None = Field(default=None, gt=0)
    note: str | None = None


class TransitionBody(BaseModel):
    status: str


def _require_plan_repo(repo: Any = Depends(get_paper_plan_repo)) -> Any:
    if repo is None:
        raise HTTPException(503, "Paper plan storage not available")
    return repo


def _require_experiment_repo(repo: Any = Depends(get_experiment_repo)) -> Any:
    if repo is None:
        raise HTTPException(503, "Experiment storage not available")
    return repo


def _derive_plan_from_experiment(exp: dict, body: DeployPaperPlanBody) -> dict:
    """从实验字段 + 可选覆盖派生 paper_plans 行（不落盘）。"""
    sp: dict = exp.get("setup_params") or {}
    entry = sp.get("entry")
    stop_loss = sp.get("stop_loss")
    take_profit = sp.get("take_profit")
    direction = sp.get("direction")
    symbol = exp.get("symbol")
    timeframe = exp.get("timeframe")

    if entry is None or stop_loss is None or take_profit is None:
        raise HTTPException(422, "Experiment has no complete setup_params (entry/SL/TP)")
    if not symbol or not timeframe:
        raise HTTPException(422, "Experiment is missing symbol/timeframe")

    quantity = body.quantity
    if quantity is None:
        # 标准纸面试仓 0.001 base 单位（如 0.001 BTC）。仅作计划跟踪参数，
        # 不写入 paper 账本（本路径不自动下单）。
        quantity = 0.001
    if quantity <= 0:
        raise HTTPException(422, "quantity must be positive")

    return {
        "experiment_id": exp["id"],
        "name": f"{exp.get('name') or 'PA 形态'} · paper 跟踪",
        "symbol": symbol,
        "timeframe": timeframe,
        "direction": direction,
        "entry": float(entry),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
        "quantity": quantity,
        "account_id": body.account_id or "paper-default",
        "status": "planned",
        "note": body.note,
    }


@experiment_plans_router.post("/{experiment_id}/paper-plan")
async def deploy_experiment_to_paper(
    experiment_id: str,
    body: DeployPaperPlanBody,
    plan_repo: Any = Depends(_require_plan_repo),
    exp_repo: Any = Depends(_require_experiment_repo),
) -> dict[str, Any]:
    """由实验登记一条 paper 计划（planned 初始态）。"""
    exp = await exp_repo.get(experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    plan = _derive_plan_from_experiment(exp, body)
    pid = await plan_repo.create(plan)
    created = await plan_repo.get(pid)
    assert created is not None
    return created


@experiment_plans_router.get("/{experiment_id}/paper-plans")
async def list_experiment_paper_plans(
    experiment_id: str,
    plan_repo: Any = Depends(_require_plan_repo),
) -> list[dict[str, Any]]:
    """某实验部署的全部 paper 计划（newest first）。"""
    return await plan_repo.list_by_experiment(experiment_id)


@paper_plans_router.get("")
async def list_paper_plans(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    plan_repo: Any = Depends(_require_plan_repo),
) -> list[dict[str, Any]]:
    """全部 paper 计划列表，可选按 status 过滤。"""
    return await plan_repo.list(status=status, limit=limit, offset=offset)


@paper_plans_router.post("/{plan_id}/transition")
async def transition_plan(
    plan_id: str,
    body: TransitionBody,
    plan_repo: Any = Depends(_require_plan_repo),
) -> dict[str, Any]:
    """状态迁移（planned → active → completed/cancelled）。非法边 → 400。"""
    plan = await plan_repo.transition(plan_id, body.status)
    if plan is None:
        raise HTTPException(400, "Invalid status transition")
    return plan
