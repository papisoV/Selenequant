"""Batch checkpoint / resume support for long-running LLM batch jobs.

A batch run processes hundreds-to-thousands of messages, taking hours. If
the process crashes mid-run, all progress is lost. This module provides
atomic JSON checkpoint persistence, so a crash only loses at most
``max_batch_size - 1`` items.

Design:
- One JSON file per checkpoint scope. For per-chat scope:
  ``batch_checkpoint_{chat_id}.json``. For global (cross-chat) scope:
  ``batch_checkpoint_global.json`` (uses ``CHAT_ID_GLOBAL = 0`` sentinel).
- Atomic write: ``tmp.replace(path)`` — POSIX and Windows both guarantee
  atomicity for ``os.replace`` / ``Path.replace``
- Corrupt-file tolerant: ``_load_checkpoint`` returns None + logs warning
  if JSON parse fails (treats as fresh start)
- Caller owns the lifecycle: load at start, save every N successes,
  delete on batch completion

Usage (per-chat):
    from tgwatcher.batch_checkpoint import (
        BatchCheckpoint, save_checkpoint, load_checkpoint, delete_checkpoint,
    )

    cp = load_checkpoint("./batch_checkpoints", chat_id=123)
    start_msg_id = cp.last_message_id if cp else 0
    # ... process messages with id > start_msg_id ...
    save_checkpoint("./batch_checkpoints", BatchCheckpoint(
        chat_id=123, last_message_id=msg["id"],
        processed_count=i + 1, started_at=start_iso, saved_at=now_iso,
    ))
    delete_checkpoint("./batch_checkpoints", chat_id=123)

Usage (global / cross-chat):
    from tgwatcher.batch_checkpoint import (
        CHAT_ID_GLOBAL, save_global_checkpoint, load_global_checkpoint,
        delete_global_checkpoint, make_global_checkpoint,
    )

    cp = load_global_checkpoint("./batch_checkpoints")
    # ... process messages with message_id > cp.last_message_id ...
    save_global_checkpoint("./batch_checkpoints", cp)
    delete_global_checkpoint("./batch_checkpoints")
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Sentinel chat_id for cross-chat / global checkpoints. Use this when
# the batch processes messages across all chats in a single stream.
CHAT_ID_GLOBAL: int = 0

# Filename for the global checkpoint (uses CHAT_ID_GLOBAL sentinel).
_GLOBAL_FILENAME: str = "batch_checkpoint_global.json"


@dataclass(frozen=True)
class BatchCheckpoint:
    """Persistent state of an in-flight LLM batch run.

    For per-chat scope, ``chat_id`` is the actual chat id.
    For global scope, ``chat_id`` is ``CHAT_ID_GLOBAL`` (0).
    """
    chat_id: int
    last_message_id: int
    processed_count: int
    started_at: str  # ISO 8601
    saved_at: str    # ISO 8601


def _checkpoint_path(checkpoint_dir: str | Path, chat_id: int) -> Path:
    """Return the checkpoint file path for one scope.

    For ``chat_id=CHAT_ID_GLOBAL`` returns the global checkpoint path.
    Otherwise returns ``batch_checkpoint_{chat_id}.json``.
    """
    if chat_id == CHAT_ID_GLOBAL:
        return Path(checkpoint_dir) / _GLOBAL_FILENAME
    return Path(checkpoint_dir) / f"batch_checkpoint_{chat_id}.json"


def _iso_now() -> str:
    """Return ISO 8601 UTC timestamp with Z suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def save_checkpoint(checkpoint_dir: str | Path, cp: BatchCheckpoint) -> None:
    """Atomically persist a checkpoint.

    Writes to a ``.tmp`` sibling file then ``Path.replace`` renames it
    into place — on both POSIX and Windows this is atomic, so a crash
    mid-write leaves either the old checkpoint or the new one, never a
    corrupt partial file.
    """
    path = _checkpoint_path(checkpoint_dir, cp.chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(cp), indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic
    logger.info(
        "Batch checkpoint saved",
        extra={
            "chat_id": cp.chat_id,
            "last_msg_id": cp.last_message_id,
            "processed": cp.processed_count,
            "action": "llm_checkpoint",
        },
    )


def load_checkpoint(checkpoint_dir: str | Path, chat_id: int) -> BatchCheckpoint | None:
    """Load a checkpoint, returning None if missing or corrupt.

    Corrupt files (partial write before crash, manual edit, encoding
    drift) log a warning and return None so the caller treats it as a
    fresh start — better to reprocess some messages than to crash the
    batch. Idempotent upserts in ``storage.save_signal_factor`` make
    reprocessing safe.
    """
    path = _checkpoint_path(checkpoint_dir, chat_id)
    if not path.exists():
        return None
    try:
        return BatchCheckpoint(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as e:
        logger.warning(
            "Batch checkpoint corrupt, ignoring and starting fresh",
            extra={"chat_id": chat_id, "error": str(e), "action": "llm_checkpoint_corrupt"},
        )
        return None


def delete_checkpoint(checkpoint_dir: str | Path, chat_id: int) -> None:
    """Delete checkpoint file (called on batch completion)."""
    path = _checkpoint_path(checkpoint_dir, chat_id)
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning(
            "Failed to delete checkpoint",
            extra={"chat_id": chat_id, "error": str(e), "action": "llm_checkpoint_cleanup"},
        )


def make_checkpoint(chat_id: int, last_message_id: int, processed_count: int, started_at: str) -> BatchCheckpoint:
    """Convenience factory — fills saved_at with current UTC time."""
    return BatchCheckpoint(
        chat_id=chat_id,
        last_message_id=last_message_id,
        processed_count=processed_count,
        started_at=started_at,
        saved_at=_iso_now(),
    )


# ── Global (cross-chat) convenience wrappers ────────────────────────────

def save_global_checkpoint(checkpoint_dir: str | Path, cp: BatchCheckpoint) -> None:
    """Save a global (cross-chat) checkpoint.

    ``cp.chat_id`` is forced to ``CHAT_ID_GLOBAL`` to ensure the file
    lands at ``batch_checkpoint_global.json`` regardless of what the
    caller passed.
    """
    if cp.chat_id != CHAT_ID_GLOBAL:
        cp = BatchCheckpoint(
            chat_id=CHAT_ID_GLOBAL,
            last_message_id=cp.last_message_id,
            processed_count=cp.processed_count,
            started_at=cp.started_at,
            saved_at=cp.saved_at,
        )
    save_checkpoint(checkpoint_dir, cp)


def load_global_checkpoint(checkpoint_dir: str | Path) -> BatchCheckpoint | None:
    """Load the global (cross-chat) checkpoint, or None if missing/corrupt."""
    return load_checkpoint(checkpoint_dir, CHAT_ID_GLOBAL)


def delete_global_checkpoint(checkpoint_dir: str | Path) -> None:
    """Delete the global (cross-chat) checkpoint."""
    delete_checkpoint(checkpoint_dir, CHAT_ID_GLOBAL)


def make_global_checkpoint(last_message_id: int, processed_count: int, started_at: str) -> BatchCheckpoint:
    """Convenience factory for global checkpoints — chat_id forced to 0."""
    return make_checkpoint(
        chat_id=CHAT_ID_GLOBAL,
        last_message_id=last_message_id,
        processed_count=processed_count,
        started_at=started_at,
    )
