"""Entry-point group and process-level error mapping."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

import selene_space_cli.main as entrypoint
from selene_space_cli.main import cli


def _invoke(*argv: str):
    return CliRunner().invoke(cli, list(argv))


def _crash_with(exc: BaseException) -> None:
    raise exc


def test_help_lists_core_commands() -> None:
    rendered = _invoke("--help").output
    assert "setup" in rendered
    assert "doctor" in rendered
    assert _invoke("--help").exit_code == 0


def test_version_reports_package_banner() -> None:
    outcome = _invoke("version")
    assert outcome.exit_code == 0
    assert "SeleneQuant v" in outcome.output


def test_interrupt_exits_with_130(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entrypoint.cli, "main", lambda *a, **k: _crash_with(KeyboardInterrupt()))
    with pytest.raises(SystemExit) as caught:
        entrypoint.main()
    assert caught.value.code == 130


def test_generic_failure_exits_with_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entrypoint.cli, "main", lambda *a, **k: _crash_with(RuntimeError("x")))
    with pytest.raises(SystemExit) as caught:
        entrypoint.main()
    assert caught.value.code == 1
