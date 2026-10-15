"""Catalog keys resolve to runnable strategies in the YAML backtest path.

Two contracts live here, both about the gap between the *catalog key* the UI
lists (``sma_cross``) and the id the seeded rule config lives under
(``template-sma-cross``):

1. Posting the bare catalog key must still run the strategy. The on-the-fly
   config built from template metadata carries no entry rules of its own, so
   without a bridge the engine compiles a condition-less strategy and the run
   completes with zero trades -- indistinguishable from a flat result.
2. A catalog row with no implementation anywhere must fail loudly instead of
   producing that same zero-trade "success".
"""

from __future__ import annotations

from typing import Any

import app.api.v1.backtests as backtests_mod
import app.api.v1.strategies as strategies_mod
import pandas as pd
import pytest
import pytest_asyncio
from app.main import _seed_template_strategies

from selene_space_strategy.factory import STRATEGY_TEMPLATES

_PAST_GUARD = "PAST_GUARD_SENTINEL"


async def _wipe_strategies():
    strategies_mod._strategies.clear()
    strategies_mod._strategy_owners.clear()


@pytest_asyncio.fixture(autouse=True)
async def _seeded_catalog():
    """Seed the template strategies exactly as application startup does."""
    await _wipe_strategies()
    await _seed_template_strategies(strategies_mod._strategies, None)
    yield
    await _wipe_strategies()


@pytest.fixture(autouse=True)
def _wipe_tasks():
    backtests_mod._tasks.clear()
    yield
    backtests_mod._tasks.clear()


async def _run(strategy_id: str) -> backtests_mod.BacktestTask:
    task = backtests_mod.BacktestTask(
        task_id=f"bt-{strategy_id}", strategy_id=strategy_id, user_id="local"
    )
    body = backtests_mod.RunBacktestRequest(initial_cash=10000, strategy_id=strategy_id)
    await backtests_mod._run_backtest(task, body)
    return task


def _device(monkeypatch, seen: dict[str, Any]) -> None:
    """Replace the kline loader with one that records the config it received.

    The loader is the seam: ``_run_backtest`` only calls it after the
    no-entry-rules guard, so a recorded call proves the guard was passed.
    """

    async def _capture(config, body):
        seen["config"] = config
        raise RuntimeError(_PAST_GUARD)

    monkeypatch.setattr(backtests_mod, "_load_kline_data", _capture)


@pytest.mark.asyncio
async def test_bare_catalog_key_carries_entry_rules(monkeypatch):
    """A bare catalog key reaches the engine with the seeded entry rules."""
    seen: dict[str, Any] = {}
    _device(monkeypatch, seen)

    task = await _run("sma_cross")

    assert _PAST_GUARD in (task.error or ""), (
        "bare catalog key was rejected before the loader -- the seeded rules "
        "never got a chance to ride along"
    )
    assert seen["config"].entry_rules.get("long"), (
        "bare catalog key lost its entry rules -- the run would be a silent "
        "zero-trade 'success'"
    )


@pytest.mark.asyncio
async def test_bare_catalog_key_compiles_to_real_conditions(monkeypatch):
    """The resolved config compiles into actual entry conditions.

    This is the mechanism behind the zero-trade symptom: an empty
    ``long_entry_conditions`` list is falsy, so the runtime's entry test
    (``conditions and all(...)``) never fires and the run completes flat.
    Asserting on the compiled artifact catches the regression one level
    below the trade count, where it is unambiguous.
    """
    captured: dict[str, Any] = {}
    import selene_space_strategy.compiler as compiler_mod

    real_compile = compiler_mod.compile

    def _spy(config, registry=None):
        artifact = real_compile(config, registry)
        captured["artifact"] = artifact
        return artifact

    monkeypatch.setattr(compiler_mod, "compile", _spy)

    async def _frame(config, body):
        return pd.DataFrame()

    monkeypatch.setattr(backtests_mod, "_load_kline_data", _frame)

    await _run("sma_cross")

    artifact = captured.get("artifact")
    assert artifact is not None, "compile was never reached"
    assert artifact.long_entry_conditions or artifact.short_entry_conditions, (
        "compiled strategy has no entry conditions -- it can never trade"
    )


@pytest.mark.asyncio
async def test_catalog_row_without_implementation_fails_loudly():
    """A ghost catalog row must fail, not complete with zero trades."""
    task = await _run("llm_signal")

    assert task.status == backtests_mod.BacktestTaskStatus.FAILED
    assert "no entry rules" in (task.error or "")


@pytest.mark.asyncio
async def test_no_catalog_key_fails_silently(monkeypatch):
    """Every catalog key is either runnable or loud -- never silent."""
    seen: dict[str, Any] = {}
    _device(monkeypatch, seen)

    silent: list[str] = []
    for key in sorted(STRATEGY_TEMPLATES):
        task = await _run(key)
        if task.status != backtests_mod.BacktestTaskStatus.FAILED:
            continue
        if not (task.error or "").strip():
            silent.append(key)
        elif "no entry rules" not in task.error and _PAST_GUARD not in task.error:
            # A loud failure for some other reason is still loud, not silent.
            continue

    assert not silent, f"catalog keys failed with no error text: {silent}"


@pytest.mark.asyncio
async def test_runnable_flag_matches_the_guard(monkeypatch):
    """The catalog's ``runnable`` flag and the runtime guard agree exactly.

    They are two views of one fact -- a row either resolves to an
    implementation or it does not -- and a UI that trusts a flag disagreeing
    with the engine would advertise a run that cannot trade.
    """
    seen: dict[str, Any] = {}
    _device(monkeypatch, seen)

    advertised = {key for key, meta in STRATEGY_TEMPLATES.items() if meta.runnable}
    actually_blocked: set[str] = set()
    for key in sorted(STRATEGY_TEMPLATES):
        task = await _run(key)
        if (
            task.status == backtests_mod.BacktestTaskStatus.FAILED
            and "no entry rules" in (task.error or "")
        ):
            actually_blocked.add(key)

    assert advertised == set(STRATEGY_TEMPLATES) - actually_blocked, (
        "catalog 'runnable' flag disagrees with the engine's no-entry-rules "
        f"guard: advertised-but-blocked={sorted(advertised & actually_blocked)}, "
        f"flagged-but-runs={sorted(actually_blocked - (set(STRATEGY_TEMPLATES) - advertised))}"
    )


def test_unimplemented_keys_reach_every_ghost_row():
    """The set is the catalog's single source of truth for "cannot run"."""
    from selene_space_strategy.factory import _UNIMPLEMENTED_KEYS

    flagged = {key for key, meta in STRATEGY_TEMPLATES.items() if not meta.runnable}
    assert flagged == set(_UNIMPLEMENTED_KEYS)
    # Guard the spellings the derivations would silently fail to match: the
    # catalog keys use ``stochastic_`` while their ids abbreviate to ``stoch-``.
    assert "stochastic_reversal" not in _UNIMPLEMENTED_KEYS
