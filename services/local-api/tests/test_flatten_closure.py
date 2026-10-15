"""T2: build_flatten_closure factory — flatten each funding_arb pair with leg pricing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.core.risk_assembly import build_flatten_closure


class _Ticker:
    def __init__(self, price: float | None) -> None:
        self.last_price = price


class _MarketSvc:
    def __init__(self, spot: _Ticker | None, perp: _Ticker | None) -> None:
        self._spot = spot
        self._perp = perp

    def get_ticker(self, symbol, exchange="binance", market_type="spot"):
        return self._spot if market_type == "spot" else self._perp


class _Executor:
    def __init__(self) -> None:
        self.flattened: list[dict] = []

    async def flatten(self, **kw) -> None:
        self.flattened.append(kw)


class _Runner:
    def __init__(self, slots: list[dict]) -> None:
        self._slots = slots

    def funding_arb_slots(self) -> list[dict]:
        return self._slots


def _slot(symbol="BTCUSDT", net_position=0.01, account_id="pa-1", strategy_id="funding_arb") -> dict:
    return {
        "deployment_id": f"d-{symbol}",
        "account_id": account_id,
        "symbol": symbol,
        "strategy_id": strategy_id,
        "net_position": net_position,
    }


def test_noop_when_executor_missing() -> None:
    """dual_leg_executor=None (paper/shadow config) → closure no-ops."""

    async def _run() -> None:
        fn = build_flatten_closure(
            strategy_runner=_Runner([_slot()]),
            dual_leg_executor=None,
            market_svc=_MarketSvc(_Ticker(100.0), _Ticker(101.0)),
            perp_venue="binance",
        )
        await fn()  # must not raise

    asyncio.run(_run())


def test_flatten_happy_path_binance() -> None:
    async def _run() -> None:
        ex = _Executor()
        fn = build_flatten_closure(
            strategy_runner=_Runner([_slot("BTCUSDT", 0.01)]),
            dual_leg_executor=ex,
            market_svc=_MarketSvc(_Ticker(100.0), _Ticker(101.0)),
            perp_venue="binance",
        )
        await fn()
        assert len(ex.flattened) == 1
        f = ex.flattened[0]
        assert f["spot_symbol"] == "BTCUSDT"
        assert f["perp_symbol"] == "BTCUSDT"  # binance → symbol as-is
        assert f["spot_qty"] == 0.01 and f["perp_qty"] == 0.01
        assert f["spot_price"] == 100.0 and f["perp_price"] == 101.0
        assert f["account_id"] == "pa-1"
        assert f["strategy_id"] == "funding_arb"

    asyncio.run(_run())


def test_flatten_perp_symbol_okx_suffix() -> None:
    """okx venue derives perp_symbol with the -SWAP suffix."""

    async def _run() -> None:
        ex = _Executor()
        fn = build_flatten_closure(
            strategy_runner=_Runner([_slot("BTCUSDT", 0.01)]),
            dual_leg_executor=ex,
            market_svc=_MarketSvc(_Ticker(100.0), _Ticker(101.0)),
            perp_venue="okx",
        )
        await fn()
        assert ex.flattened[0]["perp_symbol"] == "BTCUSDT-SWAP"

    asyncio.run(_run())


def test_zero_net_position_skipped() -> None:
    """Pairs with |net_position| ~ 0 are not flattened."""

    async def _run() -> None:
        ex = _Executor()
        fn = build_flatten_closure(
            strategy_runner=_Runner([_slot("BTCUSDT", 0.0), _slot("ETHUSDT", 0.01)]),
            dual_leg_executor=ex,
            market_svc=_MarketSvc(_Ticker(100.0), _Ticker(101.0)),
            perp_venue="binance",
        )
        await fn()
        assert len(ex.flattened) == 1
        assert ex.flattened[0]["spot_symbol"] == "ETHUSDT"

    asyncio.run(_run())


def test_price_missing_skips_pair() -> None:
    """A pair with any missing leg price is skipped (never price=0 phantom order)."""

    async def _run() -> None:
        ex = _Executor()
        fn = build_flatten_closure(
            strategy_runner=_Runner([_slot("BTCUSDT", 0.01)]),
            dual_leg_executor=ex,
            market_svc=_MarketSvc(_Ticker(100.0), None),  # perp ticker missing
            perp_venue="binance",
        )
        await fn()
        assert ex.flattened == []

    asyncio.run(_run())


def test_funding_fetcher_mark_price_fallback() -> None:
    """When the perp ticker is missing, funding_fetcher mark_price fills the gap."""

    async def _run() -> None:
        ex = _Executor()

        class _Fetcher:
            async def get_all(self):
                return [SimpleNamespace(symbol="BTCUSDT", mark_price=99.5)]

        fn = build_flatten_closure(
            strategy_runner=_Runner([_slot("BTCUSDT", 0.01)]),
            dual_leg_executor=ex,
            market_svc=_MarketSvc(_Ticker(100.0), None),
            perp_venue="binance",
            funding_fetcher=_Fetcher(),
        )
        await fn()
        assert len(ex.flattened) == 1
        assert ex.flattened[0]["perp_price"] == 99.5

    asyncio.run(_run())


def test_one_pair_failure_does_not_abort_others() -> None:
    """Per-pair isolation: a failing leg never stops the remaining pairs."""

    async def _run() -> None:
        class _FlakyExecutor:
            def __init__(self) -> None:
                self.flattened: list[dict] = []

            async def flatten(self, **kw) -> None:
                if kw["spot_symbol"] == "BADUSDT":
                    raise RuntimeError("leg fail")
                self.flattened.append(kw)

        ex = _FlakyExecutor()
        fn = build_flatten_closure(
            strategy_runner=_Runner([_slot("BADUSDT", 0.01), _slot("OKUSDT", 0.02)]),
            dual_leg_executor=ex,
            market_svc=_MarketSvc(_Ticker(100.0), _Ticker(101.0)),
            perp_venue="binance",
        )
        await fn()
        assert len(ex.flattened) == 1
        assert ex.flattened[0]["spot_symbol"] == "OKUSDT"

    asyncio.run(_run())
