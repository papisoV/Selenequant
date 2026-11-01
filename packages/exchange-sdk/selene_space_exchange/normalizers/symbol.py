"""Symbol conversion between venue-native and unified BASE/QUOTE forms."""

from __future__ import annotations

import logging
import re
from typing import Protocol, runtime_checkable

from selene_space_types.errors import ValidationError

logger = logging.getLogger(__name__)


@runtime_checkable
class ExchangeSymbolRule(Protocol):
    def to_unified(self, raw_symbol: str) -> str: ...
    def to_exchange(self, unified_symbol: str) -> str: ...


class _BinanceSymbolRule:
    """BTCUSDT -> BTC/USDT by longest-suffix quote match."""

    KNOWN_QUOTES = ("FDUSD", "USDT", "BUSD", "USDC", "TUSD", "BTC", "ETH", "BNB")

    def to_unified(self, raw_symbol: str) -> str:
        token = raw_symbol.strip().upper()
        for quote in self.KNOWN_QUOTES:
            if token.endswith(quote):
                base = token[: -len(quote)]
                if base:
                    return f"{base}/{quote}"
        raise ValidationError(f"unrecognised Binance symbol: {raw_symbol!r}")

    def to_exchange(self, unified_symbol: str) -> str:
        token = unified_symbol.strip().upper()
        return token.replace("/", "")


class _OKXSymbolRule:
    def to_unified(self, raw_symbol: str) -> str:
        parts = raw_symbol.split("-")
        if len(parts) == 2:
            return f"{parts[0].upper()}/{parts[1].upper()}"
        raise ValidationError(f"unrecognised OKX symbol: {raw_symbol!r}")

    def to_exchange(self, unified_symbol: str) -> str:
        halves = re.split(r"/", unified_symbol.strip().upper())
        if len(halves) != 2:
            raise ValidationError(f"expected BASE/QUOTE: {unified_symbol!r}")
        return "-".join(halves)


_BUILTINS: dict[str, ExchangeSymbolRule] = {
    "binance": _BinanceSymbolRule(),
    "okx": _OKXSymbolRule(),
}


class SymbolNormalizer:
    """Registry keyed by (case-insensitive) exchange name."""

    def __init__(self) -> None:
        self._rules: dict[str, ExchangeSymbolRule] = dict(_BUILTINS)

    def register(self, exchange: str, rule: ExchangeSymbolRule) -> None:
        self._rules[exchange.lower()] = rule

    def to_unified(self, exchange: str, raw_symbol: str) -> str:
        return self._get_rule(exchange).to_unified(raw_symbol)

    def to_exchange(self, exchange: str, unified_symbol: str) -> str:
        return self._get_rule(exchange).to_exchange(unified_symbol)

    def _get_rule(self, exchange: str) -> ExchangeSymbolRule:
        key = exchange.lower()
        if key not in self._rules:
            raise ValidationError(f"No symbol rule registered for {exchange!r}")
        return self._rules[key]
