"""Batch×governance L2: per-formula T4 validation endpoint + deploy-gate fallback.

Covers: validate stores an addressed verdict, gate prefers per-formula over
global report, validation read endpoint 404s cleanly, unknown formula 404s.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.api.v1 import alpha


@pytest.fixture(autouse=True)
def _clean_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(alpha, "_sessions", {})
    monkeypatch.setattr(alpha, "_results_dir", tmp_path)
    # Per-formula verdicts live under data/alpha_validations off _repo_root;
    # point the whole repo root at tmp so tests never touch the real tree.
    monkeypatch.setattr(alpha, "_repo_root", lambda: tmp_path)
    yield


def _mk_session(
    sid: str, tokens: list[int], *, symbol: str = "BTC/USDT", interval: str = "1h"
) -> None:
    with alpha._sessions_lock:
        alpha._sessions[sid] = {
            "status": "completed",
            "step": 3,
            "best_score": 0.5,
            "train_steps": 3,
            "best_formula": tokens,
            "config": {"symbol": symbol, "interval": interval},
            "started_at": 100.0,
            "history": {
                "step": [],
                "avg_reward": [],
                "best_score": [],
                "stable_rank": [],
            },
            "logs": [],
        }


# ---------------------------------------------------------------------------
# POST /formulas/{id}/validate
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validate_404_unknown_formula() -> None:
    with pytest.raises(HTTPException) as ei:
        await alpha.validate_formula_endpoint("nope", None)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_validate_runs_and_stores_verdict(monkeypatch) -> None:
    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)

    from selene_space_factor_research.mining.validation_protocol import (
        FormulaValidation,
    )

    fake = FormulaValidation(
        readable="x",
        tokens=tokens,
        canonical_hash="h",
        verdict="VALID",
        decision="accept",
        reason="ok",
    )
    recorded: dict = {}

    def fake_build(db_path, symbols, interval, bars):
        recorded["symbols"] = symbols
        recorded["interval"] = interval
        return {"1h": ({}, {})}

    monkeypatch.setattr(
        "selene_space_factor_research.mining.report.build_datasets", fake_build
    )
    monkeypatch.setattr(
        "selene_space_factor_research.mining.validation_protocol.validate_formula",
        lambda *a, **k: fake,
    )

    resp = await alpha.validate_formula_endpoint("alpha_v1", None)
    data = resp.data
    assert data["decision"] == "accept"
    assert data["data_source"] == "klines_db"
    # Session symbol/interval projected into the dataset scope.
    assert recorded["symbols"] == ["BTC/USDT"]
    assert recorded["interval"] == "1h"
    # Verdict stored at the addressed path.
    stored = alpha._repo_root() / "data" / "alpha_validations" / "alpha_v1.json"
    assert stored.exists()
    record = json.loads(stored.read_text(encoding="utf-8"))
    assert record["formula_id"] == "alpha_v1"
    assert record["tokens"] == tokens


@pytest.mark.asyncio
async def test_validate_synthetic_fallback_recorded(monkeypatch) -> None:
    _mk_session("alpha_v2", [0, 6])
    from selene_space_factor_research.mining.validation_protocol import (
        FormulaValidation,
    )

    fake = FormulaValidation(
        readable="x",
        tokens=[0, 6],
        canonical_hash="h",
        verdict="SUSPICIOUS",
        decision="hold",
        reason="thin evidence",
    )
    monkeypatch.setattr(
        "selene_space_factor_research.mining.report.build_datasets",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no db")),
    )
    monkeypatch.setattr(
        "selene_space_factor_research.mining.report.fallback_synthetic_datasets",
        lambda **k: {"synthetic": ({}, {})},
    )
    monkeypatch.setattr(
        "selene_space_factor_research.mining.validation_protocol.validate_formula",
        lambda *a, **k: fake,
    )
    resp = await alpha.validate_formula_endpoint("alpha_v2", None)
    assert resp.data["data_source"] == "synthetic_fallback"
    assert resp.data["decision"] == "hold"


# ---------------------------------------------------------------------------
# GET /formulas/{id}/validation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_validation_read_404_before_validate() -> None:
    with pytest.raises(HTTPException) as ei:
        await alpha.get_formula_validation("never_validated")
    assert ei.value.status_code == 404


# ---------------------------------------------------------------------------
# deploy gate: per-formula verdict wins over global report
# ---------------------------------------------------------------------------
def test_gate_prefers_per_formula_over_global(tmp_path, monkeypatch) -> None:
    tokens = [7, 7]
    vdir = tmp_path / "data" / "alpha_validations"
    vdir.mkdir(parents=True)
    (vdir / "alpha_v3.json").write_text(
        json.dumps(
            {"tokens": tokens, "decision": "reject", "reason": "batch rerun says no"}
        ),
        encoding="utf-8",
    )
    # Global report says accept — must be shadowed by the per-formula reject.
    exp = tmp_path / "实验" / "alpha-mining"
    exp.mkdir(parents=True)
    (exp / "validation_report.json").write_text(
        json.dumps(
            {"formulas": [{"tokens": tokens, "decision": "accept", "reason": "stale"}]}
        ),
        encoding="utf-8",
    )
    decision, reason = alpha._lookup_formula_verdict(tokens)
    assert decision == "reject"
    # The endpoint-level lookup chain (per-formula → global) also lands on reject.
    d2, r2 = alpha._lookup_formula_verdict(tokens)
    if d2 is None:
        d2, r2 = alpha._lookup_t4_decision(tokens)
    assert d2 == "reject" and "batch rerun" in r2


def test_gate_falls_back_to_global_when_no_per_formula(tmp_path) -> None:
    tokens = [9, 9]
    exp = tmp_path / "实验" / "alpha-mining"
    exp.mkdir(parents=True)
    (exp / "validation_report.json").write_text(
        json.dumps(
            {
                "formulas": [
                    {"tokens": tokens, "decision": "hold", "reason": "global only"}
                ]
            }
        ),
        encoding="utf-8",
    )
    d, _ = alpha._lookup_formula_verdict(tokens)
    assert d is None  # no per-formula file
    d2, r2 = alpha._lookup_t4_decision(tokens)
    assert d2 == "hold" and r2 == "global only"
