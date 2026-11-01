#!/usr/bin/env python3
"""Self-check: is the Selene MCP server reachable and exposing the right tools?

Run AFTER the backend is up (``python dev.py``).  Connects as a real MCP client
to ``/mcp-server`` and asserts the read-only surface is intact.

    python scripts/mcp_selfcheck.py            # :8088
    python scripts/mcp_selfcheck.py --port 8089

Exit code 0 = healthy, 1 = not reachable / wrong surface.  Prints one line per
check so a failure says *which* assumption broke, not just "it failed".
"""

from __future__ import annotations

import argparse
import asyncio
import sys

# Tools that MUST NEVER be exposed — each has a side effect despite any SAFE
# label.  If one shows up, the allowlist has regressed; fail loudly.
FORBIDDEN = ("python_exec", "paper_create_account", "paper_place_order", "backtest_run")

EXPECTED_COUNT = 16


async def check(url: str) -> int:
    try:
        from fastmcp import Client
    except ImportError:
        print(
            "FAIL: fastmcp not importable — run `pip install fastmcp` (or use the vibe env)"
        )
        return 1

    try:
        async with Client(url) as client:
            tools = sorted(t.name for t in await client.list_tools())
    except Exception as exc:  # noqa: BLE001 — any connect failure is the point
        print(f"FAIL: cannot reach {url} — {type(exc).__name__}: {exc}")
        print(
            "      is the backend up? (python dev.py)  is this tree the one serving :8088?"
        )
        return 1

    fails = 0
    print(f"OK  : connected to {url}")
    print(f"     tools exposed: {len(tools)}")

    if len(tools) != EXPECTED_COUNT:
        print(f"WARN: expected {EXPECTED_COUNT} tools, got {len(tools)}")
        fails += 1

    leaked = [t for t in FORBIDDEN if t in tools]
    if leaked:
        print(f"FAIL: side-effecting tool(s) exposed — {leaked}")
        fails += 1
    else:
        print(f"OK  : none of {len(FORBIDDEN)} side-effecting tools leaked")

    print(f"     {', '.join(tools)}")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    url = f"http://{args.host}:{args.port}/mcp-server/"
    return asyncio.run(check(url))


if __name__ == "__main__":
    sys.exit(main())
