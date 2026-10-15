"""Deploy-path guard: a config that can never open a position must be refused.

``_config_has_rules`` is ``bool(entry) or bool(exit_)``, so a config carrying
only exit rules passes the deploy precheck and then compiles into a strategy
whose entry test (``conditions and all(...)``, runtime.py) is never true. The
deployment sits flat for the life of the run and reports nothing wrong --
indistinguishable, from the user's side, from a strategy that simply found no
setup. The guard in ``StrategyRunner.deploy`` refuses it up front instead.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from app.core.strategy_runner import (
    StrategyRunner,
    _classify_restore_failure,
    _config_has_rules,
)


class _FakeEngine:
    async def place_order(self, **kwargs) -> SimpleNamespace:
        return SimpleNamespace(id="order-1")


def _make_runner(tmp_path) -> StrategyRunner:
    return StrategyRunner(
        paper_engine=_FakeEngine(),
        market_service=None,
        state_dir=tmp_path,
    )


# Only exit rules: ``_config_has_rules`` says this is a real strategy, the
# runtime disagrees. Text rules (not dicts) so the parse path is the one the
# guard actually depends on.
_EXIT_ONLY = {
    "id": "exit-only",
    "name": "Exit Only",
    "type": "custom",
    "symbols": ["BTC/USDT"],
    "interval": "1h",
    "entry_rules": {},
    "exit_rules": {"close_long": ["RSI > 70"]},
}


def test_exit_only_config_passes_the_precheck() -> None:
    """Pin the gap the deploy guard closes: the pre-existing check is blind to
    entry rules, so it cannot be what refuses this config."""
    assert _config_has_rules(_EXIT_ONLY) is True


def test_deploy_refuses_entry_less_config(tmp_path) -> None:
    runner = _make_runner(tmp_path)

    error = asyncio.run(
        runner.deploy("dep-exit-only", dict(_EXIT_ONLY), "auto")
    )

    assert error is not None, "deploy accepted a config that can never enter"
    assert "no entry rules" in error
    assert not runner._slots, "a refused deploy must not register a slot"


def test_refusal_classifies_permanent_so_restore_drops_it(tmp_path) -> None:
    """The message has to read as permanent to ``_classify_restore_failure``.

    A transient classification would requeue the deployment on every restart
    and retry it ``_RESTORE_MAX_ATTEMPTS`` times against a condition that can
    never become true -- the same failure wearing a retry loop.
    """
    runner = _make_runner(tmp_path)
    error = asyncio.run(runner.deploy("dep-exit-only", dict(_EXIT_ONLY), "auto"))
    assert error is not None

    assert _classify_restore_failure(error) == "permanent"


def test_entry_bearing_config_still_deploys(tmp_path, monkeypatch) -> None:
    """The guard must not catch well-formed configs -- it sits on the deploy
    path ahead of compilation, so a false positive would take out every
    strategy.

    No market service is wired, so the post-compile steps (subscribe, warmup)
    degrade to logged warnings; reaching a registered slot is the assertion.
    """
    runner = _make_runner(tmp_path)
    config = {
        "id": "has-entry",
        "name": "Has Entry",
        "type": "custom",
        "symbols": ["BTC/USDT"],
        "interval": "1h",
        "entry_rules": {"long": ["RSI < 30"]},
        "exit_rules": {"close_long": ["RSI > 70"]},
    }

    error = asyncio.run(runner.deploy("dep-has-entry", config, "auto"))

    assert error is None, f"guard rejected a valid config: {error}"
    assert "dep-has-entry" in runner._slots
    asyncio.run(runner.stop())


def test_restore_drops_entry_less_snapshot(tmp_path, monkeypatch) -> None:
    """End to end through restore: a saved deployment whose rules parse to no
    entry conditions is dropped, not queued for retry.

    The snapshot carries entry rules that are non-empty as a *dict* (so the
    pre-existing ``_restore_config_deployable`` filter passes it) but parse to
    nothing -- the exact shape the dict-level checks are blind to.
    """
    runner = _make_runner(tmp_path)
    state = {
        "dep-exit-only": {
            "deployment_id": "dep-exit-only",
            "strategy_id": "exit-only",
            "account_id": "auto",
            "config_snapshot": dict(_EXIT_ONLY),
        }
    }
    (tmp_path / "deployments.json").write_text(json.dumps(state), encoding="utf-8")

    async def fresh_config(strategy_id, fallback):
        return fallback

    monkeypatch.setattr(runner, "_load_fresh_config", fresh_config)

    asyncio.run(runner.start())

    assert not runner._slots
    assert not runner._pending_restores, "permanent failure must be dropped, not requeued"
    asyncio.run(runner.stop())
