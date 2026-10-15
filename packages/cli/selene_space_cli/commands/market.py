"""Market data reads proxied through the local REST API.

The CLI never reaches exchanges directly — it consumes whatever the local
API service has cached, so every endpoint here is a plain GET against
127.0.0.1 (or the configured bind host).
"""

from __future__ import annotations

import json

import httpx
import click

from selene_space_core.config import load_config


def _api_base() -> str:
    """Resolve the local API base URL from config.

    A wildcard bind address is replaced with loopback because the client
    and server run on the same machine in the desktop deployment.
    """
    cfg = load_config()
    host = cfg.api_host
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    return "http://" + str(host) + ":" + str(cfg.api_port)


class _ApiError(click.ClickException):
    """Raised when the local API cannot answer a market query."""


def _normalize(symbol: str) -> str:
    """Accept BTC/USDT or BTC-USDT; the API expects the dash form."""
    return symbol.replace("/", "-").upper()


def _get(path: str, query: dict | None = None):
    """GET ``path`` from the local API and decode JSON, raising on errors."""
    target = f"{_api_base()}{path}"
    client = httpx.Client(timeout=10.0)
    try:
        response = client.get(target, params=query)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as problem:
        raise _ApiError(f"Request failed: {problem}") from problem
    finally:
        client.close()
    return payload


def _emit(payload) -> None:
    """Pretty-print a decoded JSON payload for interactive use."""
    click.echo(json.dumps(payload, indent=2))


@click.group("market")
def market_group() -> None:
    """Ticker and kline lookups via the local API."""


_TICKER_ARG = click.argument("symbol")

_MARKET_PATHS = {
    "ticker": "/api/v1/markets/{sym}/ticker",
    "kline": "/api/v1/markets/{sym}/kline",
}


@market_group.command("ticker")
@_TICKER_ARG
def cmd_ticker(symbol: str) -> None:
    """Most recent ticker for SYMBOL (BTC-USDT or BTC/USDT both work)."""
    path = _MARKET_PATHS["ticker"].format(sym=_normalize(symbol))
    _emit(_get(path))


_KLINE_ARG = click.argument("symbol")
_KLINE_LIMIT_OPT = click.option("--limit", default=100, show_default=True, type=int)
_KLINE_INTERVAL_OPT = click.option(
    "--interval",
    default="1h",
    show_default=True,
    help="Kline interval label.",
)


@market_group.command("kline")
@_KLINE_ARG
@_KLINE_LIMIT_OPT
@_KLINE_INTERVAL_OPT
def cmd_kline(symbol: str, interval: str, limit: int) -> None:
    """Newest cached klines for SYMBOL."""
    sym = _normalize(symbol)
    params: dict[str, object] = {"interval": interval, "limit": limit}
    path = _MARKET_PATHS["kline"].format(sym=sym)
    _emit(_get(path, params))
