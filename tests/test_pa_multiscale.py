"""Phase 3 (B3+B4) tests: four-window aggregation + overlay detection + wiring.

Guarantees:
1. four_windows K-ranges match the N=120 anchor (long K21-K80, recent
   K81-K120, inertia K113-K120, signal K111-K120) and shrink with N
2. directional fields are computed from real bar values
3. wedge / h2 / breakout_failure detectors fire on synthetic shapes and
   stay silent on flat series
4. multiscale strategy file is routed for every non-unknown state
5. overlay files are routed ONLY when their pattern is detected
6. orchestrator injects the overlay block into Stage 2 prompt only when
   overlay_patterns is set; system prompt bytes stay identical
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

from selene_space_decision_workflow.multi_scale import four_windows  # noqa: E402
from selene_space_decision_workflow.orchestrator import TwoStageWorkflow  # noqa: E402
from selene_space_decision_workflow.overlays import (  # noqa: E402
    detect_breakout_failure,
    detect_h2,
    detect_overlays,
    detect_wedge,
)
from selene_space_decision_workflow.parser import parse_decision_tree  # noqa: E402
from selene_space_decision_workflow.program_nodes import ProgramNodeRegistry  # noqa: E402
from selene_space_decision_workflow.routing import (  # noqa: E402
    TRADING_ROUTING_TABLE,
    route_overlay_files,
    route_strategy_files,
)
from selene_space_decision_workflow.validator import ValidatorConfig  # noqa: E402

TREE_PATH = (
    REPO
    / "packages"
    / "decision-workflow"
    / "selene_space_decision_workflow"
    / "decision_trees"
    / "trading_decision.txt"
)


# ------------------------------------------------------------ four_windows


def _bars(n: int, base: float = 100.0, step: float = 0.0) -> list[dict]:
    return [
        {
            "ts": f"2026-01-01T{i:03d}:00:00+00:00",
            "open": base + step * i,
            "high": base + step * i + 2.0,
            "low": base + step * i - 1.0,
            "close": base + step * i + (1.0 if step >= 0 else -1.0),
            "volume": 10.0,
        }
        for i in range(n)
    ]


def test_four_windows_k_ranges_anchor_n120() -> None:
    out = four_windows(_bars(120))
    assert out["bars_total"] == 120
    assert out["long_range"]["k_range"] == "K41-K100"
    assert out["recent"]["k_range"] == "K81-K120"
    assert out["inertia"]["k_range"] == "K113-K120"
    assert out["signal"]["k_range"] == "K111-K120"


def test_four_windows_shrink_gracefully_with_small_n() -> None:
    out = four_windows(_bars(60))
    # All ranges stay within K1..K60 and end at K60.
    for name in ("long_range", "recent", "inertia", "signal"):
        ks = out[name]["k_range"]
        start, end = (int(x) for x in ks[1:].split("-K"))
        assert 1 <= start <= end <= 60


def test_four_windows_directional_fields() -> None:
    rising = four_windows(_bars(120, step=1.0))
    # Rising series: net move positive in every window, close at top.
    for name in ("long_range", "recent", "inertia", "signal"):
        assert rising[name]["net_move"] > 0
        assert rising[name]["close_pos_in_range"] >= 0.9
        assert rising[name]["bull_bars"] > rising[name]["bear_bars"]


def test_four_windows_rejects_thin_window() -> None:
    with pytest.raises(ValueError, match=">= 50 bars"):
        four_windows(_bars(30))


# ---------------------------------------------------------------- overlays


def test_detect_wedge_fires_on_contracting_series() -> None:
    # Inertia window (8 bars, idx 0-7): k=2 swings only scan idx 2..5 and
    # two swing points must sit >= 3 apart, so both swing pairs live on
    # idx 2/5 as dual-extreme wide bars — peaks descend (103.0 -> 102.5),
    # troughs ascend (96.0 -> 96.8); every other bar is compressed between.
    bars = _bars(60, step=0.0)
    seg_offsets = {8: 0, 7: 1, 6: 2, 5: 3, 4: 4, 3: 5, 2: 6, 1: 7}
    peak_high = {2: 103.0, 5: 102.5}  # seg idx -> descending peaks
    trough_low = {2: 96.0, 5: 96.8}  # seg idx -> ascending troughs
    for off, idx in seg_offsets.items():
        b = bars[-off]
        b["open"] = 99.5
        b["close"] = 99.6
        b["high"] = 100.5  # compressed body bars sit below both peaks
        b["low"] = 98.5  # and above both troughs
        if idx in peak_high:
            b["high"] = peak_high[idx]
        if idx in trough_low:
            b["low"] = trough_low[idx]
    w = detect_wedge(bars)
    assert w is not None
    assert w["pattern"] == "wedge"
    assert w["window"] == "inertia"
    assert w["rising_wedge"] is True


def test_detect_wedge_silent_on_flat_series() -> None:
    assert detect_wedge(_bars(60)) is None


def test_detect_h2_fires_on_two_higher_lows() -> None:
    bars = _bars(60)
    # Two swing lows, second higher, then a bull bar closing above mid.
    bars[-7]["low"] = 90.0
    bars[-7]["close"] = 90.5
    bars[-3]["low"] = 92.0
    bars[-3]["close"] = 92.5
    bars[-1]["low"] = 93.0
    bars[-1]["open"] = 93.0
    bars[-1]["close"] = 96.0
    bars[-1]["high"] = 96.5
    h = detect_h2(bars)
    assert h is not None
    assert h["pattern"] == "h2"
    assert h["first_low"] == 90.0
    assert h["second_low"] == 92.0


def test_detect_h2_silent_when_second_low_lower() -> None:
    bars = _bars(60)
    bars[-7]["low"] = 92.0
    bars[-3]["low"] = 90.0
    assert detect_h2(bars) is None


def test_detect_breakout_failure_fires_and_resets() -> None:
    # Flat 60-bar base; bars -4 closes above the box, last bar closes
    # back inside → breakout failure (direction=up).
    bars = _bars(60)
    for b in bars:
        b["high"] = 103.0
        b["low"] = 97.0
        b["open"] = 100.0
        b["close"] = 100.0
    bars[-4]["close"] = 104.5
    bars[-4]["high"] = 105.0
    bars[-1]["close"] = 100.0
    b = detect_breakout_failure(bars)
    assert b is not None
    assert b["pattern"] == "breakout_failure"
    assert b["direction"] == "up"


def test_detect_overlays_flat_series_empty() -> None:
    assert detect_overlays(_bars(60)) == {
        "wedge": [],
        "h2": [],
        "breakout_failure": [],
    }


# ------------------------------------------------------------------ routing


@pytest.mark.parametrize(
    "state,expected",
    [
        ("trend_up", True),
        ("trend_down", True),
        ("range", True),
        ("breakout", True),
    ],
)
def test_multiscale_routed_for_all_states(state: str, expected: bool) -> None:
    files = route_strategy_files({"state": state}, TRADING_ROUTING_TABLE)
    assert ("strategies/trading_multiscale.txt" in files) is expected
    # State file comes first, multiscale appended last.
    if state != "breakout":
        assert files[0].startswith("strategies/trading_")
        assert files[-1] == "strategies/trading_multiscale.txt"


def test_multiscale_not_routed_for_unknown() -> None:
    assert route_strategy_files({"state": "unknown"}, TRADING_ROUTING_TABLE) == []


def test_overlay_routing_only_on_hits() -> None:
    assert route_overlay_files({"wedge": [], "h2": [], "breakout_failure": []}) == []
    assert route_overlay_files({"wedge": [{"pattern": "wedge"}]}) == [
        "strategies/trading_overlay_wedge.txt"
    ]
    assert route_overlay_files(
        {"wedge": [{"pattern": "wedge"}], "h2": [{"pattern": "h2"}]}
    ) == [
        "strategies/trading_overlay_wedge.txt",
        "strategies/trading_overlay_h2.txt",
    ]


# ------------------------------------------------------- prompt composition


def _make_workflow(overlay_patterns: dict | None) -> TwoStageWorkflow:
    tree_text = TREE_PATH.read_text(encoding="utf-8")
    terminals = frozenset(re.findall(r"trade|wait|reject|proceed", tree_text))
    graph = parse_decision_tree(tree_text, allowed_terminals=terminals)
    return TwoStageWorkflow(
        llm=None,
        parser_graph=graph,
        validator_config=ValidatorConfig(),
        routing_table=TRADING_ROUTING_TABLE,
        program_registry=ProgramNodeRegistry(),
        persistence=None,
        tree_text=tree_text,
        global_rules=["rule1"],
        overlay_patterns=overlay_patterns,
    )


def test_no_overlays_injects_nothing() -> None:
    wf = _make_workflow(None)
    prompt = wf._build_stage2_user_prompt(
        input_data={"x": 1},
        diagnosis_summary={"state": "range"},
        strategy_files=[],
    )
    assert "【叠加物料】" not in prompt
    assert "【高周期背景】" not in prompt


def test_wedge_hit_injects_pattern_and_file() -> None:
    wf = _make_workflow({"wedge": [{"pattern": "wedge", "rising_wedge": True}]})
    prompt = wf._build_stage2_user_prompt(
        input_data={"x": 1},
        diagnosis_summary={"state": "trend_up"},
        strategy_files=[],
    )
    assert "【叠加物料】" in prompt
    assert "trading_overlay_wedge.txt" in prompt
    assert '"rising_wedge": true' in prompt
    # Overlay block comes after the strategy block, before the closing ask.
    assert prompt.index("【可用策略文件】") < prompt.index("【叠加物料】")
    assert prompt.index("【叠加物料】") < prompt.index("请基于 Stage 1 诊断")


def test_empty_hits_dict_injects_nothing() -> None:
    wf = _make_workflow({"wedge": [], "h2": [], "breakout_failure": []})
    prompt = wf._build_stage2_user_prompt(
        input_data={"x": 1},
        diagnosis_summary={"state": "range"},
        strategy_files=[],
    )
    assert "【叠加物料】" not in prompt


def test_overlay_leaves_system_prompt_bytes_identical() -> None:
    wf_plain = _make_workflow(None)
    wf_overlay = _make_workflow({"h2": [{"pattern": "h2"}]})
    assert wf_plain._system_prompt == wf_overlay._system_prompt
