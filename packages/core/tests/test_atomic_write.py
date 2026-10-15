"""Infra locks for selene_space_core.infra.atomic_write — text/bytes writes,
parent creation, replacement, tmp cleanup, and original preservation on error."""

from selene_space_core.infra.atomic_write import atomic_write


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
