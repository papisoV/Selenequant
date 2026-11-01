"""H19 precision tests — verify Decimal funding fields don't drift over many trades.

Float arithmetic accumulates ~0.001 USDT per 1000 trades from binary
representation error (e.g. 0.1 + 0.2 = 0.30000000000000004). Decimal
arithmetic is exact for base-10 values, so 1000 trades of the same size
must produce a balance exactly equal to the expected value — not just
within an epsilon tolerance.
"""

from __future__ import annotations

from decimal import Decimal

from selene_space_paper.accounts import AccountManager


def test_decimal_precision_1000_trades_no_drift() -> None:
    """1000 round-trip trades must produce zero net drift on balance/PnL/fee.

    Each trade: credit +10 USDT then debit -10 USDT (net zero), plus a
    0.001 USDT fee per trade (1000 trades → 1.0 USDT total fee). With
    float, the cumulative balance would drift by ~1e-12 per op; with
    Decimal, the final values are exact.
    """
    mgr = AccountManager()
    acc = mgr.create_account("Precision", Decimal("10000"))

    fee_per_trade = Decimal("0.001")
    for _ in range(1000):
        mgr.update_balance(acc.id, Decimal("10"))
        mgr.update_balance(acc.id, Decimal("-10"))
        mgr.update_balance(acc.id, -fee_per_trade)
        acc.total_fee += fee_per_trade

    # Exact equality — no pytest.approx, no epsilon. Decimal math is exact.
    assert acc.current_balance == Decimal("9999.000")
    assert acc.total_fee == Decimal("1.000")
    assert acc.total_realized_pnl == Decimal("0")

    # Sanity: float would drift. Confirm we're not on a float path.
    assert isinstance(acc.current_balance, Decimal)
    assert isinstance(acc.total_fee, Decimal)


def test_decimal_str_roundtrip() -> None:
    """Decimal → str → Decimal roundtrip preserves value exactly.

    This validates the DB TEXT storage boundary in state.py: accounts are
    serialized via json.dumps(default=str) and deserialized via
    PaperAccount.model_validate(json.loads(...)). The model_validate
    override in accounts.py routes funding fields through _to_decimal, so
    a stringified Decimal must reconstitute to the same Decimal.
    """
    original = Decimal("12345.67890")
    as_str = str(original)
    restored = Decimal(as_str)

    assert restored == original
    assert str(restored) == str(original)

    # Verify via the PaperAccount model_validate path (the actual DB boundary).
    from selene_space_paper.accounts import PaperAccount

    acct = PaperAccount(
        name="Roundtrip",
        initial_balance=original,
        current_balance=original,
        total_realized_pnl=original,
        total_fee=original,
    )
    dumped = acct.model_dump()
    # model_dump returns Decimal objects; json.dumps(default=str) stringifies them.
    import json

    serialized = json.dumps(dumped, default=str)
    deserialized = json.loads(serialized)
    restored_acct = PaperAccount.model_validate(deserialized)

    assert restored_acct.initial_balance == original
    assert restored_acct.current_balance == original
    assert restored_acct.total_realized_pnl == original
    assert restored_acct.total_fee == original
