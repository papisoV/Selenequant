"""Solana token utility functions."""

from __future__ import annotations

import logging

from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient

from .config import SolanaExecutionConfig

logger = logging.getLogger(__name__)


async def get_mint_decimals(mint_str: str, client: AsyncClient) -> int:
    """Get the decimal count for a Solana SPL token mint."""
    if mint_str == SolanaExecutionConfig.SOL_MINT:
        return 9
    try:
        pubkey = Pubkey.from_string(mint_str)
        resp_parsed = await client.get_account_info_json_parsed(pubkey)
        if resp_parsed.value is None:
            return 6
        decimals = resp_parsed.value.data.parsed["info"]["decimals"]
        return int(decimals)
    except Exception:
        return 6
