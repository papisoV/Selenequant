"""Hyperliquid public-info client — on-chain intelligence (research only).

Read-only access to Hyperliquid's public ``/info`` endpoint. No trading, no
custody, no funds movement — this pulls publicly visible address data for
scoring, same posture as ``yahoo_finance.py``.

Method signatures mirror the call sites in ``app/api/v1/hyperliquid.py``:
``get_user_state`` / ``get_user_fills`` / ``get_portfolio_history`` /
``get_leaderboard``.

**``get_leaderboard`` is deliberately not implemented.** Hyperliquid's public
``/info`` endpoint documents 29 request types and none of them returns a
trader ranking; a probe of ``{"type": "leaderboard"}`` answers exactly like a
bogus type name (HTTP 422 "Failed to deserialize the JSON body"). The
``/addresses/screen`` endpoint that depends on it therefore reports the gap
instead of pretending to have data. See the method docstring for the
evidence.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from selene_space_core.resilience import OutboundGateway

logger = logging.getLogger(__name__)

HL_INFO_URL = "https://api.hyperliquid.xyz/info"

#: Hyperliquid addresses are 42-char hex (0x + 40). Reject early with a clear
#: message: the upstream answers a malformed address with an opaque 422.
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

#: Portfolio buckets the details endpoint reads. Upstream returns these plus
#: *_allTime / perp* variants; we keep the three the UI renders.
_PORTFOLIO_BUCKETS = ("day", "week", "month")


class HyperliquidError(Exception):
    """Raised when a Hyperliquid call fails (network / HTTP / parse)."""


def _normalize_address(address: str) -> str:
    addr = (address or "").strip()
    if not _ADDRESS_RE.match(addr):
        raise HyperliquidError(
            f"invalid Hyperliquid address {addr[:12]!r} — expected 0x + 40 hex chars"
        )
    return addr.lower()


class HyperliquidClient:
    """Minimal async client for Hyperliquid's public info endpoint.

    ``OutboundGateway`` gives a shared pool + per-host circuit breaker, so a
    stalled Hyperliquid cannot block the event loop (same reason the Yahoo
    client uses it).
    """

    def __init__(
        self,
        timeout: httpx.Timeout | None = None,
        *,
        client: OutboundGateway | None = None,
    ) -> None:
        self._client = client or OutboundGateway(
            default_timeout=timeout
            or httpx.Timeout(connect=5.0, read=20.0, write=5.0, pool=5.0),
            default_circuit=True,
        )

    async def close(self) -> None:
        if not self._client.is_closed:
            await self._client.aclose()

    # -- public methods (aligned to app/api/v1/hyperliquid.py) --------------

    async def get_user_state(self, address: str) -> dict[str, Any]:
        """Perp account state for ``address``.

        Returns the ``clearinghouseState`` body plus an ``openOrders`` key,
        because that body carries no orders of its own — the details endpoint
        reads ``state["openOrders"]`` and would otherwise always show none.

        Orders are fetched separately and **degrade to empty on failure**: the
        account state (margin, positions) is the substance of the response,
        and a secondary call failing must not turn the whole details view into
        a 503. Only the state call itself is allowed to raise.
        """
        user = _normalize_address(address)
        state = await self._post({"type": "clearinghouseState", "user": user})
        if not isinstance(state, dict):
            raise HyperliquidError("clearinghouseState returned a non-object body")

        try:
            orders = await self._post({"type": "frontendOpenOrders", "user": user})
        except HyperliquidError as exc:
            logger.warning("openOrders unavailable for %s: %s", user[:10], exc)
            orders = []
        state["openOrders"] = orders if isinstance(orders, list) else []
        return state

    async def get_user_fills(self, address: str, limit: int = 500) -> list[dict]:
        """Most recent fills for ``address``, newest first (capped at ``limit``).

        Upstream returns up to 2000 and takes no limit parameter, so the cap
        is applied here.
        """
        user = _normalize_address(address)
        fills = await self._post({"type": "userFills", "user": user})
        if not isinstance(fills, list):
            raise HyperliquidError("userFills returned a non-list body")
        rows = [f for f in fills if isinstance(f, dict)]
        return rows[: max(limit, 0)] if limit else rows

    async def get_portfolio_history(self, address: str) -> dict[str, Any]:
        """Portfolio buckets keyed by name.

        Upstream answers ``portfolio`` with a list of ``[bucket, payload]``
        pairs in a fixed order; the details endpoint indexes it as a mapping
        (``portfolio.get("day", {})``). Convert once, here, rather than
        teaching every call site the wire shape.
        """
        user = _normalize_address(address)
        raw = await self._post({"type": "portfolio", "user": user})

        if isinstance(raw, dict):
            # Already keyed (defensive: upstream shape could change back).
            return raw
        if not isinstance(raw, list):
            raise HyperliquidError("portfolio returned an unexpected body")

        buckets: dict[str, Any] = {}
        for item in raw:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[0], str)
            ):
                buckets[item[0]] = item[1]
        return buckets

    async def get_leaderboard(self, window: str = "7d") -> list[dict]:
        """Not available — raises, with the reason, instead of faking data.

        Hyperliquid's public ``/info`` supports 29 request types and none
        returns a trader ranking. Evidence (probed 2026-10-08):

            {"type": "totallyBogusTypeXyz"} -> HTTP 422 "Failed to deserialize..."
            {"type": "leaderboard"}         -> HTTP 422 "Failed to deserialize..."
            {"type": "meta"}                -> 200

        A nonexistent type and ``leaderboard`` answer identically, and the
        official docs list no ranking type. ``window`` is accepted so call
        sites keep working unchanged; it is not forwarded anywhere.
        """
        raise HyperliquidError(
            "Hyperliquid's public API exposes no trader leaderboard "
            f"(requested window={window!r}); address screening is unavailable"
        )

    # -- internals ----------------------------------------------------------

    async def _post(self, body: dict[str, Any]) -> Any:
        """POST one ``/info`` request and return the decoded JSON body."""
        try:
            resp = await self._client.post(
                HL_INFO_URL,
                json=body,
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise HyperliquidError(f"network error: {exc}") from exc

        if resp.status_code >= 400:
            raise HyperliquidError(
                f"HTTP {resp.status_code} for type={body.get('type')!r}"
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise HyperliquidError(
                f"non-JSON response for type={body.get('type')!r}"
            ) from exc
