"""The kline health probe must see a frozen store, not just a clean ledger.

The sweep's own counters are blind to a dead network: every fetch fails, no new
holes appear, and ``holes_permanent`` stays flat — so the probe reported
``healthy`` for the whole of a 13h outage while 409 of 901 crypto partitions
sat stale. Liveness has to come from the data's own clock.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from app.main import (
    _KLINE_STALE_PARTITIONS_DEGRADED,
    _KLINE_STALE_PARTITIONS_UNHEALTHY,
    _KLINE_STALE_PROBE_TTL_S,
    _KLINE_STALE_SWEEP_ROUNDS,
    _KLINE_STALE_TOLERANCE_BARS,
    _KLINE_SWEEP_INTERVAL,
    _cached_stale_partitions,
    _count_stale_crypto_partitions,
)
from selene_space_market.kline_db import KlineDB
from selene_space_market.kline_manager import KlineDataManager
from selene_space_types.market import KlineEvent

NOW_MS = 1_760_000_000_000
HOUR_MS = 3_600_000
_MINUTE_MS = 60_000


def _allowed_lag_ms(interval_ms: int) -> int:
    """The lag a healthy partition is permitted, mirroring the probe's rule."""
    return _KLINE_STALE_TOLERANCE_BARS * interval_ms + _KLINE_STALE_SWEEP_ROUNDS * int(
        _KLINE_SWEEP_INTERVAL * 1000
    )


class _Manager:
    """Just the surface the probe uses: ``_db`` and ``interval_ms``."""

    def __init__(self, db: KlineDB) -> None:
        self._db = db
        self.interval_ms = KlineDataManager.interval_ms


class _MarketSvc:
    def __init__(self, sources) -> None:  # type: ignore[no-untyped-def]
        self.sources = sources


def _bar(
    symbol: str, ts: int, *, exchange: str = "binance", interval: str = "1h"
) -> KlineEvent:
    return KlineEvent(
        exchange=exchange,
        market_type="futures",
        symbol=symbol,
        timestamp=ts,
        interval=interval,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
        closed=True,
    )


@pytest.fixture
def db() -> KlineDB:
    store = KlineDB(":memory:")
    store.open()
    return store


def test_current_partitions_are_not_stale(db: KlineDB, monkeypatch) -> None:
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    db.put_klines([_bar("BTC/USDT", NOW_MS - HOUR_MS)])
    assert (
        _count_stale_crypto_partitions(
            _Manager(db), _MarketSvc({("binance", "futures")})
        )
        == 0
    )


def test_partition_behind_the_clock_counts(db: KlineDB, monkeypatch) -> None:
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    db.put_klines([_bar("BTC/USDT", NOW_MS - 6 * HOUR_MS)])
    assert (
        _count_stale_crypto_partitions(
            _Manager(db), _MarketSvc({("binance", "futures")})
        )
        == 1
    )


def test_fast_partition_within_a_sweep_round_is_not_stale(
    db: KlineDB, monkeypatch
) -> None:
    # The REST sweep is the only writer (WS klines are broadcast, never
    # persisted), so a 1m partition refreshed on time is still ~30min behind
    # the clock between rounds. Counting bars alone flagged 21 such partitions
    # against a degraded line of 25.
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    db.put_klines([_bar("BTC/USDT", NOW_MS - 10 * _MINUTE_MS, interval="1m")])
    assert (
        _count_stale_crypto_partitions(
            _Manager(db), _MarketSvc({("binance", "futures")})
        )
        == 0
    )
    # A round that runs long must not raise a false alarm either: the live
    # store showed 1m partitions 34 minutes behind after a normal sweep.
    one_round_plus_a_late_round = int((_KLINE_SWEEP_INTERVAL + 5 * 60) * 1000)
    db.put_klines(
        [_bar("BTC/USDT", NOW_MS - one_round_plus_a_late_round, interval="1m")]
    )
    assert (
        _count_stale_crypto_partitions(
            _Manager(db), _MarketSvc({("binance", "futures")})
        )
        == 0
    )


def test_fast_partition_missing_a_sweep_window_is_stale(
    db: KlineDB, monkeypatch
) -> None:
    # Skipping whole sweep rounds is the failure the probe exists to catch, and
    # it has to fire on the fastest interval, not just the slow ones.
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    missed_ms = _allowed_lag_ms(_MINUTE_MS) + 2 * _MINUTE_MS
    db.put_klines([_bar("BTC/USDT", NOW_MS - missed_ms, interval="1m")])
    assert (
        _count_stale_crypto_partitions(
            _Manager(db), _MarketSvc({("binance", "futures")})
        )
        == 1
    )


def test_slow_interval_lagging_by_design_is_not_stale(db: KlineDB, monkeypatch) -> None:
    # The sweeper does not touch a partition until it is >2 bars behind, so a
    # healthy store holds 12h partitions ~21.6h back, 1d ones ~33.6h and 3d
    # ones ~81.6h. Measuring these against the sweep cadence alone called all
    # 379 of them stale; they are the largest family in the store.
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    for symbol, interval, behind in (
        ("0GUSDT", "12h", 21.6),
        ("0GUSDT", "1d", 33.6),
        ("BTCUSDT", "3d", 81.6),
    ):
        db.put_klines([_bar(symbol, NOW_MS - int(behind * HOUR_MS), interval=interval)])
    assert (
        _count_stale_crypto_partitions(
            _Manager(db), _MarketSvc({("binance", "futures")})
        )
        == 0
    )


def test_slow_interval_past_its_own_tolerance_is_stale(
    db: KlineDB, monkeypatch
) -> None:
    # The slow-interval exemption is a tolerance, not a blind spot: 12h is
    # allowed 25h of lag, so 30h is an outage.
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    behind_ms = _allowed_lag_ms(12 * HOUR_MS) + 5 * HOUR_MS
    db.put_klines([_bar("BTCUSDT", NOW_MS - behind_ms, interval="12h")])
    assert (
        _count_stale_crypto_partitions(
            _Manager(db), _MarketSvc({("binance", "futures")})
        )
        == 1
    )


def test_unregistered_source_is_ignored(db: KlineDB, monkeypatch) -> None:
    # An equity partition is stale by construction overnight; only sources the
    # app actually serves are in scope.
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    db.put_klines([_bar("600519.SH", NOW_MS - 72 * HOUR_MS, exchange="astock")])
    assert (
        _count_stale_crypto_partitions(
            _Manager(db), _MarketSvc({("binance", "futures")})
        )
        == 0
    )


def test_thresholds_leave_a_dead_network_unhealthy() -> None:
    # A whole-outage store measured 409/901 stale partitions. One delisted
    # symbol (1 stale) must stay below the degraded line.
    assert 1 < _KLINE_STALE_PARTITIONS_DEGRADED
    assert 409 >= _KLINE_STALE_PARTITIONS_UNHEALTHY


@pytest.mark.asyncio
async def test_cached_probe_scans_once_within_ttl(
    db: KlineDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    db.put_klines([_bar("BTC/USDT", NOW_MS - 6 * HOUR_MS)])
    calls = 0
    real = _count_stale_crypto_partitions

    def counting(manager, market_svc):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return real(manager, market_svc)

    monkeypatch.setattr("app.main._count_stale_crypto_partitions", counting)
    state: dict[str, Any] = {
        "kline_manager": _Manager(db),
        "market_svc": _MarketSvc({("binance", "futures")}),
    }

    assert await _cached_stale_partitions(state) == 1
    assert await _cached_stale_partitions(state) == 1
    assert calls == 1, "the second call within the TTL must not touch the store"

    # Age the memo past the TTL: the next call rescans and sees the new bar.
    memo: tuple[float, int] = state["kline_stale_probe"]
    state["kline_stale_probe"] = (memo[0] - _KLINE_STALE_PROBE_TTL_S - 1.0, memo[1])
    db.put_klines([_bar("BTC/USDT", NOW_MS - HOUR_MS)])
    assert await _cached_stale_partitions(state) == 0
    assert calls == 2


@pytest.mark.asyncio
async def test_cached_probe_keeps_last_value_on_failure(
    db: KlineDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.main.time.time", lambda: NOW_MS / 1000)
    state = {
        "kline_manager": _Manager(db),
        "market_svc": _MarketSvc({("binance", "futures")}),
        "kline_stale_probe": (0.0, 42),
    }

    def boom(manager, market_svc):  # type: ignore[no-untyped-def]
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("app.main._count_stale_crypto_partitions", boom)
    assert await _cached_stale_partitions(state) == 42
