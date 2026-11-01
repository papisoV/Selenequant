"""Shared bootstrap defaults consumed by the setup wizard and doctor repairs.

Both entrypoints must agree on what a pristine ``~/.selenespace`` looks like,
so the canonical config text lives here exactly once. Values are rendered
from a line-template rather than a mapping so the on-disk key order stays
stable across Python versions.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_TEMPLATE_LINES = (
    "env: development",
    "log_level: INFO",
    "data_dir: {data_dir}",
    "db_path: {db_path}",
    "log_dir: {log_dir}",
    "api_host: 127.0.0.1",
    "api_port: {api_port}",
    "default_exchange: binance",
    "default_symbol: BTCUSDT",
    "llm_provider: openai_compatible",
    "llm_base_url: ''",
    "llm_model: ''",
    "llm_timeout_seconds: {llm_timeout_seconds}",
    "enable_real_trading: false",
    "paper_starting_balance: 10000.0",
)


def app_root() -> Path:
    """Per-user application directory."""
    return Path.home() / ".selenespace"


def as_posix(p: Path) -> str:
    """Forward-slash path text so YAML round-trips cleanly on Windows."""
    return f"{p}".replace("\\", "/")


def default_db_path() -> Path:
    from selene_space_storage.sqlite import DEFAULT_DB_PATH

    return DEFAULT_DB_PATH


def default_config_text(
    api_port: int = 8088,
    llm_timeout_seconds: int = 120,
) -> str:
    """Render the pristine config.yaml contents."""
    root = app_root()
    return "\n".join(_TEMPLATE_LINES).format(
        data_dir=as_posix(root / "data"),
        db_path=as_posix(default_db_path()),
        log_dir=as_posix(root / "logs"),
        api_port=api_port,
        llm_timeout_seconds=llm_timeout_seconds,
    ) + "\n"


def compose_config_text(
    patch: dict[str, object] | None = None,
    llm_timeout_seconds: int = 120,
) -> str:
    """Pristine config, optionally overlaid with wizard selections."""
    payload: dict[str, object] = yaml.safe_load(
        default_config_text(llm_timeout_seconds=llm_timeout_seconds)
    ) or {}
    if patch:
        payload.update(patch)
    return yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)


def merge_patch_into_file(path: Path, patch: dict[str, object]) -> None:
    """Overlay ``patch`` onto an existing config file in place."""
    current = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    current.update(patch)
    path.write_text(
        yaml.safe_dump(current, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
