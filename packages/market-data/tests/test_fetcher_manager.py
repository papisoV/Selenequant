"""FetcherManager priority failover + breaker + last-good cache (offline).

All tests are offline: requests is fully mocked.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from selene_space_market.fetcher_manager import (
    BaseFetcher,
    DataSourceUnavailableError,
    FetcherManager,
    RateLimitError,
    SinaDailyFetcher,
    TencentDailyFetcher,
    _to_a_symbol,
)


class FakeFetcher(BaseFetcher):
    def __init__(
        self,
        name: str,
        priority: int,
        rows: list[dict] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.priority = priority
        self.rows = rows if rows is not None else [dict(_ROWS[0])]
        self.error = error
        self.calls: list[str] = []

    def fetch_daily(self, symbol: str) -> list[dict]:
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return list(self.rows)


_ROWS = [
    {
        "date": "2026-10-07",
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 1000,
    }
]


# ---------------------------------------------------------------------------
# Manager: priority ordering + failover


def test_manager_tries_sources_in_ascending_priority() -> None:
    healthy = FakeFetcher("healthy", priority=30, rows=[dict(_ROWS[0])])
    rate_limited = FakeFetcher("limited", priority=10, error=RateLimitError("429"))
    unavailable = FakeFetcher(
        "down", priority=20, error=DataSourceUnavailableError("x")
    )

    mgr = FetcherManager()
    for f in (healthy, rate_limited, unavailable):
        mgr.register(f)

    rows, source = mgr.fetch_daily("000001.SZ")

    assert source == "healthy"
    assert rows[0]["close"] == 10.2
    assert rate_limited.calls == ["000001.SZ"]
    assert unavailable.calls == ["000001.SZ"]
    assert healthy.calls == ["000001.SZ"]


def test_equal_priority_keeps_registration_order() -> None:
    a = FakeFetcher("a", priority=10, error=DataSourceUnavailableError("x"))
    b = FakeFetcher("b", priority=10, error=DataSourceUnavailableError("x"))
    c = FakeFetcher("c", priority=10, rows=[dict(_ROWS[0])])

    mgr = FetcherManager()
    for f in (c, a, b):  # register out of order on purpose
        mgr.register(f)

    _, source = mgr.fetch_daily("600519.SH")
    assert source == "c"
    assert mgr.source_order == ["c", "a", "b"]


def test_env_priority_reranks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SELENE_SINA_PRIORITY", "50")
    monkeypatch.setenv("SELENE_TENCENT_PRIORITY", "5")

    mgr = FetcherManager()
    mgr.register(SinaDailyFetcher())
    mgr.register(TencentDailyFetcher())

    assert mgr.source_order == ["tencent", "sina"]


def test_env_priority_invalid_keeps_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELENE_SINA_PRIORITY", "not-a-number")
    f = SinaDailyFetcher()
    assert f.priority == 10


def test_all_sources_fail_raises_last_error() -> None:
    first = FakeFetcher("first", priority=10, error=RateLimitError("429"))
    last = FakeFetcher("last", priority=20, error=DataSourceUnavailableError("boom"))

    mgr = FetcherManager()
    mgr.register(first)
    mgr.register(last)

    with pytest.raises(DataSourceUnavailableError, match="boom"):
        mgr.fetch_daily("000001.SZ")


def test_empty_registry_raises_unavailable() -> None:
    mgr = FetcherManager()
    with pytest.raises(DataSourceUnavailableError):
        mgr.fetch_daily("000001.SZ")


def test_empty_rows_from_healthy_source_falls_through() -> None:
    empty = FakeFetcher("empty", priority=10, rows=[])
    healthy = FakeFetcher("full", priority=20, rows=[dict(_ROWS[0])])

    mgr = FetcherManager()
    mgr.register(empty)
    mgr.register(healthy)

    _, source = mgr.fetch_daily("000001.SZ")
    assert source == "full"


# ---------------------------------------------------------------------------
# Manager: circuit breaker (RateLimitError streak + cooldown skip)


def test_breaker_opens_after_consecutive_rate_limits_and_skips_cooldown() -> None:
    now = {"t": 0.0}
    limited = FakeFetcher("limited", priority=10, error=RateLimitError("429"))
    backup = FakeFetcher("backup", priority=20, rows=[dict(_ROWS[0])])

    mgr = FetcherManager(
        breaker_threshold=3, breaker_cooldown_sec=300.0, clock=lambda: now["t"]
    )
    mgr.register(limited)
    mgr.register(backup)

    # Threshold reached on the 3rd consecutive rate limit.
    for _ in range(3):
        mgr.fetch_daily("000001.SZ")
    assert limited.calls == ["000001.SZ"] * 3

    # Within cooldown: limited is skipped entirely, no new call on it.
    mgr.fetch_daily("000001.SZ")
    assert limited.calls == ["000001.SZ"] * 3
    assert backup.calls == ["000001.SZ"] * 4

    # Past cooldown: limited is tried again.
    now["t"] += 301.0
    mgr.fetch_daily("000001.SZ")
    assert limited.calls == ["000001.SZ"] * 4


def test_breaker_streak_resets_on_success() -> None:
    now = {"t": 0.0}
    flaky = FakeFetcher("flaky", priority=10, rows=[dict(_ROWS[0])])
    backup = FakeFetcher("backup", priority=20, rows=[dict(_ROWS[0])])

    mgr = FetcherManager(
        breaker_threshold=3, breaker_cooldown_sec=300.0, clock=lambda: now["t"]
    )
    mgr.register(flaky)
    mgr.register(backup)

    # 2 failures, then a success: streak resets, so 2 more failures still
    # do not open the breaker.
    flaky.error = RateLimitError("429")
    mgr.fetch_daily("000001.SZ")
    mgr.fetch_daily("000001.SZ")
    flaky.error = None
    mgr.fetch_daily("000001.SZ")
    flaky.error = RateLimitError("429")
    mgr.fetch_daily("000001.SZ")
    mgr.fetch_daily("000001.SZ")

    assert flaky.calls == ["000001.SZ"] * 5  # never skipped


def test_breaker_threshold_one_opens_immediately() -> None:
    now = {"t": 0.0}
    limited = FakeFetcher("limited", priority=10, error=RateLimitError("429"))
    backup = FakeFetcher("backup", priority=20, rows=[dict(_ROWS[0])])

    mgr = FetcherManager(
        breaker_threshold=1, breaker_cooldown_sec=60.0, clock=lambda: now["t"]
    )
    mgr.register(limited)
    mgr.register(backup)

    mgr.fetch_daily("000001.SZ")
    mgr.fetch_daily("000001.SZ")
    assert limited.calls == ["000001.SZ"]  # skipped after first 429

    now["t"] += 61.0
    mgr.fetch_daily("000001.SZ")
    assert limited.calls == ["000001.SZ"] * 2  # cooldown expired, retried


def test_breaker_params_read_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = {"t": 0.0}
    monkeypatch.setenv("SELENE_FETCHER_BREAKER_THRESHOLD", "2")
    monkeypatch.setenv("SELENE_FETCHER_BREAKER_COOLDOWN_SEC", "120")

    limited = FakeFetcher("limited", priority=10, error=RateLimitError("429"))
    backup = FakeFetcher("backup", priority=20, rows=[dict(_ROWS[0])])

    mgr = FetcherManager(clock=lambda: now["t"])
    mgr.register(limited)
    mgr.register(backup)

    mgr.fetch_daily("000001.SZ")
    mgr.fetch_daily("000001.SZ")  # 2nd failure → opens
    mgr.fetch_daily("000001.SZ")
    assert limited.calls == ["000001.SZ"] * 2  # skipped within 120s cooldown


def test_breaker_params_invalid_env_keeps_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SELENE_FETCHER_BREAKER_THRESHOLD", "junk")
    monkeypatch.setenv("SELENE_FETCHER_BREAKER_COOLDOWN_SEC", "junk")

    limited = FakeFetcher("limited", priority=10, error=RateLimitError("429"))
    backup = FakeFetcher("backup", priority=20, rows=[dict(_ROWS[0])])

    mgr = FetcherManager(clock=lambda: 0.0)
    mgr.register(limited)
    mgr.register(backup)

    # Default threshold 3: still tried on the 3rd call.
    for _ in range(3):
        mgr.fetch_daily("000001.SZ")
    assert limited.calls == ["000001.SZ"] * 3


# ---------------------------------------------------------------------------
# Manager: per-fetcher locking


def test_concurrent_fetches_serialize_per_source() -> None:
    import threading
    import time as time_mod

    class SlowFetcher(BaseFetcher):
        name = "slow"
        priority = 10

        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        def fetch_daily(self, symbol: str) -> list[dict]:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            time_mod.sleep(0.05)
            self.active -= 1
            return [dict(_ROWS[0])]

    slow = SlowFetcher()
    mgr = FetcherManager()
    mgr.register(slow)

    threads = [
        threading.Thread(target=lambda: mgr.fetch_daily("000001.SZ"))  # noqa: B023
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert slow.max_active == 1  # mutex held per source


# ---------------------------------------------------------------------------
# Manager: row cleaning + indicator enrichment


def test_rows_cleaned_and_sorted_by_date() -> None:
    messy = [
        {
            "date": "2026-10-08",
            "open": "11.0",
            "high": "11.5",
            "low": "10.8",
            "close": "11.2",
            "volume": "2000",
        },
        {
            "date": "",
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "volume": None,
        },
        {
            "date": "2026-10-07",
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "volume": 1000,
        },
        "garbage-row",
    ]
    src = FakeFetcher("src", priority=10, rows=messy)

    mgr = FetcherManager()
    mgr.register(src)

    rows, _ = mgr.fetch_daily("000001.SZ")

    assert [r["date"] for r in rows] == ["2026-10-07", "2026-10-08"]
    assert all(
        isinstance(r["open"], float) and isinstance(r["volume"], float) for r in rows
    )


def test_indicator_columns_added() -> None:
    rows_in = [
        {
            "date": f"2026-09-{d:02d}",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 100.0,
        }
        for d in range(1, 7)
    ]
    src = FakeFetcher("src", priority=10, rows=rows_in)

    mgr = FetcherManager()
    mgr.register(src)

    rows, _ = mgr.fetch_daily("000001.SZ")

    assert len(rows) == 6
    first = rows[0]
    assert first["ma5"] == 10.0
    assert first["ma10"] == 10.0
    assert first["ma20"] == 10.0
    assert first["volume_ratio"] == 1.0
    last = rows[-1]
    assert last["ma5"] == 10.0
    assert last["volume_ratio"] == 1.0  # constant volume → ratio 1


def test_volume_ratio_uses_mean_of_previous_five() -> None:
    rows_in = [
        {
            "date": f"2026-09-{d:02d}",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": v,
        }
        for d, v in zip(range(1, 8), [100.0] * 6 + [600.0], strict=True)
    ]
    src = FakeFetcher("src", priority=10, rows=rows_in)

    mgr = FetcherManager()
    mgr.register(src)

    rows, _ = mgr.fetch_daily("000001.SZ")
    assert rows[-1]["volume_ratio"] == pytest.approx(6.0)


def test_ma_windows_partial_at_start() -> None:
    closes = [10.0, 12.0]
    rows_in = [
        {
            "date": f"2026-09-{d:02d}",
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": c,
            "volume": 100.0,
        }
        for d, c in enumerate(closes, start=1)
    ]
    src = FakeFetcher("src", priority=10, rows=rows_in)

    mgr = FetcherManager()
    mgr.register(src)

    rows, _ = mgr.fetch_daily("000001.SZ")
    assert rows[1]["ma5"] == pytest.approx(11.0)  # mean over available bars


# ---------------------------------------------------------------------------
# Manager: last-good cache


def test_last_good_served_within_ttl_on_total_failure() -> None:
    now = {"t": 1000.0}
    mgr = FetcherManager(last_good_ttl_sec=300.0, clock=lambda: now["t"])
    healthy = FakeFetcher("healthy", priority=10, rows=[dict(_ROWS[0])])
    mgr.register(healthy)

    rows, source = mgr.fetch_daily("000001.SZ")
    assert source == "healthy"

    healthy.error = DataSourceUnavailableError("now down")
    rows, source = mgr.fetch_daily("000001.SZ")
    assert source == "last-good:healthy"
    assert rows[0]["close"] == 10.2


def test_last_good_expired_retries_and_raises() -> None:
    now = {"t": 1000.0}
    mgr = FetcherManager(last_good_ttl_sec=300.0, clock=lambda: now["t"])
    healthy = FakeFetcher("healthy", priority=10, rows=[dict(_ROWS[0])])
    mgr.register(healthy)
    mgr.fetch_daily("000001.SZ")

    healthy.error = DataSourceUnavailableError("down")
    now["t"] += 301.0
    with pytest.raises(DataSourceUnavailableError):
        mgr.fetch_daily("000001.SZ")


def test_success_after_failure_refreshes_cache_and_source_label() -> None:
    now = {"t": 0.0}
    mgr = FetcherManager(last_good_ttl_sec=300.0, clock=lambda: now["t"])
    healthy = FakeFetcher("healthy", priority=10, rows=[dict(_ROWS[0])])
    mgr.register(healthy)
    mgr.fetch_daily("000001.SZ")

    healthy.error = DataSourceUnavailableError("down")
    _, source = mgr.fetch_daily("000001.SZ")
    assert source == "last-good:healthy"

    healthy.error = None
    now["t"] += 301.0
    _, source = mgr.fetch_daily("000001.SZ")
    assert source == "healthy"


def test_last_good_returns_snapshot_copies_not_aliases() -> None:
    now = {"t": 0.0}
    mgr = FetcherManager(last_good_ttl_sec=300.0, clock=lambda: now["t"])
    healthy = FakeFetcher("healthy", priority=10, rows=[dict(_ROWS[0])])
    mgr.register(healthy)
    mgr.fetch_daily("000001.SZ")

    healthy.error = DataSourceUnavailableError("down")
    rows_a, _ = mgr.fetch_daily("000001.SZ")
    rows_b, _ = mgr.fetch_daily("000001.SZ")
    assert rows_a == rows_b
    assert rows_a is not rows_b


# ---------------------------------------------------------------------------
# SinaDailyFetcher / TencentDailyFetcher contract (requests mocked)


def test_sina_fetcher_parses_rows() -> None:
    payload = (
        '[{"day":"2026-10-07","open":"10.0","high":"10.5",'
        '"low":"9.8","close":"10.2","volume":"1000"}]'
    )

    class _Resp:
        status_code = 200
        text = payload

        def json(self):
            import json

            return json.loads(payload)

    with patch.object(requests, "get", return_value=_Resp()) as mock_get:
        rows = SinaDailyFetcher().fetch_daily("000001.SZ")

    assert rows == [
        {
            "date": "2026-10-07",
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "volume": 1000.0,
        }
    ]
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["symbol"] == "sz000001"
    assert kwargs["timeout"] == 10


def test_tencent_fetcher_parses_rows_close_before_high_low() -> None:
    payload = {
        "code": 0,
        "data": {
            "sz000001": {
                "qfqday": [
                    ["2026-10-07", "10.0", "10.2", "10.5", "9.8", "1000.0"],
                ]
            }
        },
    }

    class _Resp:
        status_code = 200

        def json(self):
            return payload

    with patch.object(requests, "get", return_value=_Resp()) as mock_get:
        rows = TencentDailyFetcher().fetch_daily("000001.SZ")

    assert rows == [
        {
            "date": "2026-10-07",
            "open": 10.0,
            "close": 10.2,
            "high": 10.5,
            "low": 9.8,
            "volume": 1000.0,
        }
    ]
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["param"].startswith("sz000001,day")
    assert kwargs["timeout"] == 10


def test_tencent_fetcher_falls_back_to_day_key() -> None:
    payload = {
        "data": {
            "sh600519": {
                "day": [["2026-10-07", "10.0", "10.2", "10.5", "9.8", "1000.0"]]
            }
        }
    }

    class _Resp:
        status_code = 200

        def json(self):
            return payload

    with patch.object(requests, "get", return_value=_Resp()):
        rows = TencentDailyFetcher().fetch_daily("600519.SH")
    assert rows[0]["close"] == 10.2
    assert rows[0]["high"] == 10.5


@pytest.mark.parametrize("fetcher_cls", [SinaDailyFetcher, TencentDailyFetcher])
def test_fetcher_connection_error_normalized(fetcher_cls: type) -> None:
    with patch.object(requests, "get", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(DataSourceUnavailableError):
            fetcher_cls().fetch_daily("000001.SZ")


@pytest.mark.parametrize("fetcher_cls", [SinaDailyFetcher, TencentDailyFetcher])
def test_fetcher_http_error_normalized(fetcher_cls: type) -> None:
    class _Resp:
        status_code = 500
        text = "server error"

        def json(self):
            raise ValueError("not json")

    with patch.object(requests, "get", return_value=_Resp()):
        with pytest.raises(DataSourceUnavailableError):
            fetcher_cls().fetch_daily("000001.SZ")


@pytest.mark.parametrize("fetcher_cls", [SinaDailyFetcher, TencentDailyFetcher])
def test_fetcher_empty_data_normalized(fetcher_cls: type) -> None:
    class _Resp:
        status_code = 200
        text = "[]"

        def json(self):
            return {"data": {}} if fetcher_cls is TencentDailyFetcher else []

    with patch.object(requests, "get", return_value=_Resp()):
        with pytest.raises(DataSourceUnavailableError):
            fetcher_cls().fetch_daily("000001.SZ")


# ---------------------------------------------------------------------------
# Symbol conversion


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("000001.SZ", "sz000001"),
        ("600519.SH", "sh600519"),
    ],
)
def test_to_a_symbol_valid(symbol: str, expected: str) -> None:
    assert _to_a_symbol(symbol) == expected


@pytest.mark.parametrize(
    "symbol",
    ["000001", "XYZ.ABC", "12345.SH", "ABCDEF.SZ", "600519.HK", ""],
)
def test_to_a_symbol_invalid_raises_value_error(symbol: str) -> None:
    with pytest.raises(ValueError):
        _to_a_symbol(symbol)


def test_invalid_symbol_propagates_value_error_without_fallback() -> None:
    """Bad symbols are deterministic — no point trying the next source."""
    sina = SinaDailyFetcher()
    healthy = FakeFetcher("healthy", priority=20, rows=[dict(_ROWS[0])])
    mgr = FetcherManager()
    mgr.register(sina)
    mgr.register(healthy)

    with pytest.raises(ValueError):
        mgr.fetch_daily("NOPE")

    assert healthy.calls == []
