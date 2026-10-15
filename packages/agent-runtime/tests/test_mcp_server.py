"""Tests for the MCP server module (read-only exposure + mount contract).

These lock the security property that matters: a side-effecting tool must never
reach the MCP surface, regardless of how it is labelled in the catalog.
"""

from __future__ import annotations

import asyncio

import pytest

from selene_space_agent.mcp import server as mcp_server
from selene_space_types.risk import RiskLevel

pytest.importorskip("fastmcp")


class _FakeTool:
    """Minimal BaseTool stand-in with a recorded execute()."""

    def __init__(self, name: str, risk: RiskLevel = RiskLevel.SAFE) -> None:
        self._name = name
        self._risk = risk
        self.calls: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"fake {self._name}"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return self._risk

    def execute(self, args: dict):
        self.calls.append(args)
        from selene_space_agent.tools.base import ToolResult

        return ToolResult(output=f"{self._name}:{args.get('symbol')}")


class _FakeCatalog:
    """Stands in for ToolCatalog — only tool_names()/get() are used."""

    def __init__(self, tools: list[_FakeTool]) -> None:
        self._tools = {t.name: t for t in tools}

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str):
        return self._tools.get(name)


def _catalog_with(*names: str) -> tuple[_FakeCatalog, dict[str, _FakeTool]]:
    tools = {n: _FakeTool(n) for n in names}
    return _FakeCatalog(list(tools.values())), tools


@pytest.fixture(autouse=True)
def _clean_module_state():
    mcp_server.reset()
    yield
    mcp_server.reset()


def test_read_only_whitelist_excludes_dangerous_tools():
    """The allowlist must not contain any known side-effecting tool."""
    for bad in (
        "python_exec",
        "paper_place_order",
        "paper_create_account",
        "backtest_run",
        "strategy_save_version",
        "strategy_deploy",
        "strategy_stop",
        "strategy_validate",
    ):
        assert bad not in mcp_server.READ_ONLY_TOOLS, f"{bad} must never be exposed"


def test_register_exposes_only_whitelisted():
    """A catalog with read-only + write tools → only read-only ones registered."""
    read = ["market_ticker", "risk_check", "read_skill"]
    write = ["python_exec", "paper_place_order", "strategy_deploy"]
    catalog, _ = _catalog_with(*(read + write))

    mcp_server.build_subapp()
    count = mcp_server.register_tools(catalog)

    assert count == len(read)
    names = {t.name for t in asyncio.run(mcp_server._mcp_instance.list_tools())}
    for r in read:
        assert r in names
    for w in write:
        assert w not in names, f"{w} leaked into MCP surface"


def test_register_is_idempotent():
    catalog, _ = _catalog_with("market_ticker", "risk_check")
    mcp_server.build_subapp()
    mcp_server.register_tools(catalog)
    mcp_server.register_tools(catalog)  # second call must not duplicate
    names = [t.name for t in asyncio.run(mcp_server._mcp_instance.list_tools())]
    assert len(names) == len(set(names)) == 2


def test_missing_whitelisted_tool_is_tolerated(caplog):
    """A whitelisted name absent from the catalog warns but does not fail."""
    catalog, _ = _catalog_with("market_ticker")  # rest of whitelist absent
    mcp_server.build_subapp()
    count = mcp_server.register_tools(catalog)
    assert count == 1


def test_tool_result_error_becomes_mcp_error():
    """A tool returning ToolResult(error=...) must raise, not silently succeed."""
    from selene_space_agent.tools.base import ToolResult

    tool = _FakeTool("market_ticker")
    tool.execute = lambda args: ToolResult(output="", error="no data")  # type: ignore[method-assign]
    fn = mcp_server._make_tool_fn(tool)
    with pytest.raises(RuntimeError, match="no data"):
        asyncio.run(fn(symbol="BTC/USDT"))


def test_tool_result_output_passthrough():
    tool = _FakeTool("market_ticker")
    fn = mcp_server._make_tool_fn(tool)
    out = asyncio.run(fn(symbol="BTC/USDT"))
    assert out == "market_ticker:BTC/USDT"
    assert tool.calls == [{"symbol": "BTC/USDT"}]


def test_build_subapp_returns_none_without_fastmcp(monkeypatch):
    """fastmcp missing → build_subapp degrades to None (backend still starts)."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "fastmcp" or name.startswith("fastmcp."):
            raise ImportError("no fastmcp")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    mcp_server.reset()
    assert mcp_server.build_subapp() is None


def test_whitelist_is_subset_of_known_top_level_names():
    """Sanity: whitelist entries are plain tool names (no accidental typos/prefixes)."""
    for n in mcp_server.READ_ONLY_TOOLS:
        assert n == n.lower()
        assert " " not in n and "/" not in n


def test_end_to_end_mount_over_asgi():
    """Full contract: mount a sub-app, chain lifespan, list + call over MCP.

    This is the integration proof — a bare tools/list without the lifespan chain
    raises 'Task group is not initialized' (see server.py docstring), so this
    test would fail if the mount wiring regressed.
    """
    from contextlib import asynccontextmanager

    from fastapi import FastAPI

    catalog, tools = _catalog_with("market_ticker", "risk_check", "python_exec")

    sub = mcp_server.build_subapp()
    assert sub is not None
    app = FastAPI()
    app.mount(mcp_server.MOUNT_PATH, sub)

    @asynccontextmanager
    async def _life(a):
        mcp_server.register_tools(catalog)
        async with sub.lifespan(a):
            yield

    app.router.lifespan_context = _life

    async def _run():
        import httpx
        from fastmcp import Client
        from fastmcp.client.transports import StreamableHttpTransport

        # Drive the REAL HTTP path (mount prefix included), not an in-process
        # shortcut: the mount + lifespan wiring is exactly what this test locks.
        def _factory(**kw):
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                **kw,
            )

        transport = StreamableHttpTransport(
            url=f"http://testserver{mcp_server.MOUNT_PATH}/",
            httpx_client_factory=_factory,
        )
        async with _life(app):
            async with Client(transport) as client:
                names = sorted(t.name for t in await client.list_tools())
                assert names == ["market_ticker", "risk_check"]  # python_exec withheld
                result = await client.call_tool("market_ticker", {"symbol": "BTC/USDT"})
                assert "market_ticker:BTC/USDT" in str(result)

    asyncio.run(_run())
    assert tools["python_exec"].calls == []  # never invocable
