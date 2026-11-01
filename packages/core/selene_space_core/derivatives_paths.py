"""Single source of truth for the derivatives SQLite path.

Same failure mode ``kline_paths`` was written to kill, one file over: the
writer (local-api lifespan) opened a CWD-relative ``data/derivatives.db``
— landing inside ``services/local-api/`` — while readers (research council,
serenity, factor bridges) resolved their own paths. ``DerivativesDB.open()``
creates parent dirs and runs the schema on connect, so a mismatched path
does not fail loudly; it manufactures a schema-only DB that looks exactly
like "no derivatives data yet" (40 KB, all tables empty) and silently
disables every funding/OI read.

Precedence mirrors :func:`selene_space_core.kline_paths.default_klines_db_path`:
1. ``SELENE_DERIVATIVES_DB`` when set.
2. ``<repo-root>/data/derivatives.db`` derived from this file's location,
   independent of the process working directory.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from selene_space_core.kline_paths import repo_root

logger = logging.getLogger(__name__)

_ROOT_MARKER = "pyproject.toml"


def default_derivatives_db_path() -> Path:
    """Resolve the canonical derivatives SQLite path (always absolute)."""
    env = os.environ.get("SELENE_DERIVATIVES_DB")
    if env:
        return Path(env).expanduser().resolve()

    root = repo_root()
    if not (root / _ROOT_MARKER).exists():
        logger.warning(
            "derivatives_paths: repo-root marker %r missing under %s — "
            "derivatives DB path may be wrong (installed outside a checkout?)",
            _ROOT_MARKER,
            root,
        )
    return root / "data" / "derivatives.db"
