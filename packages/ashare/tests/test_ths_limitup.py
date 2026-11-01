"""Tests for the 10jqka limit-up fetchers' failure contract (``ths_data``).

The load-bearing property: an unanswered endpoint and an empty answer must be
distinguishable. Both used to collapse into an empty frame, and the ladder
combiner reads an empty continuous frame as "no 连板 today" — so one timed-out
request silently relabelled every pool name a 首板. ``strict=True`` is the
caller's way to ask for the raise instead.

Zero network: a fake ``requests`` module is injected for the failure cases.
"""

from __future__ import annotations

import sys

import pandas as pd
import pytest

import selene_space_ashare.ths_data as ths
from selene_space_ashare.ths_data import (
    LimitUpDataUnavailable,
    fetch_continuous_limit_up,
    fetch_limit_up_pool_full,
)


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    # The fetchers write parquet under CACHE_DIR; keep the real cache out of it.
    monkeypatch.setattr(ths, "CACHE_DIR", tmp_path)
    yield


class _TransportError(Exception):
    """Stand-in for ``requests.RequestException`` in the injected module."""


def _install_requests(monkeypatch, *, get):
    """Inject a fake ``requests`` module (the fetchers import it lazily)."""
    fake = type(sys)("requests")
    fake.get = get
    fake.RequestException = _TransportError
    monkeypatch.setitem(sys.modules, "requests", fake)
    return fake


def _down(monkeypatch):
    def _boom(*args, **kwargs):
        raise _TransportError("connection reset")

    _install_requests(monkeypatch, get=_boom)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


# ─── continuous_limit_up ────────────────────────────────────────────


class TestContinuousFailureContract:
    def test_lenient_transport_failure_returns_empty_frame(self, monkeypatch) -> None:
        _down(monkeypatch)
        out = fetch_continuous_limit_up(date="20241001", force=True)
        assert isinstance(out, pd.DataFrame)
        assert out.empty

    def test_strict_transport_failure_raises(self, monkeypatch) -> None:
        _down(monkeypatch)
        with pytest.raises(LimitUpDataUnavailable):
            fetch_continuous_limit_up(date="20241001", force=True, strict=True)

    def test_strict_empty_answer_does_not_raise(self, monkeypatch) -> None:
        """A reply with no groups is a quiet tape, not an outage."""
        _install_requests(monkeypatch, get=lambda *a, **k: _Resp({"data": []}))
        out = fetch_continuous_limit_up(date="20241001", force=True, strict=True)
        assert out.empty

    def test_null_continue_num_falls_back_to_group_height(self, monkeypatch) -> None:
        """An explicit JSON null must not make the stock vanish.

        `dict.get("continue_num", height)` only defaults on a *missing* key, so
        a present-but-null value reached the combiner as NaN and the stock was
        dropped from every row — it is already excluded from the 首板 difference
        for being listed here, so it disappeared entirely.
        """
        payload = {
            "data": [
                {
                    "height": 4,
                    "code_list": [
                        {"code": "600001", "name": "甲", "continue_num": None},
                        {"code": "600002", "name": "乙", "continue_num": 5},
                    ],
                }
            ]
        }
        _install_requests(monkeypatch, get=lambda *a, **k: _Resp(payload))
        out = fetch_continuous_limit_up(date="20241001", force=True)
        got = dict(zip(out["code"], out["consecutive_boards"]))
        assert got == {"600001": 4, "600002": 5}

    def test_missing_continue_num_also_falls_back(self, monkeypatch) -> None:
        payload = {
            "data": [
                {
                    "height": 3,
                    "code_list": [{"code": "600001", "name": "甲"}],
                }
            ]
        }
        _install_requests(monkeypatch, get=lambda *a, **k: _Resp(payload))
        out = fetch_continuous_limit_up(date="20241001", force=True)
        assert out["consecutive_boards"].iloc[0] == 3


# ─── limit_up_pool_full ─────────────────────────────────────────────


class TestPoolFailureContract:
    def test_lenient_transport_failure_returns_empty_frame(self, monkeypatch) -> None:
        _down(monkeypatch)
        out = fetch_limit_up_pool_full(date="20241001", force=True)
        assert out.empty

    def test_strict_transport_failure_raises(self, monkeypatch) -> None:
        _down(monkeypatch)
        with pytest.raises(LimitUpDataUnavailable):
            fetch_limit_up_pool_full(date="20241001", force=True, strict=True)

    def test_strict_empty_page_ends_the_walk_without_raising(self, monkeypatch) -> None:
        """A past-the-end / no-names page is how the paging walk terminates.

        Strict must only raise on an unanswered request; raising here would
        break the fallback walk for any pool that genuinely has no more pages.
        """
        _install_requests(
            monkeypatch, get=lambda *a, **k: _Resp({"data": {"info": []}})
        )
        out = fetch_limit_up_pool_full(date="20241001", force=True, strict=True)
        assert out.empty
