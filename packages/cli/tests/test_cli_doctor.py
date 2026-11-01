"""Doctor command surface: probe registry size, runner output, repair flow."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from selene_space_cli.commands.doctor_cmd import doctor
from selene_space_cli.doctor.checks import ALL_CHECKS, CheckResult, check_python_version
from selene_space_cli.doctor.runner import print_report, run_all_checks


def test_registry_holds_exactly_nineteen_probes() -> None:
    assert len(ALL_CHECKS) == 19


def test_python_probe_passes_on_supported_interpreter() -> None:
    outcome = check_python_version()
    assert isinstance(outcome, CheckResult)
    assert outcome.status == "pass"


def test_runner_executes_every_registered_probe() -> None:
    outcomes = run_all_checks()
    assert len(outcomes) == len(ALL_CHECKS) == 19


def test_report_carries_heading_and_tally(capsys) -> None:
    print_report(run_all_checks())
    stdout = capsys.readouterr().out
    assert "SeleneQuant Doctor" in stdout
    assert "Result:" in stdout


def test_doctor_command_smoke() -> None:
    outcome = CliRunner().invoke(doctor, [])
    assert outcome.exit_code == 0
    assert "SeleneQuant Doctor" in outcome.output


def test_repair_option_runs_fixups_before_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # ``default_db_path()`` returns storage's DEFAULT_DB_PATH, a module-level
    # constant evaluated at import time from the real home — patching
    # Path.home afterwards does not move it, so --repair would write to the
    # real ~/.selenespace/data and fail where that is not writable (CI).
    from selene_space_cli import _defaults

    monkeypatch.setattr(
        _defaults,
        "default_db_path",
        lambda: tmp_path / ".selenespace" / "data" / "selenespace.db",
    )

    from selene_space_cli.commands import doctor_cmd as doctor_module

    outcome = CliRunner().invoke(doctor_module.doctor, ["--repair"])
    # Surface the underlying failure instead of a bare "assert 1 == 0":
    # when --repair raises, Click turns it into exit 1 and the traceback is
    # otherwise invisible in CI logs.
    assert outcome.exit_code == 0, (
        f"doctor --repair exited {outcome.exit_code}\n"
        f"exception: {outcome.exception!r}\n"
        f"output:\n{outcome.output}"
    )
    assert "Repair:" in outcome.output
