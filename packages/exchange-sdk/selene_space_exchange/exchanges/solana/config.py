"""Solana execution configuration."""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv

load_dotenv()


class SolanaExecutionConfig:
    """Configuration for Solana DEX trading via Jupiter aggregator."""

    RPC_URL: str = os.getenv("QUICKNODE_RPC_URL", "")
    _PRIV_KEY_STR: str = os.getenv("SOLANA_PRIVATE_KEY", "")
    _PAYER_KEYPAIR = None

    DEFAULT_SLIPPAGE_BPS: int = 200
    PRIORITY_LEVEL: str = "High"

    SOL_MINT: str = "So11111111111111111111111111111111111111112"
    USDC_MINT: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    @classmethod
    def has_private_key(cls) -> bool:
        return bool(cls._PRIV_KEY_STR)

    @classmethod
    def get_payer_keypair(cls):
        from solders.keypair import Keypair

        if cls._PAYER_KEYPAIR is not None:
            return cls._PAYER_KEYPAIR
        if not cls._PRIV_KEY_STR:
            raise ValueError("Missing SOLANA_PRIVATE_KEY in .env")
        try:
            cls._PAYER_KEYPAIR = Keypair.from_base58_string(cls._PRIV_KEY_STR)
        except Exception:
            cls._PAYER_KEYPAIR = Keypair.from_bytes(json.loads(cls._PRIV_KEY_STR))
        return cls._PAYER_KEYPAIR

    @classmethod
    def get_wallet_address(cls) -> str:
        return str(cls.get_payer_keypair().pubkey())
