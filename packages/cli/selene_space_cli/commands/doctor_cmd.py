"""`selenespace doctor` entry command.

Two modes: plain diagnostics (default) and a repair pass (`--repair`) that
runs the idempotent fix-ups from :mod:`selene_space_cli.doctor.repair`
before printing the standard 19-check report. The report itself is produced
by the runner; this module only decides whether repairs execute first.
"""

from __future__ import annotations

import click

from selene_space_cli import constants as _consts
from selene_space_cli.doctor import repair as repair_mod
from selene_space_cli.doctor.runner import print_report as _print_report
from selene_space_cli.doctor.runner import run_all_checks as _run_all_checks


def _echo_repairs(lines: list[str]) -> None:
    """Prefix each repair note so it reads as an action already taken."""
    for line in lines:
        click.echo(f"Repair: {line}")


def _tighten_permissions() -> None:
    """Best-effort chmod pass over the app root and stored secrets."""
    repair_mod.chmod_user_only_path(_consts.APP_DIR)
    repair_mod.chmod_secret_files()
    click.echo("Repair: Tightened permissions where possible")


def _safe_repair_pass() -> None:
    """Run repairs; any crash becomes one clean ClickException."""
    try:
        _echo_repairs(repair_mod.run_standard_repairs())
        _tighten_permissions()
    except Exception as problem:
        raise click.ClickException(f"Repair failed: {problem}") from problem


_REPAIR_FLAG = click.option(
    "--repair",
    "do_repair",
    is_flag=True,
    help=(
        "Before diagnosing, run the safe fix-ups: create missing directories, "
        "write the default config when absent, initialize SQLite, and "
        "tighten file permissions."
    ),
)


@click.command("doctor")
@_REPAIR_FLAG
def doctor(do_repair: bool) -> None:
    """Run environment diagnostics (19 checks)."""
    if do_repair:
        click.echo("Running repairs first...\n")
        _safe_repair_pass()
    _print_report(_run_all_checks())
