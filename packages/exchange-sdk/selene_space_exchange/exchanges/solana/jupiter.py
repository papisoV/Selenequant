"""Jupiter aggregator integration for Solana DEX swaps."""

from __future__ import annotations

import base64
import logging
from typing import Any

import aiohttp

from .config import SolanaExecutionConfig

logger = logging.getLogger(__name__)


class JupiterAggregator:
    """Jupiter V6 aggregator for Solana swap quotes and transactions."""

    def __init__(self, base_url: str = "https://quote-api.jup.ag/v6"):
        self.base_url = base_url
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10, connect=5))
        return self._session

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_integer: int,
        slippage_bps: int | None = None,
    ) -> dict[str, Any] | None:
        session = await self._get_session()
        slippage = slippage_bps or SolanaExecutionConfig.DEFAULT_SLIPPAGE_BPS
        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_integer),
            "slippageBps": str(slippage),
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }
        async with session.get(f"{self.base_url}/quote", params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("Jupiter Quote Error: %s", text)
                return None
            return await resp.json()

    async def get_swap_tx(self, quote_response: dict) -> str | None:
        session = await self._get_session()
        payload = {
            "quoteResponse": quote_response,
            "userPublicKey": SolanaExecutionConfig.get_wallet_address(),
            "wrapAndUnwrapSol": True,
            "computeUnitPriceMicroLamports": "auto",
            "prioritizationFeeLamports": "auto",
        }
        async with session.post(f"{self.base_url}/swap", json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error("Jupiter Swap API Error: %s", text)
                return None
            data = await resp.json()
            return data.get("swapTransaction")

    @staticmethod
    def deserialize_and_sign(b64_tx_str: str):
        from solders.transaction import VersionedTransaction

        tx_bytes = base64.b64decode(b64_tx_str)
        txn = VersionedTransaction.from_bytes(tx_bytes)
        signature = SolanaExecutionConfig.get_payer_keypair().sign_message(
            txn.message.to_bytes()
        )
        return VersionedTransaction.populate(txn.message, [signature])

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
