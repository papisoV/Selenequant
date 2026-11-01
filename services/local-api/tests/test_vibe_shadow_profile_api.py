"""Tests for Phase 3.6: Vibe Shadow Account Profile API.

Verifies that POST /api/v1/paper/shadow-profile/* endpoints behave correctly:
- Env gate (disabled → 404, enabled → accessible)
- Missing deps → 503
- Successful extraction with mocked bridge
- Idempotency
"""

from __future__ import annotations

import io
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.shadow_profile import router as shadow_profile_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with only the shadow_profile router."""
    app = FastAPI()
    app.include_router(shadow_profile_router, prefix="/api/v1/paper")
    return app


def _mock_shadow_account() -> MagicMock:
    """Return a mock VibeShadowAccount with canned responses."""
    mock_profile = MagicMock()
    mock_profile.shadow_id = "shad-001"
    mock_profile.rules = [{"id": "r1", "pattern": "buy low sell high"}]
    mock_profile.meta = {"source": "test_journal.csv", "rows": 42}

    mock_account = MagicMock()
    mock_account.extract_profile.return_value = mock_profile
    mock_account.save_profile.return_value = MagicMock()
    mock_account.load_profile.return_value = mock_profile
    mock_account.render_signal_engine.return_value = (
        "class SignalEngine:\n    def generate(self, data):\n        pass\n"
    )
    mock_account.run_backtest.return_value = {
        "total_trades": 10,
        "win_rate": 0.6,
        "profit_factor": 1.5,
    }
    return mock_account


# ---------------------------------------------------------------------------
# Test: env gate
# ---------------------------------------------------------------------------


class TestEnvGate:
    """Test VIBE_SHADOW_ENABLED env var gating."""

    def test_disabled_by_default(self) -> None:
        """When VIBE_SHADOW_ENABLED is not set, all endpoints return 404."""
        app = _make_app()
        client = TestClient(app)

        # extract
        resp = client.post(
            "/api/v1/paper/shadow-profile/extract",
            files={"file": ("test.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
            data={"min_support": "3", "max_rules": "5"},
        )
        assert resp.status_code == 404

        # render-engine
        resp = client.post("/api/v1/paper/shadow-profile/shad-001/render-engine")
        assert resp.status_code == 404

        # backtest
        resp = client.post(
            "/api/v1/paper/shadow-profile/shad-001/backtest",
            json={"window_start": "2024-01-01", "window_end": "2024-06-01"},
        )
        assert resp.status_code == 404

    def test_enabled_allows_access(self) -> None:
        """When VIBE_SHADOW_ENABLED=true, endpoints are reachable (503 if no deps)."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            resp = client.post(
                "/api/v1/paper/shadow-profile/extract",
                files={"file": ("test.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
                data={"min_support": "3", "max_rules": "5"},
            )
            # Should NOT be 404 — 503 means deps missing (expected in test env)
            assert resp.status_code != 404

    def test_case_insensitive_values(self) -> None:
        """VIBE_SHADOW_ENABLED=TRUE, Yes, 1 all work."""
        for value in ("TRUE", "Yes", "1"):
            with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": value}):
                app = _make_app()
                client = TestClient(app)
                resp = client.post(
                    "/api/v1/paper/shadow-profile/shad-001/render-engine",
                )
                assert resp.status_code != 404


# ---------------------------------------------------------------------------
# Test: 503 when deps missing
# ---------------------------------------------------------------------------


class TestMissingDependencies:
    """Test that 503 is returned when vibe shadow_account deps are unavailable."""

    def test_extract_returns_503(self) -> None:
        """POST /shadow-profile/extract returns 503 on ImportError."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            with patch.dict(
                "sys.modules",
                {"subsystems.vibe.bridge.shadow_account.preset": None},
            ):
                resp = client.post(
                    "/api/v1/paper/shadow-profile/extract",
                    files={"file": ("test.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
                    data={"min_support": "3", "max_rules": "5"},
                )
                assert resp.status_code == 503
                detail = resp.json()["detail"]
                assert "shadow_account" in detail["error"]
                assert "dependencies not installed" in detail["error"]

    def test_render_engine_returns_503(self) -> None:
        """POST /shadow-profile/{id}/render-engine returns 503 on ImportError."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            with patch.dict(
                "sys.modules",
                {"subsystems.vibe.bridge.shadow_account.preset": None},
            ):
                resp = client.post("/api/v1/paper/shadow-profile/shad-001/render-engine")
                assert resp.status_code == 503
                detail = resp.json()["detail"]
                assert "shadow_account" in detail["error"]

    def test_backtest_returns_503(self) -> None:
        """POST /shadow-profile/{id}/backtest returns 503 on ImportError."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            with patch.dict(
                "sys.modules",
                {"subsystems.vibe.bridge.shadow_account.preset": None},
            ):
                resp = client.post(
                    "/api/v1/paper/shadow-profile/shad-001/backtest",
                    json={"window_start": "2024-01-01", "window_end": "2024-06-01"},
                )
                assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Test: successful operations with mocked bridge
# ---------------------------------------------------------------------------


class TestSuccessfulOperations:
    """Test endpoints with a mocked VibeShadowAccount bridge."""

    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        """Patch new_vibe_shadow_account to return a mock."""
        self.mock_account = _mock_shadow_account()
        self.patcher = patch(
            "subsystems.vibe.bridge.shadow_account.preset.new_vibe_shadow_account",
            return_value=self.mock_account,
        )
        self.mock_new = self.patcher.start()
        yield
        self.patcher.stop()

    def test_extract_success(self) -> None:
        """POST /shadow-profile/extract returns shadow_id, rules, meta."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            resp = client.post(
                "/api/v1/paper/shadow-profile/extract",
                files={"file": ("test.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
                data={"min_support": "3", "max_rules": "5"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["shadow_id"] == "shad-001"
            assert len(data["rules"]) == 1
            assert data["meta"]["source"] == "test_journal.csv"
            # Verify bridge was called correctly
            self.mock_account.extract_profile.assert_called_once()
            call_kwargs = self.mock_account.extract_profile.call_args.kwargs
            assert call_kwargs["min_support"] == 3
            assert call_kwargs["max_rules"] == 5

    def test_extract_default_params(self) -> None:
        """POST /shadow-profile/extract uses defaults when params omitted."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            resp = client.post(
                "/api/v1/paper/shadow-profile/extract",
                files={"file": ("test.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
            )
            assert resp.status_code == 200
            call_kwargs = self.mock_account.extract_profile.call_args.kwargs
            assert call_kwargs["min_support"] == 3
            assert call_kwargs["max_rules"] == 5

    def test_render_engine_success(self) -> None:
        """POST /shadow-profile/{id}/render-engine returns Python source."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            resp = client.post("/api/v1/paper/shadow-profile/shad-001/render-engine")
            assert resp.status_code == 200
            data = resp.json()
            assert "source" in data
            assert "class SignalEngine" in data["source"]
            self.mock_account.load_profile.assert_called_once_with("shad-001")
            self.mock_account.render_signal_engine.assert_called_once()

    def test_backtest_success(self) -> None:
        """POST /shadow-profile/{id}/backtest returns backtest result."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            resp = client.post(
                "/api/v1/paper/shadow-profile/shad-001/backtest",
                json={"window_start": "2024-01-01", "window_end": "2024-06-01"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_trades"] == 10
            assert data["win_rate"] == 0.6
            assert data["profit_factor"] == 1.5
            self.mock_account.load_profile.assert_called_once_with("shad-001")
            self.mock_account.run_backtest.assert_called_once()
            call_kwargs = self.mock_account.run_backtest.call_args.kwargs
            assert call_kwargs["window_start"] == "2024-01-01"
            assert call_kwargs["window_end"] == "2024-06-01"
            assert call_kwargs["per_market_count"] == 5
            assert call_kwargs["source"] == "auto"

    def test_backtest_with_all_params(self) -> None:
        """POST /shadow-profile/{id}/backtest passes optional params."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            resp = client.post(
                "/api/v1/paper/shadow-profile/shad-001/backtest",
                json={
                    "window_start": "2024-01-01",
                    "window_end": "2024-06-01",
                    "markets": ["BTC/USDT", "ETH/USDT"],
                    "per_market_count": 3,
                    "source": "binance",
                },
            )
            assert resp.status_code == 200
            call_kwargs = self.mock_account.run_backtest.call_args.kwargs
            assert call_kwargs["markets"] == ["BTC/USDT", "ETH/USDT"]
            assert call_kwargs["per_market_count"] == 3
            assert call_kwargs["source"] == "binance"

    def test_backtest_missing_required_fields(self) -> None:
        """POST /shadow-profile/{id}/backtest returns 422 when required fields missing."""
        app = _make_app()
        client = TestClient(app)

        with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
            resp = client.post(
                "/api/v1/paper/shadow-profile/shad-001/backtest",
                json={},
            )
            assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """Verify endpoints are idempotent."""

    def test_extract_twice_same_result(self) -> None:
        """Calling extract twice with the same file returns consistent results."""
        mock_account = _mock_shadow_account()

        with patch(
            "subsystems.vibe.bridge.shadow_account.preset.new_vibe_shadow_account",
            return_value=mock_account,
        ):
            app = _make_app()
            client = TestClient(app)

            with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
                resp1 = client.post(
                    "/api/v1/paper/shadow-profile/extract",
                    files={"file": ("test.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
                    data={"min_support": "3", "max_rules": "5"},
                )
                resp2 = client.post(
                    "/api/v1/paper/shadow-profile/extract",
                    files={"file": ("test.csv", io.BytesIO(b"a,b,c\n1,2,3"), "text/csv")},
                    data={"min_support": "3", "max_rules": "5"},
                )
                assert resp1.json()["shadow_id"] == resp2.json()["shadow_id"]
                assert resp1.status_code == 200
                assert resp2.status_code == 200

    def test_render_engine_twice_same_result(self) -> None:
        """Calling render-engine twice returns the same source."""
        mock_account = _mock_shadow_account()

        with patch(
            "subsystems.vibe.bridge.shadow_account.preset.new_vibe_shadow_account",
            return_value=mock_account,
        ):
            app = _make_app()
            client = TestClient(app)

            with patch.dict(os.environ, {"VIBE_SHADOW_ENABLED": "true"}):
                resp1 = client.post("/api/v1/paper/shadow-profile/shad-001/render-engine")
                resp2 = client.post("/api/v1/paper/shadow-profile/shad-001/render-engine")
                assert resp1.json() == resp2.json()
                assert resp1.status_code == 200
                assert resp2.status_code == 200
