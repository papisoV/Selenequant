"""Where an alert rule's numbers come from — the only I/O the evaluator needs.

Kept apart from ``alert_evaluator`` so the comparison logic stays pure. Two
realities this module encodes, both learned from the existing call sites:

* **Symbols must be routed, not sprayed.** ``main._subscribe_default_symbols``
  already pays for this lesson: firing an A-share code at Binance returns 400
  and teaches nothing. ``source_routing.sources_for_symbol`` is the one home for
  that decision and is reused here rather than re-derived.
* **A-share price comes from a different source than its daily bars.**
  ``AstockSource.get_ticker`` returns ``None`` by contract (it is a kline-only
  feed); the intraday poller and the whole-market snapshot are the feeds that
  actually carry a last price. So price lookup walks every source that serves
  the symbol's market and takes the first real quote, ending with the
  snapshot cross-section.

Every reader returns ``None`` rather than a placeholder when it has nothing.
That ``None`` travels all the way up to "do not notify": a rule must never fire
because its feed happened to be down.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

#: Bars the indicator path asks for. RSI needs ``window`` samples for its first
#: value and MACD needs enough for the slow EMA to settle; 120 daily bars covers
#: both with room to spare and stays a cheap partition read.
_INDICATOR_BAR_LIMIT = 120


def _symbol_candidates(symbol: str) -> list[str]:
    """Forms the same listing may be keyed under across sources.

    Favorites store A-share codes bare (``"600519"``) while the feeds key them
    suffixed (``"600519.SH"``). Both are tried so a rule written from the
    watchlist and a rule written with the exchange suffix both resolve.
    """
    from app.core.source_routing import to_ashare_symbol

    value = symbol.strip()
    suffixed = to_ashare_symbol(value)
    return [value] if suffixed == value else [value, suffixed]


def make_price_reader(market_svc: Any) -> Callable[[str], float | None]:
    """Build the sync ``price(symbol)`` callable for one evaluator round.

    Sync on purpose: every read is a cache hit on data the pollers already
    fetched. Nothing here is allowed to start a network request, or the 30s
    loop would turn into a crawler.
    """
    from app.core.source_routing import market_domain, serves_symbols, source_domain
    from selene_space_types.routing import MarketDomain

    def _read(symbol: str) -> float | None:
        if market_svc is None:
            return None
        sources = getattr(market_svc, "sources", {}) or {}
        try:
            domain = market_domain(symbol)
        except Exception:  # noqa: BLE001  routing is best-effort, never fatal
            logger.warning("alert price routing failed for %s", symbol, exc_info=True)
            return None

        for candidate in _symbol_candidates(symbol):
            for exchange, market_type in sources:
                if not serves_symbols(exchange, market_type):
                    continue
                if source_domain(exchange, market_type) != domain:
                    continue
                try:
                    ticker = market_svc.get_ticker(
                        candidate, exchange=exchange, market_type=market_type
                    )
                except Exception:  # noqa: BLE001  one bad source must not end the round
                    logger.warning(
                        "alert price read failed on %s/%s for %s",
                        exchange,
                        market_type,
                        candidate,
                        exc_info=True,
                    )
                    continue
                price = getattr(ticker, "last_price", None)
                if price:
                    return float(price)

        # A-share fallback: the whole-market snapshot is the cross-section that
        # is actually warm for every listed code, including ones nobody
        # subscribed to. Read-only — the source's poller owns the fetching.
        if domain == str(MarketDomain.A_SHARE.value):
            rows = _snapshot_rows(market_svc)
            for candidate in _symbol_candidates(symbol):
                row = rows.get(candidate)
                price = getattr(row, "last", None)
                if price:
                    return float(price)
        return None

    return _read


def _snapshot_rows(market_svc: Any) -> dict[str, Any]:
    """The A-share snapshot cross-section, or ``{}`` when that source is absent."""
    sources = getattr(market_svc, "sources", {}) or {}
    source = sources.get(("astock", "market_snapshot"))
    if source is None:
        return {}
    try:
        rows = source.get_market_rows()
    except Exception:  # noqa: BLE001  a not-yet-polled source is not an error
        logger.warning("alert snapshot read failed", exc_info=True)
        return {}
    return rows if isinstance(rows, dict) else {}


async def read_indicator(
    market_svc: Any, kline_manager: Any, symbol: str, metric: str
) -> float | None:
    """Latest value of *metric* for *symbol*, or ``None`` when there is no data.

    Bars come from the local kline store first (``kline_manager`` with
    ``source="local"``) because the alert loop must not turn into a REST
    crawler. A symbol with no stored partition yields ``None`` — the rule stays
    silent and honest instead of firing on a value computed from three bars.
    """
    if kline_manager is None or market_svc is None:
        return None

    frame = await _load_bars(market_svc, kline_manager, symbol)
    if frame is None or len(frame) < _min_bars():
        return None
    return _compute(frame, metric)


async def _load_bars(market_svc: Any, kline_manager: Any, symbol: str) -> Any | None:
    """First stored partition that actually has bars for *symbol*.

    The A-share K-line store is partitioned by (exchange, market_type) and has
    been written under more than one key over time, so the candidates come from
    what is registered rather than one hardcoded pair.
    """
    from app.core.source_routing import intervals_for, serves_symbols

    sources = getattr(market_svc, "sources", {}) or {}
    for candidate in _symbol_candidates(symbol):
        for exchange, market_type in sources:
            if not serves_symbols(exchange, market_type):
                continue
            for interval in intervals_for(exchange, market_type, ["1d"]):
                try:
                    frame = await kline_manager.get_klines(
                        exchange,
                        market_type,
                        candidate,
                        interval,
                        source="local",
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "alert indicator bar read failed on %s/%s for %s",
                        exchange,
                        market_type,
                        candidate,
                        exc_info=True,
                    )
                    continue
                if frame is not None and len(frame) >= _min_bars():
                    return frame
    return None


def _compute(frame: Any, metric: str) -> float | None:
    """Latest of RSI / MACD-signal, computed with the ashare package's own math.

    Imported lazily: ``selene_space_ashare`` pulls a heavy dependency surface,
    and the alert loop must not slow down (or fail) the app's import path when
    that extra is absent.
    """
    try:
        from selene_space_ashare.factor_calc import macd_signal, rsi
    except Exception:  # noqa: BLE001  optional extra; no data beats a crash
        logger.warning("ashare factor_calc unavailable; indicator rules idle")
        return None

    closes = frame[["close"]] if hasattr(frame, "columns") else None
    if closes is None:
        return None
    try:
        series = rsi(closes) if metric == "RSI" else macd_signal(closes)
        value = series.dropna()
    except Exception:  # noqa: BLE001
        logger.warning("indicator computation failed for %s", metric, exc_info=True)
        return None
    if len(value) == 0:
        return None
    return float(value.iloc[-1])


def _min_bars() -> int:
    from app.core.alert_evaluator import indicator_min_bars

    return indicator_min_bars()


#: How far back a ``voice`` rule looks. A news item is fetched once (``link``
#: is unique), so a match older than this can never be "new" to the user —
#: without a window, every round would re-count the same historical article and
#: a single hit would keep the rule armed forever.
NEWS_LOOKBACK_MS = 60 * 60 * 1000

#: Cap on rows a keyword query may return per rule per round. The evaluator only
#: needs "at least one" plus the newest title; a broad keyword like "BTC" would
#: otherwise pull the whole table every 30 seconds.
NEWS_MATCH_LIMIT = 20


def make_news_lookup(
    store: Any,
    *,
    lookback_ms: int = NEWS_LOOKBACK_MS,
    now_ms: Callable[[], int] | None = None,
) -> Callable[[dict[str, Any]], tuple[int, str | None] | None]:
    """Build a per-rule news matcher over an already-resolved ``NewsStore``.

    Blocking: the caller runs it off the event loop. ``None`` is returned when
    the query fails, which the evaluator reports as ``no_data`` — a store
    outage must not be mistaken for "no news matched".
    """
    clock = now_ms or (lambda: int(time.time() * 1000))

    def _lookup(rule: dict[str, Any]) -> tuple[int, str | None] | None:
        threshold = rule.get("threshold")
        keyword = (
            str(threshold.get("keyword") or "").strip()
            if isinstance(threshold, dict)
            else ""
        )
        if not keyword:
            return None
        try:
            rows = store.query_news(
                keyword=keyword,
                fetched_after_ms=clock() - lookback_ms,
                limit=NEWS_MATCH_LIMIT,
            )
        except Exception:  # noqa: BLE001  store hiccup = no data, not no news
            logger.warning("alert news query failed for %r", keyword, exc_info=True)
            return None
        if not isinstance(rows, list):
            return None
        title = rows[0].get("title") if rows else None
        return (len(rows), str(title) if title else None)

    return _lookup


def make_indicator_prefetch(
    market_svc: Any, kline_manager: Any
) -> Callable[[list[dict[str, Any]]], Any]:
    """Return an async prefetcher that fills the evaluator's indicator map.

    The evaluator is synchronous; bar reads are not. Prefetching the distinct
    (symbol, metric) pairs once per round keeps that boundary explicit and
    avoids recomputing the same RSI for two rules watching the same instrument.
    """

    async def _prefetch(rules: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
        wanted: set[tuple[str, str]] = set()
        for rule in rules:
            if rule.get("type") != "indicator":
                continue
            threshold = rule.get("threshold")
            if not isinstance(threshold, dict):
                continue
            wanted.add(
                (
                    str(rule.get("symbol") or ""),
                    str(threshold.get("indicator") or "RSI"),
                )
            )
        out: dict[tuple[str, str], float] = {}
        for symbol, metric in wanted:
            if not symbol:
                continue
            value = await read_indicator(market_svc, kline_manager, symbol, metric)
            if value is not None:
                out[(symbol, metric)] = value
        return out

    return _prefetch
