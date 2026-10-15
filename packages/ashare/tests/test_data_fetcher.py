"""Tests for selene_space_ashare.data_fetcher money-flow / LHB functions.

Uses monkeypatch to stub akshare — no network access.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import pytest

from selene_space_ashare import data_fetcher


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
