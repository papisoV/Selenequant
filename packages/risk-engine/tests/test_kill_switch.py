"""KillSwitch coverage — activation lifecycle, JSON persistence across
restarts, status payload, corrupted-state fallback, and singleton identity."""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_risk.kill_switch import KillSwitch

REASON = "operator halt"


@pytest.fixture(autouse=True)
def _fresh_singleton() -> None:
    """Each test starts with a virgin KillSwitch singleton."""
    KillSwitch._reset_singleton()


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "kill_switch.json"


class TestEngagementStates:
    def test_fresh_instance_is_disengaged(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        engaged = ks.is_active
        assert engaged is False

    def test_engagement_stamps_reason_and_clock(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate(REASON)
        checks = (ks.is_active, ks.reason, ks.activated_at is not None)
        assert checks == (True, REASON, True)

    def test_disengagement_wipes_reason(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate()
        ks.deactivate()
        post = (ks.is_active, ks.reason)
        assert post == (False, "")

    def test_status_payload_reflects_engagement(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate("status test")
        payload = ks.status()
        assert all(
            [
                payload["active"] is True,
                payload["reason"] == "status test",
                bool(payload["activated_at"]),
            ]
        )


class TestRestartDurability:
    def test_engagement_outlives_process(self, state_path: Path) -> None:
        KillSwitch(state_path).activate("persist test")

        # Drop the in-memory half to emulate a restart; the JSON file remains.
        KillSwitch._reset_singleton()
        revived = KillSwitch(state_path)

        still_on = revived.is_active
        assert still_on is True
        assert revived.reason == "persist test"

    def test_garbage_file_treated_as_disengaged(self, state_path: Path) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("not valid json")

        ks = KillSwitch(state_path)
        assert ks.is_active is False


class TestSingletonIdentity:
    def test_constructions_alias_one_object(self, state_path: Path) -> None:
        left, right = KillSwitch(state_path), KillSwitch(state_path)
        assert left is right
