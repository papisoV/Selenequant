"""Single source of truth for on-disk data paths shared across packages.

The klines SQLite DB is consumed by the local-api service, the agent
runtime, alpha-core, factor-research and the CLI scripts. Before this
module each consumer resolved the path itself, so the *writer* (the
backend's increment sync) and the *readers* could disagree — the writer
opened a CWD-relative ``data/klines.db`` (landing inside
``services/local-api/``) while readers honoured ``SELENE_KLINES_DB``
pointing at the repo root. The two forks then drifted apart silently.

Every consumer should call :func:`default_klines_db_path` instead of
hardcoding a path, so the location is decided in exactly one place.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Repo-root marker: its presence distinguishes a real checkout from an
# accidental install into site-packages (which this project forbids — see
# CLAUDE.md's ``pip install -e .`` ban). A wrong root silently creates an
# empty DB that looks like "no data yet", which is worse than a loud error.
_ROOT_MARKER = "pyproject.toml"


def repo_root() -> Path:
    """Absolute path to the repository root, derived from this file's location.

    ``kline_paths.py`` -> ``selene_space_core`` -> ``core`` -> ``packages``
    -> repo root, so ``parents[3]``. Resolved (not just absolute) so the
    value is stable regardless of how the caller was started.
    """
    return Path(__file__).resolve().parents[3]


def default_klines_db_path() -> Path:
    """Resolve the canonical klines SQLite path.

    Precedence:
    1. ``SELENE_KLINES_DB`` when set (the existing, already-wired override
       point — ``core/config.py`` maps ``db_path`` onto it).
    2. ``<repo-root>/data/klines.db`` derived from this file's location,
       so the result does not depend on the process working directory.

    Always returns an absolute path.
    """
    env = os.environ.get("SELENE_KLINES_DB")
    if env:
        return Path(env).expanduser().resolve()

    root = repo_root()
    if not (root / _ROOT_MARKER).exists():
        logger.warning(
            "kline_paths: repo-root marker %r missing under %s — klines DB "
            "path may be wrong (installed outside a checkout?)",
            _ROOT_MARKER,
            root,
        )
    return root / "data" / "klines.db"
