"""Execution algorithms — schedule-based order slicing.

Each algorithm implements the ExecutionAlgo Protocol and produces a list of
ChildOrder slices. The caller is responsible for iterating the schedule and
submitting each slice through ``LiveExecutionEngine.place_order()``.
"""

from selene_space_exchange.execution.algo.base import ChildOrder, ExecutionAlgo
from selene_space_exchange.execution.algo.vwap import VWAPExecutor
from selene_space_exchange.execution.algo.pov import POVExecutor
from selene_space_exchange.execution.algo.adaptive import AdaptiveExecutor

__all__ = [
    "ChildOrder",
    "ExecutionAlgo",
    "VWAPExecutor",
    "POVExecutor",
    "AdaptiveExecutor",
]
