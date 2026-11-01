"""Tests for selene_space_ashare.data_fetcher money-flow / LHB functions.

Uses monkeypatch to stub akshare — no network access.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from selene_space_ashare import data_fetcher


@pytest.fixture(autouse=True)
def _reset_universe_cooldown(monkeypatch):
    """Module-level cooldown must not leak between tests."""
    monkeypatch.setattr(data_fetcher, "_universe_cooldown_until", 0.0)


@pytest.fixture()
def fake_fund_flow() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": ["2024-01-05", "2024-01-06", "2024-01-07"],
            "主力净流入-净额": [1.0, 2.0, 3.0],
            "超大单净流入-净额": [0.5, 0.6, 0.7],
        }
    )


def test_get_fund_flow_renames_date_and_filters(monkeypatch, fake_fund_flow):
    captured: dict[str, object] = {}

    def _fake(stock: str, market: str) -> pd.DataFrame:
        captured["stock"] = stock
        captured["market"] = market
        return fake_fund_flow

    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: _fake(**kw))
    df = data_fetcher.get_fund_flow("600519", start="20240101", end="20240106")
    assert captured["stock"] == "600519"
    assert captured["market"] == "sh"  # 6-prefixed → sh
    assert "日期" not in df.columns
    assert "date" in df.columns
    assert "code" in df.columns
    # 过滤后只剩 20240105 / 20240106 两行
    assert len(df) == 2
    assert list(df["date"]) == ["20240105", "20240106"]


def test_get_fund_flow_sz_market(monkeypatch, fake_fund_flow):
    captured: dict[str, object] = {}

    def _fake(stock: str, market: str) -> pd.DataFrame:
        captured["market"] = market
        return fake_fund_flow

    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: _fake(**kw))
    data_fetcher.get_fund_flow("000001")
    assert captured["market"] == "sz"  # 0/3-prefixed → sz


def test_get_fund_flow_empty(monkeypatch):
    monkeypatch.setattr(
        data_fetcher, "_fetch_with_retry", lambda fn, **kw: pd.DataFrame()
    )
    assert data_fetcher.get_fund_flow("600519").empty


def test_get_lhb_calls_detail_em_with_same_bounds(monkeypatch):
    captured: dict[str, str] = {}

    def _fake(start_date: str, end_date: str) -> pd.DataFrame:
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        return pd.DataFrame({"股票代码": ["600519"], "股票名称": ["贵州茅台"]})

    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: _fake(**kw))
    df = data_fetcher.get_lhb("20240105")
    assert captured["start_date"] == "20240105"
    assert captured["end_date"] == "20240105"
    assert not df.empty


def test_get_lhb_empty(monkeypatch):
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: None)
    assert data_fetcher.get_lhb("20240105").empty


def test_fetch_fund_flow_http_parses_klines(monkeypatch):
    """httpx direct path replicates akshare's Eastmoney parse (f51..f65)."""
    import httpx

    captured: dict[str, object] = {}

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "rc": 0,
                "data": {"klines": ["2024-01-05,1,2,3,4,5,6,7,8,9,10,11,12,13,14"]},
            }

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url: str, params: dict | None = None):
            captured["url"] = url
            captured["params"] = params
            return _FakeResp()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    df = data_fetcher._fetch_fund_flow_http("600519", "sh")
    assert captured["url"] == data_fetcher._EASTMONEY_FFLOW_URL
    assert captured["params"]["secid"] == "1.600519"
    assert captured["params"]["lmt"] == "0"
    assert list(df["日期"]) == ["2024-01-05"]
    assert "主力净流入-净额" in df.columns
    assert "收盘价" in df.columns
    assert "主力净流入-净占比" in df.columns


def test_get_fund_flow_falls_back_to_akshare(monkeypatch, fake_fund_flow):
    """httpx empty → akshare fallback keeps the same DataFrame contract."""
    calls: list[str] = []

    def _mock(fn, **kw):
        calls.append(fn.__name__)
        if len(calls) == 1:
            return pd.DataFrame()
        return fake_fund_flow

    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", _mock)
    df = data_fetcher.get_fund_flow("600519")
    assert calls == ["_fetch_fund_flow_http", "stock_individual_fund_flow"]
    assert not df.empty
    assert "date" in df.columns
    assert "code" in df.columns


def test_get_margin_data_uses_date_detail_and_filters_by_symbol(monkeypatch):
    """akshare 1.18.55: per-symbol margin removed → date-based full-market detail + filter."""
    import akshare as ak

    captured: dict[str, str] = {}
    full = pd.DataFrame(
        {
            "信用交易日期": ["20260814", "20260814"],
            "标的证券代码": ["600519", "600000"],
            "标的证券简称": ["贵州茅台", "浦发银行"],
            "融资余额": [1.0, 2.0],
        }
    )

    def _fake_margin(**kw):
        captured["date"] = kw.get("date")
        return full

    monkeypatch.setattr(ak, "stock_margin_detail_sse", _fake_margin)
    monkeypatch.setattr(
        ak,
        "tool_trade_date_hist_sina",
        lambda: pd.DataFrame({"trade_date": ["2026-08-14"]}),
    )
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: fn(**kw))
    monkeypatch.setattr(data_fetcher, "_write_cache", lambda *a, **k: None)

    df = data_fetcher.get_margin_data("600519", force=True)
    assert captured["date"] == "20260814"
    assert list(df["标的证券代码"]) == ["600519"]


# ─── get_a_share_list: None contract + stale-if-error ───────────────
#
# ``_fetch_with_retry`` documents "Returns None on failure instead of
# raising" (data_fetcher.py:230). The caller used to do ``df.columns = [...]``
# unconditionally, so one upstream outage turned into
# ``AttributeError: 'NoneType' object has no attribute 'columns'`` — which
# took out /ashare/overview (40 s per request, then 500) and the snapshot
# universe seed on 2026-10-21. These tests pin both halves of the fix.


def _fake_cache_path(mtime_ts: float):
    """Stand-in for ``_cache_path`` so cache age is controllable."""

    class _FakePath:
        def stat(self):
            class _Stat:
                st_mtime = mtime_ts

            return _Stat()

    return lambda name, subdir="": _FakePath()


def test_get_a_share_list_fetch_fails_without_cache_returns_empty(monkeypatch, caplog):
    """No cache + upstream down → empty frame, not AttributeError."""
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": None)
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: None)

    with caplog.at_level("WARNING", logger="selene_space_ashare.data_fetcher"):
        df = data_fetcher.get_a_share_list()

    assert df.empty
    assert list(df.columns) == ["code", "name"]
    assert "universe fetch failed" in caplog.text


def test_get_a_share_list_expired_cache_fetch_fails_serves_stale(monkeypatch, caplog):
    """Expired cache + upstream down → serve the stale frame, log it."""
    stale = pd.DataFrame(
        {"code": ["600519", "000001"], "name": ["贵州茅台", "平安银行"]}
    )
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": stale)
    monkeypatch.setattr(
        data_fetcher,
        "_cache_path",
        _fake_cache_path((datetime.now() - timedelta(days=30)).timestamp()),
    )
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: None)

    with caplog.at_level("WARNING", logger="selene_space_ashare.data_fetcher"):
        df = data_fetcher.get_a_share_list()

    assert "serving stale data" in caplog.text
    assert list(df["code"]) == ["600519", "000001"]


def test_get_a_share_list_expired_cache_fetch_succeeds_refreshes(
    monkeypatch,
):
    """Expired cache + upstream up → refetch, normalise, and write back."""
    stale = pd.DataFrame({"code": ["600519"], "name": ["贵州茅台"]})
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": stale)
    monkeypatch.setattr(
        data_fetcher,
        "_cache_path",
        _fake_cache_path((datetime.now() - timedelta(days=30)).timestamp()),
    )
    raw = pd.DataFrame(
        {"code": ["600519", "000001", "N12345"], "name": ["茅台", "平安", "新股"]}
    )
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: raw)

    writes: list[pd.DataFrame] = []
    monkeypatch.setattr(
        data_fetcher, "_write_cache", lambda df, name, subdir="": writes.append(df)
    )

    df = data_fetcher.get_a_share_list()

    assert list(df["code"]) == ["600519", "000001"]  # N-prefixed filtered out
    assert len(writes) == 1


def test_get_a_share_list_fresh_cache_skips_fetch(monkeypatch):
    """Fresh cache short-circuits before any network call."""
    cached = pd.DataFrame({"code": ["600519"], "name": ["贵州茅台"]})
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": cached)
    monkeypatch.setattr(
        data_fetcher,
        "_cache_path",
        _fake_cache_path((datetime.now() - timedelta(days=1)).timestamp()),
    )

    def _fail(fn, **kw):
        raise AssertionError("fetch must not run on a fresh cache")

    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", _fail)
    assert list(data_fetcher.get_a_share_list()["code"]) == ["600519"]


def test_get_a_share_list_failure_opens_cooldown(monkeypatch, caplog):
    """A failed refresh must not be re-paid per request.

    Without a cooldown every caller (overview's universe size, market_daily's
    name join, the snapshot seed loop) pays the full 3-attempt retry budget —
    measured 94 s per overview request on 2026-10-21. Once a refresh has
    failed, serve the stale cache immediately until the window expires.
    """
    monkeypatch.setattr(data_fetcher, "_universe_cooldown_until", 0.0)
    stale = pd.DataFrame({"code": ["600519"], "name": ["贵州茅台"]})
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": stale)
    monkeypatch.setattr(
        data_fetcher,
        "_cache_path",
        _fake_cache_path((datetime.now() - timedelta(days=30)).timestamp()),
    )

    attempts = {"n": 0}

    def _failing(fn, **kw):
        attempts["n"] += 1
        return None

    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", _failing)

    with caplog.at_level("WARNING", logger="selene_space_ashare.data_fetcher"):
        first = data_fetcher.get_a_share_list()
        second = data_fetcher.get_a_share_list()

    assert attempts["n"] == 1, "the second call must not hit the network"
    assert list(first["code"]) == ["600519"]
    assert list(second["code"]) == ["600519"]
    assert "refresh held off" in caplog.text


def test_get_a_share_list_success_clears_cooldown(monkeypatch):
    """A successful refresh re-arms the network path."""
    monkeypatch.setattr(data_fetcher, "_universe_cooldown_until", 0.0)
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": None)
    raw = pd.DataFrame({"code": ["600519"], "name": ["茅台"]})
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: raw)
    monkeypatch.setattr(data_fetcher, "_write_cache", lambda *a, **k: None)

    data_fetcher.get_a_share_list()
    assert data_fetcher._universe_cooldown_until == 0.0


def test_get_a_share_list_force_bypasses_cooldown(monkeypatch):
    """``force=True`` is the explicit "try again now" escape hatch."""
    monkeypatch.setattr("time.monotonic", lambda: 1_000_000.0)
    monkeypatch.setattr(data_fetcher, "_universe_cooldown_until", 1_000_500.0)
    stale = pd.DataFrame({"code": ["600519"], "name": ["贵州茅台"]})
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": stale)
    calls = {"n": 0}
    monkeypatch.setattr(
        data_fetcher,
        "_fetch_with_retry",
        lambda fn, **kw: (calls.__setitem__("n", calls["n"] + 1), pd.DataFrame())[1],
    )
    data_fetcher.get_a_share_list(force=True)
    assert calls["n"] == 1, "force must bypass the cooldown"


# ─── get_hs300_constituents ─────────────────────────────────────────


def test_get_hs300_constituents_fetches_and_caches(monkeypatch):
    import akshare as ak

    raw = pd.DataFrame(
        {
            "成分券代码": ["000001", "000002", "600519", "N12345"],
            "成分券名称": ["平安银行", "万科A", "贵州茅台", "新股"],
        }
    )
    for i in range(300):
        raw.loc[len(raw)] = [f"{100000 + i}", f"股{i}"]
    monkeypatch.setattr(ak, "index_stock_cons_csindex", lambda symbol: raw)
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: fn(**kw))

    write_calls: list[tuple] = []
    monkeypatch.setattr(
        data_fetcher,
        "_write_cache",
        lambda df, name, subdir="": write_calls.append((name, subdir)),
    )
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": None)

    df = data_fetcher.get_hs300_constituents()
    assert list(df.columns) == ["code", "name"]
    assert df["code"].astype(str).str.match(r"^\d{6}$").all()
    assert "N12345" not in set(df["code"])
    assert len(df) == 303
    assert write_calls == [("hs300", "universe")]


def test_get_hs300_constituents_cache_hit(monkeypatch):
    import akshare as ak

    cached = pd.DataFrame(
        {"code": ["600519", "000001"], "name": ["贵州茅台", "平安银行"]}
    )
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": cached)

    class _FakePath:
        def stat(self):
            class _Stat:
                st_mtime = datetime.now().timestamp()

            return _Stat()

    monkeypatch.setattr(
        data_fetcher, "_cache_path", lambda name, subdir="": _FakePath()
    )

    def _fail(*args, **kwargs):
        raise AssertionError("akshare should not be called on cache hit")

    monkeypatch.setattr(ak, "index_stock_cons_csindex", _fail)
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: _fail(**kw))

    df = data_fetcher.get_hs300_constituents()
    assert list(df["code"]) == ["600519", "000001"]


# ─── get_index_daily freshness ──────────────────────────────────────


def test_get_index_daily_stale_cache_refetch(monkeypatch):
    import akshare as ak

    stale = pd.DataFrame({"date": ["20260720", "20260721"], "close": [1.0, 2.0]})
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": stale)

    writes: list[pd.DataFrame] = []
    monkeypatch.setattr(
        data_fetcher, "_write_cache", lambda df, name, subdir="": writes.append(df)
    )

    monkeypatch.setattr(
        ak,
        "index_zh_a_hist",
        lambda symbol, period: pd.DataFrame({"日期": ["2026-09-23"], "收盘": [3.0]}),
    )
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: fn(**kw))

    df = data_fetcher.get_index_daily("000300")
    assert "20260923" in list(df["date"])
    assert len(writes) == 1


def test_get_index_daily_stale_refetch_failed_serves_stale_with_warning(
    monkeypatch, caplog
):
    stale = pd.DataFrame({"date": ["20260720", "20260721"], "close": [1.0, 2.0]})
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": stale)
    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: None)

    with caplog.at_level("WARNING", logger="selene_space_ashare.data_fetcher"):
        df = data_fetcher.get_index_daily("000300")
    assert "serving stale data" in caplog.text
    assert list(df["date"]) == ["20260720", "20260721"]


def test_get_index_daily_fresh_cache_no_refetch(monkeypatch):
    import akshare as ak

    recent = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    cached = pd.DataFrame({"date": [recent], "close": [1.0]})
    monkeypatch.setattr(data_fetcher, "_read_cache", lambda name, subdir="": cached)

    def _fail(**kw):
        raise AssertionError("fetch should not be called on fresh cache")

    monkeypatch.setattr(data_fetcher, "_fetch_with_retry", lambda fn, **kw: _fail(**kw))
    monkeypatch.setattr(ak, "index_zh_a_hist", _fail)

    df = data_fetcher.get_index_daily("000300")
    assert list(df["date"]) == [recent]


# ─── D1: lockup / industry comparison / LHB seat detail ────────────


class TestLockupRelease:
    def test_returns_normalized_rows(self, monkeypatch):
        rows = [
            {
                "FREE_DATE": "2025-03-15 00:00:00",
                "LIMITED_STOCK_TYPE": "首发原股东限售股份",
                "FREE_SHARES_NUM": "120000000",
                "FREE_RATIO": "25.31%",
            },
            {
                "FREE_DATE": "2026-03-16 00:00:00",
                "LIMITED_STOCK_TYPE": "定增股份",
                "FREE_SHARES_NUM": 5000000,
                "FREE_RATIO": "1.2%",
            },
        ]
        monkeypatch.setattr(data_fetcher, "_em_datacenter_get", lambda p: rows)
        df = data_fetcher.get_lockup_release("600519")
        assert list(df.columns) == [
            "symbol",
            "free_date",
            "shares_type",
            "free_shares",
            "free_ratio",
        ]
        assert len(df) == 2
        assert df.iloc[0]["free_date"] == "2025-03-15"
        assert df.iloc[0]["symbol"] == "600519"
        assert df.iloc[0]["free_ratio"] == pytest.approx(25.31)
        assert df.iloc[1]["free_shares"] == 5000000

    def test_date_filter(self, monkeypatch):
        rows = [
            {"FREE_DATE": "2025-03-15", "FREE_SHARES_NUM": "1", "FREE_RATIO": "1%"},
            {"FREE_DATE": "2026-03-16", "FREE_SHARES_NUM": "2", "FREE_RATIO": "2%"},
        ]
        monkeypatch.setattr(data_fetcher, "_em_datacenter_get", lambda p: rows)
        df = data_fetcher.get_lockup_release("600519", start="20260101")
        assert len(df) == 1
        assert df.iloc[0]["free_date"] == "2026-03-16"

    def test_empty_and_error_paths(self, monkeypatch):
        monkeypatch.setattr(data_fetcher, "_em_datacenter_get", lambda p: None)
        assert data_fetcher.get_lockup_release("600519").empty
        assert data_fetcher.get_lockup_release("").empty

    def test_throttle_called(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            data_fetcher, "_em_datacenter_get", lambda p: calls.append(p) or []
        )
        data_fetcher.get_lockup_release("600519")
        assert len(calls) == 1  # datacenter path itself is throttled inside


class TestIndustryComparison:
    def test_parses_clist_rows(self, monkeypatch):
        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "data": {
                        "diff": {
                            "0": {
                                "f12": "BK0475",
                                "f14": "银行",
                                "f3": 1.23,
                                "f104": 30,
                                "f105": 2,
                                "f140": "工商银行",
                            },
                            "1": {
                                "f12": "BK1036",
                                "f14": "白酒",
                                "f3": -0.5,
                                "f104": 5,
                                "f105": 25,
                                "f140": "贵州茅台",
                            },
                        }
                    }
                }

        def fake_get(url, **kw):
            data_fetcher._em_throttle()  # mirror real call order
            return FakeResp()

        throttle_calls = []
        monkeypatch.setattr(
            data_fetcher, "_em_throttle", lambda: throttle_calls.append(1)
        )
        monkeypatch.setattr("httpx.get", fake_get)
        df = data_fetcher.get_industry_comparison(top_n=2)
        assert list(df.columns) == [
            "rank",
            "industry",
            "change_pct",
            "up_count",
            "down_count",
            "leader",
        ]
        assert len(df) == 2
        assert df.iloc[0]["rank"] == 1
        assert df.iloc[0]["industry"] == "银行"
        assert throttle_calls, "_em_throttle must be called before push2 request"

    def test_failure_returns_empty(self, monkeypatch):
        def boom(url, **kw):
            raise ConnectionError("waf")

        monkeypatch.setattr(data_fetcher, "_em_throttle", lambda: None)
        monkeypatch.setattr("httpx.get", boom)
        df = data_fetcher.get_industry_comparison()
        assert df.empty
        assert list(df.columns) == [
            "rank",
            "industry",
            "change_pct",
            "up_count",
            "down_count",
            "leader",
        ]


class TestLhbDetail:
    def test_board_rows_and_seats(self, monkeypatch):
        payloads = {
            "RPT_DAILYBILLBOARD_DETAILSNEW": [
                {
                    "TRADE_DATE": "2026-09-25 00:00:00",
                    "EXPLANATION": "日涨幅偏离值达7%",
                    "BILLBOARD_BUY_AMT": 123456789,
                    "BILLBOARD_NET_AMT": 50000000,
                },
            ],
            "RPT_BILLBOARD_DAILYDETAILSBUY": [
                {
                    "OPERATEDEPT_CODE": "0",
                    "OPERATEDEPT_NAME": "机构专用",
                    "BUY": 80000000,
                    "NET": 40000000,
                },
                {
                    "OPERATEDEPT_CODE": "101",
                    "OPERATEDEPT_NAME": "某游资营业部",
                    "BUY": 20000000,
                    "NET": 10000000,
                },
            ],
            "RPT_BILLBOARD_DAILYDETAILSSELL": [
                {
                    "OPERATEDEPT_CODE": "0",
                    "OPERATEDEPT_NAME": "机构专用",
                    "SELL": 30000000,
                    "NET": -20000000,
                },
            ],
        }
        monkeypatch.setattr(
            data_fetcher,
            "_em_datacenter_get",
            lambda p: payloads.get(p.get("reportName"), []),
        )
        df = data_fetcher.get_lhb_detail("600519")
        assert list(df.columns) == [
            "date",
            "reason",
            "seat",
            "seat_type",
            "amount",
            "net_amount",
            "is_institution",
        ]
        board = df[df["seat_type"] == "board"]
        buys = df[df["seat_type"] == "buy"]
        sells = df[df["seat_type"] == "sell"]
        assert len(board) == 1 and len(buys) == 2 and len(sells) == 1
        assert buys["is_institution"].sum() == 1  # OPERATEDEPT_CODE == "0"
        assert sells.iloc[0]["is_institution"]
        assert board.iloc[0]["net_amount"] == 50000000

    def test_no_board_rows_returns_empty(self, monkeypatch):
        monkeypatch.setattr(data_fetcher, "_em_datacenter_get", lambda p: [])
        assert data_fetcher.get_lhb_detail("600519").empty
        assert data_fetcher.get_lhb_detail("").empty


# ─── get_daily_klines: start_date is a promise ───────────────────────
#
# Regression cover for the range_type="last" ~140-bar tail. The full-history
# fallback used to be gated on `start_date and end_date`; every product path
# that omits end_date (backtest, /factors/{symbol}, factor report default)
# therefore received a window that began *after* its own requested start —
# measured live: start_date=20260305 returned 140 rows from 20260325. Two of
# the products then fed that tail to 252-bar factors.


def _bars(start: str, n: int) -> pd.DataFrame:
    dates = pd.bdate_range(start=pd.Timestamp(start), periods=n)
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y%m%d"),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1,
            "amount": 1.0,
            "turnover": 1.0,
        }
    )


@pytest.fixture()
def ths_stub(monkeypatch):
    """Stub ths_data so get_daily_klines runs offline and records calls."""
    import sys
    import types

    calls: dict[str, object] = {"all": 0, "last": 0}
    # Faithful to the measured real shape (600519_full: 1161 rows,
    # 20220104..20261022): the "last" range is simply the tail of full
    # history, and that tail starts *after* the product paths' start_date.
    full_frame = _bars("2022-01-04", 1161)
    last_frame = full_frame.tail(140).reset_index(drop=True)

    def fetch_last(code, period="daily", range_type="last", force=False):
        calls["last"] += 1
        return last_frame.copy()

    def fetch_all(code, period="daily", force=False):
        calls["all"] += 1
        return full_frame.copy()

    # data_fetcher imports ths_data lazily inside the function, so patching
    # the module in sys.modules is what takes effect.
    module = types.SimpleNamespace(
        fetch_klines_ths=fetch_last, fetch_klines_ths_all=fetch_all
    )
    # ``monkeypatch.setitem`` restores the real module afterwards. Popping it
    # instead leaves the parent package holding the stub, and the next plain
    # ``import`` builds a *second* ths_data whose LimitUpDataUnavailable is a
    # different class object — which silently breaks pytest.raises for every
    # later test module that imports the exception (observed in test_ladder_live).
    monkeypatch.setitem(sys.modules, "selene_space_ashare.ths_data", module)
    return calls


def test_get_daily_klines_start_only_reaches_back(ths_stub):
    """start_date alone must pull full history — no end_date required.

    Fails on the old gate: `last` (~140 bars, beginning 20260325) was
    returned verbatim, after the requested start of 20260305.
    """
    df = data_fetcher.get_daily_klines("600519", start_date="20260305")
    assert ths_stub["all"] == 1, "full-history fallback was never consulted"
    # the tail contributes 0 rows to this window; old behaviour returned it
    # anyway, beginning 20 days late and never reaching the requested start
    assert df["date"].min() == "20260305"
    assert len(df) > 0


def test_get_daily_klines_prefers_in_window_history_over_bare_length(ths_stub):
    """A short full-history slice inside the window beats a long tail of it.

    Regression for comparing the full frame against an *untrimmed* tail: the
    22-row January slice is shorter than the 140-row tail, but the tail
    contributes 0 rows to a January request and the slice contributes all 22.
    """
    df = data_fetcher.get_daily_klines(
        "600519", start_date="20240102", end_date="20240131"
    )
    assert ths_stub["all"] == 1
    assert len(df) > 0
    assert df["date"].min() >= "20240102"
    assert df["date"].max() <= "20240131"


def test_get_daily_klines_trims_to_requested_start(ths_stub):
    """Returned frame never begins before start_date."""
    df = data_fetcher.get_daily_klines("600519", start_date="20250101")
    assert df["date"].min() >= "20250101"
    assert df["date"].max() <= "20261022"


def test_get_daily_klines_respects_both_bounds(ths_stub):
    df = data_fetcher.get_daily_klines(
        "600519", start_date="20240101", end_date="20260131"
    )
    assert df["date"].min() >= "20240101"
    assert df["date"].max() <= "20260131"


def test_get_daily_klines_empty_start_returns_tail(ths_stub):
    """No start bound → the cheap tail is correct; no full-history fetch."""
    df = data_fetcher.get_daily_klines("600519")
    assert not df.empty
    assert ths_stub["all"] == 0
