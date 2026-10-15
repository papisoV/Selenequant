"""US-002: GET /alpha/validation-report — 验证 tab 数据通路.

Locks the read side of the T4 validation report:
- report file served verbatim (same file ``_lookup_t4_decision`` gates deploys
  on) — no shape trimming, the frontend projects
- missing file → 404 with pipeline guidance (语义显形), never empty-data 200
- corrupt file → 500
- formula entries carry the full verdict surface (decision/reason/cost_stress/
  datasets with fold windows), matching ValidationReport.to_dict
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.api.v1 import alpha


@pytest.fixture(autouse=True)
def _isolate_repo_root(monkeypatch, tmp_path):
    """Point _repo_root at a tmp tree so tests never touch the real
    internal-archive/validation_report.json."""
    monkeypatch.setattr(alpha, "_repo_root", lambda: tmp_path)
    yield


def _report_dir(tmp_path):
    d = tmp_path / "实验" / "alpha-mining"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _mk_report(tmp_path, decision: str = "accept") -> dict:
    report = {
        "generated_at": "2026-09-12T10:00:00",
        "config": {"fold_count": 3},
        "formulas": [
            {
                "readable": "rank(ts_corr(close, volume, 5))",
                "tokens": [7, 8, 9],
                "canonical_hash": "abc123",
                "verdict": "STRONG",
                "decision": decision,
                "reason": "mean_ic 0.05, solo clean",
                "mean_ic": 0.05,
                "rolling_ic": [0.04, 0.06, 0.05],
                "train_mean_ic": 0.08,
                "subinterval_agreement": 0.9,
                "multi_symbol_agreement": 0.8,
                "dsr": 0.041,
                "pbo": 0.2,
                "cost_stress": [
                    {"fee_bps": 5, "sharpe": 1.2},
                    {"fee_bps": 10, "sharpe": 0.9},
                    {"fee_bps": 20, "sharpe": 0.4},
                ],
                "n_oos_obs": 900,
                "datasets": {
                    "BTC/USDT": {
                        "solo": [
                            {
                                "symbol": "BTC/USDT",
                                "verdict": "CLEAN",
                                "decision": "accept",
                            }
                        ],
                        "folds": [
                            {
                                "fold": 0,
                                "train_start": "2024-01-01",
                                "train_end": "2024-06-01",
                                "test_start": "2024-06-01",
                                "test_end": "2024-09-01",
                                "oos_rank_ic": 0.05,
                            }
                        ],
                    }
                },
            }
        ],
    }
    path = _report_dir(tmp_path) / "validation_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return report


@pytest.mark.asyncio
async def test_report_served_verbatim(tmp_path) -> None:
    report = _mk_report(tmp_path)
    resp = await alpha.get_validation_report()
    assert resp.error is None
    assert resp.data["report"] == report
    assert resp.data["generated_at"] == "2026-09-12T10:00:00"


@pytest.mark.asyncio
async def test_missing_report_is_404_with_guidance(tmp_path) -> None:
    # No file written.
    with pytest.raises(HTTPException) as exc_info:
        await alpha.get_validation_report()
    assert exc_info.value.status_code == 404
    assert "validation" in exc_info.value.detail
    # Guidance names the pipeline, not a dead end.
    assert "pipeline" in exc_info.value.detail


@pytest.mark.asyncio
async def test_corrupt_report_is_500(tmp_path) -> None:
    path = _report_dir(tmp_path) / "validation_report.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(HTTPException) as exc_info:
        await alpha.get_validation_report()
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_formula_verdict_surface_complete(tmp_path) -> None:
    """The wire shape matches what the 验证 tab needs to render: decision
    semantics (accept/hold/reject/defer), solo verdicts, cost stress tiers,
    fold windows."""
    _mk_report(tmp_path, decision="hold")
    resp = await alpha.get_validation_report()
    f = resp.data["report"]["formulas"][0]

    assert f["decision"] == "hold"
    assert f["verdict"] == "STRONG"
    assert isinstance(f["reason"], str)
    assert len(f["cost_stress"]) == 3
    assert {tier["fee_bps"] for tier in f["cost_stress"]} == {5, 10, 20}

    ds = f["datasets"]["BTC/USDT"]
    fold = ds["folds"][0]
    for key in ("train_start", "train_end", "test_start", "test_end", "oos_rank_ic"):
        assert key in fold
    solo = ds["solo"][0]
    assert solo["symbol"] == "BTC/USDT"
    assert solo["verdict"] == "CLEAN"


@pytest.mark.asyncio
async def test_suspicious_verdict_passes_through(tmp_path) -> None:
    """solo SUSPICIOUS (QD-absorption Phase 1 propagation formula) must
    survive the wire verbatim — the frontend renders its degraded copy."""
    report = _mk_report(tmp_path)
    report["formulas"][0]["datasets"]["BTC/USDT"]["solo"].append(
        {"symbol": "ETH/USDT", "verdict": "SUSPICIOUS", "decision": "hold"}
    )
    path = _report_dir(tmp_path) / "validation_report.json"
    path.write_text(json.dumps(report), encoding="utf-8")

    resp = await alpha.get_validation_report()
    solos = resp.data["report"]["formulas"][0]["datasets"]["BTC/USDT"]["solo"]
    assert [s["verdict"] for s in solos] == ["CLEAN", "SUSPICIOUS"]
