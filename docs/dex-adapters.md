# DEX Adapter Configuration Guide

This guide covers configuration for the three on-chain perpetual contract DEX adapters.

## Common Setup

All adapters inherit from `PerpetualDEXMixin` which provides 22 abstract methods for grid operations. They are created via the factory:

```python
from selene_space_exchange.dex.factory import create_dex_adapter

adapter = create_dex_adapter("ex", config_dict)
```

## Decibel (Aptos Chain)

### Configuration

```python
from selene_space_exchange.dex.decibel.config import DecibelConfig

config = DecibelConfig(
    base_url="https://api.decibel.finance",
    api_key="your-api-key",
    private_key="your-aptos-private-key",  # Ed25519
    trade_mode="paper",  # or "live"
    proxy_url=None,  # optional HTTP/SOCKS5 proxy
)
```

### Features
- httpx AsyncClient REST API
- Polling-based fill detection with stale price fallback
- Paper mode: Aptos BCS signing stubbed (no real transactions)

### Paper Mode
In paper mode, the adapter simulates order placement and fill detection without signing real transactions. This is safe for testing strategies.

### Live Mode
Requires a valid Aptos private key (Ed25519). The adapter uses PyNaCl for signing.

## Extended (Starknet Chain)

### Configuration

```python
from selene_space_exchange.dex.extended.config import ExtendedConfig

config = ExtendedConfig(
    base_url="https://api.extended.exchange",
    api_key="your-api-key",
    stark_private_key="0x...",  # Starknet private key
    trade_mode="paper",  # or "live"
    proxy_url=None,
)
```

### Features
- httpx + X-Api-Key authentication
- Order history-based fill confirmation
- Pure Python Stark crypto: Poseidon hash, SNIP-12 message hashing, Stark ECDSA signing
- `self_test()` verifies signing against official SDK test vectors

### Stark Crypto (`stark_crypto.py`)
- 466 lines of pure Python implementation
- Poseidon hash function
- SNIP-12 typed message hashing
- Stark ECDSA signature generation
- Self-test with official test vectors (run on import)

### Live Mode
Requires a Starknet private key. The adapter signs orders using the full Stark crypto stack.

### Paper Mode
Signing is stubbed — orders are tracked internally without being submitted on-chain.

## RISEx (Ethereum Chain)

### Configuration

```python
from selene_space_exchange.dex.risex.config import RisexConfig

config = RisexConfig(
    base_url="https://api.risex.io",
    api_key="your-api-key",
    private_key="0x...",  # Ethereum private key
    trade_mode="paper",  # or "live"
    proxy_url=None,
)
```

### Features
- httpx direct REST API (no RISEx Node.js SDK dependency)
- asyncio.Lock serial queue to prevent nonce conflicts
- 3-poll + price corroboration fill detection
- EIP-712 signing stubbed in paper mode

### Serial Queue
All order operations are serialized through an asyncio.Lock to prevent nonce conflicts. This means only one order operation runs at a time per adapter instance.

### Fill Detection
Uses a 3-step verification:
1. Order disappears from open orders (first poll)
2. Order still absent on second poll (confirmation)
3. Price has touched the order level (corroboration)

This prevents false fill detection from API latency or temporary network issues.

## Factory Usage

```python
from selene_space_exchange.dex.factory import create_dex_adapter

# Create adapter by name
de = create_dex_adapter("de", {"trade_mode": "paper"})
ex = create_dex_adapter("ex", {"trade_mode": "paper", "api_key": "..."})
rs = create_dex_adapter("rs", {"trade_mode": "paper"})

# All adapters expose the same PerpetualDEXMixin interface
markets = await ex.get_markets()
price = await ex.get_price(market_id=1)
candles = await ex.get_candles(market_id=1, timeframe_sec=3600, limit=200)
```

## Proxy Configuration

All adapters support HTTP/SOCKS5 proxy:

```python
config = DecibelConfig(
    base_url="https://api.decibel.finance",
    proxy_url="socks5://127.0.0.1:1080",
)
```

## Grid Engine Integration

```python
from selene_space_paper.grid_engine import GridExecutionEngine

adapter = create_dex_adapter("ex", config)
engine = GridExecutionEngine(adapter)

# Start a grid
state = await engine.start_grid({
    "marketId": 1,
    "mode": "neutral",
    "lower": 95000,
    "upper": 105000,
    "gridCount": 10,
    "sizeBase": 100,
    "leverage": 5,
    "outOfRangeAction": "close",
})
```
