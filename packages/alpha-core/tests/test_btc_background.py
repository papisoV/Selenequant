"""Tests for the BTC relative-strength background series (QD-absorption B2).

Locks the NaN semantics contract: missing basket members shrink the basket,
all-missing bars are NaN (never 0), and alignment is exact-timestamp.
Hand-computed anchors use tiny synthetic matrices where every value can be
verified on paper.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.factors.btc_background import (
    BTC_SYMBOL,
    DEFAULT_BASKET,
    btc_relative_strength,
    load_close_matrix,
)


def _synthetic_matrix(rows: list[dict[str, float | None]]) -> pd.DataFrame:
    """Build a close matrix from rows of {date, BTC/USDT, ETH/USDT, ...}.

    None values become NaN (missing bar). Dates are ISO strings.
    """
    index = pd.DatetimeIndex(pd.to_datetime([r.pop("_date") for r in rows], utc=True))
    return pd.DataFrame(rows, index=index).astype(float)


class TestBtcRelativeStrength:
    def test_perfect_basket_tracks_btc(self) -> None:
        # ETH always moves exactly like BTC → basket mom == btc mom → RS == 0.
        idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
        base = np.linspace(100.0, 121.0, 30)
        matrix = pd.DataFrame({"BTC/USDT": base, "ETH/USDT": base * 2.0}, index=idx)
        rs = btc_relative_strength(matrix)
        # EMA of a constant-zero series with min_periods=10 → zeros after warmup.
        valid = rs.dropna()
        assert len(valid) > 0
        np.testing.assert_allclose(valid.to_numpy(), 0.0, atol=1e-12)

    def test_btc_outperformance_positive(self) -> None:
        # BTC doubles, alt flat → RS strongly positive after warmup.
        idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
        btc = np.linspace(100.0, 200.0, 30)
        alt = np.full(30, 50.0)
        rs = btc_relative_strength(
            pd.DataFrame({"BTC/USDT": btc, "ETH/USDT": alt}, index=idx)
        )
        valid = rs.dropna()
        assert (valid > 0).all()

    def test_hand_computed_anchor(self) -> None:
        # 21 bars, BTC grows 1%/bar, ETH shrinks 2%/bar (mom_window=1, ema off via span=1).
        # log(1.01)=0.00995033..., log(0.98)=-0.02020271...
        # rs(t) = log(1.01) - log(0.98) = 0.03015305... constant → EMA(span=1)=rs.
        idx = pd.date_range("2026-01-01", periods=21, freq="D", tz="UTC")
        btc = 100.0 * (1.01 ** np.arange(21))
        eth = 100.0 * (0.98 ** np.arange(21))
        rs = btc_relative_strength(
            pd.DataFrame({"BTC/USDT": btc, "ETH/USDT": eth}, index=idx),
            mom_window=1,
            ema_span=1,
        )
        expected = np.log(1.01) - np.log(0.98)
        actual = rs.to_numpy()
        # Bar 0 is NaN: shift(1) warmup has no log return yet.
        assert np.isnan(actual[0])
        np.testing.assert_allclose(actual[1:], expected, rtol=1e-12)

    def test_missing_basket_member_shrinks_basket(self) -> None:
        # ETH has NaN in the tail; RS tail must still be valid (basket = SOL
        # only). ETH NaN starts at bar 20, mom_window=1 so bar 21 onward has a
        # valid single-member basket; EMA(ema_span=1) keeps it valid.
        idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
        btc = np.linspace(100.0, 130.0, 30)
        eth = np.where(np.arange(30) < 20, np.linspace(50, 55, 30), np.nan)
        sol = np.linspace(50.0, 53.0, 30)
        rs = btc_relative_strength(
            pd.DataFrame(
                {"BTC/USDT": btc, "ETH/USDT": eth, "SOL/USDT": sol}, index=idx
            ),
            mom_window=1,
            ema_span=1,
        )
        tail = rs.iloc[21:]
        assert tail.notna().all()

    def test_all_missing_basket_is_nan_not_zero(self) -> None:
        # No alts at all → RS undefined → all NaN (the critical fabrication guard).
        idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
        matrix = pd.DataFrame({"BTC/USDT": np.linspace(100, 110, 30)}, index=idx)
        rs = btc_relative_strength(matrix)
        assert rs.isna().all()

    def test_all_nan_basket_row_is_nan(self) -> None:
        # Alts exist but are all-NaN on some bars → those bars NaN.
        idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
        btc = np.linspace(100.0, 130.0, 30)
        eth = np.where(np.arange(30) == 25, np.nan, np.linspace(50, 60, 30))
        rs = btc_relative_strength(
            pd.DataFrame({"BTC/USDT": btc, "ETH/USDT": eth}, index=idx),
            mom_window=1,
            ema_span=1,
        )
        assert np.isnan(rs.iloc[25])

    def test_btc_missing_raises(self) -> None:
        idx = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")
        matrix = pd.DataFrame({"ETH/USDT": np.linspace(50, 60, 5)}, index=idx)
        with pytest.raises(KeyError):
            btc_relative_strength(matrix)


class TestLoadCloseMatrix:
    def test_missing_db_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_close_matrix(db_path=tmp_path / "nope.db")

    def test_synthetic_db_alignment(self, tmp_path) -> None:
        # Two symbols with offset coverage; matrix must union the axes and
        # keep NaN where a symbol lacks bars.
        import sqlite3

        db = tmp_path / "klines.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE klines (exchange TEXT, market_type TEXT, symbol TEXT,"
            " interval TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
            " close REAL, volume REAL, closed INTEGER)"
        )
        base_ms = 1_700_000_000_000
        rows = []
        for i in range(10):
            rows.append(
                (
                    "okx",
                    "swap",
                    "BTC/USDT",
                    "D1",
                    base_ms + i * 86_400_000,
                    100 + i,
                    101 + i,
                    99 + i,
                    100 + i,
                    1000.0,
                    1,
                )
            )
        for i in range(4, 10):  # ETH starts 4 days late
            rows.append(
                (
                    "okx",
                    "swap",
                    "ETH/USDT",
                    "D1",
                    base_ms + i * 86_400_000,
                    50 + i,
                    51 + i,
                    49 + i,
                    50 + i,
                    500.0,
                    1,
                )
            )
        conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        conn.close()

        matrix = load_close_matrix(
            db_path=db, symbols=("BTC/USDT", "ETH/USDT"), interval="D1"
        )
        assert list(matrix.columns) == ["BTC/USDT", "ETH/USDT"]
        assert len(matrix) == 10
        assert matrix["ETH/USDT"].iloc[:4].isna().all()
        assert matrix["ETH/USDT"].iloc[4:].notna().all()
        assert BTC_SYMBOL in matrix.columns
        assert DEFAULT_BASKET[0] == BTC_SYMBOL
