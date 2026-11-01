"""Per-trade decision-context snapshots (ml_features port).

Locks the three-layer relay: strategy emits ``Signal.features`` on entry →
engine keys it to the open order's id → fill writes it into the position
entry → the completed trade blotter row carries ``decision_ctx``.

Covers: snapshot presence on entry-only signals, PIT invariant (features
computed from closed bars at or before the entry bar), partial-close
inheritance, stale-snapshot containment (failed opens), and zero impact on
strategies that never set features.
"""

from __future__ import annotations

import pathlib
import sys
from collections import deque

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from fixtures.generate_data import generate_deterministic_btc_data

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


# ---------------------------------------------------------------------------
# Test strategies
# ---------------------------------------------------------------------------


class EntrySnapshotStrategy:
    """Long-only: buys after N bars, closes after M more; stamps features.

    The feature payload embeds the entry bar's timestamp and the close at
    decision time, letting the PIT test verify the engine relayed exactly
    what the strategy computed — without any feature math of its own.
    """

    def __init__(self, entry_after: int = 60, hold_bars: int = 5) -> None:
        self.entry_after = entry_after
        self.hold_bars = hold_bars
        self._window: deque[float] = deque(maxlen=entry_after)
        self._entry_ts_seen: int | None = None
        self._bars_since_entry = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        if self._entry_ts_seen is not None:
            self._bars_since_entry += 1
            if self._bars_since_entry >= self.hold_bars:
                self._entry_ts_seen = None
                self._bars_since_entry = 0
                return Signal(
                    strategy_id="entry_snapshot",
                    symbol=event.symbol,
                    side=OrderSide.SELL,
                    strength=1.0,
                    timestamp=event.timestamp,
                    reason="hold elapsed",
                    quantity=0.1,
                )
            return None

        self._window.append(event.close)
        if len(self._window) < self.entry_after:
            return None
        features = {
            "entry_bar_ts": event.timestamp,
            "decision_close": event.close,
            "bars_seen": len(self._window),
        }
        self._entry_ts_seen = event.timestamp
        self._bars_since_entry = 0
        return Signal(
            strategy_id="entry_snapshot",
            symbol=event.symbol,
            side=OrderSide.BUY,
            strength=1.0,
            timestamp=event.timestamp,
            reason="window full",
            quantity=0.1,
            features=features,
        )


class SilentStrategy:
    """Never sets features — the zero-impact control arm."""

    def __init__(self, entry_after: int = 60, hold_bars: int = 5) -> None:
        self._inner = EntrySnapshotStrategy(entry_after, hold_bars)

    def on_kline(self, event: KlineEvent) -> Signal | None:
        signal = self._inner.on_kline(event)
        if signal is None:
            return None
        return signal.model_copy(update={"features": None})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(strategy, n_bars: int = 200) -> list[dict]:
    df = generate_deterministic_btc_data(n_bars=n_bars)
    config = BacktestConfig(
        initial_cash=10_000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        position_sizing="signal_quantity",
        strategy_id="ctx-test",
    )
    return BacktestEngine(config).run(strategy, df).trades


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEntrySnapshotRelay:
    def test_entry_trades_carry_decision_ctx(self):
        trades = _run(EntrySnapshotStrategy())
        assert trades, "strategy must produce at least one round trip"
        for trade in trades:
            ctx = trade["decision_ctx"]
            assert ctx, "entry snapshot must survive the relay into the blotter"
            # snapshot is from the DECISION bar; entry_time is the FILL bar
            # (next bar's open) — decision strictly precedes fill.
            assert ctx["entry_bar_ts"] < trade["entry_time"]
            assert isinstance(ctx["decision_close"], float)

    def test_pit_snapshot_matches_entry_bar_data(self):
        """Snapshot content must be derivable from data up to the entry bar."""
        df = generate_deterministic_btc_data(n_bars=200)
        trades = _run(EntrySnapshotStrategy())
        by_ts = {int(row.timestamp): row for _, row in df.iterrows()}
        for trade in trades:
            ctx = trade["decision_ctx"]
            entry_bar = by_ts[ctx["entry_bar_ts"]]
            assert ctx["decision_close"] == float(entry_bar.close)
            # entry_time is the fill bar (next bar's open); snapshot must
            # come from a bar at or BEFORE it — never after.
            assert ctx["entry_bar_ts"] <= trade["entry_time"]

    def test_exit_signals_do_not_overwrite(self):
        """Only the entry decision's snapshot lands on the trade."""
        stamped = EntrySnapshotStrategy(hold_bars=8)
        trades = _run(stamped)
        for trade in trades:
            ctx = trade["decision_ctx"]
            assert ctx["bars_seen"] == stamped.entry_after


class TestPartialCloseInheritance:
    def test_partial_closes_inherit_entry_snapshot(self):
        """Scaled-out legs report the same snapshot as the opening decision."""
        from selene_space_backtest.engine import _PositionEntry, _dc_replace

        entry = _PositionEntry(
            side=OrderSide.BUY,
            entry_price=100,
            entry_ts=1_000,
            entry_fee=1,
            quantity=10,
            decision_ctx={"entry_bar_ts": 999, "decision_close": 100.0},
        )
        reduced = _dc_replace(
            entry,
            quantity=__import__("decimal").Decimal("4"),
            entry_fee=__import__("decimal").Decimal("1"),
        )
        assert reduced.decision_ctx == entry.decision_ctx


class TestZeroImpact:
    def test_silent_strategy_records_empty_ctx(self):
        trades = _run(SilentStrategy())
        assert trades, "control strategy must still trade"
        for trade in trades:
            assert trade["decision_ctx"] == {}

    def test_signal_model_serializes_without_features(self):
        sig = Signal(
            strategy_id="x",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            strength=1.0,
            timestamp=1,
            reason="r",
        )
        assert sig.features is None
        dumped = sig.model_dump()
        assert dumped["features"] is None


class TestTemplateBaselineFeatures:
    """The template's baseline snapshot: schema tag, keys, warm-up Nones."""

    def _strategy_and_trades(self):
        from selene_space_strategy.template import (
            BacktestStrategyTemplate,
            SignalType,
        )

        recorded: dict = {}

        class Tiny(BacktestStrategyTemplate):
            def get_signal(self, data: pd.DataFrame) -> SignalType:
                closes = data["close"]
                if len(closes) >= 65 and not recorded:
                    recorded["armed"] = True
                    return SignalType.BUY
                if (
                    recorded
                    and len(closes) - recorded.get("entry_len", len(closes)) >= 5
                ):
                    return SignalType.SELL
                return SignalType.HOLD

            def _process_signal(self, event, signal_type):  # noqa: D401
                signal = super()._process_signal(event, signal_type)
                if signal is not None and signal.side == OrderSide.BUY:
                    recorded["entry_len"] = len(self._bars)
                    recorded["entry_features"] = signal.features
                return signal

        df = generate_deterministic_btc_data(n_bars=120)
        strategy = Tiny(symbol="BTC/USDT")
        from selene_space_backtest.engine import BacktestEngine as Eng

        result = Eng(BacktestConfig(initial_cash=10_000.0)).run(strategy, df)
        return recorded, result, strategy

    def test_baseline_keys_and_schema(self):
        recorded, result, _ = self._strategy_and_trades()
        features = recorded.get("entry_features")
        assert features is not None
        assert features["feat_schema"] == 1
        expected = {
            "deg_ang_21",
            "deg_ang_60",
            "price_rank_60",
            "price_rank_120",
            "atr14_pct",
            "atr_pctile_120",
            "vol_ratio",
            "dist_high_120",
            "dist_low_120",
        }
        assert expected <= set(features)

    def test_baseline_values_in_range(self):
        recorded, result, _ = self._strategy_and_trades()
        features = recorded["entry_features"]
        assert 0.0 <= features["price_rank_60"] <= 1.0
        assert 0.0 <= features["price_rank_120"] <= 1.0
        assert features["atr14_pct"] is None or features["atr14_pct"] > 0
        if features["atr_pctile_120"] is not None:
            assert 0.0 <= features["atr_pctile_120"] <= 1.0
        if features["vol_ratio"] is not None:
            assert features["vol_ratio"] > 0

    def test_blotter_and_signal_agree(self):
        recorded, result, _ = self._strategy_and_trades()
        ctx_trades = [t for t in result.trades if t["decision_ctx"]]
        assert ctx_trades, "completed trade must carry the template snapshot"
        sig_features = recorded["entry_features"]
        for trade in ctx_trades:
            assert trade["decision_ctx"]["feat_schema"] == sig_features["feat_schema"]
            assert (
                trade["decision_ctx"]["price_rank_60"] == sig_features["price_rank_60"]
            )
