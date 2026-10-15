"""Decision-workflow FastAPI router (Phase 2).

Exposes 4 endpoints:
- POST /analyze: synchronous two-stage analysis
- GET  /records/{record_id}: read a previously persisted record
- GET  /trees: list available decision trees
- GET  /health: health check

v1 uses CannedLLMProvider + in-memory persistence. Real LLM + SQLite deferred
to Phase 4. Records are lost on process restart.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from app.core.decision_workflow_service import DecisionWorkflowService
from selene_space_core.kline_paths import default_klines_db_path
from app.core.dependencies import get_decision_workflow_service
from app.core.pa_heartbeat import heartbeat_enabled
from app.core.price_alert_service import PriceAlertService
from selene_space_decision_workflow.exceptions import (
    CheatError,
    DecisionWorkflowError,
    SemanticError,
    ValidationError,
)
from selene_space_decision_workflow.models import AnalysisRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/decision-workflow", tags=["decision-workflow"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AnalyzeRequest(BaseModel):
    tree_id: str
    input_data: dict[str, Any]
    input_id: str | None = None
    session_id: str | None = None


class TradingAnalyzeRequest(BaseModel):
    """POST /analyze-trading: full PA pipeline, service-side bar fetch."""

    symbol: str = "BTC/USDT"
    interval: str = "1h"
    bars: int = 120
    market_type: str = "futures"
    trading_stance: str | None = None
    # Optional higher-timeframe context (B2). The endpoint fetches the HTF
    # series from the same klines.db and aggregates htf_summary itself —
    # the frontend only names the interval.
    htf_interval: str | None = None
    input_id: str | None = None
    session_id: str | None = None
    # Extend the newest same-symbol/same-interval diagnosis instead of
    # re-reading the full window: the prior Stage1 + the bars that arrived
    # since, with the deterministic continuity guard applied over those bars.
    # Degrades to 422 (no prior / no new bars), never to a silent full run.
    incremental: bool = False
    # Phase D: run the REAL pipeline as a badged demo (record kept out of the
    # audit table so the outcome review never judges a demo order). Distinct
    # from SELENE_PA_DEMO_MODE, which downgrades to the canned walk-through.
    demo: bool = False


class AnalyzeResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    meta: dict[str, Any]
    input_data: dict[str, Any]
    stage1: dict[str, Any]
    stage2: dict[str, Any] | None
    strategy_files_used: list[str]
    usage_total: dict[str, int]
    # W2 audit keys. Declared here because Pydantic v2 drops unknown top-level
    # keys on the response model — without these the outcome state machine
    # (win/loss/expired/timeout_open) and the backtest attachment never reach
    # the frontend even though the record carries them.
    outcome: dict[str, Any] | None = None
    backtest_verification: dict[str, Any] | None = None
    # Anchored follow-up trail (Phase D), append-ordered. Declared for the
    # same reason as the two audit keys above — an undeclared top-level key
    # never survives the projection.
    followups: list[dict[str, Any]] | None = None


def _to_response(record: AnalysisRecord) -> AnalyzeResponse:
    """Project an AnalysisRecord onto the wire model.

    Single construction site so the audit keys (outcome /
    backtest_verification / followups) cannot be dropped from one endpoint
    while the others keep them — which is exactly how the win/loss loop
    stayed invisible to the frontend.
    """
    return AnalyzeResponse(
        meta=record.meta.model_dump(),
        input_data=record.input_data,
        stage1=record.stage1.model_dump(),
        stage2=record.stage2.model_dump() if record.stage2 else None,
        strategy_files_used=list(record.strategy_files_used),
        usage_total=dict(record.usage_total),
        outcome=record.outcome,
        backtest_verification=record.backtest_verification,
        followups=record.followups,
    )


class TreeInfoResponse(BaseModel):
    tree_id: str
    title: str
    description: str
    node_count: int


class TreeGraphResponse(BaseModel):
    """Read-only serialized graph (B5 visualization) — parser single source."""

    tree_id: str
    entry_node_id: str
    terminals: list[str]
    nodes: dict[str, dict[str, Any]]


class HealthResponse(BaseModel):
    status: str
    trees_loaded: int
    llm_provider: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health(
    svc: DecisionWorkflowService = Depends(get_decision_workflow_service),
) -> HealthResponse:
    return HealthResponse(
        status="ok",
        trees_loaded=len(svc.list_trees()),
        llm_provider="canned+real-trading",
    )


@router.get("/trees", response_model=list[TreeInfoResponse])
async def list_trees(
    svc: DecisionWorkflowService = Depends(get_decision_workflow_service),
) -> list[TreeInfoResponse]:
    out: list[TreeInfoResponse] = []
    for t in svc.list_trees():
        out.append(
            TreeInfoResponse(
                tree_id=t.tree_id,
                title=t.title,
                description=t.description,
                node_count=t.node_count,
            )
        )
    return out


@router.get("/trees/{tree_id}/graph", response_model=TreeGraphResponse)
async def get_tree_graph(
    tree_id: str,
    svc: DecisionWorkflowService = Depends(get_decision_workflow_service),
) -> TreeGraphResponse:
    """B5: parsed decision tree as JSON for the frontend visualization."""
    graph = svc.get_tree_graph(tree_id)
    if graph is None:
        raise HTTPException(status_code=404, detail=f"tree '{tree_id}' not found")
    return TreeGraphResponse(**graph)


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(
    req: AnalyzeRequest,
    svc: DecisionWorkflowService = Depends(get_decision_workflow_service),
) -> AnalyzeResponse:
    # Auto-generate input_id / session_id if not provided.
    input_id = req.input_id or uuid.uuid4().hex
    session_id = req.session_id or uuid.uuid4().hex
    try:
        record = svc.analyze(
            req.tree_id,
            req.input_data,
            input_id=input_id,
            session_id=session_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"tree '{req.tree_id}' not found")
    except CheatError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "cheat_detected",
                "field": exc.field,
                "message": exc.message,
            },
        )
    except SemanticError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "semantic_error",
                "message": exc.message,
                "node_id": exc.node_id,
            },
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_failed",
                "layer": exc.layer,
                "message": exc.message,
            },
        )
    except DecisionWorkflowError as exc:
        logger.error("decision-workflow analyze workflow_error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    except Exception as exc:  # noqa: BLE001
        logger.exception("decision-workflow analyze failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    return _to_response(record)


async def _load_incremental_prior(
    svc: DecisionWorkflowService, symbol: str, interval: str
) -> dict[str, Any] | None:
    """Newest prior diagnosis for symbol/interval, or None.

    Reaches the record repo the same duck-typed way pa_outcome does (the
    service builds it lazily, and only when a db is wired). An unwired or
    failing repo degrades to "no prior" — the caller turns that into a 422,
    never a 500.
    """
    from app.core.trading_input import resolve_prior_record

    getter = getattr(svc, "_get_record_repo", None)
    if getter is None:
        return None
    repo = await getter()
    if repo is None:
        return None
    try:
        # 50 is list_records' default scan window. ponytail: a symbol whose
        # newest 50 records are all other intervals would 422 "no prior" here
        # despite one existing — page or filter by interval if that shows up.
        # Demo records never land in this table, so they cannot become priors.
        records = await repo.list_records(symbol=symbol, limit=50)
    except Exception:  # noqa: BLE001
        logger.warning("incremental prior: list failed for %s", symbol, exc_info=True)
        return None
    return resolve_prior_record(records, symbol, interval)


async def _assemble_incremental_input(
    svc: DecisionWorkflowService,
    klines_db: Path,
    req: TradingAnalyzeRequest,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Incremental assembly — the HTTP counterpart of the CLI's ``--incremental``
    branch (scripts/run_pa_diagnosis.py).

    Both halves of that branch travel together: ``incremental_input`` builds
    the K-continued window and ``evaluate_continuity`` applies the
    deterministic P2-001 guard over the newly arrived bars. Wiring only the
    former drops the guard silently — no error, just a wrong diagnosis.
    """
    from app.core.trading_input import build_htf_summary
    from selene_space_decision_workflow.continuity import evaluate_continuity
    from selene_space_decision_workflow.trading_pipeline import (
        enrich_input_data,
        incremental_input,
        load_bars,
    )

    prior = await _load_incremental_prior(svc, req.symbol, req.interval)
    if prior is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "no_incremental_prior",
                "message": (
                    f"no prior {req.symbol} {req.interval} diagnosis to extend"
                    " — run a full diagnosis first"
                ),
            },
        )
    all_bars = load_bars(
        klines_db, req.symbol, req.interval, req.bars, market_type=req.market_type
    )
    try:
        input_data, new_bars, last_decision = incremental_input(
            prior, all_bars, req.symbol, req.interval
        )
    except RuntimeError as exc:
        # incremental_input's only RuntimeError: nothing arrived since the
        # prior window ended.
        raise HTTPException(
            status_code=422,
            detail={"error": "no_new_bars", "message": str(exc)},
        ) from exc
    if last_decision is not None:
        guard = evaluate_continuity(last_decision, new_bars)
        input_data["continuity_guard"] = {
            "allowed": guard.allowed,
            "rule": guard.rule,
            "reason": guard.reason,
        }
    # Shared material blocks (four windows / overlays / experiences) over the
    # full bar list — the same enrichment the full-window branch gets.
    input_data, _overlays_hit = enrich_input_data(
        input_data, all_bars, req.symbol, req.interval
    )
    htf_summary_data = build_htf_summary(
        klines_db,
        req.symbol,
        bars=req.bars,
        market_type=req.market_type,
        htf_interval=req.htf_interval,
    )
    return input_data, htf_summary_data


@router.post("/analyze-trading", response_model=AnalyzeResponse)
async def analyze_trading(
    req: TradingAnalyzeRequest,
    svc: DecisionWorkflowService = Depends(get_decision_workflow_service),
) -> AnalyzeResponse:
    """Full PA pipeline (real LLM) — the service counterpart of
    run_pa_diagnosis.py. Fetches bars from the local klines.db, assembles
    the full-window input (bar summary + four windows + overlays +
    experiences + stats baseline), then runs the two-stage workflow.

    ``incremental=true`` swaps the assembly for the prior-extending branch
    (prior Stage1 + bars arrived since + the continuity guard); the workflow
    call itself is unchanged.

    Long-running (2 real LLM calls, ~1-3 min). Returns the same record
    shape as POST /analyze.
    """
    from app.core.trading_input import build_trading_input

    input_id = req.input_id or uuid.uuid4().hex
    session_id = req.session_id or uuid.uuid4().hex

    klines_db = default_klines_db_path()
    if not klines_db.is_file():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "klines_db_unavailable",
                "message": f"klines db not found at {klines_db} — sync market data first",
            },
        )
    try:
        # Bar fetch + HTF aggregation live in build_trading_input so the PA
        # heartbeat feeds the workflow the identical input shape. A thin HTF
        # series degrades to "omitted" (CLI parity: run_pa_diagnosis B2
        # branch); it never aborts the primary diagnosis.
        if req.incremental:
            input_data, htf_summary_data = await _assemble_incremental_input(
                svc, klines_db, req
            )
        else:
            input_data, htf_summary_data = build_trading_input(
                klines_db,
                req.symbol,
                req.interval,
                bars=req.bars,
                market_type=req.market_type,
                htf_interval=req.htf_interval,
            )
        record = await svc.analyze_trading(
            input_data,
            input_id=input_id,
            session_id=session_id,
            trading_stance=req.trading_stance,
            htf_summary_data=htf_summary_data,
            demo=req.demo,
        )
    except HTTPException:
        raise
    except RuntimeError as exc:
        # load_bars raises RuntimeError("insufficient bars…") for thin series;
        # LLM-not-configured also surfaces as RuntimeError.
        if "insufficient bars" in str(exc):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "insufficient_bars",
                    "message": str(exc),
                },
            )
        raise HTTPException(
            status_code=503,
            detail={"error": "llm_not_configured", "message": str(exc)},
        )
    except CheatError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "cheat_detected",
                "field": exc.field,
                "message": exc.message,
            },
        )
    except SemanticError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "semantic_error",
                "message": exc.message,
                "node_id": exc.node_id,
            },
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_failed",
                "layer": exc.layer,
                "message": exc.message,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("decision-workflow analyze_trading failed")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
    return _to_response(record)


class RecordListItem(BaseModel):
    """Compact row for the review panel list (GET /records).

    Deliberately flat — the full record is one GET /records/{id} away,
    so the list carries only what a row needs to answer "what happened
    to the thing I waited on".
    """

    record_id: str
    timestamp: str
    demo: bool
    symbol: str | None
    interval: str | None
    decision: str | None
    direction: str | None
    outcome: dict[str, Any] | None


class RecordListResponse(BaseModel):
    records: list[RecordListItem]


@router.get("/records", response_model=RecordListResponse)
async def list_records(
    symbol: str | None = None,
    interval: str | None = None,
    limit: int = 50,
    svc: DecisionWorkflowService = Depends(get_decision_workflow_service),
) -> RecordListResponse:
    records = await svc.list_records(
        symbol=symbol, interval=interval, limit=min(limit, 200)
    )
    rows: list[RecordListItem] = []
    for r in records:
        # Direction arm = stage1 (B2 counterfactual scores stage1's
        # next_period.direction — that's the population C2 Phase B measures).
        stage1_diagnosis = r.stage1.diagnosis_or_decision if r.stage1 else {}
        summary = r.input_data.get("summary") or {}
        direction = stage1_diagnosis.get("next_period", {}).get("direction")
        rows.append(
            RecordListItem(
                record_id=r.meta.input_id,
                timestamp=r.meta.timestamp,
                demo=r.meta.demo,
                symbol=summary.get("symbol"),
                interval=summary.get("interval"),
                decision=(r.stage2.decision if r.stage2 else None),
                direction=direction,
                outcome=r.outcome,
            )
        )
    return RecordListResponse(records=rows)


@router.get("/records/{record_id}", response_model=AnalyzeResponse)
async def get_record(
    record_id: str,
    svc: DecisionWorkflowService = Depends(get_decision_workflow_service),
) -> AnalyzeResponse:
    record = await svc.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"record '{record_id}' not found")
    return _to_response(record)


class FollowUpRequest(BaseModel):
    """Body for POST /records/{record_id}/followup."""

    question: str


class FollowUpResponse(BaseModel):
    record_id: str
    # Append-ordered full trail, so the client renders what was persisted
    # rather than maintaining its own copy of the ordering.
    followups: list[dict[str, Any]]


def _assemble_live_context(
    record: AnalysisRecord, market_type: str = "futures"
) -> dict[str, Any] | None:
    """Fresh "now" reading for a hybrid follow-up (W3), or None.

    Reads the newest bar for the record's symbol/interval from the same
    klines.db /analyze-trading uses, and counts how many bars the record's
    window end is behind. Any gap (no db, empty series, missing keys,
    unknown interval) degrades to None → the fully-anchored prompt, zero
    new failure modes. Lives at the endpoint layer: the service stays
    hermetic and this is the layer that owns SELENE_KLINES_DB.
    """
    from app.core.pa_heartbeat import _INTERVAL_MS
    from app.core.trading_input import latest_bar_snapshot

    input_data = record.input_data or {}
    summary = input_data.get("summary") or {}
    symbol = input_data.get("symbol")
    interval = input_data.get("interval")
    window_end = summary.get("window_end_iso")
    if not symbol or not interval or window_end is None:
        return None
    interval_ms = _INTERVAL_MS.get(interval)
    if interval_ms is None:
        return None
    klines_db = default_klines_db_path()
    snap = latest_bar_snapshot(klines_db, symbol, interval, market_type)
    if snap is None:
        return None
    # window_end is stored as ms epoch despite the _iso suffix (klines.db
    # timestamp column); tolerate the ISO-string shape older/demo records
    # may carry.
    end_ms: int | None
    if isinstance(window_end, (int, float)):
        end_ms = int(window_end)
    else:
        try:
            from datetime import datetime

            end_ms = int(datetime.fromisoformat(str(window_end)).timestamp() * 1000)
        except ValueError:
            return None
    bars_since = max(0, round((snap["last_bar_ts"] - end_ms) / interval_ms))
    return {
        "last_close": snap["last_close"],
        "last_bar_ts": snap["last_bar_ts"],
        "bars_since_end": bars_since,
    }


async def _project_followup_to_mission(
    record: AnalysisRecord | None,
    record_id: str,
    question: str,
    *,
    used_live: bool,
) -> None:
    """Best-effort mirror of the Q&A onto the Mission that owns this record.

    Runs only after the follow-up is already persisted on the record; the
    Mission timeline is a second reader of it, so every failure here degrades
    to "one missing activity entry" and must never fail the request.
    """
    symbol = (record.input_data or {}).get("symbol") if record is not None else None
    if not symbol:
        return
    try:
        from app.core.pa_mission_sensor import sync_followup_to_mission

        await sync_followup_to_mission(
            symbol=str(symbol),
            record_id=record_id,
            question=question,
            used_live=used_live,
        )
    except Exception:  # noqa: BLE001 — Mission projection is best-effort
        logger.warning("follow-up → Mission projection failed", exc_info=True)


@router.post("/records/{record_id}/followup", response_model=FollowUpResponse)
async def create_record_followup(
    record_id: str,
    req: FollowUpRequest,
    svc: DecisionWorkflowService = Depends(get_decision_workflow_service),
) -> FollowUpResponse:
    """Ask a follow-up anchored to one persisted record.

    The record id is the anchor: the question is answered against that
    record's frozen snapshot and the Q&A pair is appended to it, so the
    audit trail lives with the decision it interrogates.

    W3 hybrid: when a fresh bar can be read for the record's symbol, the
    endpoint hands the service a labeled live_context (RECONSTRUCTED "now"
    reading) alongside the frozen record (RECORDED) — and the persisted
    entry carries ``used_live`` so the trail shows which answers saw it.
    """
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be empty")
    try:
        record = await svc.get_record(record_id)
        live_context = _assemble_live_context(record) if record is not None else None
        followups = await svc.record_followup(
            record_id, question, live_context=live_context
        )
    except RuntimeError as exc:
        # Storage unwired — an unanswered question, not an unknown record.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if followups is None:
        raise HTTPException(status_code=404, detail=f"record '{record_id}' not found")
    await _project_followup_to_mission(
        record, record_id, question, used_live=live_context is not None
    )
    return FollowUpResponse(record_id=record_id, followups=followups)


# ---------------------------------------------------------------------------
# Price alerts (S7) — in-memory one-shot rules polled by the PA alert loop
# ---------------------------------------------------------------------------


class PriceAlertCreateRequest(BaseModel):
    """Body for POST /price-alerts."""

    symbol: str
    interval: str
    direction: str | None = None
    input_id: str | None = None
    session_id: str | None = None
    levels: dict[str, float | None]


class PriceAlertCreateResponse(BaseModel):
    id: int
    status: str


class PriceAlertListResponse(BaseModel):
    alerts: list[dict[str, Any]]


class PriceAlertDeleteResponse(BaseModel):
    deleted: bool


def _get_alert_svc() -> PriceAlertService:
    from app.core.price_alert_service import get_price_alert_service

    return get_price_alert_service()


@router.post("/price-alerts", response_model=PriceAlertCreateResponse, status_code=201)
async def create_price_alert(
    req: PriceAlertCreateRequest,
    alert_svc: PriceAlertService = Depends(_get_alert_svc),
) -> PriceAlertCreateResponse:
    rule = alert_svc.create_alert(
        symbol=req.symbol,
        interval=req.interval,
        direction=req.direction,
        input_id=req.input_id,
        session_id=req.session_id,
        levels=req.levels,
    )
    return PriceAlertCreateResponse(id=rule["id"], status="registered")


@router.get("/price-alerts", response_model=PriceAlertListResponse)
async def list_price_alerts(
    alert_svc: PriceAlertService = Depends(_get_alert_svc),
) -> PriceAlertListResponse:
    return PriceAlertListResponse(alerts=alert_svc.list_alerts())


@router.delete("/price-alerts/{alert_id}", response_model=PriceAlertDeleteResponse)
async def delete_price_alert(
    alert_id: int,
    alert_svc: PriceAlertService = Depends(_get_alert_svc),
) -> PriceAlertDeleteResponse:
    return PriceAlertDeleteResponse(deleted=alert_svc.delete_alert(alert_id))


# ---------------------------------------------------------------------------
# Heartbeat status (T1 小-1) — expose PaHeartbeatScheduler.status() projection
# ---------------------------------------------------------------------------


class HeartbeatStatusResponse(BaseModel):
    """Projection of ``PaHeartbeatScheduler.status()``.

    ``enabled`` mirrors the ``SELENE_PA_HEARTBEAT`` gate; when the loop is off
    (or the scheduler instance is absent) the remaining fields stay at
    defaults so the endpoint never fails on a disabled feature.
    """

    enabled: bool
    last_round: dict[str, Any] | None = None
    attempts_today: int = 0
    budget_day: str | None = None
    busy: bool = False
    # Epoch seconds of the next daily firing window (from config.hour_utc, not a
    # rolling interval) — the frontend counts down to it.
    next_fire_at: float | None = None
    config: dict[str, Any] | None = None


@router.get("/heartbeat/status", response_model=HeartbeatStatusResponse)
async def get_heartbeat_status(
    request: Request,
) -> HeartbeatStatusResponse:
    enabled = heartbeat_enabled()
    sched = getattr(request.app.state, "_pa_heartbeat", None)
    if sched is None or not enabled:
        return HeartbeatStatusResponse(enabled=enabled)
    st = sched.status()
    return HeartbeatStatusResponse(
        enabled=True,
        last_round=st["last_round"],
        attempts_today=st["attempts_today"],
        budget_day=st["budget_day"],
        busy=st["busy"],
        next_fire_at=st["next_fire_at"],
        config=st["config"],
    )
