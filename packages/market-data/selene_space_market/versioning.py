"""Data versioning -- snapshot creation, hashing, and persistent snapshot store.

Enables reproducible backtests by recording exact data content hashes
and metadata so that a dataset can be verified at any future point.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class DataSnapshot:
    """Immutable record describing a specific version of market data."""

    snapshot_id: str
    symbol: str
    interval: str
    start_ts: int
    end_ts: int
    bar_count: int
    content_hash: str
    created_at: int
    metadata: dict = field(default_factory=dict)


def compute_data_hash(data: pd.DataFrame) -> str:
    """Compute a deterministic SHA-256 hash of a DataFrame.

    Columns are sorted and the DataFrame is serialized via CSV so that
    row/column ordering does not affect the result.
    """
    sorted_df = data[sorted(data.columns)]
    buf = io.StringIO()
    sorted_df.to_csv(buf, index=False, float_format="%.18e")
    raw = buf.getvalue().encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def create_snapshot(
    symbol: str,
    interval: str,
    data: pd.DataFrame,
    metadata: dict | None = None,
) -> DataSnapshot:
    """Create a :class:`DataSnapshot` from a DataFrame.

    The snapshot captures the time range, bar count, and content hash so
    that the data can be verified later.
    """
    content_hash = compute_data_hash(data)

    # Derive start/end timestamps from the index or a 'timestamp' column
    if isinstance(data.index, pd.DatetimeIndex):
        start_ts = int(data.index.min().timestamp() * 1000)
        end_ts = int(data.index.max().timestamp() * 1000)
    elif "timestamp" in data.columns:
        start_ts = int(data["timestamp"].min())
        end_ts = int(data["timestamp"].max())
    else:
        start_ts = 0
        end_ts = 0

    now_ms = int(time.time() * 1000)
    snapshot_id = f"{symbol}_{interval}_{content_hash[:12]}_{now_ms}"

    return DataSnapshot(
        snapshot_id=snapshot_id,
        symbol=symbol,
        interval=interval,
        start_ts=start_ts,
        end_ts=end_ts,
        bar_count=len(data),
        content_hash=content_hash,
        created_at=now_ms,
        metadata=metadata or {},
    )


def verify_snapshot(data: pd.DataFrame, snapshot: DataSnapshot) -> bool:
    """Return ``True`` if *data* matches the content hash in *snapshot*."""
    return compute_data_hash(data) == snapshot.content_hash


class SnapshotStore:
    """File-system backed store for :class:`DataSnapshot` metadata.

    Each snapshot is persisted as a JSON file under *store_dir*.
    """

    def __init__(self, store_dir: Path) -> None:
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- persistence ----------------------------------------------------------

    def save(self, snapshot: DataSnapshot) -> Path:
        """Save *snapshot* metadata as a JSON file and return the path."""
        # Sanitize snapshot_id to avoid directory separators in the filename
        safe_id = snapshot.snapshot_id.replace("/", "_").replace("\\", "_")
        path = self._dir / f"{safe_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(snapshot), indent=2), encoding="utf-8")
        return path

    def load(self, snapshot_id: str) -> DataSnapshot:
        """Load a snapshot by its *snapshot_id*."""
        safe_id = snapshot_id.replace("/", "_").replace("\\", "_")
        path = self._dir / f"{safe_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return DataSnapshot(**data)

    # -- querying -------------------------------------------------------------

    def list_snapshots(self, symbol: str | None = None) -> list[DataSnapshot]:
        """List all snapshots, optionally filtered by *symbol*."""
        snapshots: list[DataSnapshot] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                snap = DataSnapshot(**raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if symbol is None or snap.symbol == symbol:
                snapshots.append(snap)
        return snapshots

    def find_matching(
        self,
        data: pd.DataFrame,
        symbol: str,
        interval: str,
    ) -> DataSnapshot | None:
        """Find a snapshot whose content hash matches *data*.

        Returns ``None`` if no matching snapshot exists.
        """
        target_hash = compute_data_hash(data)
        for snap in self.list_snapshots(symbol=symbol):
            if snap.interval == interval and snap.content_hash == target_hash:
                return snap
        return None
