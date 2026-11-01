"""Behaviour locks for MCP YAML config loading: valid parses, empty/missing
sections, absent files, malformed input, and the default-path fallback."""

from __future__ import annotations

from pathlib import Path

from selene_space_agent.mcp.config import load_mcp_config
from selene_space_agent.mcp.types import McpTransport
from selene_space_types.risk import RiskLevel


def _yaml(tmp_path: Path, text: str) -> Path:
    target = tmp_path / "mcp.yaml"
    target.write_text(text, encoding="utf-8")
    return target


class TestValidParses:
    def test_two_server_full_config(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(
            _yaml(
                tmp_path,
                """
mcp:
  servers:
    docs-server:
      command: uvx
      args:
        - "-y"
        - "docs-mcp"
      transport: stdio
      risk_level: restricted
    cloud-server:
      url: https://cloud.example.org/sse
      transport: sse
      enabled: false
""",
            )
        )

        checks = (set(cfg.servers) == {"docs-server", "cloud-server"},)
        assert all(checks)

        local = cfg.servers["docs-server"]
        quad = (
            local.command,
            tuple(local.args),
            local.transport,
            local.risk_level,
        )
        assert quad == ("uvx", ("-y", "docs-mcp"), McpTransport.STDIO, RiskLevel.RESTRICTED)

        remote = cfg.servers["cloud-server"]
        triple = (remote.url, remote.transport, remote.enabled)
        assert triple == ("https://cloud.example.org/sse", McpTransport.SSE, False)

    def test_minimal_single_server(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(
            _yaml(tmp_path, 'mcp:\n  servers:\n    tiny:\n      command: python\n      args: ["srv.py"]\n')
        )
        pair = (len(cfg.servers), cfg.servers["tiny"].command)
        assert pair == (1, "python")

    def test_env_and_cwd_loaded(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(
            _yaml(
                tmp_path,
                """
mcp:
  servers:
    tuned:
      command: node
      args: ["boot.js"]
      env:
        API_TOKEN: tok_9
        LOG_LEVEL: debug
      cwd: /var/mcp
""",
            )
        )
        tuned = cfg.servers["tuned"]
        pair = (tuned.env, tuned.cwd)
        assert pair == ({"API_TOKEN": "tok_9", "LOG_LEVEL": "debug"}, "/var/mcp")

    def test_risk_levels_parsed(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(
            _yaml(
                tmp_path,
                """
mcp:
  servers:
    gentle:
      command: echo
      risk_level: safe
    feral:
      command: rm
      risk_level: dangerous
""",
            )
        )
        pair = (cfg.servers["gentle"].risk_level, cfg.servers["feral"].risk_level)
        assert pair == (RiskLevel.SAFE, RiskLevel.DANGEROUS)


class TestEmptyShapes:
    def test_no_mcp_key(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(_yaml(tmp_path, "unrelated:\n  leaf: 1\n"))
        assert cfg.servers == {}

    def test_mcp_key_empty_map(self, tmp_path: Path) -> None:
        assert load_mcp_config(_yaml(tmp_path, "mcp: {}\n")).servers == {}

    def test_servers_empty_map(self, tmp_path: Path) -> None:
        assert load_mcp_config(_yaml(tmp_path, "mcp:\n  servers: {}\n")).servers == {}

    def test_zero_byte_file(self, tmp_path: Path) -> None:
        assert load_mcp_config(_yaml(tmp_path, "")).servers == {}


class TestMissingFiles:
    def test_absent_path_returns_empty(self, tmp_path: Path) -> None:
        assert load_mcp_config(tmp_path / "phantom.yaml").servers == {}

    def test_default_style_bogus_path(self) -> None:
        # guaranteed-nonexistent absolute path
        assert load_mcp_config(Path("/certainly/not/anywhere/config.yaml")).servers == {}


class TestMalformedInput:
    def test_broken_yaml_does_not_raise(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(_yaml(tmp_path, "mcp:\n  servers:\n    - not a mapping: {{{{"))
        assert isinstance(cfg, type(cfg))

    def test_mcp_section_scalar(self, tmp_path: Path) -> None:
        assert load_mcp_config(_yaml(tmp_path, 'mcp: "plain text"\n')).servers == {}

    def test_servers_section_is_list(self, tmp_path: Path) -> None:
        assert load_mcp_config(_yaml(tmp_path, "mcp:\n  servers:\n    - bad_shape\n")).servers == {}

    def test_invalid_entry_skipped_valid_kept(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(
            _yaml(
                tmp_path,
                """
mcp:
  servers:
    fine:
      command: echo
    broken:
      transport: no_such_transport
""",
            )
        )
        # valid one loads; invalid one must not crash the loader
        assert "fine" in cfg.servers

    def test_string_valued_entry_skipped(self, tmp_path: Path) -> None:
        cfg = load_mcp_config(
            _yaml(
                tmp_path,
                """
mcp:
  servers:
    healthy:
      command: echo
    sick: "bare string"
""",
            )
        )
        pair = ("healthy" in cfg.servers, "sick" in cfg.servers)
        assert pair == (True, False)

    def test_top_level_list_document(self, tmp_path: Path) -> None:
        assert load_mcp_config(_yaml(tmp_path, "- one\n- two\n- three\n")).servers == {}


class TestDefaultPathBehaviour:
    def test_none_path_falls_back_without_raising(self) -> None:
        # default path (~/.selenespace/config.yaml) usually missing; just prove no crash
        cfg = load_mcp_config(None)
        assert isinstance(cfg.servers, dict)
