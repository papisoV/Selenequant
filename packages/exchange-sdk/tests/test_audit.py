"""Tests for the hash-chained audit log."""

from __future__ import annotations

import json

from selene_space_exchange.audit import AuditLog, AuditEntry, _compute_hash


class TestAuditLogAppend:
    """Tests for appending entries to the audit log."""

    def test_append_creates_entry_with_correct_fields(self):
        log = AuditLog()
        entry = log.append("order.place", "BTC/USDT", {"side": "buy", "qty": 0.1})
        assert isinstance(entry, AuditEntry)
        assert entry.action == "order.place"
        assert entry.symbol == "BTC/USDT"
        assert entry.details == {"side": "buy", "qty": 0.1}
        assert entry.timestamp_ms > 0

    def test_append_first_entry_uses_genesis_hash(self):
        log = AuditLog()
        entry = log.append("order.place", "BTC/USDT")
        assert entry.prev_hash == "0" * 64

    def test_append_chains_entries(self):
        log = AuditLog()
        e1 = log.append("order.place", "BTC/USDT")
        e2 = log.append("order.cancel", "BTC/USDT", {"order_id": "abc"})
        assert e2.prev_hash == e1.entry_hash
        assert e1.entry_hash != e2.entry_hash

    def test_append_default_details_empty_dict(self):
        log = AuditLog()
        entry = log.append("order.place", "ETH/USDT")
        assert entry.details == {}

    def test_hash_computed_correctly(self):
        log = AuditLog()
        entry = log.append("order.place", "BTC/USDT", {"side": "buy"})
        expected = _compute_hash(entry.prev_hash, entry.timestamp_ms, entry.action, entry.symbol, entry.details)
        assert entry.entry_hash == expected

    def test_get_entries_returns_all(self):
        log = AuditLog()
        log.append("order.place", "BTC/USDT")
        log.append("order.cancel", "ETH/USDT")
        log.append("deposit", "USDT")
        assert len(log.get_entries()) == 3


class TestAuditLogVerify:
    """Tests for verifying the hash chain integrity."""

    def test_verify_unmodified_chain(self):
        log = AuditLog()
        log.append("order.place", "BTC/USDT")
        log.append("order.cancel", "ETH/USDT")
        log.append("deposit", "USDT")
        assert log.verify() is True

    def test_verify_empty_log(self):
        log = AuditLog()
        assert log.verify() is True

    def test_verify_detects_tampered_entry(self):
        log = AuditLog()
        log.append("order.place", "BTC/USDT")
        log.append("order.cancel", "ETH/USDT")
        # Tamper with the first entry by replacing it
        original = log._entries[0]
        tampered = AuditEntry(
            timestamp_ms=original.timestamp_ms,
            action="order.cancel",  # changed action
            symbol=original.symbol,
            details=original.details,
            prev_hash=original.prev_hash,
            entry_hash=original.entry_hash,  # hash not recomputed
        )
        log._entries[0] = tampered
        assert log.verify() is False

    def test_verify_detects_broken_chain_link(self):
        log = AuditLog()
        log.append("order.place", "BTC/USDT")
        log.append("order.cancel", "ETH/USDT")
        # Break the chain by modifying prev_hash of second entry
        original = log._entries[1]
        broken = AuditEntry(
            timestamp_ms=original.timestamp_ms,
            action=original.action,
            symbol=original.symbol,
            details=original.details,
            prev_hash="badhash",  # corrupted prev_hash
            entry_hash=original.entry_hash,
        )
        log._entries[1] = broken
        assert log.verify() is False


class TestAuditLogGenesisHash:
    """Tests for genesis hash."""

    def test_genesis_hash_is_64_zeros(self):
        log = AuditLog()
        assert log.get_last_hash() == "0" * 64

    def test_genesis_hash_constant(self):
        assert AuditLog._GENESIS_HASH == "0" * 64
        assert len(AuditLog._GENESIS_HASH) == 64


class TestAuditLogSerialization:
    """Tests for JSON serialization and deserialization."""

    def test_to_json_from_json_roundtrip(self):
        log = AuditLog()
        log.append("order.place", "BTC/USDT", {"side": "buy"})
        log.append("order.cancel", "ETH/USDT", {"order_id": "123"})
        log.append("deposit", "USDT")

        json_str = log.to_json()
        restored = AuditLog.from_json(json_str)

        original_entries = log.get_entries()
        restored_entries = restored.get_entries()
        assert len(original_entries) == len(restored_entries)
        for orig, rest in zip(original_entries, restored_entries):
            assert orig.timestamp_ms == rest.timestamp_ms
            assert orig.action == rest.action
            assert orig.symbol == rest.symbol
            assert orig.details == rest.details
            assert orig.prev_hash == rest.prev_hash
            assert orig.entry_hash == rest.entry_hash

    def test_from_json_verifies_chain(self):
        log = AuditLog()
        log.append("order.place", "BTC/USDT")
        json_str = log.to_json()

        # Tamper with the JSON
        data = json.loads(json_str)
        data[0]["action"] = "tampered"
        tampered_json = json.dumps(data)

        try:
            AuditLog.from_json(tampered_json)
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "verification failed" in str(e).lower()

    def test_to_json_empty_log(self):
        log = AuditLog()
        assert log.to_json() == "[]"

    def test_from_json_empty_log(self):
        log = AuditLog.from_json("[]")
        assert len(log.get_entries()) == 0
        assert log.verify() is True

    def test_get_last_hash_after_entries(self):
        log = AuditLog()
        entry = log.append("order.place", "BTC/USDT")
        assert log.get_last_hash() == entry.entry_hash
