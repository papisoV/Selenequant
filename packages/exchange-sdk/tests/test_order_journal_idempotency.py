"""Tests for OrderJournal Phase 2.5 idempotency guarantees.

Verifies:
  - mark_confirmed on already-confirmed order is a no-op
  - mark_confirmed rejects transition from failed/expired terminal states
  - mark_failed on already-failed order is a no-op
  - mark_expired on already-expired order is a no-op
  - v2_idempotency migration adds UNIQUE constraint on exchange_order_id
  - duplicate exchange_order_id insert raises IntegrityError (after migration)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from selene_space_exchange.execution.order_journal import OrderJournal


@pytest.fixture
def journal(tmp_path: Path) -> OrderJournal:
    return OrderJournal(journal_dir=tmp_path / "journal")


def _seed_submitted(journal: OrderJournal, client_order_id: str = "co-1") -> None:
    """Helper: append intent then mark submitted with an exchange_order_id."""
    journal.append_intent(
        client_order_id=client_order_id,
        symbol="BTC-USDT-SWAP",
        side="buy",
        order_type="limit",
        quantity=1.0,
        price=65000.0,
        account_id="pa-abc",
    )
    journal.mark_submitted(client_order_id, exchange_order_id="ex-XYZ")


def test_mark_confirmed_idempotent_on_second_call(journal: OrderJournal) -> None:
    """mark_confirmed called twice should result in only one state transition."""
    _seed_submitted(journal, "co-dup")

    journal.mark_confirmed("co-dup")
    entry_after_first = journal.get_entry("co-dup")
    assert entry_after_first is not None
    assert entry_after_first.status == "confirmed"
    first_updated = entry_after_first.updated_at

    # Second call should be a no-op: status remains confirmed, updated_at unchanged.
    journal.mark_confirmed("co-dup")
    entry_after_second = journal.get_entry("co-dup")
    assert entry_after_second is not None
    assert entry_after_second.status == "confirmed"
    assert entry_after_second.updated_at == first_updated


def test_mark_confirmed_rejects_transition_from_failed(journal: OrderJournal) -> None:
    """If order is already failed, mark_confirmed must NOT flip it to confirmed."""
    _seed_submitted(journal, "co-failed")
    journal.mark_failed("co-failed", reason="exchange rejected")

    # Attempt to confirm a failed order (e.g. race: WS fill arrives after reject)
    journal.mark_confirmed("co-failed")

    entry = journal.get_entry("co-failed")
    assert entry is not None
    assert entry.status == "failed", "mark_confirmed must not override failed terminal state"


def test_mark_failed_idempotent_on_second_call(journal: OrderJournal) -> None:
    """mark_failed called twice should not corrupt state or change reason twice."""
    _seed_submitted(journal, "co-fail-twice")

    journal.mark_failed("co-fail-twice", reason="first failure")
    entry_after_first = journal.get_entry("co-fail-twice")
    assert entry_after_first is not None
    assert entry_after_first.status == "failed"
    assert entry_after_first.reason == "first failure"

    # Second call with different reason should be no-op (idempotent).
    journal.mark_failed("co-fail-twice", reason="second failure")
    entry_after_second = journal.get_entry("co-fail-twice")
    assert entry_after_second is not None
    assert entry_after_second.status == "failed"
    assert entry_after_second.reason == "first failure", "second mark_failed must not overwrite reason"


def test_mark_expired_idempotent_on_second_call(journal: OrderJournal) -> None:
    """mark_expired called twice should be a no-op on second call."""
    _seed_submitted(journal, "co-exp-twice")

    journal.mark_expired("co-exp-twice", reason="GTD elapsed")
    entry_after_first = journal.get_entry("co-exp-twice")
    assert entry_after_first is not None
    assert entry_after_first.status == "expired"

    journal.mark_expired("co-exp-twice", reason="second expire attempt")
    entry_after_second = journal.get_entry("co-exp-twice")
    assert entry_after_second is not None
    assert entry_after_second.status == "expired"
    assert entry_after_second.reason == "GTD elapsed"


def test_mark_failed_rejects_transition_from_confirmed(journal: OrderJournal) -> None:
    """If order is already confirmed, mark_failed must NOT flip it to failed."""
    _seed_submitted(journal, "co-confirmed")
    journal.mark_confirmed("co-confirmed")

    journal.mark_failed("co-confirmed", reason="late reject attempt")

    entry = journal.get_entry("co-confirmed")
    assert entry is not None
    assert entry.status == "confirmed", "mark_failed must not override confirmed terminal state"


def test_mark_expired_rejects_transition_from_confirmed(journal: OrderJournal) -> None:
    """If order is already confirmed, mark_expired must NOT flip it to expired."""
    _seed_submitted(journal, "co-conf-exp")
    journal.mark_confirmed("co-conf-exp")

    journal.mark_expired("co-conf-exp", reason="late expire attempt")

    entry = journal.get_entry("co-conf-exp")
    assert entry is not None
    assert entry.status == "confirmed", "mark_expired must not override confirmed terminal state"


def test_mark_expired_rejects_transition_from_failed(journal: OrderJournal) -> None:
    """If order is already failed, mark_expired must NOT flip it to expired."""
    _seed_submitted(journal, "co-failed-exp")
    journal.mark_failed("co-failed-exp", reason="rejected by exchange")

    journal.mark_expired("co-failed-exp", reason="late expire attempt")

    entry = journal.get_entry("co-failed-exp")
    assert entry is not None
    assert entry.status == "failed", "mark_expired must not override failed terminal state"


def test_v2_migration_adds_unique_on_exchange_order_id(tmp_path: Path) -> None:
    """v2_idempotency migration must add UNIQUE constraint on exchange_order_id."""
    journal = OrderJournal(journal_dir=tmp_path / "journal")
    db_path = journal._db_path  # type: ignore[attr-defined]

    # Connect and inspect schema for UNIQUE on exchange_order_id
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='order_journal'"
        )
        row = cur.fetchone()
        assert row is not None
        create_sql = row[0].upper()
        assert "EXCHANGE_ORDER_ID TEXT UNIQUE" in create_sql, (
            "v2 migration must add UNIQUE constraint on exchange_order_id"
        )
    finally:
        con.close()


def test_duplicate_exchange_order_id_raises_integrity_error(journal: OrderJournal) -> None:
    """After v2 migration, inserting two rows with same exchange_order_id must fail."""
    # Seed first order with exchange_order_id="ex-DUP"
    _seed_submitted(journal, "co-first")
    # mark_submitted set exchange_order_id="ex-XYZ"; manually insert a second
    # row sharing the same exchange_order_id to test UNIQUE constraint.
    with pytest.raises(sqlite3.IntegrityError):
        journal.append_intent(
            client_order_id="co-second",
            symbol="BTC-USDT-SWAP",
            side="sell",
            order_type="limit",
            quantity=1.0,
            price=66000.0,
            account_id="pa-abc",
        )
        # Force the duplicate exchange_order_id by direct UPDATE (bypass mark_submitted
        # which guards with status='intent' WHERE clause — still goes through).
        with journal._connect() as con:  # type: ignore[attr-defined]
            con.execute(
                "UPDATE order_journal SET exchange_order_id='ex-DUP' "
                "WHERE client_order_id='co-second'",
            )
        # Now seed first order to same exchange_order_id
        with journal._connect() as con:  # type: ignore[attr-defined]
            con.execute(
                "UPDATE order_journal SET exchange_order_id='ex-DUP' "
                "WHERE client_order_id='co-first'",
            )


def test_migration_is_idempotent(journal: OrderJournal) -> None:
    """Running v2_idempotency.migrate twice must not error (idempotent)."""
    from selene_space_exchange.execution.migrations.v2_idempotency import (
        _has_unique_on_exchange_order_id,
        migrate,
    )

    with journal._connect() as con:  # type: ignore[attr-defined]
        # First call already ran in __init__; second call must be a no-op.
        assert _has_unique_on_exchange_order_id(con) is True
        migrate(con)  # should not raise
        assert _has_unique_on_exchange_order_id(con) is True
