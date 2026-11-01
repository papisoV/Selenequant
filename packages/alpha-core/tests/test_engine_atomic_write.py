"""Atomic-write crash safety for engine._save_results.

Locks the contract that best_meme_strategy.json / training_history.json
are written via ``selene_space_core.infra.atomic_write``: if the write
dies mid-flight, the previously persisted file survives byte-identical
and no truncated file is ever exposed at the target path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types

from selene_space_core.infra import atomic_write as atomic_write_mod

_CRASH_CHILD = """
import json, os
target = {target!r}
from selene_space_core.infra.atomic_write import atomic_write

class Boom(Exception):
    pass

def dying(src, dst):
    raise Boom('simulated crash mid-write')
os.replace = dying
try:
    atomic_write(target, json.dumps({{'best_score': ['corrupted']}}))
except Boom:
    pass
"""


def test_atomic_write_survives_forced_crash(tmp_path) -> None:
    """Crash between tmp-write and os.replace: target file stays intact."""
    target = tmp_path / "training_history.json"
    original = {"best_score": [1.0, 2.0], "avg_reward": [0.5]}
    target.write_text(json.dumps(original), encoding="utf-8")

    # Subprocess doesn't inherit pytest's pythonpath; __file__ is
    # packages/core/selene_space_core/infra/atomic_write.py → three dirs up = packages/core.
    core_root = os.path.dirname(
        os.path.dirname(os.path.dirname(atomic_write_mod.__file__))
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = core_root + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, "-c", _CRASH_CHILD.format(target=str(target))],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr

    survived = json.loads(target.read_text(encoding="utf-8"))
    assert survived == original
    assert not list(
        tmp_path.glob(".training_history.json.tmp-*")
    ), "tmp sibling must not linger after a failed write"


def test_engine_save_results_is_atomic(monkeypatch, tmp_path) -> None:
    """AlphaEngine._save_results routes both files through atomic_write."""
    from selene_space_alpha.mining import engine as engine_mod

    calls: list[str] = []

    def spy_write(path, data):
        calls.append(str(path))
        atomic_write_mod.atomic_write(path, data)

    monkeypatch.setattr(engine_mod, "atomic_write", spy_write)

    stub = types.SimpleNamespace(
        best_formula={"formula": [1, 2]},
        best_score=1.5,
        output_dir=tmp_path,
        training_history={"avg_reward": [1.0]},
    )
    engine_mod.AlphaEngine._save_results(stub)  # type: ignore[arg-type]

    assert any("best_meme_strategy.json" in p for p in calls)
    assert any("training_history.json" in p for p in calls)
    assert json.loads((tmp_path / "best_meme_strategy.json").read_text()) == {
        "formula": [1, 2]
    }
    assert json.loads((tmp_path / "training_history.json").read_text()) == {
        "avg_reward": [1.0]
    }
