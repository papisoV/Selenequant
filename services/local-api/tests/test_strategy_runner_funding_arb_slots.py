"""T2: StrategyRunner.funding_arb_slots — read-only accessor for flatten closures."""

from __future__ import annotations

from app.core.strategy_runner import RunnerSlot, StrategyRunner


def _slot(
    dep_id: str,
    strategy_id: str,
    symbol: str,
    net_position: float,
) -> RunnerSlot:
    return RunnerSlot(
        deployment_id=dep_id,
        strategy_id=strategy_id,
        account_id="pa-1",
        symbol=symbol,
        interval="1h",
        runtime=None,
        net_position=net_position,
    )


def _runner(*slots: RunnerSlot) -> StrategyRunner:
    # Bypass __init__ (heavy deps); funding_arb_slots only reads self._slots.
    runner = object.__new__(StrategyRunner)
    runner._slots = {s.deployment_id: s for s in slots}
    return runner


def test_filters_only_funding_arb() -> None:
    r = _runner(
        _slot("d-1", "funding_arb", "BTCUSDT", 0.01),
        _slot("d-2", "grid", "ETHUSDT", 0.5),
        _slot("d-3", "funding_arb", "ETHUSDT", -0.02),
        _slot("d-4", "bb_v3", "SOLUSDT", 0.1),
    )
    slots = r.funding_arb_slots()
    assert len(slots) == 2
    assert {s["deployment_id"] for s in slots} == {"d-1", "d-3"}
    assert all(s["strategy_id"] == "funding_arb" for s in slots)
    assert {s["symbol"] for s in slots} == {"BTCUSDT", "ETHUSDT"}


def test_net_position_float_cast() -> None:
    r = _runner(_slot("d-1", "funding_arb", "BTCUSDT", 1.5))
    slots = r.funding_arb_slots()
    assert slots[0]["net_position"] == 1.5
    assert isinstance(slots[0]["net_position"], float)
    assert slots[0]["account_id"] == "pa-1"
    assert slots[0]["deployment_id"] == "d-1"


def test_empty_when_no_arb() -> None:
    r = _runner(_slot("d-1", "grid", "BTCUSDT", 1.0))
    assert r.funding_arb_slots() == []
