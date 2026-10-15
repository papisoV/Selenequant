"""Extended (Starknet) perpetual DEX adapter configuration."""

from pydantic import BaseModel


class ExtendedConfig(BaseModel):
    """Configuration for the Extended trading adapter.

    When api_key or stark_private_key is empty, the adapter runs in paper mode:
    real market data is fetched via REST, but all trading operations are
    simulated locally.
    """

    api_key: str = ""
    stark_private_key: str = ""
    public_key: str = ""
    vault: int = 0
    api_url: str = ""
    network: str = "mainnet"
    fee_rate: str = "0.0005"
    poll_ms: int = 2500
    start_balance: float = 10000.0
    proxy: str = ""
