"""US-005: P1 strategy template tests (A1 RSIMomentum / A4 ERStateMachine /
A3 ChopGuard / A7 ReversionCostGuard).

Each template must:
- subclass BacktestStrategyTemplate and implement the full interface
  (get_signal / _process_signal / get_parameters / reset), mirroring
  templates/presets/rsi_ma_delta.py
- produce at least one completed trade when run through ``run_backtest``
- A7 additionally: expected_reversion > friction budget gates entries,
  so a hard friction cap (0.50) yields zero trades on the SAME dip/recover
  data that a default budget trades several times
- be registered in STRATEGY_REGISTRY (llm_param_tuner.py) append-only,
  existing ids untouched

All datasets are deterministic / synthetic with positive prices.
"""

from __future__ import annotations

import importlib
import sys

import pandas as pd
import pytest

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_strategy.templates.presets.chop_guard import ChopGuardStrategy
from selene_space_strategy.templates.presets.er_state_machine import ERStateMachineStrategy
from selene_space_strategy.templates.presets.reversion_cost_guard import ReversionCostGuardStrategy
from selene_space_strategy.templates.presets.rsi_momentum_continuation import RSIMomentumContinuationStrategy
from selene_space_types.market import KlineEvent


# ---------------------------------------------------------------------------
# Deterministic synthetic data
# ---------------------------------------------------------------------------

def _klines(closes: list[float], band: float = 0.5) -> list[KlineEvent]:
    """Positive-price KlineEvent series (open/high/low/close > 0 enforced)."""
    out: list[KlineEvent] = []
    for i, c in enumerate(closes):
        out.append(
            KlineEvent(
                exchange="binance",
                symbol="BTC/USDT",
                timestamp=i * 3600_000,
                interval="1h",
                open=c - band,
                high=c + band,
                low=c - band,
                close=c,
                volume=1000.0,
                closed=True,
            )
        )
    return out


def _df(closes: list[float], band: float = 0.5) -> pd.DataFrame:
    """DataFrame shaped like the in-loop ``pd.DataFrame(self._bars)``."""
    return pd.DataFrame(
        {
            "open": [c - band for c in closes],
            "high": [c + band for c in closes],
            "low": [c - band for c in closes],
            "close": list(closes),
        }
    )


def _rsi_scenario() -> list[float]:
    """Noise warmup then two up/down cycles: RSI>70 entry, RSI<55 exit."""
    closes: list[float] = []
    base = 100.0
    for i in range(20):  # warmup flutter, RSI ~50
        base += 0.2 if i % 2 else -0.2
        closes.append(base)
    for _ in range(2):
        for _ in range(25):  # sustained up-run -> RSI ~100
            base += 1.0
            closes.append(base)
        for _ in range(20):  # down-run -> RSI < 55
            base -= 1.0
            closes.append(base)
    return closes


def _er_scenario() -> list[float]:
    """Alternating noise / monotonic-up blocks: ER>=0.5 entry, ER<0.3 exit."""
    closes: list[float] = []
    base = 100.0
    for block in range(5):
        if block % 2 == 0:  # noise: alternating +/-0.5 -> ER ~0.07
            for i in range(20):
                base += 0.5 if i % 2 else -0.5
                closes.append(base)
        else:  # monotonic +1.0 -> ER ~1.0
            for _ in range(25):
                base += 1.0
                closes.append(base)
    return closes


def _chop_scenario() -> list[float]:
    """Steep trend blocks (low CHOP) alternating with oscillation (high CHOP)."""
    closes: list[float] = []
    for cycle in range(2):
        start = 100.0 + cycle * 120.0
        for i in range(60):  # +3.0 monotonic -> CHOP < 38.2
            closes.append(start + 3.0 * i)
        end = closes[-1]
        for i in range(60):  # +/-3 oscillation around level -> CHOP > 61.8
            closes.append(end + (i % 4 - 1.5) * 2.0)
    return closes


def _reversion_scenario() -> list[float]:
    """Flat-100 warmup then 3 dip-to-94 / recover-to-106 cycles (~6% reversion)."""
    closes: list[float] = [100.0] * 25
    for _ in range(3):
        closes.extend(100.0 - s for s in range(1, 7))  # 99..94
        closes.extend(94.0 + i for i in range(1, 13))  # 95..106
    return closes


# ---------------------------------------------------------------------------
# Interface conformance (mirrors rsi_ma_delta.py contract)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cls",
    [
        RSIMomentumContinuationStrategy,
        ERStateMachineStrategy,
        ChopGuardStrategy,
        ReversionCostGuardStrategy,
    ],
)
class TestTemplateInterface:
    def test_subclasses_backtest_template(self, cls) -> None:
        assert issubclass(cls, BacktestStrategyTemplate)

    def test_implements_interface_methods(self, cls) -> None:
        impl = cls()
        for name in ("get_signal", "_process_signal", "get_parameters", "reset"):
            assert callable(getattr(impl, name))

    def test_reset_restores_flat_state(self, cls) -> None:
        st = cls(long_only=True)
        st._position = "long"
        st._tp_price = 123.0
        st.reset()
        assert st._position == "flat"
        assert st._tp_price == 0.0


class TestRSIMomentumContinuation:
    NAME = "rsi_momentum_continuation"

    def test_get_parameters(self) -> None:
        params = RSIMomentumContinuationStrategy().get_parameters()
        for key in ("rsi_len", "continue_threshold", "exit_threshold",
                    "tp_pct", "sl_pct", "enable_long", "enable_short"):
            assert key in params

    def test_get_signal_buy_on_strong_rsi(self) -> None:
        st = RSIMomentumContinuationStrategy(rsi_len=14, tp_pct=1.0, sl_pct=1.0)
        closes = [100.0 + i * 1.0 for i in range(35)]  # sustained up -> RSI~100
        assert st.get_signal(_df(closes)) == SignalType.BUY

    def test_get_signal_hold_on_weak_rsi(self) -> None:
        st = RSIMomentumContinuationStrategy(rsi_len=14, tp_pct=1.0, sl_pct=1.0)
        closes = [120.0 - i * 1.0 for i in range(35)]  # freefall -> RSI~0
        assert st.get_signal(_df(closes)) == SignalType.HOLD

    def test_backtest_produces_trades(self) -> None:
        st = RSIMomentumContinuationStrategy(tp_pct=1.0, sl_pct=1.0)
        result = st.run_backtest(_klines(_rsi_scenario()))
        assert result.total_trades >= 2


class TestERStateMachine:
    NAME = "er_state_machine"

    def test_get_parameters(self) -> None:
        params = ERStateMachineStrategy().get_parameters()
        for key in ("er_period", "entry_threshold", "exit_threshold",
                    "direction_filter", "tp_pct", "sl_pct"):
            assert key in params

    def test_get_signal_buy_on_efficient_trend(self) -> None:
        st = ERStateMachineStrategy(er_period=14, tp_pct=1.0, sl_pct=1.0)
        closes = [100.0 + i * 1.0 for i in range(35)]  # monotonic -> ER~1.0
        assert st.get_signal(_df(closes)) == SignalType.BUY

    def test_get_signal_hold_on_noise(self) -> None:
        st = ERStateMachineStrategy(er_period=14, tp_pct=1.0, sl_pct=1.0)
        closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(35)]  # ER~0.07
        assert st.get_signal(_df(closes)) == SignalType.HOLD

    def test_backtest_produces_trades(self) -> None:
        st = ERStateMachineStrategy(tp_pct=1.0, sl_pct=1.0)
        result = st.run_backtest(_klines(_er_scenario()))
        assert result.total_trades >= 2


class TestChopGuard:
    NAME = "chop_guard"

    def test_get_parameters(self) -> None:
        params = ChopGuardStrategy().get_parameters()
        for key in ("chop_period", "ma_len", "ma_type",
                    "trend_threshold", "choppy_threshold", "tp_pct", "sl_pct"):
            assert key in params

    def test_get_signal_buy_on_trend(self) -> None:
        st = ChopGuardStrategy(chop_period=14, ma_len=20, tp_pct=1.0, sl_pct=1.0)
        closes = [100.0 + i * 3.0 for i in range(40)]  # steep trend -> CHOP low
        assert st.get_signal(_df(closes, band=1.0)) == SignalType.BUY

    def test_get_signal_hold_on_choppy(self) -> None:
        st = ChopGuardStrategy(chop_period=14, ma_len=20, tp_pct=1.0, sl_pct=1.0)
        base = 100.0
        closes = [base + (i % 4 - 1.5) * 2.0 for i in range(40)]  # CHOP>61.8
        assert st.get_signal(_df(closes, band=1.0)) == SignalType.HOLD

    def test_backtest_produces_trades(self) -> None:
        st = ChopGuardStrategy(ma_len=20, tp_pct=1.0, sl_pct=1.0)
        result = st.run_backtest(_klines(_chop_scenario(), band=1.0))
        assert result.total_trades >= 2


class TestReversionCostGuard:
    NAME = "reversion_cost_guard"

    def test_get_parameters(self) -> None:
        params = ReversionCostGuardStrategy().get_parameters()
        for key in ("ma_len", "ma_type", "friction_budget",
                    "min_edge_mult", "tp_pct", "sl_pct"):
            assert key in params

    def test_round_trip_friction_default_derived(self) -> None:
        st = ReversionCostGuardStrategy(fee_rate=0.0005, slippage_bps=1.5)
        expected = 0.0005 * 2 + 1.5 * 2 / 10_000.0  # 0.0013
        assert st._round_trip_friction() == pytest.approx(expected)

    def test_round_trip_friction_explicit_override(self) -> None:
        st = ReversionCostGuardStrategy(friction_budget=0.50)
        assert st._round_trip_friction() == 0.50

    def test_get_signal_buy_when_reversion_clears_friction(self) -> None:
        st = ReversionCostGuardStrategy(ma_len=20, tp_pct=1.0, sl_pct=1.0)
        closes = [100.0] * 22 + [99.0, 98.0, 97.0, 96.0, 95.0, 94.0]
        assert st.get_signal(_df(closes)) == SignalType.BUY

    def test_get_signal_hold_when_friction_blocks_entry(self) -> None:
        st = ReversionCostGuardStrategy(ma_len=20, friction_budget=0.50,
                                        tp_pct=1.0, sl_pct=1.0)
        closes = [100.0] * 22 + [99.0, 98.0, 97.0, 96.0, 95.0, 94.0]
        assert st.get_signal(_df(closes)) == SignalType.HOLD

    def test_a7_friction_gate_blocks_all_trades(self) -> None:
        """A7 sharp test: same data, hard friction cap -> 0 trades vs default >=3."""
        data = _klines(_reversion_scenario())

        gated = ReversionCostGuardStrategy(friction_budget=0.50,
                                           tp_pct=1.0, sl_pct=1.0)
        assert gated.run_backtest(data).total_trades == 0

        default = ReversionCostGuardStrategy(tp_pct=1.0, sl_pct=1.0)
        assert default.run_backtest(data).total_trades >= 3


# ---------------------------------------------------------------------------
# STRATEGY_REGISTRY append-only integration (llm_param_tuner.py)
# ---------------------------------------------------------------------------

NEW_IDS = {
    "rsi_momentum_continuation": RSIMomentumContinuationStrategy,
    "er_state_machine": ERStateMachineStrategy,
    "chop_guard": ChopGuardStrategy,
    "reversion_cost_guard": ReversionCostGuardStrategy,
}
EXISTING_IDS = ("bb_v3", "rsi_ma_delta", "dca_grid")


def _import_tuner():
    """Import scripts.llm_param_tuner without its win32 stdout reassignment."""
    original_platform = sys.platform
    sys.platform = "linux"  # bypass the win32 console rewrap on module import
    try:
        return importlib.import_module("scripts.llm_param_tuner")
    finally:
        sys.platform = original_platform


class TestRegistryAppendOnly:
    def test_new_ids_map_to_correct_classes(self) -> None:
        reg = _import_tuner().STRATEGY_REGISTRY
        for sid, cls in NEW_IDS.items():
            assert sid in reg, f"{sid} missing from STRATEGY_REGISTRY"
            entry = reg[sid]
            assert isinstance(entry, tuple) and len(entry) == 3
            assert entry[0] is cls, f"{sid} maps to wrong class {entry[0]}"
            assert isinstance(entry[1], dict) and entry[1]  # param space non-empty

    def test_existing_ids_preserved(self) -> None:
        reg = _import_tuner().STRATEGY_REGISTRY
        for sid in EXISTING_IDS:
            assert sid in reg, f"append-only broken: {sid} displaced"
            assert len(reg[sid]) == 3

    def test_new_params_are_tunable_bounds(self) -> None:
        reg = _import_tuner().STRATEGY_REGISTRY
        for sid in NEW_IDS:
            for name, (lo, hi) in reg[sid][1].items():
                assert lo < hi, f"{sid}.{name} invalid bound ({lo}, {hi})"
