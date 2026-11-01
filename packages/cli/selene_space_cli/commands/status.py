"""`selenespace status` — one-glance environment summary."""

from __future__ import annotations

import os
from pathlib import Path

import click
import httpx

from selene_space_cli.constants import cli_version
from selene_space_core.config import load_config

_BINANCE_ENV_KEYS = ("BINANCE_API_KEY", "SELENE_BINANCE_API_KEY")
_LLM_ENV_KEYS = ("OPENAI_API_KEY", "SELENE_LLM_API_KEY")
_BINANCE_SECRET_REL = ("secrets", "binance", "api_key")
_HEALTH_TIMEOUT_S = 2.0


def _tilde_path(p: Path) -> str:
    try:
        return "~/" + "/".join(p.relative_to(Path.home()).parts)
    except ValueError:
        return str(p)


def _env_configured(names) -> bool:
    return any(os.environ.get(n) for n in names)


def _file_configured(rel) -> bool:
    return (Path.home().joinpath(".selenespace", *rel)).is_file()


class _ApiHealth:
    """Probe the local API once; ``live`` says whether /health answered."""

    __slots__ = ("url", "live")

    def __init__(self, port_number: int) -> None:
        self.url = f"http://localhost:{port_number}"
        endpoint = f"http://127.0.0.1:{port_number}/api/v1/health"
        self.live = False
        try:
            with httpx.Client(timeout=_HEALTH_TIMEOUT_S) as session:
                self.live = session.get(endpoint).status_code == 200
        except httpx.HTTPError:
            pass

    @property
    def state_word(self) -> str:
        return "Running" if self.live else "Stopped"


def _exchange_line() -> str:
    ready = _env_configured(_BINANCE_ENV_KEYS) or _file_configured(_BINANCE_SECRET_REL)
    return "Configured" if ready else "Not configured"


def _llm_line(cfg) -> str:
    via_env = _env_configured(_LLM_ENV_KEYS)
    via_base = bool((cfg.llm_base_url or "").strip())
    return "Configured" if (via_env or via_base) else "Not configured"


def _db_location(cfg) -> Path:
    raw = Path(cfg.db_path)
    return raw if raw.is_absolute() else raw.resolve()


def _bracket(path: Path) -> str:
    return "OK" if path.is_file() else "Missing"


def _collect_rows(cfg) -> list[tuple[str, str]]:
    config_file = Path.home() / ".selenespace" / "config.yaml"
    database_file = _db_location(cfg)
    health = _ApiHealth(int(cfg.api_port))
    return [
        ("Config", f"{_tilde_path(config_file)} [{_bracket(config_file)}]"),
        ("Database", f"{_tilde_path(database_file)} [{_bracket(database_file)}]"),
        ("Exchange", _exchange_line()),
        ("LLM", _llm_line(cfg)),
        ("API Server", f"{health.url} [{health.state_word}]"),
    ]


@click.command("status")
def status() -> None:
    """Show SeleneQuant environment status."""
    cfg = load_config()
    width = max(len(title) for title, _ in _collect_rows(cfg)) + 4
    lines = [
        f"SeleneQuant v{cli_version()}",
        "─────────────────────────────",
    ]
    # NOTE: rows are computed twice (once here for width) only because the
    # health probe is cheap; a cached variant would pass rows in instead.
    for title, detail in _collect_rows(cfg):
        lines.append(f"{title + ':':<{width}}{detail}")
    click.echo("\n".join(lines))
