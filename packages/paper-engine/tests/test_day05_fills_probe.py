"""Day 0.5 probe: fills 完整性验证（plan selene-0819-absorption-7items）.

tmp_path 双批 save/load 全链——验证 wipe-and-restore 快照语义下 fills 历史完整。
一次性验证件，判决写 commit message；保留为回归锁无害。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from selene_space_paper.accounts import AccountManager
from selene_space_paper.fills import Fill
from selene_space_paper.orders import PaperOrderManager
from selene_space_paper.positions import PositionManager
from selene_space_paper.state import PaperState


def test_two_batch_save_load_preserves_all_fills(tmp_path: Path) -> None:
    state = PaperState(tmp_path)

    # ── 批 1：账户 + 订单 + 成交 ──
    accts1 = AccountManager()
    orders1 = PaperOrderManager()
    poss1 = PositionManager()
    accts1.create_account("d05", 25_000)
    order1 = orders1.place_order(
        "d05", symbol="BTC/USDT", side="buy", order_type="market", quantity=0.5
    )
    fill1 = Fill(
        id="f-day05-1",
        order_id=order1.id,
        price=Decimal("50000.12345678"),
        quantity=Decimal("0.5"),
        timestamp=1_710_000_000_123,  # 毫秒位非零，测精度
    )
    poss1.apply_fill_with_symbol("d05", "BTC/USDT", fill1, order1.side)
    state.save_state(accts1, orders1, poss1, fills=[fill1])

    # ── 批 2：新 manager 集（模拟进程重启）+ 第二笔成交 ──
    accts2 = AccountManager()
    orders2 = PaperOrderManager()
    poss2 = PositionManager()
    order2 = orders2.place_order(
        "d05", symbol="ETH/USDT", side="sell", order_type="market", quantity=2.0
    )
    fill2 = Fill(
        id="f-day05-2",
        order_id=order2.id,
        price=Decimal("3451.5"),
        quantity=Decimal("2.0"),
        timestamp=1_720_000_000_456,
    )
    poss2.apply_fill_with_symbol("d05", "ETH/USDT", fill2, order2.side)
    state.save_state(accts2, orders2, poss2, fills=[fill2])

    # ── load：第二批 save 后读回（注入式签名）──
    restored = PaperState(tmp_path)
    r_accts, r_orders, r_poss = AccountManager(), PaperOrderManager(), PositionManager()
    loaded_fills, _meta = restored.load_state(r_accts, r_orders, r_poss)

    ids = {f.id for f in loaded_fills}
    print(f"PROBE: fills after batch2 = {sorted(ids)}")
    # 判据 1：批 2 的 fill 在
    assert "f-day05-2" in ids
    # 判据 2（Day 0.5 判决 = FAIL 的锁定）：快照语义下批 1 fill 被覆盖丢失。
    # 真库 ~/.selenespace/paper/paper.db fills=0 行同证。B1 归因走 runner
    # 部署流水（Story 5 备选路径），本断言防未来语义静默变更。
    assert (
        "f-day05-1" not in ids
    ), "fills 语义已从 wipe-and-restore 变为保留历史 — B1 归因可切回 paper.db fills 路径"

    # 判据 3：时间戳/价格精度无损（用批 2 fill——批 1 已被语义覆盖）
    by_id = {f.id: f for f in loaded_fills}
    f2 = by_id["f-day05-2"]
    assert f2.timestamp == 1_720_000_000_456
    assert f2.price == Decimal("3451.5")
