"""Paper account commands backed by JSON state under ~/.selenespace/paper/.

Each invocation hydrates the four paper managers from disk, mutates, and
persists again — nothing survives between processes except the state files
themselves.
"""

from __future__ import annotations

import click

import selene_space_paper.accounts as _p_accounts
import selene_space_paper.orders as _p_orders
import selene_space_paper.pnl as _p_pnl
import selene_space_paper.positions as _p_positions
import selene_space_paper.state as _p_state


class _PaperSession:
    """Hydrate all four managers once; index 0..3 mirrors load_state order."""

    __slots__ = ("accounts", "orders", "positions", "state")

    def __init__(self) -> None:
        self.accounts: _p_accounts.AccountManager = _p_accounts.AccountManager()
        self.orders: _p_orders.PaperOrderManager = _p_orders.PaperOrderManager()
        self.positions: _p_positions.PositionManager = (
            _p_positions.PositionManager()
        )
        self.state: _p_state.PaperState = _p_state.PaperState()
        self.state.load_state(self.accounts, self.orders, self.positions)

    def save(self) -> None:
        self.state.save_state(self.accounts, self.orders, self.positions)


def _persist(managers) -> None:
    managers[3].save_state(*managers[:3])


def _require_account(session, account_id: str) -> None:
    if session.accounts.get_account(account_id) is None:
        raise click.ClickException(f"Unknown account: {account_id}")


@click.group("paper")
def paper_group() -> None:
    """Inspect and manage local paper-trading accounts."""


_CREATE_NAME_OPT = click.option("--name", required=True, help="Paper account name.")
_CREATE_BALANCE_OPT = click.option(
    "--balance", default=10_000.0, show_default=True, type=float
)


@paper_group.command("create")
@_CREATE_NAME_OPT
@_CREATE_BALANCE_OPT
def cmd_create(name: str, balance: float) -> None:
    """Open a fresh simulated account."""
    bundle = _PaperSession()
    opened = bundle.accounts.create_account(name=name, balance=balance)
    bundle.save()
    click.echo(
        f"Created paper account {opened.id}"
        + f" ({opened.name}) balance={opened.initial_balance}"
    )


_ACCOUNT_COLUMNS = (("ID", 14, "id"), ("Name", 20, "name"))


def _account_rows(held) -> list[str]:
    head = (
        " ".join(f"{title:<{w}}" for title, w, _ in _ACCOUNT_COLUMNS)
        + f" {'Balance':>12} Status"
    )
    rows = [head]
    for acct in held:
        cells = [f"{getattr(acct, attr):<{w}}" for _, w, attr in _ACCOUNT_COLUMNS]
        cells.append(f"{acct.current_balance:>12.2f}")
        cells.append(str(acct.status.value))
        rows.append(" ".join(cells))
    return rows


@paper_group.command("list")
def cmd_list() -> None:
    """Enumerate every stored account."""
    held = _PaperSession().accounts.list_accounts()
    if not held:
        click.echo("No paper accounts.")
        return
    for line in _account_rows(held):
        click.echo(line)


_POSITION_TPL = "{sym}  qty={qty:.6f}  side={sd}  avg={avg:.4f}  uPnL={upnl:.2f}"


@paper_group.command("positions")
@click.argument("account_id")
def cmd_positions(account_id: str) -> None:
    """Dump open positions held by ACCOUNT_ID."""
    held = _PaperSession().positions.get_open_positions(account_id)
    if held:
        for pos in held:
            click.echo(
                _POSITION_TPL.format(
                    sym=pos.symbol,
                    qty=pos.quantity,
                    sd=pos.side.value,
                    avg=pos.avg_entry_price,
                    upnl=pos.unrealized_pnl,
                )
            )
    else:
        click.echo("No open positions.")


_PNL_PRICE_OPT = click.option(
    "--price",
    "prices",
    multiple=True,
    type=(str, float),
    nargs=2,
    help="Mark price for a symbol (repeatable): --price BTC/USDT 65000",
)

_PNL_FIELDS = (
    ("realized", lambda r: r.realized_pnl),
    ("unrealized", lambda r: r.unrealized_pnl),
    ("total", lambda r: r.total_pnl),
    ("fees", lambda r: r.fees),
)


def _render_pnl_row(rec) -> str:
    lead = f"{rec.symbol}: "
    body = " ".join(f"{name}={getter(rec):.2f}" for name, getter in _PNL_FIELDS)
    return lead + body


@paper_group.command("pnl")
@click.argument("account_id")
@_PNL_PRICE_OPT
def cmd_pnl(account_id: str, prices) -> None:
    """Per-position PnL estimate; supply marks via repeated --price."""
    session = _PaperSession()
    _require_account(session, account_id)
    positions = session.positions.get_positions(account_id)
    if positions:
        marks: dict[str, float] = {sym: float(px) for sym, px in prices}
        running_total = 0.0
        for rec in _p_pnl.calculate_account_pnl(positions, marks):
            click.echo(_render_pnl_row(rec))
            running_total += rec.total_pnl
        click.echo(f"Total PnL (approx): {running_total:.2f}")
    else:
        click.echo("No positions.")
