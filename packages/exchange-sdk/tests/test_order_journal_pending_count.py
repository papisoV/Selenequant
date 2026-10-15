"""Tests for OrderJournal.get_pending_count() — Phase 5 WAL lag indicator."""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_exchange.execution.order_journal import OrderJournal
from selene_space_types.trading import OrderSide, OrderType


@pytest.fixture
def journal(tmp_path: Path) -> OrderJournal:
    return OrderJournal(journal_dir=tmp_path / "journal")


def test_pending_count_returns_zero_initially(journal: OrderJournal) -> None:
    """Empty journal → 0 pending."""
    assert journal.get_pending_count() == 0


def test_pending_count_returns_submitted_and_intent_only(journal: OrderJournal) -> None:
    """INTENT + SUBMITTED counted; CONFIRMED/FAILED/EXPIRED not."""
    # 2 intents
    jid1 = journal.append_intent(
        client_order_id="c-1", account_id="pa-1", symbol="BTCUSDT", side=OrderSide.BUY,
        order_type=OrderType.MARKET, quantity=0.01,
    )
    jid2 = journal.append_intent(
        client_order_id="c-2", account_id="pa-1", symbol="ETHUSDT", side=OrderSide.SELL,
        order_type=OrderType.LIMIT, quantity=0.1, price=2000.0,
    )
    assert journal.get_pending_count() == 2  # 2 intents

    # Mark c-1 submitted → still pending (submitted = sent to REST, not confirmed)
    journal.mark_submitted("c-1", exchange_order_id="ex-1")
    assert journal.get_pending_count() == 2

    # Mark c-1 confirmed → drops to 1
    journal.mark_confirmed("c-1")
    assert journal.get_pending_count() == 1

    # Mark c-2 failed → drops to 0
    journal.mark_failed("c-2", reason="test")
    assert journal.get_pending_count() == 0


def test_pending_count_ignores_expired(journal: OrderJournal) -> None:
    """EXPIRED entries not counted."""
    jid = journal.append_intent(
        client_order_id="c-3", account_id="pa-1", symbol="BTCUSDT", side=OrderSide.BUY,
        order_type=OrderType.MARKET, quantity=0.01,
    )
    journal.mark_expired("c-3", reason="timeout")
    assert journal.get_pending_count() == 0
