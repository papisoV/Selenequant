# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Whole-market 龙虎榜 reader tests — no network.

Three properties carry weight:

* **``SECUCODE`` is the join key.** A row without it is dropped, not kept under
  a bare code — keeping it would introduce a second symbol vocabulary into a
  codebase whose entire symbol space is suffixed upper-case.
* **Paging follows ``result.pages``.** Day sessions routinely exceed one page;
  reading only page one truncates the board without any error.
* **A quiet day is ``"empty"``, not a failure.** Distinguishing the two is what
  lets the recap omit the block instead of claiming the data is unavailable.
"""

from __future__ import annotations

import httpx
import pytest

from selene_space_ashare import data_fetcher as df_mod
from selene_space_ashare import lhb_daily


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _page(rows, *, pages):
    return _Resp({"result": {"pages": pages, "data": rows}})


def _row(secucode, name="示例", net="1000000", reason="日涨幅偏离值达7%", code=None):
    return {
        "SECUCODE": secucode,
        "SECURITY_CODE": code or (secucode or "").split(".")[0],
        "SECURITY_NAME_ABBR": name,
        "TRADE_DATE": "2026-10-16 00:00:00",
        "EXPLANATION": reason,
        "BILLBOARD_BUY_AMT": "3000000",
        "BILLBOARD_SELL_AMT": "2000000",
        "BILLBOARD_NET_AMT": net,
        "BILLBOARD_DEAL_AMT": "5000000",
        "ACCUM_AMOUNT": "100000000",
        "DEAL_NET_RATIO": "1.0",
        "DEAL_AMOUNT_RATIO": "5.0",
        "TURNOVERRATE": "3.2",
        "CLOSE_PRICE": "10.5",
        "CHANGE_RATE": "7.1",
    }


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(df_mod, "_em_throttle", lambda: None)


def _install(monkeypatch, responses):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs.get("params", {}))
        return responses.pop(0)

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_follows_pages_and_dedupes_by_symbol_and_reason(monkeypatch):
    calls = _install(
        monkeypatch,
        [
            _page([_row("603259.SH", reason="涨幅偏离")], pages=2),
            _page(
                [
                    _row("603259.SH", reason="涨幅偏离"),  # duplicate on page 2
                    _row("000001.SZ", "平安银行", reason="换手率达20%"),
                ],
                pages=2,
            ),
        ],
    )

    rows, error = lhb_daily.fetch_lhb_day("20261016")

    assert error is None
    assert [r["symbol"] for r in rows] == ["603259.SH", "000001.SZ"]
    assert len(calls) == 2
    assert calls[0]["pageNumber"] == "1"
    assert calls[1]["pageNumber"] == "2"
    # The filter is the whole point of this reader: one session, not one symbol.
    assert calls[0]["filter"] == "(TRADE_DATE='2026-10-16')"


def test_row_without_secucode_is_dropped(monkeypatch):
    _install(
        monkeypatch,
        [_page([_row(""), _row("600519.SH", "贵州茅台")], pages=1)],
    )

    rows, error = lhb_daily.fetch_lhb_day("2026-10-16")

    assert error is None
    # Dropping is honest; keeping it as "600519" would split the symbol space.
    assert [r["symbol"] for r in rows] == ["600519.SH"]


def test_quiet_day_is_empty_not_an_error(monkeypatch):
    _install(monkeypatch, [_page([], pages=0)])

    rows, error = lhb_daily.fetch_lhb_day("2026-10-04")

    assert rows == []
    assert error == "empty"


def test_fetch_failure_never_raises(monkeypatch):
    def boom(url, **kwargs):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", boom)

    rows, error = lhb_daily.fetch_lhb_day("2026-10-16")

    assert rows == []
    assert error and error.startswith("fetch_failed")


def test_bad_date_is_reported_without_a_request(monkeypatch):
    def must_not_call(url, **kwargs):
        raise AssertionError("no request for an empty date")

    monkeypatch.setattr(httpx, "get", must_not_call)
    rows, error = lhb_daily.fetch_lhb_day("   ")
    assert rows == []
    assert error == "bad_date"


def test_summary_does_not_double_count_a_name_under_two_reasons():
    rows = [
        {
            "symbol": "603259.SH",
            "name": "药明康德",
            "change_pct": 7.1,
            "net_amount": 1.0e8,
            "reason": "涨幅偏离",
        },
        {
            "symbol": "603259.SH",
            "name": "药明康德",
            "change_pct": 7.1,
            "net_amount": 2.0e8,
            "reason": "换手率达20%",
        },
        {
            "symbol": "000001.SZ",
            "name": "平安银行",
            "change_pct": -3.0,
            "net_amount": -5.0e7,
            "reason": "跌幅偏离",
        },
    ]

    summary = lhb_daily.summarize_lhb(rows)

    assert summary["count"] == 2  # names, not rows
    assert summary["net_buy"][0]["symbol"] == "603259.SH"
    assert summary["net_buy"][0]["net_amount"] == pytest.approx(3.0e8)
    assert summary["net_buy"][0]["reasons"] == ["涨幅偏离", "换手率达20%"]
    assert summary["net_sell"][0]["symbol"] == "000001.SZ"
    assert summary["total_net_amount"] == pytest.approx(2.5e8)
    assert summary["net_buy_count"] == 1


def test_recap_context_is_empty_string_when_there_is_no_data():
    # The prompt section is omitted entirely, so the model never sees
    # "龙虎榜: (暂无)" and never invents one.
    assert lhb_daily.build_recap_context([]) == ""


def test_recap_context_names_the_amounts():
    rows = [
        {
            "symbol": "603259.SH",
            "name": "药明康德",
            "change_pct": 7.1,
            "net_amount": 3.0e8,
            "reason": "涨幅偏离",
        },
    ]
    text = lhb_daily.build_recap_context(rows)
    assert "药明康德" in text and "+3.00亿" in text
    assert "上榜家数: 1" in text
