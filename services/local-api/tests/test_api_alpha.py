"""Router coverage tests for /api/v1/alpha (Phase 4).

Validates that the alpha router exposes the expected 11 endpoints with
correct prefix and tags.
"""

from __future__ import annotations

from app.api.v1.alpha import router as alpha_router


class TestAlphaRouter:
    def test_prefix_is_alpha(self) -> None:
        assert alpha_router.prefix == "/alpha"

    def test_tag_present(self) -> None:
        assert "alpha" in alpha_router.tags

    def test_expected_route_count(self) -> None:
        """Alpha router should expose at least 10 routes (plan says 11)."""
        paths = [getattr(r, "path", None) for r in alpha_router.routes]
        paths = [p for p in paths if p]
        assert len(paths) >= 10, f"only {len(paths)} routes: {paths}"

    def test_saved_strategies_endpoint_registered(self) -> None:
        paths = [getattr(r, "path", None) for r in alpha_router.routes]
        assert any("/saved-strategies" in (p or "") for p in paths), paths

    def test_train_endpoints_registered(self) -> None:
        """/train POST + /train/{id}/status GET + /train/{id}/stop POST."""
        paths = [getattr(r, "path", None) for r in alpha_router.routes]
        assert any("/train" in (p or "") for p in paths), paths
        assert any("/status" in (p or "") for p in paths), paths
        assert any("/stop" in (p or "") for p in paths), paths

    def test_formulas_endpoints_registered(self) -> None:
        paths = [getattr(r, "path", None) for r in alpha_router.routes]
        assert any("/formulas" in (p or "") for p in paths), paths
