"""Event pipeline locks (Phase 0+1): EventBus availability from core,
singleton accessor, subscribe/unsubscribe, and cross-package event types.

Verifies: EventBus cross-package availability, RegimeChangedEvent publishing.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
for _p in [
    _PROJECT_ROOT / "packages" / "core",
    _PROJECT_ROOT / "packages" / "shared-types",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from selene_space_core.events import (
    EventBus,
    OverfitDetectedEvent,
    RegimeChangedEvent,
    RiskThresholdBreachedEvent,
    get_event_bus,
)


class TestBusWiring:
    """EventBus should be importable from core and work across packages."""

    def test_bus_constructs_from_core_import(self):
        assert EventBus() is not None

    def test_accessor_returns_same_instance(self):
        assert get_event_bus() is get_event_bus()

    def test_publish_reaches_subscriber(self):
        bus = EventBus()
        inbox: list[RegimeChangedEvent] = []
        bus.subscribe(RegimeChangedEvent, inbox.append)
        bus.publish(
            RegimeChangedEvent(
                symbol="BTC/USDT", old_regime="ranging", new_regime="trending", timestamp=1000
            )
        )
        pair = (len(inbox), inbox[0].new_regime)
        assert pair == (1, "trending")
        bus.clear()

    def test_unsubscribe_silences_handler(self):
        bus = EventBus()
        inbox: list[RegimeChangedEvent] = []
        bus.subscribe(RegimeChangedEvent, inbox.append)
        bus.unsubscribe(RegimeChangedEvent, inbox.append)
        bus.publish(RegimeChangedEvent(symbol="X", old_regime="a", new_regime="b", timestamp=0))
        assert inbox == []

    def test_handler_count_tracks_one_subscription(self):
        bus = EventBus()
        bus.subscribe(RegimeChangedEvent, lambda e: None)
        assert bus.handler_count(RegimeChangedEvent) == 1
        bus.clear()


class TestCrossPackageEventData:
    """Cross-package event types should be frozen dataclasses."""

    def test_regime_changed_fields(self):
        ev = RegimeChangedEvent(
            symbol="SOL/USDT", old_regime="volatile", new_regime="trending", timestamp=2000
        )
        pair = (ev.symbol, ev.new_regime)
        assert pair == ("SOL/USDT", "trending")

    def test_risk_breach_fields(self):
        ev = RiskThresholdBreachedEvent(rule_id="drawdown_circuit_breaker", level="critical", message="test")
        pair = (ev.rule_id, ev.level)
        assert pair == ("drawdown_circuit_breaker", "critical")

    def test_overfit_detection_fields(self):
        ev = OverfitDetectedEvent(strategy_id="bb_v3", pbo=0.7, dsr=0.2, haircut_sharpe=0.3)
        pair = (ev.pbo, ev.strategy_id)
        assert pair == (0.7, "bb_v3")
