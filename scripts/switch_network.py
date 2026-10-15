"""Switch between mainnet/testnet by rewriting active .env keys.

Usage:
    python scripts/switch_network.py mainnet
    python scripts/switch_network.py testnet

Reads SELENE_EXCHANGE_API_KEY_{NETWORK} / SECRET_{NETWORK} and writes them
into the active SELENE_EXCHANGE_API_KEY / SECRET slots. Also flips
SELENE_*_TESTNET flags and SELENE_ENABLE_REAL_TRADING.

Requires both MAINNET and TESTNET key blocks to be filled in .env.
"""
from __future__ import annotations

import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def switch(network: str) -> None:
    if network not in ("mainnet", "testnet"):
        print(f"ERROR: network must be mainnet or testnet, got {network}")
        sys.exit(1)

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)

    # Collect MAINNET/TESTNET key pairs
    keys: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        k, v = stripped.split("=", 1)
        k = k.strip()
        if k in (
            "SELENE_EXCHANGE_API_KEY_MAINNET",
            "SELENE_EXCHANGE_API_SECRET_MAINNET",
            "SELENE_EXCHANGE_API_KEY_TESTNET",
            "SELENE_EXCHANGE_API_SECRET_TESTNET",
        ):
            keys[k] = v.strip()

    needed_key = f"SELENE_EXCHANGE_API_KEY_{network.upper()}"
    needed_secret = f"SELENE_EXCHANGE_API_SECRET_{network.upper()}"
    if needed_key not in keys or not keys[needed_key]:
        print(f"ERROR: {needed_key} not set in .env. Fill it first.")
        sys.exit(1)
    if needed_secret not in keys or not keys[needed_secret]:
        print(f"ERROR: {needed_secret} not set in .env. Fill it first.")
        sys.exit(1)

    # Rewrite active slots
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        k, v = stripped.split("=", 1)
        k = k.strip()
        if k == "SELENE_USE_NETWORK":
            out.append(f"SELENE_USE_NETWORK={network}\n")
        elif k == "SELENE_EXCHANGE_API_KEY":
            out.append(f"SELENE_EXCHANGE_API_KEY={keys[needed_key]}\n")
        elif k == "SELENE_EXCHANGE_API_SECRET":
            out.append(f"SELENE_EXCHANGE_API_SECRET={keys[needed_secret]}\n")
        elif k in ("SELENE_BINANCE_TESTNET", "SELENE_EXCHANGE_TESTNET"):
            out.append(f"{k}={'true' if network == 'testnet' else 'false'}\n")
        elif k == "SELENE_ENABLE_REAL_TRADING":
            # mainnet needs REAL_TRADING=1 to actually trade; testnet too.
            out.append(f"SELENE_ENABLE_REAL_TRADING={'true' if network == 'mainnet' else 'true'}\n")
        else:
            out.append(line)

    ENV_PATH.write_text("".join(out), encoding="utf-8")
    print(f"OK: switched .env to {network}")
    print(f"  API_KEY={keys[needed_key][:8]}...{keys[needed_key][-4:]}")
    print(f"  TESTNET={'true' if network == 'testnet' else 'false'}")
    print("  REAL_TRADING=true")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/switch_network.py <mainnet|testnet>")
        sys.exit(1)
    switch(sys.argv[1].lower())
