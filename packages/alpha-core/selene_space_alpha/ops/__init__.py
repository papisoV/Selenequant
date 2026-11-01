"""Operator definitions - both torch and pandas implementations."""

from .registry import OPERATOR_DEFS

# OPS_CONFIG (torch-native) lives in ops.py — import it directly when torch is available:
#   from selene_space_alpha.ops.ops import OPS_CONFIG
# This avoids triggering torch import at package level.

__all__ = ["OPERATOR_DEFS"]
