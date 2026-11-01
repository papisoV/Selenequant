"""Offline tests for selene_space_stock.taiwan_finmind — mocks urllib.request.

No network: a fake ``urllib.request.urlopen`` returns fixed FinMind JSON
fixtures keyed by dataset name, so the whole request→parse→compute stack is
exercised against canned payloads.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

import selene_space_stock.taiwan_finmind as fin


# ---------------------------------------------------------------------------
# Fake urllib.request.urlopen
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._bytes


def _dataset_from_url(url):
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return qs.get("dataset", [""])[0]


class FakeFinMindClient:
    """dataset -> payload dict (or Exception to raise). Records every URL."""

    def __init__(self, fixtures):
        self.fixtures = fixtures
        self.urls = []

    def __call__(self, url, timeout=None):
        if isinstance(url, urllib.request.Request):
            url = url.full_url
        self.urls.append(url)
        value = self.fixtures.get(_dataset_from_url(url))
        if isinstance(value, Exception):
            raise value
        if value is None:
            payload = {"status": 200, "data": []}
        elif isinstance(value, dict) and "status" in value:
            payload = value
        else:
            payload = {"status": 200, "data": value}
        return FakeResponse(payload)


@pytest.fixture
def client(monkeypatch):
    fake = FakeFinMindClient({})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setenv("FINMIND_TOKEN", "test-token")
    return fake


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

INFO_TSMC = [
    {"stock_id": "2330", "stock_name": "台積電", "type": "twse", "industry_category": "半導體"}
]


def _price_rows():
    return [
        {
            "date": "2026-08-10",
            "open": 900.0,
            "max": 920.0,
            "min": 895.0,
            "close": 910.0,
            "Trading_Volume": 20_000_000,
            "Trading_money": 18_000_000_000,
        },
        {
            "date": "2026-08-11",
            "open": 915.0,
            "max": 940.0,
            "min": 910.0,
            "close": 930.0,
            "Trading_Volume": 25_000_000,
            "Trading_money": 23_000_000_000,
        },
    ]


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------


def test_quote_parses_snapshot_and_market_cap(client):
    client.fixtures.update(
        {
            "TaiwanStockInfo": INFO_TSMC,
            "TaiwanStockPrice": _price_rows(),
            "TaiwanStockPER": [
                {"date": "2026-08-11", "PER": 25.0, "PBR": 7.0, "dividend_yield": 1.5}
            ],
            "TaiwanStockShareholding": [
                {"date": "2026-08-01", "NumberOfSharesIssued": 25_903_000}
            ],
        }
    )
    result = fin.quote("2330")
    assert result["name"] == "台積電"
    assert result["board"] == "上市"
    assert result["currency"] == "TWD"
    assert result["date"] == "2026-08-11"
    assert result["close"] == 930.0
    assert result["open"] == 915.0
    assert result["max"] == 940.0
    assert result["min"] == 910.0
    assert result["change"] == 20.0
    assert result["change_pct"] == pytest.approx(20.0 / 910.0 * 100)
    assert result["trading_volume"] == 25_000_000
    assert result["trading_money"] == 23_000_000_000
    assert result["per"] == 25.0
    assert result["pbr"] == 7.0
    assert result["dividend_yield"] == 1.5
    assert result["shares_issued"] == 25_903_000
    assert result["market_cap"] == pytest.approx(930.0 * 25_903_000)


def test_quote_skips_valuation_when_per_empty(client):
    client.fixtures.update(
        {
            "TaiwanStockInfo": INFO_TSMC,
            "TaiwanStockPrice": _price_rows(),
            "TaiwanStockPER": [],
            "TaiwanStockShareholding": [],
        }
    )
    result = fin.quote("2330")
    assert result["per"] is None
    assert result["pbr"] is None
    assert result["dividend_yield"] is None
    assert result["shares_issued"] is None
    assert result["market_cap"] is None


def test_quote_sends_token_in_query(client):
    client.fixtures.update(
        {
            "TaiwanStockInfo": INFO_TSMC,
            "TaiwanStockPrice": _price_rows(),
            "TaiwanStockPER": [],
            "TaiwanStockShareholding": [],
        }
    )
    fin.quote("2330")
    assert any("token=test-token" in url for url in client.urls)


# ---------------------------------------------------------------------------
# valuation
# ---------------------------------------------------------------------------


def test_valuation_52w_and_per_range(client):
    prices = [
        {"date": "2025-08-01", "open": 500.0, "max": 520.0, "min": 480.0, "close": 510.0},
        {"date": "2025-09-01", "open": 600.0, "max": 700.0, "min": 590.0, "close": 690.0},
        {"date": "2026-08-11", "open": 900.0, "max": 950.0, "min": 880.0, "close": 930.0},
    ]
    pers = [
        {"date": "2025-08-01", "PER": 20.0, "PBR": 6.0, "dividend_yield": 2.0},
        {"date": "2026-08-11", "PER": 30.0, "PBR": 7.0, "dividend_yield": 1.5},
    ]
    client.fixtures.update(
        {
            "TaiwanStockInfo": INFO_TSMC,
            "TaiwanStockPrice": prices,
            "TaiwanStockPER": pers,
            "TaiwanStockShareholding": [],
        }
    )
    result = fin.valuation("2330")
    assert result["high_52w"] == 950.0
    assert result["low_52w"] == 480.0
    assert result["per"] == 30.0
    assert result["per_year_range"] == [20.0, 30.0]
    assert result["pbr"] == 7.0
    assert result["dividend_yield"] == 1.5
    assert result["market_cap"] is None


# ---------------------------------------------------------------------------
# kline
# ---------------------------------------------------------------------------


def test_kline_maps_finmind_rows_to_ohlcv(client):
    client.fixtures.update({"TaiwanStockPrice": _price_rows()})
    bars = fin.kline("2330")
    assert len(bars) == 2
    assert bars[0] == {
        "date": "2026-08-10",
        "open": 900.0,
        "high": 920.0,  # max -> high
        "low": 895.0,  # min -> low
        "close": 910.0,
        "volume": 20_000_000,  # Trading_Volume -> volume
    }
    assert bars[1]["high"] == 940.0
    assert bars[1]["volume"] == 25_000_000


def test_kline_defaults_to_days_ago(client):
    client.fixtures.update({"TaiwanStockPrice": _price_rows()})
    fin.kline("2330")
    assert any("dataset=TaiwanStockPrice" in url and "start_date=" in url for url in client.urls)


def test_kline_passes_start_end(client):
    client.fixtures.update({"TaiwanStockPrice": _price_rows()})
    fin.kline("2330", start_date="2026-01-01", end_date="2026-03-01")
    url = client.urls[0]
    assert "start_date=2026-01-01" in url
    assert "end_date=2026-03-01" in url


def test_kline_empty_raises(client):
    client.fixtures.update({"TaiwanStockPrice": []})
    with pytest.raises(fin.FinMindEmptyError):
        fin.kline("2330")


def test_kline_missing_token_raises(monkeypatch):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    with pytest.raises(fin.FinMindTokenError):
        fin.kline("2330")


# ---------------------------------------------------------------------------
# financials
# ---------------------------------------------------------------------------


def test_financials_sums_quarters_and_roe(client):
    rows = []
    q_values = [(1000, 100, 1.0), (1100, 110, 1.1), (1200, 120, 1.2), (1300, 130, 1.3)]
    for i, (rev, ni, eps) in enumerate(q_values, start=1):
        for typ, val in [
            ("Revenue", rev),
            ("EquityAttributableToOwnersOfParent", ni),
            ("EPS", eps),
        ]:
            rows.append({"date": f"2024-0{i}-01", "type": typ, "value": val})
    bs = [{"date": "2024-12-31", "type": "EquityAttributableToOwnersOfParent", "value": 2000}]
    client.fixtures.update(
        {
            "TaiwanStockInfo": INFO_TSMC,
            "TaiwanStockFinancialStatements": rows,
            "TaiwanStockBalanceSheet": bs,
        }
    )
    result = fin.financials("2330")
    year = result["years"][0]
    assert year["year"] == "2024"
    assert year["full_year"] is True
    assert year["quarters_count"] == 4
    assert year["revenue"] == 4600
    assert year["net_income"] == 460
    assert year["net_margin_pct"] == pytest.approx(460 / 4600 * 100)
    assert year["eps"] == pytest.approx(4.6)
    assert year["equity"] == 2000
    assert year["roe_pct"] == pytest.approx(460 / 2000 * 100)
    assert year["gross_margin_pct"] is None
    assert year["operating_margin_pct"] is None


def test_financials_marks_partial_year_without_roe(client):
    rows = [
        {"date": "2025-01-01", "type": "Revenue", "value": 1000},
        {"date": "2025-02-01", "type": "Revenue", "value": 1100},
    ]
    client.fixtures.update(
        {
            "TaiwanStockInfo": INFO_TSMC,
            "TaiwanStockFinancialStatements": rows,
            "TaiwanStockBalanceSheet": [{"date": "2025-12-31", "type": "EquityAttributableToOwnersOfParent", "value": 5000}],
        }
    )
    result = fin.financials("2330")
    year = result["years"][0]
    assert year["full_year"] is False
    assert year["quarters_count"] == 2
    assert year["revenue"] == 2100
    assert year["roe_pct"] is None  # partial year → no simplified ROE


# ---------------------------------------------------------------------------
# revenue
# ---------------------------------------------------------------------------


def test_revenue_yoy(client):
    rows = [
        {"revenue_year": 2024, "revenue_month": 7, "revenue": 1000},
        {"revenue_year": 2025, "revenue_month": 6, "revenue": 1500},
        {"revenue_year": 2025, "revenue_month": 7, "revenue": 1200},
    ]
    client.fixtures.update(
        {
            "TaiwanStockInfo": INFO_TSMC,
            "TaiwanStockMonthRevenue": rows,
        }
    )
    result = fin.revenue("2330")
    assert len(result["months"]) == 3
    assert result["months"][-1]["year"] == 2025
    assert result["months"][-1]["month"] == 7
    assert result["months"][-1]["revenue"] == 1200
    assert result["months"][-1]["yoy_pct"] == pytest.approx(20.0)
    assert result["months"][-2]["yoy_pct"] is None  # no same-month prior-year
    assert result["months"][-3]["yoy_pct"] is None


# ---------------------------------------------------------------------------
# dividend
# ---------------------------------------------------------------------------


def test_dividend_sums_cash_and_stock(client):
    rows = [
        {
            "year": "2024",
            "CashEarningsDistribution": 3.0,
            "CashStatutorySurplus": 1.0,
            "StockEarningsDistribution": 0.5,
            "StockStatutorySurplus": 0.0,
            "CashExDividendTradingDate": "2024-07-01",
            "CashDividendPaymentDate": "2024-07-31",
        },
        {"year": "2023", "CashEarningsDistribution": 0.0, "StockEarningsDistribution": 0.0},
    ]
    client.fixtures.update({"TaiwanStockInfo": INFO_TSMC, "TaiwanStockDividend": rows})
    result = fin.dividend("2330")
    assert len(result["dividends"]) == 1
    d = result["dividends"][0]
    assert d["year"] == "2024"
    # per-row sum of the two cash components (source behavior: one entry per payout event)
    assert d["cash"] == pytest.approx(4.0)
    assert d["stock"] == pytest.approx(0.5)
    assert d["cash_ex_date"] == "2024-07-01"
    assert d["cash_payment_date"] == "2024-07-31"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def test_search_by_name_aggregates_industries(client):
    client.fixtures["TaiwanStockInfo"] = [
        {"stock_id": "2330", "stock_name": "台積電", "type": "twse", "industry_category": "半導體"},
        {"stock_id": "2317", "stock_name": "鴻海", "type": "twse", "industry_category": "其他電子"},
        {"stock_id": "2330", "stock_name": "台積電", "type": "twse", "industry_category": "晶圓代工"},
    ]
    results = fin.search("台積")
    assert len(results) == 1
    r = results[0]
    assert r["stock_id"] == "2330"
    assert r["name"] == "台積電"
    assert r["board"] == "上市"
    assert r["industries"] == ["半導體", "晶圓代工"]


def test_search_by_code(client):
    client.fixtures["TaiwanStockInfo"] = [
        {"stock_id": "2330", "stock_name": "台積電", "type": "twse", "industry_category": "半導體"},
    ]
    results = fin.search("2330")
    assert len(results) == 1
    assert results[0]["name"] == "台積電"


def test_search_no_match_returns_empty(client):
    client.fixtures["TaiwanStockInfo"] = [
        {"stock_id": "2330", "stock_name": "台積電", "type": "twse", "industry_category": "半導體"},
    ]
    assert fin.search("不存在的公司") == []


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("FINMIND_TOKEN", raising=False)
    with pytest.raises(fin.FinMindTokenError):
        fin.quote("2330")


def test_rate_limit_raises(client):
    client.fixtures["TaiwanStockInfo"] = urllib.error.HTTPError(
        "https://api.finmindtrade.com/api/v4/data", 429, "Too Many Requests", {}, None
    )
    with pytest.raises(fin.FinMindRateLimitError):
        fin._get("TaiwanStockInfo")


def test_http_500_raises(client):
    client.fixtures["TaiwanStockInfo"] = urllib.error.HTTPError(
        "https://api.finmindtrade.com/api/v4/data", 500, "Server Error", {}, None
    )
    with pytest.raises(fin.FinMindError):
        fin._get("TaiwanStockInfo")


def test_non_200_status_raises(client):
    client.fixtures["TaiwanStockInfo"] = {"status": 400, "msg": "bad request"}
    with pytest.raises(fin.FinMindError):
        fin._get("TaiwanStockInfo")


def test_empty_price_raises(client):
    client.fixtures.update({"TaiwanStockInfo": INFO_TSMC, "TaiwanStockPrice": []})
    with pytest.raises(fin.FinMindEmptyError):
        fin.quote("2330")


# ---------------------------------------------------------------------------
# formatting helper
# ---------------------------------------------------------------------------


def test_format_twd():
    assert fin.format_twd(1.2e9) == "12.0亿"
    assert fin.format_twd(25_000) == "2.5万"
    assert fin.format_twd(1234.5) == "1,234.50"
    assert fin.format_twd(None) == "-"
    assert fin.format_twd("") == "-"
