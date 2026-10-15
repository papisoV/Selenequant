"""Tests for StrategyFailureMemory + StrategyValidateTool write-side wiring."""

from __future__ import annotations

import json
from pathlib import Path

from selene_space_agent.memory.strategy_failures import StrategyFailureMemory
from selene_space_agent.tools.strategy_tools import StrategyValidateTool


def _memory(tmp_path: Path) -> StrategyFailureMemory:
    return StrategyFailureMemory(memory_dir=tmp_path)


def _issue(code: str = "unknown_indicator", field: str = "entry_rules") -> dict:
    return {
        "code": code,
        "field_path": field,
        "message": "bad thing",
        "recovery": "fetch",
    }


def test_record_then_recall_hits(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    mem.record(
        {"strategy_type": "rsi_reversal", "symbols": ["BTC/USDT"], "interval": "1h"},
        [_issue()],
    )
    out = mem.recall("why does rsi_reversal on BTC/USDT fail")
    assert "rsi_reversal" in out
    assert "unknown_indicator" in out


def test_record_then_recall_miss_gives_empty(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    mem.record(
        {"strategy_type": "rsi_reversal", "symbols": ["BTC/USDT"], "interval": "1h"},
        [_issue()],
    )
    assert mem.recall("macd crossover ETH") == ""


def test_fifo_cap_50(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    for i in range(55):
        mem.record(
            {"strategy_type": f"type_{i}", "symbols": [], "interval": "1h"},
            [_issue()],
        )
    raw = json.loads((tmp_path / "strategy_failures.json").read_text(encoding="utf-8"))
    assert len(raw["entries"]) == 50
    assert raw["entries"][0]["strategy_type"] == "type_5"


def test_corrupt_json_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "strategy_failures.json").write_text("not json{", encoding="utf-8")
    mem = _memory(tmp_path)
    assert mem._entries == []
    assert mem.recall("anything") == ""


def test_validate_failure_triggers_record(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    tool = StrategyValidateTool(failure_memory=mem)
    tool.execute({"config": {"strategy_type": "sma_cross", "symbols": "BTC/USDT"}})
    assert len(mem._entries) >= 1


def test_validate_success_does_not_record(tmp_path: Path) -> None:
    mem = _memory(tmp_path)
    tool = StrategyValidateTool(failure_memory=mem)
    tool.execute(
        {
            "config": {
                "id": "t1",
                "name": "t",
                "type": "sma_cross",
                "symbols": ["BTC/USDT"],
                "interval": "15m",
                "parameters": {"fast_period": 10, "slow_period": 30},
                "parsed_entry_rules": {"long": []},
                "parsed_exit_rules": {"close_long": []},
            }
        }
    )
    assert mem._entries == []
