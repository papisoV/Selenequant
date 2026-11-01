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

    def fake_build(db_path, symbols, interval, bars, **kwargs):
        recorded["symbols"] = symbols
        recorded["interval"] = interval
        recorded["bars"] = bars
        recorded["window"] = (kwargs.get("start"), kwargs.get("end"))
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


# ---------------------------------------------------------------------------
# D4: the multiple-testing count must be the search's, not 1.
# ---------------------------------------------------------------------------
def _stub_validation(monkeypatch, tokens: list[int]) -> dict:
    """Capture the kwargs the endpoint hands to ``validate_formula``."""
    from selene_space_factor_research.mining.validation_protocol import (
        FormulaValidation,
    )

    seen: dict = {}
    fake = FormulaValidation(
        readable="x",
        tokens=tokens,
        canonical_hash="h",
        verdict="VALID",
        decision="accept",
        reason="ok",
    )

    def _fake(*args, **kwargs):
        seen.update(kwargs)
        return fake

    monkeypatch.setattr(
        "selene_space_factor_research.mining.report.build_datasets",
        lambda *a, **k: {"1h": ({}, {})},
    )
    monkeypatch.setattr(
        "selene_space_factor_research.mining.validation_protocol.validate_formula",
        _fake,
    )
    return seen


def _write_artifact(
    tmp_path,
    tokens: list[int],
    *,
    evaluations: int,
    search_train_end: str | None = None,
    symbols: list[str] | None = None,
    interval: str = "1h",
    bars_per_symbol: int = 1500,
    window_start: str | None = None,
    window_end: str | None = None,
) -> None:
    window: dict = {"search_train_frac": 0.7}
    if search_train_end:
        window["search_train_end"] = search_train_end
    if window_start or window_end:
        window["selects_window"] = True
        window["start"] = window_start
        window["end"] = window_end
    exp = tmp_path / "实验" / "alpha-mining"
    exp.mkdir(parents=True, exist_ok=True)
    (exp / "top_formulas.json").write_text(
        json.dumps(
            {
                "symbols": symbols or ["BTC/USDT"],
                "interval": interval,
                "bars_per_symbol": bars_per_symbol,
                "evaluations_used": evaluations,
                "window": window,
                "top_formulas": [{"rank": 1, "readable": "x", "tokens": tokens}],
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_validate_uses_artifact_evaluations_used(tmp_path, monkeypatch) -> None:
    """380 evaluations → n_trials=380. The old code hardcoded 1 and deflated
    nothing, so every candidate looked like the only one ever tried."""
    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)
    # A live session records its own step count; blank it so the artifact decides.
    with alpha._sessions_lock:
        alpha._sessions["alpha_v1"]["step"] = 0
    _write_artifact(tmp_path, tokens, evaluations=380)
    seen = _stub_validation(monkeypatch, tokens)

    await alpha.validate_formula_endpoint("alpha_v1", None)
    assert seen["n_trials"] == 380


@pytest.mark.asyncio
async def test_validate_prefers_session_step_count(monkeypatch) -> None:
    """A live session knows its own search count; that beats the batch artifact."""
    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)  # step = 3
    seen = _stub_validation(monkeypatch, tokens)

    resp = await alpha.validate_formula_endpoint("alpha_v1", None)
    assert seen["n_trials"] == 3
    record = json.loads(
        (alpha._repo_root() / "data" / "alpha_validations" / "alpha_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert record["n_trials_source"] == "session"
    assert resp.data["decision"] == "accept"


@pytest.mark.asyncio
async def test_validate_without_artifact_keeps_defaults(tmp_path, monkeypatch) -> None:
    """Negative control: no session count, no artifact → n_trials=1, as before."""
    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)
    with alpha._sessions_lock:
        alpha._sessions["alpha_v1"]["step"] = 0
    seen = _stub_validation(monkeypatch, tokens)

    await alpha.validate_formula_endpoint("alpha_v1", None)
    assert seen["n_trials"] == 1
    # No recorded split → protocol default geometry, byte-for-byte the old one.
    cfg = seen["fold_config"]
    assert (cfg.anchor, cfg.min_test_start) == ("start", 0)


@pytest.mark.asyncio
async def test_validate_artifact_split_floors_the_folds(tmp_path, monkeypatch) -> None:
    """A recorded split reaches the validator as end-anchoring geometry."""
    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)
    with alpha._sessions_lock:
        alpha._sessions["alpha_v1"]["step"] = 0
    _write_artifact(
        tmp_path, tokens, evaluations=380, search_train_end="2024-02-09T00:00:00Z"
    )
    seen = _stub_validation(monkeypatch, tokens)

    await alpha.validate_formula_endpoint("alpha_v1", None)
    cfg = seen["fold_config"]
    assert cfg.anchor == "end"
    assert cfg.search_train_frac == 0.7


# ---------------------------------------------------------------------------
# Sample scope: validate on the panel the search trained on.
# ---------------------------------------------------------------------------
def _stub_scope(monkeypatch, tokens: list[int]) -> dict:
    """Capture the dataset scope the endpoint hands to ``build_datasets``."""
    from selene_space_factor_research.mining.validation_protocol import (
        FormulaValidation,
    )

    scope: dict = {}
    fake = FormulaValidation(
        readable="x",
        tokens=tokens,
        canonical_hash="h",
        verdict="VALID",
        decision="accept",
        reason="ok",
    )

    def _fake_build(db_path, symbols, interval, bars, **kwargs):
        scope["symbols"] = symbols
        scope["interval"] = interval
        scope["bars"] = bars
        scope["start"] = kwargs.get("start")
        scope["end"] = kwargs.get("end")
        return {"1h": ({}, {})}

    monkeypatch.setattr(
        "selene_space_factor_research.mining.report.build_datasets", _fake_build
    )
    monkeypatch.setattr(
        "selene_space_factor_research.mining.validation_protocol.validate_formula",
        lambda *a, **k: fake,
    )
    return scope


@pytest.mark.asyncio
async def test_validate_uses_artifact_symbols_and_bars(tmp_path, monkeypatch) -> None:
    """A cross-sectional IC over one name is degenerate; the artifact records
    the real symbol set and sample size, so the endpoint must use those."""
    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)
    # No live-session symbol/interval — the artifact is the only source.
    with alpha._sessions_lock:
        alpha._sessions["alpha_v1"]["config"] = {}
        alpha._sessions["alpha_v1"]["step"] = 0
    _write_artifact(
        tmp_path,
        tokens,
        evaluations=380,
        symbols=["SOL/USDT", "BTC/USDT", "ETH/USDT"],
        interval="4h",
        bars_per_symbol=900,
    )
    scope = _stub_scope(monkeypatch, tokens)

    await alpha.validate_formula_endpoint("alpha_v1", None)
    assert scope["symbols"] == ["SOL/USDT", "BTC/USDT", "ETH/USDT"]
    assert scope["interval"] == "4h"
    assert scope["bars"] == 900


@pytest.mark.asyncio
async def test_validate_passes_artifact_window(tmp_path, monkeypatch) -> None:
    """The recorded sample window must reach the loader, otherwise the verdict
    describes fresh bars the search never saw."""
    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)
    with alpha._sessions_lock:
        alpha._sessions["alpha_v1"]["step"] = 0
    _write_artifact(
        tmp_path,
        tokens,
        evaluations=380,
        window_start="2023-01-01T00:00:00Z",
        window_end="2024-06-01T00:00:00Z",
    )
    scope = _stub_scope(monkeypatch, tokens)

    await alpha.validate_formula_endpoint("alpha_v1", None)
    assert scope["start"] == "2023-01-01T00:00:00Z"
    assert scope["end"] == "2024-06-01T00:00:00Z"


@pytest.mark.asyncio
async def test_validate_body_overrides_artifact_scope(tmp_path, monkeypatch) -> None:
    """An explicit request still wins — the artifact is a default, not a cage."""
    from app.api.v1.alpha import ValidateFormulaRequest

    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)
    with alpha._sessions_lock:
        alpha._sessions["alpha_v1"]["step"] = 0
    _write_artifact(
        tmp_path,
        tokens,
        evaluations=380,
        symbols=["SOL/USDT", "BTC/USDT"],
        interval="4h",
        bars_per_symbol=900,
    )
    scope = _stub_scope(monkeypatch, tokens)

    body = ValidateFormulaRequest(
        symbols=["BTC/USDT", "ETH/USDT", "BNB/USDT"], interval="1d", bars=300
    )
    await alpha.validate_formula_endpoint("alpha_v1", body)
    assert scope["symbols"] == ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
    assert scope["interval"] == "1d"
    assert scope["bars"] == 300


@pytest.mark.asyncio
async def test_validate_without_artifact_keeps_legacy_scope(monkeypatch) -> None:
    """Negative control: no artifact → the pre-fix single-symbol default."""
    tokens = [0, 6, 3, 1]
    _mk_session("alpha_v1", tokens)  # config: BTC/USDT @ 1h
    with alpha._sessions_lock:
        alpha._sessions["alpha_v1"]["step"] = 0
    scope = _stub_scope(monkeypatch, tokens)

    await alpha.validate_formula_endpoint("alpha_v1", None)
    assert scope["symbols"] == ["BTC/USDT"]
    assert scope["interval"] == "1h"
    assert scope["bars"] == alpha._SEARCH_BARS
    assert scope["start"] is None and scope["end"] is None
