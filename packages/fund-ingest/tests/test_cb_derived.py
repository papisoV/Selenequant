"""cb_derived 离线测试 — 双低排序 + 强赎回填。"""

from __future__ import annotations

import sqlite3

import pandas as pd

from selene_space_fund_ingest import cb_derived, store


class FakeAKRedeem:
    def __init__(self, df: pd.DataFrame | Exception):
        self._df = df

    def bond_cb_redeem_jsl(self):
        if isinstance(self._df, Exception):
            raise self._df
        return self._df


def _redeem_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "代码": "110047",
                "名称": "山鹰转债",
                "现价": 116.02,
                "转股价": 3.4,
                "正股价": 3.5,
                "强赎天计数": 5,
                "强赎状态": "已公告强赎",
                "最后交易日": "2026-09-20",
            },
            {
                "代码": "127003",
                "名称": "海印转债",
                "现价": 108.055,
                "转股价": 2.6,
                "正股价": 2.1,
                "强赎天计数": 0,
                "强赎状态": "",
                "最后交易日": "-",
            },
        ]
    )


def _cb_rows() -> list[dict]:
    return [
        {"code": "110047", "name": "山鹰转债", "price": 116.02, "premium_pct": 12.7},
        {"code": "127003", "name": "海印转债", "price": 108.055, "premium_pct": 33.6},
        {"code": "113050", "name": "低双低样本", "price": 100.0, "premium_pct": 5.0},
    ]


def test_double_low_formula():
    assert cb_derived.compute_double_low(116.02, 12.7) == 128.72
    assert cb_derived.compute_double_low(None, 5.0) is None
    assert cb_derived.compute_double_low(100.0, None) is None


def test_rank_by_double_low_orders_ascending_and_drops_incomplete():
    top = cb_derived.rank_by_double_low(_cb_rows(), top=10)
    # 113050 (105.0) < 127003 (141.655) < 110047 (128.72) → 升序: 113050,110047,127003
    assert [r["code"] for r in top] == ["113050", "110047", "127003"]
    top2 = cb_derived.rank_by_double_low(_cb_rows(), top=2)
    assert len(top2) == 2
    assert top2[0]["code"] == "113050"


def test_fetch_redeem_flags_maps_columns():
    rows = cb_derived.fetch_redeem_flags(FakeAKRedeem(_redeem_df()))
    assert len(rows) == 2
    first = rows[0]
    assert first["code"] == "110047"
    assert first["redeem_status"] == "已公告强赎"
    assert first["redeem_days"] == 5
    assert first["last_trade_date"] == "2026-09-20"


def test_fetch_redeem_flags_network_failure_degrades():
    assert cb_derived.fetch_redeem_flags(FakeAKRedeem(ConnectionError("dead"))) == []


def test_apply_redeem_backfills_and_defaults():
    cb = _cb_rows()
    redeem = cb_derived.fetch_redeem_flags(FakeAKRedeem(_redeem_df()))
    out = cb_derived.apply_redeem_to_cb(cb, redeem)
    by_code = {r["code"]: r for r in out}
    assert by_code["110047"]["redeem_status"] == "已公告强赎"
    assert by_code["110047"]["redeem_days"] == 5
    # 不在强赎表的行:默认空串/None
    assert by_code["113050"]["redeem_status"] == ""
    assert by_code["113050"]["redeem_days"] is None


def test_store_accepts_redeem_columns(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    cb = cb_derived.apply_redeem_to_cb(
        _cb_rows(), cb_derived.fetch_redeem_flags(FakeAKRedeem(_redeem_df()))
    )
    # upsert 用行内键集合,含强赎列
    n = store.upsert_rows(conn, "cb_bonds", cb)
    assert n == 3
    back = store.fetch_all(conn, "cb_bonds")
    by_code = {r["code"]: r for r in back}
    assert by_code["110047"]["redeem_status"] == "已公告强赎"


def test_store_migrates_old_cb_schema(tmp_path):
    """旧库(cb_bonds 无强赎列)→ connect 迁移加列,旧数据存活。"""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE cb_bonds (code TEXT PRIMARY KEY, name TEXT, price REAL,"
        " convert_price REAL, premium_pct REAL, redeem_trigger_price REAL, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO cb_bonds VALUES ('110047','山鹰转债',116.02,3.4,12.7,NULL,'2026-01-01')"
    )
    conn.commit()
    conn.close()

    conn2 = store.connect(path)
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(cb_bonds)")}
    assert {"redeem_status", "redeem_days", "last_trade_date"} <= cols
    row = store.fetch_all(conn2, "cb_bonds")[0]
    assert row["code"] == "110047"  # 迁移不丢旧数据
    assert row["redeem_status"] == ""  # 新列默认空
