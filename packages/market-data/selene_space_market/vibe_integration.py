"""Vibe loader registration into ``MarketDataService`` (Phase 3.8).

When ``VIBE_DATASOURCES_ENABLED`` environment variable is set to a
truthy value (``1`` / ``true`` / ``yes``, case-insensitive), this
module registers each vendored vibe data loader (Binance, OKX,
Dexscreener, CoinGecko, yfinance, akshare, tushare, ccxt, etc.) into a
``MarketDataService`` instance as a ``VibeLoaderSource`` namespaced
under ``exchange="vibe:<loader_id>"``.

Pattern follows Phase 3.1 (``main.py`` MCP auto-enable):
- Env var opt-in (default disabled — selene market-data untouched)
- Lazy import of vibe bridge code (no import-time coupling)
- ``try/except Exception`` with ``logger.warning(exc_info=True)`` so
  market-data init never crashes if vibe subsystem fails to import
- Idempotent — calling twice does not register duplicates

Public surface:
    - :func:`is_vibe_datasources_enabled` — env-var check (pure)
    - :func:`register_vibe_loaders` — registers vibe loaders into a
      ``MarketDataService`` (returns count registered)
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selene_space_market.service import MarketDataService

logger = logging.getLogger(__name__)


_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def is_vibe_datasources_enabled() -> bool:
    """Return True iff ``VIBE_DATASOURCES_ENABLED`` is set to a truthy value.

    Case-insensitive. Defaults to False (selene market-data untouched).
    """
    raw = os.environ.get("VIBE_DATASOURCES_ENABLED", "")
    return raw.strip().lower() in _TRUTHY


def register_vibe_loaders(service: "MarketDataService") -> int:
    """Register all vibe loaders into *service*.

    Safe to call multiple times — loaders already registered under the
    ``vibe:<id>`` key are skipped (idempotent). Returns the number of
    loaders newly registered (0 if env var disabled or import failed).

    Args:
        service: a ``MarketDataService`` with ``register_source`` and
            ``_sources`` attributes.
    """
    if not is_vibe_datasources_enabled():
        logger.debug(
            "Vibe datasources disabled (set VIBE_DATASOURCES_ENABLED=true to enable)"
        )
        return 0

    try:
        from subsystems.vibe.bridge.datasources.preset import list_vibe_loaders
        from selene_space_market.sources.vibe_adapter import VibeLoaderSource
    except Exception:
        logger.warning(
            "Failed to import vibe datasources bridge — vibe loaders not registered",
            exc_info=True,
        )
        return 0

    try:
        loader_ids = list_vibe_loaders()
    except Exception:
        logger.warning(
            "Failed to list vibe loaders — vibe loaders not registered",
            exc_info=True,
        )
        return 0

    existing = getattr(service, "_sources", {}) or {}
    registered = 0
    for loader_id in loader_ids:
        exchange_key = f"vibe:{loader_id}"
        # Idempotency: skip if a source with the same (exchange, market_type)
        # key is already registered. We use ('vibe:<id>', 'spot') as the
        # canonical key for vibe loaders.
        if ("vibe:" + loader_id, "spot") in existing:
            continue
        try:
            source = VibeLoaderSource(loader_id=loader_id, market_type="spot")
            service.register_source(source)
            registered += 1
        except Exception:
            logger.warning(
                "Failed to register vibe loader '%s'", loader_id, exc_info=True
            )
    if registered:
        logger.info(
            "Vibe datasources enabled — registered %d loader(s) under 'vibe:*' namespace",
            registered,
        )
    return registered
