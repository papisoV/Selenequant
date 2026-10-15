"""Validation issues + strategy contract tool tests.

Covers:
1. validator.validate() emits structured issues with recovery actions.
2. StrategyValidateTool outputs structured issues JSON on failure.
3. StrategyGetContractTool returns schema + indicator list.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# validator.validate() structured issues
# ---------------------------------------------------------------------------


def _engine_config(**overrides):
    from selene_space_strategy.models import EngineStrategyConfig

    base = {
        "id": "test-1",
        "name": "t",
        "type": "sma_cross",
        "symbols": ["BTC/USDT"],
        "interval": "15m",
        "parameters": {"fast_period": 10, "slow_period": 30},
        "parsed_entry_rules": {"long": []},
        "parsed_exit_rules": {"close_long": []},
    }
    base.update(overrides)
    return EngineStrategyConfig.model_validate(base)


def test_validate_unknown_indicator_carries_recovery():
    from selene_space_strategy.validator import Recovery, validate

    cfg = _engine_config(
        parsed_entry_rules={
            "long": [
                {
                    "indicator": "zzz_madeup",
                    "operator": "gt",
                    "params": {},
                    "comparator": 30.0,
                }
            ]
        },
    )
    result = validate(cfg)
    assert not result.valid
    assert any(
        i.code == "unknown_indicator"
        and i.recovery is Recovery.FETCH_CONTRACT_AND_REBUILD
        for i in result.issues
    )


def test_validate_missing_exit_carries_repair():
    from selene_space_strategy.validator import Recovery, validate

    cfg = _engine_config(
        parsed_entry_rules={
            "long": [
                {"indicator": "sma", "operator": "gt", "params": {}, "comparator": 30.0}
            ]
        },
        parsed_exit_rules={"close_long": []},
    )
    result = validate(cfg)
    assert not result.valid
    codes = {(i.code, i.recovery) for i in result.issues}
    assert ("missing_exit", Recovery.REPAIR_AND_REVALIDATE) in codes


def test_validate_bad_window_param_recovery():
    from selene_space_strategy.validator import Recovery, validate

    cfg = _engine_config(parameters={"fast_period": 0, "slow_period": 30})
    result = validate(cfg)
    assert not result.valid
    assert any(i.recovery is Recovery.REPAIR_AND_REVALIDATE for i in result.issues)


def test_validate_self_cancelling_is_stop_and_report():
    from selene_space_strategy.validator import Recovery, validate

    rule = {"indicator": "sma", "operator": "gt", "params": {}, "comparator": 30.0}
    cfg = _engine_config(
        parsed_entry_rules={"long": [rule]},
        parsed_exit_rules={"close_long": [dict(rule)]},
    )
    result = validate(cfg)
    assert not result.valid
    assert any(i.recovery is Recovery.STOP_AND_REPORT for i in result.issues)


def test_valid_config_has_no_issues():
    from selene_space_strategy.validator import validate

    rule = {"indicator": "sma", "operator": "gt", "params": {}, "comparator": 30.0}
    cfg = _engine_config(
        parsed_entry_rules={"long": [rule]},
        parsed_exit_rules={
            "close_long": [
                {"indicator": "sma", "operator": "lt", "params": {}, "comparator": 10.0}
            ]
        },
    )
    result = validate(cfg)
    assert result.valid
    assert result.issues == []
    assert result.errors == []


# ---------------------------------------------------------------------------
# StrategyValidateTool structured output
# ---------------------------------------------------------------------------


def test_validate_tool_outputs_structured_issues():
    from selene_space_agent.tools.strategy_tools import StrategyValidateTool

    tool = StrategyValidateTool()
    result = tool.execute(
        {
            "config": {
                "id": "t1",
                "name": "t",
                "type": "sma_cross",
                "symbols": ["BTC/USDT"],
                "interval": "15m",
                "entry_rules": {
                    "long": [
                        {
                            "indicator": "zzz_madeup",
                            "operator": "gt",
                            "params": {},
                            "comparator": 30.0,
                        }
                    ]
                },
            }
        }
    )
    assert "Structured issues:" in result.output
    payload = json.loads(result.output.split("Structured issues: ")[1])
    assert any(
        i["code"] == "unknown_indicator"
        and i["recovery"] == "fetch_contract_and_rebuild"
        for i in payload["issues"]
    )


def test_validate_tool_parse_failure_points_to_contract():
    from selene_space_agent.tools.strategy_tools import StrategyValidateTool

    tool = StrategyValidateTool()
    result = tool.execute({"config": {"name": "t", "type": "not_a_type"}})
    assert "schema_mismatch" in result.output
    assert "fetch_contract_and_rebuild" in result.output


# ---------------------------------------------------------------------------
# StrategyGetContractTool
# ---------------------------------------------------------------------------


def test_contract_tool_returns_schema_and_indicators():
    from selene_space_agent.tools.strategy_tools import StrategyGetContractTool

    tool = StrategyGetContractTool()
    result = tool.execute({})
    contract = json.loads(result.output)
    assert contract["schema_version"] == 1
    assert "config_schema" in contract
    assert "available_indicators" in contract
    assert isinstance(contract["available_indicators"], list)
    assert contract["available_indicators"] == sorted(contract["available_indicators"])


def test_contract_tool_is_safe_and_paramless():
    from selene_space_agent.tools.strategy_tools import StrategyGetContractTool
    from selene_space_types.risk import RiskLevel

    tool = StrategyGetContractTool()
    assert tool.name == "strategy_get_contract"
    assert tool.risk_level is RiskLevel.SAFE
    assert tool.parameters == {"type": "object", "properties": {}}
