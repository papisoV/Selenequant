"""RISEx perpetual DEX adapter configuration."""

from pydantic import BaseModel


class RisexConfig(BaseModel):
    """Configuration for the RISEx trading adapter.

    When signer_key is empty, the adapter runs in paper mode:
    real market data is fetched via REST, but all trading operations
    raise NotImplementedError (EIP-712 signing integration needed).
    """

    account: str = ""
    signer_key: str = ""
    api_url: str = ""
    ws_url: str = ""
    poll_ms: int = 2500
    start_balance: float = 10000.0
    proxy: str = ""
