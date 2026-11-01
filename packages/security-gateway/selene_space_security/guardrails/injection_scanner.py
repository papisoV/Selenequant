"""Injection scanner + special-token neutralizer (C5).

学 vibe-trading GHSA-v8fm 处置: 工具输出/LLM 文本里的特殊 token 形状
(ChatML/Llama/Gemma/U+FF5C 全角竖线)若原样透传, 可能被下游 provider 当
控制 token 解析 → 中和(ZWSP 插入) + 规则命中只 log 警告不改写内容。

**分片降级承诺(plan v3 Story 7 明文)**: ``filter_output`` 按 text_delta
分片逐次调用, ``<|…|>`` 形状可能被切在两片之间。仅单片内**完整形状**
中和, 不做 overlap buffer——被切的半形状本片放行、下一片重评(半形状
无 provider 解析语义, 接受该残余窗口)。若观察期日志实证跨片漏检成
实质问题, 二期再升级 overlap 方案。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

ZWSP = "​"


@dataclass(frozen=True)
class ScanReport:
    hits: tuple[str, ...] = ()
    neutralized: bool = False
    text: str = ""
    details: dict = field(default_factory=dict)


# 规则表(plan 要求 ≥5 条): 每条 (name, compiled pattern)
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 1. 忽略指令类(中英)
    (
        "ignore_instructions",
        re.compile(
            r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+instructions"
            r"|忽略(?:之前|以上|上面)(?:的)?(?:所有)?(?:指令|提示词|规则)",
            re.IGNORECASE,
        ),
    ),
    # 2. 角色重写类
    (
        "role_override",
        re.compile(
            r"(?:you\s+are\s+now|new\s+persona|act\s+as\s+(?:if\s+you\s+are\s+)?a[n]?\s)"
            r"|你(?:现在)?是(?:一个)?(?:全新|不同)的",
            re.IGNORECASE,
        ),
    ),
    # 3. 工具标记泄漏类
    ("tool_tag_leak", re.compile(r"</?(?:tool_call|tool_response|function_results)>")),
    # 4. ChatML 形状
    (
        "chatml_shape",
        re.compile(r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>"),
    ),
    # 5. 通用特殊 token 形状(<|...|> 任意内容) — 兜底
    ("special_token_shape", re.compile(r"<\|[a-zA-Z_]{1,40}\|>")),
)

# 需要 ZWSP 中和的 token 形状(拆开 provider 可解析的控制 token)
_NEUTRALIZE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<\|[a-zA-Z_]{1,40}\|>"),  # ChatML/Llama/Gemma 通用
    re.compile(r"｜[a-zA-Z_]{1,40}｜"),  # U+FF5C 全角竖线变体
)


def neutralize_special_tokens(text: str) -> str:
    """对完整 token 形状插 ZWSP 使其不再匹配控制 token 语法。幂等:
    ``f(neutralize(x)) == neutralize(x)`` — ZWSP 已隔断形状, 再次扫描无命中。
    """
    result = text
    for pattern in _NEUTRALIZE_PATTERNS:
        result = pattern.sub(lambda m: m.group(0)[0] + ZWSP + m.group(0)[1:], result)
    return result


def scan(text: str) -> ScanReport:
    """规则扫描 + (SELENE_NEUTRALIZE 默认开)中和。只报告, 不改写语义内容。"""
    hits = tuple(name for name, pattern in _RULES if pattern.search(text))
    neutralize_on = os.getenv("SELENE_NEUTRALIZE", "on").lower() not in (
        "off",
        "0",
        "false",
    )
    neutralized = False
    if neutralize_on:
        new_text = neutralize_special_tokens(text)
        neutralized = new_text != text
        if hits:
            logger.warning("injection_scanner rule hits: %s", ",".join(hits))
        return ScanReport(hits=hits, neutralized=neutralized, text=new_text)
    if hits:
        logger.warning(
            "injection_scanner rule hits (neutralize off): %s", ",".join(hits)
        )
    return ScanReport(hits=hits, neutralized=False, text=text)
