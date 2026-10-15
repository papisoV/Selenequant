"""OKX contract size converter — wraps the public convert_contract_coin API.

Wraps ``public_api.convert_contract_coin(type='1',
instId=instId, sz=str(amount_usdt), px=str(adjusted_price), unit='usdt',
opType='open')`` for the selene-space exchange-sdk layer.

OKX swap contracts are quoted in "contracts" (张), not in USDT or base coin.
Before placing a limit order on a swap, the caller must convert the desired
USDT notional into the exchange-native contract count. OKX exposes a public
endpoint for this conversion:

    GET /api/v5/public/convert-contract-coin
        ?type=1&instId=BTC-USDT-SWAP&sz=20&px=60000&unit=usdt&opType=open

Response: ``{"code": "0", "data": [{"sz": "0.001", ...}]}`` where ``sz`` is
the number of contracts.

This module provides a thin sync helper plus a cached async client wrapper.
The sync helper exists because the strategy engine evaluates signals on a
hot sync path and cannot await — the async wrapper is used by the live
execution layer (paper-engine / live_engine) where I/O is acceptable.

Design notes:
- ``usdt_to_contracts`` is the single public entry point.
- It accepts an explicit ``price`` so the caller controls tick rounding
  (round to tick_size before converting — see ``adjusted_price`` callers).
- Returns ``float``. Callers that need a string for the OKX ``sz`` field
  should format themselves; we keep the contract pure numeric.
- On API failure we raise ``ExchangeAPIError`` — callers MUST handle
  (e.g. skip the order, log, send a notification). We do NOT silently
  return 0 because that would place a zero-size order downstream.
"""

from __future__ import annotations

import logging
from typing import Any

from selene_space_exchange.exceptions import ExchangeAPIError

logger = logging.getLogger(__name__)


class OKXContractConverter:
    """Convert USDT notional to OKX swap contract count.

    Thin wrapper around OKX's public ``convert-contract-coin`` endpoint.
    The conversion depends on the instrument's ``ctVal`` (contract value)
    and ``ctMult`` (multiplier), which OKX computes server-side; we do not
    cache instrument metadata here — the API is authoritative.

    Usage (live, async)::

        converter = OKXContractConverter(rest_client)
        sz = await converter.usdt_to_contracts(
            inst_id="BTC-USDT-SWAP",
            amount_usdt=20.0,
            price=60000.0,
            side="buy",
        )

    Usage (sync helper, for strategy-engine hot path)::

        sz = OKXContractConverter.convert_sync(
            public_api, inst_id, amount_usdt, adjusted_price, side="buy"
        )

    The sync helper accepts a "public_api" object that exposes
    ``convert_contract_coin(**params)`` (the legacy okx.Public_api
    shape) so the strategy-engine can call it without an asyncio loop.
    """

    # ------------------------------------------------------------------
    # Async (live execution path)
    # ------------------------------------------------------------------

    def __init__(self, rest_client: Any | None = None) -> None:
        """Initialize the converter.

        Args:
            rest_client: An ``OKXRESTClient`` (or any object exposing
                ``_okx_request``) used to call the public endpoint. If
                None, only the sync helper can be used.
        """
        self._rest_client = rest_client

    async def usdt_to_contracts(
        self,
        inst_id: str,
        amount_usdt: float,
        price: float,
        side: str,
    ) -> float:
        """Convert USDT notional to OKX contract count (async).

        Args:
            inst_id: OKX instrument ID, e.g. ``"BTC-USDT-SWAP"``.
            amount_usdt: USDT notional to convert. Must be positive.
            price: Limit price (already tick-rounded by the caller).
            side: ``"buy"`` or ``"sell"``. Maps to OKX ``opType``:
                ``"open"`` for buy, ``"close"`` for sell. We always use
                ``"open"`` here because this converter is called for
                fresh entries (the entry-sizing use case); close-side sizing
                uses the position's existing contract count, not a
                fresh USDT→contract conversion.

        Returns:
            Number of contracts as a float. Zero or near-zero results
            indicate the notional is too small for one contract — the
            caller should skip the order (contract count "合约张数太小").

        Raises:
            ExchangeAPIError: If OKX returns a non-zero code or the
                response shape is unexpected.
        """
        if self._rest_client is None:
            raise ExchangeAPIError(
                "OKXContractConverter requires a rest_client for async use",
                exchange="okx",
            )
        if amount_usdt <= 0:
            raise ExchangeAPIError(
                f"amount_usdt must be positive, got {amount_usdt}",
                exchange="okx",
            )
        if price <= 0:
            raise ExchangeAPIError(
                f"price must be positive, got {price}",
                exchange="okx",
            )

        op_type = "open" if side.lower() == "buy" else "close"
        params: dict[str, Any] = {
            "type": "1",
            "instId": inst_id,
            "sz": str(amount_usdt),
            "px": str(price),
            "unit": "usdt",
            "opType": op_type,
        }

        # OKXRESTClient._okx_request supports GET with params.
        data = await self._rest_client._okx_request(
            "GET", "/api/v5/public/convert-contract-coin", params=params
        )
        return self._extract_sz(data, inst_id)

    # ------------------------------------------------------------------
    # Sync (strategy-engine hot path)
    # ------------------------------------------------------------------

    @staticmethod
    def convert_sync(
        public_api: Any,
        inst_id: str,
        amount_usdt: float,
        price: float,
        side: str,
    ) -> float:
        """Sync helper for the strategy-engine hot path.

        Mirrors the reference call shape — accepts a legacy-style ``public_api``
        object exposing ``convert_contract_coin(type, instId, sz, px, unit,
        opType)`` and returns the contract count as a float.

        This is a separate method (not the same code path as the async
        wrapper) because the legacy ``okx.Public_api.PublicAPI`` returns
        a dict synchronously, while ``OKXRESTClient`` is async. Keeping
        them separate avoids "async function called in sync context"
        footguns in the strategy engine.

        Args:
            public_api: Object with a ``convert_contract_coin`` method
                (the legacy-style OKX PublicAPI).
            inst_id: OKX instrument ID, e.g. ``"BTC-USDT-SWAP"``.
            amount_usdt: USDT notional. Must be positive.
            price: Limit price (tick-rounded by caller).
            side: ``"buy"`` or ``"sell"``.

        Returns:
            Number of contracts as a float. Zero indicates the notional
            is too small for one contract (caller should skip).

        Raises:
            ExchangeAPIError: If the API call fails or response is
                malformed.
        """
        if amount_usdt <= 0:
            raise ExchangeAPIError(
                f"amount_usdt must be positive, got {amount_usdt}",
                exchange="okx",
            )
        if price <= 0:
            raise ExchangeAPIError(
                f"price must be positive, got {price}",
                exchange="okx",
            )

        op_type = "open" if side.lower() == "buy" else "close"
        try:
            response = public_api.convert_contract_coin(
                type="1",
                instId=inst_id,
                sz=str(amount_usdt),
                px=str(price),
                unit="usdt",
                opType=op_type,
            )
        except Exception as exc:
            raise ExchangeAPIError(
                f"OKX convert_contract_coin call failed: {exc}",
                exchange="okx",
            ) from exc

        return OKXContractConverter._extract_sz(response, inst_id)

    # ------------------------------------------------------------------
    # Shared response parser
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sz(response: dict[str, Any], inst_id: str) -> float:
        """Extract the ``sz`` field from the OKX convert response.

        OKX returns::

            {"code": "0", "data": [{"sz": "0.001", ...}], "msg": ""}

        Raises ``ExchangeAPIError`` on non-zero code or missing data.

        Returns:
            Contract count as float. ``0.0`` if OKX returned a zero sz
            (notional too small for one contract — caller should skip).
        """
        code = str(response.get("code", ""))
        if code != "0":
            msg = response.get("msg", "")
            raise ExchangeAPIError(
                f"OKX convert-contract-coin failed for {inst_id}: "
                f"code={code} msg={msg}",
                exchange="okx",
                details=response,
            )

        data = response.get("data")
        if not data or not isinstance(data, list) or len(data) == 0:
            raise ExchangeAPIError(
                f"OKX convert-contract-coin returned empty data for {inst_id}",
                exchange="okx",
                details=response,
            )

        sz_raw = data[0].get("sz")
        if sz_raw is None:
            raise ExchangeAPIError(
                f"OKX convert-contract-coin response missing 'sz' for {inst_id}",
                exchange="okx",
                details=response,
            )

        try:
            return float(sz_raw)
        except (TypeError, ValueError) as exc:
            raise ExchangeAPIError(
                f"OKX convert-contract-coin returned non-numeric sz "
                f"'{sz_raw}' for {inst_id}",
                exchange="okx",
                details=response,
            ) from exc
