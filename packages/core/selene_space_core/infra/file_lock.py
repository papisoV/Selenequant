"""PID-tagged lock file guarding single-process access to a resource."""

from __future__ import annotations

import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import ctypes


class FileLock:
    """Cooperative lock file. Reentrant for the same PID; stale (dead-PID)
    locks are broken automatically."""

    def __init__(self, path: str | os.PathLike) -> None:
        self._path = Path(path)
        self._held = False

    @property
    def is_held(self) -> bool:
        return self._held

    def acquire(self) -> None:
        if self._held:
            return
        if self._path.exists():
            if not self._is_stale():
                raise RuntimeError(f"lock held by another live process: {self._path}")
            self._break_stale()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(str(os.getpid()), encoding="utf-8")
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        try:
            self._path.unlink()
        except OSError:
            pass
        self._held = False

    def _is_stale(self) -> bool:
        try:
            raw = self._path.read_text(encoding="utf-8").strip()
            owner_pid = int(raw)
        except (OSError, ValueError):
            return True  # unreadable/corrupt lock: treat as abandoned
        if owner_pid == os.getpid():
            return True  # our own leftover from an earlier acquire in this process
        if not _pid_alive(owner_pid):
            return True
        return False

    def _break_stale(self) -> None:
        try:
            self._path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _pid_alive(pid: int) -> bool:
    """Cross-platform liveness probe for an arbitrary PID."""
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
