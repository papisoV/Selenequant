"""Auto-repair actions backing `selenespace doctor --repair`.

Every action is idempotent: directories are created only when missing, the
default config is written only when absent, and the SQLite bootstrap is a
connect/close cycle that leaves an initialized schema behind. Permission
hardening applies POSIX owner-only modes where the platform supports them
and quietly skips elsewhere.
"""

from __future__ import annotations

import stat
import asyncio
import sys
from pathlib import Path

import yaml

from selene_space_cli import _defaults as _app_paths
from selene_space_cli.constants import SCHEMA_VERSION

_OWNER_ONLY = stat.S_IRWXU
_SECRET_FILE_MODE = 0o600


class _PosixChmod:
    """chmod wrapper that silently no-ops on Windows."""

    def __init__(self, mode: int) -> None:
        self.mode = mode

    def apply(self, target: Path) -> bool:
        if sys.platform == "win32":
            return False
        try:
            target.chmod(self.mode)
            return True
        except OSError:
            return False


_DIR_HARDENER = _PosixChmod(_OWNER_ONLY)
_FILE_HARDENER = _PosixChmod(_SECRET_FILE_MODE)


def chmod_user_only_path(path: Path) -> None:
    """Restrict a directory to its owner on POSIX; no-op on Windows."""
    _DIR_HARDENER.apply(path)
    # Windows returns False by design; ACLs there are handled by the OS user profile.


def chmod_secret_files() -> None:
    """Walk the secrets tree applying 0600 to every regular file found."""
    secrets_root = _app_paths.app_root() / "secrets"
    if not secrets_root.is_dir():
        return
    candidates = sorted(item for item in secrets_root.rglob("*") if item.is_file())
    for item in candidates:
        _FILE_HARDENER.apply(item)


_LAYOUT_SUBDIRS = ("data", "logs", "paper", "secrets")


def ensure_layout() -> None:
    """Create every expected subdirectory and harden permissions."""
    root = _app_paths.app_root()
    missing = [leaf for leaf in _LAYOUT_SUBDIRS if not (root / leaf).is_dir()]
    for leaf in missing or _LAYOUT_SUBDIRS:
        (root / leaf).mkdir(parents=True, exist_ok=True)
    chmod_user_only_path(root)
    chmod_secret_files()


def write_default_config_if_missing() -> Path | None:
    """Write the canonical first-run config; None when it already exists."""
    destination = _app_paths.app_root() / "config.yaml"
    if destination.is_file():
        return None
    body = yaml.safe_load(_app_paths.default_config_text(llm_timeout_seconds=60)) or {}
    rendered = yaml.safe_dump(
        body,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    destination.write_text(rendered, encoding="utf-8")
    return destination


async def _touch_db(db_path: Path) -> None:
    from selene_space_storage.sqlite import AsyncSQLiteManager

    manager = AsyncSQLiteManager(db_path)
    await manager.connect()
    await manager.close()


def init_sqlite() -> None:
    """Bootstrap schema by opening and closing a manager connection."""
    asyncio.run(_touch_db(_app_paths.default_db_path()))


def write_schema_sidecar() -> None:
    """Stamp the compiled-in schema version next to config.yaml.

    The sidecar is a plain text marker (not YAML) so the doctor check can
    compare it with a single read+strip against SCHEMA_VERSION.
    """
    root = _app_paths.app_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = f"{SCHEMA_VERSION}\n"
    (root / "schema_version").write_text(stamp, encoding="utf-8")


def run_standard_repairs() -> list[str]:
    """Apply every safe fix-up and describe what happened.

    Returns one human-readable line per action, suitable for direct echo
    into the doctor report under the ``Repair:`` prefix. Steps run in a
    fixed order — layout, config, schema sidecar, database — because later
    steps assume the directories earlier ones create.
    """
    notes: list[str] = []
    ensure_layout()
    notes.append("Ensured ~~/.selenespace directories exist")
    fresh_config = write_default_config_if_missing()
    if fresh_config is not None:
        notes.append(f"Created default config at {fresh_config}")
    else:
        notes.append("Default config already present; left untouched")
    write_schema_sidecar()
    notes.append(f"Stamped schema sidecar ({SCHEMA_VERSION})")
    init_sqlite()
    db_location = _app_paths.default_db_path()
    notes.append(f"Initialized SQLite at {db_location}")
    return notes
