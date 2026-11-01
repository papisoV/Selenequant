"""MCP server — expose Selene's read-only tools to external MCP clients.

Selene already builds one ``ToolCatalog`` shared by the agent runtime and (per
``main.py``'s FX09 comment) intended for MCP. This module supplies the missing
half: it publishes a curated subset of that catalog over the Model Context
Protocol, so Claude Code / Codex / any MCP client can call Selene's tools.

Design
------
- **Reuse, don't rebuild.** Tools come from the live ``ToolCatalog`` the backend
  already populated (with the correct service objects). No second tool set.
- **Read-only.** Only tools named in ``READ_ONLY_TOOLS`` are exposed. The filter
  is by NAME, not risk level: ``risk_level`` is advisory and observably wrong
  here — e.g. ``paper_create_account`` is labelled SAFE yet writes a new
  account. A side-effecting tool that slips onto a public MCP surface is the
  failure mode this allowlist exists to prevent.
- **Mount, don't own a process.** The sub-app is ``app.mount()``-ed into the
  existing FastAPI app. The parent MUST chain the sub-app's lifespan (see the
  ``/mcp-server`` wiring in ``main.py``); FastMCP's session manager does not
  initialize otherwise and every request 500s.

Wiring order matters and is split in two because the catalog does not exist at
``create_app()`` time (it is built during lifespan):

    create_app():   subapp = build_subapp(); app.mount(MOUNT_PATH, subapp)
    lifespan():     register_tools(catalog)   # after catalog is populated
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Curated read-only tool names. Every entry verified to have no side effects in
# its execute() (read-only service calls / in-memory computation only).
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        # market data
        "market_ticker",
        "market_kline",
        "market_orderbook",
        "market_snapshot",
        # risk
        "risk_check",
        "risk_report",
        "risk_pressure_check",
        # analysis / explanation
        "explain_pnl",
        "event_kline_attribution",
        # skills
        "read_skill",
        # strategy (read / in-memory generate only — no persistence)
        "strategy_get_contract",
        "strategy_explain",
        "strategy_generate",
        # backtest result (read) — backtest_run is excluded (writes)
        "backtest_result",
        # telegram signals (read)
        "tg_signal_lookup",
        "tg_event_impact",
    }
)

# Mount path for the MCP sub-application. Distinct from /api/v1/mcp, which is the
# existing server-management API (CRUD over *external* MCP servers) — different
# concept, must not collide.
MOUNT_PATH = "/mcp-server"

# Module-level singleton so ``create_app`` (which mounts it) and the lifespan
# (which registers tools once the catalog exists) reference the same app.
_mcp_instance: Any | None = None
MCP_SUBAPP: Any | None = None
_registered_tool_names: set[str] = set()


def _make_tool_fn(tool: Any):
    """Wrap a ``BaseTool`` as an async callable for ``FunctionTool.fn``.

    ``BaseTool.execute`` is synchronous; run it in a thread so a slow tool can't
    block the event loop. Return the tool's text output; raise on error so MCP
    reports a tool error rather than a silent empty success.
    """

    async def _run(**kwargs: Any) -> str:
        result = await asyncio.to_thread(tool.execute, kwargs)
        error = getattr(result, "error", None)
        if error:
            raise RuntimeError(str(error))
        return str(getattr(result, "output", ""))

    _run.__name__ = tool.name
    return _run


def build_subapp() -> Any | None:
    """Build the (empty) MCP sub-app. No catalog needed.

    Returns ``None`` when fastmcp is unavailable so the backend can degrade
    gracefully (it must start whether or not MCP can be mounted).
    """
    global _mcp_instance, MCP_SUBAPP

    if MCP_SUBAPP is not None:
        return MCP_SUBAPP

    try:
        from fastmcp import FastMCP
    except ImportError:
        logger.warning("fastmcp not installed — MCP server unavailable")
        return None

    _mcp_instance = FastMCP("SeleneQuant")
    # path="/" — the public prefix is controlled by app.mount(MOUNT_PATH, ...).
    MCP_SUBAPP = _mcp_instance.http_app(path="/")
    return MCP_SUBAPP


def register_tools(catalog: Any) -> int:
    """Register the read-only subset of ``catalog`` onto the MCP server.

    Call once the catalog is populated (during lifespan). Idempotent. Returns
    the number of tools exposed.
    """
    global _registered_tool_names

    if _mcp_instance is None:
        logger.warning("MCP server not built — register_tools skipped")
        return 0

    try:
        from fastmcp.tools.function_tool import FunctionTool
    except ImportError:  # pragma: no cover - build_subapp already guarded this
        return 0

    available = set(catalog.tool_names())
    exposed = sorted(READ_ONLY_TOOLS & available)
    withheld = sorted(available - READ_ONLY_TOOLS)
    missing = sorted(READ_ONLY_TOOLS - available)

    for name in exposed:
        if name in _registered_tool_names:
            continue
        tool = catalog.get(name)
        if tool is None:  # pragma: no cover - tool_names()/get() share a map
            continue
        _mcp_instance.add_tool(
            FunctionTool(
                name=tool.name,
                description=tool.description,
                parameters=tool.parameters,
                fn=_make_tool_fn(tool),
            )
        )
        _registered_tool_names.add(name)

    logger.info(
        "MCP server: exposing %d read-only tools (%s)",
        len(exposed),
        ", ".join(exposed),
    )
    if withheld:
        logger.info(
            "MCP server: withheld %d non-read-only tools: %s",
            len(withheld),
            ", ".join(withheld),
        )
    if missing:
        # A whitelisted tool absent from the catalog means drift between this
        # list and main.py's registration — warn, don't fail.
        logger.warning(
            "MCP server: whitelisted tools absent from catalog: %s", ", ".join(missing)
        )

    return len(exposed)


def reset() -> None:
    """Clear module state. Test-only helper."""
    global _mcp_instance, MCP_SUBAPP, _registered_tool_names
    _mcp_instance = None
    MCP_SUBAPP = None
    _registered_tool_names = set()
