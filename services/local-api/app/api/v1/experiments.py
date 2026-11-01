"""Experiment 登记 API — 研究版本控制雏形（"Git for Quant Research"）。

把一次"AI 识别形态 → setup 回测"固化成可追溯的实验，支持 create / list /
get / fork（parent_experiment 血缘链）。setup_params 与 backtest_result
为 JSON 快照，由 repository 序列化落盘。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.dependencies import get_experiment_repo
from app.core.evidence_report import compose_evidence_report

router = APIRouter(prefix="/experiments", tags=["experiments"])


class ExperimentBody(BaseModel):
    name: str
    hypothesis: str | None = None
    symbol: str | None = None
    timeframe: str | None = None
    dataset: str | None = None
    strategy_version: str | None = None
    setup_params: dict[str, Any] | None = None
    backtest_result: dict[str, Any] | None = None
    evidence_refs: list[Any] | None = None


class ForkBody(BaseModel):
    overrides: dict[str, Any] | None = Field(default_factory=dict)


def _require_repo(repo: Any = Depends(get_experiment_repo)) -> Any:
    if repo is None:
        raise HTTPException(503, "Experiment storage not available")
    return repo


@router.post("")
async def create_experiment(
    body: ExperimentBody,
    repo: Any = Depends(_require_repo),
) -> dict[str, Any]:
    eid = await repo.create(body.model_dump(exclude_none=True))
    return await repo.get(eid)


@router.get("")
async def list_experiments(
    symbol: str | None = None,
    limit: int = 50,
    offset: int = 0,
    repo: Any = Depends(_require_repo),
) -> list[dict[str, Any]]:
    return await repo.list(symbol=symbol, limit=limit, offset=offset)


@router.get("/{experiment_id}")
async def get_experiment(
    experiment_id: str,
    repo: Any = Depends(_require_repo),
) -> dict[str, Any]:
    row = await repo.get(experiment_id)
    if row is None:
        raise HTTPException(404, "Experiment not found")
    return row


@router.get("/{experiment_id}/evidence-report")
async def get_evidence_report(
    experiment_id: str,
    repo: Any = Depends(_require_repo),
) -> dict[str, Any]:
    """组合证据报告:实验字段 + 血缘链(祖先/子代)+ 结论文本。

    纯读端点,不产生副作用。ancestors 由父到根,children 为直接 fork 后代,
    供前端 blood-line 可视化。
    """
    exp = await repo.get(experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    ancestors = await repo.get_ancestors(experiment_id)
    children = await repo.list_children(experiment_id)
    return compose_evidence_report(exp, ancestors, children)


@router.post("/{experiment_id}/fork")
async def fork_experiment(
    experiment_id: str,
    body: ForkBody,
    repo: Any = Depends(_require_repo),
) -> dict[str, Any]:
    try:
        new_id = await repo.fork(experiment_id, overrides=body.overrides)
    except KeyError as exc:
        raise HTTPException(404, "Experiment not found") from exc
    return await repo.get(new_id)
