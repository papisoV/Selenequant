"""scripts/sync_klines.py: page-termination must not infer 'caught up' from size.

The original loop ended as soon as a page came back shorter than
``BATCH_SIZE`` (1500). Binance spot caps ``/api/v3/klines`` at 1000 rows per
request, so every spot page *looked* short and the loop stopped after its
first page — measured live as spot 1m/5m stuck exactly 999 bars past a
five-month-old tail while every other interval caught up.

Termination has to be evidence-based: an empty page, or a page whose cursor
does not advance. ``KlineDataManager.paginate_backwards`` documents the same
rule ("never on ``len(page) < _PAGE``").
"""

from __future__ import annotations

import importlib

import pytest

from selene_space_types.market import KlineEvent

HOUR_MS = 3_600_000
BASE_TS = 1711000000000


def _kline(ts: int) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        market_type="spot",
        symbol="SOL/USDT",
        timestamp=ts,
        interval="1h",
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
        closed=True,
    )


def _sync_module():
    return importlib.import_module("scripts.sync_klines")


class _CappedService:
    """Forward pager with an exchange-style per-request cap."""

    def __init__(self, candles: list[KlineEvent], cap: int) -> None:
        self._ordered = sorted(candles, key=lambda k: k.timestamp)
        self._cap = cap
        self.calls = 0

    async def fetch_klines_rest(
        self, *, symbol, exchange, market_type, interval, limit, start_time=None, **kw
    ):
        self.calls += 1
        pool = [
            k for k in self._ordered if start_time is None or k.timestamp > start_time
        ]
        return pool[: min(limit, self._cap)]


class TestDrainPagesTermination:
    @pytest.mark.asyncio
    async def test_short_pages_under_exchange_cap_are_not_caught_up(self) -> None:
        # 2,500 bars, exchange cap 1,000 < BATCH_SIZE. A size-based stop
        # collects 1,000; a cursor-based walk collects all 2,500.
        mod = _sync_module()
        candles = [_kline(BASE_TS + i * HOUR_MS) for i in range(2500)]
        svc = _CappedService(candles, cap=1000)
        sync = mod.KlineSync.__new__(mod.KlineSync)
        sync._service = svc

        out = await sync._drain_pages(
            "binance", "spot", "SOL/USDT", "1h", BASE_TS - HOUR_MS, "test"
        )
        assert len(out) == 2500
        assert svc.calls == 4  # 1000 + 1000 + 500, then one empty page ends it

    @pytest.mark.asyncio
    async def test_empty_page_terminates(self) -> None:
        mod = _sync_module()
        svc = _CappedService([], cap=1000)
        sync = mod.KlineSync.__new__(mod.KlineSync)
        sync._service = svc
        out = await sync._drain_pages("binance", "spot", "SOL/USDT", "1h", BASE_TS, "t")
        assert out == []

    @pytest.mark.asyncio
    async def test_stalled_cursor_terminates_instead_of_spinning(self) -> None:
        # A source that keeps returning the same first page forever must not
        # hang the sync: the cursor cannot advance, so the loop must stop.
        mod = _sync_module()
        page = [_kline(BASE_TS + i * HOUR_MS) for i in range(5)]
        svc = _CappedService(page, cap=5)
        # Ignore start_time: always the same page (a broken/non-advancing feed).
        svc.fetch_klines_rest = _always_same_page(page)  # type: ignore[method-assign]
        sync = mod.KlineSync.__new__(mod.KlineSync)
        sync._service = svc
        out = await sync._drain_pages(
            "binance", "spot", "SOL/USDT", "1h", BASE_TS - 1, "t"
        )
        assert len(out) == 5  # collected once, then bailed
        assert _always_same_page.calls == 2  # type: ignore[attr-defined]  # 2nd call proves cursor didn't advance


def _always_same_page(page):
    async def _fetch(**kw):
        _always_same_page.calls += 1
        return list(page)

    _always_same_page.calls = 0
    return _fetch
