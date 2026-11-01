"""Record persistence protocol + stubs. See docs/DECISION_WORKFLOW_METHODOLOGY.md §6.3."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class RecordPersistence(Protocol):
    def save(self, record: dict) -> str:
        """Persist a record and return the file path written."""
        ...


class StubPersistence:
    """Filesystem-backed persistence for staged decision records."""

    def __init__(self, base_dir: str | Path = "records/pending") -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, record: dict) -> str:
        filename = (
            f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            f"_{record['meta']['input_id']}.json"
        )
        path = self._base_dir / filename
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(path)


class InMemoryPersistence:
    """Alternate stub for tests that don't want filesystem writes."""

    def __init__(self) -> None:
        self.records: list[dict] = []

    def save(self, record: dict) -> str:
        self.records.append(record)
        return f"memory://{len(self.records)}"
