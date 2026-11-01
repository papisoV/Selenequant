"""US-1 sync.py 单测 — mock baostock, 不真连网."""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from selene_space_ashare_select.sync import (
    AshareSelectSync,
    to_baostock_code,
    _rows_to_df,
)

SAMPLE_ROWS = [
    ["2024-01-02", "10.0", "11.0", "9.5", "10.5", "1000000", "10500000"],
    ["2024-01-03", "10.5", "11.5", "10.2", "11.0", "1200000", "12800000"],
]


# 稳定的 fake: 直接实现 baostock 迭代协议
class _FakeResult:
    def __init__(self, rows, error_code="0", error_msg=""):
        self._rows = rows
        self._i = 0
        self.error_code = error_code
        self.error_msg = error_msg
        self.fields = ["date", "open", "high", "low", "close", "volume", "amount"]

    def next(self) -> bool:
        if self._i < len(self._rows):
            self._i += 1
            return True
        return False

    def get_row_data(self) -> list[str]:
        return list(self._rows[self._i - 1])


@pytest.fixture
def sync_obj(tmp_path):
    return AshareSelectSync(db_path=tmp_path / "test.db", start_date="20200101")


class TestSchema:
    def test_unique_symbol_date_constraint(self, tmp_path):
        s = AshareSelectSync(db_path=tmp_path / "t.db")
        with sqlite3.connect(s.db_path) as conn:
            conn.execute(
                "INSERT INTO stock_daily (symbol, date, close) VALUES ('600000', '2024-01-02', 10.0)"
            )
            conn.commit()
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO stock_daily (symbol, date, close) VALUES ('600000', '2024-01-02', 11.0)"
                )

    def test_db_created_in_nested_dir(self, tmp_path):
        AshareSelectSync(db_path=tmp_path / "a" / "b" / "t.db")
        assert (tmp_path / "a" / "b" / "t.db").exists()


class TestUpsert:
    def test_insert_or_replace_idempotent(self, sync_obj):
        df = _rows_to_df(SAMPLE_ROWS, "600000")
        with sqlite3.connect(sync_obj.db_path) as conn:
            n1 = sync_obj._upsert(conn, df)
            conn.commit()
            sync_obj._upsert(conn, df)  # 重放同数据
            conn.commit()
        assert n1 == 2
        with sqlite3.connect(sync_obj.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
        assert count == 2, "幂等: 重复 upsert 不产生重复行"

    def test_rows_to_df_filters(self):
        dirty = SAMPLE_ROWS + [["2024-01-04", "", "", "", "", "0", "0"]]
        df = _rows_to_df(dirty, "600000")
        assert len(df) == 2, "close NaN / volume=0 行被过滤"
        assert list(df.columns) == [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
        ]


class TestBaostockCode:
    @pytest.mark.parametrize(
        "symbol,expected",
        [
            ("600000", "sh.600000"),
            ("688001", "sh.688001"),
            ("900901", "sh.900901"),
            ("000001", "sz.000001"),
            ("300750", "sz.300750"),
            ("002415", "sz.002415"),
        ],
    )
    def test_code_mapping(self, symbol, expected):
        assert to_baostock_code(symbol) == expected


class TestSyncDaily:
    def test_sync_daily_writes_and_resume(self, sync_obj):
        session = MagicMock()
        session.login.return_value = True
        session.query_daily.return_value = SAMPLE_ROWS

        with patch(
            "selene_space_ashare_select.sync._BaostockSession", return_value=session
        ):
            n = sync_obj.sync_daily(["600000"], end="2024-01-03")
        assert n == 2
        assert session.logout.called

        # 续传: 已同步到 end, 再跑 start>end → 跳过
        session2 = MagicMock()
        session2.login.return_value = True
        with patch(
            "selene_space_ashare_select.sync._BaostockSession", return_value=session2
        ):
            n2 = sync_obj.sync_daily(["600000"], end="2024-01-03")
        assert n2 == 0
        session2.query_daily.assert_not_called()

    def test_sync_daily_login_fail_returns_zero(self, sync_obj):
        session = MagicMock()
        session.login.return_value = False
        with patch(
            "selene_space_ashare_select.sync._BaostockSession", return_value=session
        ):
            assert sync_obj.sync_daily(["600000"]) == 0


class TestBackfill:
    def test_backfill_retry_then_success(self, sync_obj):
        session = MagicMock()
        session.login.return_value = True
        calls = {"n": 0}

        def query_side_effect(*a, **kw):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("timeout")
            return SAMPLE_ROWS

        session.query_daily.side_effect = query_side_effect
        sleeps: list[float] = []

        with patch(
            "selene_space_ashare_select.sync._BaostockSession", return_value=session
        ):
            stats = sync_obj.backfill(
                ["600000"], start_date="2024-01-01", sleep_fn=sleeps.append
            )
        assert stats == {"success": 1, "skipped": 0, "failed": 0}
        assert sleeps == [2.0, 4.0], "重试间隔 2s/4s 递增"
        assert calls["n"] == 3

    def test_backfill_skip_current(self, sync_obj):
        # 先写入今天的数据
        today = pd.Timestamp.today().strftime("%Y-%m-%d")
        df = _rows_to_df([[today, "10", "11", "9", "10.5", "100", "105"]], "600000")
        with sqlite3.connect(sync_obj.db_path) as conn:
            sync_obj._upsert(conn, df)
            conn.commit()

        session = MagicMock()
        session.login.return_value = True
        with patch(
            "selene_space_ashare_select.sync._BaostockSession", return_value=session
        ):
            stats = sync_obj.backfill(["600000"], sleep_fn=lambda s: None)
        assert stats["skipped"] == 1
        session.query_daily.assert_not_called()

    def test_backfill_max_retries_fail(self, sync_obj):
        session = MagicMock()
        session.login.return_value = True
        session.query_daily.side_effect = RuntimeError("down")

        with patch(
            "selene_space_ashare_select.sync._BaostockSession", return_value=session
        ):
            stats = sync_obj.backfill(
                ["600000"], start_date="2024-01-01", sleep_fn=lambda s: None
            )
        assert stats == {"success": 0, "skipped": 0, "failed": 1}
        assert session.query_daily.call_count == 3


class TestRead:
    def test_get_ohlcv(self, sync_obj):
        df = _rows_to_df(SAMPLE_ROWS, "600000")
        with sqlite3.connect(sync_obj.db_path) as conn:
            sync_obj._upsert(conn, df)
            conn.commit()
        out = sync_obj.get_ohlcv("600000")
        assert list(out["date"]) == ["2024-01-02", "2024-01-03"]
        assert sync_obj.get_local_symbols() == ["600000"]
