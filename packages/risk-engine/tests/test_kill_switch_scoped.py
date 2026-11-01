"""Tests for scoped KillSwitch (P1-3) — three-level activation.

Covers:
  - SYSTEM/ACCOUNT/STRATEGY scope independence
  - is_active_for() OR semantics
  - activate_scope/deactivate_scope
  - v1→v2 schema migration
  - backward-compat with legacy no-arg API
  - active_scopes() listing
  - callback firing semantics
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from selene_space_risk.kill_switch import KillSwitch, KillSwitchScope


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    """Reset singleton before each test."""
    KillSwitch._reset_singleton()


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "kill_switch.json"


# ---------------------------------------------------------------------------
# Backward compatibility (legacy no-arg API)
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    def test_legacy_is_active_returns_system_scope(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        assert ks.is_active is False
        ks.activate("legacy")
        assert ks.is_active is True
        assert ks.reason == "legacy"

    def test_legacy_deactivate_clears_system_scope(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate()
        ks.deactivate()
        assert ks.is_active is False

    def test_legacy_status_dict_shape(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate("status test")
        status = ks.status()
        assert status["active"] is True
        assert status["reason"] == "status test"
        assert status["activated_at"] is not None

    def test_legacy_activate_does_not_activate_other_scopes(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate("legacy system")
        # is_active_for with account_id should also return True because SYSTEM wins
        assert ks.is_active_for(account_id="acc1") is True
        # But no ACCOUNT scope is active
        scopes = ks.active_scopes()
        assert (KillSwitchScope.ACCOUNT, "acc1") not in scopes


# ---------------------------------------------------------------------------
# Scope activation independence
# ---------------------------------------------------------------------------


class TestScopeIndependence:
    def test_account_activation_does_not_affect_strategy(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "account issue")
        assert ks.is_active_for(account_id="acc1") is True
        assert ks.is_active_for(strategy_id="strat1") is False

    def test_strategy_activation_does_not_affect_account(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.STRATEGY, "strat1", "strategy breach")
        assert ks.is_active_for(strategy_id="strat1") is True
        assert ks.is_active_for(account_id="acc1") is False

    def test_system_activation_blocks_everything(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.SYSTEM, None, "global stop")
        assert ks.is_active_for(account_id="acc1") is True
        assert ks.is_active_for(strategy_id="strat1") is True
        assert ks.is_active_for(account_id="acc1", strategy_id="strat1") is True

    def test_deactivate_single_scope_leaves_others(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        ks.activate_scope(KillSwitchScope.STRATEGY, "strat1", "b")
        ks.deactivate_scope(KillSwitchScope.ACCOUNT, "acc1")
        assert ks.is_active_for(account_id="acc1") is False
        assert ks.is_active_for(strategy_id="strat1") is True

    def test_multiple_accounts_independent(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a1")
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc2", "a2")
        assert ks.is_active_for(account_id="acc1") is True
        assert ks.is_active_for(account_id="acc2") is True
        assert ks.is_active_for(account_id="acc3") is False
        ks.deactivate_scope(KillSwitchScope.ACCOUNT, "acc1")
        assert ks.is_active_for(account_id="acc1") is False
        assert ks.is_active_for(account_id="acc2") is True


# ---------------------------------------------------------------------------
# OR semantics in is_active_for
# ---------------------------------------------------------------------------


class TestOrSemantics:
    def test_no_arguments_returns_system_state(self, state_path: Path) -> None:
        """is_active_for() with no args is equivalent to is_active property."""
        ks = KillSwitch(state_path)
        assert ks.is_active_for() is False
        ks.activate()
        assert ks.is_active_for() is True

    def test_system_overrides_account(self, state_path: Path) -> None:
        """If SYSTEM is active, is_active_for returns True regardless of account_id."""
        ks = KillSwitch(state_path)
        ks.activate()
        assert ks.is_active_for(account_id="any-account") is True

    def test_account_and_strategy_both_active(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        ks.activate_scope(KillSwitchScope.STRATEGY, "strat1", "b")
        assert ks.is_active_for(account_id="acc1", strategy_id="strat1") is True
        assert ks.is_active_for(account_id="acc1", strategy_id="other") is True
        assert ks.is_active_for(account_id="other", strategy_id="strat1") is True
        assert ks.is_active_for(account_id="other", strategy_id="other") is False


# ---------------------------------------------------------------------------
# active_scopes listing
# ---------------------------------------------------------------------------


class TestActiveScopesListing:
    def test_empty_when_nothing_active(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        assert ks.active_scopes() == []

    def test_lists_all_active_scopes(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.SYSTEM, None, "sys")
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        ks.activate_scope(KillSwitchScope.STRATEGY, "strat1", "b")
        scopes = ks.active_scopes()
        assert (KillSwitchScope.SYSTEM, None) in scopes
        assert (KillSwitchScope.ACCOUNT, "acc1") in scopes
        assert (KillSwitchScope.STRATEGY, "strat1") in scopes
        assert len(scopes) == 3

    def test_deactivated_scope_removed_from_list(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        ks.deactivate_scope(KillSwitchScope.ACCOUNT, "acc1")
        assert ks.active_scopes() == []


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


class TestCallbacks:
    def test_callback_fires_on_system_activation(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        fired: list[bool] = []
        ks.on_activate(lambda: fired.append(True))
        ks.activate("manual")
        assert fired == [True]

    def test_callback_fires_on_scope_activation(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        fired: list[bool] = []
        ks.on_activate(lambda: fired.append(True))
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        assert fired == [True]

    def test_callback_does_not_fire_on_redundant_activation(self, state_path: Path) -> None:
        """Activating an already-active scope does not re-fire callbacks."""
        ks = KillSwitch(state_path)
        fired: list[bool] = []
        ks.on_activate(lambda: fired.append(True))
        ks.activate("first")
        ks.activate("second")  # already active, should not fire
        assert fired == [True]

    def test_callback_can_query_active_scopes(self, state_path: Path) -> None:
        """Callback uses active_scopes() to decide which clients to cancel."""
        ks = KillSwitch(state_path)
        captured: list[list] = []
        ks.on_activate(lambda: captured.append(ks.active_scopes()))
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        assert len(captured) == 1
        assert (KillSwitchScope.ACCOUNT, "acc1") in captured[0]

    def test_callback_resilient_to_exceptions(self, state_path: Path) -> None:
        """A failing callback does not block others."""
        ks = KillSwitch(state_path)
        fired: list[bool] = []
        ks.on_activate(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        ks.on_activate(lambda: fired.append(True))
        ks.activate("test")
        assert fired == [True]


# ---------------------------------------------------------------------------
# Persistence + migration
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_v2_schema_round_trip(self, state_path: Path) -> None:
        ks1 = KillSwitch(state_path)
        ks1.activate_scope(KillSwitchScope.SYSTEM, None, "sys")
        ks1.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        ks1.activate_scope(KillSwitchScope.STRATEGY, "strat1", "b")
        KillSwitch._reset_singleton()

        ks2 = KillSwitch(state_path)
        # SYSTEM active → is_active_for returns True for any context (OR semantics)
        assert ks2.is_active is True
        assert ks2.is_active_for(account_id="acc1") is True
        assert ks2.is_active_for(strategy_id="strat1") is True
        # Confirm scopes persisted (not just SYSTEM)
        scopes = ks2.active_scopes()
        assert (KillSwitchScope.SYSTEM, None) in scopes
        assert (KillSwitchScope.ACCOUNT, "acc1") in scopes
        assert (KillSwitchScope.STRATEGY, "strat1") in scopes

    def test_v2_schema_round_trip_without_system(self, state_path: Path) -> None:
        """When SYSTEM is not active, only matching scoped entries block."""
        ks1 = KillSwitch(state_path)
        ks1.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        ks1.activate_scope(KillSwitchScope.STRATEGY, "strat1", "b")
        KillSwitch._reset_singleton()

        ks2 = KillSwitch(state_path)
        assert ks2.is_active is False  # SYSTEM not active
        assert ks2.is_active_for(account_id="acc1") is True
        assert ks2.is_active_for(strategy_id="strat1") is True
        assert ks2.is_active_for(account_id="other") is False
        assert ks2.is_active_for(strategy_id="other") is False

    def test_v1_schema_migrates_to_system(self, state_path: Path) -> None:
        """Old v1 flat schema auto-migrates to v2 SYSTEM scope on load."""
        state_path.parent.mkdir(parents=True, exist_ok=True)
        v1_state = {
            "active": True,
            "activated_at": 1700000000.0,
            "reason": "legacy activation",
        }
        state_path.write_text(json.dumps(v1_state), encoding="utf-8")

        ks = KillSwitch(state_path)
        assert ks.is_active is True
        assert ks.reason == "legacy activation"

        # File should now be v2 on next save
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        assert loaded["version"] == 2
        assert loaded["system"]["active"] is True

    def test_v1_inactive_migrates_to_inactive(self, state_path: Path) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        v1_state = {"active": False, "activated_at": None, "reason": ""}
        state_path.write_text(json.dumps(v1_state), encoding="utf-8")

        ks = KillSwitch(state_path)
        assert ks.is_active is False

    def test_corrupted_state_defaults_inactive(self, state_path: Path) -> None:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("not valid json", encoding="utf-8")
        ks = KillSwitch(state_path)
        assert ks.is_active is False

    def test_full_status_dict_shape(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate_scope(KillSwitchScope.ACCOUNT, "acc1", "a")
        status = ks.full_status()
        assert status["version"] == 2
        assert status["accounts"]["acc1"]["reason"] == "a"
        assert status["system"]["active"] is False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_system_scope_rejects_scope_id(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        with pytest.raises(ValueError, match="SYSTEM scope does not accept"):
            ks.activate_scope(KillSwitchScope.SYSTEM, "acc1", "x")

    def test_account_scope_requires_scope_id(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        with pytest.raises(ValueError, match="account scope requires a scope_id"):
            ks.activate_scope(KillSwitchScope.ACCOUNT, None, "x")

    def test_strategy_scope_requires_scope_id(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        with pytest.raises(ValueError, match="strategy scope requires a scope_id"):
            ks.activate_scope(KillSwitchScope.STRATEGY, None, "x")

    def test_deactivate_system_with_scope_id_raises(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        with pytest.raises(ValueError, match="SYSTEM scope does not accept"):
            ks.deactivate_scope(KillSwitchScope.SYSTEM, "acc1")

    def test_singleton_pattern(self, state_path: Path) -> None:
        ks1 = KillSwitch(state_path)
        ks2 = KillSwitch(state_path)
        assert ks1 is ks2


# ---------------------------------------------------------------------------
# auto_activate path
# ---------------------------------------------------------------------------


class TestAutoActivate:
    def test_auto_activate_records_reason_prefix(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.auto_activate("drawdown 30%", triggered_by_rule="daily_loss_limit")
        assert ks.is_active is True
        assert ks.reason.startswith("Auto:")
        assert "drawdown 30%" in ks.reason

    def test_auto_activate_does_not_overwrite_existing(self, state_path: Path) -> None:
        ks = KillSwitch(state_path)
        ks.activate("manual first")
        ks.auto_activate("auto second", triggered_by_rule="r")
        assert ks.reason == "manual first"

    def test_auto_activate_with_strategy_id_activates_strategy_scope(self, state_path: Path) -> None:
        """When strategy_id is provided, only STRATEGY scope activates —
        other strategies continue trading, SYSTEM stays inactive."""
        ks = KillSwitch(state_path)
        ks.auto_activate(
            "strategy breach",
            triggered_by_rule="max_position",
            strategy_id="strat-A",
        )
        # SYSTEM not activated
        assert ks.is_active is False
        # STRATEGY strat-A is active
        assert ks.is_active_for(strategy_id="strat-A") is True
        # Other strategy still allowed
        assert ks.is_active_for(strategy_id="strat-B") is False

    def test_auto_activate_strategy_scope_does_not_block_other_strategies(self, state_path: Path) -> None:
        """Two strategies — activating one's scope leaves the other unblocked."""
        ks = KillSwitch(state_path)
        ks.auto_activate("breach A", triggered_by_rule="r", strategy_id="A")
        assert ks.is_active_for(strategy_id="A") is True
        assert ks.is_active_for(strategy_id="B") is False

    def test_auto_activate_strategy_scope_independent_of_system(self, state_path: Path) -> None:
        """A strategy-scope activation does not prevent SYSTEM activation later."""
        ks = KillSwitch(state_path)
        ks.auto_activate("breach", triggered_by_rule="r", strategy_id="A")
        ks.activate("manual system")
        assert ks.is_active is True
        assert ks.is_active_for(strategy_id="A") is True
        assert ks.is_active_for(strategy_id="B") is True  # SYSTEM wins

    def test_auto_activate_strategy_scope_persists(self, state_path: Path) -> None:
        ks1 = KillSwitch(state_path)
        ks1.auto_activate("breach", triggered_by_rule="r", strategy_id="A")
        KillSwitch._reset_singleton()

        ks2 = KillSwitch(state_path)
        assert ks2.is_active is False
        assert ks2.is_active_for(strategy_id="A") is True
