"""End-to-end test for TGSpikeSource — verify it reproduces validated alpha.

Tests:
1. Module imports cleanly
2. load_tg_signals returns 794 real signals (noise filtered)
3. B-mapping: '*' → BTC+ETH+SOL
4. attach_vol_trigger dedups klines index (klines.db has duplicates)
5. compute_spike produces zero-lookahead shift(1) z-score
6. cluster_dedup_spikes gap>=24 bars
7. classify_tier routing rules
8. build_signal_dicts returns expected signal shape
9. End-to-end: BTC/ETH/SOL source produces ~12 signals each (matches revalidation)
"""

from __future__ import annotations
import sys
from pathlib import Path

import pytest
import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "alpha-core"))
sys.path.insert(0, str(REPO / "packages" / "shared-types"))

from selene_space_alpha.factors.tg_spike_source import (  # noqa: E402
    TGSpikeSource,
    load_tg_signals,
    map_signal_to_symbols,
    attach_vol_trigger,
    compute_spike,
    cluster_dedup_spikes,
    classify_tier,
    compute_other_symbol_spike_sets,
    A_TIER_EVENTS,
    B_TIER_EVENTS,
    TIER_POSITION_MULT,
)
from selene_space_core.kline_paths import default_klines_db_path  # noqa: E402

KLINES_DB = default_klines_db_path()
TG_DB = REPO / "实验" / "tg_factors.db"


# 需要本机真实数据盘的测试（klines.db / internal-archive）：这两个库是
# 私有研究产物，不在公开树里（公开树 0 文件）。公开树 CI 里应**跳过**而非
# 失败——纯函数单元测试不受影响，只有读库的类需要此守卫。
# ⚠️ 判据是"表可用"而非"文件存在"：实测 `internal-archive` 可能是 0 字节
# 空文件（exists() 为真但查询报 no such table），只查 exists 会漏判。
def _has_local_research_data() -> bool:
    import sqlite3

    if not KLINES_DB.exists():
        return False
    try:
        conn = sqlite3.connect(f"file:{TG_DB}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tg_factors'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return row is not None


_needs_local_data = pytest.mark.skipif(
    not _has_local_research_data(),
    reason="requires local klines.db + internal-archive (research artifacts, not in public tree)",
)


def _load_klines(symbol: str, interval: str = "4h") -> pd.DataFrame:
    import sqlite3

    conn = sqlite3.connect(str(KLINES_DB))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE exchange='binance' AND market_type='futures' AND symbol=? AND interval=? "
        "ORDER BY timestamp",
        conn,
        params=(symbol, interval),
    )
    conn.close()
    df["ts"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.set_index("ts").sort_index()


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@_needs_local_data
class TestLoadTGSignals:
    def test_loads_real_signals_only(self):
        df = load_tg_signals(TG_DB, filter_noise=True)
        assert len(df) == 794, f"expected 794 real signals, got {len(df)}"
        assert "reasoning" in df.columns
        assert not df["reasoning"].str.startswith("noise:").any()

    def test_loads_all_when_no_filter(self):
        df = load_tg_signals(TG_DB, filter_noise=False)
        assert len(df) == 2096, f"expected 2096 total, got {len(df)}"


class TestBMapping:
    def test_asterisk_maps_to_three_symbols(self):
        assert set(map_signal_to_symbols(["*"])) == {"BTC", "ETH", "SOL"}

    def test_explicit_symbol_passthrough(self):
        assert map_signal_to_symbols(["BTC"]) == ["BTC"]
        assert map_signal_to_symbols(["BTC", "ETH"]) == ["BTC", "ETH"]

    def test_unknown_symbol_filtered(self):
        assert map_signal_to_symbols(["XRP", "ADA"]) == []

    def test_mixed_asterisk_and_named(self):
        # '*' dominates, returns all three (asterisk is global)
        result = map_signal_to_symbols(["*", "BTC"])
        assert set(result) == {"BTC", "ETH", "SOL"}


@_needs_local_data
class TestAttachVolTrigger:
    def test_dedups_duplicate_index(self):
        """attach_vol_trigger dedups klines by index.

        Historically klines.db had duplicate timestamps (when queries didn't
        filter exchange/market_type); queries are now filtered to a single
        exchange/market_type so duplicates don't occur. Still verify the dedup
        safety net works with a synthetic duplicated frame.
        """
        base = _load_klines("BTC/USDT")
        # Synthesize duplicate timestamps to test dedup safety net
        klines = pd.concat([base, base.iloc[:5]])
        assert not klines.index.is_unique, "precondition: synthetic dupes"
        signals = load_tg_signals(TG_DB)
        signals["mapped_symbols"] = signals["symbols_list"].apply(map_signal_to_symbols)
        result = attach_vol_trigger(klines, signals, "BTC")
        assert result.index.is_unique, "attach_vol_trigger must dedup"
        assert "vol_trigger" in result.columns
        assert "event_count" in result.columns

    def test_vol_trigger_nonzero_on_signal_bars(self):
        klines = _load_klines("BTC/USDT")
        signals = load_tg_signals(TG_DB)
        signals["mapped_symbols"] = signals["symbols_list"].apply(map_signal_to_symbols)
        result = attach_vol_trigger(klines, signals, "BTC")
        # Should have at least some bars with non-zero vol_trigger
        nz = (result["vol_trigger"] > 0).sum()
        assert nz > 0, "expected some bars to have vol_trigger > 0"


@_needs_local_data
class TestComputeSpike:
    def test_zero_lookahead_shift(self):
        """spike_z must be shifted by 1 bar (current bar uses previous bar's z)."""
        klines = _load_klines("BTC/USDT")
        signals = load_tg_signals(TG_DB)
        signals["mapped_symbols"] = signals["symbols_list"].apply(map_signal_to_symbols)
        klines = attach_vol_trigger(klines, signals, "BTC")
        result = compute_spike(klines)
        # Verify shift: recompute z without shift, check spike_z == z.shift(1)
        mean = result["vol_trigger"].rolling(20, min_periods=20).mean()
        std = result["vol_trigger"].rolling(20, min_periods=20).std()
        z_unshifted = (result["vol_trigger"] - mean) / std.replace(0, np.nan)
        pd.testing.assert_series_equal(
            result["spike_z"],
            z_unshifted.shift(1),
            check_names=False,
            check_dtype=False,
        )

    def test_spike_threshold(self):
        klines = _load_klines("BTC/USDT")
        signals = load_tg_signals(TG_DB)
        signals["mapped_symbols"] = signals["symbols_list"].apply(map_signal_to_symbols)
        klines = attach_vol_trigger(klines, signals, "BTC")
        result = compute_spike(klines, z_threshold=2.0)
        # All spike bars must have spike_z > 2.0
        spike_bars = result[result["spike"] == 1]
        assert (spike_bars["spike_z"] > 2.0).all()
        # All non-spike bars (with valid z) must have spike_z <= 2.0
        valid_z = result["spike_z"].notna()
        non_spike = result[(result["spike"] == 0) & valid_z]
        assert (non_spike["spike_z"] <= 2.0).all()


class TestClusterDedup:
    def test_empty_input(self):
        assert cluster_dedup_spikes([]) == []

    def test_single_spike(self):
        ts = [pd.Timestamp("2025-11-01 00:00", tz="UTC")]
        assert cluster_dedup_spikes(ts) == ts

    def test_dedup_within_gap(self):
        """Spikes within 24 bars (96h) collapse to one."""
        t0 = pd.Timestamp("2025-11-01 00:00", tz="UTC")
        t1 = t0 + pd.Timedelta(hours=4)  # 1 bar later
        t2 = t0 + pd.Timedelta(hours=8)  # 2 bars later
        result = cluster_dedup_spikes([t0, t1, t2], gap_bars=24, bar_hours=4)
        assert result == [t0]

    def test_keep_across_gap(self):
        """Spikes >= 24 bars apart are both kept."""
        t0 = pd.Timestamp("2025-11-01 00:00", tz="UTC")
        t1 = t0 + pd.Timedelta(hours=4 * 24)  # exactly 24 bars later
        result = cluster_dedup_spikes([t0, t1], gap_bars=24, bar_hours=4)
        assert len(result) == 2


class TestClassifyTier:
    def test_non_spike_returns_skip(self):
        assert (
            classify_tier(
                spike_bar=False, multi_sym=False, event_type=None, symbol_short="BTC"
            )
            == "skip"
        )

    def test_multi_sym_returns_S(self):
        assert (
            classify_tier(
                spike_bar=True, multi_sym=True, event_type="market", symbol_short="BTC"
            )
            == "S"
        )

    def test_a_tier_events(self):
        for evt in A_TIER_EVENTS:
            assert (
                classify_tier(
                    spike_bar=True, multi_sym=False, event_type=evt, symbol_short="BTC"
                )
                == "A"
            )

    def test_b_tier_events(self):
        for evt in B_TIER_EVENTS:
            assert (
                classify_tier(
                    spike_bar=True, multi_sym=False, event_type=evt, symbol_short="BTC"
                )
                == "B"
            )

    def test_eth_regulatory_returns_C(self):
        assert (
            classify_tier(
                spike_bar=True,
                multi_sym=False,
                event_type="regulatory",
                symbol_short="ETH",
            )
            == "C_ETH_REG"
        )

    def test_btc_regulatory_returns_skip(self):
        """Only ETH has reverse alpha for regulatory."""
        assert (
            classify_tier(
                spike_bar=True,
                multi_sym=False,
                event_type="regulatory",
                symbol_short="BTC",
            )
            == "skip"
        )

    def test_other_event_returns_skip(self):
        assert (
            classify_tier(
                spike_bar=True, multi_sym=False, event_type="whale", symbol_short="BTC"
            )
            == "A"
        )  # whale is A-tier
        assert (
            classify_tier(
                spike_bar=True, multi_sym=False, event_type="other", symbol_short="BTC"
            )
            == "skip"
        )


class TestPositionMultiplier:
    def test_tier_mult_consistency(self):
        assert TIER_POSITION_MULT["S"] == 1.0
        assert TIER_POSITION_MULT["A"] == 0.7
        assert TIER_POSITION_MULT["B"] == 0.5
        assert TIER_POSITION_MULT["C_ETH_REG"] == 0.5
        assert TIER_POSITION_MULT["skip"] == 0.0


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@_needs_local_data
class TestEndToEnd:
    """End-to-end: TGSpikeSource produces signals matching revalidation."""

    @pytest.fixture(scope="class")
    def klines_by_symbol(self):
        return {sym: _load_klines(sym) for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]}

    @pytest.fixture(scope="class")
    def spike_sets(self, klines_by_symbol):
        return compute_other_symbol_spike_sets(TG_DB, klines_by_symbol, "4h")

    @pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    def test_produces_reasonable_signal_count(
        self, klines_by_symbol, spike_sets, symbol
    ):
        others = [
            spike_sets[s] for s in ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if s != symbol
        ]
        source = TGSpikeSource(
            tg_db_path=TG_DB,
            symbol=symbol,
            interval="4h",
            other_symbol_spike_sets=others,
        )
        klines = klines_by_symbol[symbol]
        klines = klines[klines.index >= pd.Timestamp("2025-11-01", tz="UTC")]
        signals = source.build_signal_dicts(klines)

        # Expect 10-15 signals per symbol (matches revalidation's 14 independent spikes)
        assert (
            8 <= len(signals) <= 20
        ), f"{symbol}: expected 8-20 signals, got {len(signals)}"

    @pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    def test_signal_strength_in_valid_range(self, klines_by_symbol, spike_sets, symbol):
        others = [
            spike_sets[s] for s in ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if s != symbol
        ]
        source = TGSpikeSource(
            tg_db_path=TG_DB,
            symbol=symbol,
            interval="4h",
            other_symbol_spike_sets=others,
        )
        klines = klines_by_symbol[symbol]
        klines = klines[klines.index >= pd.Timestamp("2025-11-01", tz="UTC")]
        signals = source.build_signal_dicts(klines)

        for s in signals:
            assert s["side"] in ("buy", "sell")
            assert 0.0 < s["strength"] <= 1.0
            assert s["tier"] in ("S", "A", "B", "C_ETH_REG")
            assert s["hold_bars"] > 0
            assert s["timestamp"] > 0
            assert isinstance(s["reason"], str)
            assert "tier=" in s["reason"]

    @pytest.mark.parametrize("symbol", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    def test_expected_return_close_to_validated(
        self, klines_by_symbol, spike_sets, symbol
    ):
        """Verify source's signals reproduce the validated alpha.

        From tg_pure_strategy backtest (z=2.0, hold=24, tier):
        - BTC: 14 trades, +7.69%, win 71.4%
        - ETH: 14 trades, +4.45%, win 57.1%
        - SOL: 14 trades, +13.68%, win 64.3%

        We allow some divergence because:
        - TGSpikeSource dedups slightly differently (tier != skip filter)
        - Different TP/SL defaults per symbol
        """
        others = [
            spike_sets[s] for s in ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if s != symbol
        ]
        source = TGSpikeSource(
            tg_db_path=TG_DB,
            symbol=symbol,
            interval="4h",
            other_symbol_spike_sets=others,
        )
        klines = klines_by_symbol[symbol]
        klines = klines[klines.index >= pd.Timestamp("2025-11-01", tz="UTC")]
        signals = source.build_signal_dicts(klines)

        # Compute simple short return for each signal (no commission, hold_bars)
        klines = klines[~klines.index.duplicated(keep="first")]
        returns = []
        for sig in signals:
            entry_ts = pd.Timestamp(sig["timestamp"], unit="ms", tz="UTC")
            try:
                idx = klines.index.get_loc(entry_ts)
            except KeyError:
                continue
            exit_idx = idx + sig["hold_bars"]
            if exit_idx >= len(klines):
                continue
            entry = klines.iloc[idx]["close"]
            exit_ = klines.iloc[exit_idx]["close"]
            if sig["side"] == "sell":
                ret = (entry - exit_) / entry
            else:
                ret = (exit_ - entry) / entry
            returns.append(ret * sig["strength"])

        if not returns:
            pytest.skip(f"{symbol}: no evaluable signals")

        mean_ret = float(np.mean(returns))
        win_rate = float((np.array(returns) > 0).mean())

        # Expect mean_ret > 0 (alpha exists) and win_rate > 50%
        assert (
            mean_ret > 0
        ), f"{symbol}: expected positive mean return, got {mean_ret:+.4%}"
        assert (
            win_rate >= 0.5
        ), f"{symbol}: expected win_rate >= 50%, got {win_rate:.1%}"

        # Log actual for visibility (not assertion — exact match not required)
        print(
            f"\n{symbol}: n={len(returns)}, mean_ret={mean_ret:+.4%}, win_rate={win_rate:.1%}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
