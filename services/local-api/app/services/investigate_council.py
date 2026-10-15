"""多视角调查会(A1/A3/A4)—— investigate 端点的委托目标。

单角色续写的升级:三个异质视角各自独立召回 + 独立走 A2 引用纪律,
主持人综合三方观点写下一版正式回答。分工纪律:

- 视角产物落 events(kind='investigate_perspective'),不进回答链 ——
  revision 是"对问题的正式回答",平行视角不是"下一版"。
- 只有主持人落 revision;视角失败降级继续,仅主持人失败才 503。
- 主持人提出的新问题落 research_questions(created_by='selene' +
  parent_question_id 表达"系统提议"),**不自动 investigate/close** ——
  拍板权在用户。

与 research_graph.py 的依赖方向:api → service(单向),extract_cited
从端点迁到这里改公开名。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime

from fastapi import HTTPException

from selene_space_storage.repositories.research_graph import (
    RESEARCH_RECORD,
    ResearchQuestionRepository,
    filter_known_by,
    parse_as_of,
    recall,
)
from selene_space_storage.repositories.research_graph.recall import (
    EVIDENCE,
    HYPOTHESIS,
    REVISION,
)

from app.core.llm_helper import call_llm
from app.services.market_material import assemble_market_material

logger = logging.getLogger(__name__)

# glm 系推理模型的 thinking tokens 会计入 max_tokens —— 预算小时 content 为空
# (smoke 实测: 900/2048 空 content, MiroFish 同端点用 4096 可用)。给足余量。
# 4096 下单次调用可超 90s(host 实测 85s)——超时太短会把"慢"误判成"失败"。
PERSPECTIVE_MAX_TOKENS = 4096
HOST_MAX_TOKENS = 4096
INVESTIGATE_TIMEOUT = 240.0
LEDGER_KINDS = (REVISION, HYPOTHESIS, EVIDENCE)
LEDGER_LIMIT = 200
PROMPT_TOP_N = 20
MAX_NEW_QUESTIONS = 3

_CITED_LINE_RE = re.compile(r"^CITED:\s*(.+)$", re.MULTILINE)
_INVALID_CITED_RE = re.compile(r"[^a-zA-Z0-9_,\- ]")
_NEW_QUESTION_LINE_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*])\s*(.+)$")


async def assert_writable(db) -> None:
    """开跑前确认账本能写。不可写 → 立即 503,不烧任何 LLM 调用。

    为什么需要(2026-10-02 实测):investigate 的前半程是**纯 LLM 消耗**
    (3 视角并发各调一次),写库发生在后半程。若库不可写:
      - 三个视角的 LLM 调用**已经付过费**才在 append_event 炸掉;
      - 更糟的是 run_council_background 收尾写 investigate_finished 也失败,
        前端**永远等不到结束事件**,只能轮询到 10 分钟上限。
    实测真库当时写锁持续存在(3/3 失败、busy_timeout=30s 等满 32s 仍失败),
    根因是多个 local-api 实例共享同一 SQLite 且 AsyncSQLiteManager.connect()
    未设 busy_timeout(默认 0 = 立即失败)。这是环境问题,代码补不了;
    能做的是**尽快失败、且不浪费已花的钱**。

    本检查是 best-effort:它只保证"此刻"可写(TOCTOU 仍在)——不能保证
    4 次 LLM 调用之后仍可写。但把最常见的"一开跑就注定写不进去"挡在
    花费之前,是纯收益。
    """
    try:
        async with db.connection() as conn:
            await conn.execute("SAVEPOINT investigate_write_probe")
            await conn.execute("RELEASE investigate_write_probe")
    except Exception as exc:  # noqa: BLE001 — 任何写不进去的原因都要转成明确反馈
        logger.warning("investigate 前置写检失败: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="账本当前不可写(数据库被占用或只读),已中止以免浪费 LLM 调用;"
            "请确认是否有多余的 local-api 实例在争用同一数据库",
        ) from exc


# -- 视角定义(A3):差异来自材料与人格,不来自模型(D4) ------------------------

# 经验派 ↔ 记录派按 recall_class 严格二分(一个候选必有或必无证据链);
# 挑战派全谱靠人格区隔。
PERSPECTIVES: tuple[dict[str, object], ...] = (
    {
        "key": "empiricist",
        "label": "经验派",
        "require_class": "learned_experience",
        "kinds": (REVISION, HYPOTHESIS, EVIDENCE),
        "system": (
            "你是量化研究团队里的经验派研究员。只信被证据链锚住的历史"
            "(Learned Experience):没有走到 Episode/Evidence 的记录对你"
            "只是待检验线索,不得当作既有知识消费。给出保守、可追溯的回答。"
        ),
    },
    {
        "key": "recordist",
        "label": "记录派",
        "require_class": RESEARCH_RECORD,
        # 探针证据是 learned_experience,天然进不了 Research-Record 视角;
        # 放进来也会被 require_class 过滤,参数表里放死值是误导
        "kinds": (REVISION, HYPOTHESIS),
        "system": (
            "你是量化研究团队里的记录派研究员。只看尚未被证据锚定的记录与"
            "假设(Research Record):它们是待检验线索。你关注前人没验证过的"
            "缝隙,给出探索性的回答与可检验的验证步骤。"
        ),
    },
    {
        "key": "challenger",
        "label": "挑战派",
        "require_class": None,
        "kinds": (REVISION, HYPOTHESIS, EVIDENCE),
        "system": (
            "你是量化研究团队里的挑战派研究员(魔鬼代言人)。你的任务是找出"
            "其他视角没看的缝隙:挑战既有结论的隐含假设、指出反例与盲区。"
            "你的回答必须针对材料,不许为反对而反对。"
        ),
    },
)


def extract_cited(content: str, valid_ids: set[str]) -> tuple[str, list[str]]:
    """从 LLM 输出尾部解析 CITED: 行,返回 (正文, cited 列表)。

    cited 为空表示"未声明" —— 调用方决定回退语义(引上一版/首轮空)。
    """
    m = _CITED_LINE_RE.search(content)
    if not m:
        return content.strip(), []
    cited_line = m.group(1).strip()
    body = content[: m.start()].strip()
    if _INVALID_CITED_RE.search(cited_line):
        return body, []
    cited = [t.strip() for t in cited_line.split(",") if t.strip() in valid_ids]
    declared = len([t for t in cited_line.split(",") if t.strip()])
    if len(cited) != declared:
        return body, []
    return body, cited


def _age_label(iso: str | None, *, today: str | None = None) -> str:
    """把入库时间渲染成"MM-DD(N天前)"。解析不了 → 空串(fail-safe,不抛)。

    为什么带相对年龄而不只给日期:LLM 不知道当前是哪天,单看 ``09-26``
    无法判断是 6 天前还是去年 —— 没有年龄就没有"新旧"可言,而新定位
    恰恰要求"上一段历史改变下一次判断"。
    """
    if not iso:
        return ""
    try:
        ts = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    ref = (
        datetime.fromisoformat(today).replace(tzinfo=UTC)
        if today
        else datetime.now(UTC)
    )
    days = (ref.date() - ts.date()).days
    if days <= 0:
        age = "今天"
    elif days == 1:
        age = "昨天"
    elif days < 30:
        age = f"{days}天前"
    elif days < 365:
        age = f"{days // 30}个月前"
    else:
        age = f"{days // 365}年前"
    return f"{ts.strftime('%m-%d')}({age})"


_LEDGER_LINE_MAX = 500


def _clip(text: str, limit: int = _LEDGER_LINE_MAX) -> str:
    """账本单行超长则截断并标记,保留"原文还有更多"的信号。

    实测(2026-10-02 真库):账本里的 revision 必然**不是本问题的**
    (``_run_perspective`` 用 ``revision_ids`` 排除了本问题回答链),
    即跨问题召回 —— 最长 2965 字符的整篇回答会整段进 prompt。
    ``PROMPT_TOP_N=20`` 时最坏 5.9 万字符,淹没整段材料。
    阈值取 500:高于 evidence 的 ``_EVIDENCE_TEXT_MAX=400``,
    避免对已截断过的 evidence 二次截断(双重省略号)。
    """
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…(截断,原文 {len(text)} 字符)"


def narrate_candidate(cand: dict, *, today: str | None = None) -> str:
    """B2:把候选的证据链/匹配依据翻译成人话,附在候选行尾。不 dump dict。

    N1-a:行尾附入库时间 —— 此前候选不带任何时间,三视角研究员分不出
    哪条是上个月的、哪条是刚写的。
    N1-c:超长文本截断(跨问题 revision 会整篇进账本)。
    council as_of(回放调查):候选行尾附数据窗标注(仅回放模式,
    recall 只在 as_of 下产出该键);start/end 任一缺失 → 整段不渲染
    (recall 允许单边缺失为 None,渲染侧同容忍,避免 "None~None" 进 prompt)。
    """
    parts: list[str] = []
    chain = cand.get("evidence_chain") or []
    if chain:
        parts.append(
            f"证据链走到 {chain[-1].split(':', 1)[0]} {chain[-1].split(':', 1)[1]}"
            f"(共 {len(chain)} 跳)"
        )
    matched = cand.get("matched_on") or []
    if matched:
        parts.append("锚定 " + "、".join(m.replace("anchor:", "") for m in matched))
    window = cand.get("data_window")
    if isinstance(window, dict):
        w_start, w_end = window.get("start"), window.get("end")
        if w_start and w_end:
            parts.append(f"数据窗 {_age_label(w_start)}~{_age_label(w_end)}")
    suffix = f"({'; '.join(parts)})" if parts else "(无证据链,待检验线索)"
    age = _age_label(cand.get("created_at"), today=today)
    stamp = f"[{age}] " if age else ""
    return f"{stamp}{_clip(str(cand.get('text') or ''))}{suffix}"


async def _cited_with_retry(
    settings_service,
    prompt: str,
    system: str,
    max_tokens: int,
    candidate_ids: set[str],
    fallback_cited: list[str],
) -> tuple[str, list[str]]:
    """A2 纪律:首调 → CITED 越界重试一次 → 仍无则回退引上一版;正文空 = 调用失败。"""
    content = await call_llm(
        settings_service,
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        timeout=INVESTIGATE_TIMEOUT,
    )
    if not content:
        return "", []
    body, cited = extract_cited(content, candidate_ids)
    if not cited and content.strip() != body:
        retry_prompt = (
            prompt + "\n\n注意:上一次输出的 CITED: 行含候选集之外的 id,被拒收。"
            "CITED: 只能列材料里出现过的 id。请重新输出完整回答。"
        )
        retry = await call_llm(
            settings_service,
            prompt=retry_prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=INVESTIGATE_TIMEOUT,
        )
        if retry:
            body, cited = extract_cited(retry, candidate_ids)
    if not cited:
        cited = fallback_cited
    return body, cited


async def _run_perspective(
    db,
    settings_service,
    question: dict,
    revisions: list[dict],
    perspective: dict,
    material: str | None = None,
    as_of: str | None = None,
) -> dict:
    """单视角:独立 recall(自己的过滤组合)→ prompt → LLM → A2 三步纪律。

    完成即落 events(kind='investigate_perspective',P3 轮询流的数据源),
    返回 {perspective, content, cited, status};LLM 失败返回 status='failed',
    不抛异常 —— 降级决策在主持人环节做。

    council as_of(回放调查):as_of 非空时 recall 收窄到该认知边界、
    prompt 头部插回放 banner(含 v0 局限诚实句)、候选/回答链年龄按
    as_of 计;事件 detail 带 as_of 键(仅回放,成败都留 provenance)。
    """
    key = perspective["key"]
    candidates = await recall(
        db,
        question_id=question["id"],
        kinds=perspective["kinds"],
        require_class=perspective["require_class"],
        limit=LEDGER_LIMIT,
        purpose="investigate",
        as_of=as_of,
    )
    revision_ids = {r["id"] for r in revisions}
    ledger = [c for c in candidates if c["id"] not in revision_ids]

    lines = [f"研究问题:{question['title']}"]
    if as_of:
        lines.append(
            f"回放模式:本轮调查以 {as_of} 为认知边界,之后的账本记录不可见,"
            "本轮不提供行情材料。事件记录/匹配锚点仍按当前图景计算,属 v0 已知局限。"
        )
    if question.get("body"):
        lines.append(f"问题背景:{question['body']}")
    if material:
        lines.append("\n本轮行情材料(数据快照,无证据 id,不进 CITED):")
        lines.append(material)
    if revisions:
        lines.append("\n已有的回答链(最新在前):")
        for rev in reversed(revisions):
            age = _age_label(rev.get("created_at"), today=as_of)
            stamp = f"[{age}] " if age else ""
            lines.append(f"- {stamp}v{rev['seq']} (id: {rev['id']}): {rev['content']}")
    if ledger:
        lines.append("\n账本里的相关记录与假设(方括号内为入库时间):")
        for cand in ledger[:PROMPT_TOP_N]:
            lines.append(
                f"- [{cand['recall_class']}] (id: {cand['id']}) "
                f"{narrate_candidate(cand, today=as_of)}"
            )
    persona = perspective["label"]
    lines.append(
        f"\n你是{persona}。请基于以上材料给出你的视角下的分析:延续或修正"
        "之前的判断,明确指出你用了哪些材料,并给出可检验的下一步。"
        "分析必须包含一段反向检验:什么情况会推翻当前判断(没有就写"
        "「未发现明确反证条件」)。直接输出回答正文,不要客套。"
    )
    lines.append(
        "\n输出格式:正文之后,最后一行必须以 CITED: 开头,列出你实际依据的"
        "材料 id(逗号分隔,只能用上面出现过的 id,没有依据就不列任何 id)。"
    )

    # A2 纪律:候选集 = 本视角候选 ∪ 回答链 id
    candidate_ids = {c["id"] for c in candidates} | revision_ids
    latest = revisions[-1] if revisions else None
    fallback_cited = [latest["id"]] if latest else []

    prompt = "\n".join(lines)
    body, cited = await _cited_with_retry(
        settings_service,
        prompt=prompt,
        system=perspective["system"],
        max_tokens=PERSPECTIVE_MAX_TOKENS,
        candidate_ids=candidate_ids,
        fallback_cited=fallback_cited,
    )
    if not body:
        res = {"perspective": key, "content": "", "cited": [], "status": "failed"}
    else:
        res = {"perspective": key, "content": body, "cited": cited, "status": "ok"}
    # 完成即落事件(P3):轮询端能逐个看到视角到达,而不是 gather 完一次性出现
    # council as_of:回放轮的视角事件也留 as_of provenance(成败都留痕)
    event_detail = {
        "perspective": res["perspective"],
        "content": res["content"],
        "cited": res["cited"],
        "status": res["status"],
    }
    if as_of:
        event_detail["as_of"] = as_of
    await ResearchQuestionRepository(db).append_event(
        question["id"],
        actor="selene",
        kind="investigate_perspective",
        detail=event_detail,
    )
    return res


def _build_host_prompt(
    question: dict,
    revisions: list[dict],
    perspective_results: list[dict],
    missing: list[str],
    as_of: str | None = None,
) -> str:
    lines = [f"研究问题:{question['title']}"]
    if as_of:
        lines.append(
            f"回放模式:本轮调查以 {as_of} 为认知边界,之后的账本记录不可见,"
            "本轮不提供行情材料。事件记录/匹配锚点仍按当前图景计算,属 v0 已知局限。"
        )
    if question.get("body"):
        lines.append(f"问题背景:{question['body']}")
    if revisions:
        lines.append("\n已有的回答链(最新在前):")
        for rev in reversed(revisions):
            age = _age_label(rev.get("created_at"), today=as_of)
            stamp = f"[{age}] " if age else ""
            lines.append(f"- {stamp}v{rev['seq']} (id: {rev['id']}): {rev['content']}")
    if missing:
        lines.append(
            f"\n注意:视角 {', '.join(missing)} 本次缺席,请在其缺席的维度上保持谨慎。"
        )
    lines.append("\n三位视角研究员的分析如下:")
    for res in perspective_results:
        lines.append(f"\n【{res['perspective']} 视角】\n{res['content']}")
    lines.append(
        "\n你是主持人。请综合三位视角的分析,输出下一版正式回答,严格按以下"
        "五段结构,每段以固定标题开头:\n"
        "【时间线】\n【观点整合与矛盾】\n【深层分析】\n【反向检验】\n【新问题】\n"
        "【观点整合与矛盾】段不要只罗列分歧后两边都留着:若各视角对同一事实给出"
        "互斥陈述,直接指出哪一个有误并给出理由;确实无法判定时说明缺什么证据,"
        "不要用平均化的措辞和稀泥。\n"
        "【反向检验】段整合各视角的反证条件:什么情况会推翻当前判断,观察者"
        "能拿什么信号去盯。\n"
        "【新问题】段列出你认为值得继续研究的新问题(编号列表,最多 3 个;"
        '没有就写"无")。直接输出,不要客套。'
    )
    return "\n".join(lines)


def _parse_new_questions(host_content: str) -> list[str]:
    """从【新问题】段解析编号列表,最多取前 3 个。解析不到 → 空列表(不硬失败)。"""
    m = re.search(r"【新问题】\s*\n(.*?)(?:\n【|$)", host_content, re.DOTALL)
    if not m:
        return []
    out: list[str] = []
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line:
            continue
        nm = _NEW_QUESTION_LINE_RE.match(line)
        if nm:
            title = nm.group(1).strip()
            if title and title != "无":
                out.append(title)
            if len(out) >= MAX_NEW_QUESTIONS:
                break
    return out[:MAX_NEW_QUESTIONS]


async def run_council(
    db, settings_service, question_id: str, *, as_of: str | None = None
) -> dict:
    """调查会主体。返回:

    {revision, citation_migration, council: {perspectives: [int event ids],
     new_questions: [{id, title}]}, question_id}

    LLM 失败时抛 HTTPException(503)由调用方决定如何透传 —— 视角失败降级,
    主持人失败即失败。

    P3:视角事件在 _run_perspective 内完成即落;本函数只负责主持人段与
    收尾(investigate_finished 事件也在此落,后台包装层不碰 repo)。

    council as_of(回放调查,N1 第二刀):as_of 非空时三个读面全部收窄到
    该认知边界 —— ①两处 recall 直传;②本问题回答链按 created_at 过滤
    (filter_known_by,解析不出→排除,与 recall 同语义);③行情材料**不装配**
    (装配有 _write_snapshot 门控写副作用,会把历史切片编码成"现在才被记录"
    的 RECORDED evidence 污染账本;历史 K 线数据存在,拒绝理由是写副作用,
    不是数据缺失)。新产物以当前时刻落库(repo _now(),不 backdate),
    rationale/事件 detail 带 as_of provenance(成败都留痕)。
    """
    as_of_ts = parse_as_of(as_of) if as_of else None
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
    revisions = await repo.list_revisions(question_id)
    if as_of_ts is not None:
        # 读面②:回答链前视一处断根 —— 过滤后的列表流经视角/主持人 prompt、
        # 排除集、candidate_ids、fallback,全部消费点共用此列表。
        revisions = filter_known_by(revisions, as_of_ts, key="created_at")

    # 前置写检:任何 LLM 花费之前先确认账本可写。
    # 放在这里而不是端点同步路径 —— 端点已经 202 早返回,真正开跑是后台任务;
    # 若只在端点查,查完到后台开跑之间又有窗口。这里是最贴近"即将花钱"的位置。
    await assert_writable(db)

    # 行情装配:单点调用(视角内装配有竞态),落在视角 prompt 之前才能被
    # 三视角看到;门控写入由 assemble_market_material 内部完成。
    # 读面③:回放模式不装配(理由见 docstring),prompt 不出现行情段。
    material = (
        None
        if as_of_ts is not None
        else await assemble_market_material(db, question.get("anchor"), question_id)
    )

    results = await asyncio.gather(
        *[
            _run_perspective(
                db,
                settings_service,
                question,
                revisions,
                p,
                material=material,
                as_of=as_of,
            )
            for p in PERSPECTIVES
        ]
    )

    # 视角事件已在 _run_perspective 落过;回填事件 id:按 detail.perspective 分组取各自最新一条
    ok_results: list[dict] = []
    missing: list[str] = []
    for p, res in zip(PERSPECTIVES, results):
        if res["status"] == "ok":
            ok_results.append({**res, "perspective": p["label"]})
        else:
            missing.append(str(p["label"]))
    all_events = await repo.list_events(question_id, kind="investigate_perspective")
    by_key: dict[str, int] = {}
    for e in all_events:
        k = e.get("detail", {}).get("perspective")
        if k:
            by_key[k] = int(e["id"])  # 升序遍历,同 key 后者覆盖 = 最新一条
    perspective_event_ids = [by_key.get(str(p["key"]), -1) for p in PERSPECTIVES]

    # 主持人:候选集 = 三视角候选集并集 ∪ 回答链 id(在响应契约里只落 id)
    all_candidates = await recall(
        db,
        question_id=question_id,
        kinds=LEDGER_KINDS,
        require_class=None,
        limit=LEDGER_LIMIT,
        purpose="investigate",
        as_of=as_of,
    )
    candidate_ids = {c["id"] for c in all_candidates} | {r["id"] for r in revisions}
    latest = revisions[-1] if revisions else None
    fallback_cited = [latest["id"]] if latest else []

    host_prompt = _build_host_prompt(
        question, revisions, ok_results, missing, as_of=as_of
    )
    host_system = (
        "你是量化研究团队的多视角调查会主持人。三位视角研究员(经验派/记录派/"
        "挑战派)已各自给出分析,你需要综合他们、指出矛盾、写出下一版正式回答。"
        "简洁、可证伪。"
    )
    body, cited = await _cited_with_retry(
        settings_service,
        prompt=host_prompt,
        system=host_system,
        max_tokens=HOST_MAX_TOKENS,
        candidate_ids=candidate_ids,
        fallback_cited=fallback_cited,
    )
    if not body:
        raise HTTPException(status_code=503, detail="LLM 未配置或调用失败,无法自动调查")

    rationale = ["multi-perspective council"] + [
        f"perspective-event:{eid}" for eid in perspective_event_ids
    ]
    if as_of:
        # 回放 provenance:这版回答是"以 as_of 为认知边界的调查"产的,
        # 落库时刻是 now(不 backdate)。前端按此前缀渲染"回放" chip。
        rationale.append(f"as_of_replay:{as_of}")
    revision = await repo.add_revision(
        question_id,
        body,
        actor="selene",
        rationale=rationale,
        cited=cited,
    )

    # 新问题:主持人提议,落库待拍板。不自动 investigate/close(红线)。
    new_questions: list[dict] = []
    titles = _parse_new_questions(body)
    if titles:
        parent_anchor = question.get("anchor") or {}
        for title in titles:
            child_id = await repo.create(
                title,
                body="",
                anchor=parent_anchor,
                created_by="selene",
                parent_question_id=question_id,
            )
            new_questions.append({"id": child_id, "title": title})
    else:
        logger.warning(
            "investigate: 主持人输出未解析出【新问题】条目,正文照常落 revision"
        )

    return {
        "revision": revision,
        "citation_migration": await repo.citation_migration(question_id),
        "council": {
            "perspectives": perspective_event_ids,
            "new_questions": new_questions,
        },
        "question_id": question_id,
    }


async def run_council_background(
    db, settings_service, question_id: str, as_of: str | None = None
) -> None:
    """P3 后台包装:跑完整调查会,无论成败落一条 investigate_finished 事件。

    前端轮询 detail 看到该事件即知道本轮结束(ok/failed);异常不外抛
    (后台任务没有接收方,吞掉只留事件+日志)。
    council as_of:as_of 透传给 run_council,且**成败都留痕** —— 失败分支
    的 finished detail 同样带 as_of,回放调查的失败轮也必须可辨。
    """
    try:
        result = await run_council(db, settings_service, question_id, as_of=as_of)
        detail = {
            "status": "ok",
            "revision_id": result["revision"]["id"],
            "new_questions": result["council"]["new_questions"],
            # 本轮引用迁移(新增/丢弃/保留);run_council 已算好,此处必须带出,
            # 否则前端拿不到 —— POST 已 202 化,事件是唯一通路
            "citation_migration": result["citation_migration"],
        }
    except HTTPException as e:
        detail = {"status": "failed", "error": str(e.detail)}
    except Exception as e:  # noqa: BLE001 — 后台任务必须自己收尾
        logger.exception("investigate background task crashed: %s", question_id)
        detail = {"status": "failed", "error": str(e)}
    if as_of:
        detail["as_of"] = as_of
    repo = ResearchQuestionRepository(db)
    try:
        await repo.append_event(
            question_id,
            actor="selene",
            kind="investigate_finished",
            detail=detail,
        )
    except Exception:  # noqa: BLE001
        # 收尾事件是前端轮询的收敛信号(见 research_graph 的 investigate 端点
        # docstring)。写不进去时**不能让它抛出去** —— 后台任务没有接收方,
        # 抛了就只剩日志,前端会一直轮询到上限,表现为"卡住"而非"失败"。
        # 这里吞掉并留 error 日志;前置写检(assert_writable)已尽力在开跑前
        # 挡住这类情况,此处是最后一道兜底。
        logger.exception(
            "investigate 收尾事件写入失败(前端将轮询到上限): %s | detail=%s",
            question_id,
            detail,
        )
