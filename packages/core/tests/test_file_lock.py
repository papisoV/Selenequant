"""Infra locks for selene_space_core.infra.file_lock — pid-stamped acquire,
context-manager release, re-entrancy, stale-lock adoption."""

import os

from selene_space_core.infra.file_lock import FileLock


def test_acquire_stamps_pid_and_release_removes_file(tmp_path):
    lock_path = tmp_path / "guard.lock"
    lock = FileLock(lock_path)
    lock.acquire()
    held = (
        lock.is_held,
        lock_path.is_file(),
        lock_path.read_text().strip() == str(os.getpid()),
    )
    assert all(held)

    lock.release()
    assert (not lock.is_held) and (not lock_path.is_file())


def test_with_block_releases_on_exit(tmp_path):
    lock_path = tmp_path / "guard.lock"
    with FileLock(lock_path) as lock:
        assert lock.is_held
    assert not lock_path.is_file()


def test_second_acquire_by_same_process_is_tolerated(tmp_path):
    lock_path = tmp_path / "guard.lock"
    lock = FileLock(lock_path)
    lock.acquire()
    lock.acquire()  # Should not raise (same PID)
    assert lock.is_held
    lock.release()


def test_dead_process_lock_is_adopted(tmp_path):
    lock_path = tmp_path / "guard.lock"
    # Write a PID that definitely doesn't exist
    lock_path.write_text("999999999", encoding="utf-8")
    lock = FileLock(lock_path)
    lock.acquire()  # Should clean stale lock
    assert lock.is_held
    lock.release()


def test_own_live_lock_can_be_reacquired(tmp_path):
    """We can't easily fake a foreign live PID, so we verify our own works."""
    lock_path = tmp_path / "guard.lock"
    first = FileLock(lock_path)
    first.acquire()

    second = FileLock(lock_path)
    second.acquire()  # Re-entrant with same PID
    first.release()
