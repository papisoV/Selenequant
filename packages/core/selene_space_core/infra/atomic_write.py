"""Atomic file replacement: write a sibling temp file, then replace over the target.

The point is that a reader never observes a half-written file. Opening the
target for writing truncates it first, so a process killed between the open
and the last flush leaves an empty or clipped file behind — and a caller that
only checks ``exists()`` then fails on every subsequent read, with nothing to
repair it. Writing beside the target and replacing it in one step removes that
window: the old file either survives whole or gives way to the new one whole.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# Windows refuses to replace a file a reader still has open (PermissionError /
# WinError 5), where POSIX swaps the inode and never notices. Readers here scan
# for well under a second, so a short bounded backoff crosses the window; if it
# does not, the error propagates rather than being swallowed.
#
# 10 x 0.5 s mirrors the upstream numbers this was absorbed from (TSP
# `replace_with_retry`): the budget has to clear the *slowest* reader, and the
# largest target here is a 44 MB JSON whose parse alone can approach a second.
_REPLACE_ATTEMPTS = 10
_REPLACE_DELAY_S = 0.5


def atomic_replace(
    path: str | os.PathLike,
    writer: Callable[[Path], None],
    *,
    attempts: int = _REPLACE_ATTEMPTS,
    delay_s: float = _REPLACE_DELAY_S,
) -> None:
    """Build the new file beside *path* via *writer*, then swap it in.

    *writer* receives a temp path and is expected to fill it — this keeps
    formats with no byte-level API (parquet, via pandas/pyarrow) on the same
    contract as plain text, without this module having to know about them.

    On any failure the original file survives untouched and the temp file is
    removed.
    """
    target = Path(path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp = parent / f".{target.name}.tmp-{os.getpid()}"
    try:
        writer(tmp)
        _replace_with_retry(tmp, target, attempts=attempts, delay_s=delay_s)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _replace_with_retry(src: Path, dst: Path, *, attempts: int, delay_s: float) -> None:
    """``os.replace`` that rides out a concurrent reader holding *dst* open.

    ``attempts`` is caller-supplied and floored at one, so this either returns
    having replaced *dst* or raises the last ``PermissionError`` it saw.
    """
    tries = max(attempts, 1)
    last: PermissionError
    for attempt in range(tries):
        try:
            os.replace(src, dst)
            if attempt:
                logger.debug(
                    "atomic replace succeeded after %d blocked attempt(s): %s",
                    attempt,
                    dst,
                )
            return
        except PermissionError as exc:
            last = exc
            if attempt < tries - 1:
                time.sleep(delay_s)
    raise last


def atomic_write(
    path: str | os.PathLike,
    data: str | bytes,
    *,
    attempts: int = _REPLACE_ATTEMPTS,
    delay_s: float = _REPLACE_DELAY_S,
) -> None:
    """Write *data* to *path* atomically; on any failure the original file survives."""
    if isinstance(data, str):
        payload = data.encode("utf-8")
    elif isinstance(data, (bytes, bytearray, memoryview)):
        payload = bytes(data)
    else:
        raise TypeError(f"atomic_write expects str or bytes, got {type(data).__name__}")

    def _write(tmp: Path) -> None:
        tmp.write_bytes(payload)

    atomic_replace(path, _write, attempts=attempts, delay_s=delay_s)
