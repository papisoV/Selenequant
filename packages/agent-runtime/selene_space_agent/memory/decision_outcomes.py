"""Decision → outcome → lesson closed loop (B1) + 5-tier rating parser (B2).

学 ta-core memory.py 两阶段模式: AI 部署策略时 ``record_pending`` 打 pending
标记 → 之后(手动触发 scripts/backfill_outcomes.py)回填实绩与 LLM 反思 →
``recall`` 按关键词两级注入: 同 symbol 命中给全文(cap 5), 仅跨 symbol 命中给
反思文本(cap 3)。

纯存储设计(plan v3 定案): 本模块无 LLM 调用——reflection_text 由外部喂入,
LLM 反思只存在于 scripts/backfill_outcomes.py。存储对齐 strategy_failures.py
约定(~/.selenespace/memory/ 一个 JSON, FIFO 50)。

B2 解析器最初逐字抄自 open-source-reference 旧版 ``agents/utils/rating.py``;后按上游
v0.6.0 ``agents/rating.py`` 重写为完整性版本(见下 B2 段注释)。
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from selene_space_core.infra.atomic_write import atomic_write
from selene_space_core.infra.file_lock import FileLock

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 50
_SAME_SYMBOL_CAP = 5
_CROSS_SYMBOL_CAP = 3

# 并发写只发生在多个进程之间(服务进程 + 回填脚本),故锁需跨进程;同进程的
# 同步读改写不会与 asyncio 事件循环交错。短暂争用等一会,长时间占用才 fail-loud。
_LOCK_TIMEOUT_SECONDS = 2.0
_LOCK_POLL_SECONDS = 0.02


def _utc_now_iso() -> str:
    """当前 UTC 时刻的 ISO8601 字符串(带时区偏移,跨时区可无歧义比较)。"""
    return datetime.now(timezone.utc).isoformat()


_DEFAULT_MEMORY_DIR = Path.home() / ".selenespace" / "memory"
_MEMORY_FILE = "decision_outcomes.json"


# ─── B2: 5-tier rating vocabulary + two-pass parser ─────────────────
#
# 吸收自 open-source-reference v0.6.0 ``agents/rating.py``(Apache-2.0)。旧版解析器
# (逐字抄来的那版)会把"引用别家的评级"当成自己的评级、把正文里被否定的评级词
# ("not a Sell")读成 Sell、把回显的图例("Rating Scale: Buy, ...")读成 Buy,
# 且在读不出评级时静默降级为 Hold——一个没人能读懂的决策不是 Hold,把它记成
# Hold 会被下一次运行当成一个从未做出的判断引回去(TA #1170)。上游三处修复
# (#1170/#1435/#1466)在此落地:读不出即 REVIEW 哨兵,绝不伪装成可交易档位。


RATINGS_5_TIER: tuple[str, ...] = (
    "Buy",
    "Overweight",
    "Hold",
    "Underweight",
    "Sell",
)

# 读不出可识别评级时发出的信号。它不是可交易档位——它标记"这段输出需要人/
# 重跑",而不是静默降级成 Hold。调用方若要把信号映射到五档枚举,须先用
# ``is_review`` 拦住。
RATING_REVIEW = "REVIEW"

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# 匹配 "Rating: X" / "rating - X" / "Rating — **X**" — 容忍 markdown 粗体与
# 模型写出的任意破折号/冒号分隔符。"rating" 必须是一个词的开头,故
# "Operating margin: Sell-side" 不会被当成标签。
_RATING_LABEL_RE = re.compile(
    r"(?<![a-z])rating\b[^:\-\u2010-\u2015]*[:\-\u2010-\u2015][\s*]*(\w+)",
    re.IGNORECASE,
)

# 决策自身的评级行,形如 Portfolio Manager 被要求抬头写的
# ("- **Rating**: X", "**Final Rating**: X", "## Our rating - X"):可选列表
# 标记、强调符号与标题号,且 "rating" 之前只允许指向"本决策自身"的词。
# "Consensus rating: Buy" 或 "Trader's rating: Buy" 是别人的评级。
_OWN_QUALIFIER = (
    r"(?:(?:final|our|overall|my|recommended|updated|revised|new|current)\s+)*"
)
_RATING_LINE_RE = re.compile(
    r"\s*(?P<item>(?:[-+*\u2022]|\d+[.)])\s+)?[\s*_#]*"
    + _OWN_QUALIFIER
    + r"rating[^\w:\-\u2010-\u2015]*[:\-\u2010-\u2015][\s*]*(?P<value>\w+)",
    re.IGNORECASE,
)

# 呈现量表本身而非决策的行("Rating Scale: Buy, ...")。
_RATING_SCALE_RE = re.compile(r"rating\s*(scale|options|legend)", re.IGNORECASE)


def extract_rating(text: str) -> str | None:
    """从其标签提取五档评级,没有则返回 ``None``。

    读显式 "Rating: X" 标签(容忍 markdown 粗体),在 NFKC 规范化后的文本上
    进行,故全角标点 ``Rating：Overweight`` 与 ASCII 一样匹配:先取决策的开头
    评级行,否则取其自身评级行,再否则取所有标签——前提是它们一致。
    """
    if not text:
        return None
    norm = unicodedata.normalize("NFKC", text)

    # 决策被要求以评级开头的,故首行(跳过标题后)若是该形状即为本次判断。该处
    # 的列表项也可能是在列举别家的评级,故它与决策自身的其余评级行一并计数
    # (后者不含列表项);都不行时再让所有标签计数。无论哪条路径,它们都必须
    # 一致:彼此不同、又没有任何标记指出哪个才是本次判断的,就不是一次判断
    # (#1170)。呈现量表本身的行是模型回显的图例,不是判断。
    lines = [
        line
        for line in norm.splitlines()
        if line.strip() and not _RATING_SCALE_RE.search(line)
    ]
    first = next(
        (
            line
            for line in lines
            if not line.lstrip().startswith("#") or _RATING_LINE_RE.match(line)
        ),
        "",
    )
    m = _RATING_LINE_RE.match(first)
    opening = m if m and m.group("value").lower() in _RATING_SET else None
    if opening and not opening.group("item"):
        return opening.group("value").capitalize()

    own = [opening.group("value").capitalize()] if opening else []
    labels = []
    for line in lines:
        m = _RATING_LINE_RE.match(line)
        if m and not m.group("item") and m.group("value").lower() in _RATING_SET:
            own.append(m.group("value").capitalize())
        labels += [
            v.capitalize()
            for v in _RATING_LABEL_RE.findall(line)
            if v.lower() in _RATING_SET
        ]
    # 没有标签就没有可读的判断:正文里的评级词可能是它所要反驳的那个
    # ("not a Sell"),读它等于报告一个没人做出的方向。
    found = own or labels
    return found[0] if found and len(set(found)) == 1 else None


def parse_rating(text: str, default: str = RATING_REVIEW) -> str:
    """提取五档评级;决策没有评级时返回 ``REVIEW``。

    供每个决策都必须有字符串的调用方使用,例如账本条目的评级标签。默认值是
    review 哨兵,绝不是可交易档位。
    """
    rating = extract_rating(text)
    return rating if rating is not None else default


def is_review(signal: str) -> bool:
    """该信号是否为不可交易的 REVIEW 哨兵(#1170)。"""
    return signal == RATING_REVIEW


def human_view_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """把一条账本记录投影成人类可读面所需的不可变副本(B2 端到端)。

    返回新 dict,不改原记录。补两件事,让"REVIEW 哨兵"能一路走到用户眼前:

    1. ``rating`` 归一到五档之一或 ``REVIEW`` —— 空白/缺失一并变 ``REVIEW``,
       与 ``recall`` 同口径,免得前端把"未记录"当普通空值。
    2. ``is_review`` 布尔 —— 消费点(前端 badge / 报告头)凭它把哨兵渲染成
       醒目"需人看/重跑"警告,而不是与 Buy/Hold 长得一样。
    """
    rating = str(entry.get("rating") or RATING_REVIEW)
    return {**entry, "rating": rating, "is_review": is_review(rating)}


# ─── B1: DecisionOutcomeMemory ──────────────────────────────────────


class DecisionOutcomeMemory:
    """Two-phase decision log: pending tag at deploy, outcome backfill later."""

    def __init__(self, memory_dir: Path | None = None) -> None:
        self._dir = Path(memory_dir) if memory_dir is not None else _DEFAULT_MEMORY_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / _MEMORY_FILE
        self._lock_path = self._dir / f"{_MEMORY_FILE}.lock"
        self._entries: list[dict[str, Any]] = self._load(self._file)

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get("entries", [])
            return [e for e in entries if isinstance(e, dict)]
        except FileNotFoundError:
            return []  # 首次写入前没有文件,不是错误。
        except (OSError, json.JSONDecodeError):
            logger.warning("decision outcomes memory unreadable: %s", path)
            return []

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold the log across a read-modify-write, re-reading the file first.

        每个调用点各自 ``new`` 一个实例,``_entries`` 因此只是构造那一刻的快照:
        两个写者若各自在自己的快照上改再整file覆写,后者会静默吞掉前者的条目。
        故每个写操作先跨进程取锁,再重读当前文件,改的是最新内容,写回走原子替换
        —— 并发写者的条目得以存活,崩溃也不会留下半截 JSON。
        """
        lock = FileLock(self._lock_path)
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        while True:
            try:
                lock.acquire()
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_LOCK_POLL_SECONDS)
        try:
            self._entries = self._load(self._file)
            yield
        finally:
            lock.release()

    def _rotate(self) -> None:
        """超出上限时只丢最旧的 **filled** 条目,pending 一律保留。

        pending 是"还没处理的工作",丢掉它等于让 ``pending_deployments()``
        永远列不出这条决策,回填再也轮不到 —— 静默永久丢失。只有已回填的条目
        才"旧"到可以牺牲。
        """
        if len(self._entries) <= _MAX_ENTRIES:
            return
        filled_idx = [
            i for i, e in enumerate(self._entries) if e.get("status") != "pending"
        ]
        n_pending = len(self._entries) - len(filled_idx)
        keep_filled = max(0, _MAX_ENTRIES - n_pending)
        drop_idx = set(filled_idx[: max(0, len(filled_idx) - keep_filled)])
        if not drop_idx:
            return
        self._entries = [e for i, e in enumerate(self._entries) if i not in drop_idx]

    def _persist(self) -> None:
        try:
            atomic_write(
                self._file,
                json.dumps({"entries": self._entries}, ensure_ascii=False, indent=2),
            )
        except OSError:
            logger.warning(
                "decision outcomes memory unwritable: %s", self._file, exc_info=True
            )

    def record_pending(
        self,
        deployment_id: str,
        strategy_id: str,
        symbols: list[str],
        entry_date: str,
        decision_text: str = "",
        rating: str = "",
    ) -> None:
        """Phase A: 打 pending 标记(deploy 成功时)。同 deployment 幂等覆盖。

        评级字段契约(B2): 落盘的 ``rating`` 恒为五档之一或 ``REVIEW`` 哨兵。
        调用方未显式给 ``rating`` 时,由 ``decision_text`` 经完整性解析器导出
        ——读不出即 ``REVIEW``,绝不留空、绝不伪装成 Hold。空串仅表示"未提供",
        落盘前一律归一,故下游 ``recall`` 不再出现 ``rating=?`` 的空档。
        """
        resolved_rating = rating or parse_rating(decision_text)
        entry: dict[str, Any] = {
            "deployment_id": deployment_id,
            "strategy_id": strategy_id,
            "symbols": list(symbols),
            "entry_date": entry_date,
            "rating": resolved_rating,
            "decision_text": decision_text[:400],
            "status": "pending",
            "outcome_metrics": None,
            "reflection_text": "",
            # [A] 回填写下反思的墙钟时刻(见 ``backfill``;非 TA 的出场 bar 日期)。
            # pending 期显式为 None —— 结局未知,不编造日期。旧记录缺此键,读作 None。
            "resolved_at": None,
        }
        with self._locked():
            self._entries = [
                e for e in self._entries if e.get("deployment_id") != deployment_id
            ]
            self._entries.append(entry)
            self._rotate()
            self._persist()

    def backfill(
        self,
        deployment_id: str,
        outcome_metrics: dict[str, Any],
        reflection_text: str,
        resolved_at: str | None = None,
    ) -> bool:
        """Phase B: 原子回填(pending 条目整体替换, 崩溃不留半条)。

        仅 pending 条目可回填; 已回填条目不可覆盖(重复触发防重放)。

        ``resolved_at`` 记录**本次反思写下的 UTC 时刻**(ISO8601 带时区)。
        缺省取当前 UTC —— 回填本就发生在写下反思之时,调用方无需知道确切时刻;
        测试可显式传入。

        ⚠️ 这**不是** open-source-reference 的 ``resolution_date``。那里记的是"结局变得
        可知"的**最后一根价格 bar 日期**(``settlement.py:97`` = ``exit_``),用作
        时点过滤的截止;此处记的是墙钟回填时刻,两者只在"回填晚于窗口结束"时才
        近似相等。当前数据链(``scripts/backfill_outcomes.py`` 的 slot_status 快照)
        **既无已实现收益也无出场日期**(``return_available=False``),没有可作真值
        的来源,故不假装知道"结果已知日"。

        用于 PIT 过滤时,本字段是"结局已知日"的**保守上界**(墙钟 ≥ 真值),过滤
        只会多排除、不会注入未来信息。逐字复刻 TA 的语义需先接上出场 bar 日期,
        与 N4(基准相对 alpha)同批补齐。
        """
        stamp = resolved_at or _utc_now_iso()
        with self._locked():
            for i, entry in enumerate(self._entries):
                if entry.get("deployment_id") == deployment_id:
                    if entry.get("status") != "pending":
                        return False
                    self._entries[i] = {
                        **entry,
                        "outcome_metrics": dict(outcome_metrics),
                        "reflection_text": str(reflection_text)[:600],
                        "status": "filled",
                        "resolved_at": stamp,
                    }
                    self._rotate()
                    self._persist()
                    return True
            return False

    def pending_deployments(self) -> list[str]:
        """scripts/backfill_outcomes.py 用的待回填清单。"""
        return [
            e["deployment_id"] for e in self._entries if e.get("status") == "pending"
        ]

    def recall(self, query: str) -> str:
        """两级召回(plan v3): 同 symbol 全文(cap 5) / 仅跨 symbol 反思(cap 3)。

        关键词打分照抄 strategy_failures.py:84-112 — query 是 react.py:135
        传入的用户 message, 不做 embedding。跨票教训取"已回填且未命中"
        的条目(ta-core 语义: 已有实绩的教训对本票分析仍有参考价值),
        故即使零命中也可能注入反思——但 pending 条目零命中时整体返回空。
        """
        if not query or not self._entries:
            return ""

        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in self._entries:
            keywords = {str(entry.get("strategy_id", ""))}
            keywords.update(str(s) for s in entry.get("symbols", []) if s)
            hits = sum(1 for kw in keywords if kw and kw in query)
            if hits > 0:
                scored.append((hits, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        lines: list[str] = []
        seen_ids: set[str] = set()
        for _, entry in scored[:_SAME_SYMBOL_CAP]:
            seen_ids.add(str(entry.get("deployment_id")))
            om = entry.get("outcome_metrics") or {}
            outcome = (
                f" outcome: {om.get('summary', '')}" if om else " outcome: pending"
            )
            # 旧记录可能落盘时 rating 为 ""(接线前);读取时同样归一成 REVIEW,
            # 免得把"未记录"渲染成 "rating=?" 让下游误以为字段缺失。
            entry_rating = entry.get("rating") or RATING_REVIEW
            line = (
                f"- [{entry.get('entry_date', '?')} {entry.get('strategy_id', '?')} "
                f"{'/'.join(entry.get('symbols', []))} rating={entry_rating}]"
                f"{outcome}"
            )
            reflection = str(entry.get("reflection_text", ""))
            if reflection:
                line += f" | {reflection}"
            lines.append(line)

        # 跨 symbol: 已回填且未被同票段注入 → 仅反思文本(cap 3)
        cross = [
            e
            for e in self._entries
            if e.get("status") == "filled"
            and str(e.get("deployment_id")) not in seen_ids
        ]
        for entry in cross[:_CROSS_SYMBOL_CAP]:
            lines.append(
                f"- [跨票教训 {entry.get('entry_date', '?')} "
                f"{'/'.join(entry.get('symbols', []))}] {entry.get('reflection_text', '')}"
            )

        return "\n".join(lines)
