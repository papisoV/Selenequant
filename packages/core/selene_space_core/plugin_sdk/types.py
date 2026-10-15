"""Structural contracts (Protocols) for the five plugin capability kinds.

Why Protocols instead of ABCs: plugins arrive from pip installs, workspace
folders, and user YAML with no shared base class available at authoring
time. ``@runtime_checkable`` Protocols let the loader verify capability by
attribute inspection (``isinstance(obj, StrategyPlugin)``) without the
plugin author ever importing this module.

Frozen surface: class names and member NAMES (the loader and registry
address them). Method signatures may widen -- positional order of
``subscribe(channels, symbols)``, ``complete(prompt, **options)`` etc.
is pinned only where callers pass positionally.

Grouping: ExchangeAdapter is transport; StrategyPlugin/IndicatorPlugin
consume market frames; LLMProviderPlugin and ToolPlugin serve agents.
"""

from __future__ import annotations

from typing import Any as _Any
from typing import Protocol as _Protocol
from typing import runtime_checkable as _rt


@_rt
class ExchangeAdapter(_Protocol):
    """One venue connection: stream subscription plus order transport.

    ``name`` returns the venue id used for routing and breaker keying;
    ``connect``/``disconnect`` bracket the session lifetime idempotently,
    and subscribe/unsubscribe scope which feeds arrive on the stream.
    """

    @property
    def name(self) -> str: ...

    async def connect(self) -> None:
        """Open network sessions (WS + REST); safe to call twice."""

    async def disconnect(self) -> None:
        """Close every session; no-op when already closed."""

    async def subscribe(self, channels: list[str], symbols: list[str]) -> None:
        """Start receiving the given channels for the given symbols."""

    async def unsubscribe(self, channels: list[str], symbols: list[str]) -> None:
        """Stop receiving; inverse of :meth:`subscribe`."""


@_rt
class StrategyPlugin(_Protocol):
    """Turns market data into trading signals.

    Synchronous by design: signal generation is CPU-bound over an
    in-memory frame and must not yield mid-computation.
    """

    @property
    def name(self) -> str: ...

    def generate_signals(self, frame: _Any) -> _Any:
        """Consume a market-data frame; emit the consumer's signal shape."""


@_rt
class IndicatorPlugin(_Protocol):
    """Computes a technical indicator over input data."""

    @property
    def name(self) -> str: ...

    def compute(self, frame: _Any) -> _Any:
        """Map raw input -- e.g. a price frame -- to indicator values."""


@_rt
class LLMProviderPlugin(_Protocol):
    """Chat-completion backend behind a uniform prompt call."""

    @property
    def name(self) -> str: ...

    async def complete(self, prompt: str, **options: _Any) -> str:
        """Send one prompt; provider-specific knobs ride in ``options``."""
        return ""  # pragma: no cover - Protocol body never executes


@_rt
class ToolPlugin(_Protocol):
    """Agent-invocable tool: name/description/execute triple.

    The agent reads ``description`` to decide when to invoke; arguments
    arrive as keyword pairs after validation, and results must survive
    JSON serialization to cross the tool-result boundary.
    """

    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...

    async def execute(self, **arguments: _Any) -> _Any: ...
