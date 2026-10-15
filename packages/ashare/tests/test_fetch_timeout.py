"""Tests for run_with_timeout subprocess hard-timeout isolation.

No real network: all callables are synthetic; slow ones sleep.
Spawned children are daemonic and terminated on timeout.
"""

from __future__ import annotations

import time

import pytest

from selene_space_ashare import data_fetcher
from selene_space_ashare.data_fetcher import FetchTimeoutError, run_with_timeout


def _echo(value):
    return value


def _sleep_long(seconds):
    time.sleep(seconds)
    return "never"


def _boom():
    raise ValueError("child exploded")


class CustomError(RuntimeError):
    """Module-level so the child can pickle it back to the parent."""


def _raise_custom():
    raise CustomError("pickled across processes")


def test_returns_result_immediately():
    assert run_with_timeout(_echo, args=(42,), timeout=10) == 42


def test_timeout_raises_and_does_not_hang():
    start = time.monotonic()
    with pytest.raises(FetchTimeoutError):
        run_with_timeout(_sleep_long, args=(10,), timeout=2)
    elapsed = time.monotonic() - start
    assert elapsed < 6


def test_exception_propagates_same_type():
    with pytest.raises(ValueError, match="child exploded"):
        run_with_timeout(_boom, timeout=15)


def test_custom_exception_type_survives_pickle():
    with pytest.raises(CustomError, match="pickled across processes"):
        run_with_timeout(_raise_custom, timeout=15)


def test_unpicklable_fn_runs_inline():
    class NotPicklable:
        def __call__(self):
            return "inline"

        def __reduce__(self):
            raise AssertionError("must not be pickled")

    assert run_with_timeout(NotPicklable(), timeout=5) == "inline"


def test_default_timeout_from_env(monkeypatch):
    monkeypatch.setenv(data_fetcher._FETCH_TIMEOUT_ENV, "0.5")
    start = time.monotonic()
    with pytest.raises(FetchTimeoutError):
        run_with_timeout(_sleep_long, args=(10,))
    assert time.monotonic() - start < 6


def test_timeout_resolution_defaults(monkeypatch):
    monkeypatch.delenv(data_fetcher._FETCH_TIMEOUT_ENV, raising=False)
    assert data_fetcher._fetch_timeout_seconds(None) == 30.0
    assert data_fetcher._fetch_timeout_seconds(7) == 7.0
