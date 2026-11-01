"""Deribit options fetcher + GEX (Gamma Exposure) engine.

Fetches BTC/ETH options chain from Deribit public API and computes
gamma exposure per strike, dealer positioning, and key levels.

Deribit public API (no auth required):
  - GET /api/v2/public/get_instruments?currency=BTC&kind=option
  - GET /api/v2/public/get_order_book?instrument_name=BTC-28MAR25-80000-C
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from selene_space_core.resilience import OutboundGateway
from selene_space_types.derivatives import (
    GEXLevel,
    GEXResult,
    OptionsInstrument,
    OptionsStrikeData,
    OptionsSummary,
    OptionsTicker,
)

logger = logging.getLogger(__name__)

DERIBIT_BASE_URL = "https://www.deribit.com/api/v2/public"
_CACHE_TTL = 300  # 5 minutes cache for options data


@dataclass
class OptionsChainData:
    """Full options chain snapshot for one currency (ADR-1 poller payload)."""

    instruments: list[OptionsInstrument]
    tickers: list[OptionsTicker]
    summaries: list[OptionsSummary]


class DeribitOptionsFetcher:
    """Fetch options chain from Deribit and compute GEX."""

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._cache: dict[str, tuple[float, GEXResult]] = {}
        self._chain_cache: dict[str, tuple[float, OptionsChainData]] = {}
        self._client: OutboundGateway | None = None

    async def _get_client(self) -> OutboundGateway:
        if self._client is None or self._client.is_closed:
            self._client = OutboundGateway(
                proxy=self._proxy_url,
                default_timeout=httpx.Timeout(30.0, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                default_circuit=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def fetch_chain(self, currency: str = "BTC") -> OptionsChainData | None:
        """Fetch a full options chain snapshot for one currency (ADR-1 poller).

        Reuses the same REST client and 5-min TTL cache as ``fetch_gex``. Deribit
        field mapping: ``expiration_timestamp`` (ms) passes through as-is to
        ``OptionsInstrument``; order-book ``mark_iv`` maps to
        ``OptionsTicker.implied_volatility`` (both are decimals).
        """
        cache_key = f"chain:{currency}"
        now = time.time()
        cached = self._chain_cache.get(cache_key)
        if cached is not None and now - cached[0] < _CACHE_TTL:
            return cached[1]

        try:
            client = await self._get_client()

            # 1. Underlying price (perpetual ticker)
            ticker_url = f"{DERIBIT_BASE_URL}/ticker?instrument_name={currency}-PERPETUAL"
            ticker_data = (await client.get(ticker_url)).json().get("result") or {}
            underlying_price = float(ticker_data.get("last_price", 0) or 0)
            if underlying_price <= 0:
                logger.warning("Failed to get %s underlying price from Deribit", currency)
                return None

            # 2. Instruments, filtered to the next 30 days
            inst_url = f"{DERIBIT_BASE_URL}/get_instruments?currency={currency}&kind=option"
            instruments_raw = (await client.get(inst_url)).json().get("result", [])
            if not instruments_raw:
                logger.warning("No options instruments for %s", currency)
                return None

            now_ms = int(now * 1000)
            cutoff_ms = now_ms + 30 * 24 * 3600 * 1000
            valid = [i for i in instruments_raw if int(i.get("expiration_timestamp", 0)) <= cutoff_ms]

            # 3. Concurrent order books (same Semaphore pattern as fetch_gex)
            sem = asyncio.Semaphore(20)

            async def _fetch_ob(inst_name: str) -> dict[str, Any]:
                async with sem:
                    try:
                        ob_url = f"{DERIBIT_BASE_URL}/get_order_book?instrument_name={inst_name}"
                        return (await client.get(ob_url)).json().get("result", {})
                    except Exception:
                        return {}

            ob_results = await asyncio.gather(
                *[_fetch_ob(inst.get("instrument_name", "")) for inst in valid]
            )

            # 4. Build instruments + tickers (field mapping per ADR-1)
            instruments: list[OptionsInstrument] = []
            tickers: list[OptionsTicker] = []
            for inst, ob_data in zip(valid, ob_results):
                inst_name = inst.get("instrument_name", "")
                expiry_ts = int(inst.get("expiration_timestamp", 0))
                strike = float(inst.get("strike", 0))
                option_type = inst.get("option_type", "")
                if not inst_name or expiry_ts <= 0 or strike <= 0 or option_type not in ("call", "put"):
                    continue

                instruments.append(OptionsInstrument(
                    instrument_name=inst_name,
                    base_currency=currency,
                    expiration_timestamp=expiry_ts,
                    strike=strike,
                    option_type=option_type,
                    is_active=bool(inst.get("is_active", True)),
                    tick_size=float(inst.get("tick_size", 0.0005)),
                    contract_size=float(inst.get("contract_size", 1.0)),
                ))

                greeks = ob_data.get("greeks", {})
                stats = ob_data.get("stats", {})
                mark_price = float(ob_data.get("mark_price", 0) or 0)
                tickers.append(OptionsTicker(
                    instrument_name=inst_name,
                    mark_price=mark_price,
                    mark_price_usd=mark_price,
                    underlying_price=float(ob_data.get("underlying_price", underlying_price) or underlying_price),
                    implied_volatility=float(ob_data.get("mark_iv", 0) or 0),
                    delta=float(greeks.get("delta", 0) or 0),
                    gamma=float(greeks.get("gamma", 0) or 0),
                    theta=float(greeks.get("theta", 0) or 0),
                    vega=float(greeks.get("vega", 0) or 0),
                    rho=float(greeks.get("rho", 0) or 0),
                    open_interest=float(ob_data.get("open_interest", 0) or 0),
                    volume_24h=float(stats.get("volume", 0) or 0),
                    bid_price=float(ob_data.get("best_bid_price", 0) or 0),
                    ask_price=float(ob_data.get("best_ask_price", 0) or 0),
                    last_price=float(ob_data.get("last_price", 0) or 0),
                    timestamp=now_ms,
                ))

            # 5. Per-expiry summaries
            summaries = self._build_summaries(currency, instruments, tickers, now_ms)

            chain = OptionsChainData(instruments=instruments, tickers=tickers, summaries=summaries)
            self._chain_cache[cache_key] = (now, chain)
            return chain

        except Exception:
            logger.exception("Failed to fetch options chain for %s", currency)
            return None

    @staticmethod
    def _build_summaries(
        currency: str,
        instruments: list[OptionsInstrument],
        tickers: list[OptionsTicker],
        now_ms: int,
    ) -> list[OptionsSummary]:
        """Aggregate tickers per expiry into one OptionsSummary row."""
        ticker_by_name = {t.instrument_name: t for t in tickers}
        by_expiry: dict[int, dict[str, list[float] | float]] = {}

        for inst in instruments:
            exp = by_expiry.setdefault(
                inst.expiration_timestamp,
                {
                    "call_oi": 0.0, "put_oi": 0.0,
                    "call_vol": 0.0, "put_vol": 0.0,
                    "call_ivs": [], "put_ivs": [],
                },
            )
            t = ticker_by_name.get(inst.instrument_name)
            if t is None:
                continue
            if inst.option_type == "call":
                exp["call_oi"] += t.open_interest
                exp["call_vol"] += t.volume_24h
                if t.implied_volatility > 0:
                    exp["call_ivs"].append(t.implied_volatility)
            else:
                exp["put_oi"] += t.open_interest
                exp["put_vol"] += t.volume_24h
                if t.implied_volatility > 0:
                    exp["put_ivs"].append(t.implied_volatility)

        summaries: list[OptionsSummary] = []
        for expiry_ts, exp in by_expiry.items():
            call_ivs = exp["call_ivs"]
            put_ivs = exp["put_ivs"]
            total_call_oi = float(exp["call_oi"])
            total_put_oi = float(exp["put_oi"])
            summaries.append(OptionsSummary(
                base_currency=currency,
                expiration_timestamp=expiry_ts,
                days_to_expiry=round(max((expiry_ts - now_ms) / (24 * 3600 * 1000), 0), 1),
                total_call_oi=total_call_oi,
                total_put_oi=total_put_oi,
                put_call_ratio=round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 0.0,
                max_pain=0.0,
                total_call_volume=float(exp["call_vol"]),
                total_put_volume=float(exp["put_vol"]),
                call_iv_atm=min(call_ivs) if call_ivs else 0.0,
                put_iv_atm=min(put_ivs) if put_ivs else 0.0,
                timestamp=now_ms,
            ))
        return summaries

    async def fetch_gex(self, currency: str = "BTC") -> GEXResult | None:
        """Fetch options chain and compute GEX for a currency."""
        # Check cache
        cache_key = f"gex:{currency}"
        now = time.time()
        if cache_key in self._cache:
            cached_time, cached_result = self._cache[cache_key]
            if now - cached_time < _CACHE_TTL:
                return cached_result

        try:
            client = await self._get_client()

            # 1. Get underlying price
            ticker_url = f"{DERIBIT_BASE_URL}/ticker?instrument_name={currency}-PERPETUAL"
            ticker_resp = await client.get(ticker_url)
            ticker_data = ticker_resp.json()
            if ticker_data.get("result"):
                underlying_price = float(ticker_data["result"]["last_price"])
            else:
                underlying_price = 0.0

            if underlying_price <= 0:
                logger.warning("Failed to get %s price from Deribit", currency)
                return None

            # 2. Get option instruments
            instruments_url = f"{DERIBIT_BASE_URL}/get_instruments?currency={currency}&kind=option"
            inst_resp = await client.get(instruments_url)
            inst_data = inst_resp.json()
            instruments = inst_data.get("result", [])
            if not instruments:
                logger.warning("No options instruments found for %s", currency)
                return None

            # 3. Collect per-strike data — aggregate across expiries
            strike_data: dict[float, dict[str, float]] = {}
            total_call_oi = 0.0
            total_put_oi = 0.0
            total_call_gex = 0.0
            total_put_gex = 0.0
            all_strikes: list[OptionsStrikeData] = []

            # Filter to nearest expiries (within 30 days) for relevance
            now_ms = int(time.time() * 1000)
            cutoff_ms = now_ms + 30 * 24 * 3600 * 1000

            # Phase 1: filter valid instruments
            valid_instruments: list[dict[str, Any]] = []
            for inst in instruments:
                expiry_ts = inst.get("expiration_timestamp", 0)
                if expiry_ts > cutoff_ms:
                    continue
                strike = float(inst.get("strike", 0))
                inst_name = inst.get("instrument_name", "")
                if strike <= 0 or not inst_name:
                    continue
                valid_instruments.append(inst)

            # Phase 2: concurrent fetch order books (Semaphore limits concurrency
            # to avoid Deribit rate limiting). Previously serial N calls could
            # take 150s+ for BTC (~300 instruments), blowing the 10s timeout.
            sem = asyncio.Semaphore(20)

            async def _fetch_ob(inst_name: str) -> dict[str, Any]:
                async with sem:
                    try:
                        ob_url = f"{DERIBIT_BASE_URL}/get_order_book?instrument_name={inst_name}"
                        ob_resp = await client.get(ob_url)
                        return ob_resp.json().get("result", {})
                    except Exception:
                        return {}

            ob_results = await asyncio.gather(
                *[_fetch_ob(inst.get("instrument_name", "")) for inst in valid_instruments]
            )

            # Phase 3: aggregate per-strike
            for inst, ob_data in zip(valid_instruments, ob_results):
                expiry_ts = inst.get("expiration_timestamp", 0)
                strike = float(inst.get("strike", 0))
                option_type = inst.get("option_type", "")  # "call" or "put"
                inst_name = inst.get("instrument_name", "")

                oi = float(ob_data.get("open_interest", 0))
                volume = float(ob_data.get("stats", {}).get("volume", 0))
                iv = float(ob_data.get("mark_iv", 0))
                delta = float(ob_data.get("greeks", {}).get("delta", 0))
                gamma = float(ob_data.get("greeks", {}).get("gamma", 0))

                # GEX = gamma * OI * contract_size * underlying_price^2
                # Deribit contract size = 1 BTC or 1 ETH
                contract_size = float(inst.get("contract_size", 1))
                gex_notional = gamma * oi * contract_size * underlying_price * underlying_price / 100  # normalized

                expiry_days = max((expiry_ts - now_ms) / (24 * 3600 * 1000), 0)

                key = strike
                if key not in strike_data:
                    strike_data[key] = {
                        "strike": strike,
                        "expiry_days": expiry_days,
                        "call_oi": 0, "put_oi": 0,
                        "call_volume": 0, "put_volume": 0,
                        "call_iv": 0, "put_iv": 0,
                        "call_delta": 0, "put_delta": 0,
                        "call_gamma": 0, "put_gamma": 0,
                        "call_gex": 0, "put_gex": 0,
                    }

                sd = strike_data[key]
                if option_type == "call":
                    sd["call_oi"] += oi
                    sd["call_volume"] += volume
                    sd["call_iv"] = sd["call_iv"] or iv  # use nearest expiry
                    sd["call_delta"] = sd["call_delta"] or delta
                    sd["call_gamma"] = sd["call_gamma"] or gamma
                    sd["call_gex"] += gex_notional
                    total_call_oi += oi
                    total_call_gex += gex_notional
                else:
                    sd["put_oi"] += oi
                    sd["put_volume"] += volume
                    sd["put_iv"] = sd["put_iv"] or iv
                    sd["put_delta"] = sd["put_delta"] or delta
                    sd["put_gamma"] = sd["put_gamma"] or gamma
                    sd["put_gex"] += gex_notional
                    total_put_oi += oi
                    total_put_gex += gex_notional

            # 4. Build strike list and compute key levels
            for sd in strike_data.values():
                all_strikes.append(OptionsStrikeData(
                    strike=sd["strike"],
                    expiry_days=round(sd["expiry_days"], 1),
                    call_oi=sd["call_oi"],
                    put_oi=sd["put_oi"],
                    call_volume=sd["call_volume"],
                    put_volume=sd["put_volume"],
                    call_iv=sd["call_iv"],
                    put_iv=sd["put_iv"],
                    call_delta=sd["call_delta"],
                    put_delta=sd["put_delta"],
                    call_gamma=sd["call_gamma"],
                    put_gamma=sd["put_gamma"],
                ))

            all_strikes.sort(key=lambda s: s.strike)

            # 5. Compute key levels
            net_gex = total_call_gex + total_put_gex  # puts are negative by dealer convention
            # Put GEX is negative (dealers are short puts typically)
            adjusted_put_gex = -abs(total_put_gex)
            net_gex = total_call_gex + adjusted_put_gex

            key_levels = self._compute_key_levels(all_strikes, underlying_price, total_call_gex, adjusted_put_gex)
            max_pain = self._compute_max_pain(all_strikes)
            put_call_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else 0.0

            if net_gex > 0:
                dealer_positioning = "long_gamma"
            elif net_gex < 0:
                dealer_positioning = "short_gamma"
            else:
                dealer_positioning = "neutral"

            result = GEXResult(
                symbol=f"{currency}/USDT",
                underlying_price=underlying_price,
                total_call_gex=round(total_call_gex, 2),
                total_put_gex=round(abs(total_put_gex), 2),
                net_gex=round(net_gex, 2),
                dealer_positioning=dealer_positioning,
                key_levels=key_levels,
                put_call_ratio=round(put_call_ratio, 3),
                max_pain=round(max_pain, 2),
                strikes=all_strikes,
                timestamp=int(time.time() * 1000),
            )

            self._cache[cache_key] = (now, result)
            return result

        except Exception:
            logger.exception("Failed to fetch GEX for %s", currency)
            return None

    def _compute_key_levels(
        self,
        strikes: list[OptionsStrikeData],
        underlying_price: float,
        total_call_gex: float,
        total_put_gex: float,
    ) -> list[GEXLevel]:
        """Identify key gamma levels: call wall, put wall, gamma flip."""
        levels: list[GEXLevel] = []

        # Call wall: strike with highest call OI
        max_call_oi_strike = max(strikes, key=lambda s: s.call_oi) if strikes else None
        if max_call_oi_strike and max_call_oi_strike.call_oi > 0:
            levels.append(GEXLevel(
                price=max_call_oi_strike.strike,
                net_gex=max_call_oi_strike.call_gamma * max_call_oi_strike.call_oi,
                label="call_wall",
            ))

        # Put wall: strike with highest put OI
        max_put_oi_strike = max(strikes, key=lambda s: s.put_oi) if strikes else None
        if max_put_oi_strike and max_put_oi_strike.put_oi > 0:
            levels.append(GEXLevel(
                price=max_put_oi_strike.strike,
                net_gex=max_put_oi_strike.put_gamma * max_put_oi_strike.put_oi,
                label="put_wall",
            ))

        # Gamma flip: where net GEX per strike crosses zero
        prev_net = 0.0
        for s in strikes:
            net = s.call_gamma * s.call_oi - s.put_gamma * s.put_oi
            if prev_net != 0 and net != 0 and (prev_net > 0) != (net > 0):
                levels.append(GEXLevel(
                    price=s.strike,
                    net_gex=net,
                    label="gamma_flip",
                ))
            prev_net = net

        return levels

    @staticmethod
    def _compute_max_pain(strikes: list[OptionsStrikeData]) -> float:
        """Compute max pain: the strike where total option value expires worthless is maximized."""
        if not strikes:
            return 0.0

        strike_prices = sorted(set(s.strike for s in strikes))
        min_pain_strike = strike_prices[0]
        min_pain_value = float("inf")

        for test_strike in strike_prices:
            pain = 0.0
            for s in strikes:
                # Calls expire ITM when strike < test_strike
                if s.strike < test_strike:
                    pain += s.call_oi * (test_strike - s.strike)
                # Puts expire ITM when strike > test_strike
                if s.strike > test_strike:
                    pain += s.put_oi * (s.strike - test_strike)

            if pain < min_pain_value:
                min_pain_value = pain
                min_pain_strike = test_strike

        return min_pain_strike
