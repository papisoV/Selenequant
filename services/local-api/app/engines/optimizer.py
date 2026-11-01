"""Optimization engine — grid search and LLM-based parameter suggestion.

Provides:
  - run_optimization: Grid search over a parameter space, running backtests
    for each combination and returning the best parameters.
  - suggest_param_ranges: LLM suggests parameter ranges for a strategy.
    Falls back to default ranges when LLM is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.llm_helper import call_llm, is_llm_configured

logger = logging.getLogger(__name__)

# Default parameter ranges for common strategy types
_DEFAULT_PARAM_RANGES: dict[str, dict[str, list[Any]]] = {
    "default": {
        "period": [7, 14, 21, 30, 50],
        "threshold": [0.01, 0.02, 0.03, 0.05],
        "stop_loss_pct": [0.02, 0.03, 0.05, 0.08],
        "take_profit_pct": [0.03, 0.05, 0.08, 0.12],
    },
}


async def run_optimization(
    strategy_id: str,
    param_space: dict[str, list[Any]],
    settings_service: Any,
) -> dict[str, Any]:
    """Run grid-search optimization over param_space.

    Stub implementation — full version would:
      1. Enumerate all combinations from param_space
      2. Run a backtest for each combination
      3. Rank by sharpe_ratio (or user-specified metric)
      4. Return the best parameters

    Returns template results for the community edition.
    """
    logger.info("Optimization requested for strategy %s with %d params", strategy_id, len(param_space))

    # Compute total grid size
    grid_size = 1
    for values in param_space.values():
        grid_size *= len(values)

    # Stub: return template best params (first value of each param)
    best_params: dict[str, Any] = {}
    for key, values in param_space.items():
        if values:
            best_params[key] = values[0]

    return {
        "strategy_id": strategy_id,
        "grid_size": grid_size,
        "best_params": best_params,
        "best_sharpe": 0.0,
        "best_return": 0.0,
        "all_results": [],
        "status": "completed_stub",
    }


async def suggest_param_ranges(
    settings_service: Any,
    strategy_id: str,
) -> dict[str, Any]:
    """LLM suggests parameter ranges for a strategy.

    Falls back to default ranges when LLM is unavailable.
    """
    llm_available = await is_llm_configured(settings_service)

    if llm_available:
        try:
            prompt = (
                f"Suggest optimization parameter ranges for trading strategy '{strategy_id}'.\n"
                "Return a JSON object where each key is a parameter name and each value is "
                "an array of 4-6 values to test.\n"
                "Common parameters: period (int), threshold (float), stop_loss_pct (float), "
                "take_profit_pct (float).\n"
                "Only output the JSON object, no explanation."
            )
            response = await call_llm(
                settings_service,
                prompt,
                system="You are a quantitative strategy optimizer. Output only valid JSON.",
                max_tokens=400,
            )
            if response:
                import json

                try:
                    suggested = json.loads(response)
                    if isinstance(suggested, dict):
                        return {
                            "strategy_id": strategy_id,
                            "suggestions": suggested,
                            "source": "llm",
                        }
                except json.JSONDecodeError:
                    logger.warning("LLM returned non-JSON for param suggestions, using defaults")
        except Exception as exc:
            logger.warning("LLM param suggestion failed: %s", exc)

    # Fallback: default ranges
    return {
        "strategy_id": strategy_id,
        "suggestions": _DEFAULT_PARAM_RANGES["default"],
        "source": "default",
    }
