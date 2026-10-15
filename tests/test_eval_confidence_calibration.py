"""Pure-function tests for the confidence × outcome emit-yield audit.

Covers the Phase A acceptance criteria: empty corpus renders zero-filled and
never raises; the coverage denominator discriminates the decision-workflow
record species from market-analysis records; the any-path key scan separates
"channel never wrote a value" from "wrong accessor"; the reliability table and
the counterfactual sweep reproduce hand-computed numbers; ``pnl_r is None`` is
never coerced to 0.

No I/O: every case drives ``build_calibration_report`` with in-memory records.
The market-analysis-shaped fixture mirrors the real species found in the
persisted corpus (``signal_patterns[].confidence``); the decision-workflow
fixture mirrors the contract path pinned by ``tests/test_pa_confidence.py``.
"""

from __future__ import annotations

import pytest

from selene_space_decision_workflow.eval_confidence_calibration import (
    build_calibration_report,
    confidence_of,
    gate_result_of,
    outcome_of,
)


def _dw(confidence=None, status=None, pnl_r=None, *, gate="proceed"):
    """A record in the REAL persisted shape (StageRecord / Stage2Result dumps).

    The gate is deliberately present at BOTH persisted locations: `response`
    carries the authoritative validated Stage1Result dump (orchestrator.py:142,
    read by the gate branch at :113), and `diagnosis_or_decision` carries the
    LLM-written diagnosis_summary (orchestrator.py:143) which usually — but not
    always — echoes the gate. A fixture with a flat
    ``stage1 = {"gate_result": ...}`` would certify a read path no writer emits.
    """
    summary: dict = {}
    if confidence is not None:
        summary["confidence"] = confidence
    return {
        "meta": {"timestamp": "2026-09-12T00:00:00+00:00"},
        "stage1": {
            "messages": [],
            "response": {"state": "trend_up", "gate_result": gate},
            "trace": [],
            "diagnosis_or_decision": {"state": "trend_up", "gate_result": gate},
        },
        "stage2": {
            "messages": [],
            "response": {},
            "trace": [],
            "diagnosis_or_decision": {"decision": "trade", "decision_summary": summary},
        },
        "outcome": None if status is None else {"status": status, "pnl_r": pnl_r},
    }


def _market_analysis(confidences=(0.6, 0.4)):
    """A record of the other species: confidence under signal_patterns[]."""
    return {
        "stage1": {
            "signal_patterns": [{"confidence": c} for c in confidences],
        },
        "stage2": {"decision": "wait", "next_bar_forecast": {"direction": "up"}},
        "input_data": {"klines": [{"close": 1.0}]},
    }


# --- coverage / shape discrimination ----------------------------------------


def test_empty_corpus_is_zero_filled_and_never_raises():
    report = build_calibration_report([])
    cov = report["coverage"]
    assert cov["total"] == 0
    assert cov["total_dw"] == 0
    assert cov["with_stage2"] == 0
    assert cov["excluded_by_shape"] == 0
    assert cov["confidence_key_present_anywhere"] == 0
    assert len(report["reliability"]) == 4
    assert all(row["n"] == 0 for row in report["reliability"])
    assert all(row["win_rate"] is None for row in report["reliability"])
    assert report["counterfactual_sweep"] == []
    assert report["note"] is not None  # zero-filled -> reachability label


def test_market_analysis_species_is_excluded_by_shape():
    report = build_calibration_report([_market_analysis()])
    cov = report["coverage"]
    assert cov["total"] == 1
    assert cov["total_dw"] == 0
    assert cov["excluded_by_shape"] == 1
    assert cov["with_stage2"] == 0  # pinned := total_dw, never the raw count


def test_record_without_stage2_key_does_not_crash():
    report = build_calibration_report([{"meta": {}}, {"stage1": {}}])
    cov = report["coverage"]
    assert cov["total"] == 2
    assert cov["excluded_by_shape"] == 2
    assert cov["with_confidence"] == 0


@pytest.mark.parametrize("bad_stage2", ["x", 3, ["a"], True])
def test_non_dict_stage2_does_not_crash(bad_stage2):
    report = build_calibration_report([{"stage2": bad_stage2}])
    assert report["coverage"]["excluded_by_shape"] == 1
    assert report["coverage"]["with_confidence"] == 0


@pytest.mark.parametrize("bad_stage1", ["x", 3, ["a"], True])
def test_non_dict_stage1_does_not_crash(bad_stage1):
    """gate_result_of must not raise on a truthy non-dict stage1."""
    record = _dw(0.5, "win", 1.0)
    record["stage1"] = bad_stage1
    assert gate_result_of(record) is None
    report = build_calibration_report([record])
    assert report["counterfactual_sweep"] == []  # no readable gate -> not proceed


def test_flat_stage2_is_not_dw_shape():
    """A gate short-circuit writes a flat stage2 dict — no contract path."""
    report = build_calibration_report(
        [
            {
                "stage2": {
                    "decision": "wait",
                    "decision_summary": {"short_circuited": True},
                }
            }
        ]
    )
    assert report["coverage"]["total_dw"] == 0
    assert report["coverage"]["excluded_by_shape"] == 1


# --- any-path key scan -------------------------------------------------------


def test_key_scan_separates_unwritten_from_wrong_path():
    """The corpus carries the literal key off-path: key scan > 0, contract 0."""
    report = build_calibration_report([_market_analysis(), _dw()])
    cov = report["coverage"]
    assert cov["confidence_key_present_anywhere"] == 1  # only the MA record
    assert cov["with_confidence"] == 0
    assert cov["with_stage2"] == 1  # the DW record is in the denominator
    assert report["note"] is not None


def test_top_level_confidence_is_not_the_contract_path():
    """Confidence at stage2's top level (dropped by extra='ignore') is off-path."""
    record = {
        "stage2": {
            "diagnosis_or_decision": {"decision_summary": {}},
            "confidence": 0.99,
        }
    }
    assert confidence_of(record) is None
    report = build_calibration_report([record])
    assert report["coverage"]["with_confidence"] == 0
    assert report["coverage"]["confidence_key_present_anywhere"] >= 1


# --- reliability table -------------------------------------------------------


def test_reliability_bins_are_hand_computed():
    records = [
        _dw(0.72, "win", 2.0),
        _dw(0.72, "loss", -1.0),
        _dw(0.72, "expired", None),
        _dw(0.30, "win", 1.5),
        _dw(0.90),  # confidence, no outcome
    ]
    report = build_calibration_report(records)
    bins = {row["bin"]: row for row in report["reliability"]}

    mid = bins["[0.5,0.75)"]
    assert mid["n"] == 3
    assert (mid["n_win"], mid["n_loss"], mid["n_pending"]) == (1, 1, 1)
    assert mid["n_no_outcome"] == 0
    assert mid["win_rate"] == pytest.approx(0.5)
    assert mid["mean_pnl_r"] == pytest.approx(0.5)  # (2.0 + -1.0)/2, expired out

    low = bins["[0.25,0.5)"]
    assert low["n"] == 1
    assert low["win_rate"] == pytest.approx(1.0)
    assert low["mean_pnl_r"] == pytest.approx(1.5)

    high = bins["[0.75,1.0]"]
    assert high["n"] == 1
    assert high["n_no_outcome"] == 1
    assert high["win_rate"] is None
    assert high["mean_pnl_r"] is None


def test_pnl_r_none_is_never_coerced_to_zero():
    """expired/timeout_open carry pnl_r None and stay out of the mean."""
    records = [
        _dw(0.6, "expired", None),
        _dw(0.6, "timeout_open", None),
        _dw(0.6, "win", 3.0),
    ]
    row = next(
        r
        for r in build_calibration_report(records)["reliability"]
        if r["bin"] == "[0.5,0.75)"
    )
    assert row["n"] == 3
    assert row["n_pending"] == 2
    assert row["n_win"] == 1
    assert row["mean_pnl_r"] == pytest.approx(3.0)  # not (3.0+0+0)/3


def test_outcome_of_ignores_non_dict():
    assert outcome_of({"outcome": None}) is None
    assert outcome_of({"outcome": "win"}) is None
    assert outcome_of({"outcome": {"status": "win"}}) == {"status": "win"}


# --- counterfactual sweep ----------------------------------------------------


def test_sweep_is_hand_computed_and_gate_filtered():
    records = [
        _dw(0.72, "win", 2.0),
        _dw(0.72, "loss", -1.0),
        _dw(0.30, "win", 1.5),
        _dw(0.90, "win", 2.0, gate="wait"),  # not proceed -> out of the universe
    ]
    sweep = build_calibration_report(records)["counterfactual_sweep"]
    assert [row["threshold"] for row in sweep] == [0.3, 0.72]

    lowest = sweep[0]
    assert lowest["total_win"] == 2  # the gate-blocked win is excluded
    assert lowest["total_loss"] == 1
    assert lowest["excluded_win"] == 0
    assert lowest["excluded_loss"] == 0

    at_072 = sweep[1]
    assert at_072["excluded_win"] == 1
    assert at_072["excluded_loss"] == 0
    assert at_072["win_share_excluded"] == pytest.approx(0.5)
    assert at_072["loss_share_excluded"] == pytest.approx(0.0)


def test_sweep_is_empty_without_outcomes():
    report = build_calibration_report([_dw(0.8), _dw(0.2)])
    assert report["counterfactual_sweep"] == []


def test_sweep_reads_the_persisted_gate_path():
    """Regression: the gate is read from persisted locations, not invented ones.

    Authoritative: stage1.response.gate_result (validated Stage1Result dump,
    guaranteed by the schema). Fallback: stage1.diagnosis_or_decision.gate_result
    (the LLM usually echoes it, but measured 2026-09-17 a BTC 4h record wrote
    gate_node/gate_reason and no gate_result there). A flat
    ``stage1 = {"gate_result": ...}`` is a shape no writer emits.
    """
    nested = _dw(0.7, "win", 2.0, gate="proceed")
    assert gate_result_of(nested) == "proceed"
    assert build_calibration_report([nested])["counterfactual_sweep"]  # non-empty

    # Response missing the gate, but the diagnosis echoes it -> fallback works.
    dod_only = _dw(0.7, "win", 2.0, gate="proceed")
    dod_only["stage1"]["response"] = {"state": "trend_up"}
    assert gate_result_of(dod_only) == "proceed"

    # The real BTC-4h shape: response has the gate, the summary does not
    # (it wrote gate_node/gate_reason instead). Primary path must win.
    resp_only = _dw(0.7, "win", 2.0, gate="proceed")
    resp_only["stage1"]["diagnosis_or_decision"] = {
        "state": "trend_down",
        "gate_node": "breakout",
        "gate_reason": "none",
    }
    assert gate_result_of(resp_only) == "proceed"

    flat = _dw(0.7, "win", 2.0)
    flat["stage1"] = {"gate_result": "proceed"}  # the wrong, non-persisted shape
    assert gate_result_of(flat) is None
    assert build_calibration_report([flat])["counterfactual_sweep"] == []


def test_note_names_which_channel_is_empty():
    """with_both == 0 has three causes; the note must not contradict its report."""
    neither = build_calibration_report([_dw()])["note"]  # no confidence, no outcome
    assert "neither" in neither

    conf_only = build_calibration_report([_dw(0.5)])["note"]  # confidence, no outcome
    assert "no outcome" in conf_only

    outcome_only = build_calibration_report([_dw(status="win", pnl_r=1.0)])["note"]
    assert "no contract-path confidence" in outcome_only

    with_both = build_calibration_report([_dw(0.5, "win", 2.0)])["note"]
    assert with_both is None


# --- confidence_of guards ----------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.0, 0.0),
        (1.0, 1.0),
        (0.42, 0.42),
        (True, None),  # bool is an int in Python; must not read as 1.0
        (False, None),
        (1.5, None),
        (-0.1, None),
        ("0.5", None),
        (None, None),
    ],
)
def test_confidence_of_guards(value, expected):
    assert (
        confidence_of(
            {
                "stage2": {
                    "diagnosis_or_decision": {"decision_summary": {"confidence": value}}
                }
            }
        )
        == expected
    )
