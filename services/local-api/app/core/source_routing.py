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

# Favorites are stored bare (``"600519"``) — the frontend's ``toPair`` strips
# every exchange suffix so the watchlist key is market-agnostic (and
# ``/chart/bars`` 404s on a suffixed code). The A-share feeds and the KlineDB
# partitions are keyed suffixed (``"600519.SH"``). ``is_bare_ashare_code`` is
# the shape test for that storage form.
_BARE_ASHARE_RE = re.compile(r"[0-9]{6}")


def is_bare_ashare_code(symbol: str) -> bool:
    """Whether ``symbol`` is a 6-digit A-share code with no exchange suffix."""
    return _BARE_ASHARE_RE.fullmatch(symbol.strip()) is not None


def to_ashare_symbol(symbol: str) -> str:
    """Bare A-share code → canonical suffixed symbol; anything else unchanged.

    ``"600519"`` → ``"600519.SH"`` (board rule lives in
    ``selene_space_core.sina_symbols``). Suffixed input passes through — this
    is the boundary conversion for consumers that key on the suffixed form
    (source subscriptions, ``sync_incremental``, KlineDB partitions, the
    anomaly detector). Not the inverse of ``to_favorite_storage``: idempotent
    in both directions rather than a round trip.
    """
    from selene_space_core.sina_symbols import code_to_symbol

    value = symbol.strip()
    return code_to_symbol(value) if is_bare_ashare_code(value) else value


# Suffixes the frontend's ``normalizeSymbol`` strips for A-shares. ``SS`` is
# the Yahoo spelling of Shanghai (``000001.SS``); the shared
# ``is_ashare_symbol`` only knows SZ/SH/BJ, so it is carried here to match the
# storage contract exactly rather than leaving ``000001.SS`` as a dirty row.
_ASHARE_STORAGE_SUFFIXES = frozenset({"SZ", "SH", "BJ", "SS"})


def to_favorite_storage(symbol: str) -> str:
    """Any A-share form → the bare form favorites are stored in.

    ``"600519.SH"`` → ``"600519"``; crypto pairs, US tickers and bare codes
    pass through. Two forms of the same listing otherwise survive de-dupe as
    separate rows and the watchlist renders it twice.

    Matches the frontend's ``normalizeSymbol`` suffix set (``.SZ``/``.SH``/
    ``.BJ``/``.SS``) so a client sending either spelling lands on one row.
    """
    value = symbol.strip()
    code, _, suffix = value.partition(".")
    if suffix and suffix.upper() in _ASHARE_STORAGE_SUFFIXES and len(code) == 6:
        return code
    return value


def market_domain(symbol: str) -> str:
    """Market domain a symbol belongs to, US class shares included.

    Unlike ``selene_space_types.routing.detect_market_domain`` this knows the
    favorites storage form: a bare 6-digit code is an A-share.
    """
    from selene_space_types.routing import MarketDomain, detect_market_domain

    # A bare code is the frontend's storage form for A-shares. The shared
    # ``detect_market_domain`` reports CRYPTO for it (it requires the suffix),
    # but that function is consumed by order risk and the execution engine
    # registry, so the correction belongs here — the routing boundary — not
    # there. Without it every favorite reaches Binance/OKX and never the
    # astock feed.
    if is_bare_ashare_code(symbol):
        return str(MarketDomain.A_SHARE.value)

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


def continuous_market(exchange: str, market_type: str) -> bool:
    """Whether the venue trades 24/7, i.e. every bar the clock implies exists.

    Being *registered* is not the same as being *continuous*. The gap sweeper
    judges a hole merely by comparing adjacent timestamps, so on an equity feed
    every overnight, weekend and holiday break looks like a repairable defect.
    Measured on the live store: AAPL yahoo/us_stock/1h carried 391 holes /
    10,950 missing bars, AAPL 1d 313 holes / 664 bars, and 300750.SZ astock
    1d 216 holes / 521 bars. None of those bars will ever exist, so every sweep
    re-fetched them, recorded failed attempts and eventually marked them
    permanent — pure REST churn, plus a permanent-hole ledger that reads as if
    the data were broken.

    Derived from the domain rather than a venue list so a new exchange
    inherits the right answer from what it trades.
    """
    return source_domain(exchange, market_type) == "crypto"


def sources_for_symbol(
    symbol: str, available: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """The subset of ``available`` sources that serves ``symbol``'s market.

    Filtering here (rather than firing at every venue) is what stops AAPL being
    sent to Binance: an equity symbol only ever reaches the equity source.
    """
    want = market_domain(symbol)
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
