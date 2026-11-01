"""Outbound MCP capability server — read-only + low-risk tools for external AI clients.

Direction is the inverse of agent-runtime's MCP client: selene is the *server*
here, exposing a small read-only surface (backtest, tuning lookup, kline
analysis) to Claude Desktop / Copilot-style clients.  Every tool declares a
``risk_level`` and returns only redacted payloads.
"""
