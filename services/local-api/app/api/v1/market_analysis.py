"""Market-analysis API router (US-008 / US-302 / US-304).

- POST /analyze: 同步（默认）或后台（background=true）两阶段行情分析
- GET  /analyze/{job_id}: 后台任务状态 / 结果
- DELETE /analyze/{job_id}: 取消后台任务
- POST /chat: Free Chat 自由追问（以分析记录为种子 + 最新 K 线快照）
- GET  /chat/{record_id}: 读回聊天历史
- GET  /records: 最近分析记录（演示回放列表）
- GET  /records/{record_id}: 取回完整记录（演示回放）
- GET  /records/{record_id}/export.csv: 导出 K 线 CSV（可下载）
- GET  /records/ic-panel: IC 诊断面板（只读快照；无快照 → no_snapshot 空结构）
- POST /records/ic-panel/mine: 实时计算 IC 面板并落库为可复算快照（唯一写路径）
- GET  /records/return-report: 多空回报面板（跨截面权重漂移回测）
- POST /notify: 后端触发飞书/PushPlus 通知（env 开关默认关）

依赖注入走 app.core.dependencies.get_market_analysis_service（末尾追加）。
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import logging
import uuid
from typing import Any, Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.api.v1.research_graph import get_graph_db
from app.core.dependencies import get_market_analysis_service
from app.services import factor_snapshot as fs
from selene_space_factor_research.mining.ic_panel import ICPanel
from selene_space_factor_research.mining.weight_drift_backtest import (
    WeightDriftBacktest,
    WeightDriftConfig,
)
from selene_space_market_analysis.data_source import KlineSource
from selene_space_market_analysis.eval_next_bar import (
    compute_subsequent_closes,
    evaluate_next_bar,
)
from selene_space_market_analysis.exceptions import (
    CheatError,
    MarketAnalysisError,
    SemanticError,
    ValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market-analysis", tags=["market-analysis"])


class AnalyzeRequest(BaseModel):
    exchange: str = "binance"
    market_type: str = "futures"
    symbol: str
    interval: str = "1h"
    background: bool = False
    input_id: str | None = None
    session_id: str | None = None
    # US-004: 交易倾向入参（自研命名，影响 Stage2 决策倾向）。
    trade_temperament: (
        Literal["conservative", "balanced", "aggressive", "very_aggressive"] | None
    ) = None
    # US-005: 增量分析（复用最近同 symbol+interval 的成功记录）。
    incremental: bool = False
    # B1: 历史分析纵向对比条数（0=不启用；>0 时注入最近同 symbol+interval
    # 的 N 条记录供 LLM 前后对照，仅参考）。
    history_n: int = Field(0, ge=0, le=5)


class JobResponse(BaseModel):
    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class ChatRequest(BaseModel):
    record_id: str
    message: str


class NotifyRequest(BaseModel):
    record_id: str | None = None
    message: str | None = None


@router.post("/analyze")
async def analyze(
    req: AnalyzeRequest,
    svc: Any = Depends(get_market_analysis_service),
) -> dict[str, Any]:
    """两阶段行情分析：同步返回记录，或提交后台任务返回 job_id。"""
    input_id = req.input_id or uuid.uuid4().hex
    session_id = req.session_id or uuid.uuid4().hex

    source = KlineSource()
    try:
        input_data = source.build_input_data(
            req.exchange, req.market_type, req.symbol, req.interval
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis input build failed: %s", exc)
        raise HTTPException(status_code=400, detail="symbol data unavailable") from exc
    if not input_data.get("klines"):
        raise HTTPException(
            status_code=400,
            detail=f"no kline data for {req.symbol} on {req.interval}",
        )

    if req.background:
        job_id = svc.submit(input_data, input_id=input_id, session_id=session_id)
        return {"job_id": job_id, "status": "running"}

    return await _run_and_map(
        svc,
        input_data,
        input_id=input_id,
        session_id=session_id,
        trade_temperament=req.trade_temperament,
        incremental=req.incremental,
        history_n=req.history_n,
    )


@router.post("/analyze/stream")
async def analyze_stream(
    req: AnalyzeRequest,
    svc: Any = Depends(get_market_analysis_service),
) -> StreamingResponse:
    """SSE 流式两阶段分析。

    事件序列：analyze_start → stage1_start → stage1_text* → stage1_done →
    (gate 短路则 stage2_skipped) → 否则 stage2_start → stage2_text* →
    stage2_done → analyze_done(含完整 record JSON) → 断开。
    异常时发 analyze_fail(含 error dict)，映射复用 _exception_detail。
    """
    input_id = req.input_id or uuid.uuid4().hex
    session_id = req.session_id or uuid.uuid4().hex

    source = KlineSource()
    try:
        input_data = source.build_input_data(
            req.exchange, req.market_type, req.symbol, req.interval
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis stream input build failed: %s", exc)
        raise HTTPException(status_code=400, detail="symbol data unavailable") from exc
    if not input_data.get("klines"):
        raise HTTPException(
            status_code=400,
            detail=f"no kline data for {req.symbol} on {req.interval}",
        )

    async def event_stream():
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def sink(ev: dict[str, Any]) -> None:
            await queue.put(ev)

        async def run() -> None:
            try:
                record = await svc.analyze(
                    input_data,
                    input_id=input_id,
                    session_id=session_id,
                    on_event=sink,
                    trade_temperament=req.trade_temperament,
                    incremental=req.incremental,
                    history_n=req.history_n,
                )
                await queue.put({"type": "analyze_done", "record": record})
            except Exception as exc:  # noqa: BLE001
                status, detail = _exception_detail(exc)
                if status == 500:
                    logger.exception(
                        "market-analysis stream analyze unexpected failure"
                    )
                await queue.put({"type": "analyze_fail", "error": detail})
            finally:
                await queue.put(None)

        task = asyncio.create_task(run())
        try:
            yield _sse({"type": "analyze_start"})
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield _sse(item)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/analyze/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    svc: Any = Depends(get_market_analysis_service),
) -> JobResponse:
    """查询后台任务状态 / 结果。"""
    job = svc.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    return JobResponse(**job)


@router.delete("/analyze/{job_id}", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    svc: Any = Depends(get_market_analysis_service),
) -> JobResponse:
    """取消后台任务。"""
    if not svc.cancel(job_id):
        job = svc.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
        raise HTTPException(status_code=409, detail="job is not running")
    return JobResponse(job_id=job_id, status="cancelling")


async def _run_and_map(
    svc: Any,
    input_data: dict[str, Any],
    *,
    input_id: str,
    session_id: str,
    trade_temperament: str | None = None,
    incremental: bool = False,
    history_n: int = 0,
) -> dict[str, Any]:
    """执行分析并把异常映射为 HTTP 状态码（error dict 与 SSE analyze_fail 同契约）。"""
    try:
        return dict(
            await svc.analyze(
                input_data,
                input_id=input_id,
                session_id=session_id,
                trade_temperament=trade_temperament,
                incremental=incremental,
                history_n=history_n,
            )
        )
    except Exception as exc:  # noqa: BLE001
        status, detail = _exception_detail(exc)
        if status == 502:
            logger.error("market-analysis analyze failed: %s", exc)
        elif status == 500:
            logger.exception("market-analysis analyze unexpected failure")
        raise HTTPException(status_code=status, detail=detail) from exc


# ---------------------------------------------------------------------------
# US-302 Free Chat 自由追问 + US-304 演示回放 / 导出 / 通知（末尾追加）
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(
    req: ChatRequest,
    svc: Any = Depends(get_market_analysis_service),
) -> dict[str, Any]:
    """Free Chat 自由追问：以分析记录为种子 + 最新 K 线快照，回复并落盘 jsonl。"""
    try:
        return dict(await svc.chat(req.record_id, req.message))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis chat failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM provider unavailable") from exc


@router.get("/chat/{record_id}")
async def get_chat_history(
    record_id: str,
    svc: Any = Depends(get_market_analysis_service),
) -> dict[str, Any]:
    """读回某分析记录的聊天历史（不触发 LLM）。"""
    try:
        return dict(await svc.chat_history(record_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis chat history failed: %s", exc)
        raise HTTPException(status_code=502, detail="chat history unavailable") from exc


@router.get("/records")
async def list_records(
    symbol: str | None = None,
    limit: int = 50,
    svc: Any = Depends(get_market_analysis_service),
) -> dict[str, Any]:
    """最近分析记录（演示回放列表，复用 list_recent）。"""
    try:
        records = await svc.list_records(symbol=symbol, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis list records failed: %s", exc)
        raise HTTPException(status_code=502, detail="records unavailable") from exc
    return {"records": records}


@router.get("/records/forecast-eval")
async def forecast_eval(
    symbol: str = "BTC/USDT",
    interval: str = "1h",
    exchange: str = "binance",
    market_type: str = "futures",
    limit: int = 100,
    kline_limit: int = 12000,
    svc: Any = Depends(get_market_analysis_service),
) -> dict[str, Any]:
    """预测校准审计：历史 next_bar_forecast vs 真实后续 K 线 + 动量基线。

    必须声明在 /records/{record_id} 之前，否则 record_id="forecast-eval" 会被
    get_record 捕获。暴露预测方向/概率仅用于审计面板；Stage2Card/图表仍不
    渲染 next_bar_forecast（保持 gate 语义）。
    """
    try:
        records = await svc.list_records(symbol=symbol, limit=limit)
        df = KlineSource().load(
            exchange, market_type, symbol, interval, limit=kline_limit
        )
        subsequent = compute_subsequent_closes(records, df)
        report = evaluate_next_bar(records, subsequent, window=3)

        forecast_by_rid: dict[str, dict[str, Any]] = {}
        for rec in records:
            f = (rec.get("stage2") or {}).get("next_bar_forecast") or {}
            if f:
                forecast_by_rid[rec["meta"]["record_id"]] = f
        for row in report["rows"]:
            f = forecast_by_rid.get(row["record_id"]) or {}
            row["probability"] = f.get("probability")
            row["note"] = f.get("note")

        return {"symbol": symbol, "interval": interval, **report}
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis forecast-eval failed: %s", exc)
        raise HTTPException(
            status_code=502, detail="forecast eval unavailable"
        ) from exc


# ---------------------------------------------------------------------------
# /records/ic-panel + /records/return-report — 跨截面 IC / 多空回报面板
# ---------------------------------------------------------------------------


def _parse_symbols(symbols: str) -> list[str]:
    """Comma-separated symbol list (cross-sectional semantics)."""
    out = [s.strip() for s in symbols.split(",") if s.strip()]
    if not out:
        raise HTTPException(status_code=400, detail="empty symbol list")
    return out


def _clean(value: Any) -> Any:
    """JSON-safe scalar: float NaN / pd.NA -> None."""
    try:
        return None if pd.isna(value) else float(value)
    except (TypeError, ValueError):
        return value


def _series_payload(s: pd.Series) -> dict[str, list]:
    """Series -> {dates, values}, NaN dropped, values NaN -> None."""
    s = s.dropna()
    return {
        "dates": [
            d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            for d in s.index
        ],
        "values": [_clean(v) for v in s.to_numpy()],
    }


def _heatmap_payload(heat: pd.DataFrame) -> dict[str, Any]:
    """Year x month frame -> {years, months, z}."""
    if heat is None or heat.size == 0:
        return {"years": [], "months": [], "z": []}
    months = [str(c) for c in heat.columns]
    return {
        "years": [str(i) for i in heat.index],
        "months": months,
        "z": [[_clean(heat.loc[y, m]) for m in heat.columns] for y in heat.index],
    }


def _sector_payload(df: pd.DataFrame) -> dict[str, list]:
    """sector_ic frame -> {groups, contribs}."""
    if df is None or len(df) == 0:
        return {"groups": [], "contribs": []}
    return {
        "groups": [str(g) for g in df["group"]],
        "contribs": [_clean(v) for v in df["contrib"]],
    }


def _indus_payload(df: pd.DataFrame | None) -> dict[str, list] | None:
    """df_indus -> {industries, returns}, or None when unset/empty."""
    if df is None or len(df) == 0:
        return None
    return {
        "industries": [str(x) for x in df["industry"]],
        "returns": [_clean(v) for v in df["ret_net_ls_i_mean"]],
    }


def _turnover_payload(df_agg: pd.DataFrame, date_col: str) -> dict[str, list]:
    """df_agg['turnover_ls'] + its date column -> {dates, values}."""
    if df_agg is None or len(df_agg) == 0:
        return {"dates": [], "values": []}
    s = df_agg["turnover_ls"]
    mask = s.notna()
    dates = [
        d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
        for d in df_agg.loc[mask, date_col]
    ]
    return {"dates": dates, "values": [_clean(v) for v in s[mask]]}


def _build_cross_section_panel(
    source: KlineSource,
    symbols: list[str],
    exchange: str,
    market_type: str,
    interval: str,
    *,
    limit: int,
    period: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """K 线 cross-section -> (factor_df, ret_df) 截面面板（确定性 momentum）。

    每股 symbol：momentum = close.pct_change(period)，fut_ret_1 =
    close.shift(-1)/close - 1（末根 NaN 由下游 drop）。数据不足 period+2 根的
    symbol 跳过；仅保留所有已加载 symbol 共有的 trade_date，保证 IC / 分位
    回测的截面语义成立。
    """
    factor_rows: list[pd.DataFrame] = []
    ret_rows: list[pd.DataFrame] = []
    for sym in symbols:
        df = source.load(exchange, market_type, sym, interval, limit=limit)
        if df is None or df.empty or len(df) < period + 2:
            continue
        close = df["close"].astype(float)
        # KlineSource.load 的 timestamp 是毫秒整数；compute 层 pd.to_datetime 默认按
        # 纳秒解析，会塌缩日期跨度（days=0 → 年化因子除零）。这里显式转成 datetime。
        trade_date = pd.to_datetime(df["timestamp"], unit="ms")
        factor_rows.append(
            pd.DataFrame(
                {
                    "trade_date": trade_date,
                    "symbol": sym,
                    "factor": close.pct_change(period).to_numpy(),
                }
            )
        )
        ret_rows.append(
            pd.DataFrame(
                {
                    "trade_date": trade_date,
                    "symbol": sym,
                    "fut_ret_1": (close.shift(-1) / close - 1.0).to_numpy(),
                }
            )
        )
    if not factor_rows:
        raise ValueError("no symbol data loaded for the cross-section")
    factor_df = pd.concat(factor_rows, ignore_index=True)
    ret_df = pd.concat(ret_rows, ignore_index=True)
    loaded = factor_df["symbol"].nunique()
    full_dates = factor_df["trade_date"][
        factor_df.groupby("trade_date")["symbol"].transform("nunique") == loaded
    ].unique()
    factor_df = factor_df[factor_df["trade_date"].isin(full_dates)].copy()
    ret_df = ret_df[ret_df["trade_date"].isin(full_dates)].copy()
    return factor_df, ret_df


def _ic_report_fields(
    symbol_list: list[str],
    interval: str,
    factor: str,
    period: int,
    panel: Any,
) -> dict[str, Any]:
    """ICPanelResult → 原字段契约(序列化 series → {dates, values}, NaN → null)。"""
    return {
        "symbols": symbol_list,
        "interval": interval,
        "factor": factor,
        "period": period,
        "rolling_ic": _series_payload(panel.rolling_ic),
        "rolling_ir": _series_payload(panel.rolling_ir),
        "autocorr": _series_payload(panel.autocorr),
        "ic_distribution": {k: _clean(v) for k, v in panel.ic_distribution.items()},
        "qq_stats": {k: _clean(v) for k, v in panel.qq_stats.items()},
        "monthly_heatmap": _heatmap_payload(panel.monthly_heatmap),
        "sector_ic": _sector_payload(panel.sector_ic),
    }


def _empty_ic_fields() -> dict[str, Any]:
    """no_snapshot 空结构(与 _series_payload/_heatmap_payload 空态同形)。"""
    return {
        "rolling_ic": {"dates": [], "values": []},
        "rolling_ir": {"dates": [], "values": []},
        "autocorr": {"dates": [], "values": []},
        "ic_distribution": {},
        "qq_stats": {},
        "monthly_heatmap": {"years": [], "months": [], "z": []},
        "sector_ic": {"groups": [], "contribs": []},
    }


class MineRequest(BaseModel):
    """POST /records/ic-panel/mine 请求体(参数集即快照 key,horizon 不在其中)。"""

    symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT"
    interval: str = "1h"
    exchange: str = "binance"
    market_type: str = "futures"
    factor: str = "momentum"
    period: int = 5
    limit: int = 500
    rolling_window: int = 20
    question_id: str | None = None


@router.get("/records/ic-panel")
async def ic_panel_report(
    symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT",
    interval: str = "1h",
    exchange: str = "binance",
    market_type: str = "futures",
    factor: str = "momentum",
    period: int = 5,
    rolling_window: int = 20,
    limit: int = 500,
    db: Any = Depends(get_graph_db),
) -> dict[str, Any]:
    """IC 诊断面板 —— 只读快照（快照契约）。

    必须声明在 /records/{record_id} 之前，否则 record_id="ic-panel" 会被
    get_record 捕获。本端点零 KlineSource 零实时计算：命中快照 →
    deserialize → ICPanel().compute 从快照原始值重算衍生序列（horizon 仅
    作用 rank shift，可从同一快照全参数重算）；未命中 → 200 + no_snapshot
    空结构，拒绝模拟渲染（补算走 POST /records/ic-panel/mine）。
    """
    if factor != "momentum":
        raise HTTPException(
            status_code=400, detail=f"unsupported factor {factor!r} (only 'momentum')"
        )
    symbol_list = _parse_symbols(symbols)
    key = fs.snapshot_key(
        exchange=exchange,
        market_type=market_type,
        symbols=symbol_list,
        interval=interval,
        factor=factor,
        period=period,
        limit=limit,
    )
    snap = await fs.load_latest_snapshot(db, key=key)
    if snap is None:
        return {
            "status": "no_snapshot",
            "symbols": symbol_list,
            "interval": interval,
            "factor": factor,
            "period": period,
            **_empty_ic_fields(),
        }
    factor_df, ret_df = fs.deserialize_panel(snap["payload"]["rows"])
    panel = ICPanel().compute(
        factor_df, ret_df, horizon=1, rolling_window=rolling_window
    )
    return {
        "status": "ok",
        **_ic_report_fields(symbol_list, interval, factor, period, panel),
        "snapshot": {
            "episode_id": snap["episode_id"],
            "occurred_at": snap["occurred_at"],
            "boundary_start": snap["payload"]["boundary"]["start"],
            "boundary_end": snap["payload"]["boundary"]["end"],
            "n_rows": len(snap["payload"]["rows"]),
            "target_definition": fs.resolve_target(snap["payload"]).definition(),
            "lineage": await fs.load_lineage(db, snap["episode_id"]),
        },
    }


@router.post("/records/ic-panel/mine")
async def mine_ic_panel(
    req: MineRequest,
    db: Any = Depends(get_graph_db),
) -> dict[str, Any]:
    """实时计算 IC 面板并落库为可复算快照（唯一写路径）。

    KlineSource 实时截面 → save_snapshot（RECORDED evidence，原始值 payload）
    → 从同一面板重算衍生返回完整报告 + 快照元信息。可选 question_id 走
    addresses 边（anchor fail-fast：question 不存在 → 404，anchor 无可用键
    → 400；payload 超 5MB 闸 → 413）。
    """
    if req.factor != "momentum":
        raise HTTPException(
            status_code=400,
            detail=f"unsupported factor {req.factor!r} (only 'momentum')",
        )
    symbol_list = _parse_symbols(req.symbols)
    if req.question_id is not None:
        # anchor fail-fast 在昂贵 K 线计算之前(KeyError→404, 无可用锚键→400)
        try:
            await fs.check_question_anchor(db, req.question_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        factor_df, ret_df = _build_cross_section_panel(
            KlineSource(),
            symbol_list,
            req.exchange,
            req.market_type,
            req.interval,
            limit=req.limit,
            period=req.period,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(
            status_code=502, detail=f"kline data unavailable: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis ic-panel mine failed: %s", exc)
        raise HTTPException(status_code=502, detail="kline source unavailable") from exc
    try:
        saved = await fs.save_snapshot(
            manager=db,
            exchange=req.exchange,
            market_type=req.market_type,
            symbols=symbol_list,
            interval=req.interval,
            factor=req.factor,
            period=req.period,
            limit=req.limit,
            factor_df=factor_df,
            ret_df=ret_df,
            question_id=req.question_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
    except ValueError as exc:
        code = 413 if "payload exceeds" in str(exc) else 400
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    panel = ICPanel().compute(
        factor_df, ret_df, horizon=1, rolling_window=req.rolling_window
    )
    return {
        "status": "ok",
        **_ic_report_fields(symbol_list, req.interval, req.factor, req.period, panel),
        "snapshot": {
            "episode_id": saved["episode_id"],
            "occurred_at": saved["occurred_at"],
            "key": saved["key"],
            "n_rows": saved["n_rows"],
            "target_definition": fs.DEFAULT_TARGET.definition(),
            "lineage": await fs.load_lineage(db, saved["episode_id"]),
        },
    }


@router.get("/records/return-report")
async def return_report(
    symbols: str = "BTC/USDT,ETH/USDT,SOL/USDT",
    interval: str = "1h",
    exchange: str = "binance",
    market_type: str = "futures",
    factor: str = "momentum",
    period: int = 5,
    bins: int = 5,
    epoch: int = 20,
    limit: int = 500,
) -> dict[str, Any]:
    """多空回报面板（截面语义）：权重漂移分位回测的累计 LS 回报 / 换手 / 统计。

    必须声明在 /records/{record_id} 之前。返回序列化 WeightDriftResult 关键
    字段（cum_ret_ls / cum_ret_net_ls / turnover_ls / avg_turnover_ls /
    stats_ls / df_indus）。
    """
    if factor != "momentum":
        raise HTTPException(
            status_code=400, detail=f"unsupported factor {factor!r} (only 'momentum')"
        )
    symbol_list = _parse_symbols(symbols)
    try:
        factor_df, ret_df = _build_cross_section_panel(
            KlineSource(),
            symbol_list,
            exchange,
            market_type,
            interval,
            limit=limit,
            period=period,
        )
        result = WeightDriftBacktest().run(
            factor_df, ret_df, WeightDriftConfig(n_bins=bins, epoch=epoch)
        )
        date_col = result.config.trade_date_col if result.config else "trade_date"
        return {
            "symbols": symbol_list,
            "interval": interval,
            "factor": factor,
            "period": period,
            "n_bins": bins,
            "cum_ret_ls": _series_payload(result.cum_ret_ls),
            "cum_ret_net_ls": _series_payload(result.cum_ret_net_ls),
            "turnover_ls": _turnover_payload(result.df_agg, date_col),
            "avg_turnover_ls": _clean(result.avg_turnover_ls),
            "mean_ret": _clean(result.mean_ret),
            "stats_ls": {k: _clean(v) for k, v in result.stats_ls.items()},
            "df_indus": _indus_payload(result.df_indus),
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis return-report failed: %s", exc)
        raise HTTPException(
            status_code=502, detail="return report unavailable"
        ) from exc


@router.get("/records/{record_id}")
async def get_record(
    record_id: str,
    svc: Any = Depends(get_market_analysis_service),
) -> dict[str, Any]:
    """取回完整分析记录（演示回放）。"""
    record = await svc.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"record '{record_id}' not found")
    return dict(record)


@router.get("/records/{record_id}/export.csv")
async def export_record_csv(
    record_id: str,
    svc: Any = Depends(get_market_analysis_service),
) -> Response:
    """导出记录 K 线为 CSV（可下载）。"""
    record = await svc.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"record '{record_id}' not found")
    return Response(
        content=_build_record_csv(record),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{record_id}.csv"'},
    )


@router.post("/notify")
async def send_notification(
    req: NotifyRequest,
    svc: Any = Depends(get_market_analysis_service),
) -> dict[str, Any]:
    """后端触发分析结果通知（飞书/PushPlus，env 开关默认关；webhook 密钥不暴露前端）。"""
    from app.core.notify_webhook import AnalysisNotifier, render_analysis_brief

    record = None
    if req.record_id:
        record = await svc.get_record(req.record_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"record '{req.record_id}' not found"
            )
    text = (
        render_analysis_brief(record)
        if record
        else (req.message or "selene 行情分析通知")
    )
    notifier = AnalysisNotifier()
    try:
        return await notifier.send(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("market-analysis notify failed: %s", exc)
        raise HTTPException(status_code=502, detail="notify unavailable") from exc
    finally:
        await notifier.close()


def _sse(event: dict[str, Any]) -> str:
    """单条 SSE data 帧：``data: {json}\\n\\n``。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _exception_detail(exc: Exception) -> tuple[int, dict[str, Any]]:
    """异常 → (HTTP 状态码, error dict)；/analyze 与 /analyze/stream 共用同一契约。"""
    if isinstance(exc, CheatError):
        return 422, {
            "error": "cheat_detected",
            "field": exc.field,
            "message": exc.message,
        }
    if isinstance(exc, SemanticError):
        return 422, {"error": "semantic_error", "message": exc.message}
    if isinstance(exc, ValidationError):
        return 400, {
            "error": "validation_failed",
            "layer": exc.layer,
            "message": exc.message,
        }
    if isinstance(exc, MarketAnalysisError):
        return 502, {"error": "provider_unavailable", "message": str(exc)}
    return 500, {"error": "internal_error", "message": "Internal server error"}


def _build_record_csv(record: dict[str, Any]) -> str:
    """从记录生成 K 线 CSV（可下载，含指标摘要尾部块）。"""
    input_data = record.get("input_data") or {}
    klines = input_data.get("klines") or []
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
    for k in klines:
        writer.writerow(
            [
                k.get("timestamp", ""),
                k.get("open", ""),
                k.get("high", ""),
                k.get("low", ""),
                k.get("close", ""),
                k.get("volume", ""),
            ]
        )
    bar_features = input_data.get("bar_features") or {}
    if isinstance(bar_features, dict) and bar_features:
        writer.writerow([])
        writer.writerow(["bar_features"])
        for key, value in bar_features.items():
            writer.writerow([key, value])
    return buf.getvalue()
