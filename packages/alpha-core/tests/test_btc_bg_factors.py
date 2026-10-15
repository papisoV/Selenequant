"""B2 background feature family — anchors, wiring, and NaN-contract tests.

Hand-computed anchors on synthetic background data (injected via the
``BTC_BG_DB_PATH`` env override to a temporary sqlite db), vocabulary wiring
locks (tail position, shared-segment digest unchanged), and the NaN
contract: background gaps yield NaN — never zero-filled fabrications.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.factors.btc_bg_factors import (
    BTC_BG_DB_PATH_ENV,
    BTC_BG_FACTORS,
    compute_btc_bg_rs_extreme,
    compute_btc_bg_rs_level,
    compute_btc_bg_rs_slope,
)
from selene_space_alpha.factors.pandas_bridge import ALL_FACTORS
from selene_space_alpha.vocab import FEATURE_NAMES, FORMULA_VOCAB
from selene_space_alpha.vm.vm_pandas import PandasStackVM

BG_TOKENS = ("BTC_BG_RS_LEVEL", "BTC_BG_RS_SLOPE", "BTC_BG_RS_EXTREME")
PANDAS_NAMES = ("btc_bg_rs_level", "btc_bg_rs_slope", "btc_bg_rs_extreme")


def _write_bg_db(
    db_path: Path,
    closes_btc: list[float],
    closes_eth: list[float],
    start_offset_days: int = 0,
) -> Path:
    """Two-symbol klines.db at the given path, covering days from 2026-01-01."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = db_path
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE klines (exchange TEXT, market_type TEXT, symbol TEXT,"
        " interval TEXT, timestamp INTEGER, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL, closed INTEGER)"
    )
    base_ms = 1_767_225_600_000  # 2026-01-01 00:00 UTC
    rows = []
    day_ms = 86_400_000
    for i, c in enumerate(closes_btc):
        ts = base_ms + (i + start_offset_days) * day_ms
        rows.append(("okx", "swap", "BTC/USDT", "D1", ts, c, c, c, c, 1.0, 1))
    for i, c in enumerate(closes_eth):
        ts = base_ms + (i + start_offset_days) * day_ms
        rows.append(("okx", "swap", "ETH/USDT", "D1", ts, c, c, c, c, 1.0, 1))
    conn.executemany("INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def _ohlc_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    n = len(index)
    return pd.DataFrame(
        {
            "open": np.full(n, 100.0),
            "high": np.full(n, 101.0),
            "low": np.full(n, 99.0),
            "close": np.full(n, 100.0),
            "volume": np.full(n, 1000.0),
        },
        index=index,
    )


@pytest.fixture()
def bg_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Default two-symbol db: BTC +1%/day, ETH -2%/day, 200 days."""
    n = 200
    btc = [100.0 * (1.01**i) for i in range(n)]
    eth = [100.0 * (0.98**i) for i in range(n)]
    db = _write_bg_db(tmp_path / "klines.db", btc, eth)
    monkeypatch.setenv(BTC_BG_DB_PATH_ENV, str(db))
    return db


class TestAnchors:
    def test_rs_level_constant_z_is_one(self, bg_env: Path) -> None:
        # rs is constant → trailing std == 0 → guarded to NaN... but with
        # mom_window=20 warmup the first 19+60 bars are NaN. After warmup,
        # constant series → std=0 → z NaN (guarded), NOT 0 (no fabrication).
        idx = pd.date_range("2026-06-01", periods=5, freq="D", tz="UTC")
        out = compute_btc_bg_rs_level(_ohlc_frame(idx))
        assert out.isna().all()

    def test_rs_slope_hand_anchor(self, bg_env: Path) -> None:
        # rs constant (log(1.01)-log(0.98)) → slope = diff over 5 bars = 0.
        idx = pd.date_range("2026-06-01", periods=7, freq="D", tz="UTC")
        out = compute_btc_bg_rs_slope(_ohlc_frame(idx))
        expected_rs = np.log(1.01) - np.log(0.98)
        valid = out.dropna()
        assert len(valid) == 2  # diff(5) over 7 bars → last 2 bars defined
        np.testing.assert_allclose(valid.to_numpy(), 0.0, atol=1e-12)
        # and the underlying level matches the anchor
        assert abs(expected_rs - 0.030153038170687558) < 1e-15

    def test_rs_extreme_binary_with_nan_gaps(self, bg_env: Path) -> None:
        # Constant rs → z undefined (std=0) → extreme stays NaN, never 0.
        idx = pd.date_range("2026-06-01", periods=5, freq="D", tz="UTC")
        out = compute_btc_bg_rs_extreme(_ohlc_frame(idx))
        assert out.isna().all()

    def test_extreme_flags_real_extremes(self, bg_env: Path) -> None:
        # Build a background with one genuine outlier day inside the queried
        # window: BTC halves at index 100 of 200 (rs dips hard). The z-score
        # window (60 bars) around it has mostly normal days, so the outlier
        # must exceed |z| >= 2 somewhere near index 100.
        n = 200
        btc = [100.0 * (1.01**i) for i in range(n)]
        eth = [100.0 * (0.98**i) for i in range(n)]
        btc[100] = btc[99] * 0.5
        db2 = _write_bg_db(bg_env.parent / "klines2.db", btc, eth)
        os.environ[BTC_BG_DB_PATH_ENV] = str(db2)
        try:
            # Query from db start: z needs 60 valid bars of history before the
            # crash day (index 100 = 2026-04-11) to fire, so the window must
            # span the whole background up to ~2026-05.
            idx = pd.date_range("2026-01-01", periods=125, freq="D", tz="UTC")
            out = compute_btc_bg_rs_extreme(_ohlc_frame(idx))
            assert (out == 1.0).any()
        finally:
            os.environ.pop(BTC_BG_DB_PATH_ENV, None)


class TestNaNContract:
    def test_background_gap_yields_nan(self, bg_env: Path) -> None:
        # Query a date before the db starts → reindex gap → NaN, not 0.
        idx = pd.date_range("2025-01-01", periods=3, freq="D", tz="UTC")
        assert compute_btc_bg_rs_level(_ohlc_frame(idx)).isna().all()
        assert compute_btc_bg_rs_slope(_ohlc_frame(idx)).isna().all()
        assert compute_btc_bg_rs_extreme(_ohlc_frame(idx)).isna().all()

    def test_partial_gap_mixes_nan_and_values(self, bg_env: Path) -> None:
        idx = pd.date_range("2025-12-30", periods=4, freq="D", tz="UTC")
        out = compute_btc_bg_rs_level(_ohlc_frame(idx))
        assert out.iloc[0:2].isna().all()
        assert out.iloc[2:].isna().all()  # still z-warmup (60-bar window)

    def test_missing_db_degrades_to_all_nan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # db absent → all-NaN background (not an exception): vocab features
        # must stay computable on any OHLCV frame; absence is never zero-filled.
        monkeypatch.setenv(BTC_BG_DB_PATH_ENV, str(tmp_path / "nope.db"))
        idx = pd.date_range("2026-06-01", periods=5, freq="D", tz="UTC")
        frame = _ohlc_frame(idx)
        assert compute_btc_bg_rs_level(frame).isna().all()
        assert compute_btc_bg_rs_slope(frame).isna().all()
        assert compute_btc_bg_rs_extreme(frame).isna().all()


class TestBackgroundCache:
    """The T4 chain rebuilds the background 6-12x per run at ~50s a go against
    the 2.3GB klines.db; the memo must collapse repeats WITHOUT going stale."""

    def test_repeat_call_reuses_load(
        self, bg_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from selene_space_alpha.factors import btc_background as bg_mod

        calls = {"n": 0}
        real = bg_mod.load_close_matrix

        def counting(*args: object, **kwargs: object) -> pd.DataFrame:
            calls["n"] += 1
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(bg_mod, "load_close_matrix", counting)
        first = bg_mod.build_background(db_path=bg_env)
        second = bg_mod.build_background(db_path=bg_env)
        assert calls["n"] == 1
        assert first.equals(second)

    def test_db_revision_invalidates(
        self, bg_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from selene_space_alpha.factors import btc_background as bg_mod

        # klines.db grows during mining — a rebuilt file must not serve the
        # pre-rebuild series. One extra bar changes the size, hence the key.
        bg_mod.build_background(db_path=bg_env)
        n = 201
        bg_env.unlink()
        _write_bg_db(
            bg_env,
            [100.0 * (1.01**i) for i in range(n)],
            [100.0 * (0.98**i) for i in range(n)],
        )
        calls = {"n": 0}
        real = bg_mod.load_close_matrix

        def counting(*args: object, **kwargs: object) -> pd.DataFrame:
            calls["n"] += 1
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(bg_mod, "load_close_matrix", counting)
        bg_mod.build_background(db_path=bg_env)
        assert calls["n"] == 1


class TestWiring:
    def test_bg_tokens_at_vocab_tail(self) -> None:
        # BTC_BG_* is no longer the very tail — the 7 artifact-bridge factors
        # (2026-09-18) were appended after it.
        assert FEATURE_NAMES[-10:-7] == BG_TOKENS
        assert len(FEATURE_NAMES) == 110
        assert FORMULA_VOCAB.feature_count == 110
        assert FORMULA_VOCAB.size == 110 + 23

    def test_shared_segment_digest_unchanged(self) -> None:
        """BTC_BG_* tail doesn't participate in the shared digest (W compat);
        the TS_STD_20 operator append (2026-09-18 artifact batch) DID move it —
        re-blessed, see test_vocab_k_superset.W_VOCAB_DIGEST_ORACLE."""
        assert FORMULA_VOCAB.vocab_digest == "selene-vocab-09b5d4b06dc5ed2b"

    def test_all_factors_registry_contains_bg(self) -> None:
        for key in PANDAS_NAMES:
            assert key in ALL_FACTORS
            assert ALL_FACTORS[key][0] is not None

    def test_bg_factor_dict_matches_registry(self) -> None:
        assert set(BTC_BG_FACTORS) == set(PANDAS_NAMES)

    def test_vm_resolves_bg_tokens_to_real_functions(self) -> None:
        vm = PandasStackVM()
        for name in BG_TOKENS:
            idx = list(FEATURE_NAMES).index(name)
            feat_idx, fn = vm._factor_fns[idx]
            assert feat_idx == name
            assert fn is not None

    def test_vm_executes_bg_token_on_real_data(self, bg_env: Path) -> None:
        idx = pd.date_range("2026-01-01", periods=120, freq="D", tz="UTC")
        data = _ohlc_frame(idx)
        vm = PandasStackVM()
        for name in BG_TOKENS:
            tok = list(FEATURE_NAMES).index(name)
            out = vm.execute([tok], data)
            assert out is not None, name
            # non-degenerate on the post-warmup tail
            assert out.iloc[90:].abs().sum() > 0.0, name
