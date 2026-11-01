"""Census external formula artifacts against selene's vocabulary.

Walks directories of producer artifacts, resolves each one's declared formula
names against selene's tokens, and reports what it would take to make them
runnable. Two orderings are emitted on purpose:

* ``missing_by_frequency`` — names ranked by how many files need them.
* ``set_cover`` — names ranked by picking, each round, the file with the fewest
  remaining gaps.

They disagree sharply and the difference matters: a file only runs once *all* of
its gaps are filled, so frequency order can add a dozen names and unlock nothing,
while set-cover order unlocks a file after two. Ranking by frequency is the
intuitive choice and the wrong one.

Usage::

    python scripts/report_artifact_coverage.py DIR [DIR ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from selene_space_alpha.artifact_bridge import (
    NameVerdict,
    UnsupportedArtifact,
    names_from_json,
    parse_py_header,
    resolve,
)

__all__ = ["ArtifactReport", "census", "main", "set_cover_curve"]


@dataclass
class ArtifactReport:
    """One artifact file's verdict."""

    path: str
    symbol: str
    source_kind: str
    names: list[str] = field(default_factory=list)
    verdicts: list[NameVerdict] = field(default_factory=list)
    note: str = ""

    @property
    def blocked_by(self) -> list[str]:
        """Names that stop this artifact from running, in chain order."""
        return [v.name for v in self.verdicts if not v.usable]

    @property
    def runnable(self) -> bool:
        return bool(self.names) and not self.blocked_by

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "symbol": self.symbol,
            "source_kind": self.source_kind,
            "names": self.names,
            "runnable": self.runnable,
            "blocked_by": self.blocked_by,
            "verdicts": [
                {"name": v.name, "kind": v.kind, "token": v.token, "status": v.status}
                for v in self.verdicts
            ],
            "note": self.note,
        }


def _symbol_of(path: Path, payload: dict[str, object] | None) -> str:
    if payload is not None:
        for key in ("symbol", "pair", "ticker"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return path.stem


def _evaluate(path: Path, names: list[str], kind: str, symbol: str) -> ArtifactReport:
    return ArtifactReport(
        path=str(path),
        symbol=symbol,
        source_kind=kind,
        names=names,
        verdicts=resolve(names),
    )


def _load(path: Path) -> ArtifactReport:
    """Classify one file, or record why it is not a name-bearing artifact."""
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return ArtifactReport(
                str(path), path.stem, "json", note="not a JSON object"
            )
        try:
            names = names_from_json(payload)
        except UnsupportedArtifact as exc:
            return ArtifactReport(str(path), path.stem, "json", note=str(exc))
        return _evaluate(path, names, "json", _symbol_of(path, payload))

    if path.suffix == ".py":
        try:
            header = parse_py_header(path.read_text(encoding="utf-8"))
        except UnsupportedArtifact as exc:
            return ArtifactReport(
                str(path),
                path.stem,
                "python_source",
                note=f"not a generated artifact ({exc}) — handled as source, not names",
            )
        return _evaluate(path, header.names, "generated_python", path.stem)

    return ArtifactReport(str(path), path.stem, "unknown", note="unsupported suffix")


def set_cover_curve(
    missing_by_file: dict[str, set[str]],
) -> list[dict[str, object]]:
    """Greedy set cover: each round finish the file with the fewest gaps left.

    Returns one row per round: which names were added, how many names have been
    added in total, and how many files are now fully satisfied.
    """
    remaining = {f: set(g) for f, g in missing_by_file.items() if g}
    chosen: set[str] = set()
    curve: list[dict[str, object]] = []
    while remaining:
        target = min(remaining, key=lambda k: (len(remaining[k]), k))
        # Only names this round actually introduces — a file's gap set can
        # already be partly satisfied by earlier rounds.
        added = sorted(remaining[target] - chosen)
        chosen |= set(remaining[target])
        for name in list(remaining):
            if remaining[name] <= chosen:
                del remaining[name]
        curve.append(
            {
                "added": added,
                "cumulative_names": len(chosen),
                "unlocked_files": sum(
                    1 for gaps in missing_by_file.values() if not (gaps - chosen)
                ),
                "target": target,
            }
        )
    return curve


def census(directories: list[Path]) -> dict[str, object]:
    reports: list[ArtifactReport] = []
    for directory in directories:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in (".json", ".py"):
                reports.append(_load(path))

    artifacts = [r for r in reports if r.names]
    freq: Counter[str] = Counter()
    missing_by_file: dict[str, set[str]] = {}
    for report in artifacts:
        gaps = set(report.blocked_by)
        if gaps:
            missing_by_file[report.path] = gaps
            freq.update(gaps)

    window_conflicts = [
        {"path": v.path, "name": verdict.name, "detail": verdict.detail}
        for v in artifacts
        for verdict in v.verdicts
        if verdict.status == "WINDOW_MISMATCH"
    ]

    return {
        "directories": [str(d) for d in directories],
        "files_scanned": len(reports),
        "artifacts_with_names": len(artifacts),
        "runnable_today": sum(1 for r in artifacts if r.runnable),
        "non_artifact_python": sum(
            1 for r in reports if r.source_kind == "python_source"
        ),
        "unreadable": [
            {"path": r.path, "note": r.note} for r in reports if not r.names
        ],
        "per_file": [r.to_dict() for r in artifacts],
        # Name-level coverage: resolved names / total names across artifacts.
        "name_coverage": _coverage(artifacts),
        "missing_by_frequency": [
            {"name": name, "files_needing": count} for name, count in freq.most_common()
        ],
        # Set-cover ordering. Not the same answer as frequency; see module docstring.
        "set_cover": set_cover_curve(missing_by_file),
        "window_conflicts": window_conflicts,
    }


def _coverage(artifacts: list[ArtifactReport]) -> dict[str, object]:
    total = sum(len(r.names) for r in artifacts)
    resolved = sum(1 for r in artifacts for v in r.verdicts if v.usable)
    return {
        "resolved": resolved,
        "total": total,
        "ratio": round(resolved / total, 4) if total else 0.0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print a short human summary instead of the full JSON",
    )
    args = parser.parse_args(argv)

    missing_dirs = [d for d in args.directories if not d.is_dir()]
    if missing_dirs:
        for directory in missing_dirs:
            print(f"not a directory: {directory}", file=sys.stderr)
        return 2

    result = census(args.directories)
    text = json.dumps(result, indent=2)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")

    if not args.summary:
        print(text)
        return 0

    cov = result["name_coverage"]
    assert isinstance(cov, dict), "census contract: name_coverage must be a dict"
    print(f"files scanned        : {result['files_scanned']}")
    print(f"artifacts with names : {result['artifacts_with_names']}")
    print(f"runnable today       : {result['runnable_today']}")
    print(f"non-artifact python  : {result['non_artifact_python']}")
    print(f"name coverage        : {cov['resolved']}/{cov['total']} = {cov['ratio']}")
    print()
    print("set-cover (finish the cheapest file first):")
    set_cover = result["set_cover"]
    assert isinstance(set_cover, list), "census contract: set_cover must be a list"
    for row in set_cover:
        print(
            f"  +{len(row['added']):2d} name(s) -> cumulative "
            f"{row['cumulative_names']:2d}, unlocked {row['unlocked_files']:2d} "
            f"file(s)  {row['added']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
