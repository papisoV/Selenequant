"""T3: FeeLedger — SQLite CRUD + idempotent record."""

from __future__ import annotations

from selene_space_risk.fee_ledger import FeeLedger, FeeSnapshot


def _snap(
    ts: int,
    account_id: str = "live",
    exchange: str = "aggregated",
    rate: float = 0.0005,
) -> FeeSnapshot:
    return FeeSnapshot(
        ts=ts,
        account_id=account_id,
        exchange=exchange,
        taker_rate=rate,
        maker_rate=0.0,
        effective_rate=rate,
        implied_tier="unknown",
        source="test",
        created_at=ts,
    )


def test_record_then_latest(tmp_path) -> None:
    ledger = FeeLedger(tmp_path / "fee.db")
    ledger.record(_snap(ts=1000, rate=0.0005))
    ledger.record(_snap(ts=2000, rate=0.0007))
    latest = ledger.latest("live")
    assert latest is not None
    assert latest.ts == 2000
    assert latest.effective_rate == 0.0007


def test_record_idempotent_on_ts_account_exchange(tmp_path) -> None:
    ledger = FeeLedger(tmp_path / "fee.db")
    ledger.record(_snap(ts=1000, rate=0.0005))
    ledger.record(_snap(ts=1000, rate=0.0005))  # duplicate key → no-op
    snaps = ledger.query_range("live", 0, 2000)
    assert len(snaps) == 1


def test_query_range_inclusive_start_exclusive_end(tmp_path) -> None:
    ledger = FeeLedger(tmp_path / "fee.db")
    for ts in (100, 200, 300):
        ledger.record(_snap(ts=ts))
    snaps = ledger.query_range("live", 200, 400)
    assert [s.ts for s in snaps] == [200, 300]


def test_latest_none_when_empty(tmp_path) -> None:
    ledger = FeeLedger(tmp_path / "fee.db")
    assert ledger.latest("live") is None


def test_record_isolates_by_account(tmp_path) -> None:
    """Same ts but different account_id → distinct rows (PK includes account)."""
    ledger = FeeLedger(tmp_path / "fee.db")
    ledger.record(_snap(ts=1000, account_id="live"))
    ledger.record(_snap(ts=1000, account_id="paper"))
    assert ledger.latest("live") is not None
    assert ledger.latest("paper") is not None
    assert len(ledger.query_range("live", 0, 2000)) == 1
    assert len(ledger.query_range("paper", 0, 2000)) == 1
