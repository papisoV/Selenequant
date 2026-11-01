"""Item 5 方向归属 — program-owned direction signal tests (T-A..T-M).

The plan (`internal-plan`) moves the
final direction call off the LLM: a deterministic program function
(:func:`compute_direction_signal`) computes a direction from the raw bars, the
LLM keeps structure/key-level/invalidation duties. Guarantees pinned here:

- S1 purity + determinism (T-E)
- the signal travels as a *side channel* (never inside input_data): Stage 1
  never sees it, Stage 2 does (T-I, T-A)
- a declared direction opposing the program arm is a *retryable consistency*
  error, NOT a cheat crash (T-B, T-C)
- the record top level carries the field through all writer arms (T-M)
- the counterfactual scorer reads it as a third fallback with honest attribution
  (T-D)
- the assembly seam computes exactly the pure function over the same bars
  (T-F, T-G, T-J)
- the heartbeat must not swallow a shape error as a "skip" (T-K)
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for _p in [
    REPO / "packages" / "decision-workflow",
    REPO / "packages" / "shared-types",
    REPO / "packages" / "core",
    REPO / "services" / "local-api",
    REPO / "scripts",
]:
    sys.path.insert(0, str(_p))

from selene_space_decision_workflow.eval_counterfactual import next_period_of  # noqa: E402
from selene_space_decision_workflow.llm_provider import StubLLMProvider  # noqa: E402
from selene_space_decision_workflow.persistence import InMemoryPersistence  # noqa: E402
from selene_space_decision_workflow.trading_pipeline import (  # noqa: E402
    build_trading_workflow,
    compute_direction_signal,
)

_SYMBOL = "BTC/USDT"
_INTERVAL = "1h"

_DIRECTION_SIGNAL_LONG = {
    "direction": "long",
    "basis": {
        "range_low_20": 90.0,
        "range_high_20": 110.0,
        "ema5_slope": 1.5,
        "last_close": 108.0,
    },
    "bars_total": 120,
}

_STAGE1_SUMMARY = {
    "state": "trend_up",
    "gate_result": "proceed",
    "notes": "多头结构完整",
}

_STAGE1_JSON = json.dumps(
    {
        "state": "trend_up",
        "gate_result": "proceed",
        "trace": [
            {
                "node_id": "1.1",
                "question": "数据完整性检查",
                "answer": "是",
                "bar_range": "K1-K120",
                "reason": "窗口含 K1-K120 共120根K线",
                "branch": "是",
                "program_prefill_nodes": [],
            }
        ],
        "diagnosis_summary": _STAGE1_SUMMARY,
    },
    ensure_ascii=False,
)


def _stage2_json(direction: str | None, *, decision: str = "wait") -> str:
    summary: dict = {"diagnosis_summary": _STAGE1_SUMMARY}
    if direction is not None:
        summary["direction"] = direction
    return json.dumps(
        {
            "decision": decision,
            "terminal_outcome": decision,
            "order": None,
            "trace": [],
            "decision_summary": summary,
        },
        ensure_ascii=False,
    )


def _make_workflow(
    llm, tmp_path: Path, *, direction_signal: dict | None = None, max_retry: int = 4
):
    wf, _meta = build_trading_workflow(
        llm=llm,
        persistence=InMemoryPersistence(),
        symbol=_SYMBOL,
        interval=_INTERVAL,
        results_dir=tmp_path,
        direction_signal=direction_signal,
        max_retry=max_retry,
    )
    return wf


def _np(record: dict) -> dict:
    """``next_period_of`` under a non-None narrowing for direct indexing."""
    np_ = next_period_of(record)
    assert np_ is not None
    return np_


def _bars(closes: list[float], *, base_ts: int = 1_700_000_000_000) -> list[dict]:
    step = 3_600_000
    return [
        {
            "ts": base_ts + i * step,
            "open": c,
            "high": c + 1.0,
            "low": c - 1.0,
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate(closes)
    ]


# --------------------------------------------------------------------------- T-E


def test_T_E_signal_is_deterministic_and_thin_series_is_none() -> None:
    bars = _bars([100.0 + i for i in range(30)])
    a = compute_direction_signal(bars)
    b = compute_direction_signal(bars)
    assert a == b  # byte-identical dict, no hidden state
    assert a is not None and a["direction"] == "long"
    assert set(a["basis"]) == {
        "range_low_20",
        "range_high_20",
        "ema5_slope",
        "last_close",
    }
    assert compute_direction_signal(bars[:19]) is None


def test_T_E_direction_symmetry() -> None:
    up = compute_direction_signal(_bars([100.0 + i for i in range(25)]))
    down = compute_direction_signal(_bars([200.0 - i for i in range(25)]))
    flat = compute_direction_signal(_bars([100.0] * 25))
    assert up is not None and down is not None and flat is not None
    assert up["direction"] == "long"
    assert down["direction"] == "short"
    assert flat["direction"] == "neutral"


# ----------------------------------------------------------- T-I / T-A channel


def test_T_I_stage1_never_sees_signal_stage2_does(tmp_path: Path) -> None:
    """The signal is a side channel: absent from the Stage-1 prompt, present
    in Stage-2. This is the whole reason it is not put into input_data."""
    llm = StubLLMProvider([_STAGE1_JSON, _stage2_json("long")])
    wf = _make_workflow(llm, tmp_path, direction_signal=_DIRECTION_SIGNAL_LONG)

    stage1_prompt = wf._build_stage1_user_prompt(
        input_data={"symbol": _SYMBOL, "summary": {"last_close": 1.0}}, prefill=None
    )
    assert "direction_signal" not in stage1_prompt
    assert "程序方向信号" not in stage1_prompt

    stage2_prompt = wf._build_stage2_user_prompt(
        input_data={"symbol": _SYMBOL},
        diagnosis_summary=_STAGE1_SUMMARY,
        strategy_files=[],
    )
    assert "【程序方向信号】" in stage2_prompt
    assert "ema5_slope" in stage2_prompt


def test_T_A_no_cross_request_leak_on_single_instance(tmp_path: Path) -> None:
    """One instance, two analyze() calls — the signal block is stable, no
    mutation of self between requests."""
    llm = StubLLMProvider(
        [_STAGE1_JSON, _stage2_json("long"), _STAGE1_JSON, _stage2_json("long")]
    )
    wf = _make_workflow(llm, tmp_path, direction_signal=_DIRECTION_SIGNAL_LONG)

    p1 = wf._build_stage2_user_prompt(
        input_data={}, diagnosis_summary=_STAGE1_SUMMARY, strategy_files=[]
    )
    p2 = wf._build_stage2_user_prompt(
        input_data={}, diagnosis_summary=_STAGE1_SUMMARY, strategy_files=[]
    )
    assert p1 == p2


# ---------------------------------------------------------------- T-M record


def test_T_M_record_carries_direction_signal(tmp_path: Path) -> None:
    llm = StubLLMProvider([_STAGE1_JSON, _stage2_json("long")])
    wf = _make_workflow(llm, tmp_path, direction_signal=_DIRECTION_SIGNAL_LONG)
    record = wf.analyze(
        input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
        input_id="dir-1",
        session_id="s1",
    )
    dumped = record.model_dump(mode="json")
    assert dumped["direction_signal"] == _DIRECTION_SIGNAL_LONG


def test_T_M_none_signal_field_present(tmp_path: Path) -> None:
    """None must yield the field present-and-null, never a silent drop
    (the Pydantic-v2 undeclared-key trap)."""
    llm = StubLLMProvider([_STAGE1_JSON, _stage2_json("long")])
    wf = _make_workflow(llm, tmp_path, direction_signal=None)
    record = wf.analyze(
        input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
        input_id="dir-none",
        session_id="s1",
    )
    dumped = record.model_dump(mode="json")
    assert "direction_signal" in dumped and dumped["direction_signal"] is None


# ------------------------------------------------------------ T-B / T-C channel


def test_T_B_opposing_direction_is_retryable_not_cheat(tmp_path: Path) -> None:
    """Program=long, LLM declares short once then complies — no CheatError,
    the retry feedback names the program signal, and the record lands."""
    llm = StubLLMProvider([_STAGE1_JSON, _stage2_json("short"), _stage2_json("long")])
    wf = _make_workflow(
        llm, tmp_path, direction_signal=_DIRECTION_SIGNAL_LONG, max_retry=4
    )
    record = wf.analyze(
        input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
        input_id="dir-retry",
        session_id="s1",
    )
    assert record.stage2 is not None
    assert (
        record.stage2.diagnosis_or_decision["decision_summary"]["direction"] == "long"
    )
    # The retry feedback (2nd call's user message) named the program signal.
    assert llm.last_messages is not None
    assert "program direction_signal='long'" in llm.last_messages[-1].content


def test_T_B_persistent_opposition_exhausts_to_validation_error(tmp_path: Path) -> None:
    from selene_space_decision_workflow.exceptions import ValidationError

    llm = StubLLMProvider(
        [
            _STAGE1_JSON,
            _stage2_json("short"),
            _stage2_json("short"),
            _stage2_json("short"),
            _stage2_json("short"),
        ]
    )
    wf = _make_workflow(
        llm, tmp_path, direction_signal=_DIRECTION_SIGNAL_LONG, max_retry=4
    )
    with pytest.raises(ValidationError):
        wf.analyze(
            input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
            input_id="dir-exhaust",
            session_id="s1",
        )


def test_T_C_program_direction_error_has_no_cheat_flag(tmp_path: Path) -> None:
    """The new check must emit layer='consistency' with cheat_detected False."""
    from selene_space_decision_workflow.models import Stage2Result
    from selene_space_decision_workflow.validator import (
        _check_program_direction,
    )

    result = Stage2Result(
        decision="wait",
        terminal_outcome="wait",
        order=None,
        trace=[],
        decision_summary={"direction": "short"},
    )
    errors: list = []
    _check_program_direction(result, "long", errors)
    assert len(errors) == 1
    assert errors[0].layer == "consistency"
    assert errors[0].field == "direction"
    assert errors[0].cheat_detected is False
    # Neutral expectation imposes nothing.
    errors2: list = []
    _check_program_direction(result, None, errors2)
    assert errors2 == []


# ---------------------------------------------------------------- T-D scorer


def test_T_D_scorer_third_fallback_and_precedence() -> None:
    # Short-circuit synthesized block → program_signal attribution.
    sc = {
        "stage2": {
            "decision": "wait",
            "decision_summary": {
                "short_circuited": True,
                "gate": "wait",
                "next_period": {"direction": "long", "source": "program_signal"},
            },
        },
        "direction_signal": {"direction": "long"},
    }
    assert _np(sc)["scorer_source"] == "program_signal"

    # A real Stage-1 next_period always outranks the program fallback.
    real = {
        "stage1": {"diagnosis_or_decision": {"next_period": {"direction": "short"}}},
        "direction_signal": {"direction": "long"},
    }
    np_ = _np(real)
    assert np_["direction"] == "short" and np_["scorer_source"] == "stage1"

    # Top-level only → program_signal.
    assert _np({"direction_signal": {"direction": "long"}})["scorer_source"] == (
        "program_signal"
    )

    # Neither → out of population (unchanged).
    assert next_period_of({"stage1": {"diagnosis_or_decision": {}}}) is None


def test_T_D_short_circuit_record_scores(tmp_path: Path) -> None:
    """A gate short-circuit record (wait) is scorable via the program arm."""
    gate_wait_stage1 = json.dumps(
        {
            "state": "range",
            "gate_result": "wait",
            "trace": [],
            "diagnosis_summary": {"state": "range", "gate_result": "wait"},
        },
        ensure_ascii=False,
    )
    llm = StubLLMProvider([gate_wait_stage1])
    wf = _make_workflow(llm, tmp_path, direction_signal=_DIRECTION_SIGNAL_LONG)
    record = wf.analyze(
        input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
        input_id="dir-sc",
        session_id="s1",
    )
    dumped = model_dump_json(record)
    assert next_period_of(dumped) == {
        "direction": "long",
        "source": "program_signal",
        "scorer_source": "program_signal",
    }


def model_dump_json(record) -> dict:
    return dict(record.model_dump(mode="json"))


# ---------------------------------------------------------- T-J / T-F / T-G seam


def test_T_J_build_trading_input_returns_three_tuple(tmp_path: Path) -> None:
    from app.core.trading_input import build_trading_input

    db = tmp_path / "klines.db"
    _seed_klines(db, [100.0 + i for i in range(60)])
    out = build_trading_input(db, _SYMBOL, _INTERVAL, bars=60)
    assert len(out) == 3
    input_data, htf, direction_signal = out
    assert htf is None
    assert direction_signal == compute_direction_signal(_load_bars_for_test(db, 60))


def _seed_klines(db: Path, closes: list[float], *, interval: str = "1h") -> None:
    con = sqlite3.connect(db)
    try:
        con.execute(
            "CREATE TABLE klines (timestamp INTEGER, open REAL, high REAL, "
            "low REAL, close REAL, volume REAL, symbol TEXT, interval TEXT, "
            "exchange TEXT, market_type TEXT)"
        )
        step = 3_600_000
        for i, c in enumerate(closes):
            con.execute(
                "INSERT INTO klines VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    1_700_000_000_000 + i * step,
                    c,
                    c + 1.0,
                    c - 1.0,
                    c,
                    1.0,
                    _SYMBOL,
                    interval,
                    "binance",
                    "futures",
                ),
            )
        con.commit()
    finally:
        con.close()


def _load_bars_for_test(db: Path, bars: int) -> list[dict]:
    from selene_space_decision_workflow.trading_pipeline import load_bars

    return load_bars(db, _SYMBOL, _INTERVAL, bars)


def test_T_F_seam_identity_pure_fn_matches_assembly(tmp_path: Path) -> None:
    """The assembly helper computes exactly the pure function over the same
    bars — endpoint and heartbeat share it, so their signals are identical."""
    from app.core.trading_input import build_trading_input

    db = tmp_path / "klines.db"
    _seed_klines(db, [100.0 + (i % 5) for i in range(60)])

    endpoint_sig = build_trading_input(db, _SYMBOL, _INTERVAL, bars=60)[2]
    heartbeat_sig = build_trading_input(db, _SYMBOL, _INTERVAL, bars=60)[2]
    assert endpoint_sig == heartbeat_sig
    assert endpoint_sig == compute_direction_signal(_load_bars_for_test(db, 60))


def test_T_G_incremental_signal_from_raw_bars(tmp_path: Path) -> None:
    """The incremental seam computes the signal from the RAW all_bars, so a
    summary-only implementation cannot leak in — it equals the full-window
    call over the same all_bars."""
    from app.core.trading_input import build_trading_input

    db = tmp_path / "klines.db"
    _seed_klines(db, [90.0 + i for i in range(80)])
    full_sig = build_trading_input(db, _SYMBOL, _INTERVAL, bars=80)[2]
    # Same all_bars → identical signal regardless of which branch built it.
    assert full_sig == compute_direction_signal(_load_bars_for_test(db, 80))


# ---------------------------------------------------------------- T-H canned


def test_T_H_none_signal_no_prompt_block_no_error(tmp_path: Path) -> None:
    """direction_signal=None → prompt has no program block and validation
    produces no program-direction error (behaviour identical to before)."""
    llm = StubLLMProvider([_STAGE1_JSON, _stage2_json("short")])
    wf = _make_workflow(llm, tmp_path, direction_signal=None)
    prompt = wf._build_stage2_user_prompt(
        input_data={}, diagnosis_summary=_STAGE1_SUMMARY, strategy_files=[]
    )
    assert "【程序方向信号】" not in prompt
    # A "short" declaration with no program expectation is fine.
    record = wf.analyze(
        input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
        input_id="dir-h",
        session_id="s1",
    )
    assert record.stage2 is not None


# ------------------------------------------------------------------- T-K heartbeat


@pytest.mark.asyncio
async def test_T_K_heartbeat_shape_error_is_not_swallowed_as_skip(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A builder returning a 2-tuple (stale arity) must surface as a distinct
    failure with its own marker — never silent 'skipped'."""
    import logging

    import app.core.pa_heartbeat as hbmod

    class _BadBuilder:
        def __call__(self, *a, **k):
            return {"symbol": _SYMBOL}, None  # 2-tuple — stale contract

    class _NoopService:
        async def analyze_trading(self, *a, **k):  # never reached
            raise AssertionError("must not be reached on a shape error")

    monkeypatch.setattr(hbmod, "build_trading_input", _BadBuilder())
    sched = hbmod.PaHeartbeatScheduler(
        settings_loader=lambda: _FakeSettings(),
        service_loader=lambda: _NoopService(),
        config=hbmod.PaHeartbeatConfig(interval=_INTERVAL, bars=120),
        klines_db=tmp_path / "klines.db",
        sync_loader=None,
    )
    with caplog.at_level(logging.ERROR):
        result = await sched._diagnose_one(svc=_NoopService(), symbol=_SYMBOL)
    assert result == "failed"
    assert "assembly shape error" in caplog.text


class _FakeSettings:
    def get_favorite_symbols(self) -> list[str]:
        return [_SYMBOL]


# ------------------------------------------------------------------- T-L CLI arm


def test_T_L_cli_arm_writes_declared_field_and_scores(tmp_path: Path) -> None:
    """The CLI (third writer arm) produces a record whose top level carries
    direction_signal and which the scorer attributes to program_signal —
    WITHOUT any DecisionWorkflowService in the path (record is complete the
    moment wf.analyze returns)."""
    import run_pa_diagnosis as rpd

    # The CLI imports the very same pure function (structural anti-drift).
    assert rpd.compute_direction_signal is compute_direction_signal

    captured: list[dict] = []

    class _CapturePersistence:
        def save(self, record: dict) -> str:
            captured.append(record)
            return "mem://1"

    llm = StubLLMProvider([_STAGE1_JSON, _stage2_json("long")])
    wf, _meta = rpd.build_trading_workflow(
        llm=llm,
        persistence=_CapturePersistence(),
        symbol=_SYMBOL,
        interval=_INTERVAL,
        results_dir=tmp_path,
        direction_signal=_DIRECTION_SIGNAL_LONG,
    )
    wf.analyze(
        input_data={"symbol": _SYMBOL, "interval": _INTERVAL},
        input_id="cli-1",
        session_id="s1",
    )
    assert len(captured) == 1
    written = captured[0]
    assert written["direction_signal"] == _DIRECTION_SIGNAL_LONG
    # Round-trip through JSON (what TeePersistence/record_repo do) then score.
    # This Stage-1 summary carries no next_period, so the scorer correctly
    # falls through to the program arm — end-to-end proof of the fallback.
    roundtripped = json.loads(json.dumps(written, ensure_ascii=False))
    scored = _np(roundtripped)
    assert scored["scorer_source"] == "program_signal"
    assert scored["direction"] == "long"


def test_T_L_cli_arm_has_no_service_dependency() -> None:
    """Regression pin: the CLI module source must not import/reference
    DecisionWorkflowService — the record is complete at wf.analyze return."""
    src = (REPO / "scripts" / "run_pa_diagnosis.py").read_text(encoding="utf-8")
    assert "DecisionWorkflowService" not in src
