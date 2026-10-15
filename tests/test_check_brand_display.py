"""Tests for scripts/check_brand_display.py — the display-name regression gate.

The gate's whole value is its PRECISION: it must catch a real regression
(`description = "SeleneSpace Community: ..."`) while letting through the two
legitimate ways the old name survives on purpose — rename records in prose and
the brand-guard script that asserts the old name's absence.

A test that only ran the script end-to-end would pass even if the allow-list
were empty (everything flagged) or too wide (nothing flagged). So these tests
pin the decision function directly, in both directions.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parent.parent / "scripts" / "check_brand_display.py"
_spec = importlib.util.spec_from_file_location("check_brand_display", _MOD)
bd = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(bd)


# --------------------------------------------------------------------------
# Identifiers must never match (the original design contract).
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "raise SeleneSpaceError(NOT_FOUND)",
        "class SeleneSpaceConfig: ...",
        "from selene_space_types import Foo",
        "SeleneSpacePluginAPI.register(x)",
        "import selene_space_ashare",
        "the selenespace CLI",
    ],
)
def test_identifiers_never_match(line: str) -> None:
    assert not bd.DISPLAY_RE.search(line)
    assert not bd.is_offender(line)


# --------------------------------------------------------------------------
# Real regressions: old name USED as the display name, no rename context.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'description = "SeleneSpace Community: local-first crypto quant research"',
        '{ name = "SeleneSpace" }',
        '"""SeleneSpace A-Share — Factor mining, IC/IR evaluation."""',
        "# Adapted for SeleneSpace (AGPL-3.0-only); selene-ized",
        "SeleneSpace Community Edition is built and maintained by papisoV.",
        "- License: see LICENSE for the full terms — SeleneSpace",
        "Substantial parts of SeleneSpace were derived from an upstream project",
    ],
)
def test_real_regressions_are_flagged(line: str) -> None:
    assert bd.DISPLAY_RE.search(line), "precondition: standalone old name present"
    assert bd.is_offender(line), f"should be flagged: {line!r}"


# --------------------------------------------------------------------------
# Allow-list class 1: rename RECORDS in prose (old name is the subject).
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "2026-10-07:品牌换证 SeleneSpace→SeleneQuant,身份色由「金 #e3b650」",
        "rename SeleneSpace -> SeleneQuant across the tree",
        "the SeleneQuant←SeleneSpace rebrand landed on main",
        "品牌由 SeleneSpace 改 SeleneQuant",
        "SeleneQuant (formerly SeleneSpace)",
    ],
)
def test_rename_records_are_allowed(line: str) -> None:
    assert bd.DISPLAY_RE.search(line), "precondition: old name present"
    assert not bd.is_offender(line), f"rename record should pass: {line!r}"


def test_rename_record_requires_arrow_or_verb() -> None:
    """'SeleneSpace' merely NEAR 'SeleneQuant' without a rename cue is still a fail."""
    line = "SeleneSpace ships fast. SeleneQuant is the product name."
    assert bd.is_offender(line)


# --------------------------------------------------------------------------
# Allow-list class 2: the brand-guard script, by path, whole-file.
# --------------------------------------------------------------------------


def test_guard_script_path_is_exempt() -> None:
    rel = "apps/desktop/scripts/verify-p1-identity.mjs"
    assert bd.path_allowed(rel)
    # its literal MUST survive; the file is exempt wholesale
    assert bd.path_allowed(rel)


def test_brandbar_script_is_NOT_exempt() -> None:
    """verify-brandbar.mjs does not name the old string — it must not be let in.

    Guards against the allow-list being widened as a rubber stamp.
    """
    assert not bd.path_allowed("apps/desktop/scripts/verify-brandbar.mjs")


def test_unrelated_scripts_are_not_exempt() -> None:
    for rel in (
        "apps/desktop/scripts/verify-backtest.mjs",
        "services/local-api/app/main.py",
        # a test file that merely happens to sit in tests/ is NOT exempt;
        # only the brand guard's own test is (it must name the literal).
        "tests/test_something_else.py",
    ):
        assert not bd.path_allowed(rel)


def test_brand_guard_own_source_is_exempt() -> None:
    """The guard defines OLD = "SeleneSpace" — it must be able to name it."""
    assert bd.path_allowed("scripts/check_brand_display.py")


def test_brand_guard_own_test_is_exempt() -> None:
    """The guard's test parametrizes on the literal in both directions."""
    assert bd.path_allowed("tests/test_check_brand_display.py")


# --------------------------------------------------------------------------
# --staged mode: only ADDED lines, and only offending ones.
# --------------------------------------------------------------------------


def test_scan_staged_ignores_removed_and_context_lines(monkeypatch) -> None:
    diff = (
        "diff --git a/pyproject.toml b/pyproject.toml\n"
        "+++ b/pyproject.toml\n"
        "@@ -1 +1 @@\n"
        "-" + 'description = "SeleneQuant Community"\n'
        "+" + 'description = "SeleneSpace Community"\n'
        " context SeleneSpace but not standalone-triggering\n"
    )
    import subprocess

    class _R:
        stdout = diff

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    hits = bd.scan_staged()
    assert len(hits) == 1
    assert hits[0][0] == "pyproject.toml"
    assert "SeleneSpace Community" in hits[0][1]


def test_scan_staged_skips_exempt_path(monkeypatch) -> None:
    diff = (
        "diff --git a/apps/desktop/scripts/verify-p1-identity.mjs "
        "b/apps/desktop/scripts/verify-p1-identity.mjs\n"
        "+++ b/apps/desktop/scripts/verify-p1-identity.mjs\n"
        "@@ -1 +1 @@\n"
        "+" + '  oldName: document.body.innerText.includes("SeleneSpace"),\n'
    )
    import subprocess

    class _R:
        stdout = diff

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    assert bd.scan_staged() == []
