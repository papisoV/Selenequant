"""Atomic file replacement: write a sibling temp file, then os.replace over the target."""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write(path: str | os.PathLike, data: str | bytes) -> None:
    """Write *data* to *path* atomically; on any failure the original file survives."""
    target = Path(path)
    if isinstance(data, str):
        payload = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        payload = bytes(data)
    else:
        raise TypeError(f"atomic_write expects str or bytes, got {type(data).__name__}")

    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp = parent / f".{target.name}.tmp-{os.getpid()}"
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
