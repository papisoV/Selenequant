"""NO_DATA sentinel output for data tools (A2).

全链数据失败时返回嵌"防幻觉指令"的哨兵文本 + 真实原因，而不是空串
（LLM 会把空结果当"无数据"自由发挥）。

通道约定（react.py:229-233 实证）：error 非空时 LLM 只见 error 字符串。
因此本模块只用于**可恢复的数据缺失**路径——`ToolResult(output=sentinel,
error="")`，让哨兵经 else 分支进 LLM。程序性缺参路径（error 非空）保持
现状不动。
"""

from __future__ import annotations

_SENTINEL_TEMPLATE = (
    "NO_DATA_AVAILABLE: {reason}\n"
    "Source: {source}\n"
    "\n"
    "No usable market data was returned for this request. Do not estimate, "
    "extrapolate, or fabricate any values — report that data is unavailable "
    "and state the reason above verbatim. If a number is essential to the "
    "user's question, say what data would be needed instead of guessing."
)


def no_data_output(reason: str, source: str = "market data") -> str:
    """Build the sentinel text. Pure function: same input → same output."""
    return _SENTINEL_TEMPLATE.format(
        reason=reason.strip() or "unspecified", source=source
    )
