"""Shared fixtures for the strategy-engine test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


class _YamlDocWriter:
    """Drops YAML payloads into a pytest-managed scratch directory."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir

    def __call__(self, payload: str, filename: str = "strategy.yaml") -> Path:
        return self.write(payload, filename)

    def write(self, payload: str, filename: str = "strategy.yaml") -> Path:
        target = self._base_dir / filename
        target.write_text(payload, encoding="utf-8")
        return target


@pytest.fixture
def tmp_yaml(tmp_path: Path) -> _YamlDocWriter:
    """Scratch-directory YAML writer handed to loader tests."""
    return _YamlDocWriter(tmp_path)
