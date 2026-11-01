"""Unit tests for grid sentinel tools and chat control tools.

Tests cover:
  - _extract_json: tolerant JSON extraction from LLM output
  - _build_snapshot: compact state builder from engine instances
  - GridSentinelTool: risk patrol with mock LLM
  - GridMarketAnalysisTool: multi-timeframe analysis with mock LLM
  - GridDailyReportTool: daily report with mock LLM
  - GridOutOfRangeAdviceTool: OOR advice with mock LLM
  - GridChatControlTool: chat control with whitelist enforcement
  - ActionProposal: to_dict round-trip
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from selene_space_agent.tools.sentinel_tools import (
    GridSentinelTool,
    GridMarketAnalysisTool,
    GridDailyReportTool,
    GridOutOfRangeAdviceTool,
    _extract_json,
    _build_snapshot,
)
from selene_space_agent.tools.grid_control_tools import (
    GridChatControlTool,
    ActionProposal,
    _ALLOWED_ACTIONS,
    _ALLOWED_EXCHANGES,
)
from selene_space_types.risk import RiskLevel


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_simple_object(self) -> None:
        assert _extract_json('{"a":1}') == {"a": 1}

    def test_embedded_in_text(self) -> None:
        assert _extract_json('result: {"a":1} done') == {"a": 1}

    def test_code_block_wrapped(self) -> None:
        text = '```json\n{"a":1}\n```'
        assert _extract_json(text) == {"a": 1}

    def test_nested_object(self) -> None:
        assert _extract_json('{"outer":{"inner":2}}') == {"outer": {"inner": 2}}

    def test_no_json(self) -> None:
        assert _extract_json("no json here") is None

    def test_string_with_braces(self) -> None:
        text = '{"key": "value with {curly} braces"}'
        result = _extract_json(text)
        assert result is not None
        assert result["key"] == "value with {curly} braces"

    def test_trailing_text(self) -> None:
        text = '{"a":1} some trailing text'
        assert _extract_json(text) == {"a": 1}

    def test_invalid_json_structure(self) -> None:
        assert _extract_json("{not valid json}") is None

    def test_array_is_not_extracted(self) -> None:
        # Only objects ({}), not arrays
        assert _extract_json("[1,2,3]") is None

    def test_chinese_content(self) -> None:
        text = '{"level":"warn","summary":"保证金不足"}'
        result = _extract_json(text)
        assert result is not None
        assert result["level"] == "warn"
        assert result["summary"] == "保证金不足"


# ---------------------------------------------------------------------------
# _build_snapshot
# ---------------------------------------------------------------------------


class TestBuildSnapshot:
    def test_empty_engines(self) -> None:
        snap = _build_snapshot({})
        assert set(snap.keys()) == {"de", "ex", "rs"}
        for key in ("de", "ex", "rs"):
            assert snap[key]["running"] is False

    def test_with_engine(self) -> None:
        # Build a mock engine that returns a dict-like state without
        # importing the full paper-engine chain (which has a pre-existing
        # import issue with RiskAlertConfig in risk-engine).
        state = MagicMock()
        state.running = True
        state.recovery = False
        state.config = MagicMock()
        state.config.display_name = "BTC-USDT"
        state.config.mode = "neutral"
        state.config.lower = 95000.0
        state.config.upper = 105000.0
        state.config.grid_count = 10
        state.config.size_base = 100.0
        state.config.leverage = 5
        state.config.out_of_range_action = "close"
        state.last_price = 100000.0
        state.equity = 10500.0
        state.balance = 10000.0
        state.realized_pnl = 50.0
        state.unrealized_pnl = -20.0
        state.return_pct = 0.29
        state.out_of_range = False
        state.open_orders = 5
        state.exchange_open_orders = 5
        state.stats.completed_rungs = 3
        state.volume = 1500.0
        state.health = MagicMock()
        state.health.status = "ok"
        state.health.reason = ""

        eng = MagicMock()
        eng.get_state.return_value = state
        eng._ex = MagicMock()
        eng._ex.trade_mode = "paper"

        snap = _build_snapshot({"ex": eng})
        assert snap["ex"]["running"] is True
        assert snap["ex"]["equity"] == 10500.0
        assert snap["ex"]["gridConfig"]["mode"] == "neutral"
        assert snap["ex"]["gridConfig"]["lower"] == 95000.0


# ---------------------------------------------------------------------------
# Mock LLM helper
# ---------------------------------------------------------------------------


def _make_mock_llm(response_text: str) -> AsyncMock:
    """Create a mock LLM provider that returns the given text on chat()."""
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=response_text)
    return llm


# ---------------------------------------------------------------------------
# GridSentinelTool
# ---------------------------------------------------------------------------


class TestGridSentinelTool:
    def test_properties(self) -> None:
        tool = GridSentinelTool({}, _make_mock_llm(""))
        assert tool.name == "grid_sentinel"
        assert tool.risk_level == RiskLevel.SAFE

    def test_sync_execute_returns_error(self) -> None:
        tool = GridSentinelTool({}, _make_mock_llm(""))
        result = tool.execute({})
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_async_execute_success(self) -> None:
        response = json.dumps({
            "overall": {"level": "ok", "summary": "一切正常"},
            "per": {
                "de": {"level": "ok", "summary": "未运行", "advice": ""},
                "ex": {"level": "ok", "summary": "运行正常", "advice": ""},
                "rs": {"level": "ok", "summary": "未运行", "advice": ""},
            },
        })
        tool = GridSentinelTool({}, _make_mock_llm(response))
        result = await tool.async_execute({})
        assert result is not None
        assert "OK" in result.output
        assert "一切正常" in result.output

    @pytest.mark.asyncio
    async def test_async_execute_warn(self) -> None:
        response = json.dumps({
            "overall": {"level": "warn", "summary": "保证金偏低"},
            "per": {"de": {"level": "ok", "summary": "正常", "advice": ""}},
        })
        tool = GridSentinelTool({}, _make_mock_llm(response))
        result = await tool.async_execute({})
        assert "WARN" in result.output

    @pytest.mark.asyncio
    async def test_async_execute_llm_failure(self) -> None:
        llm = AsyncMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("API down"))
        tool = GridSentinelTool({}, llm)
        result = await tool.async_execute({})
        assert result is not None
        assert result.error is not None


# ---------------------------------------------------------------------------
# GridMarketAnalysisTool
# ---------------------------------------------------------------------------


class TestGridMarketAnalysisTool:
    def test_properties(self) -> None:
        tool = GridMarketAnalysisTool({}, _make_mock_llm(""))
        assert tool.name == "grid_market_analysis"

    @pytest.mark.asyncio
    async def test_unknown_exchange(self) -> None:
        tool = GridMarketAnalysisTool({}, _make_mock_llm(""))
        result = await tool.async_execute({"exchange": "unknown"})
        assert result.error is not None


# ---------------------------------------------------------------------------
# GridDailyReportTool
# ---------------------------------------------------------------------------


class TestGridDailyReportTool:
    def test_rebaseline(self) -> None:
        tool = GridDailyReportTool({}, _make_mock_llm(""))
        tool.rebaseline()
        assert tool._baseline is not None
        assert "per" in tool._baseline

    @pytest.mark.asyncio
    async def test_report_success(self) -> None:
        tool = GridDailyReportTool({}, _make_mock_llm("日报：三所均正常运行，无风险点。"))
        tool.rebaseline()
        result = await tool.async_execute({})
        assert result is not None
        assert "日报" in result.output


# ---------------------------------------------------------------------------
# GridOutOfRangeAdviceTool
# ---------------------------------------------------------------------------


class TestGridOutOfRangeAdviceTool:
    def test_properties(self) -> None:
        tool = GridOutOfRangeAdviceTool({}, _make_mock_llm(""))
        assert tool.name == "grid_oor_advice"
        assert tool.risk_level == RiskLevel.SAFE

    @pytest.mark.asyncio
    async def test_unknown_exchange(self) -> None:
        tool = GridOutOfRangeAdviceTool({}, _make_mock_llm(""))
        result = await tool.async_execute({"exchange": "unknown"})
        assert result.error is not None


# ---------------------------------------------------------------------------
# GridChatControlTool
# ---------------------------------------------------------------------------


class TestGridChatControlTool:
    def test_properties(self) -> None:
        tool = GridChatControlTool({}, _make_mock_llm(""))
        assert tool.name == "grid_chat_control"
        assert tool.risk_level == RiskLevel.RESTRICTED

    @pytest.mark.asyncio
    async def test_missing_message(self) -> None:
        tool = GridChatControlTool({}, _make_mock_llm(""))
        result = await tool.async_execute({})
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_whitelist_enforcement(self) -> None:
        response = json.dumps({
            "reply": "好的，我来帮你",
            "action": {"type": "hack_the_mainframe", "exchange": "de", "params": {}},
        })
        tool = GridChatControlTool({}, _make_mock_llm(response))
        result = await tool.async_execute({"message": "hack it"})
        # Unknown action should be forced to "none"
        assert "none" in result.output.lower() or "提议操作" not in result.output

    @pytest.mark.asyncio
    async def test_invalid_exchange_enforcement(self) -> None:
        response = json.dumps({
            "reply": "Stopping grid",
            "action": {"type": "stop_grid", "exchange": "unknown", "params": {}},
        })
        tool = GridChatControlTool({}, _make_mock_llm(response))
        result = await tool.async_execute({"message": "stop grid"})
        # Invalid exchange should force action to none
        assert "提议操作" not in result.output or "none" in result.output.lower()

    @pytest.mark.asyncio
    async def test_valid_action_proposal(self) -> None:
        response = json.dumps({
            "reply": "好的，我来停止网格",
            "action": {"type": "stop_grid", "exchange": "de", "params": {"closePosition": True}},
        })
        tool = GridChatControlTool({}, _make_mock_llm(response))
        result = await tool.async_execute({"message": "停止Decibel网格"})
        assert "提议操作" in result.output
        assert "stop_grid" in result.output

    @pytest.mark.asyncio
    async def test_llm_failure(self) -> None:
        llm = AsyncMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("timeout"))
        tool = GridChatControlTool({}, llm)
        result = await tool.async_execute({"message": "hello"})
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_no_json_response(self) -> None:
        tool = GridChatControlTool({}, _make_mock_llm("I don't understand the question"))
        result = await tool.async_execute({"message": "hello"})
        # Should handle gracefully — raw text returned
        assert result is not None


# ---------------------------------------------------------------------------
# ActionProposal
# ---------------------------------------------------------------------------


class TestActionProposal:
    def test_to_dict(self) -> None:
        p = ActionProposal("test", "stop_grid", "de", {"closePosition": True})
        d = p.to_dict()
        assert d["reply"] == "test"
        assert d["action"]["type"] == "stop_grid"
        assert d["action"]["exchange"] == "de"
        assert d["action"]["params"] == {"closePosition": True}

    def test_defaults(self) -> None:
        p = ActionProposal("hello")
        assert p.action_type == "none"
        assert p.exchange == ""
        assert p.params == {}

    def test_allowed_actions_set(self) -> None:
        expected = {"adjust_range", "stop_grid", "cancel_orders", "close_position",
                     "reconnect", "start_recovery", "start_grid", "none"}
        assert _ALLOWED_ACTIONS == expected

    def test_allowed_exchanges_set(self) -> None:
        assert _ALLOWED_EXCHANGES == {"de", "ex", "rs"}
