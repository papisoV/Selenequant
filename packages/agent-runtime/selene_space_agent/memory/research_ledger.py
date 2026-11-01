"""把研究图谱里的历史结论接进对话召回。

前身 ``ResearchFindingsMemory`` 只读一份三条例子的 seed JSON —— 对话里说过的
"我们研究过什么"和存储层真正积累的研究账本没有关系。这里补上第二路:从
用户消息里认出 anchor(策略 / 周期 / 市场状态),再交给存储层的
``recall()`` 做跨问题召回。

**为什么不自己写关键词搜索**:``recall()`` 是分层的唯一载体 —— 走得到
Episode/Evidence 的标 ``learned_experience``,走不到的标 ``research_record``,
每个候选都带 ``matched_on``。另写一套子串匹配会立刻产生第二个"什么叫相似"
的定义,而且会把待检验线索冒充成既有知识。

**认不出 anchor 就不召回**。``recall()`` 的 v0 纪律是"匹配不上的宁可不召回";
这里同理 —— 没有可解释的锚点时不猜,返回空串,对话就不会多出一段看起来
有依据、实则随机命中的内容。

存储依赖(main.py 注入):
- ``recall``:``selene_space_storage.repositories.research_graph.recall``
- ``db_getter``:返回异步库句柄的零参函数(运行时才拿,避免启动顺序耦合)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: 单次注入的候选上限。账本段挤在 system prompt 里,条数比精度更伤。
_MAX_ITEMS = 6
#: 每条候选正文的截断长度。
_MAX_ITEM_CHARS = 220

#: 中文周期写法 → 账本里的周期字面量(与立问页写入的 anchor 对齐)。
_INTERVAL_ALIASES: dict[str, str] = {
    "日线": "1d",
    "4小时": "4h",
    "4小时线": "4h",
    "1小时": "1h",
    "1小时线": "1h",
    "30分钟": "30m",
    "15分钟": "15m",
    "5分钟": "5m",
    "1分钟": "1m",
}

#: 中文市场状态 → ``MarketRegime`` 的取值。只收不会歧义的说法。
_REGIME_ALIASES: dict[str, str] = {
    "牛市": "bull",
    "熊市": "bear",
    "震荡市": "sideways",
    "横盘": "sideways",
    "转换期": "transition",
}


def _known_strategy_ids() -> list[str]:
    """策略模板 id。导入失败(精简安装)时退化为空表 —— 少认一类锚点而已。"""
    try:
        from selene_space_strategy.factory import STRATEGY_TEMPLATES

        return list(STRATEGY_TEMPLATES)
    except Exception:  # pragma: no cover - 依赖缺失时的降级路径
        logger.debug("strategy templates unavailable for anchor inference")
        return []


def _known_intervals() -> list[str]:
    try:
        from selene_space_strategy.optimizer import INTERVAL_PERIODS_PER_YEAR

        return list(INTERVAL_PERIODS_PER_YEAR)
    except Exception:  # pragma: no cover
        return list(_INTERVAL_ALIASES.values())


def _known_regimes() -> list[str]:
    try:
        from selene_space_strategy.fusion.ensemble import MarketRegime

        return [r.value for r in MarketRegime]
    except Exception:  # pragma: no cover
        return list(_REGIME_ALIASES.values())


def _token_hit(text: str, token: str) -> bool:
    """整词命中(区分 ``bb`` 与 ``bb_v3``,也不让 ``1d`` 命中 ``21d``)。"""
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def infer_anchor(message: str) -> dict[str, list[str]]:
    """从自由文本里认出可解释的锚点;一类都没认出就返回空 dict。

    只认枚举得出来的词汇:策略模板 id、周期字面量、市场状态枚举。这不是
    语义理解 —— 它是"消息里出现了这个已知名字"的可审计陈述,与
    ``recall()`` 的 ``matched_on`` 一一对应。
    """
    if not message:
        return {}
    lower = message.lower()
    anchor: dict[str, list[str]] = {}

    strategies = [s for s in _known_strategy_ids() if _token_hit(lower, s)]
    if strategies:
        anchor["strategy_ids"] = sorted(set(strategies))

    intervals = {i for i in _known_intervals() if _token_hit(lower, i)}
    intervals |= {
        target for alias, target in _INTERVAL_ALIASES.items() if alias in message
    }
    if intervals:
        anchor["intervals"] = sorted(intervals)

    regimes = {r for r in _known_regimes() if _token_hit(lower, r)}
    regimes |= {target for alias, target in _REGIME_ALIASES.items() if alias in message}
    if regimes:
        anchor["regimes"] = sorted(regimes)

    return anchor


def _clip(text: str) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= _MAX_ITEM_CHARS:
        return collapsed
    return collapsed[:_MAX_ITEM_CHARS].rstrip() + "…"


def _render(candidate: dict[str, Any]) -> str:
    """一个候选 → 一行。标签带召回分层,消费方据此区分知识与线索。"""
    recall_class = candidate.get("recall_class") or "research_record"
    kind = candidate.get("kind") or "?"
    created = str(candidate.get("created_at") or "")[:10]
    matched = ",".join(candidate.get("matched_on") or [])
    suffix = f"[{kind} {recall_class} {created}; 命中 {matched}]"
    if candidate.get("superseded"):
        suffix += "[已被证伪/退役]"
    return f"- {suffix} {_clip(candidate.get('text', ''))}"


class ResearchLedgerRecall:
    """对话侧的研究账本召回:认 anchor → 存储层 recall → 渲染成 prompt 段。"""

    def __init__(
        self,
        *,
        recall: Callable[..., Any],
        db_getter: Callable[[], Any],
        max_items: int = _MAX_ITEMS,
    ) -> None:
        self._recall = recall
        self._db_getter = db_getter
        self._max_items = max_items

    async def recall(self, message: str) -> str:
        anchor = infer_anchor(message)
        if not anchor:
            return ""
        db = self._db_getter()
        if db is None:
            return ""
        try:
            candidates = await self._recall(
                db,
                anchor=anchor,
                purpose="investigate",
                limit=self._max_items,
            )
        except Exception:
            # 召回失败不该让对话失败 —— memory 是增强,不是依赖。
            logger.debug("research ledger recall failed", exc_info=True)
            return ""
        if not candidates:
            return ""
        return "\n".join(_render(c) for c in candidates)
