"""Test real-time connectivity to all exchange data sources.

Uses SeleneQuant's own exchange-sdk modules — not raw websockets/httpx.
This validates that our SDK layer works end-to-end with live data.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import Any, cast

# stdout is a TextIOWrapper at runtime; mypy types it as TextIO | Any.
cast(Any, sys.stdout).reconfigure(encoding="utf-8")

_RULE = "=" * 65
_WS_TIMEOUT = 10.0
_POLL_INTERVAL = 0.5
_POLL_ATTEMPTS = 20


async def _await_first(collect: list[Any]) -> Any | None:
    """Poll until the stream callback delivers at least one item (or give up)."""
    for _ in range(_POLL_ATTEMPTS):
        if collect:
            return collect[0]
        await asyncio.sleep(_POLL_INTERVAL)
    return None


async def _probe_stream(
    label: str,
    make_client: Callable[[Callable[[Any], None]], Any],
    subscribe: Callable[[Any], Awaitable[None]],
    render: Callable[[Any], list[str]],
) -> bool:
    """Connect a WS client, subscribe, wait for one message, print a summary.

    Shared by every streaming exchange so the connect/subscribe/wait/close
    lifecycle exists once. ``make_client`` receives the append-callback.
    """
    collected: list[Any] = []
    client = make_client(collected.append)
    try:
        await asyncio.wait_for(client.connect(), timeout=_WS_TIMEOUT)
        await subscribe(client)
        item = await _await_first(collected)
        if item is None:
            print(f"  [FAIL] {label}: No data in {_WS_TIMEOUT:.0f}s")
            return False
        print(f"  [OK] {label}")
        for line in render(item):
            print(f"       {line}")
        return True
    except Exception as exc:
        print(f"  [FAIL] {label}: {exc}")
        return False
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def test_binance() -> bool:
    """Test Binance via our BinanceWSClient + normalizer."""
    from selene_space_exchange.exchanges.binance.ws_client import BinanceWSClient

    return await _probe_stream(
        "Binance BTC/USDT via SDK",
        lambda cb: BinanceWSClient(on_ticker=cb),
        lambda c: c.subscribe_ticker(["btcusdt"]),
        lambda t: [
            f"last={t.last_price} bid={t.bid} ask={t.ask}",
            f"vol24h={t.volume_24h} change={t.change_24h_pct}%",
        ],
    )


async def test_binance_kline() -> bool:
    """Test Binance kline stream via SDK."""
    from selene_space_exchange.exchanges.binance.ws_client import BinanceWSClient

    return await _probe_stream(
        "Binance BTC/USDT kline via SDK",
        lambda cb: BinanceWSClient(on_kline=cb),
        lambda c: c.subscribe_kline(["btcusdt"], "1m"),
        lambda k: [
            f"o={k.open} h={k.high} l={k.low} c={k.close}",
            f"interval={k.interval} closed={k.closed}",
        ],
    )


async def test_okx() -> bool:
    """Test OKX via our OKXWSClient + normalizer."""
    from selene_space_exchange.exchanges.okx.ws_client import OKXWSClient

    return await _probe_stream(
        "OKX BTC-USDT via SDK",
        lambda cb: OKXWSClient(on_ticker=cb),
        lambda c: c.subscribe_ticker(["BTC-USDT"]),
        lambda t: [
            f"last={t.last_price} bid={t.bid} ask={t.ask}",
            f"vol24h={t.volume_24h} change={t.change_24h_pct}%",
        ],
    )


async def test_binance_l2() -> bool:
    """Test Binance L2 orderbook depth REST snapshot."""
    import httpx

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                "https://data-api.binance.vision/api/v3/depth",
                params={"symbol": "BTCUSDT", "limit": 5},
            )
            resp.raise_for_status()
            depth = resp.json()
            print("  [OK] Binance L2 depth (REST)")
            print(f"       Top bids: {depth['bids'][:3]}")
            print(f"       Top asks: {depth['asks'][:3]}")
            return True
    except Exception as exc:
        print(f"  [FAIL] Binance L2: {exc}")
        return False


def _is_live(market: Any) -> bool:
    """A market counts as live when it has tokens priced strictly inside (0,1)."""
    return bool(market.tokens) and any(0.01 < t.price < 0.99 for t in market.tokens)


async def test_polymarket() -> bool:
    """Test Polymarket via our PolymarketClient."""
    from selene_space_exchange.exchanges.polymarket.client import PolymarketClient

    client = PolymarketClient()
    try:
        markets = await client.list_markets(limit=50)
        if not markets:
            print("  [FAIL] Polymarket: No markets returned")
            return False

        active = [m for m in markets if _is_live(m)]
        print(
            f"  [OK] Total markets: {len(markets)}, active (live prices): {len(active)}"
        )

        for i, m in enumerate((active or markets)[:2]):
            print(f"       {i + 1}. {m.question[:65]}")
            for token in m.tokens[:2]:
                print(f"          {token.outcome}: price={token.price}")

        target = (active or markets)[0]
        if not target.tokens:
            return True
        token_id = target.tokens[0].token_id

        try:
            book = await client.get_orderbook(token_id)
            print(f"\n  [OK] Orderbook for token {token_id[:20]}...")
            print(f"       {len(book.bids)} bids, {len(book.asks)} asks")
            if book.bids:
                print(f"       Best bid: {book.bids[0]}")
            if book.asks:
                print(f"       Best ask: {book.asks[0]}")
            print(f"       Last trade: {book.last_trade_price}")
        except Exception as exc:
            print(f"\n  [WARN] Orderbook failed: {exc}")

        try:
            print(f"\n  [OK] Midpoint price: {await client.get_midpoint(token_id)}")
        except Exception:
            pass

        return True
    except Exception as exc:
        print(f"  [FAIL] Polymarket: {exc}")
        return False
    finally:
        await client.close()


_PROBES: tuple[tuple[str, str, Callable[[], Awaitable[bool]]], ...] = (
    ("--- 1. Binance (data-stream.binance.vision) ---", "Binance Ticker", test_binance),
    ("--- 2. Binance Kline ---", "Binance Kline", test_binance_kline),
    ("--- 3. Binance L2 Depth ---", "Binance L2", test_binance_l2),
    ("--- 4. OKX (ws.okx.com) ---", "OKX Ticker", test_okx),
    ("--- 5. Polymarket (clob.polymarket.com) ---", "Polymarket", test_polymarket),
)


async def main() -> None:
    print(_RULE)
    print("SeleneQuant Exchange SDK — Live Connectivity Test")
    print(_RULE)

    results: dict[str, bool] = {}
    for heading, name, probe in _PROBES:
        print(f"\n{heading}")
        results[name] = await probe()

    print("\n" + _RULE)
    print("RESULTS SUMMARY:")
    for name, ok in results.items():
        print(f"  {'[v]' if ok else '[x]'} {name}: {'PASS' if ok else 'FAIL'}")
    print(f"\n  {sum(results.values())}/{len(results)} data sources verified")
    print(_RULE)


if __name__ == "__main__":
    asyncio.run(main())
