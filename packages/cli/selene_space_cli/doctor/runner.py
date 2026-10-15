"""Render doctor results as a console report."""

from __future__ import annotations

from typing import Callable

from selene_space_cli import ansi
from selene_space_cli.constants import cli_version
from selene_space_cli.doctor.checks import ALL_CHECKS, CheckResult

_MARKERS: dict[str, tuple[str, Callable[[str], str]]] = {
    "pass": ("✓", ansi.ok),
    "fail": ("✗", ansi.err),
    "warn": ("!", ansi.warn),
    "skip": ("─", ansi.dim),
}
_LABEL_PAD = 28
_RULE_MIN = 38


def _marker(status_text: str) -> str:
    symbol, paint = _MARKERS.get(status_text, ("·", ansi.dim))
    return paint(symbol)


def run_all_checks() -> list[CheckResult]:
    return [check() for check in ALL_CHECKS]


def _counts(results: list[CheckResult]) -> dict[str, int]:
    totals = {"pass": 0, "fail": 0, "warn": 0, "skip": 0}
    for r in results:
        totals[r.status] = totals.get(r.status, 0) + 1
    return totals


def _heading() -> list[str]:
    title = f"SeleneQuant Doctor v{cli_version()}"
    rule = "═" * max(_RULE_MIN, len(title))
    return [title, rule]


def _result_line(r: CheckResult) -> str:
    padded_name = f"{r.name:<{_LABEL_PAD}}"
    return f"{_marker(r.status)} {padded_name} {r.message}{ansi.RESET}"


def _summary(c: dict[str, int]) -> str:
    return (
        f"Result: {c['pass']} passed, {c['fail']} failed, "
        f"{c['warn']} warning(s), {c['skip']} skipped"
    )


def print_report(results: list[CheckResult]) -> None:
    lines = _heading()
    for r in results:
        lines.append(_result_line(r))
    lines.append("═" * max(_RULE_MIN, len(lines[0])))
    lines.append(_summary(_counts(results)))
    print("\n".join(lines))
