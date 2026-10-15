"""market-analysis 异常体系（编排原语 + 编排器共用）。"""
from __future__ import annotations


class MarketAnalysisError(Exception):
    """market-analysis 基础异常。"""


class ValidationError(MarketAnalysisError):
    """LLM 输出未通过分层校验（json / consistency / semantic）或重试超限。"""

    def __init__(
        self,
        layer: str,
        field: str,
        message: str,
        cheat_detected: bool = False,
    ) -> None:
        self.layer = layer
        self.field = field
        self.message = message
        self.cheat_detected = cheat_detected
        super().__init__(f"[{layer}] {field}: {message}")


class CheatError(MarketAnalysisError):
    """不可变字段在重试间发生突变。"""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class SemanticError(MarketAnalysisError):
    """不可重试的语义层校验失败。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class ProviderError(MarketAnalysisError):
    """LLM 提供方调用失败（网络 / 超时 / 配置）。"""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        self.message = message
        self.cause = cause
        super().__init__(message)


__all__ = [
    "MarketAnalysisError",
    "ValidationError",
    "CheatError",
    "SemanticError",
    "ProviderError",
]
