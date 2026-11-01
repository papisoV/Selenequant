#!/usr/bin/env python3
"""Display-name guard: SeleneQuant must not regress to the old name SeleneSpace.

Background — the brand rename commit `30b4fd00` (SeleneSpace -> SeleneQuant,
display names only) landed on main + brand branches only. Parallel branches
were cut BEFORE it, so they still carry the old display name, and every merge
back into main re-introduces it (conflicts on NOTICE / ATTRIBUTIONS).

This script targets ONLY standalone display names. It never touches code
identifiers:
    NOT matched:  SeleneSpaceError / SeleneSpaceConfig / SeleneSpacePluginAPI
                  / selene_space_types / selenespaced
    matched:      "SeleneSpace Community Edition is built and maintained by ..."

Allow-listing is by INTENT, not by "whatever currently fails". Two legitimate
ways the old name survives on purpose:

  1. RENAME RECORD — prose that documents the rename itself, e.g.
     "2026-10-07:品牌换证 SeleneSpace→SeleneQuant". The old name is the
     SUBJECT of the sentence, an arrow names both ends. Matched by RENAME_RE.
  2. BRAND GUARD — a script/agent whose whole job is to assert the old name is
     absent from rendered output, so it must contain the literal. Scoped by
     ALLOW_PATH_RE to the guard files only.

Everything else — `description = "SeleneSpace Community: ..."`, a module
docstring, a NOTICE attribution line — is a NAME IN USE and fails the gate.

Usage:
    --staged   pre-commit mode: scan only ADDED lines in the staged diff
    --check    whole-tree audit (read-only)
    --fix      rewrite display names -> SeleneQuant in place
               (.env* is skipped here on purpose; edit those by hand)

Exit code: 0 = clean, 1 = old display name present.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Windows GBK 环境下强制 stdout/stderr 用 utf-8
for _stream in (sys.stdout, sys.stderr):
    if _stream.encoding and _stream.encoding.lower() != "utf-8":
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

ROOT = Path(__file__).resolve().parent.parent
OLD = "SeleneSpace"
NEW = "SeleneQuant"

# Standalone display name: neither neighbour is an identifier character.
# This is why SeleneSpaceError / selene_space_types can never match.
DISPLAY_RE = re.compile(rf"(?<![A-Za-z0-9_]){OLD}(?![A-Za-z0-9_])")

# (1) Rename record, prose only. The old name has to sit next to a rename cue
# that leads to the new name (arrow / "to" / "formerly" / 改 / 原名 ...), either
# order. Optional wrapping parens are allowed so "SeleneQuant (formerly
# SeleneSpace)" reads as a rename. This lets "品牌换证 SeleneSpace→SeleneQuant"
# through while rejecting 'description = "SeleneSpace Community: ..."'
# (no cue, name in use).
_RENAME_CUE = r"(?:->|<-|-->|<--|→|←|⇒|to|formerly|原名|旧名|曾用名|改|换成|变更为)"
_RENAME_GLUE = rf"\s*[（(]?\s*{_RENAME_CUE}\s*[）)]?\s*"
RENAME_RE = re.compile(
    rf"(?<![A-Za-z0-9_]){OLD}(?![A-Za-z0-9_]){_RENAME_GLUE}(?<![A-Za-z0-9_]){NEW}(?![A-Za-z0-9_])"
    rf"|(?<![A-Za-z0-9_]){NEW}(?![A-Za-z0-9_]){_RENAME_GLUE}(?<![A-Za-z0-9_]){OLD}(?![A-Za-z0-9_])"
)

# (2) Files whose PURPOSE is to guard the brand, so they must name the OLD
# string as a literal to assert its absence. Keep this list tiny and justified;
# do NOT grow it to silence a failing file. Three qualify today:
#   - verify-p1-identity.mjs  reads document.body.innerText and asserts the old
#     name never appears there.
#   - check_brand_display.py  (this file) defines OLD = "SeleneSpace" — a guard
#     that cannot name the string it forbids cannot detect it.
#   - test_check_brand_display.py  parametrizes the guard's tests on that same
#     literal in both directions (regressions flagged, rename records allowed).
# verify-brandbar.mjs does NOT name the old string, so it is deliberately absent.
ALLOW_PATH_RE = re.compile(
    r"(?:^|/)(?:apps/desktop/scripts/verify-p1-identity\.mjs"
    r"|scripts/check_brand_display\.py"
    r"|tests/test_check_brand_display\.py)$"
)

# Skip build artefacts / vendored trees outright.
SKIP_PARTS = ("node_modules/", ".next/", "dist/", "build/", ".git/")
SCAN_EXT = {
    ".md",
    ".txt",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".bat",
    ".sh",
    ".ps1",
    ".css",
    ".svg",
    ".html",
    ".cfg",
    ".ini",
    ".rs",
    ".go",
    ".env",
    ".example",
}


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True
    ).stdout
    paths = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    return [ROOT / p for p in paths]


def path_allowed(rel: str) -> bool:
    """True if the whole file is an approved brand-guard script."""
    return bool(ALLOW_PATH_RE.search(rel))


def line_allowed(line: str) -> bool:
    """True if the old name on this line is a rename RECORD, not a name in use."""
    return bool(RENAME_RE.search(line))


def is_offender(line: str) -> bool:
    """Standalone old name that is neither a rename record nor exempted."""
    return bool(DISPLAY_RE.search(line)) and not line_allowed(line)


def skippable(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return any(part in rel for part in SKIP_PARTS)


def scan_staged() -> list[tuple[str, str]]:
    """Added lines only, so existing old-name content cannot block an unrelated commit."""
    diff = subprocess.run(
        ["git", "diff", "--cached", "-U0", "--diff-filter=ACMR"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    hits: list[tuple[str, str]] = []
    cur = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            body = line[1:]
            if cur and not path_allowed(cur) and is_offender(body):
                hits.append((cur, body.strip()[:140]))
    return hits


def scan_tree() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for path in tracked_files():
        if skippable(path) or path.suffix.lower() not in SCAN_EXT:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if path_allowed(rel):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if is_offender(line):
                hits.append((rel, i, line.strip()[:140]))
    return hits


def fix_tree() -> int:
    changed = 0
    for path in tracked_files():
        if skippable(path) or path.suffix.lower() not in SCAN_EXT:
            continue
        rel = path.relative_to(ROOT).as_posix()
        if path_allowed(rel) or rel.startswith(".env"):
            continue  # .env* handled by hand (safety-net protected)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Rewrite only offending occurrences, line by line, so rename records
        # and guard scripts keep their literal exactly as written.
        out_lines = []
        file_changed = False
        for line in text.splitlines(keepends=True):
            if is_offender(line):
                out_lines.append(DISPLAY_RE.sub(NEW, line))
                file_changed = True
            else:
                out_lines.append(line)
        if file_changed:
            path.write_text("".join(out_lines), encoding="utf-8")
            print(f"FIXED {rel}")
            changed += 1
    return changed


def main() -> int:
    args = sys.argv[1:]
    if "--fix" in args:
        n = fix_tree()
        print(f"\n{n} file(s) rewritten: display name {OLD} -> {NEW}")
        print(
            "(identifiers untouched; rename records + guard scripts preserved; .env* skipped)"
        )
        return 0

    hits = scan_staged() if "--staged" in args else scan_tree()
    if not hits:
        print(f"OK: no standalone display-name '{OLD}' found.")
        return 0

    mode = "staged" if "--staged" in args else "tree"
    print(f"FAIL [{mode}]: standalone display name '{OLD}' found — use '{NEW}'.")
    for h in hits:
        if len(h) == 3:
            print(f"  {h[0]}:{h[1]}: {h[2]}")
        else:
            print(f"  {h[0]}: {h[1]}")
    print(
        f"\n{len(hits)} hit(s). Identifiers (SeleneSpaceError etc.) are exempt by design."
    )
    print(
        "Rename records ('SeleneSpace→SeleneQuant') and brand-guard scripts are exempt by intent."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
