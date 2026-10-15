"""Signal subscriber — converts alpha signals to trade intents.

Bridges the gap between AlphaFormulaStrategy Signal events and
execution-layer TradeIntent objects.
"""

from __future__ import annotations

from typing import Any


class SignalSubscriber:
    """Subscribes to alpha signals and converts them to trade intents.

    Args:
        min_confidence: Minimum confidence to act on a signal (0-1).
        min_signal: Minimum absolute signal strength to trade (0-1).
        notional_pct: Percentage of account equity per trade (0-1).
        account_equity: Total account equity for position sizing.
    """

    def __init__(
        self,
        min_confidence: float = 0.6,
        min_signal: float = 0.2,
        notional_pct: float = 0.05,
        account_equity: float = 10_000.0,
    ) -> None:
        self._min_confidence = min_confidence
        self._min_signal = min_signal
        self._notional_pct = notional_pct
        self._account_equity = account_equity

    @property
    def account_equity(self) -> float:
        return self._account_equity

    @account_equity.setter
    def account_equity(self, value: float) -> None:
        self._account_equity = value

    def convert_signal(
        self,
        symbol: str,
        signal_strength: float,
        confidence: float,
        current_price: float,
    ) -> dict[str, Any] | None:
        """Convert a single signal to a trade intent.

        Args:
            symbol: Trading pair.
            signal_strength: Directional alpha (-1 to 1, positive=long).
            confidence: Model confidence (0-1).
            current_price: Current market price.

        Returns:
            Trade intent dict, or None if filtered.
        """
        if confidence < self._min_confidence:
            return None

        if abs(signal_strength) < self._min_signal:
            return None

        side = "buy" if signal_strength > 0 else "sell"
        notional = self._notional_pct * self._account_equity
        quantity = notional / current_price if current_price > 0 else 0.0

        if quantity <= 0:
            return None

        return {
            "symbol": symbol,
            "side": side,
            "signal_strength": signal_strength,
            "confidence": confidence,
            "quantity": round(quantity, 8),
            "notional": round(notional, 2),
            "price": current_price,
        }

    def convert_batch(
        self,
        signals: list[dict[str, Any]],
        current_price: float = 100.0,
    ) -> list[dict[str, Any]]:
        """Convert a batch of signals, filtering out weak ones.

        Each signal dict should have: symbol, strength, confidence.
        """
        intents: list[dict[str, Any]] = []
        for sig in signals:
            intent = self.convert_signal(
                symbol=sig["symbol"],
                signal_strength=sig["strength"],
                confidence=sig["confidence"],
                current_price=current_price,
            )
            if intent is not None:
                intents.append(intent)
        return intents
