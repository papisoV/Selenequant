"""Unit tests for ChannelSequenceManager (protocol §3.2, §4 Step 4, §5).

Covers:
- next() monotonic per seq_key.
- Cross-key independence (paper vs trading namespaces).
- watermark() shape (AccountStateSeq) with zero defaults, single-key
  replication (paper), and per-channel counters (trading).
- Module-level set/get singleton wiring.
"""

from __future__ import annotations

from app.core.sequence import (
    ChannelSequenceManager,
    get_channel_sequence,
    set_channel_sequence,
)
from selene_space_types.trading import AccountStateSeq


class TestNext:
    def test_next_monotonic_per_key(self) -> None:
        mgr = ChannelSequenceManager(session_id="srv-1")
        assert mgr.next("paper:acct-1") == 1
        assert mgr.next("paper:acct-1") == 2
        assert mgr.next("paper:acct-1") == 3

    def test_keys_independent(self) -> None:
        mgr = ChannelSequenceManager(session_id="srv-1")
        mgr.next("local:orders")
        mgr.next("local:orders")
        assert mgr.next("local:positions") == 1
        assert mgr.next("local:balances") == 1
        assert mgr.next("paper:acct-1") == 1


class TestWatermark:
    def test_zero_default_shape(self) -> None:
        mgr = ChannelSequenceManager(session_id="srv-1")
        assert mgr.watermark() == AccountStateSeq()
        assert mgr.watermark(orders="local:orders") == AccountStateSeq(orders=0)

    def test_paper_single_key_replicated(self) -> None:
        mgr = ChannelSequenceManager(session_id="srv-1")
        mgr.next("paper:acct-1")
        mgr.next("paper:acct-1")
        w = mgr.watermark(
            orders="paper:acct-1",
            positions="paper:acct-1",
            balances="paper:acct-1",
        )
        assert w == AccountStateSeq(orders=2, positions=2, balances=2)

    def test_trading_per_channel_counters(self) -> None:
        mgr = ChannelSequenceManager(session_id="srv-1")
        mgr.next("local:orders")
        mgr.next("local:orders")
        mgr.next("local:balances")
        w = mgr.watermark(
            orders="local:orders",
            positions="local:positions",
            balances="local:balances",
        )
        assert w == AccountStateSeq(orders=2, positions=0, balances=1)


class TestSingleton:
    def test_set_get_roundtrip(self) -> None:
        mgr = ChannelSequenceManager(session_id="srv-1")
        set_channel_sequence(mgr)
        assert get_channel_sequence() is mgr
        set_channel_sequence(None)
        assert get_channel_sequence() is None
