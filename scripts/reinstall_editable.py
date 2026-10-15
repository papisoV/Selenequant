"""Reinstall all editable packages in this replica so .pth files point HERE.

Run this once after copying the project to a new directory. It finds every
packages/*/pyproject.toml + services/*/pyproject.toml, runs
`pip install -e . --force-reinstall --no-deps` on each, and verifies that
the resulting .pth files point to THIS replica (not some other copy).

Usage:
    python scripts/reinstall_editable.py            # install + verify
    python scripts/reinstall_editable.py --verify   # verify only, no install
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def find_editable_packages(root: Path) -> list[Path]:
    """Return dirs containing a pyproject.toml with [project] name."""
    found: list[Path] = []
    for sub in [*root.glob("packages/*/pyproject.toml"),
                *root.glob("services/*/pyproject.toml")]:
        try:
            data = tomllib.loads(sub.read_text(encoding="utf-8"))
        except Exception:
            continue
        if "project" in data and "name" in data["project"]:
            found.append(sub.parent)
    return sorted(set(found))


def pip_install_editable(pkg_dir: Path) -> tuple[int, str]:
    """Run `pip install -e . --force-reinstall --no-deps --no-build-isolation`.

    --no-build-isolation uses the already-installed hatchling instead of trying
    to fetch it from a (possibly 403-blocked) mirror.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".",
         "--force-reinstall", "--no-deps", "--no-build-isolation", "--quiet"],
        cwd=pkg_dir,
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def verify_pth_files(root: Path) -> list[str]:
    """Check every _editable_impl_*.pth + __editable___*_finder.py points to
    THIS root. Return list of problems."""
    # site.getsitepackages() returns wrong dir on some Windows installs;
    # use the real site-packages path under sys.prefix/Lib/site-packages.
    site_dir = Path(sys.prefix) / "Lib" / "site-packages"
    if not site_dir.exists():
        return [f"site-packages not found at {site_dir}"]
    root_str_alt = str(root)
    problems: list[str] = []

    for pth in site_dir.glob("_editable_impl_selene_space_*.pth"):
        txt = pth.read_text(encoding="utf-8").strip()
        if not txt:
            continue
        # pth may contain multiple lines (force-reinstall appends). All must
        # point to THIS root.
        for ln in txt.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            if root_str_alt.lower() not in ln.lower():
                problems.append(f"{pth.name} -> {ln}  (expected under {root_str_alt})")

    for finder in site_dir.glob("__editable___selene_space_*_finder.py"):
        txt = finder.read_text(encoding="utf-8")
        # Look for any <local-path-redacted> path that isn't THIS root
        import re
        for m in re.finditer(r"'(D:[^']+)'", txt):
            path_str = m.group(1).replace("\\\\", "\\")
            if path_str.lower().startswith("<local-path-redacted>") and \
               not path_str.lower().startswith(root_str_alt.lower()):
                problems.append(f"{finder.name} -> {m.group(1)}")

    return problems


def dedup_pth_files() -> int:
    """Remove duplicate lines from _editable_impl_*.pth files.

    `pip install --force-reinstall` appends to .pth instead of overwriting,
    so repeated runs leave duplicate path lines. Python loads all of them,
    inflating sys.path with duplicates. This collapses to unique lines.
    Returns count of files modified.
    """
    site_dir = Path(sys.prefix) / "Lib" / "site-packages"
    if not site_dir.exists():
        return 0
    modified = 0
    for pth in site_dir.glob("_editable_impl_*.pth"):
        txt = pth.read_text(encoding="utf-8")
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        seen: list[str] = []
        for ln in lines:
            if ln not in seen:
                seen.append(ln)
        if len(seen) < len(lines):
            new_txt = "\n".join(seen) + "\n" if seen else ""
            pth.write_text(new_txt, encoding="utf-8", newline="")
            modified += 1
            print(f"[dedup] {pth.name}: {len(lines)} -> {len(seen)} lines")
    return modified


def main() -> int:
    if "--verify" in sys.argv:
        only_verify = True
    else:
        only_verify = False

    print(f"Replica root: {ROOT}")
    packages = find_editable_packages(ROOT)
    print(f"Found {len(packages)} editable packages:\n")
    for p in packages:
        print(f"  {p.relative_to(ROOT)}")
    print()

    if not only_verify:
        failed = []
        for i, pkg in enumerate(packages, 1):
            print(f"[{i}/{len(packages)}] pip install -e {pkg.name} ...", end=" ", flush=True)
            code, out = pip_install_editable(pkg)
            if code == 0:
                print("OK")
            else:
                print(f"FAIL ({code})")
                failed.append((pkg, out))
        if failed:
            print("\n--- Failures ---")
            for pkg, out in failed:
                print(f"\n{pkg}:")
                print(out[:2000])
            return 1

        print("\n--- Deduplicating .pth files ---")
        n = dedup_pth_files()
        if n:
            print(f"Deduplicated {n} .pth file(s).")

    print("\n--- Verifying .pth files ---")
    problems = verify_pth_files(ROOT)
    if not problems:
        print("All .pth files point to THIS replica. ")
        return 0
    print(f"Found {len(problems)} stale .pth entries:")
    for p in problems:
        print(f"  {p}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
