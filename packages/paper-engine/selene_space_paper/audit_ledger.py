"""Hash-chained, fsynced, append-only JSONL audit ledger (C1).

学 Vibe-Trading governance/ledger.py: 每条记录 embed ``seq`` +
``prev_record_hash``, ``record_hash`` 同时折叠两者与 payload——改动/删除
任何早前记录都会使后续整条链失效（:func:`verify_chain` 可检出）。

设计取舍（v3 plan 定案）:
- 单链文件 ``decisions.chain.jsonl``——SeleneQuant 无既有 decisions.jsonl
  reader,"双文件兼容"论据不成立,避免未使用 reader 的迁移负担。
- append 前验整链拒坏链续写（LedgerCorruptionError）——O(n) 可接受,
  订单级低频写入（Vibe 同款论证）。
- fsync 每写;Windows 不加 byte-range lock（paper 单进程, YAGNI）。
- payload 先 redact 后落盘: reason 字段可能携带用户自由文本。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_GENESIS_PREV_HASH = "0" * 8

# payload 里可能携带自由文本的键 → 只保留结构化字段,自由文本截断
_REDACT_MAX_TEXT = 200
_REDACT_KEYS = ("reason", "notes", "message")


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """最小 redact: 自由文本键截断 + 递归剔除明显的 secret 形状键."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if any(s in key.lower() for s in ("key", "secret", "token", "password")):
            out[key] = "[redacted]"
        elif key in _REDACT_KEYS and isinstance(value, str):
            out[key] = value[:_REDACT_MAX_TEXT]
        else:
            out[key] = value
    return out


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class LedgerEntry:
    seq: int
    payload: dict[str, Any] = field(default_factory=dict)
    prev_hash: str = _GENESIS_PREV_HASH
    record_hash: str = ""

    def to_line(self) -> str:
        return json.dumps(
            {
                "seq": self.seq,
                "payload": self.payload,
                "prev_hash": self.prev_hash,
                "record_hash": self.record_hash,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    @staticmethod
    def from_line(line: str) -> "LedgerEntry | None":
        try:
            raw = json.loads(line)
            return LedgerEntry(
                seq=int(raw["seq"]),
                payload=dict(raw.get("payload") or {}),
                prev_hash=str(raw.get("prev_hash") or ""),
                record_hash=str(raw.get("record_hash") or ""),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def compute_hash(payload: dict[str, Any], prev_hash: str) -> str:
        return _short_hash(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + prev_hash
        )


class LedgerCorruptionError(RuntimeError):
    """链已损坏（篡改/截断/坏行）——拒绝续写。"""


class HashChainLedger:
    """Append-only hash chain over a JSONL file."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # -- read path ------------------------------------------------------

    def read_chain(self) -> list[LedgerEntry]:
        entries: list[LedgerEntry] = []
        if not self._path.exists():
            return entries
        with open(self._path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry = LedgerEntry.from_line(line)
                if entry is None:
                    raise LedgerCorruptionError(
                        f"unreadable line at seq~{len(entries) + 1}"
                    )
                entries.append(entry)
        return entries

    def verify_chain(self) -> tuple[bool, int]:
        """全链验证。返回 (ok, 第一个坏 seq)；ok 时坏 seq = 0。"""
        entries = self.read_chain()
        prev = _GENESIS_PREV_HASH
        for entry in entries:
            if entry.seq != len(entries[: entry.seq]) and entry.seq <= 0:
                return False, entry.seq
            if entry.prev_hash != prev:
                return False, entry.seq
            expected = LedgerEntry.compute_hash(entry.payload, entry.prev_hash)
            if entry.record_hash != expected:
                return False, entry.seq
            prev = entry.record_hash
        return True, 0

    # -- write path -----------------------------------------------------

    def append(self, payload: dict[str, Any]) -> LedgerEntry:
        """追加一条。链坏即抛 LedgerCorruptionError,不静默续写。"""
        ok, bad_seq = self.verify_chain()
        if not ok:
            raise LedgerCorruptionError(
                f"chain broken at seq {bad_seq}, refusing to append"
            )

        entries = self.read_chain()
        seq = (entries[-1].seq + 1) if entries else 1
        prev_hash = entries[-1].record_hash if entries else _GENESIS_PREV_HASH
        safe_payload = _redact(dict(payload))
        record_hash = LedgerEntry.compute_hash(safe_payload, prev_hash)
        entry = LedgerEntry(
            seq=seq, payload=safe_payload, prev_hash=prev_hash, record_hash=record_hash
        )

        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(entry.to_line() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry
