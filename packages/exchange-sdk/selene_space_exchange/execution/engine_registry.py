"""Engine Registry — routes orders to the correct execution engine by market domain.

Replaces the single _live_engine global with a registry that maps
MarketDomain → engine key → LiveExecutionEngine, enabling simultaneous
trading across crypto, US stocks, and A-shares.
"""

from __future__ import annotations

import logging
from typing import Any

from selene_space_types.routing import MarketDomain, detect_market_domain

logger = logging.getLogger(__name__)


class EngineRegistry:
    """Routes orders to the correct execution engine based on symbol.

    Each engine is registered with a unique key (e.g. "binance", "alpaca",
    "qmt") and optional domain associations. When routing, the registry
    detects the market domain from the symbol and returns the appropriate
    engine.
    """

    def __init__(self) -> None:
        self._engines: dict[str, Any] = {}
        self._domain_routing: dict[MarketDomain, str] = {}

    def register(
        self,
        key: str,
        engine: Any,
        *,
        domains: list[MarketDomain] | None = None,
    ) -> None:
        """Register an execution engine.

        Args:
            key: Unique engine identifier (e.g. "binance", "alpaca", "qmt").
            engine: A LiveExecutionEngine instance.
            domains: Market domains this engine handles. If None, no
                     domain routing is set up (engine accessible only by key).
        """
        self._engines[key] = engine
        if domains:
            for domain in domains:
                self._domain_routing[domain] = key
        logger.info("engine registry: registered %s for domains %s", key, domains)

    def unregister(self, key: str) -> None:
        """Remove an engine from the registry."""
        self._engines.pop(key, None)
        # Clean up domain routing
        to_remove = [d for d, k in self._domain_routing.items() if k == key]
        for d in to_remove:
            del self._domain_routing[d]

    def get_engine(self, key: str) -> Any | None:
        """Get an engine by its key."""
        return self._engines.get(key)

    def route(self, symbol: str) -> Any:
        """Resolve a symbol to the correct execution engine.

        Uses detect_market_domain() to determine the market, then
        looks up the registered engine for that domain.

        Raises:
            RuntimeError: If no engine is registered for the symbol's domain.
        """
        domain = detect_market_domain(symbol)
        key = self._domain_routing.get(domain)
        if key is None:
            # Fallback: try crypto
            key = self._domain_routing.get(MarketDomain.CRYPTO)
        if key is None:
            # Last resort: return first registered engine
            if self._engines:
                key = next(iter(self._engines))
            else:
                raise RuntimeError(f"No engine registered for domain {domain.value}")

        engine = self._engines.get(key)
        if engine is None:
            raise RuntimeError(f"Engine '{key}' not found for domain {domain.value}")
        return engine

    def all_engines(self) -> list[Any]:
        """Return all registered engine instances."""
        return list(self._engines.values())

    def registered_keys(self) -> list[str]:
        """Return all registered engine keys."""
        return list(self._engines.keys())

    def domain_for_key(self, key: str) -> MarketDomain | None:
        """Return the domain associated with an engine key."""
        for domain, k in self._domain_routing.items():
            if k == key:
                return domain
        return None

    async def start_all(self) -> None:
        """Start all registered engines."""
        for key, engine in self._engines.items():
            try:
                if hasattr(engine, "start"):
                    await engine.start()
                logger.info("engine registry: started %s", key)
            except Exception as e:
                logger.error("engine registry: failed to start %s: %s", key, e)

    async def stop_all(self) -> None:
        """Stop all registered engines."""
        for key, engine in self._engines.items():
            try:
                if hasattr(engine, "stop"):
                    await engine.stop()
                logger.info("engine registry: stopped %s", key)
            except Exception as e:
                logger.error("engine registry: failed to stop %s: %s", key, e)

    def __len__(self) -> int:
        return len(self._engines)

    def __contains__(self, key: str) -> bool:
        return key in self._engines

    def __repr__(self) -> str:
        engines = ", ".join(self._engines.keys())
        routing = ", ".join(f"{d.value}→{k}" for d, k in self._domain_routing.items())
        return f"EngineRegistry(engines=[{engines}], routing=[{routing}])"
