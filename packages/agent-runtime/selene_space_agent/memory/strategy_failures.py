"""Durable memory of strategy-validation failures, recallable by keyword.

Every failed ``strategy_validate`` call is distilled into a short entry
(strategy type, symbols, issue codes, field paths) so the agent can be
reminded of past mistakes before repeating them. Full configs are never
stored — only a 400-char digest.

Storage follows TradingMemory's convention: one JSON file under
``~/.selenespace/memory/``. Recall follows ResearchFindingsMemory's
keyword-substring scoring.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 50
_MAX_RECALL = 3
_MAX_MESSAGE = 200
_MAX_DIGEST = 400

_DEFAULT_MEMORY_DIR = Path.home() / ".selenespace" / "memory"
_MEMORY_FILE = "strategy_failures.json"


class StrategyFailureMemory:
    """Append-only failure log with keyword recall for prompt injection."""

    def __init__(self, memory_dir: Path | None = None) -> None:
        self._dir = Path(memory_dir) if memory_dir is not None else _DEFAULT_MEMORY_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / _MEMORY_FILE
        self._entries: list[dict[str, Any]] = self._load(self._file)

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get("entries", [])
            return [e for e in entries if isinstance(e, dict)]
        except (OSError, json.JSONDecodeError):
            logger.warning("strategy failures memory unreadable: %s", path)
            return []

    def _persist(self) -> None:
        try:
            self._file.write_text(
                json.dumps({"entries": self._entries}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.warning(
                "strategy failures memory unwritable: %s", self._file, exc_info=True
            )

    def record(self, config: dict[str, Any], issues: list[dict[str, Any]]) -> None:
        """Distill one failed validation into an entry (FIFO capped at 50)."""
        try:
            digest = json.dumps(config, sort_keys=True, default=str)[:_MAX_DIGEST]
        except (TypeError, ValueError):
            digest = ""
        self._entries.append(
            {
                "date": date.today().isoformat(),
                "strategy_type": config.get("strategy_type", "?"),
                "symbols": config.get("symbols", []),
                "interval": config.get("interval", "?"),
                "error_codes": [i.get("code", "?") for i in issues],
                "field_paths": [i.get("field_path", "?") for i in issues],
                "messages": [str(i.get("message", ""))[:_MAX_MESSAGE] for i in issues],
                "recovery": [i.get("recovery", "?") for i in issues],
                "config_digest": digest,
            }
        )
        self._entries = self._entries[-_MAX_ENTRIES:]
        self._persist()

    def recall(self, query: str) -> str:
        """Return up to 3 past failures whose keywords appear in *query*.

        Empty string when nothing matches — the prompt section is omitted.
        """
        if not query or not self._entries:
            return ""
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in self._entries:
            keywords = {str(entry.get("strategy_type", "?"))}
            keywords.update(str(s) for s in entry.get("symbols", []))
            keywords.update(str(c) for c in entry.get("error_codes", []) if c != "?")
            keywords.update(str(f) for f in entry.get("field_paths", []) if f != "?")
            hits = sum(1 for kw in keywords if kw and kw != "?" and kw in query)
            if hits > 0:
                scored.append((hits, entry))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        lines: list[str] = []
        for _, entry in scored[:_MAX_RECALL]:
            codes = ",".join(entry.get("error_codes", [])) or "?"
            fields = ",".join(entry.get("field_paths", [])) or "?"
            messages = "; ".join(m for m in entry.get("messages", []) if m)
            lines.append(
                f"- [{entry.get('date', '?')} {entry.get('strategy_type', '?')} "
                f"{'/'.join(entry.get('symbols', []))} {entry.get('interval', '?')}] "
                f"codes: {codes} @ {fields} → {messages}"
            )
        return "\n".join(lines)
