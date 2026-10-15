"""PaperAccount defaults and AccountManager CRUD coverage."""

from __future__ import annotations

from selene_space_paper.accounts import AccountManager, AccountStatus, PaperAccount

SEED = 10_000.0
NAME = "Demo"


class TestPaperAccountShape:
    def test_new_account_gets_active_status_and_pa_prefix(self) -> None:
        acc = PaperAccount(name="Test", initial_balance=SEED, current_balance=SEED)
        assert (acc.name, acc.status) == ("Test", AccountStatus.ACTIVE)
        assert acc.id.startswith("pa-")

    def test_initial_and_current_stored_as_given(self) -> None:
        acc = PaperAccount(name="X", initial_balance=5_000, current_balance=4_500)
        assert (acc.initial_balance, acc.current_balance) == (5_000, 4_500)


class TestAccountManagerCrud:
    def _mgr_with_demo(self) -> tuple[AccountManager, PaperAccount]:
        mgr = AccountManager()
        return mgr, mgr.create_account(NAME, SEED)

    def test_create_seeds_current_from_initial(self) -> None:
        mgr = AccountManager()
        acc = mgr.create_account(NAME, SEED)
        assert acc.name == NAME
        assert acc.initial_balance == SEED
        assert acc.current_balance == SEED

    def test_fetch_roundtrip_by_id(self) -> None:
        mgr, acc = self._mgr_with_demo()
        found = mgr.get_account(acc.id)
        assert found is not None
        assert found.id == acc.id

    def test_missing_lookup_yields_none(self) -> None:
        assert AccountManager().get_account("nonexistent") is None

    def test_listing_counts_registrations(self) -> None:
        mgr = AccountManager()
        mgr.create_account("A", 1_000)
        mgr.create_account("B", 2_000)
        assert len(mgr.list_accounts()) == 2

    def test_delete_true_once_then_false(self) -> None:
        mgr, acc = self._mgr_with_demo()
        assert mgr.delete_account(acc.id) is True
        assert mgr.get_account(acc.id) is None
        assert mgr.delete_account(acc.id) is False


class TestAccountMutations:
    def test_reset_restores_seed_and_activity(self) -> None:
        mgr = AccountManager()
        acc = mgr.create_account("Reset", SEED)
        mgr.update_balance(acc.id, -3_000)
        mgr.set_status(acc.id, AccountStatus.PAUSED)
        reset = mgr.reset_account(acc.id)
        assert reset is not None
        assert reset.current_balance == SEED
        assert reset.status == AccountStatus.ACTIVE

    def test_balance_delta_applies_to_current(self) -> None:
        mgr = AccountManager()
        acc = mgr.create_account("Bal", SEED)
        mgr.update_balance(acc.id, -1_500)
        assert mgr.get_account(acc.id).current_balance == 8_500

    def test_status_write_through(self) -> None:
        mgr = AccountManager()
        acc = mgr.create_account("Status", SEED)
        mgr.set_status(acc.id, AccountStatus.STOPPED)
        assert mgr.get_account(acc.id).status == AccountStatus.STOPPED
