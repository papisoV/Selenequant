"""Research Graph 路由 —— 研究图谱的读面 + 写入面。

与 research_missions.py 的分工:Mission 路由管"这一次调查走到哪了";
本路由管"我们在研究什么、知道什么、判断过什么"。两者通过研究图谱的边
相连,不互相拥有。

错误处理约定: 完整异常只进服务端日志,HTTP 响应返回通用文案;
但领域错误(ValueError / KeyError)映射为 400/404 —— 它们是**调用方的错**,
调用方需要知道具体哪条引用不合法,否则无法修正。

响应约定: 成功路径统一包 APIResponse 信封 —— 同一 /api/v1/research/ 前缀下
research.py 是信封、research_missions.py 是裸 dict,这个不一致是既存的;
本路由不复制它(前端 apiPost 无条件解包 wrapped.data,裸 dict 会静默变 null)。
错误路径(HTTPException 400/404)不进信封 —— FastAPI 默认 handler 返回裸 {"detail": ...}。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.dependencies import build_response_meta, get_settings_service
from app.services.investigate_council import run_council_background
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research/graph", tags=["research-graph"])

# P3:investigate 后台任务注册表(进程内,qid → Task)。重启即失——
# 可接受的简化:任务本身落 events,重启后前端看 investigate_finished 缺席
# + investigate_running=False 也能收敛到诚实状态。
_INVESTIGATE_TASKS: dict[str, "asyncio.Task[None]"] = {}

# local-api 进程内可导入 storage 包(不依赖 pip install -e)
sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "packages" / "storage"))

from selene_space_storage.repositories.research_graph import (  # noqa: E402
    LEARNED_EXPERIENCE,
    RESEARCH_RECORD,
    EpisodeRepository,
    EvidenceRepository,
    ResearchEdgeRepository,
    ResearchQuestionRepository,
    parse_as_of,
    check_graph_integrity,
    collect_changes,
    list_relations,
    recall,
)
from selene_space_storage.repositories.research_graph.derivation import (  # noqa: E402
    derive_subject_lifecycle,
)
from selene_space_storage.repositories.research_graph._shared import (  # noqa: E402
    _load_json,
)
from selene_space_storage.repositories.research_graph._promote_draft import (  # noqa: E402
    build_promote_draft,
    normalize_claim,
)
from selene_space_storage.repositories.research_graph.recall import (  # noqa: E402
    _SUPERSEDED_HYPOTHESIS_STATUSES,
    _evidence_chain,
)

# -- 引用账本的两个共用常量(焊点与 /recall 端点必须看同一个窗口) -----------

LEDGER_KINDS = ("revision", "hypothesis", "evidence")

# LEDGER_LIMIT 把取回窗口显式化;它不修 recall 的排序缺陷 ——
# 排序键 (-score, created_at, id) 在 score 打平时按 created_at 升序截断,
# 截掉的是最新的那批(与"次日引用昨天的取回"反相关)。
# 修排序属后续波次;此处只保证焊点与 /recall 看到同一个窗口。
LEDGER_LIMIT = 200

# mission 的主体类型词表(与 ResearchMissionRepository.create 的校验逐字一致;
# 这里再列一份是为了在 API 层给出 422 而不是让它冒到 repo 变成 500)。
_SUBJECT_TYPES = ("strategy", "market", "event", "system")


class QuestionCreate(BaseModel):
    title: str
    body: str = ""
    anchor: Optional[dict[str, Any]] = None


class PromotePillarFields(BaseModel):
    """草稿里的 pillar 字段(用户在对话框改完回传)。

    ``model_line`` 刻意不收 —— 服务端强制写 ``council v{seq} {rev_id}``
    (计划 Phase 2 必修③:它是采用度量的锚,客户端可改写会让账本失真)。
    """

    claim: str = Field(..., min_length=1, description="可证伪陈述")
    priority: str = "core"
    baseline: Optional[str] = None
    expected_path: Optional[str] = None
    confirm_threshold: Optional[str] = None
    warning_threshold: Optional[str] = None
    break_threshold: Optional[str] = None
    threshold_provenance: str = "draft"
    provenance: str = "draft"
    implied_action: Optional[str] = None
    next_proof_point: Optional[str] = None
    next_proof_source: Optional[str] = None
    owner: str = "selene"


class PromoteMissionFields(BaseModel):
    title: str = Field(..., min_length=1)
    question: str = ""
    subject_type: str = Field(..., description="strategy|market|event|system")
    subject_id: str = Field(..., min_length=1)


class PromoteRequest(BaseModel):
    """promote 的提交体 —— 用户在对话框改完的草稿。"""

    mission: PromoteMissionFields
    pillar: PromotePillarFields
    revision_seq: int = Field(..., ge=1, description="提升哪一版 revision")


class RevisionCreate(BaseModel):
    content: str
    rationale: list[str] = Field(default_factory=list)
    cited: list[str] = Field(default_factory=list)
    horizon: Literal["structural", "tactical"] = "structural"


class CloseRequest(BaseModel):
    terminal_status: str = Field(
        ..., description="sufficiently_answered|falsified|parked|superseded"
    )
    note: Optional[str] = None


class ReopenRequest(BaseModel):
    reason: str


class SucceedRequest(BaseModel):
    title: str
    body: str = ""


class EvidenceCreate(BaseModel):
    episode_id: str
    mode: str = Field(..., description="RECORDED|RECONSTRUCTED|AFTER_THE_FACT")
    payload: Optional[dict[str, Any]] = None
    source_ref: str = ""


class RecallRequest(BaseModel):
    """账本召回请求。

    W2 收窄:不收 anchor / subject —— 二者在本波次都没有生产者,
    且显式传入 anchor 会覆盖 question_id(recall.py L193-195)而非求交,
    一旦客户端带上它,焊点算出的集合就与前端拿到的候选不是同一个集合。

    ``as_of``(账本回放)是**查看透镜**,不是写入参数:它只把召回集缩到
    "该时刻账本已知道的东西"。cited 焊点校验(``_reject_cited_outside_ledger``)
    刻意**不传** as_of —— 校验针对"现在"的完整候选集,而回放可见集是其子集,
    故勾选必然通过。详见 recall() docstring 的两条边界声明。
    """

    question_id: Optional[str] = None
    kinds: list[str] = Field(default_factory=lambda: list(LEDGER_KINDS))
    require_class: Optional[str] = None
    limit: int = Field(default=LEDGER_LIMIT, le=LEDGER_LIMIT)
    as_of: Optional[str] = None


class LifecycleTransitionRequest(BaseModel):
    to_status: str
    reason: Optional[str] = None


class InvestigateRequest(BaseModel):
    """调查会请求。

    ``as_of``(council 回放调查,N1 第二刀)是**回放调查模式**,不是查看
    透镜(与 RecallRequest 的措辞刻意不同):它收窄调查会全部读面(账本
    召回/本问题回答链/行情材料)到该认知边界,而新产物(revision/events/
    新问题)以**当前时刻**落库并带 as_of provenance —— 是真实新写入。
    """

    as_of: Optional[str] = None


def get_graph_db() -> Any:
    """FastAPI dependency —— 懒取 app 共享的 AsyncSQLiteManager。

    做成 Depends 而不是模块内私有函数,是为了能被测试用
    `app.dependency_overrides` 换成内存库(与 test_api_account_state.py 同法),
    不必 monkeypatch 模块属性,也不必碰真实 selenespace.db。
    """
    from app.core.dependencies import get_db_manager

    db = get_db_manager()
    if db is None:
        raise HTTPException(status_code=503, detail="数据库未就绪")
    return db


def _http_500(context: str, exc: Exception) -> HTTPException:
    logger.exception("research graph api error: %s", context)
    return HTTPException(status_code=500, detail=f"{context}失败,请查看服务端日志")


async def _known_strategy_ids(db: Any) -> set[str] | None:
    """已注册策略 id 集 —— 供 promote 草稿判定 anchor 主体是否真策略。

    ``None``(表不可读)= 无登记表可查 → ``suggest_subject`` 退回纯建议行为;
    空集 ``set()`` 是有意义的真值(库里一条策略都没有),两者不可混为一谈。
    """
    try:
        rows = await db.execute("SELECT id FROM strategies")
    except Exception:  # noqa: BLE001 — 无法校验 > 谎报已校验
        return None
    return {str(r["id"]) for r in rows}


def _domain_error(exc: Exception) -> HTTPException:
    """领域错误是调用方的错 —— 原样告诉它哪条引用不合法,不然没法修。"""
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'\""))
    return HTTPException(status_code=400, detail=str(exc))


async def _reject_cited_outside_ledger(
    db: Any, question_id: str, cited: list[str]
) -> None:
    """cited 焊点(W2,roadmap 已知残余 1 的收口):cited 必须来自 recall 账本。

    契约收窄只发生在 HTTP 写入路径 —— 仓储层 _validate_citations 不动,
    直调仓储仍按"存在 + 连边"判定(层间差异,测试须同时断言两侧)。
    在空 anchor 的 W2 数据制度下它与"本问题全部 revision"外延重合;
    anchor 生产者出现的那一刻它变成真约束,断言不用改一个字。
    """
    if not cited:
        return
    candidates = await recall(
        db,
        question_id=question_id,
        kinds=LEDGER_KINDS,
        require_class=None,
        limit=LEDGER_LIMIT,
    )
    allowed = {c["id"] for c in candidates}
    outside = [cid for cid in dict.fromkeys(cited) if cid not in allowed]
    if outside:
        raise HTTPException(
            status_code=400,
            detail=f"cited not from recall ledger: {', '.join(outside)}",
        )


@router.get("/questions")
async def list_questions(
    request: Request,
    status: Optional[str] = None,
    parent: Optional[str] = None,
    limit: int = 50,
    db: Any = Depends(get_graph_db),
):
    try:
        repo = ResearchQuestionRepository(db)
        questions = await repo.list_questions(
            status=status, parent=parent, limit=max(1, min(limit, 200))
        )
        return APIResponse(
            data={"questions": questions}, meta=build_response_meta(request)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("问题列表查询", e)


@router.post("/questions")
async def create_question(
    payload: QuestionCreate, request: Request, db: Any = Depends(get_graph_db)
):
    try:
        repo = ResearchQuestionRepository(db)
        question_id = await repo.create(
            title=payload.title, body=payload.body, anchor=payload.anchor
        )
        return APIResponse(
            data={"question_id": question_id}, meta=build_response_meta(request)
        )
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("问题创建", e)


@router.get("/questions/{question_id}")
async def get_question(
    question_id: str, request: Request, db: Any = Depends(get_graph_db)
):
    try:
        questions = ResearchQuestionRepository(db)
        question = await questions.get(question_id)
        if question is None:
            raise HTTPException(
                status_code=404, detail=f"question not found: {question_id}"
            )
        edges = await ResearchEdgeRepository(db).list_edges(question_id)
        running = False
        t = _INVESTIGATE_TASKS.get(question_id)
        if t is not None and not t.done():
            running = True
        # cited 引用对象(id + evidence mode + episode_id):前端凭 mode 渲染
        # 「探针」徽章、凭 episode_id 打开 episode 抽屉。一次 IN 批量查,不逐 id 往返。
        revisions = await questions.list_revisions(question_id)
        cited_ids = sorted(
            {
                cid
                for rev in revisions
                for cid in (rev.get("cited") or [])
                if isinstance(cid, str) and cid.startswith("ev-")
            }
        )
        cited_refs: list[dict[str, Any]] = []
        if cited_ids:
            placeholders = ",".join("?" for _ in cited_ids)
            mode_rows = await db.execute(
                "SELECT id, mode, episode_id FROM research_evidence "
                f"WHERE id IN ({placeholders})",
                tuple(cited_ids),
            )
            found = {r["id"]: r for r in mode_rows}
            cited_refs = [
                {
                    "id": cid,
                    "mode": found[cid]["mode"] if cid in found else None,
                    "episode_id": found[cid]["episode_id"] if cid in found else None,
                }
                for cid in cited_ids
            ]
        return APIResponse(
            data={
                "question": question,
                "revisions": revisions,
                "events": await questions.list_events(question_id),
                "edges": edges,
                "investigate_running": running,
                "cited_refs": cited_refs,
            },
            meta=build_response_meta(request),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("问题详情查询", e)


@router.get("/changes")
async def get_changes(
    request: Request,
    since: Optional[str] = None,
    limit: int = 200,
    db: Any = Depends(get_graph_db),
):
    """跨问题变化聚合 —— 主窗口 Control Surface 数据源。

    tier1(decision pending / 观察窗到期 / 系统提议)是持续状态,常驻不受
    since 过滤;tier2/3 是瞬态流。since 合法性在这里挡(ISO 格式错 → 400)。
    """
    from datetime import datetime

    if since is not None:
        try:
            datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"since must be an ISO-8601 timestamp: {since!r}",
            )
    try:
        result = await collect_changes(db, since=since, limit=max(1, min(limit, 500)))
        from selene_space_storage.repositories.research_graph._shared import _now

        return APIResponse(
            data={**result, "server_now": _now()}, meta=build_response_meta(request)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("变化聚合查询", e)


@router.post("/questions/{question_id}/revisions")
async def add_revision(
    question_id: str,
    payload: RevisionCreate,
    request: Request,
    db: Any = Depends(get_graph_db),
):
    # 焊点在仓储调用之前(它自己抛 HTTPException,放 try 外避免被 except Exception 吞成 500)
    await _reject_cited_outside_ledger(db, question_id, payload.cited)
    try:
        repo = ResearchQuestionRepository(db)
        revision = await repo.add_revision(
            question_id,
            payload.content,
            actor="selene",
            rationale=payload.rationale,
            cited=payload.cited,
            horizon=payload.horizon,
        )
        return APIResponse(
            data={
                "revision": revision,
                "citation_migration": await repo.citation_migration(question_id),
            },
            meta=build_response_meta(request),
        )
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("回答追加", e)


@router.post("/questions/{question_id}/investigate")
async def investigate_question(
    question_id: str,
    request: Request,
    payload: Optional[InvestigateRequest] = None,
    db: Any = Depends(get_graph_db),
    settings_service: Any = Depends(get_settings_service),
):
    """多视角调查会(A1):3 视角独立召回+分析 → 主持人综合写下一版回答。

    P3 轮询流:端点只做校验+防重+后台起任务,立即 202 返回。进度经
    GET detail 轮询——investigate_perspective 事件逐个到达(视角流),
    investigate_finished 事件标记本轮结束。前端不再被 2-5 分钟长请求
    掐死(30s 代理超时的根治)。

    引用纪律(A2)在每个视角与主持人处独立执行;cited ⊆ 各自候选集。
    视角产物落 events(不进回答链),仅主持人落 revision;主持人提出的
    新问题落 research_questions 待用户拍板,不自动 investigate/close。

    ``payload.as_of``(回放调查)在 create_task **之前**同步校验:非法值
    必须 400 同步返回,不能被 202 后台吞。校验先于 question 检查(纯函数
    无 IO,顺序钉子:坏 as_of + 不存在的 qid → 400 而非 404)。
    细节见 app/services/investigate_council.py。
    """
    try:
        as_of = payload.as_of if payload else None
        if as_of is not None:
            parse_as_of(as_of)  # 非法 → ValueError → 400(下方显式分支)
        repo = ResearchQuestionRepository(db)
        question = await repo.get(question_id)
        if question is None:
            raise HTTPException(
                status_code=404, detail=f"question not found: {question_id}"
            )
        if question["status"] != "open":
            raise HTTPException(
                status_code=400,
                detail=f"question is closed ({question['status']}), "
                "reopen it before investigating",
            )
        existing = _INVESTIGATE_TASKS.get(question_id)
        if existing is not None and not existing.done():
            raise HTTPException(status_code=409, detail="该问题的调查已在进行中")
        task = asyncio.create_task(
            run_council_background(db, settings_service, question_id, as_of=as_of)
        )
        _INVESTIGATE_TASKS[question_id] = task
        task.add_done_callback(lambda _t: _INVESTIGATE_TASKS.pop(question_id, None))
        return JSONResponse(
            status_code=202,
            content={
                "data": {"question_id": question_id, "status": "investigating"},
                "meta": build_response_meta(request).model_dump(),
                "error": None,
            },
        )
    except ValueError as e:
        # 显式分支:Critic 钉子 —— 勿落进下方 except Exception → 500 吞没范围
        raise _domain_error(e)
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("自动调查", e)


@router.post("/questions/{question_id}/close")
async def close_question(
    question_id: str,
    payload: CloseRequest,
    request: Request,
    db: Any = Depends(get_graph_db),
):
    try:
        repo = ResearchQuestionRepository(db)
        question = await repo.close(
            question_id,
            payload.terminal_status,
            actor="user",
            note=payload.note,
        )
        return APIResponse(
            data={"question": question}, meta=build_response_meta(request)
        )
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("问题结束", e)


@router.post("/questions/{question_id}/reopen")
async def reopen_question(
    question_id: str,
    payload: ReopenRequest,
    request: Request,
    db: Any = Depends(get_graph_db),
):
    try:
        repo = ResearchQuestionRepository(db)
        question = await repo.reopen(question_id, actor="user", reason=payload.reason)
        return APIResponse(
            data={"question": question}, meta=build_response_meta(request)
        )
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("问题复访", e)


@router.post("/questions/{question_id}/succeed")
async def succeed_question(
    question_id: str,
    payload: SucceedRequest,
    request: Request,
    db: Any = Depends(get_graph_db),
):
    """换个问法:新问题接续本问题,原问题进 superseded(拍板闭环)。"""
    try:
        repo = ResearchQuestionRepository(db)
        successor_id = await repo.succeed(
            question_id, payload.title, payload.body, actor="user"
        )
        successor = await repo.get(successor_id)
        return APIResponse(
            data={"question_id": successor_id, "question": successor},
            meta=build_response_meta(request),
        )
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("问题接续", e)


@router.get("/episodes")
async def list_episodes(
    request: Request,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    limit: int = 50,
    db: Any = Depends(get_graph_db),
):
    try:
        repo = EpisodeRepository(db)
        episodes = await repo.list_episodes(
            subject_type=subject_type,
            subject_id=subject_id,
            limit=max(1, min(limit, 200)),
        )
        return APIResponse(
            data={"episodes": episodes}, meta=build_response_meta(request)
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("Episode 列表查询", e)


@router.get("/episodes/{episode_id}")
async def get_episode(
    episode_id: str, request: Request, db: Any = Depends(get_graph_db)
):
    try:
        repo = EpisodeRepository(db)
        episode = await repo.get(episode_id)
        if episode is None:
            raise HTTPException(
                status_code=404, detail=f"episode not found: {episode_id}"
            )
        edges = await ResearchEdgeRepository(db).list_edges(
            episode_id, kind="derived_from"
        )
        return APIResponse(
            data={
                "episode": episode,
                "evidence": await repo.list_evidence(episode_id),
                "edges": edges,
            },
            meta=build_response_meta(request),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("Episode 详情查询", e)


@router.post("/episodes/{episode_id}/lifecycle")
async def transition_episode_lifecycle(
    episode_id: str,
    payload: LifecycleTransitionRequest,
    request: Request,
    db: Any = Depends(get_graph_db),
):
    """episode 观察周期流转(状态轴)—— 抽屉「推进/退役」按钮的数据通道。

    校验全部在 storage 层(objects.record_lifecycle_transition:白名单流转、
    RETIRED 必填 reason、无 lifecycle 子键拒绝),端点只做错误映射:
    ValueError→400(非法流转/缺 reason)、KeyError→404(episode 不存在
    或无 lifecycle 子键)。actor 恒为 user:HTTP 写路径与 close/reopen 同族。
    """
    try:
        episode = await EpisodeRepository(db).record_lifecycle_transition(
            episode_id,
            payload.to_status,
            actor="user",
            reason=payload.reason,
        )
        return APIResponse(data={"episode": episode}, meta=build_response_meta(request))
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("Episode 生命周期流转", e)


@router.get("/subjects/{subject_type}/{subject_id}/derivation")
async def get_subject_derivation(
    subject_type: str,
    subject_id: str,
    request: Request,
    db: Any = Depends(get_graph_db),
):
    """因子级生命周期推导(读侧消费口)—— 链上证据 → 行动建议。

    derivation.derive_subject_lifecycle 纯读:输出链时间线 + 最新状态/verdict
    + 行动建议(retire/inspect,每条带 basis_episode_id + reason)。建议 ≠ 流转
    —— 流转走上面 /episodes/{id}/lifecycle 受控窄门,转不转人拍板。
    subject_id 含 ":"(formula:{hash}),放路径里 FastAPI 单段参数天然支持。
    """
    try:
        derivation = await derive_subject_lifecycle(
            db, subject_type=subject_type, subject_id=subject_id
        )
        return APIResponse(data=derivation, meta=build_response_meta(request))
    except Exception as e:
        raise _http_500("Subject 生命周期推导", e)


@router.post("/evidence")
async def create_evidence(
    payload: EvidenceCreate, request: Request, db: Any = Depends(get_graph_db)
):
    try:
        repo = EvidenceRepository(db)
        evidence_id = await repo.create(
            episode_id=payload.episode_id,
            mode=payload.mode,
            payload=payload.payload,
            source_ref=payload.source_ref,
        )
        return APIResponse(
            data={"evidence_id": evidence_id}, meta=build_response_meta(request)
        )
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("Evidence 记录", e)


@router.post("/recall")
async def recall_ledger(
    payload: RecallRequest, request: Request, db: Any = Depends(get_graph_db)
):
    try:
        candidates = await recall(
            db,
            question_id=payload.question_id,
            kinds=payload.kinds,
            require_class=payload.require_class,
            limit=payload.limit,
            as_of=payload.as_of,
        )
    except ValueError as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("账本召回", e)
    # learned/records 是"返回页的构成",不是全库构成 —— recall 返回前已 [:limit] 截断
    learned = sum(1 for c in candidates if c["recall_class"] == LEARNED_EXPERIENCE)
    records = sum(1 for c in candidates if c["recall_class"] == RESEARCH_RECORD)
    return APIResponse(
        data={"candidates": candidates, "learned": learned, "records": records},
        meta=build_response_meta(request),
    )


@router.get("/integrity")
async def graph_integrity(request: Request, db: Any = Depends(get_graph_db)):
    try:
        report = await check_graph_integrity(db)
        return APIResponse(data=report, meta=build_response_meta(request))
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("图谱完整性审计", e)


@router.get("/view")
async def graph_view(
    request: Request,
    limit: int = 500,
    db: Any = Depends(get_graph_db),
):
    """全图只读投影(P2):research_* 表 → echarts {nodes, links}。

    只读、无业务语义 —— P2 是"让历史改变判断"看得见;节点文本截断到
    60 字符(图上不读长文,读文去 Question 面)。
    """
    try:
        limit = max(1, min(limit, 2000))

        question_rows = await db.execute(
            "SELECT id, title, status, opened_at, created_by, parent_question_id "
            "FROM research_questions "
            "ORDER BY opened_at DESC LIMIT ?",
            (limit,),
        )
        revision_rows = await db.execute(
            "SELECT id, question_id, seq, content, actor, created_at "
            "FROM research_revisions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        hypothesis_rows = await db.execute(
            "SELECT id, statement, status, created_at FROM research_hypotheses "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        episode_rows = await db.execute(
            "SELECT id, subject_type, subject_id, title, occurred_at "
            "FROM research_episodes ORDER BY occurred_at DESC LIMIT ?",
            (limit,),
        )
        # mission 节点:桥(question→pillar)的可见性承重件。没有它,addresses
        # 边会被下面的 valid_ids 过滤静默丢弃 = 桥在图谱上不可见(计划 Phase 3)。
        mission_rows = await db.execute(
            "SELECT id, title, subject_type, subject_id, phase, opened_at "
            "FROM research_missions ORDER BY opened_at DESC LIMIT ?",
            (limit,),
        )
        # 关系读取走 list_relations(边表 ∪ 列派生)—— 只读 edge_rows 会让
        # 30 条列关系(revision.question_id / evidence.episode_id)对图谱不可见,
        # 前端只好自己合成一遍。可见性过滤仍在下面按 kind/端类型施加。
        relation_rows = await list_relations(db)
        filtered_relations = [
            r
            for r in relation_rows
            if r["to_type"] == "question"
            or r["from_type"] == "question"
            or r["kind"] == "derived_from"
        ]

        def _short(text: Any) -> str:
            s = str(text or "").replace("\n", " ").strip()
            return s[:60] + ("…" if len(s) > 60 else "")

        nodes: list[dict[str, Any]] = []
        seen: set[str] = set()

        def _add(node_id: str, category: str, name: str, extra: dict[str, Any]) -> None:
            if node_id in seen:
                return
            seen.add(node_id)
            nodes.append({"id": node_id, "category": category, "name": name, **extra})

        for row in question_rows:
            _add(
                row["id"],
                "question",
                _short(row["title"]),
                {
                    "status": row["status"],
                    "created_at": row["opened_at"],
                    "created_by": row["created_by"],
                    "parent_question_id": row["parent_question_id"],
                },
            )
        for row in revision_rows:
            _add(
                row["id"],
                "revision",
                f"v{row['seq']} {_short(row['content'])}",
                {
                    "question_id": row["question_id"],
                    "actor": row["actor"],
                    "created_at": row["created_at"],
                },
            )
        for row in hypothesis_rows:
            _add(
                row["id"],
                "hypothesis",
                _short(row["statement"]),
                {"status": row["status"], "created_at": row["created_at"]},
            )
        for row in episode_rows:
            _add(
                row["id"],
                "episode",
                _short(row["title"]),
                {
                    "subject_type": row["subject_type"],
                    "subject_id": row["subject_id"],
                    "created_at": row["occurred_at"],
                },
            )
        for row in mission_rows:
            _add(
                row["id"],
                "mission",
                _short(row["title"]),
                {
                    "subject_type": row["subject_type"],
                    "subject_id": row["subject_id"],
                    "phase": row["phase"],
                    "created_at": row["opened_at"],
                },
            )

        valid_ids = seen
        links = [
            {
                "id": row["id"],
                "source": row["from_id"],
                "target": row["to_id"],
                "kind": row["kind"],
                "derived": row["derived"],
            }
            for row in filtered_relations[:limit]
            if row["from_id"] in valid_ids and row["to_id"] in valid_ids
        ]

        return APIResponse(
            data={"nodes": nodes, "links": links},
            meta=build_response_meta(request),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("图谱视图", e)


@router.get("/memory")
async def memory_surface(request: Request, db: Any = Depends(get_graph_db)):
    """MEMORY 正典页的数据投影 —— "组织学到的,不是组织存过的"。

    分层判据复用 recall 的机械版(§6.10⑤):证据链走到 Episode / Evidence
    的 = 既有知识(knowledge);走不到的 = 待检验线索(clues)。
    行为后果(§6.10①"已改变 N 次回答")由 cited_json 反向索引算出 ——
    revision 引用了谁,谁的 used_by 里就有这条 revision。
    只读投影,零业务语义;条目池 = hypotheses + revisions 全表(v0 单用户
    库,量级几百条,ponytail: 全表扫,上万条再建索引)。
    """
    try:
        hyp_rows = await db.execute(
            "SELECT id, statement, status, created_at FROM research_hypotheses "
            "ORDER BY created_at DESC LIMIT 500"
        )
        rev_rows = await db.execute(
            "SELECT id, question_id, content, seq, cited_json, actor, created_at "
            "FROM research_revisions ORDER BY created_at DESC LIMIT 500"
        )

        # 行为后果反向索引:一遍 revisions,统计每个被引用对象的引用时间线
        used_by_map: dict[str, list[dict[str, Any]]] = {}
        revision_meta: dict[str, dict[str, Any]] = {}
        for row in rev_rows:
            revision_meta[row["id"]] = {
                "revision_id": row["id"],
                "seq": row["seq"],
                "question_id": row["question_id"],
                "content": row["content"],
                "actor": row["actor"],
                "created_at": row["created_at"],
            }
            for cited_id in _load_json(row["cited_json"]) or []:
                used_by_map.setdefault(cited_id, []).append(revision_meta[row["id"]])

        episode_repo = EpisodeRepository(db)

        async def _entry(kind: str, item: dict[str, Any]) -> dict[str, Any]:
            node_id = item["id"]
            chain_ids = await _evidence_chain(db, kind, node_id)
            chain: list[dict[str, Any]] = []
            for hop in chain_ids[1:]:  # 第一个元素是自身,不内联
                hop_kind, hop_id = hop.split(":", 1)
                if hop_kind == "episode":
                    row = await episode_repo.get(hop_id)
                    if row is not None:
                        chain.append({"kind": "episode", **row})
                elif hop_kind == "evidence":
                    row = await EvidenceRepository(db).get(hop_id)
                    if row is not None:
                        chain.append({"kind": "evidence", **row})
            used_by = used_by_map.get(node_id, [])
            used_at = [r["created_at"] for r in used_by]
            entry: dict[str, Any] = {
                "kind": kind,
                "id": node_id,
                "text": item.get("statement") or item.get("content"),
                "status": item["status"] if kind == "hypothesis" else None,
                "falsified": (
                    kind == "hypothesis"
                    and item["status"] in _SUPERSEDED_HYPOTHESIS_STATUSES
                ),
                "created_at": item["created_at"],
                "question_id": item.get("question_id"),
                "seq": item.get("seq"),
                # 所有权语义:revisions 有 actor;hypotheses 无此列 → None
                "origin": item.get("actor"),
                "chain": chain,
                "used_by": used_by,
                "used_count": len(used_by),
                "last_used_at": max(used_at) if used_at else None,
            }
            return entry

        hypotheses = [await _entry("hypothesis", dict(r)) for r in hyp_rows]
        revisions = [await _entry("revision", dict(r)) for r in rev_rows]
        pool = hypotheses + revisions

        knowledge = [e for e in pool if e["chain"]]
        clues = [e for e in pool if not e["chain"]]
        knowledge.sort(key=lambda e: e["last_used_at"] or e["created_at"], reverse=True)
        clues.sort(key=lambda e: e["created_at"], reverse=True)

        all_used = [(r["created_at"], r) for rs in used_by_map.values() for r in rs]
        recently_used = max(all_used, key=lambda p: p[0])[1] if all_used else None

        return APIResponse(
            data={
                "knowledge": knowledge,
                "clues": clues,
                "recently_used": recently_used,
                "counts": {"knowledge": len(knowledge), "clues": len(clues)},
            },
            meta=build_response_meta(request),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _http_500("记忆投影", e)


# ---- v025 主动复访 -----------------------------------------------------------
# "上一段历史主动找上门来":写入面(set_revisit)+ 读取面(due 清单)。
# 前端挂点(W2 Question 面板)与 agent 注入归 follow-up,本层只出 API。


class RevisitRequest(BaseModel):
    next_revisit_at: Optional[str] = None
    actor: str = "user"


@router.post("/questions/{question_id}/revisit")
async def set_question_revisit(
    question_id: str,
    payload: RevisitRequest,
    request: Request,
    db: Any = Depends(get_graph_db),
) -> APIResponse:
    """设/清 Question 复访时点。``next_revisit_at: null`` = 清除计划。"""
    try:
        repo = ResearchQuestionRepository(db)
        question = await repo.set_revisit(
            question_id, payload.next_revisit_at, actor=payload.actor
        )
        return APIResponse(
            data={"question": question}, meta=build_response_meta(request)
        )
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("复访计划设置", e)


@router.get("/revisit/due")
async def list_due_questions(
    request: Request,
    now: str,
    limit: int = 20,
    db: Any = Depends(get_graph_db),
) -> APIResponse:
    """到期该复访的问题清单。``now`` 必填 —— 时间判定显式注入。"""
    try:
        repo = ResearchQuestionRepository(db)
        due = await repo.due(now, limit=limit)
        return APIResponse(data={"due": due}, meta=build_response_meta(request))
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("复访清单读取", e)


# --- question → pillar 桥(计划 internal-plan)--
#
# Selene 有两条完全隔离的研究循环:question 链(council→revision,终点)与
# mission 链(mission→pillar→evidence,无人类创建面)。本组端点是桥。
# 形态是两步 confirm-submit:GET 出草稿(**纯读**),用户改完 POST 才落库。


def _active_pillars(
    mission_id: str, pillars: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """活跃支柱 = 非 superseded 且非 retired(谓词与 mission_thesis.py 同源)。

    与 ``aggregate_status`` 的活跃判定逐字一致 —— 那里的活跃集决定 mission
    verdict,所以桥在警告里列出的必须**正是**会拖累 verdict 的那批。
    """
    return [
        {
            "id": p["id"],
            "claim": p.get("claim"),
            "status": p.get("status"),
            "priority": p.get("priority"),
        }
        for p in pillars
        if p.get("superseded_by") is None and p.get("status") != "retired"
    ]


@router.get("/questions/{question_id}/promote-draft")
async def get_promote_draft(
    question_id: str,
    request: Request,
    seq: Optional[int] = None,
    subject_type: Optional[str] = None,
    subject_id: Optional[str] = None,
    db: Any = Depends(get_graph_db),
) -> APIResponse:
    """把一条 revision 组装成 pillar 草稿(**纯读,零写入**)。

    草稿在对话框里预填,用户改完才走 ``POST .../promote``;取消 = 零写入。

    ``subject_type``/``subject_id`` 为 override:用户在 UI 里改了主体时,
    前端带它们重发本 GET,好让 ``existing_pillars`` 警告跟着**真实**的复用
    mission 刷新(而不是 draft 里那个建议值)。
    """
    try:
        repo = ResearchQuestionRepository(db)
        question = await repo.get(question_id)
        if question is None:
            raise HTTPException(
                status_code=404, detail=f"question not found: {question_id}"
            )

        revisions = await repo.list_revisions(question_id)
        if not revisions:
            raise HTTPException(
                status_code=409,
                detail="该问题还没有任何 revision — 先跑一次调查会再立支柱",
            )
        if seq is None:
            revision = revisions[-1]
        else:
            matched = [r for r in revisions if r.get("seq") == seq]
            if not matched:
                raise HTTPException(
                    status_code=404, detail=f"revision seq={seq} not found"
                )
            revision = matched[0]

        # 主体建议前先取已注册策略 id 集:suggest_subject 借此对挂错的 anchor
        # (探针名/回测 id)降级为"主体未映射",而不是预填成 strategy(2026-10-13)。
        known_strategies = await _known_strategy_ids(db)
        draft = build_promote_draft(question, revision, known_strategies)

        # 主体:override 优先于 draft 的建议值(两者都无 → 交还用户手选)。
        if subject_type is not None or subject_id is not None:
            if subject_type not in _SUBJECT_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=f"subject_type 须为 {_SUBJECT_TYPES}",
                )
            if not subject_id:
                raise HTTPException(status_code=422, detail="subject_id 不可为空")
            draft["mission"]["subject_type"] = subject_type
            draft["mission"]["subject_id"] = subject_id

        # 已有的开放 mission(与 Phase 2 的 create(dedup=True) 复用键完全一致,
        # 保证警告不失真)+ 其活跃支柱。
        existing_mission_id = None
        active: list[dict[str, Any]] = []
        st, sid = draft["mission"]["subject_type"], draft["mission"]["subject_id"]
        if st and sid:
            from selene_space_storage.repositories.mission_thesis import (
                MissionThesisRepository,
            )
            from selene_space_storage.repositories.research_missions import (
                ResearchMissionRepository,
            )

            existing = await ResearchMissionRepository(db).find_open_by_question(
                st, sid, str(question.get("title") or "")
            )
            if existing is not None:
                existing_mission_id = str(existing["id"])
                pillars = await MissionThesisRepository(db).list_pillars(
                    existing_mission_id
                )
                active = _active_pillars(existing_mission_id, pillars)

        draft["existing_mission_id"] = existing_mission_id
        draft["existing_pillars"] = active
        return APIResponse(data=draft, meta=build_response_meta(request))
    except HTTPException:
        raise
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("支柱草稿生成", e)


@router.post("/questions/{question_id}/promote")
async def promote_question(
    question_id: str,
    payload: PromoteRequest,
    request: Request,
    db: Any = Depends(get_graph_db),
) -> APIResponse:
    """把一条 revision 提升为 pillar(桥的**唯一新写入面**)。

    顺序(计划 Phase 2):校验全部前置 → claim 硬门 → create mission(dedup
    复用既有开放 mission)→ add_pillar(model_line 服务端强制)→ addresses 边
    (UNIQUE 约束天然幂等)。

    **不碰 question 状态、不写 question 事件流、不自动 investigate/close**
    —— 仓内红线(见 investigate_council 注释)。问题结不结案是用户的另一颗按钮。
    """
    from selene_space_storage.repositories._mission_thesis_vocab import (
        PILLAR_PRIORITIES,
        PROVENANCE_LEVELS,
    )
    from selene_space_storage.repositories.mission_thesis import (
        MissionThesisRepository,
    )
    from selene_space_storage.repositories.research_missions import (
        ResearchMissionRepository,
    )

    try:
        repo = ResearchQuestionRepository(db)
        question = await repo.get(question_id)
        if question is None:
            raise HTTPException(
                status_code=404, detail=f"question not found: {question_id}"
            )

        # ① 校验全部前置(在写任何东西之前失败,不留孤儿)
        m, p = payload.mission, payload.pillar
        if m.subject_type not in _SUBJECT_TYPES:
            raise HTTPException(
                status_code=422, detail=f"subject_type 须为 {_SUBJECT_TYPES}"
            )
        if p.priority not in PILLAR_PRIORITIES:
            raise HTTPException(
                status_code=422, detail=f"priority 须为 {PILLAR_PRIORITIES}"
            )
        if p.provenance not in PROVENANCE_LEVELS:
            raise HTTPException(
                status_code=422,
                detail=f"provenance 须为 {PROVENANCE_LEVELS}(这是置信等级,不是来源描述)",
            )
        if p.threshold_provenance not in PROVENANCE_LEVELS:
            raise HTTPException(
                status_code=422, detail=f"threshold_provenance 须为 {PROVENANCE_LEVELS}"
            )

        revisions = await repo.list_revisions(question_id)
        matched = [r for r in revisions if r.get("seq") == payload.revision_seq]
        if not matched:
            raise HTTPException(
                status_code=404, detail=f"revision seq={payload.revision_seq} not found"
            )
        revision = matched[0]
        rev_id = str(revision["id"])

        # ② claim 硬门:claim 必须真的改写,不能是问题标题的问号剥离版。
        #    五段契约无【结论】段 → 预填必然是问句,硬门是唯一的契约保障。
        if normalize_claim(p.claim) == normalize_claim(
            str(question.get("title") or "")
        ):
            raise HTTPException(
                status_code=422,
                detail="claim 须是可证伪陈述,不能直接是问题标题(请从 revision 结论句改写)",
            )

        # ③ mission(dedup=True 复用同 (subject_type, subject_id, question) 的开放 mission)
        #    reused 必须在 create **之前**判定 —— create 之后查不出"是新造还是复用"。
        mission_repo = ResearchMissionRepository(db)
        pre_existing = await mission_repo.find_open_by_question(
            m.subject_type, m.subject_id, m.question or str(question.get("title") or "")
        )
        reused = pre_existing is not None

        mission_id = await mission_repo.create(
            title=m.title,
            question=m.question or str(question.get("title") or ""),
            subject_type=m.subject_type,
            subject_id=m.subject_id,
            created_by="user",
            context={
                "pipeline": "question-promote",
                "question_id": question_id,
                "revision_id": rev_id,
            },
        )

        # ④ pillar —— model_line 服务端强制(客户端传值一律忽略)
        #    已知近似:手写 revision 也带 council 前缀(见计划"已知局限");
        #    前缀语义实为"经桥出生",真 provenance 是 rev_id。
        pillar_id = await MissionThesisRepository(db).add_pillar(
            mission_id,
            p.claim,
            p.priority,
            baseline=p.baseline,
            expected_path=p.expected_path,
            confirm_threshold=p.confirm_threshold,
            warning_threshold=p.warning_threshold,
            break_threshold=p.break_threshold,
            threshold_provenance=p.threshold_provenance,
            provenance=p.provenance,
            model_line=f"council v{payload.revision_seq} {rev_id}",
            implied_action=p.implied_action,
            next_proof_point=p.next_proof_point,
            next_proof_source=p.next_proof_source,
            owner=p.owner,
        )

        # ⑤ addresses 边(mission→question)。UNIQUE(from_type,from_id,kind,to_type,to_id)
        #    天然幂等 —— 二次 promote 不会重复。
        edge_written = True
        try:
            await ResearchEdgeRepository(db).add_fact_edge(
                ("mission", mission_id),
                ("question", question_id),
                "addresses",
                actor="user",
            )
        except Exception:  # noqa: BLE001 — UNIQUE 冲突 = 边已存在,不是失败
            edge_written = False

        return APIResponse(
            data={
                "mission_id": mission_id,
                "pillar_id": pillar_id,
                "reused": reused,
                "edge_written": edge_written,
                "revision_id": rev_id,
            },
            meta=build_response_meta(request),
        )
    except HTTPException:
        raise
    except (ValueError, KeyError) as e:
        raise _domain_error(e)
    except Exception as e:
        raise _http_500("问题提升为支柱", e)
