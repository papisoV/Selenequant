"""Strategy tools — validate, generate, explain, version, deploy, backtest.

Module-level shared state:

- ``get_results_store()`` — the single in-memory backtest results store
  (also the source of truth for services/local-api backtest tasks).
- ``_evict_oldest_results()`` — cap enforcement by oldest ``created_at``.
- ``_sanitize_config(cfg)`` — in-place removal of None-valued keys before
  handing a strategy config dict to the compiler.

Async tools (backtest run, save/deploy/stop) deliberately refuse sync
``execute()`` so side-effectful actions never bypass policy checks.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pandas as pd
from pydantic import ValidationError

from selene_space_types.risk import RiskLevel
from selene_space_types.strategy import BacktestResult, StrategyConfig, StrategyType

from selene_space_agent.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared results store (single source of truth across agent + API layer)
# ---------------------------------------------------------------------------

_MAX_RESULTS = 500

_results_store: dict[str, BacktestResult] = {}


def get_results_store() -> dict[str, BacktestResult]:
    return _results_store


def _evict_oldest_results() -> None:
    while len(_results_store) > _MAX_RESULTS:
        oldest_id = min(_results_store, key=lambda k: _results_store[k].created_at)
        _results_store.pop(oldest_id, None)


_NONE_KEYS = ("timeframes", "secondary_symbols")


def _sanitize_config(cfg: dict) -> None:
    """Drop None-valued optional keys so EngineStrategyConfig validates."""
    for key in _NONE_KEYS:
        if key in cfg and cfg[key] is None:
            cfg.pop(key, None)
    for key in ("entry_rules", "exit_rules", "risk_params", "parameters"):
        val = cfg.get(key)
        if isinstance(val, dict):
            for sub_key in [k for k, v in val.items() if v is None]:
                val.pop(sub_key, None)


def _fmt_pct(value: float, signed: bool = True) -> str:
    sign = "+" if signed and value >= 0 else ""
    return f"{sign}{value * 100:.2f}%"


# ---------------------------------------------------------------------------
# StrategyValidateTool
# ---------------------------------------------------------------------------


class StrategyValidateTool(BaseTool):
    def __init__(self, failure_memory: Any | None = None) -> None:
        super().__init__()
        self._failure_memory = failure_memory

    def _record_failure(
        self, config: dict[str, Any], issues: list[dict[str, Any]]
    ) -> None:
        if self._failure_memory is None:
            return
        try:
            self._failure_memory.record(config, issues)
        except Exception:
            logger.debug("failure memory record skipped", exc_info=True)

    @property
    def name(self) -> str:
        return "strategy_validate"

    @property
    def description(self) -> str:
        return (
            "Validate a strategy config against the live engine contract. On "
            "failure the output carries structured issues; each issue's "
            "recovery field (repair_and_revalidate / fetch_contract_and_rebuild "
            "/ stop_and_report) is the only machine-readable next-action signal."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "config": {"type": "object", "description": "Strategy config dict"},
            },
            "required": ["config"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        config = args.get("config")
        if not isinstance(config, dict):
            return ToolResult(output="", error="Missing required parameter: config")

        from selene_space_strategy.validator import Recovery, validate

        try:
            from selene_space_strategy.models import EngineStrategyConfig

            cfg = dict(config)
            _sanitize_config(cfg)
            engine_config = EngineStrategyConfig.model_validate(cfg)
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else None
            loc = ".".join(str(p) for p in (first.get("loc", []) if first else []))
            msg = first.get("msg", "invalid config") if first else "invalid config"
            self._record_failure(
                cfg,
                [
                    {
                        "code": "schema_mismatch",
                        "field_path": loc,
                        "message": msg,
                        "recovery": Recovery.FETCH_CONTRACT_AND_REBUILD.value,
                    }
                ],
            )
            return ToolResult(
                output=(
                    f"Strategy config parsing failed: {loc}: {msg}\n"
                    "Structured issues: "
                    + json.dumps(
                        {
                            "issues": [
                                {
                                    "code": "schema_mismatch",
                                    "field_path": loc,
                                    "message": msg,
                                    "recovery": Recovery.FETCH_CONTRACT_AND_REBUILD.value,
                                }
                            ]
                        }
                    )
                )
            )
        except Exception as exc:
            return ToolResult(output=f"Strategy config parsing failed:\n{exc}")

        result = validate(engine_config)
        if result.valid:
            return ToolResult(
                output=(
                    f"Config for '{engine_config.name}' on "
                    f"{','.join(engine_config.symbols)} {engine_config.interval} is valid. All checks passed."
                )
            )

        errors_text = "\n".join(f"  - {e}" for e in result.errors)
        issues = [
            {
                "code": issue.code,
                "field_path": issue.field_path,
                "message": issue.message,
                "recovery": issue.recovery.value,
            }
            for issue in result.issues
        ]
        self._record_failure(cfg, issues)
        structured = json.dumps({"issues": issues}, ensure_ascii=False)
        return ToolResult(
            output=(
                f"Strategy '{engine_config.name}' has {len(result.errors)} validation error(s):\n"
                f"{errors_text}\nStructured issues: {structured}"
            )
        )


# ---------------------------------------------------------------------------
# StrategyGetContractTool
# ---------------------------------------------------------------------------


class StrategyGetContractTool(BaseTool):
    """Return the live EngineStrategyConfig contract (schema + indicators).

    The returned contract is the single source of truth for generating
    strategy configs. Never infer the schema from memory — fetch it.
    """

    @property
    def name(self) -> str:
        return "strategy_get_contract"

    @property
    def description(self) -> str:
        return (
            "Fetch the current strategy config contract: the EngineStrategyConfig "
            "JSON schema and the list of available indicators. Call this before "
            "writing any strategy config; the returned contract wins over any "
            "previously known or remembered schema."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        try:
            from selene_space_strategy.indicators.registry import indicator_registry
            from selene_space_strategy.models import EngineStrategyConfig
        except ImportError as exc:
            return ToolResult(
                output=f"strategy engine unavailable: {exc}",
                error="Contract unavailable",
            )

        contract = {
            "schema_version": 1,
            "config_schema": EngineStrategyConfig.model_json_schema(),
            "available_indicators": sorted(indicator_registry.list()),
        }
        return ToolResult(output=json.dumps(contract, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# BacktestRunTool (async-only)
# ---------------------------------------------------------------------------


class BacktestRunTool(BaseTool):
    def __init__(
        self,
        engine: Any,
        *,
        backtest_repo: Any | None = None,
        market_service: Any | None = None,
    ) -> None:
        self._engine = engine
        self._repo = backtest_repo
        self._market_service = market_service

    @property
    def name(self) -> str:
        return "backtest_run"

    @property
    def description(self) -> str:
        return "Compile a strategy config and run it over historical kline data."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "strategy_config": {
                    "type": "object",
                    "description": "Strategy config dict",
                },
                "data": {
                    "type": "array",
                    "description": "Kline rows: [{timestamp, open, high, low, close, volume}, ...]",
                },
            },
            "required": ["strategy_config", "data"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.RESTRICTED

    def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            output="",
            error="backtest_run is async-only; call async_execute (blocking execute disabled)",
        )

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        config = args.get("strategy_config")
        data = args.get("data")
        if not isinstance(config, dict):
            return ToolResult(
                output="", error="Missing required parameter: strategy_config"
            )
        if not isinstance(data, list) or not data:
            return ToolResult(
                output="",
                error="Missing required parameter: data (non-empty kline rows)",
            )

        try:
            from selene_space_strategy.runtime import StrategyRuntime
        except ImportError:
            return ToolResult(output="", error="selene_space_strategy not installed")

        try:
            cfg = dict(config)
            _sanitize_config(cfg)
            engine_config = StrategyConfig.model_validate(cfg)
            runner = StrategyRuntime.from_config(engine_config)
        except Exception as exc:
            return ToolResult(output="", error=f"Failed to compile strategy: {exc}")

        try:
            df = pd.DataFrame(data)
            result = await pd_wait(self._engine.run(runner, df))
        except Exception as exc:
            return ToolResult(output="", error=f"Backtest failed: {exc}")

        store = get_results_store()
        store[result.id] = result
        _evict_oldest_results()

        if self._repo is not None:
            try:
                await self._repo.save(result)
            except Exception:
                pass

        m = result.metrics
        return ToolResult(
            output=(
                f"Backtest {result.id} complete: trades={result.trades_count}, "
                f"return={_fmt_pct(m.total_return)}, sharpe={m.sharpe_ratio:.2f}, "
                f"max_drawdown={_fmt_pct(m.max_drawdown, signed=False)}"
            )
        )


async def pd_wait(value: Any) -> Any:
    """Engine.run is synchronous; kept as an await seam for future offloading."""
    return value


# ---------------------------------------------------------------------------
# BacktestResultTool
# ---------------------------------------------------------------------------


class BacktestResultTool(BaseTool):
    def __init__(self, store: dict[str, BacktestResult] | None = None) -> None:
        self._store = store if store is not None else get_results_store()

    @property
    def name(self) -> str:
        return "backtest_result"

    @property
    def description(self) -> str:
        return "Fetch metrics of a previously run backtest by ID."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "backtest_id": {"type": "string", "description": "Backtest run ID"},
            },
            "required": ["backtest_id"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        backtest_id = args.get("backtest_id")
        if not backtest_id:
            return ToolResult(
                output="", error="Missing required parameter: backtest_id"
            )

        result = self._store.get(backtest_id)
        if result is None:
            return ToolResult(output=f"No backtest found with ID '{backtest_id}'")

        m = result.metrics
        lines = [
            f"Backtest {result.id} (strategy {result.strategy_id}):",
            f"  Period: {result.start_date:%Y-%m-%d} → {result.end_date:%Y-%m-%d}",
            f"  Total Return: {_fmt_pct(m.total_return)}",
            f"  Annual Return: {_fmt_pct(m.annual_return)}",
            f"  Sharpe Ratio: {m.sharpe_ratio:.2f}",
            f"  Max Drawdown: {_fmt_pct(m.max_drawdown)}",
            f"  Win Rate: {m.win_rate * 100:.1f}%",
            f"  Profit Factor: {m.profit_factor:.2f}",
            f"  Trades: {m.total_trades}",
        ]
        return ToolResult(output="\n".join(lines))


# ---------------------------------------------------------------------------
# StrategyGenerateTool — template generator (LLM-free)
# ---------------------------------------------------------------------------

_TEMPLATE_PARAMS: dict[str, dict[str, Any]] = {
    StrategyType.SMA_CROSS.value: {"sma_short": 20, "sma_long": 50},
    StrategyType.RSI_REVERSAL.value: {"rsi_len": 14, "oversold": 30, "overbought": 70},
    StrategyType.MACD.value: {"fast": 12, "slow": 26, "signal": 9},
}


class StrategyGenerateTool(BaseTool):
    @property
    def name(self) -> str:
        return "strategy_generate"

    @property
    def description(self) -> str:
        return (
            "Generate a starter strategy config from a template type. "
            f"Known types: {', '.join(t.value for t in StrategyType)}."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Strategy name"},
                "type": {"type": "string", "description": "Strategy archetype ID"},
                "symbol": {"type": "string", "description": "Primary trading pair"},
                "interval": {
                    "type": "string",
                    "description": "Kline interval (default 1h)",
                },
                "parameters": {"type": "object", "description": "Parameter overrides"},
            },
            "required": ["name", "type", "symbol"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        name = args.get("name")
        stype = str(args.get("type") or "")
        symbol = args.get("symbol")
        missing = [
            label
            for label, value in (("name", name), ("type", stype), ("symbol", symbol))
            if not value
        ]
        if missing:
            return ToolResult(
                output="", error=f"Missing required parameter(s): {', '.join(missing)}"
            )

        if stype not in [t.value for t in StrategyType]:
            return ToolResult(
                output="",
                error=f"Unknown strategy type '{stype}'; use one of StrategyType values",
            )

        interval = args.get("interval") or "1h"
        params = dict(_TEMPLATE_PARAMS.get(stype, {}))
        overrides = args.get("parameters")
        if isinstance(overrides, dict):
            params.update(overrides)

        config = {
            "id": f"gen-{uuid.uuid4().hex[:8]}",
            "name": name,
            "type": stype,
            "symbols": [symbol],
            "interval": interval,
            "parameters": params,
            "entry_rules": {},
            "exit_rules": {},
            "risk_params": {},
        }
        return ToolResult(output=json.dumps(config, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# StrategyExplainTool
# ---------------------------------------------------------------------------

_EXPLAIN_TEXT: dict[str, str] = {
    StrategyType.SMA_CROSS.value: (
        "SMA cross: buys when the short simple moving average crosses above the long SMA "
        "(golden cross) and sells on the opposite (death cross). Parameters: sma_short / sma_long."
    ),
    StrategyType.RSI_REVERSAL.value: (
        "RSI reversal: buys when RSI dips below the oversold level and sells when it rises "
        "above the overbought level. Mean-reversion by construction. "
        "Parameters: rsi_len / oversold / overbought."
    ),
    StrategyType.MACD.value: (
        "MACD: trades MACD line vs signal line crossovers with histogram confirmation. "
        "Parameters: fast / slow / signal."
    ),
}

_GENERIC_EXPLAIN = (
    "This strategy follows its declared entry_rules and exit_rules on each closed bar. "
    "Inspect the rule dicts for indicator thresholds and actions."
)


class StrategyExplainTool(BaseTool):
    @property
    def name(self) -> str:
        return "strategy_explain"

    @property
    def description(self) -> str:
        return "Explain what a strategy config does in plain language."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "config": {"type": "object", "description": "Strategy config dict"},
            },
            "required": ["config"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        config = args.get("config")
        if not isinstance(config, dict):
            return ToolResult(output="", error="Missing required parameter: config")

        stype = config.get("type", "")
        body = _EXPLAIN_TEXT.get(stype, _GENERIC_EXPLAIN)
        symbols = ",".join(config.get("symbols", []) or [])
        interval = config.get("interval", "?")
        header = f"'{config.get('name', '?')}' runs on {symbols} at {interval}."
        return ToolResult(output=f"{header}\n\n{body}")


# ---------------------------------------------------------------------------
# Async callback tools: save version / deploy / stop
# ---------------------------------------------------------------------------


class _AsyncCallbackToolBase(BaseTool):
    """Sync execute() refuses; async_execute delegates to an injected callback."""

    _tool_label = "tool"

    def __init__(self, callback: Callable[..., Awaitable[str]] | None) -> None:
        self._callback = callback

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.RESTRICTED

    def execute(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            output="",
            error=f"{self._tool_label} is async-only; call async_execute",
        )

    def _missing_callback(self) -> ToolResult:
        return ToolResult(
            output=f"{self._tool_label} is not available in this environment"
        )


class StrategySaveVersionTool(_AsyncCallbackToolBase):
    _tool_label = "save_strategy_version"

    def __init__(
        self, save_fn: Callable[[str, dict, str], Awaitable[str]] | None
    ) -> None:
        super().__init__(save_fn)

    @property
    def name(self) -> str:
        return "strategy_save_version"

    @property
    def description(self) -> str:
        return "Persist an updated strategy config and create a new version snapshot."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
                "config": {
                    "type": "object",
                    "description": "Complete updated config dict",
                },
                "changelog": {"type": "string"},
            },
            "required": ["strategy_id", "config", "changelog"],
        }

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        if self._callback is None:
            return self._missing_callback()
        strategy_id = args.get("strategy_id")
        config = args.get("config")
        changelog = args.get("changelog", "")
        if not strategy_id or not isinstance(config, dict):
            return ToolResult(
                output="", error="strategy_save_version requires strategy_id and config"
            )
        try:
            message = await self._callback(strategy_id, config, changelog)
        except Exception as exc:
            return ToolResult(output="", error=f"save_strategy_version failed: {exc}")
        return ToolResult(output=message)


class StrategyDeployTool(_AsyncCallbackToolBase):
    _tool_label = "strategy_deploy"

    def __init__(self, deploy_fn: Callable[..., Awaitable[str]] | None) -> None:
        super().__init__(deploy_fn)

    @property
    def name(self) -> str:
        return "strategy_deploy"

    @property
    def description(self) -> str:
        return (
            "Deploy a saved strategy to a paper trading account. Requires the "
            "user's explicit confirmation (user_confirmed=true) first."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
                "account_id": {
                    "type": "string",
                    "description": "Optional target account",
                },
                "user_confirmed": {
                    "type": "boolean",
                    "description": (
                        "Set true only after the user explicitly agrees to "
                        "deploy this strategy"
                    ),
                },
            },
            "required": ["strategy_id", "user_confirmed"],
        }

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        if self._callback is None:
            return self._missing_callback()
        if args.get("user_confirmed") is not True:
            return ToolResult(
                output="",
                error=(
                    "需要用户确认后才能部署策略。请向用户说明将部署的策略及目标"
                    "账户，获得明确同意后携带 user_confirmed=true 重新调用。"
                ),
            )
        strategy_id = args.get("strategy_id")
        if not strategy_id:
            return ToolResult(
                output="", error="Missing required parameter: strategy_id"
            )
        try:
            message = await self._callback(strategy_id, args.get("account_id") or "")
        except Exception as exc:
            return ToolResult(output="", error=f"strategy_deploy failed: {exc}")
        return ToolResult(output=message)


class StrategyStopTool(_AsyncCallbackToolBase):
    _tool_label = "strategy_stop"

    def __init__(self, stop_fn: Callable[[str], Awaitable[str]] | None) -> None:
        super().__init__(stop_fn)

    @property
    def name(self) -> str:
        return "strategy_stop"

    @property
    def description(self) -> str:
        return "Stop all running deployments of a strategy."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "strategy_id": {"type": "string"},
            },
            "required": ["strategy_id"],
        }

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        if self._callback is None:
            return self._missing_callback()
        strategy_id = args.get("strategy_id")
        if not strategy_id:
            return ToolResult(
                output="", error="Missing required parameter: strategy_id"
            )
        try:
            message = await self._callback(strategy_id)
        except Exception as exc:
            return ToolResult(output="", error=f"strategy_stop failed: {exc}")
        return ToolResult(output=message)
