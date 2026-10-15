"""Input/output content scoping guard for the agent pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class GuardAction(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"


class MessageTopic(str, Enum):
    GENERAL = "general"
    MARKET = "market"
    STRATEGY = "strategy"
    TRADING = "trading"


@dataclass(frozen=True)
class GuardResult:
    action: GuardAction
    topic: MessageTopic
    reason: str | None = None
    canned_response: str | None = None


_BLOCK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"忽略(?:之前|以上|上面)(?:的)?(?:所有)?(?:指令|提示词|规则)", re.IGNORECASE
    ),
    re.compile(r"ignore (?:all )?(?:previous|prior|above) instructions", re.IGNORECASE),
    re.compile(
        r"(?:reveal|show|print)\s+(?:your\s+)?(?:system\s+)?prompt", re.IGNORECASE
    ),
    re.compile(r"泄露(?:你的)?系统提示", re.IGNORECASE),
)

_CANNED_REFUSAL = (
    "抱歉，我无法处理该请求。我是量化研究助手，"
    "只能协助行情分析、策略研究与回测相关的问题。"
)


def _classify_topic(lowered: str) -> MessageTopic:
    if any(word in lowered for word in ("策略", "回测", "strategy", "backtest")):
        return MessageTopic.STRATEGY
    if any(word in lowered for word in ("行情", "价格", "k线", "price", "market")):
        return MessageTopic.MARKET
    if any(word in lowered for word in ("下单", "交易", "order", "trade")):
        return MessageTopic.TRADING
    return MessageTopic.GENERAL


class ContentScopeGuard:
    """First gate on every agent message; also filters streamed output."""

    def check_input(self, message: str) -> GuardResult:
        lowered = message.lower().strip()
        topic = _classify_topic(lowered)
        for pattern in _BLOCK_PATTERNS:
            if pattern.search(message):
                return GuardResult(
                    action=GuardAction.BLOCK,
                    topic=topic,
                    reason="检测到提示词注入/越权指令，请求被拒绝。",
                    canned_response=_CANNED_REFUSAL,
                )
        return GuardResult(
            action=GuardAction.ALLOW,
            topic=topic,
            reason=None,
            canned_response=None,
        )

    def filter_output(self, text: str) -> str:
        try:
            from selene_space_security.guardrails.injection_scanner import scan

            report = scan(text)
            return report.text
        except Exception:
            return text
