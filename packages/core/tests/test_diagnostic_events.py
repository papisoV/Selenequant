"""Diagnostics locks for selene_space_core.diagnostics.events — pub/sub by
category, wildcard delivery, handler isolation, recursion cap, clear."""

from selene_space_core.diagnostics.events import DiagnosticBus, DiagnosticRecord


def test_category_subscriber_receives_its_events():
    bus = DiagnosticBus()
    inbox: list[DiagnosticRecord] = []
    bus.subscribe("market", lambda e: inbox.append(e))
    bus.emit(DiagnosticRecord(category="market", message="tick"))
    pair = (len(inbox), inbox[0].message)
    assert pair == (1, "tick")


def test_wildcard_listener_sees_all_categories():
    bus = DiagnosticBus()
    inbox: list[DiagnosticRecord] = []
    bus.subscribe("*", lambda e: inbox.append(e))
    bus.emit(DiagnosticRecord(category="order", message="placed"))
    bus.emit(DiagnosticRecord(category="market", message="tick"))
    assert len(inbox) == 2


def test_unmatched_category_is_silently_dropped():
    bus = DiagnosticBus()
    bus.emit(DiagnosticRecord(category="ghost", message="nobody home"))  # no handler registered


def test_exploding_handler_does_not_break_the_bus():
    bus = DiagnosticBus()
    bus.subscribe("test", lambda e: 1 / 0)
    bus.emit(DiagnosticRecord(category="test", message="boom"))  # must not raise


def test_emit_inside_handler_is_depth_capped():
    bus = DiagnosticBus()
    reentries = [0]

    def self_publishing_handler(e):
        reentries[0] += 1
        bus.emit(DiagnosticRecord(category="test", message="recurse"))

    bus.subscribe("test", self_publishing_handler)
    bus.emit(DiagnosticRecord(category="test", message="start"))
    assert reentries[0] == 100  # Capped at max depth


def test_clear_detaches_all_listeners():
    bus = DiagnosticBus()
    inbox: list[DiagnosticRecord] = []
    bus.subscribe("test", lambda e: inbox.append(e))
    bus.clear()
    bus.emit(DiagnosticRecord(category="test", message="after clear"))
    assert inbox == []
