"""Research missions 路由 — B1/B2 读面 + B4 TODAY 投影聚合。

垂直切片(TODAY = 投影非生成物):
- GET /research/missions                — Mission 列表(phase 过滤)
- GET /research/missions/{id}            — Mission 详情(四区材料:context/outcome/activity)
- GET /research/missions/{id}/activity   — 活动流回放
- POST /research/missions/{id}/decision  — NEXT 四动作(Approve/More evidence/Redirect/Park)
- GET /research/today                    — B4 聚合端点:NEEDS YOU / WORKING / WORLD CHANGED 三段读数

错误处理约定: 完整异常只进服务端日志,HTTP 响应返回通用文案,不泄露内部细节。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research-missions"])

# local-api 进程内可导入 storage 包(不依赖 pip install -e)
sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "packages" / "storage"))

from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402
from selene_space_storage.repositories.research_missions import (  # noqa: E402
    ResearchMissionRepository,
)


class MissionDecision(BaseModel):
    action: str = Field(..., description="approve|more_evidence|redirect|park")
    note: Optional[str] = None


class MissionCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    question: str = Field(..., min_length=1, max_length=2000)
    subject_type: str = Field(..., description="strategy|market|event|system")
    subject_id: str = Field(..., min_length=1, max_length=200)
    context: Optional[dict[str, Any]] = None


def _repo() -> ResearchMissionRepository:
    """Lazily build a repo over the app-shared AsyncSQLiteManager."""
    from app.core.dependencies import get_db_manager

    db = get_db_manager()
    if db is None:
        raise HTTPException(status_code=503, detail="数据库未就绪")
    return ResearchMissionRepository(db)


async def notify_experiment_completed(
    subject_type: str,
    subject_id: str,
    *,
    task_id: str,
    experiment_kind: str,
    summary: dict[str, Any] | None = None,
) -> None:
    """实验完成 → 挂到该 subject 的开放 mission 的 mission_activity(透明性批2-6)。

    Fire-and-forget:DB 未就绪 / 无匹配 mission / 写入失败一律静默,
    绝不让实验本身失败(A1 断层修复:实验完成必须让研究前进一小步)。
    """
    try:
        from app.core.dependencies import get_db_manager

        db = get_db_manager()
        if db is None:
            return
        repo = ResearchMissionRepository(db)
        missions = await repo.list_missions(
            subject_type=subject_type, subject_id=subject_id
        )
        open_missions = [
            m for m in missions if m.get("phase") not in ("resolved", "parked")
        ]
        detail: dict[str, Any] = {
            "task_id": task_id,
            "experiment_kind": experiment_kind,
        }
        if summary:
            detail["summary"] = summary
        for m in open_missions:
            await repo.append_activity(
                m["id"], actor="system", kind="experiment_completed", detail=detail
            )
    except Exception:
        logger.warning(
            "Failed to append experiment_completed activity (subject=%s/%s task=%s)",
            subject_type,
            subject_id,
            task_id,
            exc_info=True,
        )


def _http_500(context: str, exc: Exception) -> HTTPException:
    logger.exception("research missions api error: %s", context)
    return HTTPException(status_code=500, detail=f"{context}失败,请查看服务端日志")


def _mission_projection(mission: dict[str, Any]) -> dict[str, Any]:
    """TODAY 压缩投影卡(单对象单源:全部字段来自 mission 行本身)。"""
    return {
        "id": mission["id"],
        "title": mission["title"],
        "question": mission["question"],
        "phase": mission["phase"],
        "subject_type": mission["subject_type"],
        "subject_id": mission["subject_id"],
        "updated_at": mission["updated_at"],
    }


@router.post("/missions")
async def create_mission(body: MissionCreate):
    """创建新 mission(透明性批3-10:枢纽闭合,让"开新实验验证"有落点)。

    Repository.create 自带 mission_opened seed activity(append-only 链起点)。
    """
    try:
        repo = _repo()
        mission_id = await repo.create(
            body.title,
            body.question,
            body.subject_type,
            body.subject_id,
            created_by="user",
            context=body.context,
        )
        mission = await repo.get(mission_id)
        return {"mission": mission}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _http_500("Mission 创建", e)


@router.get("/missions")
async def list_missions(
    phase: Optional[str] = None,
    subject_type: Optional[str] = None,
    limit: int = 50,
):
    """Mission 列表(newest first;phase/subject_type 过滤)。"""
    try:
        repo = _repo()
        missions = await repo.list_missions(
            subject_type=subject_type, phase=phase, limit=max(1, min(limit, 200))
        )
        return {"missions": missions}
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("Mission 列表查询", e)


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: str):
    """Mission 详情 = Decision Surface 四区材料(context + outcome + activity)。"""
    try:
        repo = _repo()
        mission = await repo.get(mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission 不存在")
        activity = await repo.list_activity(mission_id)
        return {"mission": mission, "activity": activity}
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("Mission 详情查询", e)


@router.get("/missions/{mission_id}/activity")
async def get_mission_activity(mission_id: str, limit: int = 100):
    """B2 活动流回放(时间正序)。"""
    try:
        repo = _repo()
        mission = await repo.get(mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission 不存在")
        return {
            "activity": await repo.list_activity(mission_id, max(1, min(limit, 500)))
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("活动流查询", e)


@router.post("/missions/{mission_id}/decision")
async def post_decision(mission_id: str, body: MissionDecision):
    """NEXT 四动作 — 研究负责人词表,状态机合法转移。

    - approve      → needs_input → validation(批准建议,进入执行)
    - more_evidence → needs_input → investigation(要求更多证据)
    - redirect     → needs_input → investigation(重新定向,note 必填理由)
    - park         → needs_input → parked(停放)
    """
    action_map = {
        "approve": ("validation", "Approve — 批准建议进入执行"),
        "more_evidence": ("investigation", "Request more evidence — 要求补充证据"),
        "redirect": ("investigation", "Redirect — 重新定向调查"),
        "park": ("parked", "Park — 停放此调查"),
    }
    if body.action not in action_map:
        raise HTTPException(
            status_code=422,
            detail=f"未知动作 {body.action!r};合法: approve|more_evidence|redirect|park",
        )
    to_phase, note = action_map[body.action]
    if body.action == "redirect" and not body.note:
        raise HTTPException(status_code=422, detail="redirect 必须提供新方向说明")
    try:
        repo = _repo()
        mission = await repo.get(mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission 不存在")
        if mission["phase"] != "needs_input":
            raise HTTPException(
                status_code=409,
                detail=f"当前 phase={mission['phase']},不在决策点(仅 needs_input 可决策)",
            )
        updated = await repo.transition(
            mission_id,
            to_phase,
            actor="user",
            note=body.note or note,
            detail={"action": body.action},
        )
        return {"mission": updated}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise _http_500("Mission 决策", e)


@router.get("/today")
async def today_projection():
    """B4 聚合端点 — TODAY 页三段读数(投影非生成物)。

    LAST HANDOFF    最近一次 user 决策(活动流里 actor=user 的最近一条)
    NEEDS YOU       所有 phase=needs_input 的 Mission(视觉最重段)
    WORKING         investigation/validation 中的 Mission(诚实空态)
    WORLD CHANGED   resolved/parked Mission 的最新变化(轻量)
    """
    try:
        repo = _repo()
        needs_you = await repo.list_missions(phase="needs_input", limit=10)
        working = await repo.list_missions(limit=50)
        working = [m for m in working if m["phase"] in ("investigation", "validation")]
        recent_changes = await repo.list_missions(limit=20)
        recent_changes = [
            m for m in recent_changes if m["phase"] in ("resolved", "parked")
        ][:5]

        last_handoff = None
        if recent_changes:
            latest = recent_changes[0]
            acts = await repo.list_activity(latest["id"], limit=50)
            user_acts = [a for a in acts if a["actor"] == "user"]
            if user_acts:
                last = user_acts[-1]
                last_handoff = {
                    "mission_id": latest["id"],
                    "occurred_at": last["occurred_at"],
                    "kind": last["kind"],
                    "detail": last["detail"],
                }
        return {
            "last_handoff": last_handoff,
            "needs_you": [_mission_projection(m) for m in needs_you],
            "working": [_mission_projection(m) for m in working],
            "world_changed": [_mission_projection(m) for m in recent_changes],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("TODAY 投影聚合", e)


# ---------------------------------------------------------------------------
# State(T) 恢复 — 0908 施工硬要求:"先证明 State(T) 能真实恢复,再做 UI"
#
# 数据底账(全部真值,不 mock):
# - 逐笔 Episode 源 = backtests.trades_json(bt-b62d32ac,7 笔含 entry/exit
#   时间戳/价格/pnl)—— 之前视觉稿 E1-E7 逐笔是占位,真值其实一直在库里
# - K 线窗口 = klines.db 真切片(trades_json 真时间戳 → SOL/USDT 4h 3037 bars
#   全覆盖交易窗)
# - 通道截面重算 = KeltnerChannelV2Strategy 同式(EMA span / TR EMA / ADX)
#   RECONSTRUCTED 标注:规则可回源,非当时落盘
# - funding/oi/news/memory:当时不落盘 = NOT AVAILABLE 诚实空态
# ---------------------------------------------------------------------------

import json as _json  # noqa: E402

_STATE_MAIN_DB_HINT = "State(T) 主库未就绪"


def _strategy_db_path() -> Path:
    return Path.home() / ".selenespace" / "data" / "selenespace.db"


def _klines_db_path() -> Path:
    # cast: mypy resolves selene_space_core as Any (not on its search path),
    # which would trip warn_return_any; the helper's own ``-> Path`` is exact.
    return cast(Path, default_klines_db_path())


def _provenance(recorded: bool, reconstructed: bool = False) -> str:
    if recorded:
        return "RECORDED"
    return "RECONSTRUCTED" if reconstructed else "NOT_AVAILABLE"


def _ms_to_iso(ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()


async def _load_strategy_row(conn, strategy_id: str) -> dict[str, Any] | None:
    rows = await conn.execute_fetchall(
        "SELECT id, name, config_json FROM strategies WHERE id = ?", (strategy_id,)
    )
    if not rows:
        return None
    return {
        "id": rows[0][0],
        "name": rows[0][1],
        "config": _json.loads(rows[0][2]),
    }


async def _load_episodes(conn, strategy_id: str) -> list[dict[str, Any]]:
    """episodes = 失败回测的逐笔 trades(作为 Episode 源)。

    找该策略最近一次含 trades 的回测,取 trades_json 逐笔。若该回测
    symbol/interval 与策略声明配置不一致,如实标注 drift 字段(这正是
    Keltner 案:声明 BTC 1h vs 实跑 SOL 4h)。
    """
    rows = await conn.execute_fetchall(
        """
        SELECT id, symbol, interval, trades_json, trades_count, metrics_json, created_at
        FROM backtests WHERE strategy_id = ? AND trades_count > 0
        ORDER BY created_at DESC LIMIT 1
        """,
        (strategy_id,),
    )
    if not rows:
        return []
    bt_id, bt_symbol, bt_interval, trades_raw, trades_count, metrics_raw, created = (
        rows[0]
    )
    trades = _json.loads(trades_raw) if trades_raw else []
    metrics = _json.loads(metrics_raw) if metrics_raw else {}

    cfg = await conn.execute_fetchall(
        "SELECT config_json FROM strategies WHERE id = ?", (strategy_id,)
    )
    declared = _json.loads(cfg[0][0]) if cfg else {}
    declared_symbol = (declared.get("symbols") or [None])[0]
    declared_interval = declared.get("interval")
    drifted = declared_symbol is not None and (
        bt_symbol != declared_symbol or bt_interval != declared_interval
    )

    episodes: list[dict[str, Any]] = []
    for i, t in enumerate(trades, 1):
        qty = t.get("quantity") or 0.0
        entry_px = t.get("entry_price") or 0.0
        pnl = t.get("pnl") or 0.0
        ret = pnl / (entry_px * qty) if entry_px and qty else None
        episodes.append(
            {
                "n": i,
                "backtest_id": bt_id,
                "symbol": t.get("symbol") or bt_symbol,
                "side": t.get("side"),
                "entry_time": _ms_to_iso(t["entry_time"]),
                "exit_time": _ms_to_iso(t["exit_time"]),
                "entry_price": entry_px,
                "exit_price": t.get("exit_price"),
                "pnl": pnl,
                "return": ret,
                "quantity": qty,
            }
        )
    return [
        {
            "episodes": episodes,
            "backtest_id": bt_id,
            "as_run": {"symbol": bt_symbol, "interval": bt_interval},
            "declared": {
                "symbol": declared_symbol,
                "interval": declared_interval,
            },
            "drift": drifted,
            "metrics": metrics,
            "created_at": created,
        }
    ]


def _kline_window(
    symbol: str, interval: str, start_ms: int, end_ms: int, pad_bars: int = 60
) -> list[dict[str, Any]]:
    """klines.db 真切片(RECORDED)。pad_bars 给指标 warmup 前文。"""
    import sqlite3

    path = _klines_db_path()
    if not path.exists():
        return []
    interval_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}.get(interval)
    if interval_ms is None:
        return []
    start_padded = start_ms - pad_bars * interval_ms
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume FROM klines
            WHERE exchange='binance' AND market_type='spot' AND symbol=?
              AND interval=? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
            """,
            (symbol, interval, start_padded, end_ms),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "t": _ms_to_iso(r[0]),
            "o": r[1],
            "h": r[2],
            "l": r[3],
            "c": r[4],
            "v": r[5],
        }
        for r in rows
    ]


def _keltner_snapshot(
    df_bars: list[dict[str, Any]], params: dict[str, Any]
) -> dict[str, Any] | None:
    """Keltner V2 同式通道截面重算(RECONSTRUCTED — 规则可回源,非当时落盘)。

    与 KeltnerChannelV2Strategy._envelope / _adx_line 完全同式:
    EMA(span=ma_length) 中心 + EMA TR(span=atr_length) 波幅;ADX 同参。
    """
    import pandas as pd

    if (
        len(df_bars)
        < max(params.get("ma_length", 20), params.get("adx_length", 14)) + 5
    ):
        return None
    data = pd.DataFrame(df_bars).rename(
        columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    )
    n = len(data) - 1
    centre = data["close"].ewm(span=params["ma_length"], adjust=False).mean()
    prev_close = data["close"].shift(1)
    tr_legs = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - prev_close).abs(),
            (data["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    tr = tr_legs.max(axis=1)
    rng = tr.ewm(span=params["atr_length"], adjust=False).mean()
    mult = params["mult"]
    upper = centre + rng * mult
    lower = centre - rng * mult

    from selene_space_strategy.indicators.adx import ADX

    adx_series = ADX(params["adx_length"]).calculate_full(data).adx

    return {
        "entry_bar": {
            "t": df_bars[n]["t"],
            "close": df_bars[n]["c"],
            "upper_band": round(float(upper.iloc[n]), 4),
            "lower_band": round(float(lower.iloc[n]), 4),
            "centre": round(float(centre.iloc[n]), 4),
            "adx": round(float(adx_series.iloc[n]), 2),
            "adx_threshold": params["adx_threshold"],
        },
        "rule": {
            "entry": f"close pierces {'upper' if params.get('enable_long') else 'lower'} band "
            f"+ ADX>{params['adx_threshold']} + no-gap filter",
            "exit": "opposite band give-back / bracket "
            f"(stop {params.get('stop_pct', 0):.1%} / tp {params.get('tp_pct', 0):.1%})",
        },
    }


async def _open_main_db():
    """主库连接(aiosqlite,与 local-api 线程模型一致)。"""
    import aiosqlite

    path = _strategy_db_path()
    if not path.exists():
        return None
    conn = await aiosqlite.connect(path)
    conn.row_factory = None
    return conn


async def _ensure_decisions_table(conn) -> None:
    """user_decisions 落库表(0908 复审:USER DECISION 要落盘)。

    轻量自管表,不进 migrations_pkg(同 serenity_pool_scans 先例:读面配套
    表,不碰共享迁移链)。幂等 CREATE IF NOT EXISTS。
    """
    await conn.execute(_DECISIONS_DDL)
    await conn.commit()


_DECISIONS_DDL = """
CREATE TABLE IF NOT EXISTS user_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT NOT NULL,
    episode_n INTEGER NOT NULL,
    decided_at TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('execute','skip','investigate')),
    note TEXT,
    UNIQUE (strategy_id, episode_n)
)
"""


@router.get("/strategy/{strategy_id}/state")
async def get_strategy_state(strategy_id: str):
    """STRATEGY workspace 六段数据(全部真值)。

    NOW = 声明 vs 实跑 + 最近回测 metrics + 开放 Mission
    HISTORY/ERRORS = 逐笔 Episode(trades_json 真值)
    INTELLIGENCE/EVOLUTION = Mission outcome(结案连锁)+ Lineage(声明漂移)
    """
    conn = await _open_main_db()
    if conn is None:
        raise HTTPException(status_code=503, detail=_STATE_MAIN_DB_HINT)
    try:
        strategy = await conn.execute_fetchall(
            "SELECT id, name, config_json FROM strategies WHERE id = ?",
            (strategy_id,),
        )
        if not strategy:
            raise HTTPException(status_code=404, detail="策略不存在")
        bundle = await _load_episodes_sync(conn, strategy_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="该策略无含成交的回测记录")

        episodes_pack = bundle
        missions = await conn.execute_fetchall(
            """
            SELECT id, title, question, phase, updated_at, context_json, outcome_json
            FROM research_missions
            WHERE subject_type='strategy' AND subject_id=?
            ORDER BY updated_at DESC LIMIT 10
            """,
            (strategy_id,),
        )
        mission_list = []
        for r in missions:
            mission_list.append(
                {
                    "id": r[0],
                    "title": r[1],
                    "question": r[2],
                    "phase": r[3],
                    "updated_at": r[4],
                    "context": _json.loads(r[5]) if r[5] else {},
                    "outcome": _json.loads(r[6]) if r[6] else {},
                }
            )
        return {
            "strategy": {
                "id": strategy[0][0],
                "name": strategy[0][1],
                "config": _json.loads(strategy[0][2]),
            },
            "episodes_pack": episodes_pack,
            "missions": mission_list,
        }
    except HTTPException:
        raise
    except Exception as e:
        await conn.close()
        raise _http_500("Strategy State 查询", e)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


async def _load_episodes_sync(conn, strategy_id: str) -> dict[str, Any] | None:
    bundle = await _load_episodes(conn, strategy_id)
    return bundle[0] if bundle else None


@router.get("/strategy/{strategy_id}/state/{episode_n}")
async def get_episode_state(strategy_id: str, episode_n: int):
    """Episode State(T) 恢复 — 给定策略+Episode 序号,恢复 T 时刻各分量。

    每分量带 provenance:
    - market_state: klines.db 真切片 RECORDED + 通道截面重算 RECONSTRUCTED
    - strategy_state: 声明配置 RECORDED + 实跑环境(as_run)RECORDED
    - signal_state: 规则触发重算 RECONSTRUCTED(当时无信号落盘)
    - event/funding/oi/news/memory: NOT_AVAILABLE(当时不落盘,诚实空态)
    - outcome: trades_json RECORDED(Reveal 前端不得渲染)
    """
    conn = await _open_main_db()
    if conn is None:
        raise HTTPException(status_code=503, detail=_STATE_MAIN_DB_HINT)
    try:
        strategy = await conn.execute_fetchall(
            "SELECT config_json FROM strategies WHERE id = ?", (strategy_id,)
        )
        if not strategy:
            raise HTTPException(status_code=404, detail="策略不存在")
        bundle = await _load_episodes_sync(conn, strategy_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="该策略无含成交的回测记录")
        matches = [e for e in bundle["episodes"] if e["n"] == episode_n]
        if not matches:
            raise HTTPException(status_code=404, detail=f"Episode {episode_n} 不存在")
        ep = matches[0]

        params = _json.loads(strategy[0][0])["parameters"]
        as_run = bundle["as_run"]

        from datetime import datetime

        entry_ms = int(datetime.fromisoformat(ep["entry_time"]).timestamp() * 1000)
        exit_ms = int(datetime.fromisoformat(ep["exit_time"]).timestamp() * 1000)

        bars = _kline_window(as_run["symbol"], as_run["interval"], entry_ms, exit_ms)
        snapshot = _keltner_snapshot(bars, params) if bars else None

        await _ensure_decisions_table(conn)
        user_decisions = await conn.execute_fetchall(
            "SELECT decision, decided_at, note FROM user_decisions "
            "WHERE strategy_id=? AND episode_n=?",
            (strategy_id, episode_n),
        )
        ud = (
            {
                "decision": user_decisions[0][0],
                "decided_at": user_decisions[0][1],
                "note": user_decisions[0][2],
                "provenance": "RECORDED",
            }
            if user_decisions
            else None
        )

        return {
            "episode": ep,
            "as_run": as_run,
            "drift": bundle["drift"],
            "state_at_t": {
                "market_state": {
                    "bars": bars,
                    "keltner": snapshot,
                    "provenance": "RECORDED",
                    "keltner_provenance": "RECONSTRUCTED",
                },
                "strategy_state": {
                    "declared_params": params,
                    "as_run_env": as_run,
                    "provenance": "RECORDED",
                },
                "signal_state": {
                    "rule": snapshot["rule"] if snapshot else None,
                    "note": "当时无逐 bar 信号落盘;此处为规则重算,非当时记录",
                    "provenance": "RECONSTRUCTED",
                },
                "event_state": {"provenance": "NOT_AVAILABLE"},
                "funding": {"provenance": "NOT_AVAILABLE"},
                "open_interest": {"provenance": "NOT_AVAILABLE"},
                "news_at_t": {"provenance": "NOT_AVAILABLE"},
                "memory_state": {
                    "note": "当时记忆系统不存在(冷启动字面为真)",
                    "provenance": "NOT_AVAILABLE",
                },
                "available_evidence": {
                    "kline_window_bars": len(bars),
                    "note": "当时可见 = K 线 + 策略规则;其余全不可见",
                },
                "user_decision": ud,
                "selene_decision": {
                    "note": "当时无实时判断落盘;当时唯一真实动作 = 跑了一个配置漂移的回测",
                    "provenance": "RECONSTRUCTED",
                },
            },
            "outcome": {
                "pnl": ep["pnl"],
                "return": ep["return"],
                "exit_time": ep["exit_time"],
                "exit_price": ep["exit_price"],
                "provenance": "RECORDED",
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("Episode State(T) 恢复", e)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


class UserDecisionBody(BaseModel):
    decision: str = Field(..., description="execute|skip|investigate")
    note: Optional[str] = None


@router.post("/strategy/{strategy_id}/state/{episode_n}/decision")
async def post_episode_decision(
    strategy_id: str, episode_n: int, body: UserDecisionBody
):
    """USER DECISION 落盘(0908 复审:先决定再 Reveal,决定要保存)。

    T 时刻三选一(execute/skip/investigate),Reveal 前后都可落,
    Reveal 后三方对比 You vs Selene vs Reality。
    """
    if body.decision not in ("execute", "skip", "investigate"):
        raise HTTPException(
            status_code=422, detail="decision 须为 execute|skip|investigate"
        )
    conn = await _open_main_db()
    if conn is None:
        raise HTTPException(status_code=503, detail=_STATE_MAIN_DB_HINT)
    try:
        await _ensure_decisions_table(conn)
        from datetime import UTC, datetime

        await conn.execute(
            """
            INSERT INTO user_decisions (strategy_id, episode_n, decided_at, decision, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (strategy_id, episode_n)
            DO UPDATE SET decision=excluded.decision, decided_at=excluded.decided_at, note=excluded.note
            """,
            (
                strategy_id,
                episode_n,
                datetime.now(UTC).isoformat(timespec="seconds"),
                body.decision,
                body.note,
            ),
        )
        await conn.commit()
        return {"saved": True, "decision": body.decision}
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("USER DECISION 落盘", e)
    finally:
        try:
            await conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Thesis scorecard (v019) — 可证伪支柱 + append-only 证据账本
# ---------------------------------------------------------------------------

from selene_space_storage.repositories.mission_thesis import (  # noqa: E402
    ACT_DRIFT_CHECK,
    MissionThesisRepository,
    aggregate_status,
)
from selene_space_storage.repositories._mission_thesis_vocab import (  # noqa: E402
    PILLAR_PRIORITIES,
    PILLAR_STATUSES,
    SIGNALS,
)


class PillarCreate(BaseModel):
    claim: str = Field(..., min_length=1, description="可证伪陈述")
    priority: str = Field("supporting", description="core|supporting")
    baseline: Optional[str] = None
    expected_path: Optional[str] = None
    confirm_threshold: Optional[str] = None
    warning_threshold: Optional[str] = None
    break_threshold: Optional[str] = None
    threshold_provenance: str = "draft"
    provenance: str = "draft"
    model_line: Optional[str] = None
    implied_action: Optional[str] = None
    next_proof_point: Optional[str] = None
    next_proof_source: Optional[str] = None
    owner: str = "selene"


class EvidenceCreate(BaseModel):
    pillar_id: str
    occurred_at: str
    source_type: str
    fact: str = Field(..., min_length=1)
    interpretation: str = Field(..., min_length=1)
    signal: str
    source_ref: Optional[str] = None
    prior_expectation: Optional[str] = None
    market_expectation: Optional[str] = None
    evidence_quality: Optional[str] = None
    impact: Optional[dict[str, Any]] = None
    follow_up: Optional[str] = None
    owner: str = "selene"


class PillarStatusUpdate(BaseModel):
    status: str
    status_note: Optional[str] = None


class DriftCheckPayload(BaseModel):
    patterns: dict[str, dict[str, Any]]
    checked_by: str = "selene"
    note: Optional[str] = None


def _thesis_repo() -> MissionThesisRepository:
    from app.core.dependencies import get_db_manager

    db = get_db_manager()
    if db is None:
        raise HTTPException(status_code=503, detail="数据库未就绪")
    return MissionThesisRepository(db)


@router.get("/missions/{mission_id}/thesis")
async def get_thesis_scorecard(mission_id: str):
    """认识论计分卡:支柱分布 + 聚合裁决 + 信号分布 + 最近一次漂移核查。"""
    try:
        repo = _repo()
        mission = await repo.get(mission_id)
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission 不存在")
        t_repo = _thesis_repo()
        pillars = await t_repo.list_pillars(mission_id)
        verdict = aggregate_status(pillars)
        evidence = await t_repo.list_evidence(mission_id, limit=500)
        signal_distribution: dict[str, int] = {}
        for row in evidence:
            signal_distribution[row["signal"]] = (
                signal_distribution.get(row["signal"], 0) + 1
            )
        active_counts: dict[str, int] = {}
        for p in pillars:
            if p.get("superseded_by") is None and p["status"] != "retired":
                active_counts[p["status"]] = active_counts.get(p["status"], 0) + 1
        if not active_counts:
            aggregate_reason = "尚无活跃支柱 — 论点未检验"
        else:
            aggregate_reason = "活跃支柱状态:" + " / ".join(
                f"{s}×{n}" for s, n in sorted(active_counts.items())
            )
        last_drift = None
        activity = await repo.list_activity(mission_id, limit=200)
        for act in reversed(activity):
            if act["kind"] == ACT_DRIFT_CHECK:
                last_drift = act
                break
        return {
            "mission_id": mission_id,
            "pillars": pillars,
            "aggregate": verdict["aggregate"],
            "aggregate_reason": aggregate_reason,
            "requires_override": verdict["requires_override"],
            "signal_distribution": signal_distribution,
            "evidence_count": len(evidence),
            "evidence": evidence,
            "last_drift_check": last_drift,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("Thesis 计分卡查询", e)


@router.post("/missions/{mission_id}/pillars")
async def post_pillar(mission_id: str, body: PillarCreate):
    """开一根可证伪支柱(claim 一经写入禁改;变更走 supersede)。"""
    if body.priority not in PILLAR_PRIORITIES:
        raise HTTPException(
            status_code=422, detail=f"priority 须为 {PILLAR_PRIORITIES}"
        )
    try:
        repo = _repo()
        if await repo.get(mission_id) is None:
            raise HTTPException(status_code=404, detail="Mission 不存在")
        pillar_id = await _thesis_repo().add_pillar(
            mission_id,
            body.claim,
            body.priority,
            baseline=body.baseline,
            expected_path=body.expected_path,
            confirm_threshold=body.confirm_threshold,
            warning_threshold=body.warning_threshold,
            break_threshold=body.break_threshold,
            threshold_provenance=body.threshold_provenance,
            provenance=body.provenance,
            model_line=body.model_line,
            implied_action=body.implied_action,
            next_proof_point=body.next_proof_point,
            next_proof_source=body.next_proof_source,
            owner=body.owner,
        )
        return {"pillar_id": pillar_id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail="Mission 不存在")
    except Exception as e:
        raise _http_500("支柱创建", e)


@router.post("/missions/{mission_id}/evidence")
async def post_evidence(mission_id: str, body: EvidenceCreate):
    """追加证据行(append-only;快照按 (occurred_at, seq) 对账推进)。"""
    if body.signal not in SIGNALS:
        raise HTTPException(status_code=422, detail=f"signal 须为十档之一: {SIGNALS}")
    try:
        repo = _repo()
        if await repo.get(mission_id) is None:
            raise HTTPException(status_code=404, detail="Mission 不存在")
        evidence_id = await _thesis_repo().append_evidence(
            mission_id,
            body.pillar_id,
            occurred_at=body.occurred_at,
            source_type=body.source_type,
            fact=body.fact,
            interpretation=body.interpretation,
            signal=body.signal,
            source_ref=body.source_ref,
            prior_expectation=body.prior_expectation,
            market_expectation=body.market_expectation,
            evidence_quality=body.evidence_quality,
            impact=body.impact,
            follow_up=body.follow_up,
            owner=body.owner,
        )
        return {"evidence_id": evidence_id}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except KeyError:
        raise HTTPException(status_code=404, detail="支柱不存在")
    except Exception as e:
        raise _http_500("证据追加", e)


@router.post("/missions/{mission_id}/pillars/{pillar_id}/status")
async def post_pillar_status(mission_id: str, pillar_id: str, body: PillarStatusUpdate):
    """支柱状态推进(状态列可写,历史经 activity 保留)。"""
    if body.status not in PILLAR_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 须为 {PILLAR_STATUSES}")
    try:
        repo = _repo()
        if await repo.get(mission_id) is None:
            raise HTTPException(status_code=404, detail="Mission 不存在")
        pillar = await _thesis_repo().get_pillar(pillar_id)
        if pillar is None or pillar["mission_id"] != mission_id:
            raise HTTPException(status_code=404, detail="支柱不存在")
        updated = await _thesis_repo().set_pillar_status(
            pillar_id, body.status, status_note=body.status_note, actor="user"
        )
        return {"pillar": updated}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _http_500("支柱状态更新", e)


@router.post("/missions/{mission_id}/drift-check")
async def post_drift_check(mission_id: str, body: DriftCheckPayload):
    """五漂移模式核查(append-only 活动行,不改写历史)。"""
    try:
        repo = _repo()
        if await repo.get(mission_id) is None:
            raise HTTPException(status_code=404, detail="Mission 不存在")
        await _thesis_repo().record_drift_check(
            mission_id, body.patterns, checked_by=body.checked_by, note=body.note
        )
        # 透明性批3-12: 漂移核查 → 系统事件(FeedBar 可见)
        from app.api.v1.events import record_system_event

        mission = await repo.get(mission_id)
        await record_system_event(
            "drift_check",
            "research",
            f"漂移核查 {mission['title'] if mission else mission_id}: {len(body.patterns)} 项",
            severity="warning" if body.patterns else "info",
            payload={
                "mission_id": mission_id,
                "patterns": body.patterns,
                "checked_by": body.checked_by,
            },
        )
        return {"ok": True}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _http_500("漂移核查", e)


# ---------------------------------------------------------------------------
# 失败归因五分类(v021)— Replay 的结构化产出
#
# 建议即算即显不落库,Director 确认才是唯一落库事件;drift bundle 走硬规则
# 幂等批插。写入面全部走 get_db_manager()(FK per-connection 真实强制),
# 不碰 _open_main_db()(FK OFF,只留给既有只读读面)。
# ---------------------------------------------------------------------------

import aiosqlite  # noqa: E402

from selene_space_storage.repositories.strategy_attributions import (  # noqa: E402
    ATTRIBUTION_SOURCES,
    StrategyAttributionRepository,
)

_ATTRIBUTION_DB_HINT = "主库未就绪"


def _attribution_repo() -> StrategyAttributionRepository:
    from app.core.dependencies import get_db_manager

    db = get_db_manager()
    if db is None:
        raise HTTPException(status_code=503, detail="数据库未就绪")
    return StrategyAttributionRepository(db)


class AttributionCreate(BaseModel):
    category_code: str = Field(..., min_length=1, description="五分类 code")
    suggested_by: str = Field("director", description="llm|rule|director")
    rationale: str = ""
    episode_graph_id: Optional[str] = None


async def _bundle_or_404(strategy_id: str) -> dict[str, Any]:
    """组装 strategy 的最新 drift bundle(不含 episode 定位)。

    bundle 读取走 get_db_manager() 连接(与写入面同源,测试同库可对账;
    裸 _open_main_db 不参与本件)。策略/含成交回测不存在 → 404。
    """
    db = None
    try:
        from app.core.dependencies import get_db_manager

        db = get_db_manager()
    except Exception:
        db = None
    if db is None:
        raise HTTPException(status_code=503, detail=_ATTRIBUTION_DB_HINT)

    strategy = await db.execute(
        "SELECT id FROM strategies WHERE id = ?", (strategy_id,)
    )
    if not strategy:
        raise HTTPException(status_code=404, detail="策略不存在")

    rows = await db.execute(
        """
        SELECT id, symbol, interval, trades_json, metrics_json, created_at
        FROM backtests WHERE strategy_id = ? AND trades_count > 0
        ORDER BY created_at DESC LIMIT 1
        """,
        (strategy_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="该策略无含成交的回测记录")
    bt = dict(rows[0])
    trades = _json.loads(bt["trades_json"]) if bt["trades_json"] else []
    metrics = _json.loads(bt["metrics_json"]) if bt["metrics_json"] else {}
    cfg_rows = await db.execute(
        "SELECT config_json FROM strategies WHERE id = ?", (strategy_id,)
    )
    declared = _json.loads(cfg_rows[0]["config_json"]) if cfg_rows else {}
    declared_symbol = (declared.get("symbols") or [None])[0]
    declared_interval = declared.get("interval")
    drifted = declared_symbol is not None and (
        bt["symbol"] != declared_symbol or bt["interval"] != declared_interval
    )
    episodes = [
        {
            "n": i,
            "backtest_id": bt["id"],
            "symbol": t.get("symbol") or bt["symbol"],
            "side": t.get("side"),
            "entry_time": _ms_to_iso(t["entry_time"]),
            "exit_time": _ms_to_iso(t["exit_time"]),
            "entry_price": t.get("entry_price") or 0.0,
            "exit_price": t.get("exit_price"),
            "pnl": t.get("pnl") or 0.0,
            "quantity": t.get("quantity") or 0.0,
        }
        for i, t in enumerate(trades, 1)
    ]
    return {
        "episodes": episodes,
        "backtest_id": bt["id"],
        "as_run": {"symbol": bt["symbol"], "interval": bt["interval"]},
        "declared": {"symbol": declared_symbol, "interval": declared_interval},
        "drift": drifted,
        "metrics": metrics,
        "created_at": bt["created_at"],
    }


async def _episode_payload_or_404(
    strategy_id: str, episode_n: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """bundle 定位单个 episode(episode 不存在 → 404)。"""
    bundle = await _bundle_or_404(strategy_id)
    matches = [e for e in bundle["episodes"] if e["n"] == episode_n]
    if not matches:
        raise HTTPException(status_code=404, detail=f"Episode {episode_n} 不存在")
    return matches[0], bundle


@router.get("/attribution/categories")
async def get_attribution_categories():
    """五分类字典全行(UI 下拉 + LLM prompt 共用单一事实源)。"""
    try:
        return {"categories": await _attribution_repo().list_categories()}
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("归因字典查询", e)


@router.get("/attribution/stats")
async def get_attribution_stats():
    """分档统计 {category: count} + episodes 明细(可逐条对账)。"""
    try:
        stats = await _attribution_repo().stats()
        return {"counts": stats["counts"], "episodes": stats["episodes"]}
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("归因统计查询", e)


@router.get("/strategy/{strategy_id}/state/{episode_n}/attribution/suggest")
async def get_attribution_suggest(strategy_id: str, episode_n: int):
    """LLM 失败归因建议 — 即算即显无写(建议态不落库,§2.4)。

    单次 completion(无工具循环);LLM 不可用/失败 → suggestion=null
    (前端退化手选 suggested_by='director'),不抛 500。
    """
    episode, bundle = await _episode_payload_or_404(strategy_id, episode_n)
    categories = await _attribution_repo().list_categories()
    runtime = None
    try:
        from app.core.dependencies import get_agent_runtime

        runtime = get_agent_runtime()
    except Exception:
        runtime = None
    provider = getattr(runtime, "_llm", None) if runtime is not None else None
    if provider is None:
        return {"suggestion": None, "reason": "llm_unavailable"}

    vocab_lines = "\n".join(f"- {c['code']}: {c['content']}" for c in categories)
    prompt = (
        "你是量化策略失败归因分析员。以下是一笔回测亏损 episode 与其上下文,"
        "请从五分类封闭集中选一个最合理的失败原因。\n\n"
        f"五分类字典:\n{vocab_lines}\n\n"
        f"Episode 上下文(JSON):\n{_json.dumps(episode, ensure_ascii=False, default=str)}\n"
        f"声明 vs 实跑漂移(drift): {bundle.get('drift')}\n"
        f"回测 metrics: {_json.dumps(bundle.get('metrics') or {}, ensure_ascii=False, default=str)}\n\n"
        '只返回 JSON 对象:{"category_code": "<五分类之一>", '
        '"rationale": "<一句话依据>", "confidence": <0到1的小数>}'
    )
    schema = {
        "type": "object",
        "properties": {
            "category_code": {"type": "string"},
            "rationale": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["category_code", "rationale"],
    }
    try:
        raw = await provider.generate_structured(
            [{"role": "user", "content": prompt}], schema
        )
    except Exception:
        logger.exception("attribution suggest LLM call failed")
        return {"suggestion": None, "reason": "llm_error"}

    code = str(raw.get("category_code") or "")
    if code not in {c["code"] for c in categories}:
        return {"suggestion": None, "reason": "llm_out_of_vocab"}
    confidence = raw.get("confidence")
    return {
        "suggestion": {
            "category_code": code,
            "rationale": str(raw.get("rationale") or ""),
            "confidence": float(confidence) if confidence is not None else None,
        }
    }


@router.get("/strategy/{strategy_id}/state/{episode_n}/attribution")
async def get_attribution(strategy_id: str, episode_n: int):
    """读单条归因行(replay"学到"行二态判别:有行=徽章态,404=待归因)。"""
    try:
        row = await _attribution_repo().for_episode(strategy_id, episode_n)
        if row is None:
            raise HTTPException(status_code=404, detail="该 episode 尚无归因行")
        return {"attribution": row}
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("归因查询", e)


@router.post("/strategy/{strategy_id}/state/{episode_n}/attribution")
async def post_attribution(strategy_id: str, episode_n: int, body: AttributionCreate):
    """Director 确认直插(落库即确认;LLM 建议确认与手选都走此端点)。"""
    if body.suggested_by not in ATTRIBUTION_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"suggested_by 须为 {list(ATTRIBUTION_SOURCES)}",
        )
    try:
        await _episode_payload_or_404(strategy_id, episode_n)
        row = await _attribution_repo().upsert(
            strategy_id,
            episode_n,
            body.category_code,
            body.suggested_by,
            rationale=body.rationale,
            episode_graph_id=body.episode_graph_id,
        )
        return {"attribution": row}
    except HTTPException:
        raise
    except aiosqlite.IntegrityError as e:
        if "UNIQUE" in str(e):
            raise HTTPException(
                status_code=409,
                detail=f"already attributed: {strategy_id} episode {episode_n}(改判走 PUT)",
            )
        raise HTTPException(status_code=400, detail=f"归因校验失败: {e}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _http_500("归因落库", e)


@router.put("/strategy/{strategy_id}/state/{episode_n}/attribution")
async def put_attribution(strategy_id: str, episode_n: int, body: AttributionCreate):
    """改判 UPDATE(同路径覆盖;created_at 不动)。"""
    if body.suggested_by not in ATTRIBUTION_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"suggested_by 须为 {list(ATTRIBUTION_SOURCES)}",
        )
    try:
        existing = await _attribution_repo().for_episode(strategy_id, episode_n)
        if existing is None:
            raise HTTPException(status_code=404, detail="该 episode 尚无归因行")
        await _episode_payload_or_404(strategy_id, episode_n)
        row = await _attribution_repo().upsert(
            strategy_id,
            episode_n,
            body.category_code,
            body.suggested_by,
            rationale=body.rationale,
            episode_graph_id=body.episode_graph_id,
            overwrite=True,
        )
        return {"attribution": row}
    except HTTPException:
        raise
    except aiosqlite.IntegrityError as e:
        raise HTTPException(status_code=400, detail=f"归因校验失败: {e}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise _http_500("归因改判", e)


@router.post("/strategy/{strategy_id}/state/attribution/rules")
async def post_attribution_rules(strategy_id: str, body: Optional[dict] = None):
    """drift bundle 硬规则幂等批插(suggested_by='rule', category='execution')。

    对该 strategy 的 drift bundle 全部亏损 episode 批插;已存在行跳过
    (幂等)。请求体可带 {"episode_ns": [...]} 覆盖,缺省服务端自算。
    """
    try:
        bundle = await _bundle_or_404(strategy_id)
        if not bundle.get("drift"):
            raise HTTPException(
                status_code=422, detail="仅 drift=true 的 bundle 可走 rule 批插"
            )
        losses = [e["n"] for e in bundle["episodes"] if (e.get("pnl") or 0.0) < 0]
        if not losses:
            return {"inserted": [], "rule_episode_ns": []}
        override = (body or {}).get("episode_ns")
        episode_ns = [int(n) for n in override] if override else losses
        inserted = await _attribution_repo().rule_batch(
            strategy_id,
            episode_ns,
            rationale="drift=true 硬规则:配置漂移污染,不可归因于策略逻辑",
        )
        return {"inserted": inserted, "rule_episode_ns": episode_ns}
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("rule 批插", e)
