"""The deploy → backtest seam: the formula lands where the reader looks.

``alpha.deploy_formula`` mirrors into ``data/strategies/alpha_v_deploy/`` and
``backtests.py``'s ``alpha:{id}`` branch globs ``data/strategies/alpha_v*``.
Both bases are CWD-relative because the API serves from ``services/local-api``.
If either side re-anchors (repo root, absolute path), deploy still reports
success and the backtest then can't find the formula it just deployed — a
silent break, so it's pinned here rather than left to review.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.api.v1 import alpha
from app.api.v1.backtests import _find_alpha_archive


def test_writer_archive_dir_is_inside_reader_glob_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Relative, not resolved: this is the CWD-relative base the reader globs.
    assert alpha._deploy_archive_dir == Path("data/strategies/alpha_v_deploy")

    archive = alpha._deploy_archive_dir
    archive.mkdir(parents=True)
    (archive / "f1.json").write_text(
        json.dumps(
            {
                "id": "alpha:f1",
                "formula": {"tokens": [0, 6, 3, 1]},
                "vocab": {"feature_names": ["X"], "operator_names": ["RET"]},
            }
        ),
        encoding="utf-8",
    )

    found = _find_alpha_archive("f1")
    assert found is not None
    assert found["formula"]["tokens"] == [0, 6, 3, 1]


def test_missing_archive_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _find_alpha_archive("nope") is None
