"""Alpha mining API endpoints — factor discovery and formula management.

Provides endpoints for:
- Starting/stopping training sessions
- Listing discovered formulas
- Backtesting and deploying formulas
- Getting vocab information
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import io
import json
import logging
import math
import os
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    import pandas as pd

from selene_space_types.common import APIResponse
from selene_space_core.infra.atomic_write import atomic_write
from selene_space_core.kline_paths import default_klines_db_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alpha", tags=["alpha"])

# ---------------------------------------------------------------------------
# In-memory session store (production would use DB)
# ---------------------------------------------------------------------------

_sessions: dict[str, dict[str, Any]] = {}
_results_dir = Path("data/alpha_results")

#: Backtest-visible archive dir — CWD-relative to match ``backtests.py``'s
#: ``Path("data/strategies")`` lookup, so accept -> deploy -> backtest resolves
#: ``alpha:{id}`` with no manual copying.
_deploy_archive_dir = Path("data/strategies/alpha_v_deploy")

# Guard all _sessions writes (start/on_step/complete/stop) from the
# background training thread against event-loop reads (status/logs).
_sessions_lock = threading.Lock()
# Ring-buffer cap for per-session training logs (GET /train/{id}/logs).
_LOG_RING_SIZE = 200

# Batch fan-out registry (POST /train/batch): batch_id -> batch record.
# Guarded by the same lock as _sessions: batch reads are O(sessions) dict
# copies, far cheaper than the per-step appends they share the lock with.
_batches: dict[str, dict[str, Any]] = {}


def _checkpoint_path(session_id: str) -> Path:
    """Where a session's training checkpoint lives (resume/断点续训)."""
    return _results_dir / session_id / "checkpoint.pt"


def _repo_root() -> Path:
    """Project root (alpha.py -> v1 -> api -> app -> local-api -> services -> root)."""
    return Path(__file__).resolve().parent.parent.parent.parent.parent.parent


_strategies_dir = _repo_root() / "data" / "strategies"


def _default_klines_db_path() -> Path:
    """Resolve the shared klines SQLite DB (canonical repo-root copy).

    Delegates to the single-source helper so this module agrees with every
    other consumer; the old exists()-based fork silently diverged instead.
    """
    # cast: mypy resolves selene_space_core as Any (the package is not on its
    # search path), which would otherwise trip warn_return_any here. The helper
    # is declared ``-> Path``, so the annotation is exact, not a suppression.
    return cast(Path, default_klines_db_path())


_PARQUET_REQUIRED_COLUMNS = ("time", "open", "high", "low", "close", "volume")


def _load_parquet_df(data_file: str) -> pd.DataFrame:
    """Read a user-supplied parquet kline file, mirroring the backtest
    endpoint's path sandbox (``~/.selenespace/data`` or ``demo/data``).

    Raises ``FileNotFoundError`` / ``ValueError`` so ``_run_training``'s
    existing synthetic-fallback branch handles bad input identically to a
    missing DB.
    """
    import pandas as pd

    path = Path(data_file)
    if not path.is_absolute():
        # Bare filenames resolve against the sandbox roots first (server CWD
        # is services/local-api, which is not a sandbox root).
        safe_root = Path.home() / ".selenespace" / "data"
        demo_root = Path.cwd() / "demo" / "data"
        for root in (safe_root, demo_root):
            candidate = root / path
            if candidate.exists():
                path = candidate
                break
        else:
            path = Path.cwd() / path
    safe_root = Path.home() / ".selenespace" / "data"
    demo_root = Path.cwd() / "demo" / "data"
    resolved = path.resolve()
    if not (
        str(resolved).startswith(str(safe_root.resolve()))
        or str(resolved).startswith(str(demo_root.resolve()))
    ):
        raise ValueError(f"data_file must be under {safe_root} or {demo_root}")
    if path.suffix != ".parquet" or not path.exists():
        raise FileNotFoundError(f"Parquet file not found: {data_file}")
    df = pd.read_parquet(path)
    missing = [c for c in _PARQUET_REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Parquet file missing required columns: {missing}")
    if df.empty:
        raise ValueError("Parquet file contains no rows")
    return df


class TrainRequest(BaseModel):
    train_steps: int = Field(
        100, ge=1, le=10000, description="Number of training steps"
    )
    batch_size: int = Field(256, ge=1, le=8192, description="Batch size")
    max_formula_len: int = Field(
        12, ge=4, le=32, description="Max formula token length"
    )
    use_lord: bool = Field(True, description="Enable LoRD regularization")
    symbol: str = Field("BTC/USDT", description="Trading pair for real kline data")
    exchange: str = Field("binance", description="Exchange (binance/okx)")
    market_type: str = Field("futures", description="Market type (spot/futures)")
    interval: str = Field("1h", description="Kline interval")
    use_synthetic_data: bool = Field(
        False, description="Use synthetic data if DB unavailable"
    )
    data_file: str = Field(
        "",
        description=(
            "Optional path to a .parquet kline file (under ~/.selenespace/data "
            "or demo/data). Takes priority over klines.db when set."
        ),
    )
    start_step: int = Field(
        0, ge=0, description="Resume training from this step (checkpoint)"
    )


class BatchTrainRequest(TrainRequest):
    """Batch variant of :class:`TrainRequest`.

    One training session is spawned per ``(symbol, interval)`` pair in the
    cartesian product of ``symbols`` x ``intervals``. The inherited singular
    ``symbol`` / ``interval`` are ignored here — the pair list is authoritative.
    """

    symbols: list[str] = Field(
        default_factory=list,
        min_length=1,
        description="Trading pairs to mine (one session per pair x interval)",
    )
    intervals: list[str] = Field(
        default_factory=list,
        min_length=1,
        description="Kline intervals to mine (one session per pair x interval)",
    )
    max_concurrency: int | None = Field(
        None,
        ge=1,
        le=32,
        description=(
            "Concurrent sessions for this batch. Falls back to "
            "SELENE_ALPHA_BATCH_MAX_CONCURRENCY (default 4) when omitted."
        ),
    )


class FormulaInfo(BaseModel):
    formula_id: str
    formula_tokens: list[int]
    formula_readable: str
    best_score: float
    created_at: float


def _new_session_record(
    request: TrainRequest, *, status: str = "starting"
) -> dict[str, Any]:
    """Seed a session dict. Shared by POST /train and the batch fan-out so the
    two paths cannot drift on the history-key contract the frontend reads."""
    return {
        "status": status,
        "step": 0,
        "best_score": -float("inf"),
        "train_steps": request.train_steps,
        "config": request.model_dump(),
        "started_at": time.time(),
        "history": {
            "step": [],
            "avg_reward": [],
            "best_score": [],
            "stable_rank": [],
            "rank_step": [],
            "reward_std": [],
        },
        "logs": [],
    }


_DEFAULT_BATCH_MAX_CONCURRENCY = 4


def _batch_max_concurrency(override: int | None = None) -> int:
    """Resolve the batch concurrency cap.

    Every session lands in the process-wide default executor pool
    (``run_in_executor(None, ...)`` below), which is CPU-bound and shared with
    any other training — an uncapped fan-out thrashes it. Hence a cap rather
    than "as many as the request asks for".
    """
    if override is not None:
        return override
    raw = os.environ.get("SELENE_ALPHA_BATCH_MAX_CONCURRENCY")
    if raw is None:
        return _DEFAULT_BATCH_MAX_CONCURRENCY
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning(
            "Invalid SELENE_ALPHA_BATCH_MAX_CONCURRENCY=%r; using default %d",
            raw,
            _DEFAULT_BATCH_MAX_CONCURRENCY,
        )
        return _DEFAULT_BATCH_MAX_CONCURRENCY


def _batch_session_request(
    request: BatchTrainRequest, symbol: str, interval: str
) -> TrainRequest:
    """Project the batch request onto one (symbol, interval) session.

    Everything except the three batch-only fields projects straight through, so
    a new TrainRequest knob reaches batch sessions without touching this.
    """
    payload = request.model_dump(exclude={"symbols", "intervals", "max_concurrency"})
    payload["symbol"] = symbol
    payload["interval"] = interval
    return TrainRequest(**payload)


# Session statuses that mean the training thread has not finished yet.
_ACTIVE_SESSION_STATUSES = frozenset({"queued", "starting", "running", "stopping"})


def _batch_status_from_sessions(statuses: list[str]) -> str:
    """Roll session statuses up to one batch status.

    Any live session keeps the batch ``running``; once every session is
    terminal, an all-completed batch is ``completed``, an all-failed one is
    ``failed``, and anything mixed is ``partial`` — no rounding a partially
    broken batch up to success.
    """
    if not statuses:
        return "failed"
    if any(status in _ACTIVE_SESSION_STATUSES for status in statuses):
        return "running"
    if all(status == "completed" for status in statuses):
        return "completed"
    if all(status == "failed" for status in statuses):
        return "failed"
    return "partial"


async def _run_batch(
    batch_id: str, plans: list[tuple[str, TrainRequest]], cap: int
) -> None:
    """Fan out a batch under a concurrency cap, then freeze its final status.

    ``_run_training`` already traps its own exceptions and records
    ``status='failed'`` + ``error`` on the session. The gather guard here covers
    the residual case where the call itself raises: without it the session would
    stay ``queued`` forever and pin the batch status at ``running``.
    """
    semaphore = asyncio.Semaphore(cap)

    async def _one(session_id: str, session_request: TrainRequest) -> None:
        async with semaphore:
            await _run_training(session_id, session_request)

    results = await asyncio.gather(
        *(_one(session_id, session_request) for session_id, session_request in plans),
        return_exceptions=True,
    )

    with _sessions_lock:
        for (session_id, _), outcome in zip(plans, results, strict=True):
            if not isinstance(outcome, BaseException):
                continue
            logger.error(
                "Batch %s session %s escaped: %s", batch_id, session_id, outcome
            )
            record = _sessions.get(session_id)
            if record is not None:
                record["status"] = "failed"
                record.setdefault("error", str(outcome))
        batch = _batches.get(batch_id)
        if batch is not None:
            batch["status"] = _batch_status_from_sessions(
                [
                    (_sessions.get(sid) or {}).get("status", "missing")
                    for sid in batch["session_ids"]
                ]
            )
            batch["finished_at"] = time.time()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/train")
async def start_training(request: TrainRequest) -> APIResponse:
    """Start a new alpha mining training session."""
    session_id = f"alpha_{int(time.time())}"

    with _sessions_lock:
        _sessions[session_id] = _new_session_record(request)

    # Run training in background
    asyncio.create_task(_run_training(session_id, request))

    return APIResponse(
        success=True,
        data={"session_id": session_id, "status": "starting"},
        message="Training session started",
    )


@router.post("/train/batch")
async def start_batch_training(request: BatchTrainRequest) -> APIResponse:
    """Start one training session per (symbol, interval) pair.

    Thin orchestrator: every session runs the same ``_run_training`` path as
    POST /train, gated by a semaphore so the shared executor pool is not
    thrashed. No second session store, no engine changes.
    """
    pairs = [
        (symbol, interval)
        for symbol in request.symbols
        for interval in request.intervals
    ]
    batch_id = f"batch_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    cap = _batch_max_concurrency(request.max_concurrency)

    plans: list[tuple[str, TrainRequest]] = []
    with _sessions_lock:
        for index, (symbol, interval) in enumerate(pairs):
            # ``int(time.time())`` alone collides when the fan-out seeds several
            # sessions inside one tick — and the id is a checkpoint directory
            # name, so a collision would clobber training output.
            session_id = f"alpha_{int(time.time())}_{batch_id[-8:]}_{index}"
            session_request = _batch_session_request(request, symbol, interval)
            _sessions[session_id] = _new_session_record(
                session_request, status="queued"
            )
            plans.append((session_id, session_request))
        _batches[batch_id] = {
            "status": "running",
            "session_ids": [session_id for session_id, _ in plans],
            "symbols": list(request.symbols),
            "intervals": list(request.intervals),
            "max_concurrency": cap,
            "started_at": time.time(),
        }

    asyncio.create_task(_run_batch(batch_id, plans, cap))

    return APIResponse(
        success=True,
        data={
            "batch_id": batch_id,
            "session_ids": [session_id for session_id, _ in plans],
            "status": "running",
            "max_concurrency": cap,
        },
        message=f"Batch started: {len(plans)} training sessions",
    )


@router.get("/train/batch/{batch_id}/status")
async def get_batch_status(batch_id: str) -> APIResponse:
    """Aggregate per-session progress for a batch."""
    with _sessions_lock:
        batch = _batches.get(batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"Unknown batch_id: {batch_id}")
        sessions: list[dict[str, Any]] = []
        for session_id in batch["session_ids"]:
            session = _sessions.get(session_id)
            if session is None:
                sessions.append({"session_id": session_id, "status": "missing"})
                continue
            config = session.get("config") or {}
            entry: dict[str, Any] = {
                "session_id": session_id,
                "status": session.get("status"),
                "step": session.get("step"),
                "train_steps": session.get("train_steps"),
                "best_score": _safe_score(session.get("best_score")),
                "symbol": config.get("symbol"),
                "interval": config.get("interval"),
            }
            best_formula = session.get("best_formula")
            if best_formula:
                from selene_space_alpha.vocab import FORMULA_VOCAB

                try:
                    entry["formula_readable"] = FORMULA_VOCAB.decode(best_formula)
                except Exception:
                    pass
            if session.get("error"):
                entry["error"] = session["error"]
            sessions.append(entry)
        data = {
            "batch_id": batch_id,
            "status": _batch_status_from_sessions(
                [str(entry["status"]) for entry in sessions]
            ),
            "session_count": len(sessions),
            "max_concurrency": batch.get("max_concurrency"),
            "started_at": batch.get("started_at"),
            "sessions": sessions,
        }

    return APIResponse(success=True, data=data, message="Batch status")


def _safe_score(value: float | None) -> float | None:
    """Normalize non-finite scores (-inf/inf/NaN) to None so the JSON
    response stays parseable — json.dumps serializes -inf as ``-Infinity``,
    which the frontend JSON.parse cannot decode (500 on the client)."""
    if value is None:
        return None
    try:
        if not math.isfinite(value):
            return None
    except (TypeError, ValueError):
        return None
    return value


@router.get("/train/{session_id}/status")
async def get_training_status(session_id: str) -> APIResponse:
    """Get current training progress."""
    with _sessions_lock:
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        session = dict(_sessions[session_id])
        # Defensive copies so the event-loop caller never sees mid-append lists.
        history = {k: list(v) for k, v in (session.get("history") or {}).items()}

    formula_readable = None
    best_formula = session.get("best_formula")
    if best_formula:
        from selene_space_alpha.vocab import FORMULA_VOCAB

        try:
            formula_readable = FORMULA_VOCAB.decode(best_formula)
        except Exception:
            formula_readable = None

    return APIResponse(
        success=True,
        data={
            "session_id": session_id,
            "status": session["status"],
            "step": session.get("step", 0),
            "best_score": _safe_score(session.get("best_score")),
            "train_steps": session.get("train_steps"),
            "data_source": session.get("data_source"),
            "fallback_reason": session.get("fallback_reason"),
            "elapsed_seconds": time.time() - session.get("started_at", time.time()),
            "history": history,
            "formula_readable": formula_readable,
        },
    )


@router.get("/train/{session_id}/logs")
async def get_training_logs(session_id: str) -> APIResponse:
    """Get training log lines (ring-buffered, last 200)."""
    with _sessions_lock:
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        # Defensive read-side cap: the engine thread trims on write, but any
        # pre-existing/large buffer must still be bounded to the ring size.
        logs = list(_sessions[session_id].get("logs", [])[-_LOG_RING_SIZE:])

    return APIResponse(
        success=True,
        data={"session_id": session_id, "logs": logs},
    )


@router.post("/train/{session_id}/stop")
async def stop_training(session_id: str) -> APIResponse:
    """Stop a running training session."""
    with _sessions_lock:
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        _sessions[session_id]["status"] = "stopping"
    return APIResponse(
        success=True, data={"session_id": session_id, "status": "stopping"}
    )


@router.post("/train/{session_id}/resume")
async def resume_training(session_id: str) -> APIResponse:
    """Resume a stopped/failed training session from its checkpoint (断点续训).

    Loads the persisted ``checkpoint.pt`` (model/opt/best/history/config) and
    restarts the background training at the saved step. A live session
    (starting/running/stopping) or an already-completed one is rejected.
    If the session is gone from memory but its checkpoint exists on disk
    (crash/restart), it is rebuilt from the checkpoint.
    """
    import torch

    ckpt_path = _checkpoint_path(session_id)
    if not ckpt_path.exists():
        raise HTTPException(status_code=404, detail="No checkpoint to resume")

    try:
        ckpt = torch.load(ckpt_path, map_location="cpu")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Corrupt checkpoint: {exc}")

    with _sessions_lock:
        if session_id in _sessions:
            status = _sessions[session_id].get("status")
            if status in {"starting", "running", "stopping"}:
                raise HTTPException(
                    status_code=400, detail="Session is already running"
                )
            if status == "completed":
                raise HTTPException(status_code=400, detail="Session already completed")

    saved_step = int(ckpt.get("step", 0))
    extra = ckpt.get("extra") or {}
    config = ckpt.get("config") or {}
    saved_history = ckpt.get("training_history") or {
        "step": [],
        "avg_reward": [],
        "best_score": [],
        "stable_rank": [],
        "rank_step": [],
        "reward_std": [],
    }

    # Rebuild (or overwrite) the in-memory session from checkpoint state.
    req_fields = {
        "train_steps": extra.get("train_steps", config.get("TRAIN_STEPS", 100)),
        "batch_size": extra.get("batch_size", config.get("BATCH_SIZE", 256)),
        "max_formula_len": extra.get(
            "max_formula_len", config.get("MAX_FORMULA_LEN", 12)
        ),
        "use_lord": extra.get("use_lord", True),
        "symbol": extra.get("symbol", "BTC/USDT"),
        "exchange": extra.get("exchange", "binance"),
        "market_type": extra.get("market_type", "futures"),
        "interval": extra.get("interval", "1h"),
        "use_synthetic_data": extra.get("use_synthetic_data", False),
    }
    request = TrainRequest(**req_fields)
    request.start_step = saved_step

    with _sessions_lock:
        _sessions[session_id] = {
            "status": "starting",
            "step": saved_step,
            "best_score": ckpt.get("best_score", -float("inf")),
            "best_formula": ckpt.get("best_formula"),
            "train_steps": req_fields["train_steps"],
            "config": extra or req_fields,
            "started_at": time.time(),
            "history": saved_history,
            "logs": [],
        }

    asyncio.create_task(_run_training(session_id, request, saved_step))

    return APIResponse(
        success=True,
        data={
            "session_id": session_id,
            "status": "starting",
            "resumed_from_step": saved_step,
        },
        message="Training resumed",
    )


@router.post("/train/{session_id}/explain")
async def explain_training(session_id: str) -> APIResponse:
    """Generate an AI interpretation of a training session.

    Reuses the shared LLM helper (``call_llm``) with the configured provider.
    When the LLM is not configured or the call fails/times out, falls back to a
    deterministic rule-based interpretation built from the session's live
    history — the endpoint never errors on LLM unavailability.
    """
    with _sessions_lock:
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail="Session not found")
        session = dict(_sessions[session_id])
        history = {k: list(v) for k, v in (session.get("history") or {}).items()}

    best_score = _safe_score(session.get("best_score"))
    steps = history.get("step", [])
    avg_rewards = history.get("avg_reward", [])
    best_scores = history.get("best_score", [])
    config = session.get("config") or {}

    # Build a compact context for the LLM prompt.
    trend_desc = _training_trend_description(steps, avg_rewards, best_scores)
    config_desc = (
        f"symbol={config.get('symbol', 'N/A')} interval={config.get('interval', 'N/A')} "
        f"steps={config.get('train_steps', 'N/A')} batch={config.get('batch_size', 'N/A')} "
        f"lord={'on' if config.get('use_lord') else 'off'} "
        f"data_source={session.get('data_source', 'N/A')}"
    )

    explain_prompt = (
        "你是 SeleneQuant 的因子挖掘助手。以下是 Alpha 因子训练会话的状态摘要:\n"
        f"- 会话: {session_id}\n"
        f"- 配置: {config_desc}\n"
        f"- 当前最佳分数: {best_score if best_score is not None else 'N/A'}\n"
        f"- 趋势: {trend_desc}\n\n"
        "请用 2-4 句话解读:1) 训练进行得如何 2) 是否值得继续挖/继续训练 "
        "3) 一个可操作的下一步建议。语言:简体中文,不要使用 markdown 列表。"
    )

    from app.core.dependencies import get_settings_service
    from app.core.llm_helper import call_llm

    llm_text: str | None = None
    try:
        settings_service = get_settings_service()
        llm_text = await call_llm(
            settings_service,
            prompt=explain_prompt,
            system=(
                "You are SeleneQuant's alpha-factor mining analyst. Be concise, "
                "factual, and give actionable advice in Simplified Chinese."
            ),
            max_tokens=300,
            timeout=30.0,
        )
    except Exception:
        logger.warning(
            "Explain LLM call failed, falling back to rule-based", exc_info=True
        )

    if llm_text:
        return APIResponse(
            success=True,
            data={
                "session_id": session_id,
                "source": "llm",
                "explanation": llm_text,
                "summary": _rule_summary(best_score, steps, avg_rewards),
            },
        )

    return APIResponse(
        success=True,
        data={
            "session_id": session_id,
            "source": "rule",
            "explanation": _rule_explanation(best_score, steps, avg_rewards, config),
            "summary": _rule_summary(best_score, steps, avg_rewards),
        },
    )


def _training_trend_description(
    steps: list[int],
    avg_rewards: list[float],
    best_scores: list[float],
) -> str:
    """Describe the training trend in one sentence for the LLM prompt."""
    if not steps:
        return "训练尚未产生有效数据"
    if len(steps) < 2:
        return (
            f"仅 1 步:最佳分数 {best_scores[-1]:.4f},avg_reward {avg_rewards[-1]:.4f}"
        )
    # Compare first-half vs second-half average best_score to describe trajectory.
    half = len(best_scores) // 2
    first = sum(best_scores[:half]) / max(len(best_scores[:half]), 1)
    last = sum(best_scores[half:]) / max(len(best_scores[half:]), 1)
    direction = "上升" if last > first else "持平/下降"
    recent_avg = sum(avg_rewards[-min(20, len(avg_rewards)) :]) / max(
        min(20, len(avg_rewards)), 1
    )
    return (
        f"共 {len(steps)} 步,最佳分数 {best_scores[-1]:.4f}(前半段均值 {first:.4f} → "
        f"后半段均值 {last:.4f},{direction});最近 avg_reward 均值 {recent_avg:.4f}"
    )


def _rule_summary(
    best_score: float | None,
    steps: list[int],
    avg_rewards: list[float],
) -> dict[str, Any]:
    """Small structured summary surfaced alongside any explanation."""
    if not steps:
        return {"step_count": 0, "best_score": best_score}
    return {
        "step_count": len(steps),
        "best_score": best_score,
        "recent_avg_reward": round(
            sum(avg_rewards[-min(20, len(avg_rewards)) :])
            / max(min(20, len(avg_rewards)), 1),
            4,
        ),
    }


def _rule_explanation(
    best_score: float | None,
    steps: list[int],
    avg_rewards: list[float],
    config: dict[str, Any],
) -> str:
    """Deterministic fallback explanation when no LLM is configured."""
    if not steps:
        return (
            "训练刚启动,还没有产生有效数据。等待前几步完成后再查看解读,"
            "或检查后端日志确认数据加载是否成功。"
        )

    # Compute recent improvement to decide whether to continue.
    recent_avg = sum(avg_rewards[-min(20, len(avg_rewards)) :]) / max(
        min(20, len(avg_rewards)), 1
    )
    if best_score is not None and best_score > 0:
        verdict = "分数为正,因子有潜力,值得继续挖以寻找更优组合"
    elif recent_avg > 0:
        verdict = "avg_reward 为正,训练在向有利方向推进,可以再观察若干步"
    else:
        verdict = "当前信号偏弱,建议先调整数据源/周期,或增大训练步数再试"
    return (
        f"已训练 {len(steps)} 步,当前最佳分数 {best_score if best_score is not None else 'N/A'},"
        f"最近 avg_reward 均值 {recent_avg:.4f}。{verdict}。"
        "(未配置 LLM,此解读由规则生成)"
    )


@router.get("/formulas")
async def list_formulas() -> APIResponse:
    """List all discovered alpha formulas."""
    formulas: list[FormulaInfo] = []

    if _results_dir.exists():
        for formula_id, f in _iter_formula_files():
            try:
                with open(f, encoding="utf-8") as fh:
                    tokens = json.load(fh)
                from selene_space_alpha.vocab import FORMULA_VOCAB

                readable = FORMULA_VOCAB.decode(tokens)
                formulas.append(
                    FormulaInfo(
                        formula_id=formula_id,
                        formula_tokens=tokens,
                        formula_readable=readable,
                        best_score=0.0,
                        created_at=f.stat().st_mtime,
                    )
                )
            except Exception:
                continue

    # Also check in-memory sessions
    with _sessions_lock:
        items = list(_sessions.items())
    for sid, session in items:
        if session.get("best_formula"):
            from selene_space_alpha.vocab import FORMULA_VOCAB

            formulas.append(
                FormulaInfo(
                    formula_id=sid,
                    formula_tokens=session["best_formula"],
                    formula_readable=FORMULA_VOCAB.decode(session["best_formula"]),
                    best_score=session.get("best_score", 0.0),
                    created_at=session.get("started_at", 0.0),
                )
            )

    return APIResponse(
        success=True, data={"formulas": [f.model_dump() for f in formulas]}
    )


@router.get("/formulas/{formula_id}")
async def get_formula_detail(formula_id: str) -> APIResponse:
    """Get details of a specific formula."""
    # Check in-memory (locked read — the engine thread writes _sessions).
    with _sessions_lock:
        if formula_id in _sessions:
            session = dict(_sessions[formula_id])
            tokens = list(session.get("best_formula", []))
            history = {k: list(v) for k, v in (session.get("history") or {}).items()}
            from selene_space_alpha.vocab import FORMULA_VOCAB

            return APIResponse(
                success=True,
                data={
                    "formula_id": formula_id,
                    "formula_tokens": tokens,
                    "formula_readable": FORMULA_VOCAB.decode(tokens),
                    "best_score": _safe_score(session.get("best_score")),
                    "training_history": history,
                },
            )

    # Check file system
    filepath = _results_dir / f"best_meme_strategy_{formula_id}.json"
    if not filepath.exists():
        filepath = _results_dir / "best_meme_strategy.json"

    if filepath.exists():
        with open(filepath, encoding="utf-8") as f:
            tokens = json.load(f)
        from selene_space_alpha.vocab import FORMULA_VOCAB

        return APIResponse(
            success=True,
            data={
                "formula_id": formula_id,
                "formula_tokens": tokens,
                "formula_readable": FORMULA_VOCAB.decode(tokens),
            },
        )

    raise HTTPException(status_code=404, detail="Formula not found")


@router.post("/formulas/{formula_id}/backtest")
async def backtest_formula(
    formula_id: str,
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    exchange: str = Query("binance", description="Exchange provider"),
    market_type: str = Query("futures", description="Market type: spot or futures"),
    interval: str = Query("1h", description="Kline interval"),
) -> APIResponse:
    """Run a backtest for a specific formula.

    Uses real klines from the local SQLite cache when available for the
    requested partition; falls back to synthetic random-walk data only
    when no local data exists. Response includes ``source`` to distinguish.
    """
    tokens = _get_formula_tokens(formula_id)
    if tokens is None:
        raise HTTPException(status_code=404, detail="Formula not found")

    import pandas as pd
    import numpy as np
    from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy

    strategy = AlphaFormulaStrategy(formula_tokens=tokens)
    data: pd.DataFrame | None = None
    source = "synthetic"

    # Prefer real klines from local SQLite (same resolution as saved-strategy backtest)
    try:
        from selene_space_market.kline_db import KlineDB

        db = KlineDB(db_path=str(_default_klines_db_path()))
        df = db.get_klines(exchange, market_type, symbol, interval)
        if not df.empty and len(df) >= 50:
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            # CEX klines lack liquidity/fdv; factor functions may read them (ser_* live-only).
            # Backfill with benign constants so VM factors resolve, matching the strategy bridge.
            if "liquidity" not in df.columns:
                df["liquidity"] = 0.0
            if "fdv" not in df.columns:
                df["fdv"] = 0.0
            # Cap to the most recent bars — full partitions (up to 36k) make per-feature
            # construction over the whole history slow for an interactive backtest view.
            data = df.tail(1500).reset_index(drop=True)
            source = "real"
    except Exception:
        logger.warning(
            "Real kline backtest unavailable for %s %s/%s %s, falling back to synthetic",
            exchange,
            market_type,
            symbol,
            interval,
            exc_info=True,
        )

    if data is None:
        # Synthetic fallback (only when no local data for this partition)
        np.random.seed(42)
        n = 500
        data = pd.DataFrame(
            {
                "open": np.random.randn(n).cumsum() + 100,
                "high": np.random.randn(n).cumsum() + 101,
                "low": np.random.randn(n).cumsum() + 99,
                "close": np.random.randn(n).cumsum() + 100,
                "volume": np.abs(np.random.randn(n)) * 1e6,
                "liquidity": np.abs(np.random.randn(n)) * 1e6,
                "fdv": np.abs(np.random.randn(n)) * 1e8,
            }
        )

    score = strategy.compute_score(data)
    signals = strategy.get_signal_series(data)
    counts = signals.value_counts().to_dict()

    return APIResponse(
        success=True,
        data={
            "formula_id": formula_id,
            "formula_readable": strategy.formula_readable,
            "latest_score": score,
            "signal_counts": counts,
            "total_bars": len(data),
            "source": source,
            "symbol": symbol,
            "interval": interval,
            "exchange": exchange,
            "market_type": market_type,
        },
    )


@router.get("/validation-report")
async def get_validation_report() -> APIResponse:
    """Latest T4 validation report (验证 tab 数据通路).

    Serves ``internal-archive/validation_report.json`` — the same file the
    deploy gate (``_lookup_t4_decision``) judges on — verbatim: no shape
    trimming, the frontend projects. Missing file is a 404 with guidance
    (语义显形), never an empty-data 200.
    """
    report_path = _repo_root() / "实验" / "alpha-mining" / "validation_report.json"
    if not report_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "No validation report at internal-archive/validation_report.json. "
                "Run the T4 validation pipeline first: "
                "python -m selene_space_factor_research.mining.report"
            ),
        )
    try:
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Validation report unreadable: {exc}"
        )

    return APIResponse(
        success=True,
        data={
            "report": report,
            "generated_at": report.get("generated_at"),
        },
    )


@router.post("/formulas/{formula_id}/deploy")
async def deploy_formula(formula_id: str) -> APIResponse:
    """Deploy a formula as a strategy.

    Gate (decision item B, 2026-09-10): the formula's most recent T4
    validation verdict must be ``accept`` (``decision=="accept"`` in
    ``internal-archive/validation_report.json``). ``hold``/``reject``/``defer``
    are refused with 403 unless ``SELENE_ALPHA_DEPLOY_ALLOW_HOLD=1``. Formulas
    never validated (no report entry) deploy unhindered — the gate judges on
    evidence, not absence of it.
    """
    tokens = _get_formula_tokens(formula_id)
    if tokens is None:
        raise HTTPException(status_code=404, detail="Formula not found")

    # Per-formula verdict (batch×governance L2) takes precedence; the global
    # CLI report is the fallback. Neither existing = deploy unhindered.
    decision, report_reason = _lookup_formula_verdict(tokens)
    if decision is None:
        decision, report_reason = _lookup_t4_decision(tokens)
    if decision is not None and decision != "accept":
        allow_hold = os.environ.get("SELENE_ALPHA_DEPLOY_ALLOW_HOLD") == "1"
        if not allow_hold:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Deploy refused: T4 validation decision is '{decision}' "
                    f"({report_reason}). Set SELENE_ALPHA_DEPLOY_ALLOW_HOLD=1 "
                    "to override."
                ),
            )

    from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy

    strategy = AlphaFormulaStrategy(formula_tokens=tokens)
    config = strategy.to_dict()
    config["deployed_at"] = time.time()

    # Save deployment config
    deploy_dir = _results_dir / "deployed"
    deploy_dir.mkdir(parents=True, exist_ok=True)
    with open(deploy_dir / f"{formula_id}.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    # Mirror into the alpha_v* archive that `alpha:{id}` backtests resolve. The
    # vocab block is the strategy's own: mining searches in FORMULA_VOCAB, and
    # the reader's CEX_V4 fallback would silently reindex these tokens.
    atomic_write(
        _deploy_archive_dir / f"{formula_id}.json",
        json.dumps(
            {
                "id": f"alpha:{formula_id}",
                "source": "alpha:mined",
                "formula": {
                    "tokens": tokens,
                    "buy_th": config.get("buy_threshold", 0.85),
                    "sell_th": config.get("sell_threshold", 0.45),
                    "readable": strategy.formula_readable,
                },
                "vocab": {
                    "feature_names": config.get("vocab_feature_names", []),
                    "operator_names": config.get("vocab_operator_names", []),
                },
                "deployed_at": config["deployed_at"],
            },
            indent=2,
        ),
    )

    return APIResponse(
        success=True,
        data={
            "formula_id": formula_id,
            "formula_readable": strategy.formula_readable,
            "status": "deployed",
        },
    )


@router.get("/formulas/{formula_id}/ic-decay")
async def get_ic_decay(
    formula_id: str,
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    exchange: str = Query("binance", description="Exchange provider"),
    market_type: str = Query("futures", description="Market type: spot or futures"),
    interval: str = Query("1h", description="Kline interval"),
    max_lag: int = Query(20, ge=1, le=100, description="Max forward lag (bars)"),
) -> APIResponse:
    """IC decay analysis for a mined formula (验证 tab 图表通路).

    Loads the same partition as ``/formulas/{id}/backtest`` (real klines,
    synthetic only as fallback), runs the formula over it for the raw score
    series, and feeds ``compute_ic_decay`` against forward returns. No
    formula → 404; no usable data → 422 (语义显形, never fake zeros).
    """
    tokens = _get_formula_tokens(formula_id)
    if tokens is None:
        raise HTTPException(status_code=404, detail="Formula not found")

    import pandas as pd

    from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy
    from selene_space_alpha.mining.ic_decay import compute_ic_decay

    strategy = AlphaFormulaStrategy(formula_tokens=tokens)
    data: pd.DataFrame | None = None
    source = "synthetic"

    try:
        from selene_space_market.kline_db import KlineDB

        db = KlineDB(db_path=str(_default_klines_db_path()))
        df = db.get_klines(exchange, market_type, symbol, interval)
        if not df.empty and len(df) >= 50:
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            if "liquidity" not in df.columns:
                df["liquidity"] = 0.0
            if "fdv" not in df.columns:
                df["fdv"] = 0.0
            data = df.tail(1500).reset_index(drop=True)
            source = "real"
    except Exception:
        logger.warning(
            "Real kline load unavailable for IC decay %s %s/%s %s",
            exchange,
            market_type,
            symbol,
            interval,
            exc_info=True,
        )

    if data is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No usable kline data for {exchange} {market_type} {symbol} "
                f"{interval}: IC decay needs a real partition (>=50 bars), "
                "and synthetic fallback would fake the decay shape."
            ),
        )

    signal = strategy.get_score_series(data)
    returns = pd.to_numeric(data["close"], errors="coerce").pct_change().fillna(0.0)

    if int(signal.notna().sum()) < 10:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Formula {formula_id} produced fewer than 10 finite score "
                "values on this partition; IC decay is not computable."
            ),
        )

    result = compute_ic_decay(signal, returns, max_lag=max_lag)

    return APIResponse(
        success=True,
        data={
            "formula_id": formula_id,
            "formula_readable": strategy.formula_readable,
            "symbol": symbol,
            "interval": interval,
            "source": source,
            "max_lag": max_lag,
            "ic_by_lag": {int(lag): float(v) for lag, v in result.ic_by_lag.items()},
            "half_life": result.half_life,
            "peak_ic": result.peak_ic,
            "peak_lag": result.peak_lag,
            "turnover": result.turnover,
            "turnover_adjusted_ic": result.turnover_adjusted_ic,
            "is_decayed": result.is_decayed,
        },
    )


def _lookup_t4_decision(tokens: list[int]) -> tuple[str | None, str]:
    """Match deployed tokens against the latest T4 validation report.

    Returns ``(decision, reason)``; ``(None, "")`` when no report exists or no
    entry matches the token list — absence of evidence is not a refusal.
    """
    report_path = _repo_root() / "实验" / "alpha-mining" / "validation_report.json"
    if not report_path.exists():
        return None, ""
    try:
        with open(report_path, encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.warning(
            "T4 deploy gate: unreadable report at %s", report_path, exc_info=True
        )
        return None, ""
    tokens = [int(t) for t in tokens]
    for entry in reversed(report.get("formulas", [])):
        if [int(t) for t in entry.get("tokens", [])] == tokens:
            return entry.get("decision"), entry.get("reason", "")
    return None, ""


def _formula_verdict_path(formula_id: str) -> Path:
    """Per-formula T4 verdict file (batch×governance L2: 批量产出逐条独立裁决)."""
    return _repo_root() / "data" / "alpha_validations" / f"{formula_id}.json"


def _lookup_formula_verdict(tokens: list[int]) -> tuple[str | None, str]:
    """Match tokens against stored per-formula verdicts (newest file first).

    Same contract as :func:`_lookup_t4_decision`: ``(None, "")`` when nothing
    matches — absence of evidence is not a refusal.
    """
    vdir = _formula_verdict_path("").parent
    if not vdir.exists():
        return None, ""
    target = [int(t) for t in tokens]
    for path in sorted(
        vdir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        try:
            with open(path, encoding="utf-8") as f:
                entry = json.load(f)
            if [int(t) for t in entry.get("tokens", [])] == target:
                return entry.get("decision"), entry.get("reason", "")
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return None, ""


class ValidateFormulaRequest(BaseModel):
    """Optional dataset scope for a per-formula T4 run."""

    symbols: list[str] | None = Field(
        None,
        description="Dataset symbols (default: the formula's session symbol + peers)",
    )
    interval: str | None = Field(
        None, description="Kline interval (default: session's)"
    )
    bars: int = Field(200, ge=50, le=2000, description="Bars per symbol from klines.db")


@router.post("/formulas/{formula_id}/validate")
async def validate_formula_endpoint(
    formula_id: str, body: ValidateFormulaRequest | None = None
) -> APIResponse:
    """Run T4 walk-forward validation on ONE formula and store its verdict.

    The global report (``internal-archive/validation_report.json``) is a
    batch-level artifact from the CLI pipeline; this endpoint gives each
    mined formula its own addressed verdict so the deploy gate can judge
    batch outputs individually instead of only via the global report.
    Falls back to synthetic panels when klines.db lacks the data (recorded
    in the verdict's datasets, never silently).
    """
    tokens = _get_formula_tokens(formula_id)
    if tokens is None:
        raise HTTPException(status_code=404, detail="Formula not found")
    if not tokens:
        raise HTTPException(
            status_code=409, detail=f"Formula {formula_id} has no best_formula yet"
        )

    session = _sessions.get(formula_id) or {}
    cfg = session.get("config") or {}
    interval = (body.interval if body else None) or cfg.get("interval") or "1h"
    symbols = (body.symbols if body else None) or [cfg.get("symbol") or "BTC/USDT"]
    bars = body.bars if body else 200

    from selene_space_factor_research.mining.validation_protocol import (
        FoldConfig,
        validate_formula,
    )
    from selene_space_factor_research.mining.report import (
        build_datasets,
        fallback_synthetic_datasets,
    )

    try:
        datasets = build_datasets(_default_klines_db_path(), symbols, interval, bars)
        data_source = "klines_db"
    except Exception as exc:
        logger.warning(
            "T4 per-formula: klines.db datasets unavailable (%s); synthetic fallback",
            exc,
        )
        datasets = fallback_synthetic_datasets()
        data_source = "synthetic_fallback"

    # Fresh treads run in a worker thread via run_in_executor is overkill for a
    # single formula (T4 on 200 bars is seconds); run inline like /explain does.
    fv = validate_formula(
        tokens,
        datasets,
        n_trials=1,
        fold_config=FoldConfig(min_train=70, fold_size=20, n_folds=5),
    )
    record = fv.to_dict()
    record["formula_id"] = formula_id
    record["data_source"] = data_source
    record["validated_at"] = time.time()

    out = _formula_verdict_path(formula_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(out, json.dumps(record, ensure_ascii=False))

    return APIResponse(
        success=True,
        data={
            "formula_id": formula_id,
            "verdict": record["verdict"],
            "decision": record["decision"],
            "reason": record["reason"],
            "mean_ic": record["mean_ic"],
            "data_source": data_source,
            "stored_at": str(out),
        },
        message=f"T4 validated {formula_id}: {record['decision']}",
    )


@router.get("/formulas/{formula_id}/validation")
async def get_formula_validation(formula_id: str) -> APIResponse:
    """Read the stored per-formula verdict (404 = never validated here)."""
    path = _formula_verdict_path(formula_id)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No per-formula validation for {formula_id}. POST /formulas/{formula_id}/validate first.",
        )
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Validation record unreadable: {exc}"
        )
    return APIResponse(success=True, data=record)


@router.get("/vocab")
async def get_vocab() -> APIResponse:
    """Get the current formula vocabulary (features + operators)."""
    from selene_space_alpha.vocab import FORMULA_VOCAB
    from selene_space_alpha.ops import OPS_CONFIG  # type: ignore[attr-defined]

    return APIResponse(
        success=True,
        data={
            "feature_names": list(FORMULA_VOCAB.feature_names),
            "operator_names": list(FORMULA_VOCAB.operator_names),
            "total_tokens": FORMULA_VOCAB.size,
            "operators": [
                {"name": name, "arity": arity} for name, _, arity in OPS_CONFIG
            ],
        },
    )


# ---------------------------------------------------------------------------
# Saved Alpha Strategies (V5/V6 Optuna-discovered)
# ---------------------------------------------------------------------------


class SavedStrategyInfo(BaseModel):
    name: str
    version: str
    formula_readable: str
    formula_tokens: list[int]
    profiles: list[dict[str, Any]]
    file_path: str
    review_status: str = "discovered"


@router.get("/saved-strategies")
async def list_saved_strategies() -> APIResponse:
    """List all saved alpha strategies from data/strategies/alpha_v*/."""
    strategies: list[SavedStrategyInfo] = []

    if not _strategies_dir.exists():
        return APIResponse(success=True, data={"strategies": []})

    for version_dir in sorted(_strategies_dir.glob("alpha_v*")):
        if not version_dir.is_dir():
            continue
        version = version_dir.name  # e.g., "alpha_v5"
        for json_file in sorted(version_dir.glob("*.json")):
            try:
                with open(json_file, encoding="utf-8") as f:
                    data = json.load(f)

                formula = data.get("formula", {})
                tokens = formula.get("tokens", [])
                readable = formula.get("readable", "")

                # Decode tokens if readable not in file
                if not readable and tokens:
                    try:
                        from selene_space_alpha.vocab import FORMULA_VOCAB

                        readable = FORMULA_VOCAB.decode(tokens)
                    except Exception:
                        readable = f"formula({len(tokens)} tokens)"

                # Extract strategy profiles (e.g., 15m_short_term, 1h_mid_term)
                profiles = []
                strat_configs = data.get("strategies", {})
                for profile_name, profile_data in strat_configs.items():
                    profiles.append(
                        {
                            "name": profile_name,
                            **profile_data,
                        }
                    )
                if not profiles and "best_formula" in data:
                    # V4/V6 format with flat structure
                    bf = data["best_formula"]
                    profiles.append(
                        {
                            "name": "default",
                            "base_interval": data.get("base_interval", "15m"),
                            "aux_interval": data.get("aux_interval", "1h"),
                            "buy_th": bf.get("buy_th", 0.57),
                            "sell_th": bf.get("sell_th", 0.41),
                            "stop_loss": bf.get("stop_loss", 0.04),
                            "position_size": bf.get("position_size", 0.40),
                        }
                    )

                strategies.append(
                    SavedStrategyInfo(
                        name=json_file.stem,
                        version=version,
                        formula_readable=readable,
                        formula_tokens=tokens,
                        profiles=profiles,
                        file_path=str(json_file),
                        review_status=data.get("review_status", "discovered"),
                    )
                )
            except Exception:
                logger.warning("Failed to parse strategy file: %s", json_file)
                continue

    return APIResponse(
        success=True,
        data={"strategies": [s.model_dump() for s in strategies]},
    )


class BacktestSavedStrategyRequest(BaseModel):
    symbol: str = Field("BTC/USDT", description="Trading pair")
    interval: str = Field("15m", description="Kline interval")
    exchange: str = Field("binance", description="Exchange")
    market_type: str = Field("futures", description="Market type")
    profile: str = Field("default", description="Strategy profile name")
    initial_capital: float = Field(10000.0, gt=0, description="Starting capital")


class DeploySavedStrategyRequest(BaseModel):
    symbol: str = Field("BTC/USDT", description="Trading pair")
    interval: str = Field("15m", description="Kline interval")
    profile: str = Field("default", description="Strategy profile name")
    exchange: str = Field(
        "binance", description="Data source exchange (astock for A-share)"
    )
    market_type: str = Field(
        "spot", description="Market type (spot/futures; astock uses spot)"
    )


def _pandas_vocab_for_version(version: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return (feature_names, operator_names) for a saved strategy's version tag.

    v7_mining strategies carry the mining vocab: CEX_FEATURE_NAMES (11 features)
    with the full torch op order INCLUDING GATE — their tokens don't fit V4/V6.
    v6 → CEX_FEATURE_NAMES_V6; everything else → V4. Both drop GATE.
    """
    from selene_space_alpha.real_data_loader import (
        CEX_FEATURE_NAMES,
        CEX_FEATURE_NAMES_V4,
        CEX_FEATURE_NAMES_V6,
    )
    from selene_space_alpha.ops.ops import OPS_CONFIG
    from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS

    version = str(version or "")
    if version.startswith("v7_mining"):
        feature_names = tuple(CEX_FEATURE_NAMES)
        op_names = tuple(cfg[0] for cfg in OPS_CONFIG)
    else:
        feature_names = (
            CEX_FEATURE_NAMES_V6 if version.startswith("v6") else CEX_FEATURE_NAMES_V4
        )
        op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    return feature_names, op_names


@router.post("/saved-strategies/{strategy_name}/backtest")
async def backtest_saved_strategy(
    strategy_name: str,
    body: BacktestSavedStrategyRequest,
) -> APIResponse:
    """Backtest a saved alpha strategy with real kline data."""
    # Find the strategy file
    strategy_data = _find_saved_strategy(strategy_name)
    if strategy_data is None:
        raise HTTPException(
            status_code=404, detail=f"Strategy '{strategy_name}' not found"
        )

    formula = strategy_data.get("formula") or strategy_data.get("best_formula", {})
    tokens = formula.get("tokens", [])
    if not tokens:
        raise HTTPException(status_code=400, detail="Strategy has no formula tokens")

    # Resolve profile parameters
    buy_th = 0.57
    sell_th = 0.41
    aux_interval = None
    strat_configs = strategy_data.get("strategies", {})
    if body.profile != "default" and body.profile in strat_configs:
        p = strat_configs[body.profile]
        buy_th = p.get("buy_th", buy_th)
        sell_th = p.get("sell_th", sell_th)
        aux_interval = p.get("aux_interval")
    elif formula.get("buy_th"):
        buy_th = formula["buy_th"]
        sell_th = formula.get("sell_th", sell_th)
    # Default aux interval mapping
    if not aux_interval:
        aux_interval_map = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "4h"}
        aux_interval = aux_interval_map.get(body.interval)

    # Load kline data from local SQLite
    import pandas as pd
    from selene_space_market.kline_db import KlineDB

    db_path = default_klines_db_path()

    try:
        db = KlineDB(db_path=str(db_path))
        # klines.db stores A-share bars under market_type="astock" (import script
        # convention; A-share has no futures partition) regardless of what the
        # caller passed — same split as the live path's astock→spot normalization.
        db_market_type = "astock" if body.exchange == "astock" else body.market_type
        df = db.get_klines(
            body.exchange,
            db_market_type,
            body.symbol,
            body.interval,
        )
        if df.empty or len(df) < 50:
            raise ValueError(
                f"Not enough kline data for {body.symbol} {body.interval} ({len(df)} bars)"
            )
    except ImportError:
        raise HTTPException(status_code=500, detail="KlineDB not available")
    except Exception as exc:
        logger.error("Failed to load kline data: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")

    # Merge auxiliary timeframe data (needed for TF_TREND, TF_PRESSURE etc.)
    aux_interval_map = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "4h"}
    aux_interval = aux_interval_map.get(body.interval, "1h")
    try:
        aux_df = db.get_klines(body.exchange, db_market_type, body.symbol, aux_interval)
        if not aux_df.empty:
            for col in ["open", "high", "low", "close", "volume"]:
                aux_df[col] = pd.to_numeric(aux_df[col], errors="coerce")
            prefix = f"TF{aux_interval.replace('m', 'M') if 'm' in aux_interval else aux_interval}_"
            aux_aligned = aux_df.set_index("timestamp").reindex(
                df["timestamp"], method="ffill"
            )
            aux_aligned.columns = [f"{prefix}{c}" for c in aux_aligned.columns]
            aux_aligned = aux_aligned.reset_index(drop=True)
            df = pd.concat([df, aux_aligned], axis=1)
    except Exception:
        logger.warning("Failed to merge aux timeframe data, proceeding with base only")

    # Run vectorized backtest using the same engine as weekly_pnl_report
    try:
        # Prefer the artifact's own vocab block (token indices are stable
        # only against the vocab it was mined with); fall back to the
        # legacy version-tag resolution for old archives without one.
        from selene_space_alpha.strategy_bridge import resolve_artifact_vocab
        from selene_space_alpha.vm import PandasStackVM

        if (strategy_data.get("vocab") or {}).get("feature_names"):
            vocab = resolve_artifact_vocab(strategy_data)
        else:
            from selene_space_alpha.vocab import FormulaVocab

            feature_names, op_names = _pandas_vocab_for_version(
                strategy_data.get("version")
            )
            vocab = FormulaVocab(
                feature_names=feature_names,
                operator_names=op_names,
            )

        # Compute signals vectorized
        vm = PandasStackVM(vocab)

        # Pre-compute features
        features: dict[int, pd.Series] = {}
        for idx, (feat_name, fn) in enumerate(vm._factor_fns):
            if fn is not None:
                try:
                    series = fn(df).fillna(0.0)  # type: ignore[operator]
                    series = (
                        series.replace([float("inf")], 1e6)
                        .replace([float("-inf")], -1e6)
                        .clip(-1e6, 1e6)
                    )
                    features[idx] = series
                except Exception:
                    features[idx] = pd.Series(0.0, index=df.index)
            else:
                col_name = feat_name.lower()
                if col_name in df.columns:
                    features[idx] = df[col_name].fillna(0.0)
                else:
                    features[idx] = pd.Series(0.0, index=df.index)

        score_series = vm.execute(tokens, df, precomputed_features=features)
        if score_series is None:
            raise ValueError("Formula execution returned None")

        score_pd = pd.Series(score_series.values.astype(float))
        rolling_mean = score_pd.rolling(60, min_periods=10).mean()
        rolling_std = score_pd.rolling(60, min_periods=10).std() + 1e-8
        score_norm = ((score_pd - rolling_mean) / rolling_std).fillna(0.0).clip(-10, 10)

        import numpy as np

        sigmoid = 1.0 / (1.0 + np.exp(-score_norm))
        signals = np.where(sigmoid > buy_th, 1, np.where(sigmoid < sell_th, -1, 0))

        # Vectorized backtest loop
        closes = df["close"].values.astype(float)
        lows = df["low"].values.astype(float) if "low" in df.columns else closes
        highs = df["high"].values.astype(float) if "high" in df.columns else closes
        n = len(closes)

        fee_rate = 0.0002  # maker
        slippage_frac = 0.0  # maker no slippage
        stop_loss_val = 0.04
        hold_bars = 2
        cooldown_bars = 4
        position_size = 0.40

        trade_notional = body.initial_capital * position_size
        capital = body.initial_capital
        equity_curve: list[float] = [body.initial_capital]
        trades_list: list[dict] = []
        position = 0
        entry_price = 0.0
        position_notional = 0.0
        bars_in_position = 0
        bars_since_exit = cooldown_bars + 1

        for i in range(1, n):
            sig = int(signals[i - 1])
            price = closes[i]
            low = lows[i]
            high = highs[i]

            if not np.isfinite(capital) or capital <= 0:
                equity_curve.append(equity_curve[-1])
                continue

            if position == 0:
                bars_since_exit += 1
                if sig == 1 and bars_since_exit >= cooldown_bars:
                    position = 1
                    entry_price = price * (1.0 + slippage_frac)
                    position_notional = trade_notional
                    capital -= position_notional * fee_rate
                    bars_in_position = 0
                    bars_since_exit = 0
                elif sig == -1 and bars_since_exit >= cooldown_bars:
                    position = -1
                    entry_price = price * (1.0 - slippage_frac)
                    position_notional = trade_notional
                    capital -= position_notional * fee_rate
                    bars_in_position = 0
                    bars_since_exit = 0

            elif position == 1:
                bars_in_position += 1
                stopped = False
                if stop_loss_val > 0:
                    stop_price = entry_price * (1.0 - stop_loss_val)
                    if low <= stop_price:
                        exit_price = stop_price * (1.0 - slippage_frac)
                        pnl_pct = (exit_price - entry_price) / entry_price
                        net_pnl = (
                            position_notional * pnl_pct
                            - position_notional * abs(1.0 + pnl_pct) * fee_rate
                        )
                        capital += net_pnl
                        trades_list.append(
                            {"pnl": net_pnl, "stop": True, "side": "long"}
                        )
                        position = 0
                        bars_since_exit = 0
                        stopped = True
                if not stopped and bars_in_position >= hold_bars and sig == -1:
                    exit_price = price * (1.0 - slippage_frac)
                    pnl_pct = (exit_price - entry_price) / entry_price
                    net_pnl = (
                        position_notional * pnl_pct
                        - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    )
                    capital += net_pnl
                    trades_list.append({"pnl": net_pnl, "stop": False, "side": "long"})
                    position = 0
                    bars_since_exit = 0

            elif position == -1:
                bars_in_position += 1
                stopped = False
                if stop_loss_val > 0:
                    stop_price = entry_price * (1.0 + stop_loss_val)
                    if high >= stop_price:
                        exit_price = stop_price * (1.0 + slippage_frac)
                        pnl_pct = (entry_price - exit_price) / entry_price
                        net_pnl = (
                            position_notional * pnl_pct
                            - position_notional * abs(1.0 + pnl_pct) * fee_rate
                        )
                        capital += net_pnl
                        trades_list.append(
                            {"pnl": net_pnl, "stop": True, "side": "short"}
                        )
                        position = 0
                        bars_since_exit = 0
                        stopped = True
                if not stopped and bars_in_position >= hold_bars and sig == 1:
                    exit_price = price * (1.0 + slippage_frac)
                    pnl_pct = (entry_price - exit_price) / entry_price
                    net_pnl = (
                        position_notional * pnl_pct
                        - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    )
                    capital += net_pnl
                    trades_list.append({"pnl": net_pnl, "stop": False, "side": "short"})
                    position = 0
                    bars_since_exit = 0

            # Equity tracking
            if position == 1:
                unrealized = position_notional * (price - entry_price) / entry_price
                equity = capital + unrealized
            elif position == -1:
                unrealized = position_notional * (entry_price - price) / entry_price
                equity = capital + unrealized
            else:
                equity = capital
            equity = max(equity, 0.0) if np.isfinite(equity) else 0.0
            equity_curve.append(equity)

        # Compute metrics
        total_return = (equity_curve[-1] - body.initial_capital) / body.initial_capital
        peak = body.initial_capital
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd

        wins = [t for t in trades_list if t["pnl"] > 0]
        win_rate = len(wins) / len(trades_list) if trades_list else 0.0

        # Sharpe (annualized)
        if len(equity_curve) > 1:
            returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
            sharpe = float(
                np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(35040)
            )  # 15m annual bars
        else:
            sharpe = 0.0

        gross_profit = sum(t["pnl"] for t in trades_list if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in trades_list if t["pnl"] <= 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0

        # Annualized return: the strategy backtest uses 15m bars by default;
        # scale by the number of bars actually traded vs. the interval's bars/year.
        bars_per_year = {"15m": 35040, "1h": 8760, "4h": 2190, "1d": 365}.get(
            body.interval, 35040
        )
        years = len(equity_curve) / bars_per_year if bars_per_year > 0 else 0.0
        annual_return = (
            ((equity_curve[-1] / body.initial_capital) ** (1.0 / years) - 1.0)
            if years > 0 and equity_curve[-1] > 0
            else 0.0
        )

        # Sortino: downside-only deviation.
        sortino = 0.0
        if len(equity_curve) > 1:
            returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
            downside = returns[returns < 0]
            downside_std = np.std(downside) if len(downside) > 0 else 0.0
            if downside_std > 0:
                sortino = float(
                    np.mean(returns) / downside_std * np.sqrt(bars_per_year)
                )

        # Rolling Sharpe (e.g., 96-bar window ≈ 1 day on 15m) — sequence for a sparkline.
        rolling_sharpe: list[float] = []
        window = 96
        if len(equity_curve) > 1:
            returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
            for i in range(0, len(returns), max(1, window // 2)):
                chunk = returns[i : i + window]
                if len(chunk) < 10:
                    continue
                std = float(np.std(chunk))
                if std > 1e-12:
                    rolling_sharpe.append(
                        float(np.mean(chunk) / std * np.sqrt(bars_per_year))
                    )

        readable = formula.get("readable", "")
        if not readable:
            try:
                readable = vocab.decode(tokens)
            except Exception:
                readable = f"formula({len(tokens)} tokens)"

        return APIResponse(
            success=True,
            data={
                "strategy_name": strategy_name,
                "formula_readable": readable,
                "symbol": body.symbol,
                "interval": body.interval,
                "total_return": total_return,
                "annual_return": annual_return,
                "sharpe_ratio": sharpe,
                "sortino_ratio": sortino,
                "max_drawdown": -max_dd,
                "win_rate": win_rate,
                "total_trades": len(trades_list),
                "profit_factor": profit_factor,
                "equity_curve": equity_curve[
                    -500:
                ],  # Last 500 points to keep response small
                "rolling_sharpe": rolling_sharpe,
                "trades": trades_list[-50:],  # Last 50 trades
            },
        )
    except Exception as exc:
        logger.error("Backtest failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/saved-strategies/{strategy_name}/deploy")
async def deploy_saved_strategy(
    strategy_name: str,
    body: DeploySavedStrategyRequest,
) -> APIResponse:
    """Deploy a saved alpha strategy to the paper trading StrategyRunner."""
    try:
        strategy_data = _find_saved_strategy(strategy_name)
        if strategy_data is None:
            raise HTTPException(
                status_code=404, detail=f"Strategy '{strategy_name}' not found"
            )

        formula = strategy_data.get("formula") or strategy_data.get("best_formula", {})
        tokens = formula.get("tokens", [])
        if not tokens:
            raise HTTPException(
                status_code=400, detail="Strategy has no formula tokens"
            )

        # Resolve profile parameters
        buy_th = formula.get("buy_th", 0.57)
        sell_th = formula.get("sell_th", 0.41)
        aux_interval = None
        strat_configs = strategy_data.get("strategies", {})
        if body.profile != "default" and body.profile in strat_configs:
            p = strat_configs[body.profile]
            buy_th = p.get("buy_th", buy_th)
            sell_th = p.get("sell_th", sell_th)
            aux_interval = p.get("aux_interval")
        elif formula.get("buy_th"):
            buy_th = formula["buy_th"]
            sell_th = formula.get("sell_th", sell_th)
        # Default aux interval mapping
        if not aux_interval:
            aux_interval_map = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "4h"}
            aux_interval = aux_interval_map.get(body.interval)

        # Build strategy_config for StrategyRunner.deploy()
        # Artifact vocab wins when present — a mined formula's tokens only mean
        # something in the vocab it was searched in (see resolve_artifact_vocab);
        # legacy archives without a vocab block fall back to version-tag resolution.
        from selene_space_alpha.strategy_bridge import resolve_artifact_vocab

        if (strategy_data.get("vocab") or {}).get("feature_names"):
            artifact_vocab = resolve_artifact_vocab(strategy_data)
        else:
            from selene_space_alpha.vocab import FormulaVocab

            feature_names, op_names = _pandas_vocab_for_version(
                strategy_data.get("version")
            )
            artifact_vocab = FormulaVocab(
                feature_names=feature_names, operator_names=op_names
            )

        strategy_config = {
            "id": f"alpha:{strategy_name}",
            "source": "alpha:mined",
            "formula_tokens": tokens,
            "buy_threshold": buy_th,
            "sell_threshold": sell_th,
            "vocab": {
                "feature_names": list(artifact_vocab.feature_names),
                "operator_names": list(artifact_vocab.operator_names),
            },
            "symbols": [body.symbol],
            "interval": body.interval,
            "aux_interval": aux_interval,
            "exchange": body.exchange,
            # market-data sources are keyed ("astock", "spot") — A-share has no
            # futures feed; the klines.db convention market_type="astock" does
            # not apply to the live data path.
            "market_type": "spot" if body.exchange == "astock" else body.market_type,
        }

        from app.core.dependencies import get_strategy_runner

        runner = get_strategy_runner()
        if runner is None:
            raise HTTPException(status_code=503, detail="Strategy runner not available")

        deployment_id = f"dep-alpha-{strategy_name[:16]}-{int(time.time())}"
        account_id = "auto"

        error = await runner.deploy(deployment_id, strategy_config, account_id)
        if error:
            raise HTTPException(status_code=400, detail=error)

        slot = runner.get_slot_status(deployment_id)

        market_regime = None
        regime_warning = None
        try:
            from app.core.regime_guard import get_market_state

            state = await get_market_state(
                body.symbol,
                body.interval,
                exchange=body.exchange,
                market_type=strategy_config["market_type"],
            )
            if state is not None:
                market_regime = state.regime.value
                if market_regime in ("ranging", "volatile"):
                    kind = "ranging" if market_regime == "ranging" else "volatile"
                    regime_warning = (
                        f"Market is {kind}; mined formula behavior may diverge "
                        "from the mining period."
                    )
        except Exception:
            logger.debug("Regime hint unavailable for %s", body.symbol, exc_info=True)

        return APIResponse(
            success=True,
            data={
                "strategy_name": strategy_name,
                "deployment_id": deployment_id,
                "account_id": slot.get("account_id") if slot else account_id,
                "symbol": body.symbol,
                "interval": body.interval,
                "status": "running",
                "formula_readable": formula.get("readable", ""),
                "market_regime": market_regime,
                "regime_warning": regime_warning,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Deploy failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


def _compute_tearsheet(
    tokens: list[int],
    symbol: str,
    interval: str,
    exchange: str,
    market_type: str = "spot",
    vocab_version: str = "v4",
) -> dict[str, Any]:
    """Recompute a formula's factor series on real klines and build tearsheet stats.

    Same PandasStackVM pipeline as the saved-strategy backtest endpoint; the
    feature vocab is matched to the strategy's mining version via
    ``_pandas_vocab_for_version``.
    """
    import numpy as np
    import pandas as pd

    from selene_space_alpha.vocab import FormulaVocab
    from selene_space_alpha.vm import PandasStackVM
    from selene_space_market.kline_db import KlineDB

    db_path = _default_klines_db_path()
    if not db_path.exists():
        raise HTTPException(
            status_code=503, detail="klines.db not found; import data first"
        )
    db = KlineDB(db_path=str(db_path))
    # klines.db stores A-share bars under market_type="astock" (import script
    # convention) regardless of the caller's market_type — same split as the
    # saved-strategy backtest endpoint.
    db_market_type = "astock" if exchange == "astock" else market_type
    df = db.get_klines(exchange, db_market_type, symbol, interval)
    if df.empty or len(df) < 40:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Insufficient kline data for {symbol} {interval} ({len(df)} bars); "
                "import more data or re-run mining."
            ),
        )

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().reset_index(drop=True)

    feature_names, op_names = _pandas_vocab_for_version(vocab_version)
    vocab = FormulaVocab(feature_names=feature_names, operator_names=op_names)
    vm = PandasStackVM(vocab)

    features: dict[int, pd.Series] = {}
    for idx, (feat_name, fn) in enumerate(vm._factor_fns):
        if fn is not None:
            try:
                series = fn(df).fillna(0.0)
                series = (
                    series.replace([float("inf")], 1e6)
                    .replace([float("-inf")], -1e6)
                    .clip(-1e6, 1e6)
                )
                features[idx] = series
            except Exception:
                features[idx] = pd.Series(0.0, index=df.index)
        else:
            col_name = feat_name.lower()
            features[idx] = (
                df[col_name].fillna(0.0)
                if col_name in df.columns
                else pd.Series(0.0, index=df.index)
            )

    score = vm.execute(tokens, df, precomputed_features=features)
    if score is None:
        raise HTTPException(
            status_code=422, detail="Formula is invalid for the current vocabulary/VM"
        )
    factor = (
        pd.Series(np.asarray(score, dtype=float)).fillna(0.0).reset_index(drop=True)
    )

    close = df["close"].values.astype(float)
    fwd_ret = np.full(len(factor), np.nan)
    fwd_ret[:-1] = close[1:] / close[:-1] - 1.0
    ret_s = pd.Series(fwd_ret)

    valid = factor.notna() & ret_s.notna()
    n_valid = int(valid.sum())

    # Rolling IC: corr(factor window, forward return window)
    window = 20
    ic_series: list[float | None] = []
    for i in range(len(factor)):
        lo = max(0, i - window + 1)
        f_win = factor.iloc[lo : i + 1]
        r_win = ret_s.iloc[lo : i + 1]
        mask = ~(f_win.isna() | r_win.isna())
        if mask.sum() < 5 or f_win[mask].std() < 1e-12:
            ic_series.append(None)
            continue
        ic = f_win[mask].rank().corr(r_win[mask].rank())
        ic_series.append(None if pd.isna(ic) else round(float(ic), 4))

    # Turnover: |rank change| of factor values between consecutive bars
    ranks = factor.rank(pct=True)
    turnover = float(ranks.diff().abs().mean()) if n_valid > 1 else 0.0

    # Quantile buckets: factor quintile vs mean forward return per bucket
    n_buckets = 5
    try:
        bucket_ids = (
            factor.rank(method="first")
            .apply(
                lambda v: int(v / max(1, len(factor)) * n_buckets)
                if v <= len(factor)
                else n_buckets - 1
            )
            .clip(0, n_buckets - 1)
        )
        quantile_ret = []
        for b in range(n_buckets):
            sel = (bucket_ids == b) & valid
            quantile_ret.append(
                round(float(ret_s[sel.values].mean()) * 100, 4) if sel.sum() else None
            )
    except Exception:
        quantile_ret = [None] * n_buckets

    ic_arr = np.array([v for v in ic_series if v is not None], dtype=float)

    return {
        "available": True,
        "symbol": symbol,
        "interval": interval,
        "n_bars": int(len(df)),
        "n_valid": n_valid,
        "summary": {
            "observations": n_valid,
            "ic_mean": round(float(ic_arr.mean()), 4) if len(ic_arr) else None,
            "turnover": round(turnover, 4),
            "abs_mean": round(float(factor.abs()[valid].mean()), 6)
            if n_valid
            else None,
        },
        "ic_series": ic_series,
        "quantile_returns": quantile_ret,
        "timestamps": [int(t) for t in df["timestamp"].tolist()],
        "factor_preview": [round(float(v), 6) for v in factor.tail(50)],
    }


def _saved_strategy_tokens(strategy_data: dict[str, Any]) -> tuple[list[int], str]:
    """Extract formula tokens + version tag from a saved strategy JSON."""
    formula = strategy_data.get("formula") or strategy_data.get("best_formula", {})
    tokens = formula.get("tokens", [])
    version = str(strategy_data.get("version") or "v4")
    return tokens, version


class TearsheetCompareRequest(BaseModel):
    strategy_names: list[str] = Field(..., min_length=1, max_length=5)
    symbol: str = Field("BTC/USDT", description="Trading pair")
    interval: str = Field("15m", description="Kline interval")
    exchange: str = Field(
        "binance", description="Data source exchange (astock for A-share)"
    )
    market_type: str = Field(
        "spot", description="Market type (spot/futures; astock uses astock partition)"
    )


@router.get("/saved-strategies/{strategy_name}/tearsheet")
async def get_strategy_tearsheet(
    strategy_name: str,
    symbol: str = Query("BTC/USDT"),
    interval: str = Query("15m"),
    exchange: str = Query("binance"),
    market_type: str = Query("spot"),
) -> APIResponse:
    """Tearsheet for a saved alpha formula: rolling IC / turnover / quantile returns.

    Computed on demand from klines.db (mining engine does not persist factor series).
    """
    strategy_data = _find_saved_strategy(strategy_name)
    if strategy_data is None:
        raise HTTPException(
            status_code=404, detail=f"Strategy '{strategy_name}' not found"
        )
    tokens, vocab_version = _saved_strategy_tokens(strategy_data)
    payload = _compute_tearsheet(
        tokens,
        symbol,
        interval,
        exchange,
        market_type=market_type,
        vocab_version=vocab_version,
    )
    return APIResponse(
        success=True,
        data={
            "strategy_name": strategy_name,
            "formula_readable": (
                strategy_data.get("formula") or strategy_data.get("best_formula", {})
            ).get("readable", ""),
            **payload,
        },
    )


# -- onboarding: data-file enumeration + kline coverage/sync (刀A/刀B) -------


def _parquet_sandbox_roots() -> list[Path]:
    return [
        Path.home() / ".selenespace" / "data",
        Path.cwd() / "demo" / "data",
    ]


@router.get("/data-files")
async def list_data_files() -> APIResponse:
    """Enumerate parquet kline files under the sandbox roots."""
    files: list[dict[str, Any]] = []
    for root in _parquet_sandbox_roots():
        if not root.exists():
            continue
        for p in sorted(root.glob("*.parquet")):
            files.append(
                {"name": p.name, "path": str(p), "size_bytes": p.stat().st_size}
            )
    return APIResponse(success=True, data={"files": files})


@router.get("/kline-coverage")
async def kline_coverage(
    exchange: str = Query(..., description="Exchange provider"),
    market_type: str = Query(..., description="Market type: spot or futures"),
    symbol: str = Query(..., description="Trading pair"),
    interval: str = Query(..., description="Kline interval"),
) -> APIResponse:
    """Show how many candles a partition holds and its time span.

    Read-semantics probe, so GET: the client aborts every in-flight GET when a
    non-GET fires, and the panel probes coverage on mount — as a POST this
    killed the validation report GET sharing the same tick.
    """
    from selene_space_market.kline_db import KlineDB

    db = KlineDB(db_path=str(_default_klines_db_path()))
    try:
        count = db.count(exchange, market_type, symbol, interval)
        latest = db.get_latest_timestamp(exchange, market_type, symbol, interval)
    finally:
        db.close()
    return APIResponse(
        success=True,
        data={
            "count": count,
            "latest_timestamp": latest,
            "latest_iso": (
                time.strftime("%Y-%m-%d %H:%M", time.gmtime(latest / 1000))
                if latest
                else None
            ),
        },
    )


@router.post("/saved-strategies/tearsheet/compare")
async def compare_strategy_tearsheets(body: TearsheetCompareRequest) -> APIResponse:
    """Compare 2-5 saved alpha formulas on the same symbol/interval.

    Returns one tearsheet payload per strategy; missing strategies are listed
    in ``missing``.
    """
    tearsheets: list[dict[str, Any]] = []
    missing: list[str] = []
    for name in dict.fromkeys(body.strategy_names):
        strategy_data = _find_saved_strategy(name)
        if strategy_data is None:
            missing.append(name)
            continue
        tokens, vocab_version = _saved_strategy_tokens(strategy_data)
        payload = _compute_tearsheet(
            tokens,
            body.symbol,
            body.interval,
            body.exchange,
            market_type=body.market_type,
            vocab_version=vocab_version,
        )
        tearsheets.append({"strategy_name": name, **payload})
    return APIResponse(
        success=True,
        data={
            "symbol": body.symbol,
            "interval": body.interval,
            "tearsheets": tearsheets,
            "missing": missing,
        },
    )


REVIEW_STATUSES = ("discovered", "inspected", "paper_trading", "live", "retired")


def _resolve_strategy_file(strategy_name: str) -> Path | None:
    """Resolve a saved alpha strategy JSON file path by name across all version dirs."""
    if not _strategies_dir.exists():
        return None
    for version_dir in _strategies_dir.glob("alpha_v*"):
        if not version_dir.is_dir():
            continue
        target = version_dir / f"{strategy_name}.json"
        if target.exists():
            return target
        for json_file in version_dir.glob("*.json"):
            if json_file.stem == strategy_name or strategy_name in json_file.stem:
                return json_file
    return None


class ReviewStatusUpdate(BaseModel):
    review_status: str = Field(
        ..., description="One of: discovered, inspected, paper_trading, live, retired"
    )


@router.patch("/saved-strategies/{strategy_name}/review-status")
async def update_review_status(
    strategy_name: str, body: ReviewStatusUpdate
) -> APIResponse:
    """Set the review lifecycle status of a saved alpha strategy (persisted in its JSON)."""
    if body.review_status not in REVIEW_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid review_status '{body.review_status}'. Allowed: {', '.join(REVIEW_STATUSES)}",
        )
    path = _resolve_strategy_file(strategy_name)
    if path is None:
        raise HTTPException(
            status_code=404, detail=f"Strategy '{strategy_name}' not found"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["review_status"] = body.review_status
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return APIResponse(
        success=True, data={"name": strategy_name, "review_status": body.review_status}
    )


class BatchReviewStatusUpdate(BaseModel):
    strategy_names: list[str] = Field(..., min_length=1)
    review_status: str = Field(
        ..., description="One of: discovered, inspected, paper_trading, live, retired"
    )


@router.patch("/saved-strategies/review-status/batch")
async def batch_update_review_status(body: BatchReviewStatusUpdate) -> APIResponse:
    """Batch-update review status. Returns updated/missing groups; caller applies only updated."""
    if body.review_status not in REVIEW_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid review_status '{body.review_status}'. Allowed: {', '.join(REVIEW_STATUSES)}",
        )
    updated: list[dict[str, str]] = []
    missing: list[str] = []
    for name in dict.fromkeys(body.strategy_names):  # dedupe, keep order
        path = _resolve_strategy_file(name)
        if path is None:
            missing.append(name)
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            data["review_status"] = body.review_status
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            updated.append({"name": name, "review_status": body.review_status})
        except Exception:
            logger.warning("Failed to update review status: %s", name)
            missing.append(name)
    return APIResponse(success=True, data={"updated": updated, "missing": missing})


class KlineSyncRequest(BaseModel):
    exchange: str
    market_type: str
    symbol: str
    interval: str
    days: int = Field(365, ge=1, le=3650)


@router.post("/kline-sync")
async def kline_sync(request: KlineSyncRequest) -> APIResponse:
    """Backfill one kline partition from the exchange (REST, ~365d).

    Retries twice with backoff: the local proxy drops concurrent TLS
    handshakes (the WS poll storms run continuously), so a single attempt
    often lands inside a storm window.
    """
    from app.core.dependencies import get_kline_manager

    manager = get_kline_manager()
    if manager is None:
        raise HTTPException(status_code=503, detail="Kline manager not initialized")
    written = 0
    for attempt in range(3):
        written = await manager.sync_incremental(
            request.exchange,
            request.market_type,
            request.symbol,
            request.interval,
            days=request.days,
        )
        if written > 0:
            break
        await asyncio.sleep(3.0 * (attempt + 1))
    return APIResponse(success=True, data={"written": written})


@router.get("/mining")
async def list_mining_sessions() -> APIResponse:
    """Return active alpha mining sessions.

    Lists currently-tracked mining sessions (running, completed, failed)
    from the in-memory session store. Returns an empty list when no
    sessions have been started yet.
    """
    sessions: list[dict[str, Any]] = []
    with _sessions_lock:
        items = list(_sessions.items())
    for sid, session in items:
        sessions.append(
            {
                "session_id": sid,
                "status": session.get("status", "unknown"),
                "step": session.get("step", 0),
                "best_score": _safe_score(session.get("best_score")),
                "train_steps": session.get("train_steps"),
                "started_at": session.get("started_at"),
            }
        )

    sessions.sort(key=lambda x: x.get("started_at", 0) or 0, reverse=True)
    return APIResponse(success=True, data={"sessions": sessions})


# ---------------------------------------------------------------------------
# P2: artifact management (zip export / import)
# ---------------------------------------------------------------------------

# Per-session files that count as "alpha artifacts" for listing/export. The
# legacy flat layout (best_meme_strategy_{id}.json at the results root) is
# picked up by a separate glob. Everything else (screenshots, report.html,
# deployed/ copies) is left out of the bundle.
_SESSION_ARTIFACT_NAMES = (
    "best_meme_strategy.json",
    "training_history.json",
    "checkpoint.pt",
)


def _artifact_kind(name: str) -> str:
    if name == "training_history.json":
        return "history"
    if name == "checkpoint.pt":
        return "checkpoint"
    return "formula"


def _iter_artifact_files() -> list[tuple[str, Path]]:
    """Return (relpath, path) for every persisted alpha artifact.

    Walks the per-session subdir layout (``{session_id}/best_meme_strategy.json``
    and ``{session_id}/training_history.json``) plus legacy flat files
    (``best_meme_strategy_{id}.json`` at the results root). Relpaths are
    relative to ``_results_dir`` so the zip round-trips cleanly.
    """
    out: list[tuple[str, Path]] = []
    if not _results_dir.exists():
        return out
    for sub in sorted(_results_dir.iterdir()):
        if sub.is_dir():
            for name in _SESSION_ARTIFACT_NAMES:
                candidate = sub / name
                if candidate.exists():
                    out.append(
                        (candidate.relative_to(_results_dir).as_posix(), candidate)
                    )
    for f in sorted(_results_dir.glob("best_meme_strategy_*.json")):
        out.append((f.relative_to(_results_dir).as_posix(), f))
    return out


@router.get("/artifacts")
async def list_artifacts() -> APIResponse:
    """List persisted alpha artifacts (formula JSON + training history)."""
    artifacts: list[dict[str, Any]] = []
    for relpath, f in _iter_artifact_files():
        artifacts.append(
            {
                "name": Path(relpath).as_posix(),
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
                "kind": _artifact_kind(f.name),
            }
        )
    artifacts.sort(key=lambda x: x["name"])
    return APIResponse(success=True, data={"artifacts": artifacts})


@router.post("/artifacts/export")
async def export_artifacts() -> APIResponse:
    """Bundle all alpha artifacts into a zip (base64 in JSON envelope)."""
    files = _iter_artifact_files()
    if not files:
        return APIResponse(
            success=False, data={"zip_base64": None}, message="No artifacts to export"
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for relpath, f in files:
            zf.write(f, arcname=Path(relpath).as_posix())
    zip_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return APIResponse(
        success=True,
        data={
            "zip_base64": zip_b64,
            "count": len(files),
            "files": [Path(rel).as_posix() for rel, _ in files],
        },
    )


class ArtifactImportRequest(BaseModel):
    zip_base64: str = Field(
        default="", description="Base64-encoded zip of alpha artifacts"
    )
    # Single raw file (checkpoint.pt etc.) for direct upload without zipping.
    raw_name: str = Field(default="", description="File name for raw_base64 uploads")
    raw_base64: str = Field(
        default="", description="Base64-encoded single artifact file"
    )

    model_config = {"extra": "forbid"}


@router.post("/artifacts/import")
async def import_artifacts(request: ArtifactImportRequest) -> APIResponse:
    """Extract a zip bundle into ``_results_dir``, then re-list formulas.

    Rejects entries that escape the results dir (zip-slip) or are neither
    JSON artifacts nor .pt training checkpoints. A single raw file
    (``raw_base64`` + ``raw_name``) is accepted without zipping — same
    suffix allowlist, lands at ``{session_dir}/{raw_name}``.
    """
    _results_dir.mkdir(parents=True, exist_ok=True)
    imported: list[str] = []
    skipped: list[str] = []

    if request.raw_base64:
        rel = Path(request.raw_name or "checkpoint.pt")
        if rel.suffix not in (".json", ".pt") or rel.is_absolute():
            raise HTTPException(
                status_code=400, detail=f"Unsupported raw artifact: {rel.name}"
            )
        session_dir = _results_dir / rel.stem
        session_dir.mkdir(parents=True, exist_ok=True)
        try:
            raw = base64.b64decode(request.raw_base64, validate=True)
        except binascii.Error as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid base64 raw file: {exc}"
            )
        target = session_dir / rel.name
        atomic_write(target, raw)
        imported.append(rel.as_posix())
        return APIResponse(
            success=True,
            data={"imported": imported, "skipped": skipped},
        )

    try:
        raw = base64.b64decode(request.zip_base64, validate=True)
    except binascii.Error as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 zip: {exc}")

    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = Path(info.filename)
            # zip-slip guard: normalize and require the target stays under _results_dir
            target = (_results_dir / rel).resolve()
            if not target.is_relative_to(_results_dir.resolve()):
                skipped.append(info.filename)
                continue
            if target.suffix not in (".json", ".pt"):
                skipped.append(info.filename)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
            imported.append(rel.as_posix())

    return APIResponse(
        success=True,
        data={"imported": imported, "skipped": skipped},
        message=f"Imported {len(imported)} artifact(s), skipped {len(skipped)}",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_formula_files() -> list[tuple[str, Path]]:
    """Yield (formula_id, path) for every persisted best formula.

    Supports both the per-session subdir layout the engine writes today
    (``{session_id}/best_meme_strategy.json``) and the legacy flat layout
    (``best_meme_strategy_{id}.json`` at the results root), so nothing
    currently on disk is lost.
    """
    out: list[tuple[str, Path]] = []
    if not _results_dir.exists():
        return out
    for sub in sorted(_results_dir.iterdir()):
        if sub.is_dir():
            candidate = sub / "best_meme_strategy.json"
            if candidate.exists():
                out.append((sub.name, candidate))
    for f in sorted(_results_dir.glob("best_meme_strategy_*.json")):
        out.append((f.stem.replace("best_meme_strategy_", ""), f))
    return out


def _get_formula_tokens(formula_id: str) -> list[int] | None:
    with _sessions_lock:
        if formula_id in _sessions:
            return _sessions[formula_id].get("best_formula")

    filepath = _results_dir / f"best_meme_strategy_{formula_id}.json"
    if not filepath.exists():
        filepath = _results_dir / "best_meme_strategy.json"
    if not filepath.exists():
        filepath = _results_dir / formula_id / "best_meme_strategy.json"

    if filepath.exists():
        with open(filepath, encoding="utf-8") as f:
            return cast("list[int] | None", json.load(f))
    return None


def _find_saved_strategy(strategy_name: str) -> dict[str, Any] | None:
    """Find a saved alpha strategy JSON by name across all version directories."""
    if not _strategies_dir.exists():
        return None

    for version_dir in _strategies_dir.glob("alpha_v*"):
        if not version_dir.is_dir():
            continue
        # Try exact name match (e.g., "alpha_v5_adaptive")
        target = version_dir / f"{strategy_name}.json"
        if target.exists():
            with open(target, encoding="utf-8") as f:
                return cast("dict[str, Any] | None", json.load(f))
        # Try without version prefix (e.g., "adaptive" matches "alpha_v5_adaptive.json")
        for json_file in version_dir.glob("*.json"):
            if json_file.stem == strategy_name or strategy_name in json_file.stem:
                with open(json_file, encoding="utf-8") as f:
                    return cast("dict[str, Any] | None", json.load(f))
    return None


async def _run_training(
    session_id: str, request: TrainRequest, start_step: int = 0
) -> None:
    """Background task to run alpha mining training."""
    try:
        from selene_space_alpha.mining import AlphaEngine, TrainingStopRequested
        from selene_space_alpha.config import AlphaCoreConfig
        from selene_space_alpha.data_loader import CryptoDataLoader

        config = AlphaCoreConfig(
            TRAIN_STEPS=request.train_steps,
            BATCH_SIZE=request.batch_size,
            MAX_FORMULA_LEN=request.max_formula_len,
        )

        loader = CryptoDataLoader(config)
        if request.use_synthetic_data:
            loader._create_synthetic_data(n_tokens=300)
        elif request.data_file:
            # Explicit parquet file wins over klines.db (刀② 数据入口).
            try:
                df = _load_parquet_df(request.data_file)
                loader.load_from_dataframe(df)
                with _sessions_lock:
                    _sessions[session_id]["data_source"] = "parquet"
            except (FileNotFoundError, ValueError, OSError) as exc:
                logger.warning(
                    "Parquet data file %s unusable (%s), falling back to klines.db",
                    request.data_file,
                    exc,
                )
                loader.load_from_sqlite(
                    db_path=str(_default_klines_db_path()),
                    symbol=request.symbol,
                    exchange=request.exchange,
                    market_type=request.market_type,
                    interval=request.interval,
                    limit_bars=1500,
                )
                with _sessions_lock:
                    _sessions[session_id]["data_source"] = "real"
                    _sessions[session_id]["fallback_reason"] = str(exc)
        else:
            # Load real kline data from local SQLite (same resolution as saved-strategy backtest)
            try:
                loader.load_from_sqlite(
                    db_path=str(_default_klines_db_path()),
                    symbol=request.symbol,
                    exchange=request.exchange,
                    market_type=request.market_type,
                    interval=request.interval,
                    limit_bars=1500,
                )
                with _sessions_lock:
                    _sessions[session_id]["data_source"] = "real"
            except (FileNotFoundError, ValueError) as exc:
                logger.warning(
                    "No local kline data for %s %s/%s %s (%s), falling back to synthetic",
                    request.exchange,
                    request.market_type,
                    request.symbol,
                    request.interval,
                    exc,
                )
                loader._create_synthetic_data(n_tokens=300)
                with _sessions_lock:
                    _sessions[session_id]["data_source"] = "synthetic"
                    _sessions[session_id]["fallback_reason"] = str(exc)

        output_dir = _results_dir / session_id
        engine = AlphaEngine(
            data_loader=loader,
            config=config,
            use_lord_regularization=request.use_lord,
            output_dir=output_dir,
        )

        # Resume from checkpoint: restore model/opt/best/history, then continue
        # at start_step+1 (checkpoint.step = last fully-trained step).
        if start_step > 0:
            try:
                saved_step = engine.load_checkpoint(_checkpoint_path(session_id))
                start_step = saved_step + 1
            except FileNotFoundError:
                logger.warning(
                    "Resume requested but no checkpoint at %s; starting from 0",
                    _checkpoint_path(session_id),
                )
                start_step = 0

        def _on_step(
            step: int,
            avg_reward: float,
            best_score: float,
            stable_rank: float | None,
            log_line: str | None,
        ) -> None:
            """Write-through callback: engine loop (executor thread) → session.

            Also honors stop: raises StopIteration when the stop endpoint set
            status='stopping', so run_in_executor exits early.
            """
            with _sessions_lock:
                s = _sessions.get(session_id)
                if s is None:
                    return
                if s.get("status") == "stopping":
                    raise TrainingStopRequested("stop requested")
                s["step"] = step
                s["best_score"] = best_score
                # 公式流 write-through:engine 线程同 thread 写,读侧轮询直接看运行中最优。
                s["best_formula"] = engine.best_formula
                hist = s.setdefault(
                    "history",
                    {
                        "step": [],
                        "avg_reward": [],
                        "best_score": [],
                        "stable_rank": [],
                        "rank_step": [],
                        "reward_std": [],
                    },
                )
                hist.setdefault("rank_step", [])
                hist.setdefault("reward_std", [])
                hist["step"].append(step)
                hist["avg_reward"].append(avg_reward)
                hist["best_score"].append(best_score)
                if stable_rank is not None:
                    hist["stable_rank"].append(stable_rank)
                    # rank_step mirrors the engine's sparse stable_rank branch:
                    # same condition, same step — zip(history) is the alignment
                    # contract, step%100 is only the frontend fallback.
                    hist["rank_step"].append(step)
                # Engine appends reward_std before invoking on_step (same
                # thread), so the tail is this step's batch std. The .get
                # guard keeps stand-in engines without the key on the old
                # behavior (skip instead of KeyError).
                reward_std_tail = engine.training_history.get("reward_std")
                if reward_std_tail:
                    hist["reward_std"].append(reward_std_tail[-1])
                if log_line:
                    logs = s.setdefault("logs", [])
                    logs.append(log_line)
                    if len(logs) > _LOG_RING_SIZE:
                        del logs[: len(logs) - _LOG_RING_SIZE]

        with _sessions_lock:
            _sessions[session_id]["status"] = "running"
            _sessions[session_id]["step"] = start_step
            _sessions[session_id]["best_score"] = engine.best_score

        # Run training in thread pool to not block event loop
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                engine.train,
                _on_step,
                start_step,
                str(_checkpoint_path(session_id)),
                10,
            )
        except TrainingStopRequested:
            # Cooperative stop: engine raised from on_step. Finalize with the
            # current best (skip only if nothing was ever found). Persist a
            # final checkpoint so the session can be resumed later.
            last_step = (
                engine.training_history["step"][-1]
                if engine.training_history["step"]
                else start_step
            )
            engine.save_checkpoint(
                _checkpoint_path(session_id), last_step, extra=request.model_dump()
            )
            with _sessions_lock:
                _sessions[session_id]["status"] = "stopped"
                _sessions[session_id]["best_score"] = engine.best_score
                _sessions[session_id]["best_formula"] = engine.best_formula
                _sessions[session_id]["history"] = dict(engine.training_history)
            logger.info("Training %s stopped by user request", session_id)
            return

        # Normal completion: persist final checkpoint before flipping status.
        engine.save_checkpoint(
            _checkpoint_path(session_id),
            request.train_steps - 1,
            extra=request.model_dump(),
        )
        with _sessions_lock:
            _sessions[session_id]["status"] = "completed"
            _sessions[session_id]["best_score"] = engine.best_score
            _sessions[session_id]["best_formula"] = engine.best_formula
            _sessions[session_id]["history"] = dict(engine.training_history)

        # 透明性批2-6: 挖矿完成 → 该标的开放 mission 的 mission_activity
        from app.api.v1.research_missions import notify_experiment_completed

        await notify_experiment_completed(
            "market",
            request.symbol,
            task_id=session_id,
            experiment_kind="mining",
            summary={
                "symbol": request.symbol,
                "interval": request.interval,
                "best_score": engine.best_score,
                "best_formula": engine.best_formula,
            },
        )

    except Exception as e:
        logger.error("Training failed: %s", e)
        with _sessions_lock:
            _sessions[session_id]["status"] = "failed"
            _sessions[session_id]["error"] = str(e)


# ---------------------------------------------------------------------------
# B4 monitoring quadruple endpoints (QD-absorption Phase 2)
#
# v0 scope (consensus plan lock): quadruple registry + new-bar recompute +
# TTL cache. Data source hard-wired to klines.db. Slow recomputes follow the
# backtests.py 202->task_id async convention; stale cache is served with an
# explicit ``stale`` flag rather than blocking.
# ---------------------------------------------------------------------------


class MonitorRegisterRequest(BaseModel):
    symbol: str = Field("BTC/USDT", description="Trading pair, e.g. BTC/USDT")
    interval: str = Field("D1", description="Kline interval, e.g. D1/4h/1h")
    factor: str = Field(
        "BTC_BG_RS_LEVEL",
        description="Background factor token (BTC_BG_RS_LEVEL/SLOPE/EXTREME)",
    )


_monitor_lock = threading.Lock()
_monitor: Any = None
_monitor_tasks: dict[str, dict[str, Any]] = {}


def _get_monitor() -> Any:
    """Lazily build the module-level B4Monitor (klines.db single source)."""
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            from selene_space_alpha.sensors.b4_monitor import B4Monitor

            db = _default_klines_db_path()
            registry = _repo_root() / "data" / "b4_monitor_registry.json"
            _monitor = B4Monitor(db_path=db, registry_path=registry)
        return _monitor


def _monitor_payload(reading: Any) -> dict[str, Any]:
    return {
        "value": _safe_score(reading.value),
        "computed_at": reading.computed_at,
        "last_bar_ts": reading.last_bar_ts,
        "stale": reading.stale,
    }


@router.post("/monitor/register")
async def monitor_register(
    body: MonitorRegisterRequest,
) -> APIResponse[dict[str, Any]]:
    """Register a (klines.db, symbol, interval, factor) monitoring quadruple."""
    try:
        q = _get_monitor().register(body.symbol, body.interval, body.factor)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return APIResponse(
        success=True,
        data={
            "source": q.source,
            "symbol": q.symbol,
            "interval": q.interval,
            "factor": q.factor,
        },
        message="monitor registered",
    )


@router.post("/monitor/unregister")
async def monitor_unregister(
    body: MonitorRegisterRequest,
) -> APIResponse[dict[str, Any]]:
    """Remove a monitoring quadruple. 404 when it was not registered."""
    removed = _get_monitor().unregister(body.symbol, body.interval, body.factor)
    if not removed:
        raise HTTPException(404, "monitor not registered")
    return APIResponse(success=True, data={"removed": True}, message="monitor removed")


@router.get("/monitor/list")
async def monitor_list() -> APIResponse[list[dict[str, Any]]]:
    """List all registered monitoring quadruples."""
    quads = _get_monitor().list_quadruples()
    return APIResponse(
        success=True,
        data=[
            {
                "source": q.source,
                "symbol": q.symbol,
                "interval": q.interval,
                "factor": q.factor,
            }
            for q in quads
        ],
    )


@router.post("/monitor/read", response_model=None)
async def monitor_read(
    body: MonitorRegisterRequest,
    background: BackgroundTasks,
) -> Response | APIResponse[dict[str, Any]]:
    """Serve a reading.

    Cache miss → first recompute synchronously (caller must get a value).
    Cache hit fresh → value + recomputed=false.
    Cache hit stale (TTL expired or new bar) → old value + stale=true, and a
    background refresh is scheduled so the next read is warm (202-style:
    the endpoint never blocks on slow recompute once a value exists).
    """
    mon = _get_monitor()
    if not mon.cache_contains(body.symbol, body.interval, body.factor):
        try:
            reading, recomputed = mon.read(body.symbol, body.interval, body.factor)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e)) from e
        return APIResponse(
            success=True,
            data={**_monitor_payload(reading), "recomputed": recomputed},
            message="first compute",
        )
    # cached path: never block — serve stale-aware snapshot, refresh in background
    reading = mon.read_stale_aware(body.symbol, body.interval, body.factor)
    if reading.stale:
        background.add_task(mon.recompute, body.symbol, body.interval, body.factor)
    return APIResponse(
        success=True,
        data={**_monitor_payload(reading), "recomputed": False},
        message=("stale — refresh scheduled" if reading.stale else "cache hit"),
    )


@router.post("/monitor/read/async")
async def monitor_read_async(
    body: MonitorRegisterRequest,
) -> APIResponse[dict[str, Any]]:
    """Kick off a recompute as a background task; poll GET /monitor/task/{task_id}."""
    task_id = f"b4m-{uuid.uuid4().hex[:8]}"
    _monitor_tasks[task_id] = {
        "task_id": task_id,
        "status": "pending",
        "symbol": body.symbol,
        "interval": body.interval,
        "factor": body.factor,
        "created_at": int(time.time() * 1000),
    }
    asyncio.create_task(_run_monitor_recompute(task_id, body))
    return APIResponse(
        success=True,
        data={"task_id": task_id, "status": "pending"},
        message="recompute scheduled",
    )


@router.get("/monitor/task/{task_id}")
async def monitor_task_status(task_id: str) -> APIResponse[dict[str, Any]]:
    task = _monitor_tasks.get(task_id)
    if task is None:
        raise HTTPException(404, f"unknown monitor task {task_id}")
    return APIResponse(success=True, data=task)


async def _run_monitor_recompute(task_id: str, body: MonitorRegisterRequest) -> None:
    mon = _get_monitor()
    _monitor_tasks[task_id]["status"] = "running"
    try:
        await asyncio.to_thread(mon.recompute, body.symbol, body.interval, body.factor)
        _monitor_tasks[task_id]["status"] = "completed"
    except Exception as e:
        logger.error("Monitor recompute %s failed: %s", task_id, e)
        _monitor_tasks[task_id]["status"] = "failed"
        _monitor_tasks[task_id]["error"] = str(e)
