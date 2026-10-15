"""License compliance test (US-004).

Every source file carrying the absorption marker
`# Absorbed from US_Stock_Crash_Monitor ...` must be registered in the
attribution carrier — the private ledger `THIRD_PARTY_NOTICES.md` (repo root)
or, in the public tree, `ATTRIBUTIONS.md`. Registration drift = test failure.
"""

from __future__ import annotations

from pathlib import Path

MARKER = "Absorbed from US_Stock_Crash_Monitor"

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MACRODATA_SRC = PROJECT_ROOT / "packages" / "macrodata"
# 归属载体：私有树是根 THIRD_PARTY_NOTICES.md（含上游归属段与本地路径，公开树
# 排除）；公开树的载体是 ATTRIBUTIONS.md。两者都含 "US_Stock_Crash_Monitor (MIT)"
# 段，故判据锚在**section 内容**而非某一个文件名 —— 义务在公开树里不丢失。
NOTICE_CANDIDATES = (
    PROJECT_ROOT / "THIRD_PARTY_NOTICES.md",
    PROJECT_ROOT / "ATTRIBUTIONS.md",
)


def _notice_text() -> str | None:
    """第一个存在的归属载体的正文；都没有则 None。"""
    for path in NOTICE_CANDIDATES:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def _marked_py_files() -> list[Path]:
    hits = [
        p
        for p in MACRODATA_SRC.rglob("*.py")
        if "tests" not in p.parts
        and MARKER in p.read_text(encoding="utf-8", errors="replace")
    ]
    return sorted(hits)


def test_absorbed_files_exist() -> None:
    """Sanity: the absorbed cnn_fng.py is present with its provenance marker."""
    files = _marked_py_files()
    assert any(
        p.name == "cnn_fng.py" for p in files
    ), f"cnn_fng.py with marker not found; found: {[p.name for p in files]}"


def test_all_marked_files_registered_in_notices() -> None:
    """Every marker-bearing file must appear in the attribution carrier."""
    notices_text = _notice_text()
    assert notices_text is not None, (
        "neither THIRD_PARTY_NOTICES.md nor ATTRIBUTIONS.md found at repo root"
    )
    assert (
        "US_Stock_Crash_Monitor (MIT)" in notices_text
    ), "attribution carrier lacks the US_Stock_Crash_Monitor (MIT) section"
    files = _marked_py_files()
    assert files, "no marked files found — marker scan is broken or files were removed"
    unregistered = [p for p in files if f"{p.name}" not in notices_text]
    assert not unregistered, (
        f"files carry the absorption marker but are NOT registered in the "
        f"attribution carrier: {unregistered}"
    )


def test_marker_header_is_first_line_comment() -> None:
    """Provenance header must be the first line of the absorbed file."""
    files = _marked_py_files()
    for p in files:
        first = p.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        assert MARKER in first, f"{p.name}: marker not on line 1: {first!r}"
