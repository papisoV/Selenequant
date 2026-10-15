"""Make sibling ``factor-research`` and the local ``tests`` dir importable.

When the alpha-core suite runs in isolation (``cd packages/alpha-core && pytest
tests/``) the standalone ``selene_space_factor_research`` package is not on the
path; the root pyproject already adds it via ``[tool.pytest] pythonpath`` for
repo-wide runs. Keep both invocations working by inserting the sibling dir here.
The ``tests`` dir is a namespace package, so ``_module_loader`` (shared by the
path-loading tests) needs an explicit path entry to be importable.
"""

import pathlib
import sys

_SIBLING = pathlib.Path(__file__).resolve().parent.parent / "factor-research"
if str(_SIBLING) not in sys.path:
    sys.path.insert(0, str(_SIBLING))

_TESTS = pathlib.Path(__file__).resolve().parent / "tests"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))
