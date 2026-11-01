"""Infra locks for selene_space_core.infra.atomic_write — text/bytes writes,
parent creation, replacement, tmp cleanup, and original preservation on error."""

import os
import threading
import time

import pytest

from selene_space_core.infra import atomic_write as _atomic_write
from selene_space_core.infra.atomic_write import atomic_replace, atomic_write


def test_plain_text_lands_at_target(tmp_path):
    target = tmp_path / "note.txt"
    atomic_write(target, "greetings")
    assert target.read_text() == "greetings"


def test_binary_payload_roundtrips(tmp_path):
    target = tmp_path / "blob.bin"
    payload = b"\x07\x08\x09"
    atomic_write(target, payload)
    assert target.read_bytes() == payload


def test_missing_parent_directories_are_created(tmp_path):
    deep = tmp_path / "l1" / "l2" / "leaf.txt"
    atomic_write(deep, "buried")
    assert deep.read_text() == "buried"


def test_second_write_replaces_first(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("stale")
    atomic_write(target, "fresh")
    assert target.read_text() == "fresh"


def test_no_temp_files_remain_after_success(tmp_path):
    target = tmp_path / "note.txt"
    atomic_write(target, "payload")
    leftovers = list(tmp_path.glob(".*tmp"))
    assert leftovers == []


def test_failed_write_leaves_original_untouched(tmp_path):
    """If writing fails, the original file should remain intact."""
    target = tmp_path / "keep.txt"
    target.write_text("precious")

    # Simulate a write failure by passing a bad type
    try:
        atomic_write(target, 98765)  # type: ignore[arg-type]
    except (TypeError, AttributeError):
        pass

    assert target.read_text() == "precious"


def test_atomic_replace_does_not_touch_the_target_while_writing(tmp_path):
    """The whole point: the old file is whole for the entire write.

    A writer that opens the target directly would truncate it momentarily; one
    that only sees the temp path cannot reach the target at all.
    """
    target = tmp_path / "note.txt"
    target.write_text("previous")
    seen: list[str] = []

    def _writer(tmp):
        # Whatever the writer is doing, the live file still reads as before.
        seen.append(target.read_text())
        tmp.write_bytes(b"next")

    atomic_replace(target, _writer)

    assert seen == ["previous"]
    assert target.read_text() == "next"


def test_atomic_replace_cleans_tmp_when_the_writer_raises(tmp_path):
    target = tmp_path / "note.txt"
    target.write_text("previous")

    def _writer(tmp):
        tmp.write_bytes(b"half")
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError, match="disk full"):
        atomic_replace(target, _writer)

    assert target.read_text() == "previous"
    assert list(tmp_path.glob(".*tmp")) == []


@pytest.mark.skipif(os.name != "nt", reason="POSIX replaces open files happily")
def test_atomic_replace_retries_past_a_concurrent_reader(tmp_path):
    """Windows raises PermissionError while a reader holds the target open.

    The retry is what keeps an otherwise-correct write from failing whenever a
    scan happens to be mid-flight. A reader below holds the file for a while
    and then lets go, which is what a real scan does; if the retry were absent
    the replace would raise on the very first attempt.
    """
    target = tmp_path / "note.txt"
    target.write_text("previous")
    released = threading.Event()

    def _read_then_release():
        with open(target, "rb"):
            time.sleep(0.3)
        released.set()

    reader = threading.Thread(target=_read_then_release)
    reader.start()
    try:
        atomic_replace(
            target,
            lambda tmp: tmp.write_bytes(b"next"),
            attempts=40,
            delay_s=0.05,
        )
    finally:
        reader.join()

    assert released.is_set()
    assert target.read_text() == "next"


def test_zero_attempts_still_tries_once_and_raises(tmp_path):
    """Guards the loop's edge: no attempt budget must still fail loudly.

    ``attempts`` is caller-supplied, so ``0`` has to mean "try once, then
    raise" rather than "return as if it worked" — the latter would report a
    successful write that never happened.
    """
    target = tmp_path / "note.txt"
    real_replace = os.replace

    def _refuse(src, dst):
        raise PermissionError(13, "held open")

    os.replace = _refuse
    try:
        with pytest.raises(PermissionError):
            atomic_replace(
                target, lambda tmp: tmp.write_bytes(b"x"), attempts=0, delay_s=0
            )
    finally:
        os.replace = real_replace

    assert not target.exists()


def test_default_retry_budget_covers_the_slowest_reader():
    """The defaults, not just the mechanism, are load-bearing.
    The budget has to outlast whatever holds the target open. The largest
    atomic target in this repo is a 44 MB JSON whose parse alone can approach a
    second, so a sub-second budget would gamble on that path. Pinned to the
    upstream numbers this was absorbed from (10 x 0.5 s).
    """
    assert _atomic_write._REPLACE_ATTEMPTS == 10
    assert _atomic_write._REPLACE_DELAY_S == 0.5
    assert _atomic_write._REPLACE_ATTEMPTS * _atomic_write._REPLACE_DELAY_S >= 5.0
