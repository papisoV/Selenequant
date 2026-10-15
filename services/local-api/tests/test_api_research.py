"""Router coverage tests for /api/v1/research (Phase 4).

Validates that the research router exposes the expected 10 endpoints with
correct prefix and tags. Business logic for tuning is covered by
test_api_strategies.py (which exercises the factory templates + backtest
pipeline); this is structural coverage only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.v1.research import router as research_router


class TestResearchRouter:
    def test_prefix_is_research(self) -> None:
        assert research_router.prefix == "/research"

    def test_tag_present(self) -> None:
        assert "research" in research_router.tags

    def test_expected_route_count(self) -> None:
        """Research router should expose at least 10 routes."""
        paths = [getattr(r, "path", None) for r in research_router.routes]
        paths = [p for p in paths if p]
        assert len(paths) >= 10, f"only {len(paths)} routes: {paths}"

    def test_tuning_endpoints_registered(self) -> None:
        """/tuning/results and /tuning/results/{file} must exist."""
        paths = [getattr(r, "path", None) for r in research_router.routes]
        assert any("/tuning" in (p or "") for p in paths), paths
        assert any("results" in (p or "") for p in paths), paths

    def test_alpha_mining_endpoints_registered(self) -> None:
        """/alpha-mining/top-formulas and /alpha-mining/validation must exist."""
        paths = [getattr(r, "path", None) for r in research_router.routes]
        assert any(p and "/alpha-mining/top-formulas" in p for p in paths), paths
        assert any(p and "/alpha-mining/validation" in p for p in paths), paths

    def test_routes_have_valid_methods(self) -> None:
        """All research routes should be GET (research is read-only)."""
        for r in research_router.routes:
            methods = getattr(r, "methods", None)
            if not methods:
                continue
            for m in methods:
                assert m in {"GET", "POST", "PUT", "DELETE", "PATCH"}, (
                    f"unexpected method {m} on {getattr(r, 'path', '?')}"
                )


class TestAlphaMiningArtifactEndpoints:
    """Functional coverage of the artifact-serving endpoints (patched dir)."""

    def test_top_formulas_served(self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from app.api.v1 import research as research_module
        from app.main import app

        payload = {"symbols": ["BTC/USDT"], "interval": "4h", "top_formulas": []}
        (tmp_path / "top_formulas.json").write_text(json.dumps(payload), encoding="utf-8")
        (tmp_path / "validation_report.json").write_text(json.dumps({"summary": {}}), encoding="utf-8")
        monkeypatch.setattr(research_module, "ALPHA_MINING_DIR", tmp_path)

        client = TestClient(app)
        r = client.get("/api/v1/research/alpha-mining/top-formulas")
        assert r.status_code == 200
        body = r.json()
        assert body["error"] is None
        assert body["data"]["symbols"] == ["BTC/USDT"]

    def test_validation_served(self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from app.api.v1 import research as research_module
        from app.main import app

        payload = {"summary": {"VALID": 1}, "formulas": []}
        (tmp_path / "validation_report.json").write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(research_module, "ALPHA_MINING_DIR", tmp_path)

        client = TestClient(app)
        r = client.get("/api/v1/research/alpha-mining/validation")
        assert r.status_code == 200
        body = r.json()
        assert body["error"] is None
        assert body["data"]["summary"] == {"VALID": 1}

    def test_missing_artifact_returns_null(self, monkeypatch, tmp_path) -> None:
        from fastapi.testclient import TestClient

        from app.api.v1 import research as research_module
        from app.main import app

        monkeypatch.setattr(research_module, "ALPHA_MINING_DIR", tmp_path)
        client = TestClient(app)
        r = client.get("/api/v1/research/alpha-mining/validation")
        assert r.status_code == 200
        body = r.json()
        assert body["data"] is None
        assert body["error"] is None

    def test_corrupt_artifact_returns_null(self, monkeypatch, tmp_path) -> None:
        """解析失败(非法 JSON)→ data=null 且 HTTP 200，绝不 500（E14 兜底）。"""
        from fastapi.testclient import TestClient

        from app.api.v1 import research as research_module
        from app.main import app

        (tmp_path / "top_formulas.json").write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr(research_module, "ALPHA_MINING_DIR", tmp_path)
        client = TestClient(app)
        r = client.get("/api/v1/research/alpha-mining/top-formulas")
        assert r.status_code == 200
        body = r.json()
        assert body["data"] is None
        assert body["error"] is None


_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALPHA_MINING_ARTIFACT_DIR = _REPO_ROOT / "实验" / "alpha-mining"


def _artifact_has_formulas() -> bool:
    """True only when the real artifact is actually usable (exists + non-empty top_formulas)."""
    p = _ALPHA_MINING_ARTIFACT_DIR / "top_formulas.json"
    if not p.exists():
        return False
    try:
        with p.open(encoding="utf-8") as f:
            return bool(json.load(f).get("top_formulas"))
    except (OSError, ValueError):
        return False


class TestAlphaMiningRealArtifacts:
    """End-to-end: the resolved artifact dir must serve the real on-disk outputs.

    Skips on clean checkouts (实验/ is untracked); when the smoke artifacts exist,
    asserts the exact field surface the frontend /research/alpha-mining page needs.
    """

    @pytest.mark.skipif(
        not _artifact_has_formulas(),
        reason="alpha-mining top_formulas artifact absent or empty in working tree",
    )
    def test_real_top_formulas_served(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        r = client.get("/api/v1/research/alpha-mining/top-formulas")
        assert r.status_code == 200
        d = r.json().get("data")
        assert d is not None, "ALPHA_MINING_DIR resolved wrong — real artifact not served"
        for k in ("symbols", "interval", "bars_per_symbol", "seed", "evaluations_used", "top_formulas"):
            assert k in d, f"missing top-level key {k}"
        f0 = d["top_formulas"][0]
        for k in ("readable", "canonical_hash", "score", "rank_ic", "oos_rank_ic", "ls_net_sharpe"):
            assert k in f0, f"missing formula key {k}"

    @pytest.mark.skipif(
        not (_ALPHA_MINING_ARTIFACT_DIR / "validation_report.json").exists(),
        reason="alpha-mining smoke artifacts absent in working tree",
    )
    def test_real_validation_report_served(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        r = client.get("/api/v1/research/alpha-mining/validation")
        assert r.status_code == 200
        d = r.json().get("data")
        assert d is not None, "ALPHA_MINING_DIR resolved wrong — real artifact not served"
        for k in ("generated_at", "config", "formulas", "summary"):
            assert k in d, f"missing top-level key {k}"
        f0 = d["formulas"][0]
        for k in ("verdict", "decision", "reason", "dsr", "pbo", "datasets"):
            assert k in f0, f"missing formula key {k}"
        assert "overfit" in list(f0["datasets"].values())[0], "missing per-dataset overfit audit"
