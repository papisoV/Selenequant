"""Backtest launch and history commands.

Two entry points: ``run`` executes a compiled strategy against a Parquet
OHLCV file and prints headline metrics; ``list`` shows the most recent
runs recorded in the local SQLite store.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import click
import pandas as pd

import selene_space_backtest.engine as _bt_engine
import selene_space_storage.sqlite as _storage_sqlite
import selene_space_strategy.compiler as _st_compiler
import selene_space_strategy.models as _st_models
import selene_space_strategy.runtime as _st_runtime


@click.group("backtest")
def backtest_group() -> None:
    """Execute strategy backtests and browse stored runs."""


def _load_runtime(strategy_yaml: Path):
    strat = _st_models.load_strategy(strategy_yaml)
    return strat, _st_runtime.StrategyRuntime(_st_compiler.compile(strat))


def _execute(strategy_yaml: Path, data_path: Path):
    strat, runtime = _load_runtime(strategy_yaml)
    frame = pd.read_parquet(data_path)
    engine = _bt_engine.BacktestEngine(_bt_engine.BacktestConfig(strategy_id=strat.id))
    return engine.run(strategy=runtime, data=frame)


def _parquet_path(**kwargs) -> click.Path:
    return click.Path(exists=True, dir_okay=False, path_type=Path)


_RUN_STRATEGY_ARG = click.argument("strategy_yaml", type=_parquet_path())
_RUN_DATA_OPT = click.option(
    "--data",
    "data_path",
    type=_parquet_path(),
    required=True,
    help="Parquet file with OHLCV columns: timestamp, open, high, low, close, volume.",
)


@backtest_group.command("run")
@_RUN_STRATEGY_ARG
@_RUN_DATA_OPT
def cmd_run(strategy_yaml: Path, data_path: Path) -> None:
    """Execute STRATEGY_YAML against a Parquet OHLCV file."""
    try:
        outcome = _execute(strategy_yaml, data_path)
        report = _summary_text(outcome)
    except Exception as problem:
        raise click.ClickException(str(problem)) from problem
    click.echo(report)


_SUMMARY_LABELS = (
    ("Total return", lambda m: f"{m.total_return:.2%}"),
    ("Sharpe", lambda m: f"{m.sharpe_ratio:.4f}"),
    ("Max DD", lambda m: f"{m.max_drawdown:.2%}"),
)


def _summary_text(outcome) -> str:
    m = outcome.metrics
    lines = ["Backtest complete"]
    for label, render in _SUMMARY_LABELS:
        lines.append(f"  {label}: {render(m)}")
    lines.append(f"  Trades executed: {outcome.trades_count}")
    return "\n".join(lines)


_LIST_DB_OPT = click.option(
    "--db",
    "db_path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="SQLite path override (default: config value or ~/.selenespace/data).",
)

_HISTORY_HEAD = f"{'ID':<14} {'Strategy':<18} {'Trades':<8} Created"

_HISTORY_SQL_COLUMNS = (
    "id",
    "strategy_id",
    "start_date",
    "end_date",
    "trades_count",
    "created_at",
)


def _history_rows(rows) -> list[str]:
    out = [_HISTORY_HEAD]
    for bid, sid, _sdt, _edt, tc, cat in rows:
        cells = (f"{bid:<14}", f"{sid:<18}", f"{tc:<8}", str(cat))
        out.append(" ".join(cells))
    return out


@backtest_group.command("list")
@_LIST_DB_OPT
def cmd_list(db_path: Path | None) -> None:
    """Show the most recent rows recorded in SQLite."""
    db_file = _locate_db(db_path)
    if not db_file.is_file():
        click.echo("Database not found — nothing recorded yet.")
        return
    try:
        rows = asyncio.run(_read_history(db_file))
    except Exception as problem:
        raise click.ClickException(f"Could not read backtests: {problem}") from problem

    if not rows:
        click.echo("No stored backtest runs yet.")
        return

    for line in _history_rows(rows):
        click.echo(line)


def _locate_db(explicit: Path | None) -> Path:
    from selene_space_core.config import load_config

    candidate = explicit or Path(load_config().db_path)
    return candidate if candidate.is_absolute() else candidate.resolve()


async def _read_history(db_file: Path) -> list[tuple]:
    manager = _storage_sqlite.AsyncSQLiteManager(db_file)
    await manager.connect()
    column_list = ", ".join(_HISTORY_SQL_COLUMNS)
    raw_rows = await manager.execute(
        f"SELECT {column_list} FROM backtests ORDER BY created_at DESC LIMIT 50",
        (),
    )
    await manager.close()
    picked = tuple(_HISTORY_SQL_COLUMNS)
    return [tuple(row[column] for column in picked) for row in raw_rows]
