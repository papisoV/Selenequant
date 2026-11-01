"""Tests for tgwatcher.batch_checkpoint — crash-safe LLM batch resume.

Verifies:
- save/load round-trip preserves all fields
- load returns None for missing file
- load returns None + logs warning for corrupt JSON
- save is atomic (no partial file on simulated crash)
- delete removes the file
- make_checkpoint fills saved_at automatically
- Global (cross-chat) wrappers use CHAT_ID_GLOBAL sentinel + correct filename
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tgwatcher.batch_checkpoint import (
    CHAT_ID_GLOBAL,
    BatchCheckpoint,
    delete_checkpoint,
    delete_global_checkpoint,
    load_checkpoint,
    load_global_checkpoint,
    make_checkpoint,
    make_global_checkpoint,
    save_checkpoint,
    save_global_checkpoint,
)


@pytest.fixture()
def cp_dir(tmp_path: Path) -> Path:
    """Isolated checkpoint directory per test."""
    return tmp_path / "checkpoints"


class TestSaveLoadRoundTrip:
    def test_round_trip_preserves_all_fields(self, cp_dir: Path):
        cp = BatchCheckpoint(
            chat_id=42,
            last_message_id=1234,
            processed_count=50,
            started_at="2026-07-25T10:00:00Z",
            saved_at="2026-07-25T11:30:00Z",
        )
        save_checkpoint(cp_dir, cp)
        loaded = load_checkpoint(cp_dir, 42)
        assert loaded is not None
        assert loaded.chat_id == 42
        assert loaded.last_message_id == 1234
        assert loaded.processed_count == 50
        assert loaded.started_at == "2026-07-25T10:00:00Z"
        assert loaded.saved_at == "2026-07-25T11:30:00Z"

    def test_file_is_named_per_chat(self, cp_dir: Path):
        cp = BatchCheckpoint(
            chat_id=99, last_message_id=1, processed_count=1,
            started_at="2026-07-25T00:00:00Z", saved_at="2026-07-25T00:00:00Z",
        )
        save_checkpoint(cp_dir, cp)
        assert (cp_dir / "batch_checkpoint_99.json").exists()


class TestLoadMissing:
    def test_load_returns_none_for_missing_file(self, cp_dir: Path):
        assert load_checkpoint(cp_dir, 999) is None


class TestLoadCorrupt:
    def test_load_returns_none_and_logs_for_corrupt_json(self, cp_dir: Path, caplog):
        # Write corrupt JSON directly
        cp_dir.mkdir(parents=True, exist_ok=True)
        (cp_dir / "batch_checkpoint_7.json").write_text("{not valid json", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="tgwatcher.batch_checkpoint"):
            result = load_checkpoint(cp_dir, 7)
        assert result is None
        assert any("corrupt" in r.message.lower() for r in caplog.records)

    def test_load_returns_none_for_missing_required_field(self, cp_dir: Path):
        # Valid JSON but missing required dataclass field
        cp_dir.mkdir(parents=True, exist_ok=True)
        (cp_dir / "batch_checkpoint_8.json").write_text(
            json.dumps({"chat_id": 8, "last_message_id": 1}),  # missing processed_count, started_at, saved_at
            encoding="utf-8",
        )
        # dataclass TypeError on missing fields — caught, returns None
        assert load_checkpoint(cp_dir, 8) is None


class TestAtomicWrite:
    def test_save_leaves_no_tmp_file(self, cp_dir: Path):
        cp = BatchCheckpoint(
            chat_id=1, last_message_id=1, processed_count=1,
            started_at="2026-07-25T00:00:00Z", saved_at="2026-07-25T00:00:00Z",
        )
        save_checkpoint(cp_dir, cp)
        # After save, .tmp file should not linger
        assert not (cp_dir / "batch_checkpoint_1.tmp").exists()
        assert (cp_dir / "batch_checkpoint_1.json").exists()

    def test_save_overwrites_previous_checkpoint(self, cp_dir: Path):
        cp1 = BatchCheckpoint(
            chat_id=1, last_message_id=10, processed_count=10,
            started_at="2026-07-25T00:00:00Z", saved_at="2026-07-25T00:10:00Z",
        )
        save_checkpoint(cp_dir, cp1)
        cp2 = BatchCheckpoint(
            chat_id=1, last_message_id=20, processed_count=20,
            started_at="2026-07-25T00:00:00Z", saved_at="2026-07-25T00:20:00Z",
        )
        save_checkpoint(cp_dir, cp2)
        loaded = load_checkpoint(cp_dir, 1)
        assert loaded is not None
        assert loaded.last_message_id == 20
        assert loaded.processed_count == 20


class TestDelete:
    def test_delete_removes_file(self, cp_dir: Path):
        cp = BatchCheckpoint(
            chat_id=5, last_message_id=1, processed_count=1,
            started_at="2026-07-25T00:00:00Z", saved_at="2026-07-25T00:00:00Z",
        )
        save_checkpoint(cp_dir, cp)
        assert (cp_dir / "batch_checkpoint_5.json").exists()
        delete_checkpoint(cp_dir, 5)
        assert not (cp_dir / "batch_checkpoint_5.json").exists()

    def test_delete_missing_file_is_noop(self, cp_dir: Path):
        # missing_ok=True — should not raise
        delete_checkpoint(cp_dir, 999)


class TestMakeCheckpoint:
    def test_make_checkpoint_fills_saved_at(self):
        cp = make_checkpoint(
            chat_id=1, last_message_id=100, processed_count=50,
            started_at="2026-07-25T10:00:00Z",
        )
        assert cp.chat_id == 1
        assert cp.last_message_id == 100
        assert cp.processed_count == 50
        assert cp.started_at == "2026-07-25T10:00:00Z"
        # saved_at auto-filled with current UTC ISO time
        assert cp.saved_at is not None
        assert cp.saved_at.endswith("Z")


class TestSimulatedResume:
    def test_simulated_crash_then_resume(self, cp_dir: Path):
        """End-to-end: save checkpoint after 3 messages, 'crash', resume.

        On resume, load_checkpoint returns the saved state. Caller skips
        messages with id <= cp.last_message_id.
        """
        # Save checkpoint after processing msg 3
        cp = BatchCheckpoint(
            chat_id=42, last_message_id=3, processed_count=3,
            started_at="2026-07-25T10:00:00Z", saved_at="2026-07-25T10:05:00Z",
        )
        save_checkpoint(cp_dir, cp)

        # Simulate crash + restart — load checkpoint
        loaded = load_checkpoint(cp_dir, 42)
        assert loaded is not None
        assert loaded.last_message_id == 3

        # Caller would skip messages 1,2,3 and resume from 4
        pending_messages = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}, {"id": 5}]
        to_process = [m for m in pending_messages if m["id"] > loaded.last_message_id]
        assert [m["id"] for m in to_process] == [4, 5]

        # On completion, caller deletes checkpoint
        delete_checkpoint(cp_dir, 42)
        assert load_checkpoint(cp_dir, 42) is None


class TestGlobalCheckpoint:
    """Global (cross-chat) wrappers — used by batch scripts that process
    messages across all chats in a single stream."""

    def test_save_global_uses_global_filename(self, cp_dir: Path):
        cp = make_global_checkpoint(
            last_message_id=500, processed_count=100,
            started_at="2026-07-25T10:00:00Z",
        )
        save_global_checkpoint(cp_dir, cp)
        # File should be batch_checkpoint_global.json, not batch_checkpoint_0.json
        assert (cp_dir / "batch_checkpoint_global.json").exists()
        assert not (cp_dir / "batch_checkpoint_0.json").exists()

    def test_load_global_returns_none_when_missing(self, cp_dir: Path):
        assert load_global_checkpoint(cp_dir) is None

    def test_save_then_load_global_round_trip(self, cp_dir: Path):
        cp = make_global_checkpoint(
            last_message_id=999, processed_count=42,
            started_at="2026-07-25T10:00:00Z",
        )
        save_global_checkpoint(cp_dir, cp)
        loaded = load_global_checkpoint(cp_dir)
        assert loaded is not None
        assert loaded.chat_id == CHAT_ID_GLOBAL
        assert loaded.last_message_id == 999
        assert loaded.processed_count == 42

    def test_save_global_forces_chat_id_to_zero(self, cp_dir: Path):
        """Even if caller passes a non-zero chat_id, the global wrapper
        forces it to CHAT_ID_GLOBAL so the file lands at the global path."""
        cp = BatchCheckpoint(
            chat_id=42,  # wrong — should be 0 for global
            last_message_id=100, processed_count=10,
            started_at="2026-07-25T10:00:00Z", saved_at="2026-07-25T10:05:00Z",
        )
        save_global_checkpoint(cp_dir, cp)
        loaded = load_global_checkpoint(cp_dir)
        assert loaded is not None
        assert loaded.chat_id == CHAT_ID_GLOBAL
        assert loaded.last_message_id == 100

    def test_delete_global_removes_file(self, cp_dir: Path):
        cp = make_global_checkpoint(
            last_message_id=1, processed_count=1,
            started_at="2026-07-25T10:00:00Z",
        )
        save_global_checkpoint(cp_dir, cp)
        assert (cp_dir / "batch_checkpoint_global.json").exists()
        delete_global_checkpoint(cp_dir)
        assert not (cp_dir / "batch_checkpoint_global.json").exists()

    def test_delete_global_missing_is_noop(self, cp_dir: Path):
        delete_global_checkpoint(cp_dir)  # should not raise

    def test_make_global_checkpoint_sets_chat_id_zero(self):
        cp = make_global_checkpoint(
            last_message_id=1, processed_count=1,
            started_at="2026-07-25T10:00:00Z",
        )
        assert cp.chat_id == CHAT_ID_GLOBAL
        assert cp.chat_id == 0

    def test_simulated_crash_resume_global(self, cp_dir: Path):
        """End-to-end: global checkpoint saved mid-run, 'crash', resume."""
        cp = make_global_checkpoint(
            last_message_id=1500, processed_count=300,
            started_at="2026-07-25T10:00:00Z",
        )
        save_global_checkpoint(cp_dir, cp)

        loaded = load_global_checkpoint(cp_dir)
        assert loaded is not None
        assert loaded.last_message_id == 1500

        # Caller skips messages with message_id <= 1500
        pending = [{"id": 1499}, {"id": 1500}, {"id": 1501}, {"id": 1502}]
        to_process = [m for m in pending if m["id"] > loaded.last_message_id]
        assert [m["id"] for m in to_process] == [1501, 1502]

        delete_global_checkpoint(cp_dir)
        assert load_global_checkpoint(cp_dir) is None
