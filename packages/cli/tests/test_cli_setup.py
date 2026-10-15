"""Setup wizard helpers: config template, secret layout, guard rails."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from selene_space_cli.commands.setup import _default_config_yaml, _write_binance_key


def test_config_template_parses_with_expected_port() -> None:
    parsed = yaml.safe_load(_default_config_yaml())
    assert parsed["api_port"] == 8088


def test_binance_key_lands_in_secrets_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_home = tmp_path / "h"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    _write_binance_key("secret-key")

    stored = fake_home / ".selenespace" / "secrets" / "binance" / "api_key"
    assert stored.read_text() == "secret-key"


def test_setup_refuses_unsupported_python(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from selene_space_cli.commands import setup as setup_module

    monkeypatch.setattr(setup_module.sys, "version_info", (3, 10, 0))
    outcome = CliRunner().invoke(setup_module.setup, [])
    assert outcome.exit_code != 0


def test_setup_defaults_complete_without_answers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """All-default wizard pass: five Enters, DB init stubbed via module attr."""
    from selene_space_cli.commands import setup as setup_module

    monkeypatch.setattr(setup_module.sys, "version_info", sys.version_info)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    db_file = tmp_path / ".selenespace" / "data" / "selenespace.db"
    monkeypatch.setattr(setup_module, "DEFAULT_DB_PATH", db_file)

    # Five default-Enter replies: Continue / mainnet (no) / testnet (no) /
    # LLM (skip) / backtest (no)
    outcome = CliRunner().invoke(setup_module.setup, input="\n\n\n\n\n")

    assert outcome.exit_code == 0
    assert (tmp_path / ".selenespace" / "config.yaml").is_file()
    assert db_file.is_file()
