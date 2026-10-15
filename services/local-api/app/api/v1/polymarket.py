"""Polymarket prediction market endpoints.

Provides enriched market data from Polymarket Gamma + CLOB APIs:
- Event listing with volume sorting and category detection
- Market details with Chinese translations
- Orderbook depth
- Price queries
- Crypto Up/Down rolling prediction markets (5m/15m/1h/daily)
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import build_response_meta, get_settings_service
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/polymarket", tags=["polymarket"])

# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------

_poly_client = None


async def _get_client():
    global _poly_client
    if _poly_client is None:
        from selene_space_exchange.exchanges.polymarket.client import PolymarketClient

        _poly_client = PolymarketClient()
    return _poly_client


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

# 每个分类的关键词(词边界, 大小写不敏感)。给同一分类需求, 词表只有一种写法,
# 故按 category → 关键词 组织, 编译放到 _CATEGORY_RULES。
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "crypto": [
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "solana",
        "crypto",
        "defi",
        "nft",
        "web3",
        "blockchain",
        "altcoin",
        "memecoin",
        "doge",
        "xrp",
        "cardano",
        "microstrategy",
        r"\bbackpack\s+fdv\b",
        "airdrop",
        r"\bpump\.fun\b",
        "hyperliquid",
    ],
    "finance": [
        "crude oil",
        "gold",
        "silver",
        "s&p",
        "spx",
        "nasdaq",
        "dow",
        "fed decision",
        r"\bfed\s",
        "rate cut",
        "rate hike",
        "interest rate",
        "cpi",
        "inflation",
        "gdp",
        "treasury",
        "ipo",
    ],
    "geopolitics": [
        "iran",
        "israel",
        "ukraine",
        "russia",
        "taiwan",
        "north korea",
        "syria",
        "gaza",
        "hamas",
        "hezbollah",
        "military",
        "invasion",
        "ceasefire",
        "offensive",
        "nato",
        "sanctions",
    ],
    "politics": [
        "election",
        "president",
        "democrat",
        "republican",
        "vote",
        "congress",
        "trump",
        "biden",
        "parliament",
        "senate",
        "nominee",
        "governor",
        "prime minister",
        "tariff",
        "speaker",
    ],
    "sports": [
        "nba",
        "nfl",
        "mlb",
        "nhl",
        "fifa",
        "f1",
        "ufc",
        "atp",
        "ncaa",
        "premier league",
        "la liga",
        "champions league",
        "world cup",
        "super bowl",
        "stanley cup",
        "playoff",
        r"\bvs\.?\b",
    ],
    "entertainment": [
        "eurovision",
        "oscar",
        "grammy",
        "emmy",
        "movie",
        "gta",
        "taylor swift",
    ],
    "tech": ["elon musk", "openai", "tesla", "spacex", "tweet"],
    "science": ["climate", "earthquake", "hurricane", "nasa"],
}


def _compile_category_rules() -> list[tuple[str, list[re.Pattern[str]]]]:
    """把词表编成 (分类, [正则])。

    纯关键词自动加词边界 ``\\b…\\b``; 已自带 ``\\b`` 开头的(如 fed 子串)按原样编译,
    以保留它们各自的开/闭边界语义。
    """
    rules: list[tuple[str, list[re.Pattern[str]]]] = []
    for cat, kws in _CATEGORY_KEYWORDS.items():
        patterns = [
            re.compile(kw if kw.startswith(r"\b") else rf"\b{kw}\b", re.I) for kw in kws
        ]
        rules.append((cat, patterns))
    return rules


_CATEGORY_RULES = _compile_category_rules()


def _detect_category(title: str, description: str = "") -> str:
    """Categorize an event based primarily on its title using regex word boundaries."""
    scores: dict[str, int] = {}
    for cat, patterns in _CATEGORY_RULES:
        score = sum(3 for p in patterns if p.search(title))
        if description:
            score += sum(1 for p in patterns if p.search(description))
        if score > 0:
            scores[cat] = score
    if not scores:
        return "other"
    return max(scores, key=scores.get)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Chinese translation helpers
# ---------------------------------------------------------------------------

# 术语 → 中文替换表。**顺序即优先**: 短语在前, 单词在后(先长后短)。给定
# 同一套译名, 表只有一种写法; 编译放到 _ZH_TERM_MAP。
_ZH_TERM_PAIRS: list[tuple[str, str]] = [
    # Multi-word phrases first (longer matches take priority)
    (r"\bPresidential Election Winner\b", "总统大选获胜者"),
    (r"\bPresidential Election\b", "总统大选"),
    (r"\bPresidential Nominee\b", "总统提名人"),
    (r"\bParliamentary Election\b", "议会选举"),
    (r"\bFed decision\b", "美联储决议"),
    (r"\brate cut\b", "降息"),
    (r"\brate hike\b", "加息"),
    (r"\bground offensive\b", "地面进攻"),
    (r"\bFIFA World Cup\b", "FIFA世界杯"),
    (r"\bWorld Cup\b", "世界杯"),
    (r"\bSuper Bowl\b", "超级碗"),
    (r"\bMarch Madness\b", "疯狂三月"),
    (r"\bCrude Oil\b", "原油"),
    (r"\bUS forces\b", "美军"),
    (r"\bby end of\b", "截止"),
    # Crypto
    (r"\bBitcoin\b", "比特币"),
    (r"\bEthereum\b", "以太坊"),
    (r"\bSolana\b", "Solana"),
    (r"\bMicroStrategy\b", "MicroStrategy"),
    # Sports
    (r"\bNBA Champion\b", "NBA 冠军"),
    (r"\bF1 Drivers' Champion\b", "F1 车手冠军"),
    (r"\bEurovision Winner\b", "欧洲歌唱大赛冠军"),
    (r"\bPlayoff\b", "季后赛"),
    # Politics / geopolitics
    (r"\bDemocratic\b", "民主党"),
    (r"\bRepublican\b", "共和党"),
    (r"\bceasefire\b", "停火"),
    (r"\bWinner\b", "获胜者"),
    (r"\bNominee\b", "提名人"),
    (r"\bChampion\b", "冠军"),
    (r"\bElection\b", "选举"),
    # Months
    (r"\bJanuary\b", "一月"),
    (r"\bFebruary\b", "二月"),
    (r"\bMarch\b(?!\s+Madness)", "三月"),
    (r"\bApril\b", "四月"),
    (r"\bJune\b", "六月"),
    (r"\bJuly\b", "七月"),
    (r"\bAugust\b", "八月"),
    (r"\bSeptember\b", "九月"),
    (r"\bOctober\b", "十月"),
    (r"\bNovember\b", "十一月"),
    (r"\bDecember\b", "十二月"),
]

_ZH_TERM_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(pat, re.I), repl) for pat, repl in _ZH_TERM_PAIRS
]

_ZH_CATEGORY_MAP = {
    "crypto": "加密货币",
    "politics": "政治",
    "sports": "体育",
    "finance": "金融",
    "entertainment": "娱乐",
    "geopolitics": "地缘政治",
    "tech": "科技",
    "science": "科学",
    "other": "其他",
}


def _translate_title(title: str) -> str:
    result = title
    for pattern, replacement in _ZH_TERM_MAP:
        result = pattern.sub(replacement, result)
    return result


# ---------------------------------------------------------------------------
# Outcome extraction from Gamma market objects
# ---------------------------------------------------------------------------


def _as_json_list(value: Any) -> Any:
    """Gamma 把数组字段存成 JSON 字符串; 是字符串就解析, 解析失败给空表。"""
    import json as _json

    if not isinstance(value, str):
        return value
    if not value:
        return []
    try:
        return _json.loads(value)
    except Exception:
        return []


def _outcomes_from_clob(m: dict[str, Any]) -> list[dict[str, Any]]:
    """新字段: clobTokenIds × outcomes × outcomePrices 三列对齐成期权。"""
    clob_ids = _as_json_list(m.get("clobTokenIds", ""))
    names = _as_json_list(m.get("outcomes", ""))
    prices = _as_json_list(m.get("outcomePrices", ""))
    if not (isinstance(clob_ids, list) and isinstance(names, list)):
        return []
    price_list = prices if isinstance(prices, list) else []
    return [
        {
            "token_id": str(tid),
            "outcome": str(names[i]) if i < len(names) else f"Outcome {i}",
            "price": float(price_list[i]) if i < len(price_list) else 0.0,
        }
        for i, tid in enumerate(clob_ids)
    ]


def _outcomes_from_tokens(m: dict[str, Any]) -> list[dict[str, Any]]:
    """旧字段回退: tokens[] 里每项直接给 token_id/outcome/price。"""
    tokens = _as_json_list(m.get("tokens", []))
    if not isinstance(tokens, list):
        return []
    return [
        {
            "token_id": t.get("token_id", ""),
            "outcome": t.get("outcome", ""),
            "price": float(t.get("price", 0) or 0),
        }
        for t in tokens
        if isinstance(t, dict)
    ]


def _extract_outcomes_from_market(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract outcomes from a Gamma market object.

    Gamma markets store token IDs in ``clobTokenIds`` (JSON-encoded list),
    outcome labels in ``outcomes`` (JSON-encoded list), and prices in
    ``outcomePrices`` (JSON-encoded list).  The older ``tokens`` field may
    be empty for short-lived prediction markets.
    """
    from_clob = _outcomes_from_clob(m)
    return from_clob if from_clob else _outcomes_from_tokens(m)


# ---------------------------------------------------------------------------
# Enrich event data
# ---------------------------------------------------------------------------


def _enrich_market(m: dict[str, Any]) -> dict[str, Any]:
    """单个 Gamma 市场 → 前端条目(期权补 winner 默认值)。"""
    outcomes = _extract_outcomes_from_market(m)
    for o in outcomes:
        o.setdefault("winner", False)
    question = m.get("question", "")
    return {
        "id": m.get("id", ""),
        "question": question,
        "question_zh": _translate_title(question),
        "condition_id": m.get("conditionId", m.get("condition_id", "")),
        "slug": m.get("slug", ""),
        "active": m.get("active", True),
        "closed": m.get("closed", False),
        "volume": float(m.get("volume", 0) or 0),
        "liquidity": float(m.get("liquidity", 0) or 0),
        "outcomes": outcomes,
    }


def _enrich_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Add category, Chinese translation, and structured market data."""
    title = raw.get("title", "")
    desc = raw.get("description", "")
    category = raw.get("category", "").lower() or _detect_category(title, desc)

    markets_enriched = [_enrich_market(m) for m in raw.get("markets", [])]

    return {
        "id": raw.get("id", ""),
        "title": title,
        "title_zh": _translate_title(title),
        "slug": raw.get("slug", ""),
        "description": desc,
        "category": category,
        "category_zh": _ZH_CATEGORY_MAP.get(category, category),
        "image": raw.get("image", ""),
        "icon": raw.get("icon", ""),
        "active": raw.get("active", True),
        "closed": raw.get("closed", False),
        "volume": float(raw.get("volume", 0) or 0),
        "volume_24h": float(raw.get("volume24hr", 0) or 0),
        "liquidity": float(raw.get("liquidity", 0) or 0),
        "start_date": raw.get("startDate", ""),
        "end_date": raw.get("endDate", ""),
        "markets": markets_enriched,
        "market_count": len(markets_enriched),
    }


# ---------------------------------------------------------------------------
# Events (primary endpoint — Gamma API with enrichment)
# ---------------------------------------------------------------------------


@router.get("/events")
async def list_events(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="Number of events"),
    active: bool = Query(True, description="Only active events"),
    closed: bool = Query(False, description="Include closed events"),
    category: str = Query("", description="Filter by category"),
    sort: str = Query(
        "volume24hr", description="Sort field: volume24hr, volume, liquidity"
    ),
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """List enriched Polymarket events with categories and Chinese translations.

    Sorted by 24h volume (descending) by default to show the hottest events first.
    """
    try:
        fetch_limit = limit * 3 if category else limit
        events_raw = await client.list_events(
            limit=min(fetch_limit, 100),
            active=active,
            closed=closed,
            order=sort,
            ascending=False,
        )
        enriched = [_enrich_event(e) for e in events_raw]

        if category:
            cat_lower = category.lower()
            enriched = [e for e in enriched if e["category"] == cat_lower]
            enriched = enriched[:limit]

        return APIResponse(
            data={
                "events": enriched,
                "count": len(enriched),
                "categories": list(_ZH_CATEGORY_MAP.keys()),
                "categories_zh": _ZH_CATEGORY_MAP,
            },
            meta=build_response_meta(request),
            error=None,
        )
    except Exception as exc:
        logger.error("Failed to fetch Polymarket events: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Polymarket API error: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Event detail
# ---------------------------------------------------------------------------


@router.get("/events/{event_id}")
async def get_event(
    event_id: str,
    request: Request,
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """Get full details for a single Polymarket event, including all sub-markets."""
    try:
        raw = await client.get_event(event_id)
        enriched = _enrich_event(raw)

        enriched["description_zh"] = _translate_title(raw.get("description", ""))
        enriched["comment_count"] = raw.get("commentCount", 0)
        enriched["created_at"] = raw.get("createdAt", "")
        enriched["updated_at"] = raw.get("updatedAt", "")
        enriched["competitive"] = raw.get("competitive", 0)
        enriched["enableOrderBook"] = raw.get("enableOrderBook", True)
        enriched["neg_risk"] = raw.get("negRisk", False)

        for m in enriched["markets"]:
            raw_m_list = raw.get("markets", [])
            raw_m: dict[str, Any] = next(
                (
                    rm
                    for rm in raw_m_list
                    if rm.get("id", "") == m["id"]
                    or rm.get("conditionId", "") == m["condition_id"]
                ),
                {},
            )
            m["description"] = raw_m.get("description", "")
            m["description_zh"] = _translate_title(raw_m.get("description", ""))
            m["end_date"] = raw_m.get("endDate", "")
            m["start_date"] = raw_m.get("startDate", "")
            m["neg_risk"] = raw_m.get("negRisk", False)
            m["best_bid"] = float(raw_m.get("bestBid", 0) or 0)
            m["best_ask"] = float(raw_m.get("bestAsk", 0) or 0)
            m["spread"] = float(raw_m.get("spread", 0) or 0)
            m["last_trade_price"] = float(raw_m.get("lastTradePrice", 0) or 0)
            m["volume_24h"] = float(raw_m.get("volume24hr", 0) or 0)

        return APIResponse(data=enriched, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Failed to fetch event %s: %s", event_id, exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Polymarket API error: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Crypto price predictions (BTC/ETH Up/Down rolling markets)
# ---------------------------------------------------------------------------

_UP_OR_DOWN_RE = re.compile(r"Up or Down", re.I)

_ASSET_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    ("BTC", "比特币", re.compile(r"\bBitcoin\b", re.I)),
    ("ETH", "以太坊", re.compile(r"\bEthereum\b", re.I)),
    ("SOL", "Solana", re.compile(r"\bSolana\b", re.I)),
    ("DOGE", "狗狗币", re.compile(r"\bDogecoin\b", re.I)),
    ("XRP", "XRP", re.compile(r"\bXRP\b", re.I)),
    ("BNB", "BNB", re.compile(r"\bBNB\b", re.I)),
    ("HYPE", "Hyperliquid", re.compile(r"\b(?:Hyperliquid|HYPE)\b", re.I)),
]

_TIMEFRAME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("5m", re.compile(r"updown[_-]5m[_-]", re.I)),
    ("15m", re.compile(r"updown[_-]15m[_-]", re.I)),
    ("1h", re.compile(r"\d{1,2}(?:AM|PM)\s+ET$", re.I)),
    ("daily", re.compile(r"Up or Down (?:on|–) ", re.I)),
]


def _first_match(patterns: list, text: str) -> Any:
    """First pattern (in table order) that hits ``text``, else ``None``."""
    return next((entry for entry in patterns if entry[-1].search(text)), None)


def _detect_asset(title: str) -> tuple[str, str]:
    hit = _first_match(_ASSET_PATTERNS, title)
    return (hit[0], hit[1]) if hit else ("OTHER", "其他")


def _detect_timeframe(slug: str, title: str) -> str:
    for tf, pat in _TIMEFRAME_PATTERNS:
        if pat.search(slug) or pat.search(title):
            return tf
    return "other"


_TIMEFRAME_LABELS = {
    "5m": {"en": "5 Minutes", "zh": "5分钟"},
    "15m": {"en": "15 Minutes", "zh": "15分钟"},
    "1h": {"en": "Hourly", "zh": "1小时"},
    "daily": {"en": "Daily", "zh": "日级别"},
    "other": {"en": "Other", "zh": "其他"},
}

_TIMEFRAME_SECONDS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "daily": 24 * 60 * 60,
}

_SLUG_TS_RE = re.compile(r"-(\d{10})$")


def _parse_iso_date(s: str) -> datetime | None:
    """Best-effort ISO date parsing."""
    if not s:
        return None
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _extract_window_from_slug(
    slug: str, tf: str
) -> tuple[datetime | None, datetime | None]:
    """Extract the real prediction window start/end from the slug timestamp.

    Gamma API ``startDate`` is the *market creation* time, NOT the prediction
    window start.  The actual window start is the Unix epoch embedded at the
    end of the slug (e.g. ``btc-updown-5m-1774510200``).
    """
    found = _SLUG_TS_RE.search(slug)
    if not found:
        return None, None

    start = datetime.fromtimestamp(int(found.group(1)), tz=UTC)
    seconds = _TIMEFRAME_SECONDS.get(tf)
    return start, (start + timedelta(seconds=seconds) if seconds else None)


def _event_dates(ev: dict[str, Any], markets: list[Any]) -> tuple[str, str]:
    """起止日期: 事件自身优先, 缺则取首个市场的(市场创建时间)。"""
    start = ev.get("startDate", "")
    end = ev.get("endDate", "")
    if markets:
        end = end or markets[0].get("endDate", "")
        start = start or markets[0].get("startDate", "")
    return start, end


def _outcomes_of_markets(markets: list[Any]) -> list[dict[str, Any]]:
    """把所有市场的期权拼平。"""
    outcomes: list[dict[str, Any]] = []
    for m in markets:
        outcomes.extend(_extract_outcomes_from_market(m))
    return outcomes


def _prediction_card(
    ev: dict[str, Any],
    *,
    title: str,
    asset: str,
    asset_zh: str,
    slug: str,
    tf: str,
    start_date_str: str,
    end_date_str: str,
    window_start: Any,
    window_end: Any,
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    """拼出 Crypto 预测卡(Gamma 事件 → 前端条目)。"""
    return {
        "id": str(ev.get("id", "")),
        "title": title,
        "title_zh": _translate_title(title),
        "slug": slug,
        "asset": asset,
        "asset_zh": asset_zh,
        "timeframe": tf,
        "timeframe_label": _TIMEFRAME_LABELS.get(tf, _TIMEFRAME_LABELS["other"]),
        "active": ev.get("active", True),
        "closed": ev.get("closed", False),
        "start_date": start_date_str,
        "end_date": end_date_str,
        "_window_start": window_start,
        "_window_end": window_end or _parse_iso_date(end_date_str),
        "volume": float(ev.get("volume", 0) or 0),
        "volume_24h": float(ev.get("volume24hr", 0) or 0),
        "liquidity": float(ev.get("liquidity", 0) or 0),
        "outcomes": outcomes,
        "polymarket_url": f"https://polymarket.com/event/{slug}",
    }


def _event_to_prediction(ev: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a Gamma event to a prediction entry if it matches Up/Down."""
    title = ev.get("title", "")
    if not _UP_OR_DOWN_RE.search(title):
        return None

    asset, asset_zh = _detect_asset(title)
    slug = ev.get("slug", "")
    tf = _detect_timeframe(slug, title)

    markets_raw = ev.get("markets", [])
    start_date_str, end_date_str = _event_dates(ev, markets_raw)
    outcomes = _outcomes_of_markets(markets_raw)

    # The TRUE prediction window comes from the slug timestamp, not
    # Gamma's startDate (which is the market creation time).
    window_start, window_end = _extract_window_from_slug(slug, tf)

    return _prediction_card(
        ev,
        title=title,
        asset=asset,
        asset_zh=asset_zh,
        slug=slug,
        tf=tf,
        start_date_str=start_date_str,
        end_date_str=end_date_str,
        window_start=window_start,
        window_end=window_end,
        outcomes=outcomes,
    )


_ASSET_SLUGS = ["btc", "eth", "sol", "doge", "xrp", "bnb", "hype"]
_ROLLING_TFS = [("5m", 300), ("15m", 900)]


def _build_current_window_slugs(now: datetime) -> list[tuple[str, str, int]]:
    """Generate slugs for the currently active prediction windows.

    Returns ``(slug, tf_label, window_start_ts)`` tuples for each
    asset × timeframe combination.
    """
    now_ts = int(now.timestamp())
    slugs: list[tuple[str, str, int]] = []
    for asset_slug in _ASSET_SLUGS:
        for tf_label, duration in _ROLLING_TFS:
            window_start_ts = now_ts - (now_ts % duration)
            slug = f"{asset_slug}-updown-{tf_label}-{window_start_ts}"
            slugs.append((slug, tf_label, window_start_ts))
    return slugs


@router.get("/crypto-predictions")
async def list_crypto_predictions(
    request: Request,
    limit: int = Query(30, ge=1, le=100, description="Max predictions to return"),
    asset: str = Query("", description="Filter by asset: BTC, ETH, SOL, etc."),
    timeframe: str = Query("", description="Filter by timeframe: 5m, 15m, 1h, daily"),
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """Fetch crypto Up/Down rolling prediction markets.

    Only returns events whose **prediction window** is currently in progress.
    The real window start/end is derived from the slug's Unix timestamp
    (Gamma ``startDate`` is the market *creation* time, not the window start).

    Results are sorted by ``window_end`` ascending so the soonest-to-resolve
    (= currently active window) appears first.
    """
    try:
        import asyncio as _aio

        now = datetime.now(tz=UTC)
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        # --- Strategy 1: deterministic slug fetch for 5m/15m (fast & precise) ---
        current_slugs = _build_current_window_slugs(now)

        async def _fetch_one(slug: str) -> dict[str, Any] | None:
            result: dict[str, Any] | None = await client.get_event_by_slug(slug)
            return result

        # --- Strategy 2: end_date_min filter for 1h/daily (official Gamma param) ---
        async def _fetch_hourly() -> list[dict[str, Any]]:
            events: list[dict[str, Any]] = await client.list_events(
                limit=50,
                active=True,
                closed=False,
                order="endDate",
                ascending=True,
                end_date_min=now_iso,
            )
            return events

        slug_results, hourly_events = await _aio.gather(
            _aio.gather(
                *[_fetch_one(s) for s, _, _ in current_slugs], return_exceptions=True
            ),
            _fetch_hourly(),
            return_exceptions=False,
        )

        predictions: list[dict[str, Any]] = []

        # Process slug-based results (5m/15m)
        for (_slug, tf_label, ws_ts), result in zip(
            current_slugs, slug_results, strict=False
        ):
            if isinstance(result, Exception) or result is None:
                continue
            pred = _event_to_prediction(result)
            if pred is None:
                continue
            pred.pop("_window_start", None)
            pred.pop("_window_end", None)

            if asset and pred["asset"].upper() != asset.upper():
                continue
            if timeframe and pred["timeframe"] != timeframe.lower():
                continue

            window_end = datetime.fromtimestamp(
                ws_ts + _TIMEFRAME_SECONDS.get(tf_label, 300), tz=UTC
            )
            pred["_end_sort"] = window_end
            predictions.append(pred)

        # Process end_date_min results (1h/daily/other)
        seen_slugs = {s for s, _, _ in current_slugs}
        for ev in hourly_events:
            title = ev.get("title", "")
            if not _UP_OR_DOWN_RE.search(title):
                continue
            ev_slug = ev.get("slug", "")
            if ev_slug in seen_slugs:
                continue  # already covered by slug fetch

            pred = _event_to_prediction(ev)
            if pred is None:
                continue
            if pred["timeframe"] in ("5m", "15m"):
                # Already handled above; use slug timestamp for these
                window_start = pred.pop("_window_start", None)
                window_end = pred.pop("_window_end", None)
                if window_start and window_start > now:
                    continue
            else:
                window_start = pred.pop("_window_start", None)
                window_end = pred.pop("_window_end", None)
                if window_start and window_start > now:
                    continue

            if asset and pred["asset"].upper() != asset.upper():
                continue
            if timeframe and pred["timeframe"] != timeframe.lower():
                continue

            pred["_end_sort"] = (
                window_end or _parse_iso_date(ev.get("endDate", "")) or now
            )
            predictions.append(pred)

        predictions.sort(key=lambda p: p.pop("_end_sort"))

        # Deduplicate: keep at most 1 per (asset, timeframe) combination
        seen: set[tuple[str, str]] = set()
        deduped: list[dict[str, Any]] = []
        for p in predictions:
            key = (p["asset"], p["timeframe"])
            if key not in seen:
                seen.add(key)
                deduped.append(p)
        predictions = deduped[:limit]

        by_asset: dict[str, list[dict[str, Any]]] = {}
        for p in predictions:
            by_asset.setdefault(p["asset"], []).append(p)

        by_timeframe: dict[str, list[dict[str, Any]]] = {}
        for p in predictions:
            by_timeframe.setdefault(p["timeframe"], []).append(p)

        available_assets = sorted({p["asset"] for p in predictions})

        return APIResponse(
            data={
                "predictions": predictions,
                "by_asset": by_asset,
                "by_timeframe": by_timeframe,
                "count": len(predictions),
                "available_assets": available_assets,
                "timeframe_labels": _TIMEFRAME_LABELS,
            },
            meta=build_response_meta(request),
            error=None,
        )
    except Exception as exc:
        logger.error("Failed to fetch crypto predictions: %s", exc, exc_info=True)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Polymarket API error: {type(exc).__name__}: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Markets (CLOB)
# ---------------------------------------------------------------------------


@router.get("/markets")
async def list_markets(
    request: Request,
    limit: int = Query(20, ge=1, le=100, description="Number of markets"),
    next_cursor: str = Query("", description="Pagination cursor"),
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """List active Polymarket prediction markets from CLOB."""
    try:
        markets = await client.list_markets(limit=limit, next_cursor=next_cursor)
        result = []
        for m in markets:
            d = m.model_dump()
            d["category"] = _detect_category(m.question, m.description)
            d["category_zh"] = _ZH_CATEGORY_MAP.get(d["category"], d["category"])
            d["question_zh"] = _translate_title(m.question)
            result.append(d)
        return APIResponse(
            data={"markets": result, "count": len(result)},
            meta=build_response_meta(request),
            error=None,
        )
    except Exception as exc:
        logger.error("Failed to fetch Polymarket markets: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Polymarket API error: {exc}",
        ) from exc


@router.get("/markets/{condition_id}")
async def get_market(
    condition_id: str,
    request: Request,
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """Get details for a specific market by condition ID."""
    try:
        from selene_space_exchange.exchanges.polymarket.redemption import (
            PolymarketRedemptionClient,
        )

        redemption = PolymarketRedemptionClient(wallet_address="")
        try:
            market = await redemption.get_market(condition_id)
            d = market.model_dump()
            d["category"] = _detect_category(market.question, market.description)
            d["category_zh"] = _ZH_CATEGORY_MAP.get(d["category"], d["category"])
            d["question_zh"] = _translate_title(market.question)
            return APIResponse(data=d, meta=build_response_meta(request), error=None)
        finally:
            await redemption.close()
    except Exception as exc:
        logger.error("Failed to fetch market %s: %s", condition_id, exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Polymarket API error: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Orderbook
# ---------------------------------------------------------------------------


@router.get("/orderbook/{token_id}")
async def get_orderbook(
    token_id: str,
    request: Request,
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """Get orderbook for a Polymarket outcome token."""
    try:
        book = await client.get_orderbook(token_id)
        return APIResponse(
            data=book.model_dump(),
            meta=build_response_meta(request),
            error=None,
        )
    except Exception as exc:
        logger.error("Failed to fetch orderbook for %s: %s", token_id, exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Polymarket API error: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------


@router.get("/price/{token_id}")
async def get_price(
    token_id: str,
    request: Request,
    side: str = Query("BUY", description="BUY or SELL"),
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """Get market price for a Polymarket outcome token."""
    try:
        price = await client.get_price(token_id, side=side.upper())
        midpoint = await client.get_midpoint(token_id)
        return APIResponse(
            data={
                "token_id": token_id,
                "price": price,
                "midpoint": midpoint,
                "side": side.upper(),
            },
            meta=build_response_meta(request),
            error=None,
        )
    except Exception as exc:
        logger.error("Failed to fetch price for %s: %s", token_id, exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Polymarket API error: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Phase 3: Polymarket Address Analysis
# ---------------------------------------------------------------------------

# Module-level stores
_address_cache: dict[str, dict] = {}
_watchlist: dict[str, dict] = {}  # address -> {label, note, tags, source, added_at}
_alert_rules: dict[
    str, dict
] = {}  # rule_id -> {kind, target, address, market_keyword, min_notional, price_min, price_max, enabled, created_at}
_alert_matches: list[dict] = []


# ---------------------------------------------------------------------------
# Request / response bodies
# ---------------------------------------------------------------------------


class AnalyzeAddressesRequest(BaseModel):
    """Body for POST /polymarket/addresses/analyze."""

    addresses: list[str] = Field(
        ..., min_length=1, description="Wallet addresses to analyze"
    )
    window_days: int = Field(30, ge=1, le=365, description="Lookback window in days")
    min_settled_markets: int = Field(
        5, ge=0, description="Minimum settled markets to include"
    )
    category: str | None = Field(None, description="Filter by market category")
    state: str | None = Field(None, description="Filter by market state")


class CompareAddressesRequest(BaseModel):
    """Body for POST /polymarket/addresses/compare."""

    addresses: list[str] = Field(
        ..., min_length=2, description="Wallet addresses to compare"
    )
    window_days: int = Field(30, ge=1, le=365, description="Lookback window in days")
    min_settled_markets: int = Field(
        5, ge=0, description="Minimum settled markets to include"
    )
    mode: str = Field("full", description="Comparison mode: full")


class PrefetchAddressesRequest(BaseModel):
    """Body for POST /polymarket/addresses/prefetch."""

    addresses: list[str] = Field(
        ..., min_length=1, description="Wallet addresses to prefetch"
    )
    window_days: int = Field(30, ge=1, le=365, description="Lookback window in days")
    min_settled_markets: int = Field(
        5, ge=0, description="Minimum settled markets to include"
    )
    mode: str = Field("fast", description="Prefetch mode: fast")


class WatchlistAddRequest(BaseModel):
    """Body for POST /polymarket/addresses/watchlist."""

    addresses: list[str] = Field(
        ..., min_length=1, description="Addresses to add to watchlist"
    )
    label: str | None = Field(None, description="Optional label for the group")
    note: str | None = Field(None, description="Optional note")
    tags: list[str] = Field(default_factory=list, description="Optional tags")
    source: str = Field("manual", description="Source of the watchlist entry")


class AlertRuleCreateRequest(BaseModel):
    """Body for POST /polymarket/addresses/alerts."""

    kind: str = Field(..., description="Alert kind: trade, position, price")
    target: str | None = Field(None, description="Alert target")
    address: str | None = Field(None, description="Address to monitor")
    market_keyword: str | None = Field(None, description="Market keyword filter")
    min_notional: float = Field(0, ge=0, description="Minimum notional value")
    price_min: float | None = Field(None, description="Minimum price threshold")
    price_max: float | None = Field(None, description="Maximum price threshold")
    enabled: bool = Field(True, description="Whether the rule is active")


class AlertRulePatchRequest(BaseModel):
    """Body for PATCH /polymarket/addresses/alerts/{rule_id}."""

    kind: str | None = None
    target: str | None = None
    address: str | None = None
    market_keyword: str | None = None
    min_notional: float | None = Field(None, ge=0)
    price_min: float | None = None
    price_max: float | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _ensure_analyzed(address: str, window_days: int = 30) -> dict[str, Any]:
    """Analyze a single address if not already cached."""
    cache_key = f"{address}:{window_days}"
    if cache_key in _address_cache:
        return _address_cache[cache_key]

    from app.engines.address_analyzer import analyze_addresses

    settings_service = get_settings_service()
    results = await analyze_addresses(
        settings_service, [address], window_days=window_days
    )
    result = (
        results[0]
        if results
        else {
            "address": address,
            "classification": "unknown",
            "win_rate": 0,
            "roi": 0,
            "trade_count": 0,
            "total_volume": 0,
            "trading_style": None,
            "llm_enhanced": False,
        }
    )
    _address_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/addresses/analyze")
async def analyze_addresses_endpoint(
    request: Request,
    body: AnalyzeAddressesRequest,
) -> APIResponse[dict[str, Any]]:
    """Analyze a batch of Polymarket wallet addresses.

    Computes metrics (win_rate, roi, trade_count, total_volume) and
    classification (elite/proficient/retail/unknown) for each address.
    LLM enrichment generates a trading_style narrative when available.
    """
    from app.engines.address_analyzer import analyze_addresses

    settings_service = get_settings_service()
    results = await analyze_addresses(
        settings_service,
        body.addresses,
        window_days=body.window_days,
    )

    # Cache results
    for r in results:
        cache_key = f"{r['address']}:{body.window_days}"
        _address_cache[cache_key] = r

    # Apply optional filters
    filtered = results
    if body.min_settled_markets > 0:
        filtered = [
            r for r in filtered if r.get("trade_count", 0) >= body.min_settled_markets
        ]

    llm_enhanced = any(r.get("llm_enhanced", False) for r in filtered)

    return APIResponse(
        data={"results": filtered, "llm_enhanced": llm_enhanced},
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/addresses/compare")
async def compare_addresses(
    request: Request,
    body: CompareAddressesRequest,
) -> APIResponse[dict[str, Any]]:
    """Compare multiple Polymarket addresses side by side.

    Analyzes each address and computes comparative metrics including
    relative rankings and aggregate statistics.
    """
    from app.engines.address_analyzer import analyze_addresses

    settings_service = get_settings_service()
    results = await analyze_addresses(
        settings_service,
        body.addresses,
        window_days=body.window_days,
    )

    # Cache results
    for r in results:
        cache_key = f"{r['address']}:{body.window_days}"
        _address_cache[cache_key] = r

    # Apply min_settled_markets filter
    filtered = results
    if body.min_settled_markets > 0:
        filtered = [
            r for r in filtered if r.get("trade_count", 0) >= body.min_settled_markets
        ]

    # Compute comparative metrics
    if filtered:
        win_rates = [r["win_rate"] for r in filtered]
        rois = [r["roi"] for r in filtered]
        volumes = [r["total_volume"] for r in filtered]
        comparison = {
            "address_count": len(filtered),
            "avg_win_rate": round(sum(win_rates) / len(win_rates), 4),
            "avg_roi": round(sum(rois) / len(rois), 4),
            "avg_volume": round(sum(volumes) / len(volumes), 2),
            "best_win_rate": max(win_rates),
            "worst_win_rate": min(win_rates),
            "best_roi": max(rois),
            "worst_roi": min(rois),
            "rankings": {
                "by_win_rate": sorted(
                    [
                        {"address": r["address"], "win_rate": r["win_rate"]}
                        for r in filtered
                    ],
                    key=lambda x: x["win_rate"],
                    reverse=True,
                ),
                "by_roi": sorted(
                    [{"address": r["address"], "roi": r["roi"]} for r in filtered],
                    key=lambda x: x["roi"],
                    reverse=True,
                ),
            },
        }
    else:
        comparison = {"address_count": 0, "rankings": {}}

    return APIResponse(
        data={"addresses": filtered, "comparison": comparison},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/addresses/screen")
async def screen_addresses(
    request: Request,
    classification: str = Query(
        "", description="Filter by classification: elite, proficient, retail, unknown"
    ),
    window_days: int = Query(30, ge=1, le=365, description="Lookback window"),
    min_score: float = Query(0, ge=0, le=100, description="Minimum win_rate * 100"),
    min_settled_markets: int = Query(0, ge=0, description="Minimum trade count"),
    category: str = Query("", description="Filter by market category"),
    state: str = Query("", description="Filter by state"),
    limit: int = Query(50, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> APIResponse[dict[str, Any]]:
    """Screen cached addresses by classification and metrics.

    Returns addresses from the in-memory cache filtered by the given criteria.
    Addresses must first be analyzed via /addresses/analyze or /addresses/prefetch.
    """
    entries = list(_address_cache.values())

    if classification:
        entries = [e for e in entries if e.get("classification") == classification]
    if min_score > 0:
        entries = [e for e in entries if e.get("win_rate", 0) * 100 >= min_score]
    if min_settled_markets > 0:
        entries = [e for e in entries if e.get("trade_count", 0) >= min_settled_markets]

    total = len(entries)
    page = entries[offset : offset + limit]

    return APIResponse(
        data={"addresses": page, "total": total},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/addresses/smart-money")
async def get_smart_money(
    request: Request,
    classification: str = Query("elite", description="Classification filter"),
    window_days: int = Query(30, ge=1, le=365, description="Lookback window"),
    min_score: float = Query(60, ge=0, le=100, description="Minimum win_rate * 100"),
    min_settled_markets: int = Query(5, ge=0, description="Minimum trade count"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> APIResponse[dict[str, Any]]:
    """Get top smart-money addresses filtered by classification.

    Returns the highest-performing addresses from the cache, sorted by
    win_rate descending. Defaults to elite classification.
    """
    entries = list(_address_cache.values())

    if classification:
        entries = [e for e in entries if e.get("classification") == classification]
    if min_score > 0:
        entries = [e for e in entries if e.get("win_rate", 0) * 100 >= min_score]
    if min_settled_markets > 0:
        entries = [e for e in entries if e.get("trade_count", 0) >= min_settled_markets]

    # Sort by win_rate descending
    entries.sort(key=lambda e: e.get("win_rate", 0), reverse=True)

    total = len(entries)
    page = entries[offset : offset + limit]

    return APIResponse(
        data={"addresses": page, "total": total},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/addresses/pipeline")
async def get_address_pipeline(
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """Get the address analysis pipeline status and available stages."""
    return APIResponse(
        data={
            "status": "available",
            "stages": ["fetch", "score", "classify", "enrich"],
        },
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/addresses/prefetch")
async def prefetch_addresses(
    request: Request,
    body: PrefetchAddressesRequest,
) -> APIResponse[dict[str, Any]]:
    """Pre-populate the address cache by analyzing addresses in batch.

    Useful for warming the cache before screening or smart-money queries.
    """
    from app.engines.address_analyzer import analyze_addresses

    settings_service = get_settings_service()
    results = await analyze_addresses(
        settings_service,
        body.addresses,
        window_days=body.window_days,
    )

    prefetched = 0
    for r in results:
        if (
            body.min_settled_markets > 0
            and r.get("trade_count", 0) < body.min_settled_markets
        ):
            continue
        cache_key = f"{r['address']}:{body.window_days}"
        _address_cache[cache_key] = r
        prefetched += 1

    return APIResponse(
        data={"prefetched": prefetched},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/addresses/watchlist")
async def get_watchlist(
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """Get all addresses on-chain address watchlist entries."""
    entries = list(_watchlist.values())
    return APIResponse(
        data={"entries": entries, "total": len(entries)},
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/addresses/watchlist")
async def add_to_watchlist(
    request: Request,
    body: WatchlistAddRequest,
) -> APIResponse[dict[str, Any]]:
    """Add addresses to the on-chain watchlist."""
    added = 0
    now = int(time.time() * 1000)
    for address in body.addresses:
        if address not in _watchlist:
            _watchlist[address] = {
                "address": address,
                "label": body.label or "",
                "note": body.note or "",
                "tags": body.tags,
                "source": body.source,
                "added_at": now,
            }
            added += 1
    return APIResponse(
        data={"added": added},
        meta=build_response_meta(request),
        error=None,
    )


@router.delete("/addresses/watchlist/{address}")
async def remove_from_watchlist(
    address: str,
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """Remove an address from the watchlist."""
    deleted = _watchlist.pop(address, None) is not None
    return APIResponse(
        data={"deleted": deleted},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/addresses/alerts")
async def get_alert_rules(
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """Get all address alert rules."""
    rules = list(_alert_rules.values())
    return APIResponse(
        data={"rules": rules, "total": len(rules)},
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/addresses/alerts")
async def create_alert_rule(
    request: Request,
    body: AlertRuleCreateRequest,
) -> APIResponse[dict[str, Any]]:
    """Create a new address alert rule."""
    rule_id = f"alert-{uuid.uuid4().hex[:8]}"
    rule = {
        "rule_id": rule_id,
        "kind": body.kind,
        "target": body.target,
        "address": body.address,
        "market_keyword": body.market_keyword,
        "min_notional": body.min_notional,
        "price_min": body.price_min,
        "price_max": body.price_max,
        "enabled": body.enabled,
        "created_at": int(time.time() * 1000),
    }
    _alert_rules[rule_id] = rule
    return APIResponse(
        data=rule,
        meta=build_response_meta(request),
        error=None,
    )


@router.patch("/addresses/alerts/{rule_id}")
async def patch_alert_rule(
    rule_id: str,
    request: Request,
    body: AlertRulePatchRequest,
) -> APIResponse[dict[str, Any]]:
    """Partially update an address alert rule."""
    rule = _alert_rules.get(rule_id)
    if rule is None:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND,
            message=f"Alert rule '{rule_id}' not found",
        )
    update_data = body.model_dump(exclude_none=True)
    rule.update(update_data)
    return APIResponse(
        data=rule,
        meta=build_response_meta(request),
        error=None,
    )


@router.delete("/addresses/alerts/{rule_id}")
async def delete_alert_rule(
    rule_id: str,
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """Delete an address alert rule."""
    deleted = _alert_rules.pop(rule_id, None) is not None
    return APIResponse(
        data={"deleted": deleted},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/addresses/alerts/matches")
async def get_alert_matches(
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """Get recent alert matches."""
    return APIResponse(
        data={"matches": _alert_matches, "total": len(_alert_matches)},
        meta=build_response_meta(request),
        error=None,
    )


# --- Parameterized address route (MUST be after all static /addresses/* routes) ---


@router.get("/addresses/{address}/details")
async def get_address_details(
    address: str,
    request: Request,
    window_days: int = Query(30, ge=1, le=365, description="Lookback window"),
) -> APIResponse[dict[str, Any]]:
    """Get detailed analysis for a single Polymarket address.

    Returns cached data if available, otherwise fetches and caches.
    """
    result = await _ensure_analyzed(address, window_days=window_days)
    return APIResponse(
        data=result,
        meta=build_response_meta(request),
        error=None,
    )
