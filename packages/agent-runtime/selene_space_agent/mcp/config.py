"""Load MCP server configuration from a YAML file. Never raises."""

from __future__ import annotations

from pathlib import Path

import yaml

from selene_space_agent.mcp.types import McpConfig, McpServerConfig

DEFAULT_CONFIG_PATH = Path.home() / ".selenespace" / "config.yaml"


def load_mcp_config(path: Path | str | None = None) -> McpConfig:
    """Read ``mcp.servers`` from YAML; any problem yields an empty config."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        return McpConfig()

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return McpConfig()

    if not isinstance(data, dict):
        return McpConfig()

    mcp_section = data.get("mcp")
    if not isinstance(mcp_section, dict):
        return McpConfig()

    servers_section = mcp_section.get("servers")
    if not isinstance(servers_section, dict):
        return McpConfig()

    servers: dict[str, McpServerConfig] = {}
    for name, entry in servers_section.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        try:
            servers[name] = McpServerConfig.model_validate(entry)
        except Exception:
            continue
    return McpConfig(servers=servers)
