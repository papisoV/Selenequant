"""Decibel (Aptos) perpetual DEX adapter configuration."""

from pydantic import BaseModel


class DecibelConfig(BaseModel):
    """Configuration for the Decibel trading adapter.

    When api_key or private_key is empty, the adapter runs in paper mode:
    real market data is fetched via REST, but all trading operations are
    simulated locally.
    """

    api_key: str = ""
    private_key: str = ""
    subaccount: str = ""
    api_url: str = ""
    network: str = "mainnet"  # mainnet or testnet
    poll_ms: int = 2000
    start_balance: float = 10000.0
    proxy: str = ""
