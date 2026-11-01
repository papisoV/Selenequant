"""Behaviour locks for the market-context prompt module.

Covers: canonical key resolution from Selene's ``StockAsset.market`` strings
and common aliases; the positive-focus + negative-guardrail content for every
market and both languages; and the fail-open contract for unknown markets.
"""

from __future__ import annotations

import pytest

from selene_space_types.market_context import (
    MarketGuidance,
    market_guidance,
    render_market_guidance,
    resolve_market,
)


class TestResolveMarket:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("US", "US"),
            ("us", "US"),
            ("CN", "CN"),
            ("HK", "HK"),
            ("CRYPTO", "CRYPTO"),
            ("ashare", "CN"),
            ("a_share", "CN"),
            ("kospi", "KR"),
            ("taiwan", "TW"),
            ("spot", "CRYPTO"),
            ("futures", "CRYPTO"),
            ("hong-kong", "HK"),
        ],
    )
    def test_known_markets_resolve(self, raw: str, expected: str) -> None:
        assert resolve_market(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "atlantis", "MARS"])
    def test_unknown_markets_return_none(self, raw: str | None) -> None:
        assert resolve_market(raw) is None


class TestMarketGuidance:
    @pytest.mark.parametrize("market", ["CN", "HK", "US", "JP", "KR", "TW", "CRYPTO"])
    @pytest.mark.parametrize("lang", ["zh", "en"])
    def test_every_market_has_focus_and_avoid(self, market: str, lang: str) -> None:
        guidance = market_guidance(market, lang)
        assert isinstance(guidance, MarketGuidance)
        assert guidance.focus, f"{market}/{lang} has no positive focus"
        assert guidance.avoid, f"{market}/{lang} has no negative guardrail"

    def test_unknown_market_has_no_guidance(self) -> None:
        assert market_guidance("atlantis", "en") is None
        assert market_guidance(None, "en") is None

    def test_non_zh_language_falls_back_to_en(self) -> None:
        assert market_guidance("US", "ko") == market_guidance("US", "en")
        assert market_guidance("US", "") == market_guidance("US", "en")

    def test_ashare_guard_names_crypto_concepts(self) -> None:
        avoid = " ".join(market_guidance("CN", "en").avoid).lower()
        assert "funding rate" in avoid
        assert "perpetual" in avoid

    def test_foreign_market_guard_excludes_ashare_concepts(self) -> None:
        for market in ("JP", "KR", "US"):
            avoid = " ".join(market_guidance(market, "en").avoid).lower()
            assert "northbound" in avoid or "price limit" in avoid

    def test_crypto_guard_excludes_equity_concepts(self) -> None:
        avoid = " ".join(market_guidance("CRYPTO", "en").avoid).lower()
        assert "price limit" in avoid
        assert "t+1" in avoid


class TestRenderMarketGuidance:
    def test_english_block_structure(self) -> None:
        text = render_market_guidance("US", "en")
        assert text.startswith("## Market Context")
        assert "Do NOT apply" in text
        assert "US stock" in text

    def test_chinese_block_structure(self) -> None:
        text = render_market_guidance("CN", "zh")
        assert text.startswith("## 市场语境")
        assert "不要套用" in text
        assert "A 股" in text

    @pytest.mark.parametrize("raw", [None, "", "atlantis"])
    def test_unknown_market_renders_empty(self, raw: str | None) -> None:
        assert render_market_guidance(raw, "en") == ""
        assert render_market_guidance(raw, "zh") == ""

    def test_all_focus_and_avoid_items_appear(self) -> None:
        guidance = market_guidance("TW", "en")
        assert guidance is not None
        text = render_market_guidance("TW", "en")
        for item in guidance.focus:
            assert item in text
        for item in guidance.avoid:
            assert item in text
