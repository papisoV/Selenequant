"""失败经验回放召回 —— rd-agent error_query 的单层扁平移植(internal-survey 7.2)。

 Portions derived from rd-agent (https://github.com/microsoft/rd-agent),
 Copyright (c) Microsoft Corporation — MIT License.
 本文件为机制语义的独立表达:默认参数(1 对/3 条/20 连败)、退化剪枝语义、
 analyze_error 两级抽取取自上述项目;SQL/数据结构为 Selene 原生实现。

三路召回,rd-agent 执行顺序 former_trace → component → error,
prompt 注入优先级相反:error 回放最高。本模块只做前两路新写 +
熔断;第三路(相似成功)复用现有 recall() 的 anchor 重叠,零新代码
(遵守 recall.py:16 "不上 embedding" 的 v0 纪律)。

对 rd-agent(微软,MIT License)机制语义的移植:
- 默认参数:同错回放 1 对 / 失败史 3 条 / 20 连败熔断(CoSTEER/config.py)
- 退化剪枝:"值检查错→执行错"相邻对 = 修复走反,这段历史不喂模型
  (KM:563-571 注释原文 "deterioration of the trial")
- 写入时机改进:错误关联失败时就写(rd-agent 要等成功才固化),
  放弃史的 error occurrence 计数同样沉淀。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

FAIL_TASK_TRIAL_LIMIT = 20
N_ERROR_PAIRS = 1
N_FORMER_FAILS = 3

# 执行错误:traceback 最后一帧(照抄 rd-agent analyze_error 的两级抽取语义)
_TRACEBACK_RE = re.compile(
    r'File "(?P<file>.+)", line (?P<line>\d+), in (?P<function>.+)\n'
    r"\s+(?P<error_line>.+)\n(?P<error_type>\w+): (?P<error_message>.+)"
)

# 值检查错误白名单:rd-agent 原版 5 条针对其 GT 比对器;Selene 场景换成
# 回测/数据校验错误文本,命中什么存什么,匹配不到进 Undefined 兜底桶。
_VALUE_CHECK_PATTERNS = re.compile(
    r"|".join(
        re.escape(p)
        for p in (
            "回测区间与数据窗口不一致",
            "信号索引与行情索引不一致",
            "指标值与重算值超出容差",
            "前瞻窗口穿越数据末端",
            "多级索引列命名错误",
        )
    )
)

UNDEFINED_ERROR = "Undefined Error"

# CoSTEER 三层(rd-agent CoSTEERSingleFeedback):return_checking=跑通但
# 结果不合预期;execution=跑崩;code=代码质量问题。rd-agent 用 LLM 判层,
# Selene 用规则:白名单→return_checking,traceback→execution,
# "跑通但断言/指标不达标"→code,兜底 undefined。
ERROR_LAYERS = ("return_checking", "execution", "code", "undefined")

# 自检不达标信号(无 traceback,反馈在报告指标而非异常栈)→ code 层:
# rd-agent 的"代码质量"层在 Selene 无 GT 比对器的场景下收自评反馈。
# 中文条目 = 回测自检;英文条目 = T4 验证协议 verdict(_decide)的稳定
# 子串 —— 实战桥接发现 5 条真实失败全落 undefined 兜底后补齐。
_CODE_LAYER_PATTERNS = re.compile(
    r"|".join(
        re.escape(p)
        for p in (
            "指标不达标",
            "断言失败",
            "低于随机基线",
            "IC 不显著",
            "回测失败:未产生有效信号",
            "degenerate output",
            "fewer than 2 finite OOS folds",
            "OOS Rank-IC",
            "invalid RPN formula",
            "subinterval agreement",
            "multi-symbol agreement",
            "cost stress",
            "DSR",
        )
    )
)


def classify_error(feedback_text: str) -> list[tuple[str, str]]:
    """反馈文本 → (错误内容, 层) 对;层取值见 ``ERROR_LAYERS``。

    判层顺序 = rd-agent analyze_error 的抽取顺序 + CoSTEER 三层语义:
    值检查白名单 → return_checking;traceback → execution;
    自检不达标文本(无 traceback)→ code;兜底 → undefined。
    """
    contents = [m.group(0) for m in _VALUE_CHECK_PATTERNS.finditer(feedback_text)]
    if contents:
        return [(c, "return_checking") for c in contents]
    m = _TRACEBACK_RE.search(feedback_text)
    if m:
        return [
            (
                f"ErrorType: {m['error_type']}\nError line: {m['error_line']}",
                "execution",
            )
        ]
    if _CODE_LAYER_PATTERNS.search(feedback_text):
        return [(feedback_text.strip(), "code")]
    return [(UNDEFINED_ERROR, "undefined")]


def analyze_error(feedback_text: str) -> list[str]:
    """反馈文本 → 错误内容字符串列表(v017 两级语义:白名单 → traceback → 兜底)。

    复用 classify_error 的抽取序,code 层不进旧版召回,落回兜底桶 ——
    与 v017 行为逐字一致,旧调用方零感知。
    """
    contents = [c for c, layer in classify_error(feedback_text) if layer != "code"]
    return contents or [UNDEFINED_ERROR]


def is_value_check_error(error_contents: list[str]) -> bool:
    """白名单命中 = 值检查错;traceback/兜底 = 执行错。退化剪枝用。"""
    return bool(
        error_contents
        and all(
            c != UNDEFINED_ERROR and not c.startswith("ErrorType:")
            for c in error_contents
        )
    )


@dataclass(frozen=True)
class ErrorPair:
    """ "同错误失败代码 + 后来改对的代码"成对回放的单元。"""

    error_content: str
    failed_experiment_id: str
    success_experiment_id: str
    failed_config: dict[str, Any]
    success_config: dict[str, Any]


@dataclass(frozen=True)
class Abandoned:
    """20 连败熔断:不再喂知识,该假设升级人工或换路线。"""

    fail_count: int
    reason: str = "fail_task_trial_limit reached"


@dataclass(frozen=True)
class ErrorReplayResult:
    error_pairs: list[ErrorPair] = field(default_factory=list)
    former_fails: list[dict[str, Any]] = field(default_factory=list)
    latest_attempt: dict[str, Any] | None = None


def prune_deterioration(fails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[值检查错, 执行错] 相邻对 → 删后者(修复方向走反,不喂模型)。

    照抄 rd-agent former_trace_query 的剪枝语义;错误内容从
    error_ids_json 反查不在本层 —— 尝试行约定携带 ``_error_contents``
    (召回前由调用方 hydrate)。
    """
    out: list[dict[str, Any]] = []
    for cur in fails:
        if (
            out
            and is_value_check_error(out[-1].get("_error_contents", []))
            and not is_value_check_error(cur.get("_error_contents", []))
        ):
            continue
        out.append(cur)
    return out


async def recall_error_replay(
    db: Any,
    hypothesis_id: str,
    *,
    experiments: Any,
    edges: Any,
    error_types: Any,
    fail_task_trial_limit: int = FAIL_TASK_TRIAL_LIMIT,
    n_error_pairs: int = N_ERROR_PAIRS,
    n_former_fails: int = N_FORMER_FAILS,
) -> ErrorReplayResult | Abandoned:
    """按假设召回失败经验。任何调用方拿结果后自行渲染 prompt。"""
    fail_count = await experiments.count_outcome(hypothesis_id, "failed")
    if fail_count >= fail_task_trial_limit:
        return Abandoned(fail_count=fail_count)

    # ── 路 1:同错误回放(优先级最高,放 prompt 最前)────────────────
    # 一条 SQL:最后一条失败的 error_ids ∩ resolved_by 边两端实验,
    # 根除 rd-agent "error→trace→desc→success" 三跳遍历的残留变量坑。
    last_failed_rows = await db.execute(
        """
        SELECT e.id, e.config_json, e.error_ids_json
        FROM research_experiments e
        WHERE e.hypothesis_id = ? AND e.outcome = 'failed'
        ORDER BY e.created_at DESC, e.rowid DESC LIMIT 1
        """,
        (hypothesis_id,),
    )
    error_pairs: list[ErrorPair] = []
    if last_failed_rows:
        last_failed = dict(last_failed_rows[0])
        error_ids = _load_ids(last_failed.get("error_ids_json"))
        if error_ids:
            rows = await db.execute(
                """
                SELECT f.id AS fail_id, f.config_json AS fail_cfg,
                       s.id AS success_id, s.config_json AS success_cfg,
                       f.error_ids_json AS fail_err_ids
                FROM research_edges e
                JOIN research_experiments f
                  ON f.id = e.from_id AND e.from_type = 'experiment'
                JOIN research_experiments s
                  ON s.id = e.to_id AND e.to_type = 'experiment'
                WHERE e.kind = 'resolved_by' AND e.to_type = 'experiment'
                  AND f.outcome = 'failed' AND s.outcome = 'success'
                  AND e.id NOT IN (
                      SELECT to_id FROM research_edges WHERE kind = 'revokes'
                  )
                ORDER BY s.created_at DESC
                LIMIT 200
                """,
            )
            for row in rows:
                row_err_ids = _load_ids(row["fail_err_ids"])
                hit = [eid for eid in error_ids if eid in row_err_ids]
                if not hit:
                    continue
                error_content = await _content_of(error_types, hit[0])
                error_pairs.append(
                    ErrorPair(
                        error_content=error_content,
                        failed_experiment_id=row["fail_id"],
                        success_experiment_id=row["success_id"],
                        failed_config=_load_cfg(row["fail_cfg"]),
                        success_config=_load_cfg(row["success_cfg"]),
                    )
                )
                if len(error_pairs) >= n_error_pairs:
                    break

    # ── 路 2:自身失败史 + 退化剪枝 ─────────────────────────────────
    # created_at 是秒精度,同秒的尝试排序必须看插入序(rowid),
    # 否则"相邻对"的语义会随机翻转(EvidenceRepository 同款坑)。
    fail_rows = await db.execute(
        """
        SELECT * FROM research_experiments
        WHERE hypothesis_id = ? AND outcome = 'failed'
        ORDER BY created_at ASC, rowid ASC
        """,
        (hypothesis_id,),
    )
    fails = [dict(r) for r in fail_rows]
    for f in fails:
        f["config"] = _load_cfg(f.get("config_json"))
        f["_error_contents"] = await _contents_of_ids(
            error_types, _load_ids(f.get("error_ids_json"))
        )
    pruned = prune_deterioration(fails)
    former_fails = pruned[-n_former_fails:] if pruned else []
    latest_attempt = None
    if fails and (not former_fails or fails[-1]["id"] != former_fails[-1]["id"]):
        latest_attempt = fails[-1]

    return ErrorReplayResult(
        error_pairs=error_pairs,
        former_fails=former_fails,
        latest_attempt=latest_attempt,
    )


def render_error_replay(
    result: ErrorReplayResult,
    *,
    hypothesis_statement: str = "",
    max_chars: int | None = None,
) -> str:
    """召回结果 → prompt 注入文本(照 rd-agent prompts.yaml 结构,internal-survey 2.5)。

    渲染顺序 = 注入优先级:同错误回放 > 失败史 > 最新失败提醒。
    (相似成功一路走现有 recall(),由调用方拼在其后,本函数不管。)

    超长削减顺序照抄报告 7.2:最新失败提醒 → 失败史头部 → error 对尾部。
    error 对优先级最高,最后才丢。
    """
    header = [
        "--------------Target hypothesis:---------------",
        hypothesis_statement,
    ]
    error_blocks: list[list[str]] = []
    for pair in result.error_pairs:
        error_blocks.append(
            [
                "",
                "Recall your last failure, your implementation met some errors.",
                "When doing other tasks, you met some similar errors but you "
                "finally solve them. Here are some examples:",
                f"--------------Similar error ({pair.error_content}):---------------",
                "=====Failed code with this error:=====",
                str(pair.failed_config.get("code", "")),
                "=====Success code that resolved it:=====",
                str(pair.success_config.get("code", "")),
            ]
        )
    former_blocks: list[list[str]] = []
    for f in result.former_fails:
        cfg = f.get("config") or _load_cfg(f.get("config_json"))
        former_blocks.append(
            [
                "",
                f"=====Attempt {f.get('id', '?')}=====",
                str(cfg.get("code", "")),
            ]
        )
    latest_block: list[str] | None = None
    if result.latest_attempt is not None:
        cfg = result.latest_attempt.get("config") or _load_cfg(
            result.latest_attempt.get("config_json")
        )
        latest_block = [
            "",
            "You tried to correct your former failed code but still met errors. "
            "Do not repeat it:",
            "=====Latest attempt=====",
            str(cfg.get("code", "")),
            "=====Feedback=====",
            str(cfg.get("feedback", "")),
        ]

    def _join(sections: list[list[str]]) -> str:
        return "\n".join(line for sec in sections for line in sec)

    sections = [header, *error_blocks, *former_blocks]
    if latest_block is not None:
        sections.append(latest_block)
    if max_chars is None or len(_join(sections)) <= max_chars:
        return _join(sections)

    n_err = len(error_blocks)
    if latest_block is not None:
        sections.pop()
    while len(_join(sections)) > max_chars and len(sections) > 1 + n_err:
        sections.pop(1 + n_err)  # 失败史从头部丢(最老的最先丢)
    while len(_join(sections)) > max_chars and len(sections) > 1:
        sections.pop()  # error 对从尾部丢
    return _join(sections)


def _load_ids(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return loaded if isinstance(loaded, list) else []


def _load_cfg(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


async def _content_of(error_types: Any, error_id: str) -> str:
    row = await error_types.get(error_id)
    return row["content"] if row else error_id


async def _contents_of_ids(error_types: Any, error_ids: list[str]) -> list[str]:
    return [await _content_of(error_types, eid) for eid in error_ids]
