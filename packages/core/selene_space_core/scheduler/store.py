"""Run-history persistence: append-only JSONL of task outcomes.

Reads tolerate a truncated final line -- whatever parses as a JSON object
survives; everything else is skipped.
"""

import json
from datetime import datetime
from pathlib import Path


class SchedulerStore:
    """Owns one ``*.jsonl`` file mapping task names to their latest runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def log_run(
        self,
        task_name: str,
        started_at: datetime,
        finished_at: datetime,
        status: str,
        error: str | None = None,
    ) -> None:
        self._write_line(
            {
                "task_name": task_name,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "status": status,
                "error": error,
            }
        )

    def read_log(self) -> list[dict]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8", errors="replace")
        out: list[dict] = []
        for chunk in text.splitlines():
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                decoded = json.loads(chunk)
            except ValueError:
                continue
            if isinstance(decoded, dict):
                out.append(decoded)
        return out

    def _write_line(self, record: dict) -> None:
        folder = self.path.parent
        if str(folder) not in ("", "."):
            folder.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(record, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as sink:
            sink.write(serialized + "\n")
