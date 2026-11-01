"""Tests for LimitPickStrategy — 限价接针 (limit-order dip-taking).

Covers:
- LimitPickConfig immutability + defaults (use_tiered_trailing=False) [US-A2-01]
- try/except ImportError protection for TieredTrailingStopTracker [US-A2-02]
- Ascending klines, no [::-1] reversal [US-A2-03]
- OKX contract count parity with the reference call shape [US-A2-04]
- Trend judgment (EMA240): bullish → BUY limit, bearish → SELL limit
- selected_value = max(min(amp, atr_ratio) * mult, 0.8)
- min_bars gate: insufficient bars → None
- Both sides disabled → None
- ema_period=0 → both sides True (reference behavior)
- Contract conversion plumbing (sync + None fallback)
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import FrozenInstanceError
from typing import Any

import pandas as pd
import pytest

from selene_space_strategy.templates.presets.limit_pick_strategy import (
    LimitPickConfig,
    LimitPickContext,
    LimitPickStrategy,
    _sync_convert,
)
from selene_space_types.agent import TradeIntent
from selene_space_types.trading import OrderSide, OrderType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_klines(
    n: int = 300,
    base_price: float = 60000.0,
    drift: float = 0.0,
    amplitude_pct: float = 1.0,
) -> pd.DataFrame:
    """Build ascending OHLCV klines (oldest-first, newest-last).

    ``drift`` shifts the close each bar by ``drift`` (absolute, not pct).
    ``amplitude_pct`` sets (high-low)/close as a percentage.
    """
    closes = [base_price + drift * i for i in range(n)]
    rows = []
    for i, c in enumerate(closes):
        half_spread = c * amplitude_pct / 100.0 / 2.0
        high = c + half_spread
        low = c - half_spread
        # Open = previous close (smooth), close = c.
        open_ = closes[i - 1] if i > 0 else c
        rows.append(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": c,
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# LimitPickConfig tests
# ---------------------------------------------------------------------------


class TestLimitPickConfig:
    def test_default_use_tiered_trailing_false(self) -> None:
        """[US-A2-01] use_tiered_trailing defaults to False (v4 A1)."""
        cfg = LimitPickConfig()
        assert cfg.use_tiered_trailing is False

    def test_frozen_dataclass(self) -> None:
        """Config is immutable (per python coding-style immutability rule)."""
        cfg = LimitPickConfig()
        with pytest.raises(FrozenInstanceError):
            cfg.ema_period = 100  # type: ignore[misc]

    def test_defaults_match_reference(self) -> None:
        cfg = LimitPickConfig()
        assert cfg.ema_period == 240
        assert cfg.atr_period == 60
        assert cfg.amplitude_period == 60
        assert cfg.value_multiplier == 2.0
        assert cfg.min_selected_value_pct == 0.8
        assert cfg.long_amount_usdt == 20.0
        assert cfg.short_amount_usdt == 20.0

    def test_validation_negative_ema(self) -> None:
        with pytest.raises(ValueError, match="ema_period"):
            LimitPickConfig(ema_period=-1)

    def test_validation_zero_atr(self) -> None:
        with pytest.raises(ValueError, match="atr_period"):
            LimitPickConfig(atr_period=0)

    def test_validation_negative_amount(self) -> None:
        with pytest.raises(ValueError, match="long_amount_usdt"):
            LimitPickConfig(long_amount_usdt=0.0)


# ---------------------------------------------------------------------------
# ImportError protection [US-A2-02]
# ---------------------------------------------------------------------------


class TestImportErrorProtection:
    def test_strategy_loads_without_tiered_trailing_module(self) -> None:
        """[US-A2-02] try/except ImportError protects the import.

        Simulate A1 being unavailable by hiding the
        ``selene_space_paper.tiered_trailing_stop`` module, then reload
        the strategy module and verify it still imports cleanly.
        """
        # Stash and remove the paper-engine module.
        original = sys.modules.pop("selene_space_paper.tiered_trailing_stop", None)
        # Block re-import.
        sys.modules["selene_space_paper.tiered_trailing_stop"] = None  # type: ignore[assignment]
        try:
            # Force reload.
            mod_name = "selene_space_strategy.templates.presets.limit_pick_strategy"
            mod = importlib.reload(sys.modules[mod_name])
            # The strategy class should still be constructible.
            strategy = mod.LimitPickStrategy(
                mod.LimitPickConfig(use_tiered_trailing=True)
            )
            # Trailing tracker should be None because A1 is unavailable.
            assert strategy._tiered_tracker is None
        finally:
            # Restore.
            sys.modules.pop("selene_space_paper.tiered_trailing_stop", None)
            if original is not None:
                sys.modules["selene_space_paper.tiered_trailing_stop"] = original
            # Reload again to restore the real module state for other tests.
            mod_name = "selene_space_strategy.templates.presets.limit_pick_strategy"
            importlib.reload(sys.modules[mod_name])


# ---------------------------------------------------------------------------
# Trend judgment + K-line ordering [US-A2-03]
# ---------------------------------------------------------------------------


class TestTrendJudgment:
    def test_bullish_trend_emits_buy_limit(self) -> None:
        """Rising closes → close > EMA240 → BUY LIMIT below mark price."""
        df = _make_klines(n=300, base_price=100.0, drift=0.5)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        strategy = LimitPickStrategy()
        intent = strategy.evaluate(ctx)
        assert intent is not None
        assert intent.side == OrderSide.BUY
        assert intent.order_type == OrderType.LIMIT
        # Limit price below mark price (for a BUY).
        assert intent.price is not None
        assert intent.price < ctx.mark_price

    def test_bearish_trend_emits_sell_limit(self) -> None:
        """Falling closes → close < EMA240 → SELL LIMIT above mark price."""
        df = _make_klines(n=300, base_price=60000.0, drift=-1.0)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        cfg = LimitPickConfig(enable_short=True, enable_long=False)
        strategy = LimitPickStrategy(cfg)
        intent = strategy.evaluate(ctx)
        assert intent is not None
        assert intent.side == OrderSide.SELL
        assert intent.order_type == OrderType.LIMIT
        # Limit price above mark price (for a SELL).
        assert intent.price is not None
        assert intent.price > ctx.mark_price

    def test_klines_ascending_not_reversed(self) -> None:
        """[US-A2-03] Strategy uses ascending klines without [::-1].

        We feed ascending klines (newest-last). The EMA computed on
        ascending data should match pandas ewm directly. If the strategy
        were applying [::-1] (the OKX-descending workaround), the EMA
        would be inverted and the trend would flip.
        """
        df = _make_klines(n=300, base_price=100.0, drift=0.5)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        strategy = LimitPickStrategy()
        # Patch _evaluate_trend to capture the EMA value it computes.
        original_eval = strategy._evaluate_trend
        captured: dict[str, Any] = {}

        def spy(df: pd.DataFrame, mark_price: float) -> Any:
            result = original_eval(df, mark_price)
            closes = df["close"]
            ema_series = closes.ewm(span=240, adjust=False).mean()
            captured["ema_value"] = float(ema_series.iloc[-1])
            captured["last_close"] = float(closes.iloc[-1])
            return result

        strategy._evaluate_trend = spy  # type: ignore[assignment]
        strategy.evaluate(ctx)
        # EMA should be below last close in a rising market → bullish.
        assert captured["last_close"] > captured["ema_value"]

    def test_ema_period_zero_enables_both_sides(self) -> None:
        """ema_period=0 → both trends True (reference behavior)."""
        df = _make_klines(n=300)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        cfg = LimitPickConfig(ema_period=0, enable_long=True, enable_short=True)
        strategy = LimitPickStrategy(cfg)
        # With both sides enabled and ema=0, the long side is picked first
        # (the bullish branch is checked first in evaluate).
        intent = strategy.evaluate(ctx)
        assert intent is not None
        assert intent.side == OrderSide.BUY


# ---------------------------------------------------------------------------
# selected_value math
# ---------------------------------------------------------------------------


class TestSelectedValue:
    def test_selected_value_floored_at_0_8(self) -> None:
        """min(amp, ratio) * mult < 0.8 → clamped to 0.8."""
        # Tiny amplitude + rising drift (to ensure bullish trend) →
        # selected_value clamped to 0.8.
        df = _make_klines(n=300, base_price=60000.0, drift=0.5, amplitude_pct=0.1)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        cfg = LimitPickConfig(enable_long=True, enable_short=False)
        strategy = LimitPickStrategy(cfg)
        intent = strategy.evaluate(ctx)
        assert intent is not None
        assert intent.risk_params["selected_value_pct"] == pytest.approx(0.8)

    def test_selected_value_formula(self) -> None:
        """selected_value == max(min(amp, atr_ratio) * mult, 0.8)."""
        df = _make_klines(n=300, base_price=60000.0, drift=0.5, amplitude_pct=2.0)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        cfg = LimitPickConfig(enable_long=True, enable_short=False)
        strategy = LimitPickStrategy(cfg)
        intent = strategy.evaluate(ctx)
        assert intent is not None
        amp = intent.risk_params["average_amplitude_pct"]
        ratio = intent.risk_params["price_atr_ratio"]
        expected = max(min(amp, ratio) * cfg.value_multiplier, 0.8)
        assert intent.risk_params["selected_value_pct"] == pytest.approx(
            expected, rel=1e-6
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_insufficient_bars_returns_none(self) -> None:
        """< ema_period+1 bars → None."""
        df = _make_klines(n=10)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        strategy = LimitPickStrategy(LimitPickConfig(ema_period=240))
        assert strategy.evaluate(ctx) is None

    def test_both_sides_disabled_returns_none(self) -> None:
        df = _make_klines(n=300)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        cfg = LimitPickConfig(enable_long=False, enable_short=False)
        strategy = LimitPickStrategy(cfg)
        assert strategy.evaluate(ctx) is None

    def test_zero_mark_price_returns_none(self) -> None:
        df = _make_klines(n=300)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=0.0,
        )
        strategy = LimitPickStrategy()
        assert strategy.evaluate(ctx) is None

    def test_neutral_trend_returns_none(self) -> None:
        """Mark price == EMA → neither bullish nor bearish."""
        # Flat closes → close == EMA → neutral.
        df = _make_klines(n=300, base_price=100.0, drift=0.0, amplitude_pct=0.1)
        # Override mark price to exactly match EMA.
        ema_value = float(df["close"].ewm(span=240, adjust=False).mean().iloc[-1])
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=ema_value,
        )
        strategy = LimitPickStrategy()
        assert strategy.evaluate(ctx) is None

    def test_intent_is_trade_intent(self) -> None:
        df = _make_klines(n=300, base_price=100.0, drift=0.5)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        strategy = LimitPickStrategy()
        intent = strategy.evaluate(ctx)
        assert isinstance(intent, TradeIntent)


# ---------------------------------------------------------------------------
# OKX contract conversion [US-A2-04]
# ---------------------------------------------------------------------------


class _FakePublicAPI:
    """Legacy-style public_api stub."""

    def __init__(self, sz: str = "0.001") -> None:
        self._sz = sz
        self.calls: list[dict[str, Any]] = []

    def convert_contract_coin(
        self,
        *,
        type: str = "",
        instId: str = "",
        sz: str = "",
        px: str = "",
        unit: str = "",
        opType: str = "",
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "type": type,
                "instId": instId,
                "sz": sz,
                "px": px,
                "unit": unit,
                "opType": opType,
            }
        )
        return {
            "code": "0",
            "msg": "",
            "data": [{"sz": self._sz}],
        }


class TestContractConversion:
    def test_parity_with_reference_call_shape(self) -> None:
        """[US-A2-04] OKX call shape matches the reference conversion call."""
        public_api = _FakePublicAPI(sz="0.5")
        # Replicate the reference call.
        result = _sync_convert(
            public_api,
            inst_id="BTC-USDT-SWAP",
            amount_usdt=20.0,
            price=60000.0,
            side="buy",
        )
        assert result == 0.5
        assert len(public_api.calls) == 1
        call = public_api.calls[0]
        assert call["type"] == "1"
        assert call["instId"] == "BTC-USDT-SWAP"
        assert call["sz"] == "20.0"
        assert call["px"] == "60000.0"
        assert call["unit"] == "usdt"
        assert call["opType"] == "open"

    def test_strategy_uses_sync_converter_when_public_api_wired(self) -> None:
        """Strategy converts via public_api when okx_inst_id is set."""
        public_api = _FakePublicAPI(sz="3.0")
        df = _make_klines(n=300, base_price=60000.0, drift=0.5)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        cfg = LimitPickConfig(okx_inst_id="BTC-USDT-SWAP")
        strategy = LimitPickStrategy(cfg, public_api=public_api)
        intent = strategy.evaluate(ctx)
        assert intent is not None
        # Quantity should be the converted contract count (3.0).
        assert intent.quantity == pytest.approx(3.0)
        assert len(public_api.calls) == 1

    def test_strategy_falls_back_to_usdt_notional_without_converter(self) -> None:
        """No public_api / converter → quantity == amount_usdt (paper)."""
        df = _make_klines(n=300, base_price=60000.0, drift=0.5)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        cfg = LimitPickConfig(okx_inst_id=None, long_amount_usdt=20.0)
        strategy = LimitPickStrategy(cfg)
        intent = strategy.evaluate(ctx)
        assert intent is not None
        assert intent.quantity == pytest.approx(20.0)

    def test_zero_contract_count_skips_intent(self) -> None:
        """Converter returns 0 (notional too small) → None (skip branch)."""
        public_api = _FakePublicAPI(sz="0")
        df = _make_klines(n=300, base_price=60000.0, drift=0.5)
        ctx = LimitPickContext(
            symbol="BTC/USDT",
            klines=df,
            mark_price=float(df["close"].iloc[-1]),
        )
        cfg = LimitPickConfig(okx_inst_id="BTC-USDT-SWAP")
        strategy = LimitPickStrategy(cfg, public_api=public_api)
        intent = strategy.evaluate(ctx)
        assert intent is None


# ---------------------------------------------------------------------------
# TieredTrailingStopTracker integration (A1) — only when available
# ---------------------------------------------------------------------------


class TestTieredTrailingIntegration:
    def test_tracker_constructed_when_opted_in(self) -> None:
        """use_tiered_trailing=True + A1 available → tracker constructed."""
        try:
            from selene_space_paper.tiered_trailing_stop import (
                TieredTrailingStopTracker,
            )
        except ImportError:
            pytest.skip("A1 (tiered trailing stop) not installed")
        cfg = LimitPickConfig(use_tiered_trailing=True)
        strategy = LimitPickStrategy(cfg)
        assert strategy._tiered_tracker is not None
        assert isinstance(strategy._tiered_tracker, TieredTrailingStopTracker)

    def test_tracker_not_constructed_when_opted_out(self) -> None:
        """use_tiered_trailing=False → tracker is None (default)."""
        strategy = LimitPickStrategy()
        assert strategy._tiered_tracker is None
