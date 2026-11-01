"""Durable memory of past agent conversations, recallable by symbol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from selene_space_types.agent import ChatMessage

_DEFAULT_MEMORY_DIR = Path.home() / ".selenespace" / "memory"
_MEMORY_FILE = "sessions.json"


class TradingMemory:
    """Persist conversation contexts to disk and recall them for prompts.

    Each session is stored with its messages plus a short summary. When no
    summary is provided the first user message doubles as one.
    """

    def __init__(self, memory_dir: Path | None = None) -> None:
        self._dir = Path(memory_dir) if memory_dir is not None else _DEFAULT_MEMORY_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / _MEMORY_FILE
        self._sessions: dict[str, dict[str, Any]] = self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._file.exists():
            return {}
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _flush(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps(self._sessions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_context(
        self,
        session_id: str,
        messages: list[ChatMessage],
        summary: str | None = None,
    ) -> None:
        if summary is None:
            summary = next(
                (m.content for m in messages if m.role == "user" and m.content),
                session_id,
            )
        self._sessions[session_id] = {
            "session_id": session_id,
            "summary": summary,
            "messages": [
                {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                for m in messages
            ],
        }
        self._flush()

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {"session_id": s["session_id"], "summary": s["summary"]}
            for s in self._sessions.values()
        ]

    def recall_for_prompt(self, context: dict[str, Any]) -> str:
        symbols = [str(s) for s in context.get("symbols", [])]
        if not symbols or not self._sessions:
            return ""

        matched_blocks: list[str] = []
        for record in self._sessions.values():
            haystack_parts = [record.get("summary", "") or ""]
            for msg in record.get("messages", []):
                haystack_parts.append(str(msg.get("content", "")))
            haystack = "\n".join(haystack_parts)

            hits = [sym for sym in symbols if sym in haystack]
            if hits:
                matched_blocks.append(f"[{record['session_id']}] {record['summary']}")

        return "\n".join(matched_blocks)

    def clear(self) -> None:
        self._sessions.clear()
        self._flush()
