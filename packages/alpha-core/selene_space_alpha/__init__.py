"""SeleneQuant Alpha - AI-powered factor mining and formula discovery."""

__version__ = "0.1.0"

from .vocab import FORMULA_VOCAB, FormulaVocab
from .vm.vm_pandas import PandasStackVM

__all__ = [
    "AlphaFormulaStrategy",
    "FORMULA_VOCAB",
    "FormulaVocab",
    "PandasStackVM",
    "StackVM",
]


def __getattr__(name: str):
    if name == "AlphaFormulaStrategy":
        from .strategy_bridge import AlphaFormulaStrategy
        return AlphaFormulaStrategy
    if name == "StackVM":
        from .vm import StackVM
        return StackVM
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
