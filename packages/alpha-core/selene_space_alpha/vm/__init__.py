"""Virtual machine for formula execution - both torch and pandas."""

from .vm_pandas import PandasStackVM

__all__ = ["StackVM", "PandasStackVM"]


def __getattr__(name: str):
    if name == "StackVM":
        from .vm import StackVM
        return StackVM
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
