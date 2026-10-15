"""selene_space_paper — paper-trading simulation for SeleneQuant.

Decimal-precise in-memory bookkeeping (accounts / orders / positions / fills),
a gated decision pipeline front-door, and an async execution engine mirroring
the platform's live-engine surface. Grid-strategy support lives in the
companion ``grid_*`` modules.
"""

import importlib

_SUBMODULE_EXPORTS = {
    "accounts": [
        "AccountManager",
        "AccountStatus",
        "PaperAccount",
    ],
    "orders": [
        "InvalidOrderTransition",
        "PaperOrderManager",
    ],
    "fills": [
        "FixedPaperSlippage",
        "RealisticSlippage",
        "try_fill",
    ],
    "positions": ["PositionManager"],
    "pnl": [
        "calculate_account_pnl",
        "calculate_pnl",
    ],
    "state": ["PaperState"],
    "decision_pipeline": [
        "DecisionPipeline",
        "PipelineAction",
        "PipelineConfig",
        "PipelineResult",
    ],
    "execution_engine": ["ExecutionEngine"],
    "paper_execution": ["PaperExecutionEngine"],
    # grid strategy companion modules
    "grid_types": [
        "GridConfig",
        "GridHealth",
        "GridState",
        "GridStats",
        "OutOfRangeAction",
        "RangeAdjustment",
        "RecoveryConfig",
    ],
    "grid_math": [
        "FilledOrder",
        "GridSpec",
        "ReplacementOrder",
        "SeedOrder",
        "build_grid",
        "is_reduce_only",
        "replacement_for",
        "rung_profit",
        "seed_orders",
    ],
    "grid_engine": ["GridExecutionEngine"],
    "grid_state_repo": ["GridStateRepository"],
}


def _reexport() -> list[str]:
    names: list[str] = []
    for module_name, export_names in _SUBMODULE_EXPORTS.items():
        module = importlib.import_module(f".{module_name}", __name__)
        for export_name in export_names:
            globals()[export_name] = getattr(module, export_name)
            names.append(export_name)
    return names


__all__ = sorted(_reexport())
