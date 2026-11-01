"""Time-Series Momentum with Volatility Targeting — 时序动量 + 波动率目标策略.

Absorbed from ``time-series-momentum-effect.py`` (Moskowitz–Ooi–Pedersen)
via academic-strategy-catalog. Pure price data.

Strategy logic (adapted to a single-asset binary position model):
  1. Momentum = trailing ``lookback``-bar return; its sign decides the
     direction (positive -> long, negative -> short) — the QC "12-month
     return" rule, expressed in bars instead of months.
  2. Position size is scaled by ``vol_target / realized_vol`` so the book
     de-risks when realized volatility is high — the QC inverse-volatility
     / vol-targeting rule, transferred to the sizing dimension.
  3. Exit when momentum decays back to ``exit_threshold``.

The QC source rebalances monthly, uses a 12*21-day lookback, a 60-day vol
window, a 10% target vol and a 4x leverage cap. In selene the leverage cap
maps to ``max_position_pct > 1``; this template only ever *reduces* size
below the configured ``position_size_pct`` (no lever-up), which is the
conservative transfer. Timestamps are not needed here — only closes.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType

_TARGET_VOL = 0.20
_LOOKBACK = 90
_VOL_LOOKBACK = 30


class TsmomVolTargetStrategy(BacktestStrategyTemplate):
    """Time-series momentum with volatility-targeted sizing.

    Goes long when the trailing ``lookback``-bar return is above
    ``entry_threshold`` and short when below ``-entry_threshold``; closes
    when momentum decays to ``exit_threshold``. Position size scales down
    with rising realized volatility (``vol_target / realized_vol``).

    Args:
        symbol: Trading pair.
        lookback: Momentum window in bars (e.g. 90 daily bars ~ 3 months).
        entry_threshold: Long when momentum > threshold; short when < -threshold.
        exit_threshold: Close when |momentum| decays to this level.
        vol_target: Annualized volatility target for position sizing.
        vol_lookback: Bars used to estimate realized volatility.
        enable_long: Allow long entries.
        enable_short: Allow short entries (sets base ``long_only`` accordingly).
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        lookback: int = _LOOKBACK,
        entry_threshold: float = 0.0,
        exit_threshold: float = 0.0,
        vol_target: float = _TARGET_VOL,
        vol_lookback: int = _VOL_LOOKBACK,
        enable_long: bool = True,
        enable_short: bool = False,
        **kwargs,
    ) -> None:
        # Direction is fully controlled by enable_long/enable_short, so the
        # base long_only flag is derived from enable_short.
        super().__init__(
            symbol,
            initial_capital,
            leverage,
            fee_rate,
            max_bars,
            long_only=not enable_short,
            **kwargs,
        )
        self.lookback = lookback
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.vol_target = vol_target
        self.vol_lookback = vol_lookback
        self.enable_long = enable_long
        self.enable_short = enable_short

    def get_parameters(self) -> dict[str, Any]:
        params = super().get_parameters()
        params.update(
            {
                "lookback": self.lookback,
                "entry_threshold": self.entry_threshold,
                "exit_threshold": self.exit_threshold,
                "vol_target": self.vol_target,
                "vol_lookback": self.vol_lookback,
                "enable_long": self.enable_long,
                "enable_short": self.enable_short,
            }
        )
        return params

    @classmethod
    def from_config(cls, config: EngineStrategyConfig) -> "TsmomVolTargetStrategy":
        """Construct from an EngineStrategyConfig (factory integration)."""
        if not config.symbols:
            raise ValueError("TsmomVolTargetStrategy requires >=1 symbol")
        params = config.parameters or {}
        return cls(
            symbol=config.symbols[0],
            lookback=int(params.get("lookback", _LOOKBACK)),
            entry_threshold=float(params.get("entry_threshold", 0.0)),
            exit_threshold=float(params.get("exit_threshold", 0.0)),
            vol_target=float(params.get("vol_target", _TARGET_VOL)),
            vol_lookback=int(params.get("vol_lookback", _VOL_LOOKBACK)),
            enable_long=bool(params.get("enable_long", True)),
            enable_short=bool(params.get("enable_short", False)),
        )

    def _momentum(self, data: pd.DataFrame) -> float:
        """Trailing ``lookback``-bar return (recent vs ``lookback`` bars ago)."""
        closes = data["close"]
        if len(closes) <= self.lookback:
            return 0.0
        start = closes.iloc[-1 - self.lookback]
        if start <= 0:
            return 0.0
        return float(closes.iloc[-1] / start - 1.0)

    def _realized_vol(self) -> float | None:
        """Annualized realized volatility over the last ``vol_lookback`` bars."""
        need = self.vol_lookback + 1
        if len(self._bars) < need:
            return None
        closes = pd.Series([b["close"] for b in self._bars[-need:]])
        rets = closes.pct_change().dropna()
        if len(rets) < 2:
            return None
        return float(rets.std(ddof=0)) * (252**0.5)

    def _compute_position_fraction(self) -> float:
        """Volatility-targeted sizing: scale down as realized vol rises."""
        base = min(self._position_size_pct, self._max_position_pct)
        realized = self._realized_vol()
        if realized is None or realized <= 0:
            return base
        scale = min(self.vol_target / realized, 1.0)  # de-risk only, no lever-up
        return min(base * scale, self._max_position_pct)

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        mom = self._momentum(data)

        if self._position == "flat":
            if self.enable_long and mom > self.entry_threshold:
                return SignalType.BUY
            if self.enable_short and mom < -self.entry_threshold:
                return SignalType.SELL
            return SignalType.HOLD

        if self._position == "long":
            if mom <= self.exit_threshold:
                return SignalType.CLOSE_LONG
            return SignalType.HOLD

        if self._position == "short":
            if mom >= -self.exit_threshold:
                return SignalType.CLOSE_SHORT
            return SignalType.HOLD

        return SignalType.HOLD
