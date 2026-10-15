# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: the DSA six-block
# per-symbol profile is rebuilt as a provider-driven, IO-free aggregator —
# each block is an injected async provider, a failing block degrades to
# ``unavailable`` without sinking the profile, and the global evidence
# quality is a pure rollup of the block statuses.
"""Per-symbol profile aggregation behind one partial-data contract.

A "profile" is six independent blocks assembled for the same symbol::

    quote · history · research · intelligence · portfolio · monitors

Every block reports its own ``status`` (``fresh`` / ``partial`` /
``unavailable``) and a list of ``limitations``, so one dead source never
sinks the whole picture. Callers should read ``evidence_quality.status``
first, then descend into the blocks that matter.

Design notes (why this is provider-driven rather than importing sources):

- Providers are injected, never imported. The aggregator therefore has no
  dependency on any Selene package and can be unit-tested with plain
  callables. The local-api router is the only place that binds real
  sources (analysis records / news store / price alerts / paper positions).
- A ``None`` provider — or one whose call raises, or returns ``None`` — is
  reported as ``unavailable`` with a stable reason string. We never
  fabricate a block that had no source.
- Providers may return a bare payload (wrapped as ``fresh``) or a full
  block dict carrying their own ``status`` / ``limitations`` (to express
  ``partial``).
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Block order is fixed: it drives both the response shape and the rollup.
BLOCK_NAMES: tuple[str, ...] = (
    "quote",
    "history",
    "research",
    "intelligence",
    "portfolio",
    "monitors",
)

_VALID_STATUSES = frozenset({"fresh", "partial", "unavailable"})

#: A provider takes the canonical symbol and returns the block payload.
#: It may be sync or async — both are accepted (see ``_invoke``).
BlockProvider = Callable[..., Any]


class InvalidStockProfileSymbol(ValueError):
    """Raised when a profile request cannot be tied to a single symbol."""


class StockProfileService:
    """Assemble a per-symbol profile, isolating optional sources by block.

    Parameters are the six block providers; every one is optional. A missing
    provider yields an ``unavailable`` block rather than an error, which is
    the whole point of the partial-data contract.
    """

    def __init__(
        self,
        *,
        quote_provider: BlockProvider | None = None,
        history_provider: BlockProvider | None = None,
        research_provider: BlockProvider | None = None,
        intelligence_provider: BlockProvider | None = None,
        portfolio_provider: BlockProvider | None = None,
        monitors_provider: BlockProvider | None = None,
    ) -> None:
        self._providers: dict[str, BlockProvider | None] = {
            "quote": quote_provider,
            "history": history_provider,
            "research": research_provider,
            "intelligence": intelligence_provider,
            "portfolio": portfolio_provider,
            "monitors": monitors_provider,
        }

    async def get_profile(
        self, symbol: str, *, history_days: int = 60
    ) -> dict[str, Any]:
        """Build the profile for *symbol*.

        Raises ``InvalidStockProfileSymbol`` when the symbol is blank; any
        other failure is contained inside the offending block.
        """
        canonical = self.canonicalize_symbol(symbol)
        blocks: dict[str, dict[str, Any]] = {}
        for name in BLOCK_NAMES:
            blocks[name] = await self._run_block(
                name, self._providers[name], canonical, history_days=history_days
            )
        return {
            "requested_symbol": str(symbol).strip(),
            "symbol": canonical,
            "as_of": datetime.now(timezone.utc).isoformat(),
            **blocks,
            "evidence_quality": self.evidence_quality(blocks),
        }

    @staticmethod
    def canonicalize_symbol(value: str) -> str:
        """Normalize a request symbol, rejecting values with no identity.

        Deliberately conservative: we uppercase and strip, and refuse blank
        input. We do *not* infer a market or rewrite the symbol shape — the
        caller owns whatever convention its sources use.
        """
        raw = str(value or "").strip()
        if not raw:
            raise InvalidStockProfileSymbol("stock profile requires a symbol")
        return raw.upper()

    async def _run_block(
        self,
        name: str,
        provider: BlockProvider | None,
        symbol: str,
        *,
        history_days: int,
    ) -> dict[str, Any]:
        """Run one block, converting absence/failure into ``unavailable``."""
        if provider is None:
            return self._unavailable(f"{name}_source_not_configured")
        try:
            result = await self._invoke(provider, symbol, history_days=history_days)
        except Exception as exc:  # noqa: BLE001 — a block must never sink the profile
            logger.warning("stock profile %s block failed: %s", name, exc)
            return self._unavailable(f"{name}_source_error")
        if result is None:
            return self._unavailable(f"{name}_unavailable")
        if isinstance(result, dict) and "status" in result and "data" in result:
            return self._normalize_block(result)
        return {"status": "fresh", "data": result, "limitations": []}

    @staticmethod
    async def _invoke(
        provider: BlockProvider, symbol: str, *, history_days: int
    ) -> Any:
        """Call a provider that may be sync or async, passing only what it takes.

        ``history_days`` is forwarded only when the provider advertises it,
        so simple providers need not carry an unused parameter.
        """
        accepts_history = _accepts_history_days(provider)
        kwargs = {"history_days": history_days} if accepts_history else {}
        result = provider(symbol, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _normalize_block(block: dict[str, Any]) -> dict[str, Any]:
        status = str(block.get("status") or "fresh")
        if status not in _VALID_STATUSES:
            status = "partial"
        return {
            "status": status,
            "data": block.get("data"),
            "limitations": [str(item) for item in (block.get("limitations") or [])],
            **{
                k: v
                for k, v in block.items()
                if k not in {"status", "data", "limitations"}
            },
        }

    @staticmethod
    def _unavailable(reason: str, **payload: Any) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "data": None,
            **payload,
            "limitations": [reason],
        }

    @staticmethod
    def evidence_quality(blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Roll the six block statuses into one verdict plus the union of reasons.

        ``fresh`` only when every block is fresh; ``unavailable`` only when
        every block is unavailable; otherwise ``partial``. Limitations are
        de-duplicated in block order.
        """
        statuses = {name: str(blocks[name].get("status")) for name in BLOCK_NAMES}
        distinct = set(statuses.values())
        if distinct == {"fresh"}:
            overall = "fresh"
        elif distinct == {"unavailable"}:
            overall = "unavailable"
        else:
            overall = "partial"
        limitations: list[str] = []
        for name in BLOCK_NAMES:
            limitations.extend(
                str(item) for item in blocks[name].get("limitations") or []
            )
        return {
            "status": overall,
            "blocks": statuses,
            "limitations": list(dict.fromkeys(limitations)),
        }


def _accepts_history_days(provider: BlockProvider) -> bool:
    """Best-effort check for a keyword-only ``history_days`` parameter."""
    try:
        signature = inspect.signature(provider)
    except (TypeError, ValueError):
        return False
    if "history_days" in signature.parameters:
        return True
    return any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    )


__all__ = ["BLOCK_NAMES", "InvalidStockProfileSymbol", "StockProfileService"]
