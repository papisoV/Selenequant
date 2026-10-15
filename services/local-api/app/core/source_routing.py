"""Route a symbol to the market-data sources that actually serve it.

One home for the domain→source mapping because three call sites must agree on
it: the startup subscription pass (``main._subscribe_default_symbols``), the
startup kline sync (``main._kline_startup_sync``), and the runtime favorites
sync (``api.v1.favorites._sync_symbol_klines``). They drifted once — the
favorites path hardcoded the crypto venues and fired A-share codes at Binance
(400 Bad Request), so ``AstockSource``/``AstockIntradaySource`` never received
the subscription the user's favorite implied.
"""

from __future__ import annotations

import re

# Exchange name → domain for the venues whose market_type does not say it.
# AstockSource registers as ("astock", "spot") because A-share has no futures
# feed and it reuses the crypto market_type vocabulary — a market_type-only
# mapping would file it under CRYPTO. The exchange name is the unambiguous
# signal, and it is the venue's identity rather than user-supplied text.
_EQUITY_EXCHANGE_DOMAINS = {"astock": "ashare", "yahoo": "us_stock"}

# US tickers, including class shares (BRK-B, BRK.A). detect_market_domain
# falls back to CRYPTO for the suffixed forms (its alphabetic-only heuristic
# rejects the separator), so the routing layer carries this pattern to keep
# them reaching the equity feed instead of a crypto venue. Deliberately not
# crypto-pair shaped — no "/" and at most five letters.
_US_TICKER_RE = re.compile(r"[A-Z]{1,5}([.$-][A-Z])?")

# Sources that carry a whole-market cross-section rather than a per-symbol
# stream. They share their exchange with per-symbol feeds, so `source_domain`
# alone would file them as subscribable and every routing call site would try
# to subscribe/watch them one symbol at a time. Kept as a set of (exchange,
# market_type) pairs so a future whole-market source is a one-line addition.
_WHOLE_MARKET_ONLY_SOURCES: frozenset[tuple[str, str]] = frozenset(
    {("astock", "market_snapshot")}
)


def _domain_of(symbol: str) -> str:
    """Market domain a symbol belongs to, US class shares included."""
    from selene_space_types.routing import MarketDomain, detect_market_domain

    domain = detect_market_domain(symbol)
    # mypy cannot resolve the workspace package, so ``domain`` is Any and the
    # str() pins the -> str return (warn_return_any).
    if domain == MarketDomain.CRYPTO and _US_TICKER_RE.fullmatch(
        symbol.strip().upper()
    ):
        return str(MarketDomain.US_STOCK.value)
    return str(domain.value)


def source_domain(exchange: str, market_type: str) -> str:
    """Market domain a registered source serves."""
    from selene_space_types.routing import market_type_to_domain

    mapped = _EQUITY_EXCHANGE_DOMAINS.get(exchange)
    if mapped is not None:
        return mapped
    # str() is a no-op at runtime (MarketDomain is a str enum) but pins the
    # annotation. mypy cannot resolve this workspace package, so it sees
    # the whole expression as Any and trips no-any-return on a -> str function.
    return str(market_type_to_domain(market_type).value)


def serves_symbols(exchange: str, market_type: str) -> bool:
    """Whether a source can be subscribed to one symbol at a time.

    Whole-market pollers cannot. ``("astock","market_snapshot")`` fetches the
    entire A-share cross-section in one sweep and exposes it via
    ``get_market_rows()``; handing it individual symbols would both starve it
    to the favorites pool (the exact subscription-scoping the source exists to
    escape) and make the kline sync walk a partition it never writes.
    """
    return (exchange, market_type) not in _WHOLE_MARKET_ONLY_SOURCES


def sources_for_symbol(
    symbol: str, available: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """The subset of ``available`` sources that serves ``symbol``'s market.

    Filtering here (rather than firing at every venue) is what stops AAPL being
    sent to Binance: an equity symbol only ever reaches the equity source.
    """
    want = _domain_of(symbol)
    return [
        (ex, mt)
        for ex, mt in available
        if source_domain(ex, mt) == want and serves_symbols(ex, mt)
    ]


def intervals_for(exchange: str, market_type: str, intervals: list[str]) -> list[str]:
    """Intervals to sync for one source.

    A-share has no intraday kline feed — ``AstockSource`` serves 1d only and
    coerces anything else, so asking it for 30m/1h just refetches the same
    daily partition. Pin it to 1d here.
    """
    if source_domain(exchange, market_type) == "ashare":
        return ["1d"]
    return intervals
