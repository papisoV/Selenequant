"""Setup 回测 API — 验证"这个 entry/SL/TP 的 R:R 在历史上赚不赚"。

``POST /api/v1/setup-backtest``：拉历史 K 线 + 跑 setup 模拟器 → SetupBacktestResult。

数据源复用 kline_manager.get_klines(source=auto: local→remote 回退)，与
backtests.py / markets.py 同源。同步小量起步（setup 扫描是 O(n) 单遍）；
数据量上来再转 backtests.py 的 202+task_id 异步任务模型（见计划阶段 1b）。
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.dependencies import (
    build_response_meta,
    get_decision_workflow_service,
    get_kline_manager,
)
from selene_space_market_analysis.setup_backtest import (
    SetupBacktestError,
    run_setup_backtest as run_setup_sim,
)
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/setup-backtest", tags=["setup-backtest"])


class SetupBacktestRequest(BaseModel):
    symbol: str = Field(..., description="交易对，如 BTC/USDT")
    timeframe: str = Field("1h", description="K 线周期（1m/5m/15m/30m/1h/4h/1d）")
    exchange: str = Field("binance", description="binance / okx")
    market_type: str = Field("futures", description="spot / futures")
    entry: float = Field(..., description="入场价")
    stop_loss: float = Field(..., description="止损价")
    take_profit: float = Field(..., description="止盈价")
    direction: Literal["long", "short"] | None = Field(
        None, description="缺省由 entry 与 SL 相对位置推断"
    )
    lookback_bars: int | None = Field(
        None, ge=20, le=100_000, description="只看最近 N 根（缺省全量）"
    )
    input_id: str | None = Field(
        None, description="关联的诊断记录 input_id — 非空时回测结果挂到该 record"
    )


@router.post("")
async def run_setup_backtest(
    body: SetupBacktestRequest,
    request: Request,
    km: Any = Depends(get_kline_manager),
) -> APIResponse[dict[str, Any]]:
    """拉历史 K 线并跑 setup 采样统计，返回聚合结果（无触发为 200 空样本）。"""
    if km is None:
        raise HTTPException(503, "Kline manager not available")

    try:
        df = await km.get_klines(
            exchange=body.exchange,
            market_type=body.market_type,
            symbol=body.symbol,
            interval=body.timeframe,
            source="auto",
            min_candles=20,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("setup-backtest kline fetch failed: %s", exc)
        raise HTTPException(502, f"kline fetch failed: {exc}") from exc

    if df is None or len(df) == 0:
        raise HTTPException(400, f"no kline data for {body.symbol} {body.timeframe}")

    if body.lookback_bars is not None and len(df) > body.lookback_bars:
        df = df.tail(body.lookback_bars)

    try:
        result = run_setup_sim(
            df,
            body.entry,
            body.stop_loss,
            body.take_profit,
            direction=body.direction,
        )
    except SetupBacktestError as exc:
        raise HTTPException(422, str(exc)) from exc

    payload = result.to_dict()
    payload["symbol"] = body.symbol
    payload["timeframe"] = body.timeframe
    payload["exchange"] = body.exchange
    payload["market_type"] = body.market_type

    # T2a 审计链：input_id 非空 → 回测结果挂到诊断记录（经 service 刷缓存）。
    # 挂载失败只 warning 不失败请求 — 回测本身已经算完，用户照常拿结果。
    attached = False
    if body.input_id:
        svc = get_decision_workflow_service()
        if svc is not None:
            try:
                attached = await svc.attach_backtest(
                    body.input_id, _backtest_attachment(payload)
                )
            except Exception:  # noqa: BLE001 - attachment must not break the response
                logger.warning(
                    "setup-backtest attach failed input_id=%s",
                    body.input_id,
                    exc_info=True,
                )
        else:
            logger.warning(
                "setup-backtest attach skipped (no service) input_id=%s", body.input_id
            )
    payload["attached_to_record"] = attached

    return APIResponse(
        data=payload,
        meta=build_response_meta(request),
        error=None,
    )


def _backtest_attachment(payload: dict[str, Any]) -> dict[str, Any]:
    """Slim copy of the backtest payload for the record's audit entry."""
    return {
        "symbol": payload.get("symbol"),
        "timeframe": payload.get("timeframe"),
        "total_setups": payload.get("total_setups"),
        "win_rate": payload.get("win_rate"),
        "expectancy": payload.get("expectancy"),
        "profit_factor": payload.get("profit_factor"),
        "max_drawdown": payload.get("max_drawdown"),
        "avg_bars_held": payload.get("avg_bars_held"),
        "open_setups": payload.get("open_setups"),
        "no_trigger": payload.get("no_trigger"),
        "source": "setup_backtest",
    }
