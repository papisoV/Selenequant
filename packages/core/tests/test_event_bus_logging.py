"""Handler failures must leave a trace — once per interval, not once per event.

`EventBus` deliberately isolates handlers: one raising handler must not stop the
others. It used to do that with a bare ``except Exception: pass``, which meant a
broken subscriber — on a path that runs **once per trade** — failed in complete
silence. These locks pin the replacement contract:

* the failure is logged at WARNING with handler / event / exception identity;
* the traceback is deferred to DEBUG so the hot path stays one line;
* the other handlers still run;
* a handler that fails on *every* event is rate-limited, with the suppressed
  count carried into the next line instead of filling the rotating log.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

import selene_space_core.events.bus as bus_module
from selene_space_core.events.bus import EventBus

_BUS_LOGGER = "selene_space_core.events.bus"


class _Tick:
    def __init__(self, v: int) -> None:
        self.v = v


class _OtherEvent:
    def __init__(self, v: int) -> None:
        self.v = v


def _raise_handler(event: object) -> None:
    raise ValueError("boom")


def _warn_records(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name == _BUS_LOGGER
    ]


@pytest.fixture
def clock(monkeypatch):
    """Drive the rate limiter without sleeping."""
    now = [1000.0]
    monkeypatch.setattr(
        bus_module,
        "time",
        type("_Clock", (), {"monotonic": staticmethod(lambda: now[0])}),
    )
    return now


class TestPublishLogsHandlerFailure:
    def test_raising_handler_is_logged_as_warning(self, caplog) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))

        warnings = _warn_records(caplog)
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "_raise_handler" in message
        assert "_Tick" in message
        assert "ValueError" in message
        assert "boom" in message

    def test_warning_carries_no_traceback(self, caplog) -> None:
        """WARNING stays one line; the traceback is DEBUG-only on purpose."""
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))

        assert _warn_records(caplog)[0].exc_info is None

    def test_traceback_is_available_at_debug(self, caplog) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.DEBUG, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))

        debug_records = [
            r for r in caplog.records if r.levelno == logging.DEBUG and r.exc_info
        ]
        assert len(debug_records) == 1
        assert debug_records[0].exc_info[0] is ValueError

    def test_other_handlers_still_run_after_a_failure(self, caplog) -> None:
        bus = EventBus()
        ran: list[str] = []
        bus.subscribe(_Tick, _raise_handler)
        bus.subscribe(_Tick, lambda e: ran.append("after"))

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))

        assert ran == ["after"]

    def test_lambda_handler_is_named_in_the_log(self, caplog) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, lambda e: (_ for _ in ()).throw(ValueError("boom")))

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))

        warnings = _warn_records(caplog)
        assert len(warnings) == 1
        # A lambda's __qualname__ ends in "<lambda>"; must not fall through to repr.
        assert "<lambda>" in warnings[0].getMessage()

    def test_healthy_publish_logs_nothing(self, caplog) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, lambda e: None)

        with caplog.at_level(logging.DEBUG, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))

        assert [r for r in caplog.records if r.name == _BUS_LOGGER] == []


class TestFailureRateLimit:
    def test_repeats_within_interval_are_suppressed(self, caplog, clock) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            for _ in range(50):
                bus.publish(_Tick(1))

        assert len(_warn_records(caplog)) == 1

    def test_next_line_after_interval_carries_suppressed_count(
        self, caplog, clock
    ) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            for _ in range(4):
                bus.publish(_Tick(1))
            clock[0] += bus_module._FAILURE_LOG_INTERVAL_SECONDS
            bus.publish(_Tick(1))

        warnings = _warn_records(caplog)
        assert len(warnings) == 2
        assert "3 more suppressed" in warnings[1].getMessage()

    def test_exactly_at_interval_logs_again(self, caplog, clock) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))
            clock[0] += bus_module._FAILURE_LOG_INTERVAL_SECONDS
            bus.publish(_Tick(1))

        assert len(_warn_records(caplog)) == 2

    def test_different_handlers_are_tracked_separately(self, caplog, clock) -> None:
        bus = EventBus()

        def _first(event: object) -> None:
            raise ValueError("boom")

        def _second(event: object) -> None:
            raise ValueError("boom")

        bus.subscribe(_Tick, _first)
        bus.subscribe(_Tick, _second)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))

        messages = [r.getMessage() for r in _warn_records(caplog)]
        assert len(messages) == 2
        assert any("_first" in m for m in messages)
        assert any("_second" in m for m in messages)

    def test_different_event_types_are_tracked_separately(self, caplog, clock) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)
        bus.subscribe(_OtherEvent, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))
            bus.publish(_OtherEvent(1))

        assert len(_warn_records(caplog)) == 2

    def test_different_exception_types_are_tracked_separately(
        self, caplog, clock
    ) -> None:
        bus = EventBus()
        calls = {"n": 0}

        def _alternating(event: object) -> None:
            calls["n"] += 1
            raise (ValueError if calls["n"] % 2 else RuntimeError)("boom")

        bus.subscribe(_Tick, _alternating)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))  # ValueError
            bus.publish(_Tick(1))  # RuntimeError — separate key, still logged

        messages = [r.getMessage() for r in _warn_records(caplog)]
        assert len(messages) == 2
        assert any("ValueError" in m for m in messages)
        assert any("RuntimeError" in m for m in messages)

    def test_clear_resets_suppression_state(self, caplog, clock) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))
            bus.clear()
            bus.subscribe(_Tick, _raise_handler)
            bus.publish(_Tick(1))

        assert len(_warn_records(caplog)) == 2

    def test_suppressed_count_is_not_reported_twice(self, caplog, clock) -> None:
        """Each suppressed count belongs to exactly one log line."""
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            bus.publish(_Tick(1))  # logs
            bus.publish(_Tick(1))  # suppressed
            bus.publish(_Tick(1))  # suppressed
            clock[0] += bus_module._FAILURE_LOG_INTERVAL_SECONDS
            bus.publish(_Tick(1))  # logs "(2 more suppressed)"
            clock[0] += bus_module._FAILURE_LOG_INTERVAL_SECONDS
            bus.publish(_Tick(1))  # nothing new was suppressed

        messages = [r.getMessage() for r in _warn_records(caplog)]
        assert len(messages) == 3
        assert "2 more suppressed" in messages[1]
        assert "suppressed" not in messages[2]

    def test_state_table_is_bounded(self, caplog, clock) -> None:
        """A pathological stream of distinct failures must not grow memory without bound."""
        bus = EventBus()
        limit = bus_module._FAILURE_LOG_MAX_KEYS
        exc = ValueError("boom")

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            for i in range(limit + 10):
                # A distinct event *instance* type per iteration = a distinct key.
                bus._log_handler_failure(_raise_handler, type(f"Evt{i}", (), {})(), exc)

        assert len(bus._failure_log_state) <= limit


class TestPublishAsyncLogsHandlerFailure:
    def test_sync_handler_failure_is_logged(self, caplog) -> None:
        bus = EventBus()
        bus.subscribe(_Tick, _raise_handler)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            asyncio.run(bus.publish_async(_Tick(1)))

        warnings = _warn_records(caplog)
        assert len(warnings) == 1
        assert "ValueError" in warnings[0].getMessage()

    def test_async_handler_failure_is_logged(self, caplog) -> None:
        bus = EventBus()

        async def _async_raise(event: object) -> None:
            raise RuntimeError("async boom")

        bus.subscribe(_Tick, _async_raise)

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            asyncio.run(bus.publish_async(_Tick(1)))

        warnings = _warn_records(caplog)
        assert len(warnings) == 1
        assert "_async_raise" in warnings[0].getMessage()
        assert "async boom" in warnings[0].getMessage()

    def test_publish_async_still_reaches_later_handlers(self, caplog) -> None:
        bus = EventBus()
        ran: list[str] = []
        bus.subscribe(_Tick, _raise_handler)
        bus.subscribe(_Tick, lambda e: ran.append("after"))

        with caplog.at_level(logging.WARNING, logger=_BUS_LOGGER):
            asyncio.run(bus.publish_async(_Tick(1)))

        assert ran == ["after"]


class TestHandlerName:
    def test_named_function_uses_qualname(self) -> None:
        assert bus_module._handler_name(_raise_handler) == "_raise_handler"

    def test_lambda_keeps_its_qualname_not_a_repr(self) -> None:
        name = bus_module._handler_name(lambda e: None)
        # Enclosing scope prefixes a lambda's qualname; only the tail is stable.
        assert name.endswith("<lambda>")
        assert not name.startswith("<")

    def test_object_without_qualname_falls_back_to_repr(self) -> None:
        class _Callable:
            def __call__(self, event: object) -> None: ...

            def __repr__(self) -> str:
                return "<_Callable sentinel>"

        assert bus_module._handler_name(_Callable()) == "<_Callable sentinel>"
