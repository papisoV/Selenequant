"""Solana DEX trader - buy/sell via Jupiter aggregator."""

from __future__ import annotations

import logging

from solders.pubkey import Pubkey
from solana.rpc.types import TokenAccountOpts

from .config import SolanaExecutionConfig
from .jupiter import JupiterAggregator
from .rpc_handler import QuickNodeClient
from .utils import get_mint_decimals

logger = logging.getLogger(__name__)


class SolanaTrader:
    """Executes Solana DEX trades via Jupiter aggregator."""

    TOKEN_PROGRAM_ID = Pubkey.from_string(
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
    )

    def __init__(self):
        self.rpc = QuickNodeClient()
        self.jup = JupiterAggregator()

    async def buy(
        self,
        token_address: str,
        amount_sol: float,
        slippage_bps: int = 500,
    ) -> str | None:
        """Buy a token with SOL. Returns transaction signature on success."""
        logger.info("Executing BUY: %f SOL -> %s", amount_sol, token_address)
        balance = await self.rpc.get_balance()
        if balance < amount_sol + 0.02:
            logger.warning(
                "Insufficient SOL balance: %f. Needed: %f", balance, amount_sol + 0.02
            )
            return None

        amount_lamports = int(amount_sol * 1e9)
        quote = await self.jup.get_quote(
            input_mint=SolanaExecutionConfig.SOL_MINT,
            output_mint=token_address,
            amount_integer=amount_lamports,
            slippage_bps=slippage_bps,
        )
        if not quote:
            logger.error("No quote found for buy.")
            return None

        logger.info("Quote received. Est. output: %s raw units", quote.get("outAmount"))
        b64_tx = await self.jup.get_swap_tx(quote)
        if not b64_tx:
            return None

        try:
            txn = self.jup.deserialize_and_sign(b64_tx)
        except Exception as e:
            logger.error("Signing failed: %s", e)
            return None

        sig = await self.rpc.send_and_confirm(txn)
        if sig:
            logger.info("BUY successful: %s | Tx: %s", token_address, sig)
        return sig

    async def sell(
        self,
        token_address: str,
        percentage: float = 1.0,
        slippage_bps: int = 500,
    ) -> str | None:
        """Sell a token for SOL. Returns transaction signature on success."""
        logger.info("Executing SELL: %.0f%% of %s -> SOL", percentage * 100, token_address)

        raw_balance = 0
        try:
            wallet_pubkey = Pubkey.from_string(SolanaExecutionConfig.get_wallet_address())
            mint_pubkey = Pubkey.from_string(token_address)
            opts = TokenAccountOpts(program_id=self.TOKEN_PROGRAM_ID, mint=mint_pubkey)
            resp = await self.rpc.client.get_token_accounts_by_owner_json_parsed(
                wallet_pubkey, opts
            )
            if resp.value:
                for account_info in resp.value:
                    amount_str = account_info.account.data.parsed["info"]["tokenAmount"][
                        "amount"
                    ]
                    raw_balance += int(amount_str)
            logger.info("Token balance: %d raw units", raw_balance)

            if raw_balance == 0:
                logger.warning("No balance for %s, skipping sell.", token_address)
                return None

            sell_amount = int(raw_balance * percentage)
            if sell_amount == 0:
                logger.warning("Sell amount is 0")
                return None
        except Exception as e:
            logger.error("Failed to fetch token balance: %s", e)
            return None

        quote = await self.jup.get_quote(
            input_mint=token_address,
            output_mint=SolanaExecutionConfig.SOL_MINT,
            amount_integer=sell_amount,
            slippage_bps=slippage_bps,
        )
        if not quote:
            logger.error("Sell quote not found.")
            return None

        b64_tx = await self.jup.get_swap_tx(quote)
        if not b64_tx:
            return None

        try:
            txn = self.jup.deserialize_and_sign(b64_tx)
            sig = await self.rpc.send_and_confirm(txn)
            if sig:
                logger.info("SELL successful: %s | Tx: %s", token_address, sig)
                return sig
        except Exception as e:
            logger.error("Sell execution failed: %s", e)
        return None

    async def get_token_price_sol(self, token_address: str) -> float:
        """Get the price of a token in SOL via Jupiter quote."""
        try:
            decimals = await get_mint_decimals(token_address, self.rpc.client)
            amount_1_unit = 10**decimals
            quote = await self.jup.get_quote(
                input_mint=token_address,
                output_mint=SolanaExecutionConfig.SOL_MINT,
                amount_integer=amount_1_unit,
            )
            if quote:
                out_lamports = int(quote["outAmount"])
                return out_lamports / 1e9
        except Exception as e:
            logger.warning("Price fetch failed for %s: %s", token_address, e)
        return 0.0

    async def close(self) -> None:
        await self.rpc.close()
        await self.jup.close()
