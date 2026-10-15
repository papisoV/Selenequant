"""Locks for selene_space_core.kline_paths — the single source of truth for
the klines SQLite location.

Regression origin: the backend's write side hardcoded a CWD-relative
``data/klines.db`` (landing under ``services/local-api/``) while readers
honoured ``SELENE_KLINES_DB`` at the repo root, so the two DBs drifted
apart. These tests pin "one absolute path, independent of CWD".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_core.kline_paths import default_klines_db_path, repo_root


def test_env_override_wins(monkeypatch, tmp_path):
    target = tmp_path / "custom" / "my.db"
    monkeypatch.setenv("SELENE_KLINES_DB", str(target))
    assert default_klines_db_path() == target.resolve()


def test_env_override_is_expanded_and_absolute(monkeypatch, tmp_path):
    monkeypatch.setenv("SELENE_KLINES_DB", str(tmp_path / "x.db"))
    result = default_klines_db_path()
    assert result.is_absolute()


def test_default_is_repo_root_data_dir(monkeypatch):
    monkeypatch.delenv("SELENE_KLINES_DB", raising=False)
    result = default_klines_db_path()
    assert result.is_absolute()
    assert result.name == "klines.db"
    assert result == repo_root() / "data" / "klines.db"


def test_result_is_cwd_independent(monkeypatch, tmp_path):
    """The whole point: the same answer regardless of process CWD."""
    monkeypatch.delenv("SELENE_KLINES_DB", raising=False)
    first = default_klines_db_path()
    monkeypatch.chdir(tmp_path)
    second = default_klines_db_path()
    assert first == second


def test_empty_env_falls_back_to_repo_root(monkeypatch):
    """An empty string must not be treated as a usable path."""
    monkeypatch.setenv("SELENE_KLINES_DB", "")
    result = default_klines_db_path()
    assert result == repo_root() / "data" / "klines.db"


def test_repo_root_marker_present():
    """repo_root() must point at a real checkout, not site-packages."""
    assert (repo_root() / "pyproject.toml").exists()


@pytest.mark.parametrize("cwd", [".", "packages", "packages/core"])
def test_same_path_from_various_subdirs(monkeypatch, cwd):
    monkeypatch.delenv("SELENE_KLINES_DB", raising=False)
    root = repo_root()
    target = (root / cwd).resolve()
    monkeypatch.chdir(target)
    assert default_klines_db_path() == root / "data" / "klines.db"


def test_returns_path_object(monkeypatch):
    monkeypatch.setenv("SELENE_KLINES_DB", str(Path("rel.db")))
    assert isinstance(default_klines_db_path(), Path)
