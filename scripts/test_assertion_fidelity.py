"""Test assertion fidelity verifier for cleanroom test rewrites.

Compares tests/*.py between a base ref and the working tree at the AST level
and flags any file whose assertion surface shrank. Used as the mechanical gate
for the "assertion semantics unchanged" iron rule during test rewrites.

Usage:
    python scripts/test_assertion_fidelity.py <pkg> [pkg ...]   # packages/<name>/tests
    python scripts/test_assertion_fidelity.py --all
    python scripts/test_assertion_fidelity.py --base <git-ref> <pkg> ...

Base ref defaults to HEAD. During an in-flight rewrite HEAD still holds the
pre-rewrite content, so the default is correct. After the rewrite has been
committed, pass --base <rewrite-commit>^ to compare against the true
pre-rewrite baseline; running against a post-commit HEAD compares the tree
with itself and always reports "all faithful" (a vacuous pass).

KNOWN LIMITATION (static counting): the verdict counts literal assert /
raises statements at the AST level. Equivalent rewrites that merge several
independent asserts into a loop over an expectation table, a tuple-compare,
or @pytest.mark.parametrize rows reduce the static count while keeping (or
raising) runtime comparison count. Such files show RED-FLAG here even though
semantics are preserved. Static counts are an auxiliary signal only; semantic
equivalence is established by the pytest behaviour locks plus manual spot
checks of the flagged files.

Per-file verdict rule:
    PASS      test-function count not reduced AND (assert + raises) total
              >= 90% of base (10% slack for equivalent merging/splitting)
    UP        PASS and assertion surface grew (strengthening)
    NEW       file absent at base ref (all-new tests)
    RED FLAG  counts shrank beyond tolerance -> needs human review

Exit code: 0 = all faithful, 1 = red flags present, 2 = usage error.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ASSERT_SLACK = 0.9


@dataclass
class FileStats:
    test_funcs: int = 0
    param_combos: int = 0
    asserts: int = 0
    raises: int = 0
    patches: int = 0
    parse_ok: bool = True


def _attr_chain_names(node: ast.AST) -> list[str]:
    """Collect dotted-name segments of an Attribute/Name chain."""
    names: list[str] = []
    while isinstance(node, ast.Attribute):
        names.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        names.append(node.id)
    return names


def _static_len(node: ast.AST) -> int | None:
    """Static length of a list/tuple literal expression, following + concat."""
    if isinstance(node, (ast.List, ast.Tuple)):
        return len(node.elts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_len(node.left)
        right = _static_len(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _module_const_lens(tree: ast.Module) -> dict[str, int]:
    lens: dict[str, int] = {}
    for stmt in tree.body:
        target: ast.AST | None = None
        value: ast.AST | None = None
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        if isinstance(target, ast.Name) and value is not None:
            n = _static_len(value)
            if n is not None:
                lens[target.id] = n
    return lens


def _parametrize_combo(decorator: ast.AST, consts: dict[str, int]) -> int:
    """Rows contributed by one @pytest.mark.parametrize decorator."""
    if not isinstance(decorator, ast.Call):
        return 1
    names = _attr_chain_names(decorator.func)
    if not names or names[-1] != "parametrize":
        return 1
    if len(decorator.args) >= 2:
        n = _static_len(decorator.args[1])
        if n is not None:
            return n
        if isinstance(decorator.args[1], ast.Name):
            return consts.get(decorator.args[1].id, 1)
    return 1


def analyze_source(source: str) -> FileStats:
    stats = FileStats()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return FileStats(parse_ok=False)

    consts = _module_const_lens(tree)

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                stats.test_funcs += 1
                combos = 1
                for dec in node.decorator_list:
                    combos *= _parametrize_combo(dec, consts)
                stats.param_combos += combos
        elif isinstance(node, ast.Assert):
            stats.asserts += 1
        elif isinstance(node, ast.Call):
            names = _attr_chain_names(node.func)
            if names and names[-1] == "raises":
                stats.raises += 1
            if names and names[-1] == "patch":
                stats.patches += 1
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return stats


def head_content(relpath: str, base: str = "HEAD") -> str | None:
    r = subprocess.run(
        ["git", "show", f"{base}:{relpath}"], cwd=REPO, capture_output=True
    )
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", errors="replace")


def fmt(before: int, after: int) -> str:
    arrow = "->" if before != after else "=="
    return f"{before}{arrow}{after}"


def verdict_for(before: FileStats | None, after: FileStats) -> str:
    if after.parse_ok is False or (before is not None and before.parse_ok is False):
        return "PARSE-ERR"
    if before is None:
        return "NEW"
    surface_b = before.asserts + before.raises
    surface_a = after.asserts + after.raises
    tests_ok = after.test_funcs >= before.test_funcs
    surface_ok = surface_a >= surface_b * ASSERT_SLACK
    if tests_ok and surface_ok:
        return "UP" if surface_a > surface_b or after.test_funcs > before.test_funcs else "OK"
    return "RED-FLAG"


def check_package(pkg: str, base: str = "HEAD") -> bool:
    tests_dir = REPO / "packages" / pkg / "tests"
    if not tests_dir.is_dir():
        print(f"[{pkg}] no tests dir, skipped")
        return True

    rows: list[tuple[str, FileStats | None, FileStats, str]] = []
    for py in sorted(tests_dir.rglob("*.py")):
        rel = py.relative_to(REPO).as_posix()
        after_raw = py.read_text(encoding="utf-8", errors="replace")
        after = analyze_source(after_raw)
        head_raw = head_content(rel, base)
        before = analyze_source(head_raw) if head_raw is not None else None
        rows.append((py.name, before, after, verdict_for(before, after)))

    print(f"\n===== {pkg} ({len(rows)} test files, {base} vs worktree) =====")
    print(f"{'file':<42} {'tests':>9} {'params':>13} {'assert':>11} "
          f"{'raises':>11} {'patch':>11}  verdict")
    red_flags: list[str] = []
    for name, before, after, verdict in rows:
        b = before or FileStats()
        print(f"{name:<42} {fmt(b.test_funcs, after.test_funcs):>9} "
              f"{fmt(b.param_combos, after.param_combos):>13} "
              f"{fmt(b.asserts, after.asserts):>11} "
              f"{fmt(b.raises, after.raises):>11} "
              f"{fmt(b.patches, after.patches):>11}  {verdict}")
        if verdict in ("RED-FLAG", "PARSE-ERR"):
            red_flags.append(f"{name}: {verdict}")

    tot_b = FileStats()
    tot_a = FileStats()
    for _, before, after, _ in rows:
        b = before or FileStats()
        tot_b.test_funcs += b.test_funcs
        tot_b.asserts += b.asserts
        tot_b.raises += b.raises
        tot_a.test_funcs += after.test_funcs
        tot_a.asserts += after.asserts
        tot_a.raises += after.raises
    surf_b, surf_a = tot_b.asserts + tot_b.raises, tot_a.asserts + tot_a.raises
    print(f"{'TOTAL':<42} {tot_b.test_funcs}->{tot_a.test_funcs:>5} "
          f"{'':>13} {surf_b}->{surf_a} (surface)")
    if red_flags:
        print(f"[{pkg}] RED FLAGS ({len(red_flags)}):")
        for rf in red_flags:
            print(f"  - {rf}")
    else:
        print(f"[{pkg}] all faithful")
    return not red_flags


def discover_packages() -> list[str]:
    pkgs = sorted(
        d.name for d in (REPO / "packages").iterdir()
        if (d / "tests").is_dir() and d.is_dir()
    )
    return pkgs


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("packages", nargs="*", help="package names under packages/")
    parser.add_argument("--all", action="store_true", help="check every package with tests/")
    parser.add_argument(
        "--base",
        default="HEAD",
        metavar="GIT-REF",
        help="base ref to compare against (default HEAD). Use <rewrite-commit>^ "
        "when the rewrite is already committed; HEAD-after-commit compares the "
        "tree with itself and always passes vacuously.",
    )
    args = parser.parse_args(argv)

    pkgs = discover_packages() if args.all else args.packages
    if not pkgs:
        parser.print_usage()
        return 2

    ok = True
    for pkg in pkgs:
        ok = check_package(pkg, args.base) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
