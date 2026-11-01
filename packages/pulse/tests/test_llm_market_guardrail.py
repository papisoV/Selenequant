"""Behaviour locks for the market-context guardrail wired into pulse reports.

``summarize_with_llm`` builds an asset system prompt from ``asset.market``;
these tests capture the outgoing messages (no network) and assert the
market-specific focus/guardrail block is injected for a known market,
in the requested language, and omitted (fail-open) for an unknown one.
"""

from __future__ import annotations

import json

import pytest

from ai_market_pulse.config import LLMSettings
from ai_market_pulse.llm import _asset_system_prompt, summarize_with_llm
from ai_market_pulse.models import Asset, AssetAnalysis, PriceSnapshot, SignalScore


def _analysis(market: str, symbol: str = "ABC") -> AssetAnalysis:
    return AssetAnalysis(
        asset=Asset(symbol=symbol, name="ABC Corp", market=market),
        snapshot=PriceSnapshot(
            symbol=symbol,
            name="ABC Corp",
            currency="USD",
            last_close=120,
            previous_close=115,
            change_pct=0.0435,
            start_date="2026-01-01",
            end_date="2026-07-07",
            rows=100,
        ),
        metrics={"return_20d": 0.1, "rsi14": 55},
        signal=SignalScore(
            score=60, stance="watch bullish", risk_level="low", reasons=["reason"]
        ),
    )


class _Capture:
    """Capture the last ``_chat_completion`` message list and return canned text."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] | None = None

    def __call__(self, messages: list[dict[str, str]], settings: LLMSettings) -> str:
        self.messages = messages
        return "summary"


class TestAssetSystemPrompt:
    def test_known_market_injects_guardrail(self) -> None:
        text = _asset_system_prompt(_analysis("CN"), LLMSettings(), "en")
        assert "## Market Context" in text
        assert "Do NOT apply" in text

    def test_language_selects_block(self) -> None:
        zh = _asset_system_prompt(_analysis("CN"), LLMSettings(), "zh")
        assert "## 市场语境" in zh
        assert "不要套用" in zh

    def test_unknown_market_leaves_base_prompt(self) -> None:
        text = _asset_system_prompt(_analysis("MARS"), LLMSettings(), "en")
        assert "## Market Context" not in text
        assert "Do NOT apply" not in text

    def test_crypto_market_guardrails(self) -> None:
        text = _asset_system_prompt(
            _analysis("CRYPTO", "BTC/USDT"), LLMSettings(), "en"
        )
        assert "funding rate" in text
        assert "24/7" in text


class TestSummarizeWiring:
    def test_summarize_injects_guardrail_into_system_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture = _Capture()
        monkeypatch.setattr("ai_market_pulse.llm._chat_completion", capture)

        result = summarize_with_llm(_analysis("CN"), LLMSettings(), "en")

        assert result == "summary"
        assert capture.messages is not None
        system = capture.messages[0]
        assert system["role"] == "system"
        assert "## Market Context" in system["content"]

    def test_summarize_unknown_market_omits_guardrail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        capture = _Capture()
        monkeypatch.setattr("ai_market_pulse.llm._chat_completion", capture)

        summarize_with_llm(_analysis("MARS"), LLMSettings(), "en")

        assert capture.messages is not None
        assert "## Market Context" not in capture.messages[0]["content"]


class TestAssetContextUnchanged:
    """The JSON context payload must still carry the asset's market field."""

    def test_context_includes_market(self) -> None:
        from ai_market_pulse.llm import _asset_context

        context = _asset_context(_analysis("CN"), "en")
        assert json.loads(json.dumps(context["asset"]))["market"] == "CN"
