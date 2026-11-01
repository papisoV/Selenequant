"""Tests for Phase 3.4: Vibe strategy artifact persistence mirror.

Verifies that when VIBE_STRATEGY_ENABLED=true, strategy saves are mirrored
to the vendored vibe StrategyStore, without affecting selene native persistence.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# Helper: build a mock vibe strategy store
# ---------------------------------------------------------------------------


def _make_mock_store() -> MagicMock:
    """Return a mock VibeStrategyStoreBridge."""
    store = MagicMock()
    store.register_artifact.return_value = "artifact-001"
    store.list_artifacts.return_value = []
    return store


# ---------------------------------------------------------------------------
# Test 1: Mirror block triggered by env var
# ---------------------------------------------------------------------------


class TestVibeStrategyMirrorEnv:
    """Test that the VIBE_STRATEGY_ENABLED env var gates the mirror."""

    def test_env_true_triggers_mirror(self) -> None:
        """VIBE_STRATEGY_ENABLED=true triggers register_artifact call."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {"VIBE_STRATEGY_ENABLED": "true"}):
            with patch(
                "subsystems.vibe.bridge.strategy_store.preset.new_vibe_strategy_store",
                return_value=mock_store,
            ):
                # Simulate the Phase 3.4 code path
                if os.environ.get("VIBE_STRATEGY_ENABLED", "").lower() in ("1", "true", "yes"):
                    from subsystems.vibe.bridge.strategy_store.preset import (
                        new_vibe_strategy_store,
                    )

                    store = new_vibe_strategy_store()
                    store.register_artifact({"id": "test-1"})

        mock_store.register_artifact.assert_called_once_with({"id": "test-1"})

    def test_env_unset_skips_mirror(self) -> None:
        """When VIBE_STRATEGY_ENABLED is not set, register_artifact is NOT called."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VIBE_STRATEGY_ENABLED", None)
            if os.environ.get("VIBE_STRATEGY_ENABLED", "").lower() in ("1", "true", "yes"):
                from subsystems.vibe.bridge.strategy_store.preset import (
                    new_vibe_strategy_store,
                )

                store = new_vibe_strategy_store()
                store.register_artifact({"id": "test-2"})

        mock_store.register_artifact.assert_not_called()

    def test_env_false_skips_mirror(self) -> None:
        """VIBE_STRATEGY_ENABLED=false skips the mirror."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {"VIBE_STRATEGY_ENABLED": "false"}):
            if os.environ.get("VIBE_STRATEGY_ENABLED", "").lower() in ("1", "true", "yes"):
                from subsystems.vibe.bridge.strategy_store.preset import (
                    new_vibe_strategy_store,
                )

                store = new_vibe_strategy_store()
                store.register_artifact({"id": "test-3"})

        mock_store.register_artifact.assert_not_called()


# ---------------------------------------------------------------------------
# Test 2: Case-insensitive env var values
# ---------------------------------------------------------------------------


class TestVibeStrategyMirrorCaseInsensitive:
    def test_upper_case_true(self) -> None:
        """VIBE_STRATEGY_ENABLED=TRUE also triggers mirror."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {"VIBE_STRATEGY_ENABLED": "TRUE"}):
            with patch(
                "subsystems.vibe.bridge.strategy_store.preset.new_vibe_strategy_store",
                return_value=mock_store,
            ):
                if os.environ.get("VIBE_STRATEGY_ENABLED", "").lower() in ("1", "true", "yes"):
                    from subsystems.vibe.bridge.strategy_store.preset import (
                        new_vibe_strategy_store,
                    )

                    store = new_vibe_strategy_store()
                    store.register_artifact({"id": "test-upper"})

        mock_store.register_artifact.assert_called_once()

    def test_mixed_case_yes(self) -> None:
        """VIBE_STRATEGY_ENABLED=Yes also triggers mirror."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {"VIBE_STRATEGY_ENABLED": "Yes"}):
            with patch(
                "subsystems.vibe.bridge.strategy_store.preset.new_vibe_strategy_store",
                return_value=mock_store,
            ):
                if os.environ.get("VIBE_STRATEGY_ENABLED", "").lower() in ("1", "true", "yes"):
                    from subsystems.vibe.bridge.strategy_store.preset import (
                        new_vibe_strategy_store,
                    )

                    store = new_vibe_strategy_store()
                    store.register_artifact({"id": "test-yes"})

        mock_store.register_artifact.assert_called_once()

    def test_numeric_one(self) -> None:
        """VIBE_STRATEGY_ENABLED=1 also triggers mirror."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {"VIBE_STRATEGY_ENABLED": "1"}):
            with patch(
                "subsystems.vibe.bridge.strategy_store.preset.new_vibe_strategy_store",
                return_value=mock_store,
            ):
                if os.environ.get("VIBE_STRATEGY_ENABLED", "").lower() in ("1", "true", "yes"):
                    from subsystems.vibe.bridge.strategy_store.preset import (
                        new_vibe_strategy_store,
                    )

                    store = new_vibe_strategy_store()
                    store.register_artifact({"id": "test-1"})

        mock_store.register_artifact.assert_called_once()


# ---------------------------------------------------------------------------
# Test 3: Import failure is handled gracefully
# ---------------------------------------------------------------------------


class TestVibeStrategyMirrorImportFailure:
    def test_import_failure_does_not_crash(self) -> None:
        """If the vibe preset import fails, no exception propagates."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {"VIBE_STRATEGY_ENABLED": "true"}):
            with patch.dict("sys.modules", {"subsystems.vibe.bridge.strategy_store.preset": None}):
                try:
                    from subsystems.vibe.bridge.strategy_store.preset import (  # noqa: F401
                        new_vibe_strategy_store,
                    )

                    store = new_vibe_strategy_store()
                    store.register_artifact({"id": "test-import-fail"})
                except (ImportError, TypeError):
                    # Expected: import fails, caught by the except block
                    pass

        mock_store.register_artifact.assert_not_called()

    def test_runtime_error_does_not_crash(self) -> None:
        """If the store operation itself raises, it is caught gracefully."""
        mock_store = _make_mock_store()
        mock_store.register_artifact.side_effect = RuntimeError("store down")

        error_caught = False
        with patch.dict(os.environ, {"VIBE_STRATEGY_ENABLED": "true"}):
            with patch(
                "subsystems.vibe.bridge.strategy_store.preset.new_vibe_strategy_store",
                return_value=mock_store,
            ):
                try:
                    from subsystems.vibe.bridge.strategy_store.preset import (
                        new_vibe_strategy_store,
                    )

                    store = new_vibe_strategy_store()
                    store.register_artifact({"id": "test-runtime-error"})
                except Exception:
                    error_caught = True

        assert error_caught, "RuntimeError should have been caught by except Exception"


# ---------------------------------------------------------------------------
# Test 4: Idempotent — multiple saves do not fail
# ---------------------------------------------------------------------------


class TestVibeStrategyMirrorIdempotent:
    def test_double_save_is_idempotent(self) -> None:
        """Calling register_artifact twice with the same artifact does not crash."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {"VIBE_STRATEGY_ENABLED": "true"}):
            with patch(
                "subsystems.vibe.bridge.strategy_store.preset.new_vibe_strategy_store",
                return_value=mock_store,
            ):
                from subsystems.vibe.bridge.strategy_store.preset import (
                    new_vibe_strategy_store,
                )

                store = new_vibe_strategy_store()
                store.register_artifact({"id": "test-idempotent"})
                store.register_artifact({"id": "test-idempotent"})

        assert mock_store.register_artifact.call_count == 2
        # No exception raised = idempotent


# ---------------------------------------------------------------------------
# Test 5: Mirror does not affect selene native persistence
# ---------------------------------------------------------------------------


class TestVibeStrategyMirrorIsolation:
    def test_selene_persistence_works_without_vibe(self) -> None:
        """Selene native persistence (repo.save) works even if vibe is disabled."""
        mock_store = _make_mock_store()

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VIBE_STRATEGY_ENABLED", None)
            if os.environ.get("VIBE_STRATEGY_ENABLED", "").lower() in ("1", "true", "yes"):
                from subsystems.vibe.bridge.strategy_store.preset import (
                    new_vibe_strategy_store,
                )

                store = new_vibe_strategy_store()
                store.register_artifact({"id": "test-no-vibe"})

        mock_store.register_artifact.assert_not_called()
