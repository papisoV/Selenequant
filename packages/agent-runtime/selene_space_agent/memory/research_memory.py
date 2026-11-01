"""Recall preregistered research findings relevant to a user query.

Distilled NO-GO/GO verdicts from validation studies live in a seed JSON;
keyword substring recall surfaces them so the agent cites prior research
instead of repeating refuted blog claims.

ErrorReplayInjector (US-202) 是同文件的第二路记忆:把研究图谱里"同错误
失败→成功"的配对与自身失败史渲染成 prompt 注入。存储与召回都在
selene_space_storage(repositories/research_graph/error_replay.py),
本模块只做组合与防御 —— agent-runtime 不依赖 storage,依赖由调用方
(main.py)以参数注入。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_MAX_RECALL = 3

_SEED_FILE = Path(__file__).parent / "research_findings_seed.json"

#: 假设 id 字面量。UI 的"当前形态 · hyp-…"就显示这个,所以用户在对话里
#: 粘贴它是有据可依的输入路由,不是猜。
_HYPOTHESIS_ID_RE = re.compile(r"(?<![A-Za-z0-9_])hyp-[0-9a-f]{6,}(?![0-9A-Za-z_])")


def _extract_hypothesis_id(message: str) -> str | None:
    """从消息里找假设 id;找不到返回 None。"""
    match = _HYPOTHESIS_ID_RE.search(message or "")
    return match.group(0) if match else None


def _is_abandoned(result: Any) -> bool:
    """延迟 import 的 Abandoned 判定(避免模块级硬依赖 storage)。"""
    try:
        from selene_space_storage.repositories.research_graph.error_replay import (
            Abandoned,
        )
    except ImportError:
        return False
    return isinstance(result, Abandoned)


class ErrorReplayInjector:
    """把 error_replay 召回接到 ReAct 的 ``error_replay_recall`` 钩子上。

    ``recall`` 是 selene_space_storage.recall_error_replay(异步)。假设 id
    有两种来路:调用方显式传入(``hypothesis_id=``,结构化路由),或从
    对话消息里认出 ``hyp-…`` 字面量 —— 后者是用户唯一达得到这条记忆的
    手边路径(研究账本页会显示 id)。都拿不到时返回 None,prompt 不带这段。

    ⚠️ 不能用 ``message.strip()`` 当 id:ReAct 传进来的 message 可能是
    ``_enrich_message`` 之后的多行文本(前面拼了 UI 上下文),整串做 id
    永远查不到,注入会静默地从不生效。
    """

    def __init__(
        self,
        *,
        hypotheses: Any,
        experiments: Any,
        edges: Any,
        error_types: Any,
        recall: Callable[..., Any],
        max_chars: int = 6000,
    ) -> None:
        self._hypotheses = hypotheses
        self._experiments = experiments
        self._edges = edges
        self._error_types = error_types
        self._recall = recall
        self._max_chars = max_chars

    async def recall(
        self, message: str, *, hypothesis_id: str | None = None
    ) -> str | None:
        from selene_space_storage.repositories.research_graph.error_replay import (
            render_error_replay,
        )

        hypothesis_id = hypothesis_id or _extract_hypothesis_id(message)
        if not hypothesis_id:
            return None
        hypothesis = await self._hypotheses.get(hypothesis_id)
        if hypothesis is None:
            return None
        result = await self._recall(
            self._experiments.db,
            hypothesis_id,
            experiments=self._experiments,
            edges=self._edges,
            error_types=self._error_types,
        )
        if _is_abandoned(result):
            return (
                "- [error-replay] 该假设已触发 20 连败熔断:不再自动回放失败史,"
                "请考虑升级人工评审或换研究路线。"
            )
        if result is None:
            return None
        text = render_error_replay(
            result,
            hypothesis_statement=str(hypothesis.get("statement", "")),
            max_chars=self._max_chars,
        )
        if not text:
            return None
        # 渲染体是代码块,不能逐行加前缀(会把代码弄坏)。只在前面加一条
        # ``- [`` 摘要行:ReAct 的"已参考 N 条"数的是行首 ``-``,不标这一下,
        # 回放了失败史却报 0 条。
        return f"- [错误回放 · 假设 {hypothesis_id} 的失败史与解决配对]\n{text}"


class ResearchFindingsMemory:
    """Keyword-substring recall over distilled research findings."""

    def __init__(self, seed_path: Path | None = None) -> None:
        path = seed_path if seed_path is not None else _SEED_FILE
        self._entries: list[dict[str, Any]] = self._load(path)

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get("entries", [])
            return [e for e in entries if isinstance(e, dict)]
        except (OSError, json.JSONDecodeError):
            logger.warning("research findings seed unreadable: %s", path)
            return []

    def recall(self, query: str) -> str:
        """Return up to 3 findings whose keywords appear in the query.

        Empty string when nothing matches — the Memory Context prompt
        section is omitted entirely in that case.
        """
        if not query or not self._entries:
            return ""
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in self._entries:
            hits = sum(1 for kw in entry.get("keywords", []) if kw and kw in query)
            if hits > 0:
                scored.append((hits, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        lines: list[str] = []
        for _, entry in scored[:_MAX_RECALL]:
            lines.append(
                f"- [{entry.get('verdict', '?')} {entry.get('date', '?')}] "
                f"{entry.get('claim', '')} → {entry.get('evidence', '')} "
                f"(详见 {entry.get('findings_path', '')})"
            )
        return "\n".join(lines)
