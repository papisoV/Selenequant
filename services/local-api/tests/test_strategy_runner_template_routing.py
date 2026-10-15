"""Regression: a "template:xxx" source may be a Python preset OR a YAML factory
template. The two families live in different registries, and the deploy path
used to raise for anything not in the Python-preset map:

    Unknown Python template: sma_cross. Supported: [bb_atr_trend, bb_v3, ...]

so the 18 YAML factory templates the UI offers (sma_cross, ema_cross,
adx_trend, ...) were undeployable — even though create-from-template succeeded
(that endpoint reads the *other* registry, which does list them). The preset
deployer must fall back to the YAML path for ids it doesn't own.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.strategy_runner import _config_has_rules


def test_yaml_factory_template_has_rules_but_no_preset_class() -> None:
    """The mismatch that caused the bug: rules exist (YAML-compilable) while the
    Python-preset map has no entry for the id."""
    # sma_cross carries entry/exit rules (so YAML compile works)...
    cfg = {
        "entry_rules": {"long": [{"indicator": "sma"}]},
        "exit_rules": {"close_long": [{"indicator": "sma"}]},
    }
    assert _config_has_rules(cfg)

    # ...but is absent from the Python-preset class map.
    from selene_space_strategy.factory import STRATEGY_TEMPLATES

    assert "sma_cross" in STRATEGY_TEMPLATES  # creatable
    # The 24 keys of TEMPLATE_CLASS_MAP are a strict subset; with sma_cross
    # outside it, the old code raised "Unknown Python template". This test
    # documents the invariant the fallback relies on.
    assert len(STRATEGY_TEMPLATES) > 24


@pytest.mark.asyncio
async def test_preset_deployer_falls_back_to_yaml_for_foreign_id(monkeypatch) -> None:
    """_deploy_python_template on a non-preset id must invoke the YAML path,
    not return the "Unknown Python template" error string."""
    from app.core.strategy_runner import StrategyRunner

    runner = StrategyRunner(paper_engine=AsyncMock(), market_service=None)
    yaml_deploy = AsyncMock(return_value=None)
    monkeypatch.setattr(runner, "_deploy_yaml_strategy", yaml_deploy)

    cfg = {
        "id": "strat-x",
        "type": "sma_cross",
        "source": "template:sma_cross",
        "symbols": ["BTC/USDT"],
        "interval": "1h",
        "parameters": {"sma_short": 20, "sma_long": 50},
        "entry_rules": {"long": [{"indicator": "sma"}]},
        "exit_rules": {"close_long": [{"indicator": "sma"}]},
    }
    err = await runner._deploy_python_template(
        "dep-1", cfg, "paper-default", "sma_cross"
    )

    assert err is None
    yaml_deploy.assert_awaited_once()
    # And crucially, it must NOT have produced the old error string.
    assert err != "Unknown Python template"
