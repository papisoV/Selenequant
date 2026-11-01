"""QuickNode RPC handler for Solana."""

from __future__ import annotations

import logging

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed

from .config import SolanaExecutionConfig

logger = logging.getLogger(__name__)


class QuickNodeClient:
    """Async Solana RPC client via QuickNode."""

    def __init__(self, rpc_url: str | None = None):
        url = rpc_url or SolanaExecutionConfig.RPC_URL
        if not url:
            raise ValueError("QuickNode RPC URL not configured")
        self.client = AsyncClient(url, commitment=Confirmed)

    async def get_balance(self) -> float:
        try:
            resp = await self.client.get_balance(
                SolanaExecutionConfig.get_payer_keypair().pubkey()
            )
            return resp.value / 1e9
        except Exception as e:
            logger.error("Failed to get balance: %s", e)
            return 0.0

    async def send_and_confirm(self, txn, max_retries: int = 3) -> str | None:
        for attempt in range(max_retries):
            try:
                signature = await self.client.send_transaction(txn, opts=None)
                sig_str = str(signature.value)
                await self.client.confirm_transaction(signature.value)
                logger.info("Transaction confirmed: https://solscan.io/tx/%s", sig_str)
                return sig_str
            except Exception as e:
                logger.error("Transaction failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
        return None

    async def close(self) -> None:
        await self.client.close()
