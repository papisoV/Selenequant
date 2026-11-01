"""ResearchAutorenew:把"结案派生的问题"接到"下一轮调查"上。

**它补的是五步环 ⑤→① 那颗心跳。** 2026-10-25 在生产库实测:结案会派生
一个 ``created_by='selene'`` 的子问题(带 ``derived_from`` 边、继承父问题
anchor),然后就没有然后了 —— 没有任何东西去推进它。``promote-draft`` 对
它返回 409("还没有任何 revision"),而跑出 revision 的唯一路径是
``POST /questions/{id}/investigate``,一颗**只由人按**的按钮。

于是"循环"实际是:人结案 → 系统留下一个问题标题 → 等人再点一次调查。
本模块把那一次点击交给系统;**再往后的每一步仍停在人手里**。

两步,职责严格分开:

1. **续研** (``renew_one_question``): 对"由结案派生、尚无 revision、
   status=open"的问题跑一次调查会 → 产出第 1 版 revision。
2. **开立** (``promote_one_question``): 把该 revision 提升为一个
   ``investigation`` 相位的 mission。此后由既有的 ``mission_worker`` 接手:
   真回测、代码判定 signal、停在 ``needs_input`` 等人拍板。

第二步是**可选**的(``auto_promote``)。不开的话,续研只把 revision 摆出来,
立支柱仍由人点 —— 那也已经是改进,因为人面对的从"一个空问题"变成了
"一版带引用的回答"。

## 为什么自动路径**不建 pillar**

``POST /questions/{id}/promote`` 有一道硬门:claim 必须真的改写,不能是问题
标题剥问号版(五段契约没有【结论】段,自动提取只能得到问句)。自动路径若
自己去造 claim 骗过这门,等于把"可证伪陈述"降级成模板句 —— 正是这道门要
防的事。

不需要这么做:``mission_verifier.verify_mission`` 在 mission **没有活跃支柱**
时会自己开一条,claim 用**实际解析出的** strategy/symbol/interval 填
(``_AUTO_CLAIM``),provenance=draft。那条比任何从问题标题猜出来的句子都更
贴近真实证据。所以这里只建 mission(不建 pillar),把 pillar 留给验证器。

## 红线与它为什么被跨过

仓内红线是"开调查是用户的按钮"。本模块确实跨了它 —— 但**默认关闭**,并且
只在用户把 ``research_autorenew.enabled`` 打开后才跑。开关本身就是授权:
打开 = "我允许系统自己对结案派生的问题发起调查"。

**绝不跨的两条**:
- 不 ``resolve`` / 不 ``close`` 任何 mission 或 question(结案仍只许人点);
- 不开任何**非结案派生**的调查 —— 只认 ``derived_from`` 入边。council 自己
  提议的【新问题】只有 ``parent_question_id``、没有这条边,所以不会指数增殖。

## 预算:一次性账本 + 全局 token 上限

结案派生的边是**一次性**的:一个子问题被续研过,``autorenew_started`` 事件
就落了账,此后永不再自动跑。所以自动花费的上界 = 历史上结案派生的子问题数
× 一场调查会,不是"每轮 × N"。新子问题只来自新一轮**人手动**结案,而结案
本身要人按。

另有全局兜底 ``llm_budget.daily_tokens`` —— 2026-10-26 核实:``call_llm``
在发请求前调 ``budget_exceeded``,到上限即返回 None;本模块的 council 与
worker 都经 ``call_llm``,所以**确实受它约束**(0 = 不设上限,本机默认)。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from selene_space_storage.repositories.research_graph import (
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)

from app.core import investigation_guard

logger = logging.getLogger(__name__)

#: 已落这几个事件的问题不再被自动续研(幂等账本)。
_AUTORENEW_KIND = "autorenew_started"
#: 已落这几个事件的问题不再被自动立 mission。
_AUTOPROMOTE_KIND = "autopromote_done"

#: 自动开立的 mission 的 ``context.pipeline`` 标记 —— 便于事后从库里
#: 区分"人 promote 的"与"系统续研派生后开立的"。
_PIPELINE = "autorenew"

#: 每轮扫描的**候选上限**(已按 derived_from 过滤之后)。不是花费上限。
_DEFAULT_SCAN_LIMIT = 3

#: 与 ``ResearchMissionRepository._MARKET_SUBJECT_RE`` 逐字一致 —— 在这里
#: 先挡,是为了把"为什么跳过"写进事件,而不是让 create 抛错。
_MARKET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


async def _has_event(
    repo: ResearchQuestionRepository, question_id: str, kind: str
) -> bool:
    return bool(await repo.list_events(question_id, limit=1, kind=kind))


async def _is_settlement_derived(db: Any, question_id: str) -> str | None:
    """该问题是否由结案派生(有 ``derived_from`` 出边)?返回父问题 id 或 None。

    **这是防增殖的关键闸门**,也顺带筛掉脏数据:council 提议的新问题没有
    这条边,所以不会自动续研;结案派生的子问题有,且其 anchor 继承自父
    问题(即真实回测跑过的那个 anchor)。
    """
    try:
        edges = await ResearchEdgeRepository(db).list_edges(
            question_id, direction="out", kind="derived_from", limit=5
        )
    except Exception:  # noqa: BLE001 — 读不到边 = 不续研,不是崩
        logger.warning(
            "autorenew: derived_from lookup failed for %s", question_id, exc_info=True
        )
        return None
    for edge in edges:
        if edge.get("from_type") == "question" and edge.get("to_type") == "question":
            return str(edge.get("to_id") or "") or None
    return None


def _market_subject(anchor: dict[str, Any]) -> tuple[str, str] | None:
    """anchor 里可用的标的 → ``("market", symbol)``;没有则 None。

    只认 anchor 自己声明的标的,且须过 ``ResearchMissionRepository`` 的
    market 形态门(``^[A-Za-z0-9][A-Za-z0-9._:/-]*$``)—— 在这里先挡住,
    而不是让 ``create`` 抛出来,是为了能把"为什么跳过"写进事件。
    """
    for key in ("symbols", "symbol"):
        val = anchor.get(key)
        candidate = ""
        if isinstance(val, (list, tuple)) and val:
            candidate = str(val[0]).strip()
        elif isinstance(val, str):
            candidate = val.strip()
        if not candidate or not _MARKET_ID_RE.match(candidate):
            continue
        return ("market", candidate)
    return None


async def _candidates(db: Any, limit: int) -> list[tuple[dict[str, Any], str]]:
    """(问题, 父问题 id) 候选列表。按状态/版数/事件/边由便宜到贵逐层过滤。"""
    repo = ResearchQuestionRepository(db)
    questions = await repo.list_questions(status="open", limit=max(50, limit * 20))
    out: list[tuple[dict[str, Any], str]] = []
    for q in questions:
        if int(q.get("revision_count") or 0) > 0:
            continue
        if not q.get("parent_question_id"):
            continue
        qid = str(q["id"])
        if await _has_event(repo, qid, _AUTORENEW_KIND):
            continue
        parent_id = await _is_settlement_derived(db, qid)
        if parent_id is None:
            continue
        out.append((q, parent_id))
        if len(out) >= limit:
            break
    return out


async def renew_one_question(
    db: Any,
    settings_service: Any,
    question: dict[str, Any],
    parent_question_id: str,
    *,
    council_fn: Any = None,
) -> dict[str, Any]:
    """对一个结案派生的问题跑一次调查会。

    返回 ``{"question_id", "status", ...}``;
    ``status`` ∈ ``renewed`` / ``skipped_running`` / ``skipped_already`` / ``failed``。
    """
    question_id = str(question["id"])
    repo = ResearchQuestionRepository(db)

    # 幂等复查(候选阶段已查过一次;这里再查,因为从候选到真正开跑之间
    # 可能过了别的轮次 —— 代价是一次廉价查询)。
    if await _has_event(repo, question_id, _AUTORENEW_KIND):
        return {"question_id": question_id, "status": "skipped_already"}

    # 与人手点的调查共用同一把锁 —— 两处各跑一轮就是三视角 LLM 双份账单。
    if investigation_guard.is_running(question_id):
        return {"question_id": question_id, "status": "skipped_running"}

    # **先写账本再花钱**:即便调查会中途崩溃,也不会被下一轮再自动续研一次。
    await repo.append_event(
        question_id,
        actor="selene",
        kind=_AUTORENEW_KIND,
        detail={"parent_question_id": parent_question_id},
    )

    run_council = council_fn
    if run_council is None:
        from app.services.investigate_council import run_council as run_council

    try:
        result = await run_council(db, settings_service, question_id)
    except Exception as exc:  # noqa: BLE001 — 单条失败不连坐整轮
        logger.warning(
            "autorenew council failed for %s: %s", question_id, exc, exc_info=True
        )
        try:
            await repo.append_event(
                question_id,
                actor="selene",
                kind="autorenew_failed",
                detail={"error": f"{type(exc).__name__}: {exc}"[:400]},
            )
        except Exception:  # noqa: BLE001 — 记失败本身失败不该再炸
            logger.warning("autorenew could not record failure: %s", question_id)
        return {
            "question_id": question_id,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }

    revision = (result or {}).get("revision") or {}
    revision_id = revision.get("id")
    await repo.append_event(
        question_id,
        actor="selene",
        kind="autorenew_done",
        detail={"revision_id": revision_id, "seq": revision.get("seq")},
    )
    return {
        "question_id": question_id,
        "status": "renewed",
        "revision_id": revision_id,
        "revision_seq": revision.get("seq"),
    }


async def promote_one_question(
    db: Any,
    question: dict[str, Any],
    revision: dict[str, Any],
) -> dict[str, Any]:
    """把一版自动产出的 revision 开立为 ``investigation`` mission(不建 pillar)。

    主体取自问题**自己的** anchor —— 结案子问题在 ``mission_settlement`` 里
    继承父问题的 anchor,所以两者通常相同;若子问题自带了更具体的 anchor,
    用它的才是对的(那是本轮要研究的东西)。映射不出主体 → 跳过并留痕,
    绝不猜一个。

    ``research_missions.create`` 的 ``dedup=True`` 会复用同
    ``(subject_type, subject_id, question)`` 的开放 mission,天然防重复开立。
    """
    from selene_space_storage.repositories.research_graph._promote_draft import (
        suggest_subject,
    )

    question_id = str(question["id"])
    revision_id = str(revision.get("id") or "")
    repo = ResearchQuestionRepository(db)

    known_strategy_ids = None
    try:
        rows = await db.execute("SELECT id FROM strategies")
        known_strategy_ids = {str(r["id"]) for r in rows}
    except Exception:  # noqa: BLE001 — 无登记表 = 不做诚实校验
        pass

    # 诚实模式:anchor 里挂的若是探针/回测 id(生产库里确实有),
    # ``suggest_subject`` 会返回 None 而不是把它当策略。
    anchor = question.get("anchor") or {}
    subject, subject_reason = suggest_subject(anchor, known_strategy_ids)
    downgrade: dict[str, Any] | None = None

    # 诚实模式判掉 strategy 建议时,**不直接放弃**:同一个 anchor 里往往还有
    # 合法标的(2026-10-25 实测 q-1ccf83a4f424 的 anchor 是
    # ``strategy_ids:["rsi_ma_delta"]`` + ``symbols:["BTC/USDT"]``,而
    # ``rsi_ma_delta`` 是预设模板名、不在 strategies 表 —— 它父亲 mission
    # 实际用的主体就是 ``market/BTC/USDT``)。退成 market 主体不是猜:标的
    # 来自 anchor,且回测策略由 ``resolve_verification_target`` 另从
    # ``anchor.strategy_ids`` 取,不受主体类型影响。降级必须留痕。
    if subject is None:
        fallback = _market_subject(anchor)
        if fallback is not None:
            downgrade = {"from": subject_reason, "to": fallback}
            subject = fallback
            subject_reason = (
                f"strategy 建议被诚实模式拒绝({subject_reason}),"
                f"降级为 market/{fallback[1]}"
            )

    if subject is None:
        await repo.append_event(
            question_id,
            actor="selene",
            kind="autopromote_skipped",
            detail={"revision_id": revision_id, "reason": subject_reason},
        )
        return {
            "question_id": question_id,
            "status": "skipped_subject",
            "reason": subject_reason,
        }

    mission_repo = ResearchMissionRepository(db)
    title = str(question.get("title") or "")
    body = str(question.get("body") or "") or title
    mission_id = await mission_repo.create(
        title=title,
        question=body,
        subject_type=subject[0],
        subject_id=subject[1],
        created_by="selene",
        context={
            "pipeline": _PIPELINE,
            "question_id": question_id,
            "revision_id": revision_id,
            "parent_question_id": question.get("parent_question_id"),
        },
    )
    edge_written = True
    try:
        await ResearchEdgeRepository(db).add_fact_edge(
            ("mission", mission_id),
            ("question", question_id),
            "addresses",
            actor="selene",
        )
    except Exception:  # noqa: BLE001 — UNIQUE 冲突 = 边已存在,不是失败
        edge_written = False

    await repo.append_event(
        question_id,
        actor="selene",
        kind=_AUTOPROMOTE_KIND,
        detail={
            "mission_id": mission_id,
            "revision_id": revision_id,
            "subject_type": subject[0],
            "subject_id": subject[1],
            "subject_reason": subject_reason,
            "subject_downgrade": downgrade,
            "edge_written": edge_written,
        },
    )
    return {
        "question_id": question_id,
        "status": "promoted",
        "mission_id": mission_id,
        "subject_type": subject[0],
        "subject_id": subject[1],
    }


async def autorenew_round(
    db: Any,
    settings_service: Any,
    *,
    limit: int | None = None,
    auto_promote: bool | None = None,
    enabled: bool | None = None,
    council_fn: Any = None,
) -> dict[str, int]:
    """扫描一轮:续研 →(可选)开立。返回计数。

    三个开关都是 ``None`` 时**从设置里读**(与 ``autorenew_loop`` 同一份口径);
    显式传值则覆盖。此前这里对 ``auto_promote`` 有个写死的 ``True`` 默认,
    与调用方刚读过设置里"关掉它"的事实相反 —— 直接调 round 的路径(测试、
    将来别的调度器)会静默按开处理。
    """
    counts = {
        "scanned": 0,
        "renewed": 0,
        "promoted": 0,
        "skipped": 0,
        "failed": 0,
    }
    cfg = _read_config(settings_service)
    if enabled is None:
        enabled = bool(cfg["enabled"])
    if limit is None:
        limit = int(cfg["max_per_round"])
    if auto_promote is None:
        auto_promote = bool(cfg["auto_promote"])

    # 关掉 = 完全不碰库。调度器已在外层判过一次,但直接调 round 的路径
    # 也该得到同一个答案,否则"关闭"只是一个建议。
    if not enabled:
        return counts

    candidates = await _candidates(db, limit)
    counts["scanned"] = len(candidates)
    repo = ResearchQuestionRepository(db)

    for question, parent_id in candidates:
        question_id = str(question["id"])
        try:
            renewed = await renew_one_question(
                db, settings_service, question, parent_id, council_fn=council_fn
            )
        except Exception:  # noqa: BLE001 — 单条异常不连坐整轮
            logger.warning("autorenew failed on %s", question_id, exc_info=True)
            counts["failed"] += 1
            continue

        if renewed["status"] != "renewed":
            key = "failed" if renewed["status"] == "failed" else "skipped"
            counts[key] += 1
            continue
        counts["renewed"] += 1

        if not auto_promote:
            continue
        revision_id = renewed.get("revision_id")
        if not revision_id:
            continue
        try:
            if await _has_event(repo, question_id, _AUTOPROMOTE_KIND):
                counts["skipped"] += 1
                continue
            revision = next(
                (
                    r
                    for r in await repo.list_revisions(question_id)
                    if str(r.get("id")) == str(revision_id)
                ),
                None,
            )
            fresh = await repo.get(question_id) or question
            if revision is None:
                counts["failed"] += 1
                continue
            promoted = await promote_one_question(db, fresh, revision)
            if promoted["status"] == "promoted":
                counts["promoted"] += 1
            else:
                counts["skipped"] += 1
        except Exception:  # noqa: BLE001
            logger.warning("autopromote failed on %s", question_id, exc_info=True)
            counts["failed"] += 1

    return counts


async def autorenew_loop(
    state: dict,
    *,
    default_interval: float,
) -> None:
    """周期扫描。每轮重读设置(关闭后无需重启即生效)。

    单轮异常不连坐:记 warning 继续下一轮。间隔下限夹在 60 秒 —— 一场
    三视角调查会要数分钟,间隔调到几秒只会堆叠,不会更快。

    db 取法与 ``mission_worker_round`` 一致:``research_db`` 优先。
    """
    while True:
        interval = default_interval
        try:
            db = state.get("research_db") or state.get("db_manager")
            settings_service = state.get("settings_service")
            if db is not None and settings_service is not None:
                cfg = _read_config(settings_service)
                if cfg["enabled"]:
                    interval = max(60.0, float(cfg["interval_minutes"]) * 60.0)
                    counts = await autorenew_round(
                        db,
                        settings_service,
                        limit=int(cfg["max_per_round"]),
                        auto_promote=bool(cfg["auto_promote"]),
                        enabled=True,
                    )
                    if any(counts[k] for k in ("renewed", "promoted", "failed")):
                        logger.info(
                            "research autorenew: scanned=%d renewed=%d promoted=%d "
                            "skipped=%d failed=%d",
                            counts["scanned"],
                            counts["renewed"],
                            counts["promoted"],
                            counts["skipped"],
                            counts["failed"],
                        )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("research autorenew round failed", exc_info=True)
        await asyncio.sleep(interval)


def _read_config(settings_service: Any) -> dict[str, Any]:
    """读 ``research_autorenew`` 段,缺键按默认(**关闭**)处理。"""
    try:
        section = settings_service.get_research_autorenew()
    except Exception:  # noqa: BLE001 — 读不到设置 = 保持关闭
        logger.warning("could not read research autorenew settings", exc_info=True)
        return _DEFAULTS
    return {
        "enabled": bool(section.get("enabled", False)),
        "interval_minutes": int(section.get("interval_minutes", 30)),
        "max_per_round": int(section.get("max_per_round", _DEFAULT_SCAN_LIMIT)),
        "auto_promote": bool(section.get("auto_promote", True)),
    }


_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "interval_minutes": 30,
    "max_per_round": _DEFAULT_SCAN_LIMIT,
    "auto_promote": True,
}


__all__ = [
    "autorenew_loop",
    "autorenew_round",
    "promote_one_question",
    "renew_one_question",
]
