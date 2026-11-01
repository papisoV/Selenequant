"""fund-ingest 测试 — 全离线(akshare 接口注入 fake,零网络)。

复刻 news-ingest test_detector.py:150 monkeypatch 模式;网络面由
scripts 侧手动脚本覆盖(端点级波动,不进 pytest)。
"""

from __future__ import annotations

import pandas as pd
import pytest

from selene_space_fund_ingest import collector, store
from selene_space_fund_ingest.config import FUND_DB_PATH


class FakeAK:
    """注入用 fake akshare 模块。"""

    def __init__(self, **dfs: pd.DataFrame | Exception):
        self._dfs = dfs

    def __getattr__(self, name: str):
        v = self._dfs.get(name)
        if v is None:
            raise AttributeError(name)
        # 异常存 lambda 内:调用时抛(与真实 akshare 一致),让 collector 的重试+退化链生效
        if isinstance(v, Exception):
            return lambda: (_ for _ in ()).throw(v)
        return lambda: v


def _cb_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "债券代码": "110047",
                "债券简称": "山鹰转债",
                "正股价": 3.5,
                "转股价": 3.4,
                "转股价值": 102.9,
                "债现价": 116.02,
                "转股溢价率": 12.7,
            },
            {
                "债券代码": "127003",
                "债券简称": "海印转债",
                "正股价": 2.1,
                "转股价": 2.6,
                "转股价值": 80.8,
                "债现价": 108.055,
                "转股溢价率": 33.6,
            },
            {
                "债券代码": "",
                "债券简称": "空代码行应被跳过",
                "债现价": 100.0,
            },
        ]
    )


def _lof_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"代码": "501025", "名称": "港股通银行LOF", "最新价": 0.953},
            {"代码": "160323", "名称": "军工LOF", "最新价": 1.25},
        ]
    )


def _reits_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"代码": "508038", "名称": "临港REIT", "最新价": 4.32, "涨跌幅": 1.2},
            {"代码": "180101", "名称": "蛇口REIT", "最新价": 2.85, "涨跌幅": -0.4},
        ]
    )


# ---------------------------------------------------------------------------
# collector
# ---------------------------------------------------------------------------
def test_fetch_cb_bonds_maps_columns_and_skips_empty_code():
    rows = collector.fetch_cb_bonds(FakeAK(bond_zh_cov=_cb_df()))
    assert len(rows) == 2  # 空代码行被跳过
    first = rows[0]
    assert first["code"] == "110047"
    assert first["price"] == 116.02
    assert first["convert_price"] == 3.4
    assert first["premium_pct"] == 12.7
    assert first["updated_at"]


def test_fetch_cb_bonds_network_failure_degrades_to_empty():
    ak = FakeAK(bond_zh_cov=ConnectionError("SSL EOF"))
    assert collector.fetch_cb_bonds(ak) == []


def test_fetch_lof_etf_spot_two_categories():
    ak = FakeAK(fund_lof_spot_em=_lof_df(), fund_etf_spot_em=_lof_df())
    rows = collector.fetch_lof_etf_spot(ak)
    assert len(rows) == 4
    assert {r["category"] for r in rows} == {"LOF", "ETF"}
    assert rows[0]["price"] == 0.953
    assert rows[0]["nav"] is None  # spot 接口无净值列,待配对层补


def test_fetch_reits_spot_maps_columns():
    rows = collector.fetch_reits_spot(FakeAK(reits_realtime_em=_reits_df()))
    assert len(rows) == 2
    assert rows[0]["code"] == "508038"
    assert rows[1]["change_pct"] == -0.4


# ---------------------------------------------------------------------------
# compute_premium_pct(策略形态核心,正负号验证)
# ---------------------------------------------------------------------------
def test_premium_positive_when_price_above_nav():
    assert collector.compute_premium_pct(1.05, 1.0) == pytest.approx(5.0)


def test_premium_negative_when_price_below_nav():
    assert collector.compute_premium_pct(0.96, 1.0) == pytest.approx(-4.0)


def test_premium_none_on_bad_inputs():
    assert collector.compute_premium_pct(None, 1.0) is None
    assert collector.compute_premium_pct(1.0, None) is None
    assert collector.compute_premium_pct(1.0, 0.0) is None


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
def test_store_upsert_and_refetch_roundtrip(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    rows = collector.fetch_cb_bonds(FakeAK(bond_zh_cov=_cb_df()))
    assert store.upsert_rows(conn, "cb_bonds", rows) == 2
    back = store.fetch_all(conn, "cb_bonds")
    assert len(back) == 2
    assert back[0]["code"] in {"110047", "127003"}

    # 二次 upsert 修改值 → 覆盖不重复
    rows[0]["price"] = 120.0
    store.upsert_rows(conn, "cb_bonds", rows)
    back2 = store.fetch_all(conn, "cb_bonds")
    assert len(back2) == 2
    assert any(r["price"] == 120.0 for r in back2)


def test_store_empty_rows_clears_table(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.upsert_rows(
        conn, "cb_bonds", collector.fetch_cb_bonds(FakeAK(bond_zh_cov=_cb_df()))
    )
    assert len(store.fetch_all(conn, "cb_bonds")) == 2
    store.upsert_rows(conn, "cb_bonds", [])  # 采集失败退化 → 清表
    assert store.fetch_all(conn, "cb_bonds") == []


def test_config_db_path_default_under_data():
    assert FUND_DB_PATH.name == "fund_ingest.db"
    assert FUND_DB_PATH.parent.name == "data"
