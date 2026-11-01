"""PaperState save/load/wipe over the three manager registries."""

from __future__ import annotations

from pathlib import Path

from selene_space_paper.accounts import AccountManager
from selene_space_paper.orders import PaperOrderManager
from selene_space_paper.positions import PositionManager
from selene_space_paper.state import PaperState
from selene_space_types.trading import OrderSide, OrderType

STORE_NAME = "Test"
SEED = 10_000.0


def _seed_order(mgr: PaperOrderManager) -> None:
    mgr.place_order(
        "acc-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.5,
    )


class TestPerRegistryPersistence:
    def test_accounts_survive_a_cycle(self, tmp_path: Path) -> None:
        ps = PaperState(tmp_path)
        origin = AccountManager()
        origin.create_account(STORE_NAME, SEED)
        ps.save_state(origin, PaperOrderManager(), PositionManager())

        revived = AccountManager()
        ps.load_state(revived, PaperOrderManager(), PositionManager())
        assert [a.name for a in revived.list_accounts()] == [STORE_NAME]

    def test_orders_survive_a_cycle(self, tmp_path: Path) -> None:
        ps = PaperState(tmp_path)
        origin = PaperOrderManager()
        _seed_order(origin)
        ps.save_state(AccountManager(), origin, PositionManager())

        revived = PaperOrderManager()
        ps.load_state(AccountManager(), revived, PositionManager())
        assert len(revived.get_orders("acc-1")) == 1


class TestMetaBag:
    def test_extra_dict_round_trips(self, tmp_path: Path) -> None:
        ps = PaperState(tmp_path)
        ps.save_state(
            AccountManager(),
            PaperOrderManager(),
            PositionManager(),
            extra={"fees": {"BTC/USDT": 42.5}},
        )
        _, extra = ps.load_state(AccountManager(), PaperOrderManager(), PositionManager())
        assert extra["fees"]["BTC/USDT"] == 42.5

    def test_uninitialized_store_reads_as_empty_meta(self, tmp_path: Path) -> None:
        ps = PaperState(tmp_path)
        _, extra = ps.load_state(AccountManager(), PaperOrderManager(), PositionManager())
        assert extra == {}


class TestWipe:
    def test_clear_state_removes_account_rows_file(self, tmp_path: Path) -> None:
        ps = PaperState(tmp_path / "paper")
        origin = AccountManager()
        origin.create_account(STORE_NAME, SEED)
        ps.save_state(origin, PaperOrderManager(), PositionManager())

        ps.clear_state()
        assert not (tmp_path / "paper" / "accounts.json").exists()
