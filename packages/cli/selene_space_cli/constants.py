"""Shared constants for the SeleneQuant CLI.

Paths derive from the user home at import time; ``cli_version`` prefers the
installed distribution metadata and falls back to a source-tree default so
the command works from a checkout without installation.
"""

from __future__ import annotations

from pathlib import Path

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version


def _user_home() -> Path:
    """Indirection kept so tests can patch the home lookup if ever needed."""
    return Path.home()


def _app_dir() -> Path:
    """Per-user application root (~/.selenespace)."""
    return _user_home() / ".selenespace"


APP_DIR: Path = _app_dir()
DEFAULT_CONFIG_PATH = APP_DIR / "config.yaml"
DEFAULT_DATA_DIR = APP_DIR / "data"
DEFAULT_LOG_DIR = APP_DIR / "logs"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "selenespace.db"
SCHEMA_VERSION = "0.1"

_DISTRIBUTION_NAME = "selene-space-cli"
_SOURCE_FALLBACK_VERSION = "0.1.0"


def cli_version() -> str:
    """Installed distribution version, or a source-tree fallback."""
    try:
        return _installed_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return _SOURCE_FALLBACK_VERSION
