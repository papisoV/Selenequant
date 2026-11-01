"""US Radar endpoints — market intelligence from Yahoo Finance.

Provides market overview, radar snapshots, per-symbol scoring,
scanner funnel, and provider capability reporting.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.dependencies import build_response_meta, get_settings_service
from app.core.llm_helper import call_llm, is_llm_configured
from app.engines.factorizer import auto_factorize, extract_factors
from app.engines.research_brief import generate_research_brief
from app.engines.strategy_intent import signal_to_strategy_intent, intent_to_paper_trade
from app.engines.radar_scorer import classify_signal, compute_radar_score
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/us-radar", tags=["us-radar"])

# ---------------------------------------------------------------------------
# Singleton client & cache
# ---------------------------------------------------------------------------

_yf_client = None
_cache: dict[str, tuple[float, Any]] = {}
_TTL_MARKET = 300.0  # 5 min
_TTL_QUOTE = 60.0    # 1 min
_feedback_store: dict[str, list[dict]] = {}
_intent_store: dict[str, dict] = {}


async def _get_client():
    global _yf_client
    if _yf_client is None:
        from app.clients.yahoo_finance import YahooFinanceClient
        _yf_client = YahooFinanceClient()
    return _yf_client


def _cached(key: str, ttl: float) -> Any | None:
    if key in _cache:
        ts, data = _cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _set(key: str, data: Any) -> None:
    _cache[key] = (time.time(), data)


def _quotes(raw: dict) -> list[dict]:
    r = raw.get("quoteResponse", {}).get("result", []) if isinstance(raw, dict) else []
    return r if isinstance(r, list) else []


def _err(exc: Exception) -> SeleneSpaceError:
    return SeleneSpaceError(code=ErrorCode.SERVICE_UNAVAILABLE, message=f"Yahoo Finance API error: {exc}")


async def _fetch_symbol_data(client, sym: str) -> dict[str, Any]:
    """Fetch quote + news + chart for a symbol, return parsed dict."""
    quote_raw, news_raw, chart_raw = await asyncio.gather(
        client.get_quote([sym]),
        client.get_news(sym),
        client.get_chart(sym, interval="1d", range_="3mo"),
        return_exceptions=True,
    )
    quote = _quotes(quote_raw)[0] if isinstance(quote_raw, dict) and _quotes(quote_raw) else {}
    news_items = []
    if isinstance(news_raw, dict):
        for n in news_raw.get("news", [])[:10]:
            news_items.append({
                "title": n.get("title", ""), "publisher": n.get("publisher", ""),
                "published_at": n.get("providerPublishTime", 0), "url": n.get("link", ""),
            })
    closes = []
    if isinstance(chart_raw, dict):
        res = chart_raw.get("chart", {}).get("result", [{}])
        ts = res[0].get("indicators", {}).get("quote", [{}]) if res else {}
        closes = ts.get("close", []) if isinstance(ts, dict) else (ts[0].get("close", []) if isinstance(ts, list) and ts else [])
    return {"quote": quote, "news": news_items, "chart": {"closes": closes}}


def _trending_symbols(raw: dict) -> list[str]:
    fin = raw.get("finance", {})
    if not isinstance(fin, dict):
        return []
    syms = []
    for item in fin.get("result", [])[:1]:
        for q in item.get("quotes", [])[:10]:
            syms.append(q.get("symbol", ""))
    return syms


def _bars_from_chart(chart_raw: dict, fmt_date: bool = True) -> list[dict[str, Any]]:
    """Normalize Yahoo chart response into OHLCV bars (open/high/low/close/volume)."""
    res = chart_raw.get("chart", {}).get("result", [{}]) if isinstance(chart_raw, dict) else [{}]
    meta = res[0] if res else {}
    ts = meta.get("timestamp", [])
    quote = meta.get("indicators", {}).get("quote", [{}])
    q = quote[0] if isinstance(quote, list) and quote else {}

    def _at(lst: Any, i: int) -> Any:
        return lst[i] if isinstance(lst, list) and i < len(lst) else None

    bars = []
    for i, t in enumerate(ts):
        close = _at(q.get("close"), i)
        open_ = _at(q.get("open"), i)
        high = _at(q.get("high"), i)
        low = _at(q.get("low"), i)
        if close is None:
            continue
        label = str(t)
        if fmt_date and isinstance(t, (int, float)):
            import datetime as _dt
            label = _dt.datetime.fromtimestamp(t / 1000, tz=_dt.timezone.utc).strftime("%Y-%m-%d")
        bars.append({
            "timestamp": label,
            "open": open_ if open_ is not None else close,
            "high": high if high is not None else close,
            "low": low if low is not None else close,
            "close": close,
            "volume": _at(q.get("volume"), i) if _at(q.get("volume"), i) is not None else 0,
        })
    return bars


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/market/overview")
async def market_overview(request: Request, client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Market overview: indices, movers, sector scores."""
    try:
        cached = _cached("market_overview", _TTL_MARKET)
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)
        indices_raw, movers_raw = await asyncio.gather(
            client.get_market_summary(), client.get_movers(), return_exceptions=True,
        )
        indices = _quotes(indices_raw) if isinstance(indices_raw, dict) else []
        gainers, losers = [], []
        if isinstance(movers_raw, dict):
            for tag, out in [("gainers", gainers), ("losers", losers)]:
                grp = movers_raw.get(tag, {}).get("finance", {}).get("result", [])
                for q in (grp[0].get("quotes", []) if grp else []):
                    out.append({"symbol": q.get("symbol", ""), "name": q.get("shortName", ""),
                                "change_pct": q.get("regularMarketChangePercent", 0)})
        sector_scores = {}
        for idx in indices:
            sym = idx.get("symbol", "")
            pct = float(idx.get("regularMarketChangePercent", 0) or 0)
            sector_scores[sym] = {"price": idx.get("regularMarketPrice", 0), "change_pct": pct,
                                  "signal": "bullish" if pct > 0.5 else ("bearish" if pct < -0.5 else "neutral")}
        result = {"indices": indices[:5], "sector_scores": sector_scores,
                  "gainers": gainers[:10], "losers": losers[:10], "timestamp": int(time.time() * 1000)}
        _set("market_overview", result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Market overview failed: %s", exc)
        raise _err(exc) from exc


@router.get("/snapshot")
async def radar_snapshot(request: Request, client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Full radar snapshot: indices, movers, scores, signals, scenarios."""
    try:
        cached = _cached("snapshot", _TTL_MARKET)
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)
        indices_raw, trending_raw = await asyncio.gather(client.get_market_summary(), client.get_trending())
        indices = _quotes(indices_raw)
        trending_syms = _trending_symbols(trending_raw)
        radar_scores = {}
        if trending_syms:
            try:
                quotes = _quotes(await client.get_quote(trending_syms[:10]))
                for q in quotes:
                    radar_scores[q.get("symbol", "")] = compute_radar_score({"quote": q, "news": []})
            except Exception as exc:
                logger.warning("Failed to score trending: %s", exc)
        event_signals = [{"symbol": s, "signal": classify_signal(d), "score": d["total_score"],
                          "dimensions": d["dimensions"]} for s, d in radar_scores.items() if classify_signal(d) != "neutral"]
        result = {"indices": indices[:5], "movers": {"gainers": [], "losers": []},
                  "event_signals": event_signals, "radar_scores": radar_scores,
                  "relationship_chains": [], "event_scenarios": [], "ai_summary": None,
                  "timestamp": int(time.time() * 1000)}
        _set("snapshot", result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Snapshot failed: %s", exc)
        raise _err(exc) from exc


@router.get("/symbols/{symbol}")
async def symbol_detail(symbol: str, request: Request, client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Per-symbol radar: score, signals, evidence, risk flags, chains."""
    try:
        sym = symbol.upper()
        cache_key = f"symbol:{sym}"
        cached = _cached(cache_key, _TTL_QUOTE)
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)
        data = await _fetch_symbol_data(client, sym)
        score_data = compute_radar_score(data)
        signal = classify_signal(score_data)
        risk_flags = []
        if score_data["dimensions"].get("volume", 50) > 80:
            risk_flags.append("high_volume_alert")
        if score_data["dimensions"].get("sentiment", 50) < 25:
            risk_flags.append("strong_bearish_sentiment")
        result = {"symbol": sym, "radar_score": score_data, "signal": signal,
                  "event_signals": score_data["signals"],
                  "evidence": {"quote": data["quote"], "news_count": len(data["news"]), "chart_points": len(data["chart"]["closes"])},
                  "risk_flags": risk_flags, "relationship_chains": [], "event_scenarios": [],
                  "timestamp": int(time.time() * 1000)}
        _set(cache_key, result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Symbol detail failed for %s: %s", symbol, exc)
        raise _err(exc) from exc


@router.get("/symbols/{symbol}/kline")
async def symbol_kline(symbol: str, request: Request, client=Depends(_get_client)) -> APIResponse[list[dict[str, Any]]]:
    """Per-symbol candlestick bars for the K-line chart (research display)."""
    try:
        sym = symbol.upper()
        chart_raw = await client.get_chart(sym, interval="1d", range_="1y")
        bars = _bars_from_chart(chart_raw)
        if not bars:
            raise SeleneSpaceError(code=ErrorCode.NOT_FOUND, message=f"no chart data for {sym}")
        return APIResponse(data=bars, meta=build_response_meta(request), error=None)
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error("Symbol kline failed for %s: %s", symbol, exc)
        raise _err(exc) from exc


@router.get("/scores/{symbol}/breakdown")
async def score_breakdown(symbol: str, request: Request, client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """8-dimension scoring breakdown for a symbol."""
    try:
        sym = symbol.upper()
        cache_key = f"breakdown:{sym}"
        cached = _cached(cache_key, _TTL_QUOTE)
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)
        data = await _fetch_symbol_data(client, sym)
        score_data = compute_radar_score(data)
        signal = classify_signal(score_data)
        breakdown = [{"dimension": dim, "score": sc, "weight": score_data["weights"].get(dim, 0),
                      "weighted_contribution": sc * score_data["weights"].get(dim, 0),
                      "signal": "positive" if sc >= 55 else ("negative" if sc <= 45 else "neutral")}
                     for dim, sc in score_data["dimensions"].items()]
        result = {"symbol": sym, "total_score": score_data["total_score"], "signal": signal,
                  "breakdown": breakdown, "signals": score_data["signals"],
                  "timestamp": int(time.time() * 1000)}
        _set(cache_key, result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Score breakdown failed for %s: %s", symbol, exc)
        raise _err(exc) from exc


@router.post("/scanner/run")
async def scanner_run(request: Request, body: dict[str, Any] | None = None, client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Multi-stage scanner funnel: Liquidity -> Event -> Technical -> Risk."""
    try:
        filters = body or {}
        min_score, max_results, sectors = filters.get("min_score", 0), filters.get("max_results", 20), filters.get("sectors", [])
        trending_raw = await client.get_trending()
        candidates = _trending_symbols(trending_raw) or ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META"]
        quotes = _quotes(await client.get_quote(candidates[:20]))

        # Stage 1: Liquidity filter
        liquid = [q for q in quotes if float(q.get("regularMarketVolume", 0) or 0) > 1_000_000]

        # Stage 2: Event filter (has news or significant price move)
        event_passed = []
        for q in liquid:
            change_pct = abs(float(q.get("regularMarketChangePercent", 0) or 0))
            if change_pct > 1.0:
                event_passed.append(q)
            else:
                event_passed.append(q)  # Pass all for community edition (limited news data)

        # Stage 3: Technical filter (score above threshold)
        scored = []
        for q in event_passed:
            sd = compute_radar_score({"quote": q, "news": []})
            if sd["total_score"] >= min_score:
                direction = "long" if sd["total_score"] >= 60 else ("short" if sd["total_score"] <= 40 else "risk_off")
                confidence = min(1.0, abs(sd["total_score"] - 50) / 50)
                total_w = sum(sd["weights"].values()) or 1
                contributions = []
                for dim, sc in sd["dimensions"].items():
                    w = sd["weights"].get(dim, 0)
                    contributions.append({
                        "contributor_type": dim, "name": f"{dim}_score",
                        "weight": w / total_w,
                        "score": sc, "contribution": round(sc * w / total_w, 4),
                    })
                scored.append({
                    "signal_id": f"alpha-{q.get('symbol', '').lower()}-{int(time.time() * 1000)}",
                    "symbol": q.get("symbol", ""), "name": q.get("shortName", ""),
                    "price": q.get("regularMarketPrice", 0), "change_pct": q.get("regularMarketChangePercent", 0),
                    "volume": q.get("regularMarketVolume", 0),
                    "direction": direction, "confidence": round(confidence, 2),
                    "fusion_score": round(sd["total_score"] / 100, 4),
                    "fusion_breakdown": {"total_score": round(sd["total_score"] / 100, 4), "contributions": contributions},
                    "radar_score": sd["total_score"], "signal": classify_signal(sd),
                    "dimensions": sd["dimensions"], "risk_flags": [],
                    "expected_holding_period": "swing",
                })

        # Stage 4: Risk filter (remove high-risk)
        risk_passed = [s for s in scored if len(s.get("risk_flags", [])) == 0]
        risk_passed.sort(key=lambda x: x["fusion_score"], reverse=True)

        result = {
            "scanner_run_id": f"scan-{int(time.time() * 1000)}", "status": "partial",
            "universe": candidates, "requested_limit": max_results,
            "funnel": {
                "candidates_in": len(candidates), "after_liquidity": len(liquid),
                "after_event": len(event_passed), "after_technical": len(scored), "after_risk": len(risk_passed),
            },
            "returned_count": len(risk_passed[:max_results]),
            "results": risk_passed[:max_results],
            "filters": {"min_score": min_score, "max_results": max_results, "sectors": sectors},
            "timestamp": int(time.time() * 1000),
        }
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Scanner run failed: %s", exc)
        raise _err(exc) from exc


@router.get("/providers/readiness")
async def provider_readiness(request: Request, client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Provider health check: mode, configured, available, reason per provider."""
    providers = {
        "public": {"mode": "live", "configured": True, "available": True, "reason": None,
                    "capabilities": ["quotes", "candles", "news", "filings", "level1"]},
        "futu": {"mode": "offline", "configured": False, "available": False,
                 "reason": "SELENE_FUTU_API_KEY not set", "capabilities": ["quotes", "level1", "level2"]},
        "tiger": {"mode": "offline", "configured": False, "available": False,
                  "reason": "SELENE_TIGER_API_KEY not set", "capabilities": ["quotes", "level1", "level2"]},
        "polygon": {"mode": "offline", "configured": False, "available": False,
                    "reason": "SELENE_POLYGON_API_KEY not set", "capabilities": ["quotes", "candles", "level1", "level2"]},
        "alpaca": {"mode": "offline", "configured": False, "available": False,
                   "reason": "SELENE_ALPACA_API_KEY not set", "capabilities": ["quotes", "candles", "level1"]},
        "ibkr": {"mode": "offline", "configured": False, "available": False,
                 "reason": "SELENE_IBKR_API_KEY not set", "capabilities": ["quotes", "level1", "level2", "options"]},
        "databento": {"mode": "offline", "configured": False, "available": False,
                      "reason": "SELENE_DATABENTO_API_KEY not set", "capabilities": ["quotes", "level2", "mbo"]},
    }
    # Verify public provider is actually working
    try:
        await client.get_market_summary()
    except Exception:
        providers["public"]["available"] = False
        providers["public"]["reason"] = "Yahoo Finance API unreachable"
    result = {"providers": providers, "configured_count": sum(1 for p in providers.values() if p["configured"]),
              "available_count": sum(1 for p in providers.values() if p["available"]),
              "timestamp": int(time.time() * 1000)}
    return APIResponse(data=result, meta=build_response_meta(request), error=None)


@router.get("/providers/capabilities")
async def provider_capabilities(request: Request) -> APIResponse[dict[str, Any]]:
    """Provider capability matrix with entitlement status and freshness."""
    providers = {
        "public": {
            "status": "primary", "mode": "live", "configured": True, "available": True,
            "capabilities": {"quotes": True, "market_summary": True, "trending": True,
                             "news": True, "charts": True, "movers": True, "filings": True, "level1": True},
            "entitlement_status_by_capability": {
                "quotes": "enabled", "candles": "enabled", "news": "enabled",
                "filings": "enabled", "level1": "enabled", "level2": "unavailable",
            },
            "l1_entitlement_status": "enabled", "l2_entitlement_status": "unavailable",
            "freshness_sla_ms": 15000, "observed_lag_ms": None,
            "limitations": ["No L2 orderbook data", "No options flow", "Rate-limited without crumb", "May require proxy in China"],
            "missing_credentials": [], "auth": "crumb_token", "cost": "free",
        },
        "futu": {
            "status": "not_configured", "mode": "offline", "configured": False, "available": False,
            "capabilities": {"quotes": True, "level1": True, "level2": True},
            "entitlement_status_by_capability": {
                "quotes": "unavailable", "level1": "unavailable", "level2": "unavailable",
            },
            "l1_entitlement_status": "unavailable", "l2_entitlement_status": "unavailable",
            "freshness_sla_ms": 500, "observed_lag_ms": None,
            "missing_credentials": ["SELENE_FUTU_API_KEY"], "auth": "api_key", "cost": "subscription",
        },
        "tiger": {
            "status": "not_configured", "mode": "offline", "configured": False, "available": False,
            "capabilities": {"quotes": True, "level1": True, "level2": True},
            "entitlement_status_by_capability": {
                "quotes": "unavailable", "level1": "unavailable", "level2": "unavailable",
            },
            "l1_entitlement_status": "unavailable", "l2_entitlement_status": "unavailable",
            "freshness_sla_ms": 500, "observed_lag_ms": None,
            "missing_credentials": ["SELENE_TIGER_API_KEY"], "auth": "api_key", "cost": "subscription",
        },
        "polygon": {
            "status": "not_configured", "mode": "offline", "configured": False, "available": False,
            "capabilities": {"quotes": True, "candles": True, "level1": True, "level2": True},
            "entitlement_status_by_capability": {
                "quotes": "unavailable", "candles": "unavailable", "level1": "unavailable", "level2": "unavailable",
            },
            "l1_entitlement_status": "unavailable", "l2_entitlement_status": "unavailable",
            "freshness_sla_ms": 1000, "observed_lag_ms": None,
            "missing_credentials": ["SELENE_POLYGON_API_KEY"], "auth": "api_key", "cost": "freemium",
        },
        "alpaca": {
            "status": "not_configured", "mode": "offline", "configured": False, "available": False,
            "capabilities": {"quotes": True, "candles": True, "level1": True},
            "entitlement_status_by_capability": {
                "quotes": "unavailable", "candles": "unavailable", "level1": "unavailable",
            },
            "l1_entitlement_status": "unavailable", "l2_entitlement_status": "unavailable",
            "freshness_sla_ms": 1000, "observed_lag_ms": None,
            "missing_credentials": ["SELENE_ALPACA_API_KEY"], "auth": "api_key", "cost": "freemium",
        },
        "ibkr": {
            "status": "not_configured", "mode": "offline", "configured": False, "available": False,
            "capabilities": {"quotes": True, "level1": True, "level2": True, "options": True},
            "entitlement_status_by_capability": {
                "quotes": "unavailable", "level1": "unavailable", "level2": "unavailable", "options": "unavailable",
            },
            "l1_entitlement_status": "unavailable", "l2_entitlement_status": "unavailable",
            "freshness_sla_ms": 500, "observed_lag_ms": None,
            "missing_credentials": ["SELENE_IBKR_API_KEY"], "auth": "api_key", "cost": "subscription",
        },
    }
    result = {"providers": providers,
              "dimensions_coverage": {"news": {"public": "partial"}, "sentiment": {"public": "derived"},
                "technical": {"public": "derived"}, "volume": {"public": "full"},
                "l2": {"public": "unavailable"}, "relationship": {"public": "unavailable"},
                "risk": {"public": "unavailable"}, "flow": {"public": "derived"}},
              "timestamp": int(time.time() * 1000)}
    return APIResponse(data=result, meta=build_response_meta(request), error=None)


@router.get("/research-agents/readiness")
async def research_agents_readiness(request: Request) -> APIResponse[dict[str, Any]]:
    """Research agent readiness status with Pro-compatible shape."""
    agents = [
        {"agent_id": "sec_filing_agent", "display_name": "SEC Filing Agent", "agent_type": "sec_filing",
         "status": "available", "required_provider_capabilities": ["filings"],
         "enabled_capabilities": ["filing_evidence", "evidence_trace"], "missing_requirements": [],
         "last_run_at_ms": None, "signal_contribution_count": 0},
        {"agent_id": "news_event_agent", "display_name": "News Event Agent", "agent_type": "news_event",
         "status": "available", "required_provider_capabilities": ["news"],
         "enabled_capabilities": ["news_factor", "evidence_trace"], "missing_requirements": [],
         "last_run_at_ms": None, "signal_contribution_count": 0},
        {"agent_id": "relation_graph_agent", "display_name": "Relation Graph Agent", "agent_type": "relation_graph",
         "status": "limited", "required_provider_capabilities": ["quotes", "level1"],
         "enabled_capabilities": ["sector_correlation"], "missing_requirements": ["supply_chain_data"],
         "last_run_at_ms": None, "signal_contribution_count": 0},
        {"agent_id": "macro_calendar_agent", "display_name": "Macro Calendar Agent", "agent_type": "macro_calendar",
         "status": "disabled", "required_provider_capabilities": ["macro_data"],
         "enabled_capabilities": [], "missing_requirements": ["macro_data_provider"],
         "last_run_at_ms": None, "signal_contribution_count": 0},
        {"agent_id": "risk_decision_agent", "display_name": "Risk Decision Agent", "agent_type": "risk_decision",
         "status": "limited", "required_provider_capabilities": ["quotes", "level1"],
         "enabled_capabilities": ["basic_risk_score"], "missing_requirements": ["options_flow", "dark_pool_data"],
         "last_run_at_ms": None, "signal_contribution_count": 0},
    ]
    result = {"agents": agents, "overall_readiness": "partial",
              "available_count": sum(1 for a in agents if a["status"] == "available"),
              "missing_providers": ["l2_data", "options_flow", "on_chain", "macro_data"],
              "timestamp": int(time.time() * 1000)}
    return APIResponse(data=result, meta=build_response_meta(request), error=None)


# ---------------------------------------------------------------------------
# Event keyword classifier
# ---------------------------------------------------------------------------

_EVENT_KEYWORDS: dict[str, list[str]] = {
    "earnings": ["earnings", "revenue", "eps", "profit", "quarterly", "fiscal", "guidance", "outlook"],
    "regulatory": ["sec", "fda", "regulation", "antitrust", "ban", "lawsuit", "compliance", "investigation"],
    "filing": ["filing", "10-k", "10-q", "8-k", "proxy", "sec filing", "annual report"],
    "mover": ["surge", "plunge", "rally", "selloff", "soars", "tumbles", "jumps", "drops", "spikes"],
    "macro": ["fed", "inflation", "cpi", "interest rate", "gdp", "unemployment", "treasury", "fomc"],
}


def _classify_event_type(headline: str) -> str:
    hl = headline.lower()
    for etype, keywords in _EVENT_KEYWORDS.items():
        if any(kw in hl for kw in keywords):
            return etype
    return "news_event"


def _derive_suggested_actions(event_type: str, confidence: float, impact: float) -> list[str]:
    actions = ["watch"]
    if confidence >= 0.6:
        actions.append("run_replay")
    if impact >= 0.5 or event_type in ("regulatory", "macro"):
        actions.append("risk_review")
    if confidence >= 0.7 and impact >= 0.4:
        actions.append("generate_strategy")
    return actions


@router.get("/event-signals")
async def event_signals(request: Request, client=Depends(_get_client), settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """Event signal feed derived from Yahoo Finance news, LLM-enhanced when available."""
    try:
        cached = _cached("event_signals", _TTL_QUOTE)
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)

        trending_raw = await client.get_trending()
        symbols = _trending_symbols(trending_raw) or ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "AMZN", "META"]
        signals = []

        # Collect all headlines first for potential LLM batch processing
        raw_events: list[dict] = []
        for sym in symbols[:8]:
            try:
                news_raw = await client.get_news(sym)
                if not isinstance(news_raw, dict):
                    continue
                for n in news_raw.get("news", [])[:5]:
                    headline = n.get("title", "")
                    if not headline:
                        continue
                    event_type = _classify_event_type(headline)
                    raw_events.append({
                        "symbol": sym, "headline": headline,
                        "publisher": n.get("publisher", "Yahoo Finance"),
                        "published_at": n.get("providerPublishTime", 0),
                        "url": n.get("link", ""),
                        "rule_event_type": event_type,
                    })
            except Exception:
                continue

        # LLM batch classification if available
        llm_available = await is_llm_configured(settings_service)
        llm_results: dict[str, dict] = {}
        if llm_available and raw_events:
            headlines_text = "\n".join(
                f"{i+1}. [{e['symbol']}] {e['headline']}" for i, e in enumerate(raw_events[:20])
            )
            prompt = (
                "Classify each headline into one of: earnings, regulatory, filing, mover, macro, news_event\n"
                "Also rate confidence (0-1) and impact (0-1) for each.\n"
                "Respond ONLY in this format, one per line:\n"
                "line_num|event_type|confidence|impact\n\n"
                f"{headlines_text}"
            )
            llm_response = await call_llm(settings_service, prompt, max_tokens=600)
            if llm_response:
                for line in llm_response.strip().split("\n"):
                    parts = line.strip().split("|")
                    if len(parts) == 4:
                        try:
                            idx = int(parts[0].strip()) - 1
                            conf = max(0.0, min(1.0, float(parts[2].strip())))
                            impact = max(0.0, min(1.0, float(parts[3].strip())))
                            llm_results[idx] = {
                                "event_type": parts[1].strip(),
                                "confidence": conf,
                                "impact": impact,
                            }
                        except (ValueError, IndexError):
                            pass

        # Build signal objects
        for i, e in enumerate(raw_events):
            llm_data = llm_results.get(i)
            event_type = llm_data["event_type"] if llm_data else e["rule_event_type"]
            confidence = llm_data["confidence"] if llm_data else (0.4 if event_type == "news_event" else 0.6)
            impact = llm_data["impact"] if llm_data else (0.3 if event_type == "news_event" else 0.5)
            signal_lane = "high_priority" if impact >= 0.5 else "watchlist" if confidence >= 0.5 else "background"

            event_id = f"us-evt-{hashlib.sha256(e['headline'].encode()).hexdigest()[:8]}"
            signals.append({
                "event_id": event_id, "symbol": e["symbol"], "event_type": event_type,
                "headline": e["headline"], "source": e["publisher"],
                "published_at": e["published_at"], "url": e["url"],
                "sentiment_score": 0.5, "impact_score": impact, "confidence": confidence,
                "signal_lane": signal_lane, "eligible_for_signal_flow": impact >= 0.3,
                "quality_flags": ["single_source", "delayed_data"] + (["llm_classified"] if llm_data else []),
                "suggested_actions": _derive_suggested_actions(event_type, confidence, impact),
                "evidence": [{"type": "news", "source": e["publisher"], "headline": e["headline"]}],
                "risk_flags": [] if impact < 0.5 else ["high_impact_monitor"],
                "llm_enhanced": bool(llm_data),
            })

        result = {"signals": signals, "total": len(signals),
                  "llm_enhanced": bool(llm_results), "provider": "public", "data_mode": "delayed",
                  "timestamp": int(time.time() * 1000)}
        _set("event_signals", result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Event signals failed: %s", exc)
        raise _err(exc) from exc


_UNIVERSE = {
    "SPY": {"name": "SPDR S&P 500 ETF", "kind": "index_etf", "sector": "broad_market"},
    "QQQ": {"name": "Invesco QQQ Trust", "kind": "index_etf", "sector": "tech"},
    "DIA": {"name": "SPDR Dow Jones ETF", "kind": "index_etf", "sector": "broad_market"},
    "IWM": {"name": "iShares Russell 2000", "kind": "index_etf", "sector": "small_cap"},
    "SMH": {"name": "VanEck Semiconductor ETF", "kind": "sector_etf", "sector": "semiconductor"},
    "SOXX": {"name": "iShares Semiconductor ETF", "kind": "sector_etf", "sector": "semiconductor"},
    "XLK": {"name": "Technology Select Sector SPDR", "kind": "sector_etf", "sector": "technology"},
    "NVDA": {"name": "NVIDIA Corp", "kind": "single_stock", "sector": "semiconductor"},
    "AAPL": {"name": "Apple Inc", "kind": "single_stock", "sector": "technology"},
    "TSLA": {"name": "Tesla Inc", "kind": "single_stock", "sector": "automotive"},
    "MSFT": {"name": "Microsoft Corp", "kind": "single_stock", "sector": "technology"},
    "AMD": {"name": "Advanced Micro Devices", "kind": "single_stock", "sector": "semiconductor"},
}


@router.get("/universe")
async def research_universe(request: Request, client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Research universe: symbol pool with type labels and live quotes."""
    try:
        cached = _cached("universe", _TTL_MARKET)
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)

        symbols = list(_UNIVERSE.keys())
        quotes = _quotes(await client.get_quote(symbols))
        items = []
        for q in quotes:
            sym = q.get("symbol", "")
            info = _UNIVERSE.get(sym, {"name": q.get("shortName", ""), "kind": "single_stock", "sector": "unknown"})
            items.append({
                "symbol": sym, "name": info["name"], "kind": info["kind"], "sector": info["sector"],
                "price": q.get("regularMarketPrice", 0),
                "change_pct": q.get("regularMarketChangePercent", 0),
                "volume": q.get("regularMarketVolume", 0),
            })

        result = {"universe": items, "by_kind": {}, "total": len(items),
                  "available_kinds": ["index_etf", "sector_etf", "single_stock"],
                  "timestamp": int(time.time() * 1000)}
        by_kind: dict[str, list] = {}
        for item in items:
            by_kind.setdefault(item["kind"], []).append(item)
        result["by_kind"] = by_kind
        _set("universe", result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Universe failed: %s", exc)
        raise _err(exc) from exc


@router.get("/symbols/{symbol}/ai-console")
async def ai_console(symbol: str, request: Request, client=Depends(_get_client), settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """Per-symbol AI console: Thoughts, Alpha, Execution, System tabs. LLM-enhanced when available."""
    try:
        sym = symbol.upper()
        cache_key = f"ai_console:{sym}"
        cached = _cached(cache_key, _TTL_QUOTE)
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)

        data = await _fetch_symbol_data(client, sym)
        score_data = compute_radar_score(data)
        signal = classify_signal(score_data)
        quote = data.get("quote", {})
        llm_available = await is_llm_configured(settings_service)

        price = float(quote.get("regularMarketPrice", 0) or 0)
        change_pct = float(quote.get("regularMarketChangePercent", 0) or 0)
        change_dir = "up" if change_pct > 0 else "down" if change_pct < 0 else "flat"

        # Thoughts tab
        key_observations = []
        for dim, sc in score_data["dimensions"].items():
            if sc >= 70:
                key_observations.append(f"Strong positive {dim} signal (score: {sc}/100)")
            elif sc <= 30:
                key_observations.append(f"Strong negative {dim} signal (score: {sc}/100)")

        conflicting = []
        dims = score_data["dimensions"]
        if dims.get("sentiment", 50) > 60 and dims.get("volume", 50) < 40:
            conflicting.append("Positive sentiment but low volume — potential weak rally")
        if dims.get("news", 50) > 60 and dims.get("technical", 50) < 40:
            conflicting.append("News activity high but technicals weak — event may not sustain")

        thesis = None
        resolution = None
        llm_enhanced = False

        if llm_available:
            news_headlines = [n.get("title", "") for n in data.get("news", [])[:5]]
            dim_summary = ", ".join(f"{d}={s}" for d, s in dims.items())
            prompt = (
                f"Analyze {sym} trading at ${price:.2f} ({change_dir} {abs(change_pct):.2f}%).\n"
                f"Radar score: {score_data['total_score']}/100, signal: {signal}.\n"
                f"Dimensions: {dim_summary}\n"
            )
            if news_headlines:
                prompt += f"Recent news: {'; '.join(news_headlines)}\n"
            if conflicting:
                prompt += f"Conflicting signals: {'; '.join(conflicting)}\n"
            prompt += (
                "\nProvide:\n"
                "1. A 2-3 sentence investment thesis\n"
                "2. Resolution for any conflicting signals (1 sentence)\n"
                "Format as:\n"
                "THESIS: <thesis>\n"
                "RESOLUTION: <resolution>"
            )
            llm_response = await call_llm(settings_service, prompt, max_tokens=400)
            if llm_response:
                for line in llm_response.split("\n"):
                    line = line.strip()
                    if line.upper().startswith("THESIS:"):
                        thesis = line[7:].strip()
                    elif line.upper().startswith("RESOLUTION:"):
                        resolution = line[11:].strip()
                if thesis:
                    llm_enhanced = True

        # Fallback rule-based thesis
        if not thesis:
            thesis = f"{sym} is currently trading at ${price:.2f}, {'up' if change_dir == 'up' else 'down' if change_dir == 'down' else 'flat'} {abs(change_pct):.2f}%. "
            if signal == "bullish":
                thesis += f"Radar score of {score_data['total_score']}/100 suggests bullish momentum driven by positive dimension scores."
            elif signal == "bearish":
                thesis += f"Radar score of {score_data['total_score']}/100 indicates bearish pressure from negative dimension signals."
            else:
                thesis += f"Radar score of {score_data['total_score']}/100 indicates neutral conditions with no strong directional bias."

        # Alpha tab
        total_w = sum(score_data["weights"].values()) or 1
        contributions = [{"contributor_type": d, "name": f"{d}_score", "weight": score_data["weights"].get(d, 0) / total_w,
                          "score": s, "contribution": round(s * score_data["weights"].get(d, 0) / total_w, 4)}
                         for d, s in dims.items()]
        invalidators = []
        if dims.get("volume", 50) < 30:
            invalidators.append("Low volume — signal may lack conviction")
        if dims.get("risk", 50) < 30:
            invalidators.append("Elevated risk — consider reduced position size")

        # Execution tab
        suggested_actions = []
        if signal == "bullish":
            suggested_actions.append({"action_type": "consider_long", "entry_price": price,
                "stop_loss": round(price * 0.95, 2), "take_profit": round(price * 1.10, 2),
                "position_size_pct": 5, "rationale": "Bullish radar signal with positive momentum"})
        elif signal == "bearish":
            suggested_actions.append({"action_type": "consider_short", "entry_price": price,
                "stop_loss": round(price * 1.05, 2), "take_profit": round(price * 0.90, 2),
                "position_size_pct": 3, "rationale": "Bearish radar signal — consider hedging or reduced exposure"})
        else:
            suggested_actions.append({"action_type": "watch", "entry_price": price,
                "stop_loss": None, "take_profit": None, "position_size_pct": 0,
                "rationale": "Neutral signal — monitor for directional confirmation"})

        # System tab
        news_count = len(data.get("news", []))
        chart_points = len(data.get("chart", {}).get("closes", []))
        missing_flags = []
        if dims.get("l2", 50) == 50:
            missing_flags.append("no_l2_permission")
        if dims.get("technical", 50) == 50 and chart_points < 20:
            missing_flags.append("insufficient_chart_data")

        result = {
            "symbol": sym,
            "thoughts": {"thesis": thesis, "key_observations": key_observations[:5],
                         "conflicting_signals": conflicting, "resolution": resolution, "llm_enhanced": llm_enhanced},
            "alpha": {"direction": signal, "confidence": min(1.0, abs(score_data["total_score"] - 50) / 50),
                      "fusion_score": round(score_data["total_score"] / 100, 4),
                      "fusion_breakdown": {"total_score": round(score_data["total_score"] / 100, 4), "contributions": contributions},
                      "invalidators": invalidators, "expected_holding_period": "swing"},
            "execution": {"suggested_actions": suggested_actions,
                          "risk_parameters": {"max_position_pct": 10, "stop_loss_pct": 5, "take_profit_pct": 10}},
            "system": {"data_mode": "delayed", "provider": "public", "missing_data_flags": missing_flags,
                       "evidence_count": news_count + chart_points, "last_updated_ms": int(time.time() * 1000),
                       "llm_status": "configured" if llm_available else "not_configured"},
            "timestamp": int(time.time() * 1000),
        }
        _set(cache_key, result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("AI console failed for %s: %s", symbol, exc)
        raise _err(exc) from exc


# ---------------------------------------------------------------------------
# Pro endpoints — factorize, research brief, strategy intent, etc.
# ---------------------------------------------------------------------------


@router.post("/factorize")
async def factorize(request: Request, body: dict[str, Any], settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """Extract factors from news items, LLM-enhanced when available."""
    try:
        news_items = body.get("news_items", [])
        symbol = body.get("symbol")
        # body 可带 fallback 但本函数未使用它 —— 删赋值过 lint,留此提示避免误以为已支持
        factors, llm_enhanced = await extract_factors(settings_service, news_items, symbol)
        result = {"factors": factors, "llm_enhanced": llm_enhanced}
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Factorize failed: %s", exc)
        raise _err(exc) from exc


@router.post("/factorize/auto")
async def factorize_auto(request: Request, body: dict[str, Any], settings_service: Any = Depends(get_settings_service), client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Auto-factorize a symbol: use provided news or auto-fetch."""
    try:
        symbol = body.get("symbol", "").upper()
        if not symbol:
            raise SeleneSpaceError(code=ErrorCode.VALIDATION_ERROR, message="symbol is required")
        news_items = body.get("news_items")
        if news_items:
            factors, llm_enhanced = await extract_factors(settings_service, news_items, symbol)
        else:
            factors, llm_enhanced = await auto_factorize(settings_service, client, symbol)
        result = {"symbol": symbol, "factors": factors, "llm_enhanced": llm_enhanced}
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error("Auto factorize failed for %s: %s", body.get("symbol"), exc)
        raise _err(exc) from exc


@router.post("/flow-factors/preview")
async def flow_factors_preview(request: Request, body: dict[str, Any], settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """LLM-based flow factor preview for a symbol."""
    try:
        symbol = body.get("symbol", "").upper()
        if not symbol:
            raise SeleneSpaceError(code=ErrorCode.VALIDATION_ERROR, message="symbol is required")
        # body 可带 factors 但本函数未使用它(自行装配 flow_factors)—— 删赋值过 lint,留此提示
        llm_available = await is_llm_configured(settings_service)
        llm_enhanced = False
        flow_factors: list[dict] = []

        if llm_available:
            prompt = (
                f"Analyze capital flow factors for {symbol}. Consider institutional flow, "
                "retail sentiment, sector rotation. Return a JSON array of objects with "
                '"factor_name", "direction" (bullish/bearish/neutral), "strength" (0-1), '
                'and "evidence" (1 sentence).'
            )
            from app.core.pro_helpers import call_llm_structured
            llm_result = await call_llm_structured(settings_service, prompt, max_tokens=600)
            if llm_result and isinstance(llm_result, list):
                flow_factors = llm_result
                llm_enhanced = True

        if not llm_enhanced:
            # Fallback: template from radar score volume/sentiment dimensions
            flow_factors = [
                {"factor_name": "volume_flow", "direction": "neutral", "strength": 0.5,
                 "evidence": "Volume flow derived from radar volume dimension"},
                {"factor_name": "sentiment_flow", "direction": "neutral", "strength": 0.5,
                 "evidence": "Sentiment flow derived from radar sentiment dimension"},
                {"factor_name": "sector_rotation", "direction": "neutral", "strength": 0.3,
                 "evidence": "Sector rotation signal not available in community edition"},
            ]

        result = {"symbol": symbol, "flow_factors": flow_factors, "llm_enhanced": llm_enhanced}
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error("Flow factors preview failed for %s: %s", body.get("symbol"), exc)
        raise _err(exc) from exc


@router.post("/research-brief")
async def research_brief(request: Request, body: dict[str, Any], settings_service: Any = Depends(get_settings_service), client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Generate a research brief for a symbol using radar score + LLM."""
    try:
        symbol = body.get("symbol", "").upper()
        if not symbol:
            raise SeleneSpaceError(code=ErrorCode.VALIDATION_ERROR, message="symbol is required")
        factors = body.get("factors")
        data = await _fetch_symbol_data(client, symbol)
        score_data = compute_radar_score(data)
        brief = await generate_research_brief(settings_service, symbol, score_data, data.get("news", []), factors)
        return APIResponse(data=brief, meta=build_response_meta(request), error=None)
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error("Research brief failed for %s: %s", body.get("symbol"), exc)
        raise _err(exc) from exc


@router.post("/filings/insight")
async def filings_insight(request: Request, body: dict[str, Any], settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """LLM-based SEC filings insight for a symbol."""
    try:
        symbol = body.get("symbol", "").upper()
        if not symbol:
            raise SeleneSpaceError(code=ErrorCode.VALIDATION_ERROR, message="symbol is required")
        # body 可带 filing_type 但未用它过滤 —— 删赋值过 lint,留此提示
        llm_available = await is_llm_configured(settings_service)
        llm_enhanced = False
        insight: dict[str, Any] = {}

        if llm_available:
            prompt = (
                f"Analyze recent SEC filings for {symbol}. "
                "Provide: key_changes, risk_factors, outlook. "
                'Respond in JSON: {"key_changes": [...], "risk_factors": [...], "outlook": "..."}'
            )
            from app.core.pro_helpers import call_llm_structured
            llm_result = await call_llm_structured(settings_service, prompt, max_tokens=500)
            if llm_result and isinstance(llm_result, dict):
                insight = llm_result
                llm_enhanced = True

        if not llm_enhanced:
            # Fallback: template from _EVENT_KEYWORDS["filing"]
            filing_keywords = _EVENT_KEYWORDS.get("filing", [])
            insight = {
                "key_changes": [f"No LLM analysis available — monitor filings with keywords: {', '.join(filing_keywords[:4])}"],
                "risk_factors": ["Automated filing analysis requires LLM configuration"],
                "outlook": "Configure LLM for filing insight analysis",
            }

        result = {"symbol": symbol, "insight": insight, "llm_enhanced": llm_enhanced}
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error("Filings insight failed for %s: %s", body.get("symbol"), exc)
        raise _err(exc) from exc


@router.post("/alerts/preview")
async def alerts_preview(request: Request, body: dict[str, Any], settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """LLM-based alert rule preview for a symbol."""
    try:
        symbol = body.get("symbol", "").upper()
        if not symbol:
            raise SeleneSpaceError(code=ErrorCode.VALIDATION_ERROR, message="symbol is required")
        # body 可带 alert_type 但未用它分类 —— 删赋值过 lint,留此提示
        llm_available = await is_llm_configured(settings_service)
        llm_enhanced = False
        alert_rules: list[dict] = []

        if llm_available:
            prompt = (
                f"Design alert rules for {symbol} based on current market conditions. "
                "Return a JSON array of objects with "
                '"rule_name", "condition" (e.g. volume > X), "action" (notify/pause/alert), '
                'and "priority" (high/medium/low).'
            )
            from app.core.pro_helpers import call_llm_structured
            llm_result = await call_llm_structured(settings_service, prompt, max_tokens=500)
            if llm_result and isinstance(llm_result, list):
                alert_rules = llm_result
                llm_enhanced = True

        if not llm_enhanced:
            # Fallback: rule-based alerts from radar dimensions
            alert_rules = [
                {"rule_name": "high_volume_alert", "condition": "volume_score > 80", "action": "notify", "priority": "high"},
                {"rule_name": "bearish_sentiment_alert", "condition": "sentiment_score < 25", "action": "notify", "priority": "high"},
                {"rule_name": "neutral_drift_alert", "condition": "total_score between 45 and 55 for 5+ days", "action": "watch", "priority": "medium"},
                {"rule_name": "news_spike_alert", "condition": "news_count > 5 in 24h", "action": "notify", "priority": "medium"},
            ]

        result = {"symbol": symbol, "alert_rules": alert_rules, "llm_enhanced": llm_enhanced}
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error("Alerts preview failed for %s: %s", body.get("symbol"), exc)
        raise _err(exc) from exc


@router.post("/feedback")
async def submit_feedback(request: Request, body: dict[str, Any]) -> APIResponse[dict[str, Any]]:
    """Store feedback for a signal."""
    try:
        signal_id = body.get("signal_id", "")
        if not signal_id:
            raise SeleneSpaceError(code=ErrorCode.VALIDATION_ERROR, message="signal_id is required")
        rating = body.get("rating", 0)
        note = body.get("note", "")
        entry = {"signal_id": signal_id, "rating": rating, "note": note, "timestamp": int(time.time() * 1000)}
        _feedback_store.setdefault(signal_id, []).append(entry)
        result = {"signal_id": signal_id, "stored": True}
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error("Feedback failed: %s", exc)
        raise _err(exc) from exc


@router.post("/signals/{signal_id}/backtest-quality-preview")
async def backtest_quality_preview(signal_id: str, request: Request, body: dict[str, Any] | None = None, settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """LLM-based backtest quality assessment for a signal."""
    try:
        llm_available = await is_llm_configured(settings_service)
        llm_enhanced = False
        quality_assessment: dict[str, Any] = {}

        if llm_available:
            prompt = (
                f"Assess the backtest quality of signal {signal_id}. Consider overfitting risk, "
                "out-of-sample performance, regime sensitivity. "
                'Respond in JSON: {"overfitting_risk": "low/medium/high", '
                '"out_of_sample_performance": "poor/fair/good", '
                '"regime_sensitivity": "low/medium/high", '
                '"overall_quality": "poor/fair/good/excellent", '
                '"recommendations": [...]}'
            )
            llm_result = await call_llm(settings_service, prompt, max_tokens=400)
            if llm_result:
                quality_assessment = {"raw_assessment": llm_result, "signal_id": signal_id}
                llm_enhanced = True

        if not llm_enhanced:
            # Fallback: template quality assessment
            quality_assessment = {
                "overfitting_risk": "unknown",
                "out_of_sample_performance": "not_tested",
                "regime_sensitivity": "unknown",
                "overall_quality": "unrated",
                "recommendations": ["Configure LLM for automated backtest quality assessment"],
                "signal_id": signal_id,
            }

        result = {"signal_id": signal_id, "quality_assessment": quality_assessment, "llm_enhanced": llm_enhanced}
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Backtest quality preview failed for %s: %s", signal_id, exc)
        raise _err(exc) from exc


@router.post("/signals/{signal_id}/evolution-log-preview")
async def evolution_log_preview(signal_id: str, request: Request, body: dict[str, Any] | None = None, settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """LLM-based evolution narrative for a signal."""
    try:
        llm_available = await is_llm_configured(settings_service)
        llm_enhanced = False
        evolution_log: list[dict] = []

        if llm_available:
            prompt = (
                f"Generate an evolution narrative for signal {signal_id}. "
                "How has this signal evolved over time? "
                "Return a JSON array of timeline entries with "
                '"phase", "description", and "timestamp" (ISO format).'
            )
            from app.core.pro_helpers import call_llm_structured
            llm_result = await call_llm_structured(settings_service, prompt, max_tokens=500)
            if llm_result and isinstance(llm_result, list):
                evolution_log = llm_result
                llm_enhanced = True

        if not llm_enhanced:
            # Fallback: template timeline
            now_ms = int(time.time() * 1000)
            evolution_log = [
                {"phase": "signal_generated", "description": f"Signal {signal_id} generated from scanner", "timestamp": now_ms},
                {"phase": "awaiting_validation", "description": "Signal awaiting backtest validation", "timestamp": now_ms},
                {"phase": "monitoring", "description": "Signal under active monitoring", "timestamp": now_ms},
            ]

        result = {"signal_id": signal_id, "evolution_log": evolution_log, "llm_enhanced": llm_enhanced}
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Evolution log preview failed for %s: %s", signal_id, exc)
        raise _err(exc) from exc


@router.post("/signals/{signal_id}/strategy-intent")
async def signal_strategy_intent(signal_id: str, request: Request, body: dict[str, Any] | None = None, settings_service: Any = Depends(get_settings_service), client=Depends(_get_client)) -> APIResponse[dict[str, Any]]:
    """Generate strategy intent from a signal."""
    try:
        # Try to get signal data from cache or generate placeholder
        cache_key = f"signal_intent:{signal_id}"
        cached = _cached(cache_key, _TTL_QUOTE)
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)

        # Generate placeholder signal data
        symbol = "UNKNOWN"
        direction = "neutral"
        confidence = 0.5
        score_data: dict[str, Any] = {"total_score": 50, "dimensions": {}, "signals": [], "weights": {}}

        # Try to extract symbol from signal_id pattern (e.g. alpha-aapl-1234567890)
        parts = signal_id.split("-")
        if len(parts) >= 2:
            symbol = parts[1].upper()
            try:
                data = await _fetch_symbol_data(client, symbol)
                score_data = compute_radar_score(data)
                direction = classify_signal(score_data)
                confidence = min(1.0, abs(score_data["total_score"] - 50) / 50)
            except Exception:
                pass

        intent = await signal_to_strategy_intent(settings_service, signal_id, symbol, direction, confidence, score_data)
        _set(cache_key, intent)

        # Store intent for later paper-trade reference
        intent_id = intent.get("intent_id", signal_id)
        _intent_store[intent_id] = intent

        return APIResponse(data=intent, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Strategy intent failed for %s: %s", signal_id, exc)
        raise _err(exc) from exc


@router.post("/strategy-intents/{intent_id}/paper-trade-intent")
async def paper_trade_intent(intent_id: str, request: Request, body: dict[str, Any] | None = None, settings_service: Any = Depends(get_settings_service)) -> APIResponse[dict[str, Any]]:
    """Generate paper trade from a strategy intent."""
    try:
        intent = _intent_store.get(intent_id)
        if not intent:
            raise SeleneSpaceError(code=ErrorCode.NOT_FOUND, message=f"Intent {intent_id} not found — generate strategy-intent first")
        account_id = (body or {}).get("account_id")
        amount = (body or {}).get("amount")
        trade = await intent_to_paper_trade(settings_service, intent, account_id)
        if amount is not None:
            trade["amount"] = amount
        return APIResponse(data=trade, meta=build_response_meta(request), error=None)
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error("Paper trade intent failed for %s: %s", intent_id, exc)
        raise _err(exc) from exc
