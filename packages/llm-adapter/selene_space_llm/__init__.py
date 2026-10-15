"""selene_space_llm -- LLM provider abstraction for SeleneQuant.

Public API:

- ``LLMProvider``: Abstract base class for LLM providers.
- ``LLMConfig``: Provider configuration model.
- ``LLMMessage``, ``LLMRole``: Chat message types.
- ``LLMError``, ``LLMConnectionError``, ``LLMRateLimitError``, ``LLMAuthError``: Exceptions.
- ``OpenAICompatProvider``: OpenAI / DeepSeek / OpenRouter provider.
- ``OllamaProvider``: Local Ollama provider.
- ``LLMRouter``: Multi-provider router with fallback chain.
- ``get_json_schema``, ``extract_structured``: Structured output helpers.
"""

from selene_space_llm.base import (
    LLMAuthError,
    LLMConfig,
    LLMConnectionError,
    LLMContextLengthError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRateLimitError,
    LLMRole,
)
from selene_space_llm.ollama import OllamaProvider
from selene_space_llm.openai_compat import OpenAICompatProvider
from selene_space_llm.router import LLMRouter
from selene_space_llm.schemas import (
    MarketAnalysis,
    TokenUsage,
    ToolCall,
    ToolCallResult,
    extract_structured,
    get_json_schema,
    market_analysis_schema,
    strategy_config_schema,
    trade_intent_schema,
)

__all__ = [
    "LLMAuthError",
    "LLMConfig",
    "LLMConnectionError",
    "LLMContextLengthError",
    "LLMError",
    "LLMMessage",
    "LLMProvider",
    "LLMRateLimitError",
    "LLMRole",
    "LLMRouter",
    "OllamaProvider",
    "OpenAICompatProvider",
    "MarketAnalysis",
    "TokenUsage",
    "ToolCall",
    "ToolCallResult",
    "extract_structured",
    "get_json_schema",
    "trade_intent_schema",
    "strategy_config_schema",
    "market_analysis_schema",
]
