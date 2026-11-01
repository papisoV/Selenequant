"""Search imports must not pull torch.

The DSL / structure-validation modules are pandas-only by design: importing
``selene_space_factor_research.mining.{dsl, structure_validation, dsl_config,
fitness, ..., pipeline}`` in a fresh interpreter must leave ``torch`` out of
``sys.modules``. Runs in a subprocess so a stale torch import in this test
process cannot mask a leak.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

_PKG_DIRS = [
    str(pathlib.Path(__file__).resolve().parents[3] / "packages" / "factor-research"),
    str(pathlib.Path(__file__).resolve().parents[3] / "packages" / "alpha-core"),
    str(pathlib.Path(__file__).resolve().parents[3] / "packages" / "core"),
]

_CODE = (
    "import sys\n"
    f"sys.path[:0] = {_PKG_DIRS!r}\n"
    "import selene_space_factor_research.mining.dsl\n"
    "import selene_space_factor_research.mining.structure_validation\n"
    "import selene_space_factor_research.mining.dsl_config\n"
    "import selene_space_factor_research.mining.fitness\n"
    "import selene_space_factor_research.mining.search_config\n"
    "import selene_space_factor_research.mining.genetic_search\n"
    "import selene_space_factor_research.mining.runner\n"
    "import selene_space_factor_research.mining.validation_protocol\n"
    "import selene_space_factor_research.mining.report\n"
    "import selene_space_factor_research.mining.pipeline\n"
    "import selene_space_factor_research.mining.deterministic_fitness\n"
    "import selene_space_factor_research.mining.ledger_bridge\n"
    "import selene_space_alpha.vocab\n"
    "print('TORCH_PRESENT' if 'torch' in sys.modules else 'TORCH_FREE')\n"
)


def test_search_imports_do_not_pull_torch() -> None:
    proc = subprocess.run([sys.executable, "-c", _CODE], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "TORCH_FREE" in proc.stdout
