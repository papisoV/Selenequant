"""SymbolNormalizer round-trips, quote detection, and registry behavior."""

from __future__ import annotations

import pytest

from selene_space_exchange.normalizers.symbol import SymbolNormalizer
from selene_space_types.errors import ValidationError


@pytest.fixture
def normalizer() -> SymbolNormalizer:
    return SymbolNormalizer()


# (raw, unified) pairs the binance rule must handle both ways.
BINANCE_CASES = [
    ("BTCUSDT", "BTC/USDT"),
    ("ETHBTC", "ETH/BTC"),
    ("SOLUSDT", "SOL/USDT"),
    ("BTCFDUSD", "BTC/FDUSD"),
    ("BNBETH", "BNB/ETH"),
    ("ETHUSDC", "ETH/USDC"),
]


class TestRawToUnified:
    @pytest.mark.parametrize(("raw", "unified"), BINANCE_CASES, ids=lambda v: v.replace("/", "_"))
    def test_quote_suffix_split(self, normalizer: SymbolNormalizer, raw: str, unified: str) -> None:
        assert normalizer.to_unified("binance", raw) == unified

    @pytest.mark.parametrize(("raw", "unified"), [c for c in BINANCE_CASES if c[0] == "BTCUSDT"])
    def test_lowercase_tolerated(self, normalizer: SymbolNormalizer, raw: str, unified: str) -> None:
        assert normalizer.to_unified("binance", raw.lower()) == unified

    def test_unknown_quote_asset_rejected(self, normalizer: SymbolNormalizer) -> None:
        with pytest.raises(ValidationError):
            normalizer.to_unified("binance", "XYZABC")


class TestUnifiedToRaw:
    @pytest.mark.parametrize(
        ("unified", "expected_raw"),
        [("BTC/USDT", "BTCUSDT"), ("ETH/BTC", "ETHBTC")],
        ids=["usdt-pair", "btc-pair"],
    )
    def test_join_without_separator(
        self, normalizer: SymbolNormalizer, unified: str, expected_raw: str
    ) -> None:
        assert normalizer.to_exchange("binance", unified) == expected_raw

    @pytest.mark.parametrize("already_raw", ["BTCUSDT"], ids=["no-slash-passthrough"])
    @pytest.mark.parametrize("lowered", ["btc/usdt"], ids=["case-folded"])
    def test_normalization_inputs(self, normalizer: SymbolNormalizer, already_raw: str, lowered: str) -> None:
        assert normalizer.to_exchange("binance", already_raw) == "BTCUSDT"
        assert normalizer.to_exchange("binance", lowered) == "BTCUSDT"


class TestRuleRegistry:
    def test_unregistered_exchange_rejected(self, normalizer: SymbolNormalizer) -> None:
        with pytest.raises(ValidationError, match="No symbol rule"):
            normalizer.to_unified("unknown_exchange", "BTCUSDT")

    @pytest.mark.parametrize("spelling", ["Binance", "BINANCE"], ids=["mixed", "upper"])
    def test_exchange_name_case_insensitive(self, normalizer: SymbolNormalizer, spelling: str) -> None:
        assert normalizer.to_unified(spelling, "BTCUSDT") == "BTC/USDT"

    def test_custom_rule_plugs_in_dash_convention(self, normalizer: SymbolNormalizer) -> None:
        class DashRule:
            def to_unified(self, raw_symbol: str) -> str:
                return raw_symbol.replace("-", "/")

            def to_exchange(self, unified_symbol: str) -> str:
                return unified_symbol.replace("/", "-")

        normalizer.register("okx", DashRule())
        assert normalizer.to_unified("okx", "BTC-USDT") == "BTC/USDT"
        assert normalizer.to_exchange("okx", "BTC/USDT") == "BTC-USDT"
