"""Console entrypoint assembling the selenespace command tree.

Registration order matters for ``--help`` output: setup and status come
first, then the nested groups, then doctor. The version subcommand is a
leaf appended after the table is applied.
"""

from __future__ import annotations

import sys
import os

import click

from selene_space_cli.commands import (
    ashare,
    ask,
    backtest,
    doctor_cmd,
    market,
    paper,
    setup as setup_cmd,
    status,
)
from selene_space_cli.constants import cli_version


class _ExitCodes:
    INTERRUPTED = 130
    GENERIC = 1


class _CliAssembler:
    """Builds the root group and wires every command module onto it."""

    def __init__(self) -> None:
        self.root = self._make_root()
        self._wire_commands()

    @staticmethod
    def _make_root() -> click.Group:
        @click.group(invoke_without_command=True)
        @click.pass_context
        def root(ctx: click.Context) -> None:
            """SeleneQuant — local-first crypto quant research and paper trading.

            Run without arguments prints this help. Subcommands cover the
            first-run wizard, live status, market reads, backtests, the
            paper-trading ledger, and environment diagnostics.
            """
            if ctx.invoked_subcommand is None:
                click.echo(ctx.get_help())

        return root

    def _wire_commands(self) -> None:
        wired = (
            ("ask", ask.ask),
            ("setup", setup_cmd.setup),
            ("status", status.status),
            ("market", market.market_group),
            ("ashare", ashare.ashare_group),
            ("backtest", backtest.backtest_group),
            ("paper", paper.paper_group),
            ("doctor", doctor_cmd.doctor),
        )
        for alias, handler in wired:
            self.root.add_command(handler, name=alias)


_assembler = _CliAssembler()
cli = _assembler.root


@cli.command("version")
def version_cmd() -> None:
    """Print the CLI version string (distribution metadata, source fallback)."""
    click.echo(f"SeleneQuant v{cli_version()}")


_DEBUG_TRUTHY = frozenset({"1", "true", "yes"})


def _trace_if_debug(problem: Exception) -> None:
    if os.environ.get("SELENE_DEBUG", "").lower() in _DEBUG_TRUTHY:
        import traceback

        traceback.print_exc()


def _handle_interrupt(_problem: BaseException | None = None) -> int:
    click.echo("\nInterrupted.", err=True)
    return _ExitCodes.INTERRUPTED


def _handle_click_error(problem: click.ClickException) -> int:
    click.echo(problem.format_message(), err=True)
    return problem.exit_code


def _handle_unexpected(problem: Exception) -> int:
    click.echo(f"Error: {problem}", err=True)
    _trace_if_debug(problem)
    return _ExitCodes.GENERIC


_FAILURE_HANDLERS = (
    (KeyboardInterrupt, _handle_interrupt),
    (click.ClickException, _handle_click_error),
)


def _dispatch(argv: list[str] | None = None) -> int:
    """Invoke the command tree and translate failures into exit codes."""
    try:
        cli.main(standalone_mode=True, args=argv)
    except BaseException as problem:  # noqa: BLE001 — boundary maps all to exit codes
        # KeyboardInterrupt subclasses BaseException, not Exception, so the
        # table must be consulted before any Exception-only branch.
        for kind, handler in _FAILURE_HANDLERS:
            if isinstance(problem, kind):
                return handler(problem)  # type: ignore[arg-type] — row-paired dispatch table
        if isinstance(problem, Exception):
            return _handle_unexpected(problem)
        raise
    return 0


def main() -> None:
    """Console-script wrapper."""
    sys.exit(_dispatch())


if __name__ == "__main__":
    main()
