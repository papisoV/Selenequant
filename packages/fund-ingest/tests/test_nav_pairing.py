"""nav_pairing 离线测试 — 净值配对与溢价回填。"""

from __future__ import annotations

import pandas as pd

from selene_space_fund_ingest.collector import compute_premium_pct
from selene_space_fund_ingest.nav_pairing import enrich_with_nav, fetch_latest_nav


class FakeAKNav:
    """symbol → DataFrame 的注入 fake。"""

    def __init__(self, frames: dict[str, pd.DataFrame | Exception]):
        self.frames = frames

    def fund_open_fund_info_em(self, symbol: str, indicator: str = "单位净值走势"):
        v = self.frames.get(symbol)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise RuntimeError(f"no frame for {symbol}")
        return v


def _nav_df(values: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"净值日期": d, "单位净值": n, "日增长率": 0.0, "_": ""} for d, n in values]
    )


def test_fetch_latest_nav_returns_last_row():
    ak = FakeAKNav({"501025": _nav_df([("2026-09-03", 0.94), ("2026-09-04", 0.951)])})
    nav, date = fetch_latest_nav(ak, "501025")
    assert nav == 0.951
    assert date == "2026-09-04"


def test_fetch_latest_nav_failure_returns_none_pair():
    ak = FakeAKNav({"501025": ConnectionError("dead endpoint")})
    assert fetch_latest_nav(ak, "501025") == (None, None)


def test_enrich_fills_premium_positive_and_negative():
    ak = FakeAKNav(
        {
            "501025": _nav_df([("2026-09-04", 1.00)]),  # price 0.953 → -4.7%
            "160323": _nav_df([("2026-09-04", 1.20)]),  # price 1.25 → +4.17%
        }
    )
    rows = [
        {
            "code": "501025",
            "price": 0.953,
            "nav": None,
            "nav_date": None,
            "premium_pct": None,
        },
        {
            "code": "160323",
            "price": 1.25,
            "nav": None,
            "nav_date": None,
            "premium_pct": None,
        },
    ]
    out = enrich_with_nav(ak, rows)
    by_code = {r["code"]: r for r in out}
    assert by_code["501025"]["premium_pct"] < 0  # 折价(负溢价)
    assert by_code["160323"]["premium_pct"] > 0  # 溢价(正)
    assert abs(by_code["160323"]["premium_pct"] - (1.25 - 1.20) / 1.20 * 100) < 1e-9


def test_enrich_leaves_none_when_nav_unavailable():
    ak = FakeAKNav({"501025": ConnectionError("dead")})
    rows = [
        {
            "code": "501025",
            "price": 0.953,
            "nav": None,
            "nav_date": None,
            "premium_pct": None,
        }
    ]
    out = enrich_with_nav(ak, rows)
    assert out[0]["nav"] is None
    assert out[0]["premium_pct"] is None


def test_premium_formula_matches_stock_form():
    # stock LOF_arbitrage.py:38-40: (price - netvalue)/netvalue*100
    import pytest

    assert compute_premium_pct(1.04, 1.0) == pytest.approx(4.0)
    assert compute_premium_pct(0.98, 1.0) == pytest.approx(-2.0)
