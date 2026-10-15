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
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_MAX_RECALL = 3

_SEED_FILE = Path(__file__).parent / "research_findings_seed.json"


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

    ``recall`` 是 selene_space_storage.recall_error_replay(异步);message
    约定为 hypothesis id,取不到该假设时返回 None(prompt 不带这段)。
    Abandoned(20 连败熔断)时注入固定提示,让上层知道该假设已放弃。
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

    async def recall(self, message: str) -> str | None:
        from selene_space_storage.repositories.research_graph.error_replay import (
            render_error_replay,
        )

        hypothesis_id = message.strip()
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
                "[error-replay] 该假设已触发 20 连败熔断:不再自动回放失败史,"
                "请考虑升级人工评审或换研究路线。"
            )
        if result is None:
            return None
        text = render_error_replay(
            result,
            hypothesis_statement=str(hypothesis.get("statement", "")),
            max_chars=self._max_chars,
        )
        return text or None


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
