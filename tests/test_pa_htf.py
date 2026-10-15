"""HTF context tests (B2, design review Phase 2).

Guarantees:
1. htf_summary aggregates a synthetic series correctly (structure fields only)
2. None htf_summary injects nothing (existing prompts byte-unchanged)
3. A set htf_summary injects 【高周期背景】 after the strategy block,
   before the closing instruction
4. Setting htf_summary leaves the shared system prompt byte-identical
5. the CLI's --htf-interval defaults to None (via the real build_parser)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in [
    REPO / "packages" / "decision-workflow",
    REPO / "packages" / "shared-types",
    REPO / "packages" / "core",
    REPO / "packages" / "security-gateway",
]:
    sys.path.insert(0, str(p))

from selene_space_decision_workflow.orchestrator import TwoStageWorkflow  # noqa: E402
from selene_space_decision_workflow.parser import parse_decision_tree  # noqa: E402
from selene_space_decision_workflow.program_nodes import ProgramNodeRegistry  # noqa: E402
from selene_space_decision_workflow.routing import RoutingTable  # noqa: E402
from selene_space_decision_workflow.validator import ValidatorConfig  # noqa: E402

sys.path.insert(0, str(REPO / "scripts"))

TREE_PATH = (
    REPO
    / "packages"
    / "decision-workflow"
    / "selene_space_decision_workflow"
    / "decision_trees"
    / "trading_decision.txt"
)


def _make_workflow(htf_summary: dict | None) -> TwoStageWorkflow:
    tree_text = TREE_PATH.read_text(encoding="utf-8")
    terminals = frozenset(re.findall(r"trade|wait|reject|proceed", tree_text))
    graph = parse_decision_tree(tree_text, allowed_terminals=terminals)
    return TwoStageWorkflow(
        llm=None,
        parser_graph=graph,
        validator_config=ValidatorConfig(),
        routing_table=RoutingTable({}),
        program_registry=ProgramNodeRegistry(),
        persistence=None,
        tree_text=tree_text,
        global_rules=["rule1"],
        htf_summary=htf_summary,
    )


def _stage2_prompt(wf: TwoStageWorkflow) -> str:
    return wf._build_stage2_user_prompt(
        input_data={"x": 1},
        diagnosis_summary={"state": "range"},
        strategy_files=[],
    )


# ------------------------------------------------------------- aggregation


def _synthetic_bars(n: int) -> list[dict]:
    """Rising series: bar i has open=100+i, close=101+i, h=102+i, l=99+i."""
    return [
        {
            "ts": f"2026-01-01T{i:02d}:00:00+00:00",
            "open": 100.0 + i,
            "high": 102.0 + i,
            "low": 99.0 + i,
            "close": 101.0 + i,
            "volume": 10.0,
        }
        for i in range(n)
    ]


def test_htf_summary_aggregates_synthetic_bars() -> None:
    from run_pa_diagnosis import htf_summary

    bars = _synthetic_bars(60)
    s = htf_summary(bars, "BTC/USDT", "4h")
    assert s["symbol"] == "BTC/USDT"
    assert s["interval"] == "4h"
    assert s["bars_total"] == 60
    assert s["window_start_iso"] == bars[0]["ts"]
    assert s["window_end_iso"] == bars[-1]["ts"]
    assert s["last_close"] == 160.0  # close of bar 59
    assert s["range_high_20"] == 102.0 + 59  # max of last 20 highs
    assert s["range_low_20"] == 99.0 + 40  # min of last 20 lows
    # Rising series with constant +1 range: every TR is exactly 3.0.
    assert s["atr14"] == pytest.approx(3.0)


def test_htf_summary_short_series_atr_fallback() -> None:
    from run_pa_diagnosis import htf_summary

    bars = _synthetic_bars(5)
    s = htf_summary(bars, "BTC/USDT", "4h")
    # trs covers bars 1..4 (4 entries), min(14, 4) denominator.
    assert s["atr14"] == pytest.approx(3.0)
    # 20-bar range falls back to whatever exists.
    assert s["range_high_20"] == 102.0 + 4
    assert s["range_low_20"] == 99.0


# --------------------------------------------------------------- injection


def test_none_htf_injects_nothing() -> None:
    wf = _make_workflow(None)
    prompt = _stage2_prompt(wf)
    assert "【高周期背景】" not in prompt
    # Nothing injected: the strategy block runs straight into the closing
    # instruction. An unconditional JSON Schema block is appended *after* that
    # instruction (orchestrator._build_stage2_user_prompt), so the prompt no
    # longer ends with the instruction — assert on position instead.
    assert prompt.index("【可用策略文件】") < prompt.index(
        "请基于 Stage 1 诊断进行 Stage 2 决策"
    )


def test_htf_injects_after_strategy_before_closing() -> None:
    wf = _make_workflow({"interval": "4h", "last_close": 160.0})
    prompt = _stage2_prompt(wf)
    assert "【高周期背景】" in prompt
    assert '"last_close": 160.0' in prompt
    assert prompt.index("【可用策略文件】") < prompt.index("【高周期背景】")
    assert prompt.index("【高周期背景】") < prompt.index(
        "请基于 Stage 1 诊断进行 Stage 2 决策"
    )


def test_htf_leaves_system_prompt_bytes_identical() -> None:
    wf_plain = _make_workflow(None)
    wf_htf = _make_workflow({"interval": "4h", "last_close": 160.0})
    assert wf_plain._system_prompt == wf_htf._system_prompt


def test_cli_htf_interval_defaults_to_none() -> None:
    from run_pa_diagnosis import build_parser

    args = build_parser().parse_args([])
    assert args.htf_interval is None


def test_thin_htf_series_degrades_instead_of_aborting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_bars raises for a thin primary window, but the HTF fetch passes
    min_bars=0 so a thin HTF series degrades to the warning branch (architect
    REJECT fix: the >=50 check must be reachable, not dead code)."""
    import sqlite3

    import run_pa_diagnosis as rpd

    db_path = tmp_path / "klines.db"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE klines (timestamp TEXT, open REAL, high REAL, low REAL,"
        " close REAL, volume REAL, exchange TEXT, market_type TEXT,"
        " symbol TEXT, interval TEXT, closed INTEGER)"
    )
    db.execute(
        "INSERT INTO klines VALUES ('2026-01-01T00:00:00+00:00', 1, 2, 0.5, 1.5,"
        " 10, 'binance', 'futures', 'BTC/USDT', '4h', 1)"
    )
    db.commit()
    db.close()
    monkeypatch.setattr(rpd, "KLINES_DB", db_path)

    # Default threshold: 1 row < 50 → raise (primary-window contract intact).
    with pytest.raises(RuntimeError, match="insufficient bars"):
        rpd.load_bars("BTC/USDT", "4h", 120)
    # HTF fetch shape: min_bars=0 never raises regardless of row count.
    assert len(rpd.load_bars("BTC/USDT", "4h", 120, min_bars=0)) == 1
    assert rpd.load_bars("NOPE/USDT", "4h", 120, min_bars=0) == []
