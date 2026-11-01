"""Static guard: the klines SQLite path has exactly ONE source of truth.

Background — the write/read split
---------------------------------
The backend hardcoded a bare relative ``"data/klines.db"`` while the backend's
CWD is ``services/local-api``, so it wrote to ``services/local-api/data/klines.db``;
every reader honoured ``SELENE_KLINES_DB`` pinned to ``<repo-root>/data/klines.db``.
Two DBs silently drifted apart for days. The fix routed every call site through
``selene_space_core.kline_paths.default_klines_db_path``.

Why a static guard rather than a runtime test
---------------------------------------------
The dangerous regression is not a crash — it is a *silent empty database*.
``KlineDB.open()`` auto-creates a missing file, so a reintroduced legacy default
yields an empty DB and a page that quietly renders zeros. Only a whole-repo
static scan catches that before it ships.

What is forbidden (code-level string literals only — docstrings/comments may
still *describe* paths in prose):

  1. the retired location ``services/local-api/data/klines.db``, whether it
     appears as one literal or is rebuilt by a ``root / "services" / … /
     "klines.db"`` join chain;
  2. any absolute machine path ending in ``klines.db`` (a drive letter +
     separator, e.g. a Windows ``D:`` root);
  3. a bare CWD-relative default (``"klines.db"`` / ``"data/klines.db"``) — the
     original bug's shape, whose target depends on the process CWD. This covers
     both spellings: the flat literal, and the same path split across a join
     (``Path("data") / "klines.db"``) that survives the anchored-join exemption
     because each component looks harmless on its own.

Exemptions are explicit and enumerated below; nothing is exempted silently.

Scope — the shipping surface, not every script
----------------------------------------------
Only code that *ships or runs in production* is scanned: ``packages/`` and
``services/`` (a legacy default there silently opens an empty DB and renders
zeros to users), plus the live entry-point scripts named in ``LIVE_SCRIPTS``.
One-off research scripts under ``scripts/`` run by hand for a single study and
belong to the same "not shipped" class as ``scripts/research-archive/`` — a
legacy default there is a stale-analysis nuisance, not a user-facing
regression. They were left on their historical paths deliberately: migrating
them (and the pre-existing type debt they carry) is a separate sweep.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The retired DB location. Nothing executable may point at it any more.
RETIRED_LEGACY = "services/local-api/data/klines.db"

#: Roots whose code ships or runs in production. Scanned unconditionally.
SCAN_ROOTS = ("packages/", "services/")

#: Live entry-point scripts. These live under ``scripts/`` but are the ones
#: operators (and ``CLAUDE.md``) actually invoke against the canonical DB, so a
#: stale default here misfills or misreads the real database. Everything else
#: under ``scripts/`` is a one-off study and is out of scope by design.
LIVE_SCRIPTS = frozenset(
    {
        "scripts/sync_klines.py",  # root-DB filler
        "scripts/merge_klines_db.py",  # one-off migration tool (kept for re-runs)
        "scripts/llm_param_tuner.py",  # documented CLI; reads klines to tune
        "scripts/run_pa_diagnosis.py",  # documented PA pipeline CLI (PROVENANCE.md)
    }
)

# Frozen research archive — one-off historical scripts, never run in production.
# Their legacy paths are deliberate historical record, not live defaults.
ARCHIVE_PREFIX = "scripts/research-archive/"

#: Explicit, documented exemptions. Each needs a reason it is NOT a live default.
EXEMPT_FILES = {
    # Interpolates the path into sandboxed code *strings* (mode=rwc creates);
    # never reads real kline data, so Phase 4 retirement cannot break it.
    "packages/agent-runtime/tests/test_agent_code_exec.py",
    # This guard: contains the forbidden needles as test fixtures by necessity.
    "tests/test_kline_path_single_source.py",
    # Helper's own test: asserts on the literal default shape it pins.
    "packages/core/tests/test_kline_paths.py",
    # Publish-exclusion list. The string names a *committed test fixture*
    # (a different DB, scoped to services/local-api/tests/data) that must be
    # kept out of the public tree — it is an exclusion entry, not a path the
    # code resolves at runtime.
    "scripts/prepublish_gate.py",
}

#: Exemption entries whose *own path* is dropped from the published snapshot by
#: ``scripts/prepublish_gate.py`` (PUBLIC_EXCLUDE_SET). This guard ships in the
#: public tree; those files do not, so the "must match a tracked file" check
#: must not fail on the deliberate absence. Kept explicit (never a wildcard) so
#: a genuine typo in any other entry still trips the assertion.
PUBLISH_EXCLUDED_EXEMPTIONS = frozenset(
    {
        # The publish gate itself: excluded from the public tree because it
        # names the very upstream tokens the gate exists to keep out.
        "scripts/prepublish_gate.py",
    }
)


def _tracked_py_files() -> list[str]:
    """Every Python file that would be committed — tracked OR untracked-but-not-ignored.

    Untracked files matter: a brand-new script that reintroduces a legacy
    default must fail this guard before its first commit, not after.
    """
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def _is_scanned(rel: str) -> bool:
    """Only the shipping surface is scanned — see the module docstring's Scope."""
    return rel.startswith(SCAN_ROOTS) or rel in LIVE_SCRIPTS


def _is_exempt(rel: str) -> bool:
    return rel in EXEMPT_FILES or rel.startswith(ARCHIVE_PREFIX)


def _code_string_literals(src: str) -> list[tuple[int, str, bool]]:
    """Code string constants with a flag: is this a path-join *component*?

    A docstring is a Constant whose value is the *same object* ``ast`` returns
    from ``get_docstring``; comparing by identity separates prose from code.

    A component of ``root / "data" / "klines.db"`` is anchored to an explicit
    root and is fine; a literal passed directly to a call (``open("data/k.db")``)
    depends on the process CWD and is the dangerous shape.
    """
    tree = ast.parse(src)
    docstring_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                for child in ast.walk(node):
                    if isinstance(child, ast.Constant) and child.value is doc:
                        docstring_ids.add(id(child))

    joined_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant):
                    joined_ids.add(id(side))

    found: list[tuple[int, str, bool]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
        ):
            found.append((node.lineno, node.value, id(node) in joined_ids))
    return found


def _scan() -> list[tuple[str, int, str, bool]]:
    """(rel, lineno, literal, is_path_component) for non-exempt path-shaped literals."""
    hits: list[tuple[str, int, str, bool]] = []
    for rel in _tracked_py_files():
        if not _is_scanned(rel) or _is_exempt(rel):
            continue
        path = REPO_ROOT / rel
        try:
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "klines.db" not in src:
            continue
        for lineno, value, is_component in _code_string_literals(src):
            if _is_path_shaped_klines(value):
                hits.append((rel, lineno, value, is_component))
    return hits


def _is_absolute_klines(value: str) -> bool:
    stripped = value.strip()
    if not stripped.endswith("klines.db"):
        return False
    return ":/" in stripped or ":\\" in stripped or stripped.startswith("/")


#: Values that are literally a path to a klines DB (the only shapes that can
#: become a resolved default). Prose like "klines.db query failed: " ends with
#: neither a separator-plus-name nor a trailing space, so it is not matched.
_PATH_SHAPED = ("klines.db", "data/klines.db", "data\\klines.db", RETIRED_LEGACY)


def _is_path_shaped_klines(value: str) -> bool:
    stripped = value.strip()
    if stripped != value:  # leading/trailing whitespace → message text
        return False
    if stripped in _PATH_SHAPED:
        return True
    if stripped.endswith("/klines.db") or stripped.endswith("\\klines.db"):
        return True
    return _is_absolute_klines(stripped)


# --------------------------------------------------------------------------
# Regression pins. Each must FAIL if the corresponding rule is violated.
# --------------------------------------------------------------------------


def test_no_code_literal_points_at_retired_legacy_db() -> None:
    offenders = [
        (rel, lineno, value)
        for rel, lineno, value, _ in _scan()
        if RETIRED_LEGACY in value
    ]
    assert not offenders, (
        "Code string literal(s) still point at the retired klines DB location "
        f"{RETIRED_LEGACY!r}. Route them through "
        "selene_space_core.kline_paths.default_klines_db_path() instead:\n"
        + "\n".join(f"  {rel}:{lineno}  {value!r}" for rel, lineno, value in offenders)
    )


def test_no_code_literal_is_an_absolute_machine_path() -> None:
    offenders = [
        (rel, lineno, value)
        for rel, lineno, value, _ in _scan()
        if _is_absolute_klines(value)
    ]
    assert not offenders, (
        "Code string literal(s) hardcode an absolute klines.db path (breaks on "
        "any other machine/replica). Use default_klines_db_path():\n"
        + "\n".join(f"  {rel}:{lineno}  {value!r}" for rel, lineno, value in offenders)
    )


def test_no_path_join_rebuilds_the_retired_legacy_db() -> None:
    """A ``root / "services" / "local-api" / "data" / "klines.db"`` chain lands
    on the retired location just as surely as the flat literal does."""
    offenders = _scan_chains()
    assert not offenders, (
        f"Path-join chain(s) rebuild the retired klines DB location "
        f"{RETIRED_LEGACY!r}. Use default_klines_db_path() instead:\n"
        + "\n".join(
            f"  {rel}:{lineno}  {joined!r}" for rel, lineno, joined in offenders
        )
    )


def test_no_relative_path_literal_is_used_as_a_default() -> None:
    """A relative path *with a directory* at a call site depends on the process CWD.

    This is the exact shape of the original write/read split (a bare relative
    ``data/klines.db`` resolved against the backend's CWD). Fragments of a
    ``root / "data" / "klines.db"`` join are anchored to an explicit root and
    are allowed. A bare ``"klines.db"`` with no directory is excluded too — it
    is never a resolvable default, and in this tree it is always a semantic
    label (e.g. ``q.source == "klines.db"``).
    """
    offenders = [
        (rel, lineno, value)
        for rel, lineno, value, is_component in _scan()
        if not is_component and _is_relative_dir_path(value)
    ]
    assert not offenders, (
        "Code string literal(s) pass a CWD-relative klines.db path — the exact "
        "shape of the original write/read split. Use default_klines_db_path():\n"
        + "\n".join(f"  {rel}:{lineno}  {value!r}" for rel, lineno, value in offenders)
    )


def test_no_relative_join_rebuilds_a_cwd_dependent_default() -> None:
    """``Path("data") / "klines.db"`` is the flat literal's CWD-relative twin.

    Splitting the literal across a join defeats both earlier rules: no single
    operand is path-shaped, and the join looks anchored. It is not — the base is
    a bare relative literal, so resolution still follows the process CWD, which
    is exactly how the original write/read split happened.
    """
    offenders = _scan_relative_join_defaults()
    assert not offenders, (
        "Path-join(s) build a CWD-relative klines.db default — the original "
        "write/read split's shape, just spelled as a join. Use "
        "default_klines_db_path():\n"
        + "\n".join(
            f"  {rel}:{lineno}  {joined!r}" for rel, lineno, joined in offenders
        )
    )


def _is_relative_dir_path(value: str) -> bool:
    stripped = value.strip()
    if stripped != value:  # leading/trailing whitespace → message text
        return False
    if not stripped.endswith("klines.db"):
        return False
    if ":/" in stripped or ":\\" in stripped or stripped.startswith("/"):
        return False  # absolute — covered by the absolute-path rule
    return "/" in stripped or "\\" in stripped


def _flatten_div(node: ast.AST) -> list[ast.AST]:
    """Operands of a ``a / b / c`` join, left to right."""
    parts: list[ast.AST] = []

    def rec(n: ast.AST) -> None:
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            rec(n.left)
            rec(n.right)
        else:
            parts.append(n)

    rec(node)
    return parts


def _path_join_chains(src: str) -> list[tuple[int, str]]:
    """(lineno, slash-joined constants) for every ``/``-join with ≥2 string parts.

    The single-literal rules above miss a chained build such as
    ``REPO / "services" / "local-api" / "data" / "klines.db"`` — no *one*
    literal is path-shaped, so it slipped past until the join is reconstructed.
    Non-constant operands are dropped, so a partial match cannot fabricate the
    full needle (the caller matches the complete retired path).
    """
    tree = ast.parse(src)
    chains: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            consts = [
                p.value
                for p in _flatten_div(node)
                if isinstance(p, ast.Constant) and isinstance(p.value, str)
            ]
            if len(consts) >= 2:
                chains.append((node.lineno, "/".join(consts)))
    return chains


def _scan_chains() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for rel in _tracked_py_files():
        if not _is_scanned(rel) or _is_exempt(rel):
            continue
        try:
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "klines.db" not in src:
            continue
        for lineno, joined in _path_join_chains(src):
            if RETIRED_LEGACY in joined:
                hits.append((rel, lineno, joined))
    return hits


def _relative_literal_path(node: ast.AST) -> str | None:
    """``Path("data")`` → ``"data"``; ``None`` for an anchored base.

    A Path built from a bare relative literal resolves against the process CWD,
    which is the original bug. An anchored base (``REPO_ROOT``,
    ``Path(__file__)…``, ``_repo_root()``) does not — so only the literal form
    is a defect.
    """
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Path"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        value = node.args[0].value
        if ":/" not in value and ":\\" not in value and not value.startswith("/"):
            return value
    return None


def _relative_join_defaults(src: str) -> list[tuple[int, str]]:
    """(lineno, joined) for a relative-base join that lands on a klines DB.

    ``Path("data") / "klines.db"`` is the CWD-relative twin of the flat
    ``"data/klines.db"`` literal: same resolution, but split so no single
    operand looks path-shaped and the anchored-join rule waves it through.
    """
    tree = ast.parse(src)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        parts = _flatten_div(node)
        base = _relative_literal_path(parts[0])
        if base is None:
            continue
        consts = [
            p.value
            for p in parts[1:]
            if isinstance(p, ast.Constant) and isinstance(p.value, str)
        ]
        joined = "/".join([base, *consts])
        if joined.endswith("klines.db"):
            found.append((node.lineno, joined))
    return found


def _scan_relative_join_defaults() -> list[tuple[str, int, str]]:
    hits: list[tuple[str, int, str]] = []
    for rel in _tracked_py_files():
        if not _is_scanned(rel) or _is_exempt(rel):
            continue
        try:
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "klines.db" not in src:
            continue
        for lineno, joined in _relative_join_defaults(src):
            hits.append((rel, lineno, joined))
    return hits


# --------------------------------------------------------------------------
# The single source itself must keep resolving to the repo-root DB.
# --------------------------------------------------------------------------


def test_helper_resolves_to_repo_root_and_honours_env(monkeypatch) -> None:
    from selene_space_core.kline_paths import default_klines_db_path

    monkeypatch.delenv("SELENE_KLINES_DB", raising=False)
    assert default_klines_db_path() == REPO_ROOT / "data" / "klines.db"

    monkeypatch.setenv("SELENE_KLINES_DB", str(REPO_ROOT / "elsewhere" / "k.db"))
    assert default_klines_db_path() == (REPO_ROOT / "elsewhere" / "k.db")


def test_exemptions_are_real_paths() -> None:
    """A typo'd exemption would silently widen the allowlist."""
    tracked = set(_tracked_py_files())
    for rel in EXEMPT_FILES:
        if rel in PUBLISH_EXCLUDED_EXEMPTIONS:
            # This entry's own file is dropped from the published snapshot, so
            # in the public tree it is legitimately absent from both the index
            # and disk — skip the tracked check there. In the private tree
            # (where the file still exists) keep guarding against a rename.
            path = REPO_ROOT / rel
            if path.is_file():
                assert rel in tracked, (
                    f"publish-excluded exemption {rel!r} exists but is untracked"
                )
            continue
        assert rel in tracked, f"exemption {rel!r} does not match a tracked file"


def test_live_scripts_are_real_paths() -> None:
    """A typo'd LIVE_SCRIPTS entry would silently drop a live script from scope."""
    tracked = set(_tracked_py_files())
    for rel in LIVE_SCRIPTS:
        assert (
            rel in tracked
        ), f"LIVE_SCRIPTS entry {rel!r} does not match a tracked file"


def test_scope_covers_the_write_and_read_paths() -> None:
    """The scan must include the writer (services) and the reader packages.

    Guards against a well-meaning edit that narrows ``SCAN_ROOTS`` back to a
    single subtree — which would reopen the write/read split silently.
    """
    assert any(r.startswith("services/") for r in SCAN_ROOTS), SCAN_ROOTS
    assert any(r.startswith("packages/") for r in SCAN_ROOTS), SCAN_ROOTS
    assert _is_scanned("services/local-api/app/main.py")
    assert _is_scanned("packages/market-data/selene_space_market/kline_db.py")
