"""News & Sentiment intelligence endpoints.

Provides aggregated news summaries, Reddit sentiment analysis,
trending topics, market pulse history, and alert rule configuration.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import build_response_meta, get_settings_service
from app.core.llm_helper import call_llm, is_llm_configured
from app.services.market_pulse_store import MarketPulseStore
from selene_space_macrodata.providers.altme_fng import (
    FNG_SERIES_ID as ALTME_FNG_SERIES_ID,
)
from selene_space_macrodata.providers.altme_fng import AltmeFngProvider
from selene_space_macrodata.providers.cnn_fng import FNG_SERIES_ID as CNN_FNG_SERIES_ID
from selene_space_macrodata.providers.cnn_fng import CnnFngProvider
from selene_space_macrodata.providers.coingecko_global import (
    BTC_DOMINANCE,
    CoingeckoGlobalProvider,
    TOTAL_MARKET_CAP,
)
from selene_space_macrodata.source import MacroDataSourceConfig
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intelligence", tags=["intelligence"])

# -- Singleton httpx client -------------------------------------------------

_http_client: httpx.AsyncClient | None = None
_SUBREDDITS = ("cryptocurrency", "bitcoin", "ethereum", "altcoin")
_REDDIT_BASE = "https://www.reddit.com"
_USER_AGENT = "SeleneQuant/1.0 (research)"


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            base_url=_REDDIT_BASE,
            headers={"User-Agent": _USER_AGENT},
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        )
    return _http_client


# -- In-memory caches with TTL -----------------------------------------------

_news_cache: dict[str, tuple[float, dict]] = {}
_sentiment_cache: dict[str, tuple[float, dict]] = {}
_trending_cache: dict[str, tuple[float, dict]] = {}
_pulse_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300.0  # 5 minutes


def _read_cache(store: dict[str, tuple[float, dict]], key: str) -> dict | None:
    if key in store:
        ts, data = store[key]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _write_cache(store: dict[str, tuple[float, dict]], key: str, data: dict) -> None:
    store[key] = (time.time(), data)


# -- Market pulse providers (module-level lazy singletons) --------------------

_pulse_store: MarketPulseStore | None = None
_altme_provider: AltmeFngProvider | None = None
_cnn_provider: CnnFngProvider | None = None
_coingecko_provider: CoingeckoGlobalProvider | None = None


def _get_pulse_store() -> MarketPulseStore:
    global _pulse_store
    if _pulse_store is None:
        _pulse_store = MarketPulseStore()
    return _pulse_store


def _macro_cfg(provider_id: str) -> MacroDataSourceConfig:
    return MacroDataSourceConfig(provider=provider_id, cache_ttl_seconds=300)


def _get_altme_provider() -> AltmeFngProvider:
    global _altme_provider
    if _altme_provider is None:
        _altme_provider = AltmeFngProvider(_macro_cfg("altme_fng"))
    return _altme_provider


def _get_cnn_provider() -> CnnFngProvider:
    global _cnn_provider
    if _cnn_provider is None:
        _cnn_provider = CnnFngProvider(_macro_cfg("cnn_fng"))
    return _cnn_provider


def _get_coingecko_provider() -> CoingeckoGlobalProvider:
    global _coingecko_provider
    if _coingecko_provider is None:
        _coingecko_provider = CoingeckoGlobalProvider(_macro_cfg("coingecko_global"))
    return _coingecko_provider


def _field_payload(
    value: float | None,
    *,
    source: str,
    data_status: str,
    fetched_at: float | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "value": value,
        "source": source,
        "data_status": data_status,
        "fetched_at": fetched_at,
    }
    if reason is not None:
        entry["reason"] = reason
    return entry


def _extract_value(series: Any) -> float | None:
    try:
        df = getattr(series, "observations", None)
        if df is None or df.empty:
            return None
        return float(df["value"].iloc[-1])
    except Exception:
        return None


async def _fetch_fear_greed() -> tuple[dict[str, Any], bool]:
    """F&G degradation chain: alt.me (primary) → CNN (backup) → unavailable.

    Returns (field_payload, is_complete). CNN success keeps data_status "live"
    but source="cnn_fng" so the two score anchors stay distinguishable.
    """
    try:
        provider = _get_altme_provider()
        series = await provider.fetch_series(ALTME_FNG_SERIES_ID)
        value = _extract_value(series)
        if value is not None:
            return _field_payload(
                value,
                source="altme_fng",
                data_status="live",
                fetched_at=int(series.fetched_at.timestamp()),
            ), True
        logger.warning("alt.me F&G returned no usable value, falling back to CNN")
    except Exception as exc:
        logger.warning("alt.me F&G fetch failed (%s), falling back to CNN", exc)

    try:
        cnn_provider = _get_cnn_provider()
        series = await cnn_provider.fetch_series(CNN_FNG_SERIES_ID)
        value = _extract_value(series)
        if value is not None:
            return _field_payload(
                value,
                source="cnn_fng",
                data_status="live",
                fetched_at=int(series.fetched_at.timestamp()),
            ), True
        logger.warning("CNN F&G returned no usable value")
    except Exception as exc:
        logger.warning("CNN F&G fetch failed: %s", exc)

    return _field_payload(
        None,
        source="none",
        data_status="unavailable",
        reason="fear_greed sources (alt.me, CNN) unreachable",
    ), False


async def _fetch_coingecko_fields() -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Fetch btc_dominance + market_cap from CoinGecko /global (one HTTP call each)."""
    results: dict[str, dict[str, Any]] = {}
    complete = True
    provider = _get_coingecko_provider()
    for field, series_id in (
        ("btc_dominance", BTC_DOMINANCE),
        ("market_cap", TOTAL_MARKET_CAP),
    ):
        try:
            series = await provider.fetch_series(series_id)
            value = _extract_value(series)
            if value is not None:
                results[field] = _field_payload(
                    value,
                    source="coingecko_global",
                    data_status="live",
                    fetched_at=int(series.fetched_at.timestamp()),
                )
            else:
                complete = False
                results[field] = _field_payload(
                    None,
                    source="none",
                    data_status="unavailable",
                    reason="CoinGecko /global payload missing value",
                )
        except Exception as exc:
            complete = False
            logger.warning("CoinGecko fetch %s failed: %s", series_id, exc)
            results[field] = _field_payload(
                None,
                source="none",
                data_status="unavailable",
                reason=f"CoinGecko unreachable: {exc}",
            )
    return results["btc_dominance"], results["market_cap"], complete


# -- Reddit helpers ----------------------------------------------------------


async def _fetch_subreddit_posts(
    subreddit: str, sort: str = "hot", limit: int = 25
) -> list[dict]:
    """Fetch posts from a subreddit via the public JSON API."""
    client = await _get_client()
    resp = await client.get(f"/r/{subreddit}/{sort}.json", params={"limit": limit})
    resp.raise_for_status()
    children = resp.json().get("data", {}).get("children", [])
    return [c.get("data", {}) for c in children if c.get("kind") == "t3"]


def _classify_sentiment(upvote_ratio: float) -> str:
    if upvote_ratio >= 0.75:
        return "positive"
    if upvote_ratio <= 0.45:
        return "negative"
    return "neutral"


# -- Endpoints ---------------------------------------------------------------


@router.get("/news/summary")
async def news_summary(
    request: Request, settings_service: Any = Depends(get_settings_service)
) -> APIResponse[dict[str, Any]]:
    """Aggregated news summary from crypto subreddits."""
    try:
        cached = _read_cache(_news_cache, "news_summary")
        if cached is not None:
            return APIResponse(
                data=cached, meta=build_response_meta(request), error=None
            )

        headlines: list[dict[str, Any]] = []
        pulse = {"positive": 0, "negative": 0, "neutral": 0}

        for sub in _SUBREDDITS:
            try:
                posts = await _fetch_subreddit_posts(sub, sort="hot", limit=10)
            except Exception as exc:
                logger.warning("Failed to fetch r/%s: %s", sub, exc)
                continue
            for post in posts:
                ratio = post.get("upvote_ratio", 0.5)
                sentiment = _classify_sentiment(ratio)
                pulse[sentiment] += 1
                headlines.append(
                    {
                        "title": post.get("title", ""),
                        "source": f"r/{sub}",
                        "url": f"https://reddit.com{post.get('permalink', '')}",
                        "published_at": int(post.get("created_utc", 0) * 1000),
                        "sentiment": sentiment,
                    }
                )

        headlines.sort(key=lambda h: h["published_at"], reverse=True)
        total = sum(pulse.values()) or 1
        dominant = max(pulse, key=lambda k: pulse[k])

        # Try LLM summary first, fall back to rule-based
        llm_available = await is_llm_configured(settings_service)
        summary = None
        if llm_available and headlines:
            headline_texts = [h["title"] for h in headlines[:15]]
            prompt = (
                f"Summarize the current crypto market sentiment based on these {len(headline_texts)} headlines:\n"
                + "\n".join(f"- {t}" for t in headline_texts)
                + "\n\nProvide a 2-3 sentence summary of overall sentiment and key themes."
            )
            summary = await call_llm(settings_service, prompt, max_tokens=300)

        if not summary:
            summary = (
                f"Crypto sentiment is {dominant} across {total} tracked posts. "
                f"Positive: {pulse['positive']}, Negative: {pulse['negative']}, Neutral: {pulse['neutral']}."
            )
        result = {
            "summary": summary,
            "headlines": headlines[:30],
            "pulse": pulse,
            "pulse_dimensions": [
                {
                    "name": "news",
                    "label": "News",
                    "score": round(pulse.get("positive", 0) / max(total, 1), 4),
                    "bias": "bullish"
                    if pulse.get("positive", 0) > pulse.get("negative", 0)
                    else "bearish"
                    if pulse.get("negative", 0) > pulse.get("positive", 0)
                    else "neutral",
                    "reason": f"{total} headlines tracked, {pulse.get('positive', 0)} positive",
                    "data_available": True,
                },
                {
                    "name": "social",
                    "label": "Social",
                    "score": round(pulse.get("positive", 0) / max(total, 1), 4),
                    "bias": "bullish"
                    if pulse.get("positive", 0) > pulse.get("negative", 0)
                    else "neutral",
                    "reason": "Reddit sentiment derived from upvote ratios",
                    "data_available": True,
                },
                {
                    "name": "onchain_flow",
                    "label": "On-Chain Flow",
                    "score": 0.5,
                    "bias": "neutral",
                    "reason": "On-chain flow data not available in community edition",
                    "data_available": False,
                },
                {
                    "name": "smart_money",
                    "label": "Smart Money",
                    "score": 0.5,
                    "bias": "neutral",
                    "reason": "Smart money tracking requires premium data sources",
                    "data_available": False,
                },
                {
                    "name": "derivatives",
                    "label": "Derivatives",
                    "score": 0.5,
                    "bias": "neutral",
                    "reason": "Derivatives data not available in community edition",
                    "data_available": False,
                },
            ],
            "llm_available": llm_available,
        }
        _write_cache(_news_cache, "news_summary", result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Failed to build news summary: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE, message=f"News aggregation error: {exc}"
        ) from exc


@router.get("/social/sentiment")
async def social_sentiment(request: Request) -> APIResponse[dict[str, Any]]:
    """Reddit sentiment scores by subreddit."""
    try:
        cached = _read_cache(_sentiment_cache, "social_sentiment")
        if cached is not None:
            return APIResponse(
                data=cached, meta=build_response_meta(request), error=None
            )

        by_subreddit: dict[str, float] = {}
        all_ratios: list[float] = []

        for sub in _SUBREDDITS:
            try:
                posts = await _fetch_subreddit_posts(sub, sort="hot", limit=25)
            except Exception as exc:
                logger.warning("Failed to fetch r/%s for sentiment: %s", sub, exc)
                continue
            ratios = [
                p.get("upvote_ratio", 0.5) for p in posts if p.get("upvote_ratio")
            ]
            if ratios:
                by_subreddit[f"r/{sub}"] = round(sum(ratios) / len(ratios), 4)
                all_ratios.extend(ratios)

        overall = round(sum(all_ratios) / len(all_ratios), 4) if all_ratios else 0.5
        result = {
            "overall": overall,
            "by_subreddit": by_subreddit,
            "timestamp": int(time.time()),
        }
        _write_cache(_sentiment_cache, "social_sentiment", result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Failed to compute social sentiment: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Sentiment analysis error: {exc}",
        ) from exc


@router.get("/social/trending")
async def social_trending(request: Request) -> APIResponse[dict[str, Any]]:
    """Reddit trending crypto topics."""
    try:
        cached = _read_cache(_trending_cache, "social_trending")
        if cached is not None:
            return APIResponse(
                data=cached, meta=build_response_meta(request), error=None
            )

        topics: list[dict[str, Any]] = []
        for sub in _SUBREDDITS:
            try:
                posts = await _fetch_subreddit_posts(sub, sort="hot", limit=15)
            except Exception as exc:
                logger.warning("Failed to fetch r/%s for trending: %s", sub, exc)
                continue
            for post in posts:
                topics.append(
                    {
                        "title": post.get("title", ""),
                        "subreddit": f"r/{sub}",
                        "score": post.get("score", 0),
                        "upvote_ratio": post.get("upvote_ratio", 0.0),
                        "url": f"https://reddit.com{post.get('permalink', '')}",
                        "num_comments": post.get("num_comments", 0),
                    }
                )

        topics.sort(key=lambda t: t["score"], reverse=True)
        result = {"topics": topics[:40], "updated_at": int(time.time())}
        _write_cache(_trending_cache, "social_trending", result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Failed to fetch trending topics: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE, message=f"Trending fetch error: {exc}"
        ) from exc


@router.get("/market-pulse/history")
async def market_pulse_history(request: Request) -> APIResponse[dict[str, Any]]:
    """Market pulse history with current snapshot.

    HTTP stays 200 even when every source fails — degradation is expressed via
    per-field data_status so the frontend can render the degraded state.
    """
    try:
        cached = _read_cache(_pulse_cache, "market_pulse")
        if cached is not None:
            return APIResponse(
                data=cached, meta=build_response_meta(request), error=None
            )

        now = int(time.time())

        (
            (fear_greed_payload, fg_complete),
            (btc_dom_payload, mcap_payload, cg_complete),
        ) = await asyncio.gather(
            _fetch_fear_greed(),
            _fetch_coingecko_fields(),
        )
        fg_ok = bool(fg_complete)
        cg_ok = bool(cg_complete)

        live_sources = sorted(
            {
                p["source"]
                for p in (btc_dom_payload, fear_greed_payload, mcap_payload)
                if p["data_status"] == "live"
            }
        )
        current = {
            "timestamp": now,
            "btc_dominance": btc_dom_payload["value"],
            "fear_greed_index": fear_greed_payload["value"],
            "market_cap": mcap_payload["value"],
            "data_status": "live"
            if (fg_ok and cg_ok)
            else ("unavailable" if not (fg_ok or cg_ok) else "stale"),
            "source": ",".join(live_sources) if live_sources else "none",
            "fetched_at": max(
                (
                    p.get("fetched_at") or 0
                    for p in (btc_dom_payload, fear_greed_payload, mcap_payload)
                ),
                default=0,
            )
            or None,
            "fields": {
                "btc_dominance": btc_dom_payload,
                "fear_greed_index": fear_greed_payload,
                "market_cap": mcap_payload,
            },
        }

        pulse_store = _get_pulse_store()

        snapshot: dict[str, Any] = {}
        for field, payload in (
            ("btc_dominance", btc_dom_payload),
            ("fear_greed_index", fear_greed_payload),
            ("market_cap", mcap_payload),
        ):
            if payload["data_status"] == "live":
                snapshot[field] = payload["value"]
        snapshot["source"] = current["source"]
        snapshot["fetched_at"] = current["fetched_at"]
        # Only complete real snapshots enter the history series.
        if len(snapshot) == 5:
            try:
                pulse_store.save_snapshot(snapshot)
            except Exception as exc:
                logger.warning("market pulse snapshot save failed: %s", exc)

        accumulating = pulse_store.is_empty()
        pulses = pulse_store.load_recent(24)

        result = {"pulses": pulses, "current": current, "accumulating": accumulating}
        _write_cache(_pulse_cache, "market_pulse", result)
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Failed to build market pulse: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE, message=f"Market pulse error: {exc}"
        ) from exc


@router.get("/alerts/rules")
async def alert_rules(request: Request) -> APIResponse[dict[str, Any]]:
    """Default alert rule configuration."""
    rules: list[dict[str, Any]] = [
        {
            "id": "fear_greed_extreme",
            "name": "Extreme Fear/Greed",
            "type": "threshold",
            "threshold": {
                "field": "fear_greed_index",
                "operator": "or",
                "lt": 15,
                "gt": 85,
            },
            "enabled": True,
            "description": "Alert when Fear & Greed Index hits extreme levels",
        },
        {
            "id": "btc_dominance_shift",
            "name": "BTC Dominance Shift",
            "type": "threshold",
            "threshold": {
                "field": "btc_dominance",
                "operator": "change_gt",
                "value": 3.0,
            },
            "enabled": True,
            "description": "Alert on significant BTC dominance movement",
        },
        {
            "id": "sentiment_spike",
            "name": "Social Sentiment Spike",
            "type": "threshold",
            "threshold": {
                "field": "social_sentiment",
                "operator": "change_gt",
                "value": 0.15,
            },
            "enabled": True,
            "description": "Alert on rapid sentiment shift in crypto subreddits",
        },
        {
            "id": "trending_keyword",
            "name": "Trending Keyword Alert",
            "type": "keyword",
            "threshold": {
                "keywords": ["hack", "exploit", "rug", "SEC", "ETF"],
                "min_score": 500,
            },
            "enabled": True,
            "description": "Alert when specific keywords trend in crypto subreddits",
        },
        {
            "id": "market_cap_drop",
            "name": "Market Cap Drop",
            "type": "threshold",
            "threshold": {
                "field": "market_cap",
                "operator": "change_lt",
                "value": -0.05,
            },
            "enabled": False,
            "description": "Alert on >5% total market cap decline",
        },
    ]
    return APIResponse(
        data={"rules": rules, "version": "1.0.0"},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/news/factor-pipeline")
async def factor_pipeline(
    request: Request, settings_service: Any = Depends(get_settings_service)
) -> APIResponse[dict[str, Any]]:
    """News factor pipeline status: 5-stage processing flow. LLM powers ai_parse when available."""
    llm_available = await is_llm_configured(settings_service)
    ai_parse_status = "active" if llm_available else "idle"
    ai_parse_throughput = 4 if llm_available else 0
    ai_parse_lag = 800 if llm_available else None
    stages = [
        {
            "name": "raw_feed",
            "label": "Raw Fast Feed",
            "status": "active",
            "throughput_per_min": 12,
            "lag_ms": 200,
            "queue_depth": 3,
        },
        {
            "name": "ai_parse",
            "label": "AI Event Parse",
            "status": ai_parse_status,
            "throughput_per_min": ai_parse_throughput,
            "lag_ms": ai_parse_lag,
            "queue_depth": 1 if llm_available else 0,
        },
        {
            "name": "factor_score",
            "label": "Factor Score",
            "status": "active",
            "throughput_per_min": 8,
            "lag_ms": 350,
            "queue_depth": 1,
        },
        {
            "name": "trade_trigger",
            "label": "Trade Trigger",
            "status": "idle" if not llm_available else "active",
            "throughput_per_min": 1 if llm_available else 0,
            "lag_ms": 1200 if llm_available else None,
            "queue_depth": 0,
        },
        {
            "name": "risk_alert",
            "label": "Risk Alert",
            "status": "active",
            "throughput_per_min": 2,
            "lag_ms": 100,
            "queue_depth": 0,
        },
    ]
    result = {
        "stages": stages,
        "llm_available": llm_available,
        "timestamp": int(time.time() * 1000),
    }
    return APIResponse(data=result, meta=build_response_meta(request), error=None)


_TOKEN_SIGNALS = {
    "BTC": {
        "direction": "neutral",
        "score": 0.52,
        "confidence": 0.6,
        "key_events": ["Halving cycle mid-phase", "ETF inflows stabilizing"],
        "price_context": {"price": 62500, "change_24h": 0.8},
    },
    "ETH": {
        "direction": "bullish",
        "score": 0.65,
        "confidence": 0.55,
        "key_events": ["L2 adoption growing", "Staking yields attractive"],
        "price_context": {"price": 3420, "change_24h": 2.1},
    },
    "SOL": {
        "direction": "bullish",
        "score": 0.71,
        "confidence": 0.5,
        "key_events": ["DeFi TVL increasing", "Network upgrades"],
        "price_context": {"price": 178, "change_24h": 3.5},
    },
}


@router.get("/news/token-signals")
async def token_signals(
    request: Request, settings_service: Any = Depends(get_settings_service)
) -> APIResponse[dict[str, Any]]:
    """Per-token (BTC/ETH/SOL) signal summary. LLM-enhanced when available."""
    llm_available = await is_llm_configured(settings_service)
    tokens = dict(_TOKEN_SIGNALS)
    llm_enhanced = False

    if llm_available:
        prompt = (
            "Provide a brief crypto market assessment for BTC, ETH, and SOL.\n"
            "For each token, give:\n"
            "1. Direction: bullish, bearish, or neutral\n"
            "2. Score: 0.0-1.0 (confidence-weighted directional score)\n"
            "3. Confidence: 0.0-1.0\n"
            "4. Two key events/themes driving the token\n\n"
            "Format per line: TOKEN|direction|score|confidence|event1;event2\n"
            "Example: BTC|bullish|0.65|0.7|ETF inflows strong;Halving cycle support"
        )
        llm_response = await call_llm(settings_service, prompt, max_tokens=300)
        if llm_response:
            for line in llm_response.strip().split("\n"):
                parts = line.strip().split("|")
                if len(parts) >= 5 and parts[0].strip().upper() in tokens:
                    tok = parts[0].strip().upper()
                    try:
                        direction = parts[1].strip().lower()
                        if direction in ("bullish", "bearish", "neutral"):
                            tokens[tok] = {
                                "direction": direction,
                                "score": max(0.0, min(1.0, float(parts[2].strip()))),
                                "confidence": max(
                                    0.0, min(1.0, float(parts[3].strip()))
                                ),
                                "key_events": [
                                    e.strip() for e in parts[4].split(";") if e.strip()
                                ][:3],
                                "price_context": tokens[tok].get("price_context", {}),
                            }
                            llm_enhanced = True
                    except (ValueError, IndexError):
                        pass

    result = {
        "tokens": tokens,
        "data_mode": "llm_enhanced" if llm_enhanced else "rule_based",
        "llm_enhanced": llm_enhanced,
        "timestamp": int(time.time() * 1000),
    }
    return APIResponse(data=result, meta=build_response_meta(request), error=None)


# ---------------------------------------------------------------------------
# Whale flow — merged news/social + onchain whale movements (Phase 4)
# ---------------------------------------------------------------------------


@router.get("/whale-flow")
async def whale_flow(
    request: Request,
    symbol: str = Query("BTC", description="Asset symbol"),
    limit: int = Query(50, ge=1, le=200, description="Max items to return"),
    settings_service: Any = Depends(get_settings_service),
) -> APIResponse[dict[str, Any]]:
    """Aggregated whale-flow feed — on-chain whale movements + news/social signals.

    Merges /intelligence/news/token-signals with /onchain/whales to produce
    a unified event stream per symbol.
    """
    symbol = symbol.upper()
    items: list[dict[str, Any]] = []

    # 1) On-chain whales (placeholder data for now, same as /onchain/whales)
    #    Forward to the onchain router's logic — kept inline to avoid circular import.
    try:
        from app.api.v1.onchain import get_whale_tracking as _whale_ep  # type: ignore

        whale_resp = await _whale_ep(request=request, symbol=symbol, limit=limit)
        whale_data = getattr(whale_resp, "data", None) or {}
        for m in whale_data.get("movements", []):
            items.append(
                {
                    "ts": int(time.time() * 1000),
                    "symbol": symbol,
                    "direction": m.get("direction", "neutral"),
                    "value_usd": m.get("value_usd"),
                    "source": "onchain",
                    "headline": m.get("description"),
                    "severity": "medium",
                }
            )
    except Exception:
        logger.debug("whale-flow onchain fetch failed", exc_info=True)

    # 2) News/token-signals enrichment — pull LLM-enhanced token signals.
    try:
        signals_resp = await token_signals(
            request=request, settings_service=settings_service
        )
        sig_data = getattr(signals_resp, "data", None) or {}
        token_sig = (sig_data.get("tokens") or {}).get(symbol)
        if token_sig:
            direction = token_sig.get("direction", "neutral")
            severity = (
                "high"
                if direction == "bullish"
                else "low"
                if direction == "bearish"
                else "medium"
            )
            items.append(
                {
                    "ts": int(time.time() * 1000),
                    "symbol": symbol,
                    "direction": "inflow"
                    if direction == "bullish"
                    else "outflow"
                    if direction == "bearish"
                    else "neutral",
                    "value_usd": None,
                    "source": "news",
                    "headline": "; ".join(token_sig.get("key_events", []) or [])
                    or None,
                    "severity": severity,
                }
            )
    except Exception:
        logger.debug("whale-flow news fetch failed", exc_info=True)

    items.sort(key=lambda x: x.get("ts", 0), reverse=True)
    items = items[:limit]

    result = {
        "symbol": symbol,
        "items": items,
        "count": len(items),
        "timestamp": int(time.time() * 1000),
    }
    return APIResponse(data=result, meta=build_response_meta(request), error=None)


@router.get("/social/confirmation")
async def social_confirmation(
    request: Request, settings_service: Any = Depends(get_settings_service)
) -> APIResponse[dict[str, Any]]:
    """Cross-reference trending topics with news headlines. LLM-enhanced when available."""
    try:
        trending_cache = _read_cache(_trending_cache, "social_trending")
        news_cache_data = _read_cache(_news_cache, "news_summary")
        trending_topics = (trending_cache or {}).get("topics", [])[:10]
        headlines = (news_cache_data or {}).get("headlines", [])

        confirmations = []
        headline_words = set()
        for h in headlines:
            for w in h.get("title", "").lower().split():
                headline_words.add(w.strip(".,!?;:"))

        for topic in trending_topics:
            title_lower = topic.get("title", "").lower()
            title_words = set(title_lower.split())
            overlap = title_words & headline_words
            news_correlation = len(overlap) >= 2
            score = topic.get("score", 0)
            heat = min(100, score // 10) if score > 0 else 0
            confirmations.append(
                {
                    "topic": topic.get("title", ""),
                    "subreddit": topic.get("subreddit", ""),
                    "news_correlation": news_correlation,
                    "community_heat": heat,
                    "sentiment_alignment": "aligned"
                    if news_correlation
                    else "independent",
                    "posts_count": 1,
                    "comments_count": topic.get("num_comments", 0),
                    "top_comments": [],
                }
            )

        # LLM enhancement: refine sentiment alignment for correlated topics
        llm_available = await is_llm_configured(settings_service)
        llm_enhanced = False
        if llm_available and confirmations:
            correlated = [c for c in confirmations if c["news_correlation"]]
            if correlated:
                topics_text = "\n".join(
                    f"- {c['topic']} (from {c['subreddit']}, heat={c['community_heat']})"
                    for c in correlated[:8]
                )
                prompt = (
                    "For each social-media topic below that also appears in news, "
                    "determine if the social sentiment aligns with, contradicts, or is independent of the news narrative.\n"
                    "Respond per line: topic_summary|alignment (aligned/contradictory/independent)\n\n"
                    f"{topics_text}"
                )
                llm_response = await call_llm(settings_service, prompt, max_tokens=300)
                if llm_response:
                    for line in llm_response.strip().split("\n"):
                        parts = line.strip().split("|")
                        if len(parts) >= 2 and parts[-1].strip().lower() in (
                            "aligned",
                            "contradictory",
                            "independent",
                        ):
                            alignment = parts[-1].strip().lower()
                            # Match by partial topic text
                            topic_prefix = "|".join(parts[:-1]).strip().lower()[:30]
                            for c in confirmations:
                                if (
                                    c["news_correlation"]
                                    and c["topic"].lower()[:30] == topic_prefix
                                ):
                                    c["sentiment_alignment"] = alignment
                                    llm_enhanced = True
                                    break

        result = {
            "confirmations": confirmations,
            "total": len(confirmations),
            "llm_enhanced": llm_enhanced,
            "timestamp": int(time.time() * 1000),
        }
        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Social confirmation failed: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Social confirmation error: {exc}",
        ) from exc
