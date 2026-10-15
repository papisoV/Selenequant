# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); ported the market-theme aggregation
# schema and the pure phase/strength/active-themes/stock-role helpers into a
# standalone no-IO aggregator (caller supplies already-fetched rankings).

"""行情热点/题材层（📋5）— 纯聚合，不联网、不做 IO。

从 daily_stock_analysis 的 market_structure schema 与
market_hotspot_service / market_structure_service 的核心纯函数移植：
调用方把已抓取的行业/概念排名（plain dicts）喂进来，这里只做
归一化 → 活跃题材 → 领涨/领跌 → 广度 → 数据质量快照。
数据源缺失时如实降级为 partial，绝不编造数据。
"""

from __future__ import annotations

from typing import Any, Iterable, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

MARKET_THEME_SCHEMA_VERSION = "market-theme-v1"
STOCK_MARKET_POSITION_SCHEMA_VERSION = "stock-market-position-v1"

MarketStructureStatus = Literal["ok", "partial", "unknown", "not_supported"]
ThemeRankSource = Literal["industry", "concept", "mixed", "unknown"]
ThemePhase = Literal["warming", "accelerating", "cooling", "unknown"]
StockRole = Literal["leader", "follower", "edge", "unknown"]

_ACCELERATING_THRESHOLD = 3.0


# ---------------------------------------------------------------------------
# 契约模型（移植自 DSA src/schemas/market_structure.py）
# ---------------------------------------------------------------------------


class RankedThemeItem(BaseModel):
    """一条板块/题材排名条目。"""

    name: str
    change_pct: Optional[float] = None
    rank: Optional[int] = None
    source: ThemeRankSource = "unknown"
    code: Optional[str] = None
    updated_at: Optional[str] = None


class MarketThemeItem(RankedThemeItem):
    """活跃题材条目：在排名条目上附加相位/强度/理由。"""

    phase: ThemePhase = "unknown"
    strength_score: Optional[int] = None
    reason: Optional[str] = None


class ThemeBreadth(BaseModel):
    """题材广度计数快照。"""

    active_count: int = 0
    leading_industry_count: int = 0
    leading_concept_count: int = 0
    lagging_count: int = 0


class MarketStructureDataQuality(BaseModel):
    """数据质量快照（展示语义）：缺什么如实记录，不静默降级为 ok。"""

    status: MarketStructureStatus = "unknown"
    missing_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class MarketThemeContext(BaseModel):
    """行情题材上下文顶层聚合。"""

    schema_version: str = MARKET_THEME_SCHEMA_VERSION
    status: MarketStructureStatus = "unknown"
    market: str = "cn"
    trade_date: Optional[str] = None
    active_themes: list[MarketThemeItem] = Field(default_factory=list)
    leading_industries: list[RankedThemeItem] = Field(default_factory=list)
    leading_concepts: list[RankedThemeItem] = Field(default_factory=list)
    lagging_themes: list[RankedThemeItem] = Field(default_factory=list)
    theme_breadth: ThemeBreadth = Field(default_factory=ThemeBreadth)
    data_quality: MarketStructureDataQuality = Field(
        default_factory=MarketStructureDataQuality
    )


# ---------------------------------------------------------------------------
# 归一化（移植 DSA _safe_float / _safe_int / _optional_text）
# ---------------------------------------------------------------------------


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("%"):
                text = text[:-1].strip()
            return float(text)
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_theme_name(value: str) -> str:
    return str(value or "").strip().lower()


def _normalize_stock_code(value: str) -> str:
    return str(value or "").strip().upper()


# ---------------------------------------------------------------------------
# 核心纯函数（移植 DSA market_hotspot_service）
# ---------------------------------------------------------------------------


def phase_from_change(value: Optional[float]) -> ThemePhase:
    """涨跌幅 → 题材相位。>=3 加速 / >0 转暖 / 其余退潮 / 缺数未知。"""
    if value is None:
        return "unknown"
    if value >= _ACCELERATING_THRESHOLD:
        return "accelerating"
    if value > 0:
        return "warming"
    return "cooling"


def strength_from_change(value: Optional[float]) -> Optional[int]:
    """涨跌幅 → 0..100 强度分（50 为零轴基准，斜率 8）。缺数返回 None。"""
    if value is None:
        return None
    return max(0, min(100, int(round(50 + value * 8))))


def build_active_themes(
    items: Sequence[RankedThemeItem],
    *,
    limit: int,
) -> list[MarketThemeItem]:
    """仅保留 change_pct > 0 的条目，按涨幅降序取前 limit 个并附相位/强度。"""
    positive_items = [
        item for item in items if item.change_pct is not None and item.change_pct > 0
    ]
    positive_items.sort(key=lambda item: item.change_pct or 0, reverse=True)

    active: list[MarketThemeItem] = []
    for item in positive_items[:limit]:
        active.append(
            MarketThemeItem(
                name=item.name,
                code=item.code,
                change_pct=item.change_pct,
                rank=item.rank,
                source=item.source,
                updated_at=item.updated_at,
                phase=phase_from_change(item.change_pct),
                strength_score=strength_from_change(item.change_pct),
                reason="industry/concept ranking gain",
            )
        )
    return active


# ---------------------------------------------------------------------------
# 排名归一化 + 上下文聚合
# ---------------------------------------------------------------------------


def normalize_ranking(
    rows: Iterable[Mapping[str, Any]],
    *,
    source: ThemeRankSource,
) -> list[RankedThemeItem]:
    """把已抓取的排名行（dict）归一化为 RankedThemeItem 列表。

    接受 ashare `get_industry_comparison` 的行形态
    （rank/industry/change_pct/...，industry 列做 name）以及
    DSA 原生形态（name/code/板块代码/涨跌幅）。
    """
    normalized: list[RankedThemeItem] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            continue
        name = _optional_text(row.get("name") or row.get("industry") or row.get("板块名称"))
        if not name:
            continue
        change_pct = _safe_float(
            row.get("change_pct")
            if "change_pct" in row
            else row.get("pct_chg")
            if "pct_chg" in row
            else row.get("涨跌幅")
            if "涨跌幅" in row
            else row.get("涨跌幅%")
        )
        normalized.append(
            RankedThemeItem(
                name=name,
                code=_optional_text(row.get("code") or row.get("板块代码")),
                change_pct=change_pct,
                rank=_safe_int(row.get("rank")) or index,
                source=source,
                updated_at=_optional_text(row.get("updated_at")),
            )
        )
    return normalized


def _top_by_change(
    items: Sequence[RankedThemeItem], *, limit: int
) -> list[RankedThemeItem]:
    ranked = [item for item in items if item.change_pct is not None and item.change_pct > 0]
    ranked.sort(key=lambda item: item.change_pct or 0, reverse=True)
    return ranked[:limit]


def _bottom_by_change(
    items: Sequence[RankedThemeItem], *, limit: int
) -> list[RankedThemeItem]:
    ranked = [item for item in items if item.change_pct is not None and item.change_pct < 0]
    ranked.sort(key=lambda item: item.change_pct or 0)
    return ranked[:limit]


def build_theme_context(
    rankings: Mapping[str, Any],
    *,
    market: str = "cn",
    trade_date: Optional[str] = None,
    limit: int = 10,
) -> MarketThemeContext:
    """由已抓取的行业/概念排名聚合出 MarketThemeContext。

    rankings 键: "industry"（必选）与 "concept"（可选），值为排名行 dict
    列表（或已归一化的 RankedThemeItem）。任一来源缺失 → status="partial"
    并写入 missing_fields，绝不编造数据。
    """
    missing_fields: list[str] = []
    errors: list[str] = []

    industry_rows = rankings.get("industry")
    concept_rows = rankings.get("concept")

    industries = _as_ranked_items(industry_rows, source="industry")
    concepts = _as_ranked_items(concept_rows, source="concept")

    if industry_rows is None:
        missing_fields.append("industry_ranking")
    elif not industries and industry_rows:
        errors.append("industry_ranking_unusable")

    if concept_rows is None:
        missing_fields.append("concept_ranking")
    elif not concepts and concept_rows:
        errors.append("concept_ranking_unusable")

    status: MarketStructureStatus
    if missing_fields or errors:
        status = "partial"
    else:
        status = "ok"

    active = build_active_themes([*industries, *concepts], limit=limit)
    leading_industries = _top_by_change(industries, limit=limit)
    leading_concepts = _top_by_change(concepts, limit=limit)
    lagging = _bottom_by_change([*industries, *concepts], limit=limit)

    return MarketThemeContext(
        status=status,
        market=market,
        trade_date=trade_date,
        active_themes=active,
        leading_industries=leading_industries,
        leading_concepts=leading_concepts,
        lagging_themes=lagging,
        theme_breadth=ThemeBreadth(
            active_count=len(active),
            leading_industry_count=len(leading_industries),
            leading_concept_count=len(leading_concepts),
            lagging_count=len(lagging),
        ),
        data_quality=MarketStructureDataQuality(
            status=status,
            missing_fields=missing_fields,
            errors=errors,
        ),
    )


def _as_ranked_items(
    rows: Any,
    *,
    source: ThemeRankSource,
) -> list[RankedThemeItem]:
    if rows is None:
        return []
    if isinstance(rows, list) and rows and isinstance(rows[0], RankedThemeItem):
        return list(rows)  # type: ignore[arg-type]
    if isinstance(rows, Iterable):
        return normalize_ranking(rows, source=source)
    return []


# ---------------------------------------------------------------------------
# 个股在题材中的位置（移植 DSA market_structure_service._infer_stock_role）
# ---------------------------------------------------------------------------


class StockBoardPosition(BaseModel):
    """个股关联板块快照。"""

    name: str
    type: Optional[str] = None
    code: Optional[str] = None
    rank: Optional[int] = None
    change_pct: Optional[float] = None
    source: ThemeRankSource = "unknown"


class PrimaryTheme(BaseModel):
    """个股归属的主题材。"""

    name: str
    source: ThemeRankSource = "unknown"
    phase: ThemePhase = "unknown"
    rank: Optional[int] = None
    change_pct: Optional[float] = None


def _extract_stock_code(item: Mapping[str, Any]) -> str:
    for key in ("code", "stock_code", "ts_code", "ticker", "symbol"):
        value = item.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized.upper()
    return ""


def _extract_item_themes(item: Mapping[str, Any]) -> set[str]:
    themes: set[str] = set()
    for key in (
        "theme",
        "theme_name",
        "topic",
        "topic_name",
        "industry",
        "industry_name",
        "concept",
        "concept_name",
    ):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                themes.add(_normalize_theme_name(text))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for element in value:
                text = _optional_text(element)
                if text:
                    themes.add(_normalize_theme_name(text))
    return themes


def _match_stock_in_thematic_list(
    stock_code: str,
    theme_name: str,
    items: Sequence[Mapping[str, Any]],
) -> bool:
    normalized_code = _normalize_stock_code(stock_code)
    normalized_theme = _normalize_theme_name(theme_name)
    if not normalized_code or not normalized_theme:
        return False
    for item in items:
        candidate_code = _extract_stock_code(item)
        if not candidate_code or candidate_code != normalized_code:
            continue
        themes = _extract_item_themes(item)
        if not themes:
            continue
        if normalized_theme in themes:
            return True
    return False


def is_stock_leader_for_theme(
    stock_code: str,
    theme_name: str,
    leader_stocks: Sequence[Mapping[str, Any]],
) -> bool:
    return _match_stock_in_thematic_list(stock_code, theme_name, leader_stocks)


def is_stock_in_constituents_for_theme(
    stock_code: str,
    theme_name: str,
    hotspot_constituents: Sequence[Mapping[str, Any]],
) -> bool:
    return _match_stock_in_thematic_list(stock_code, theme_name, hotspot_constituents)


def has_primary_market_evidence(primary_theme: Optional[PrimaryTheme]) -> bool:
    if primary_theme is None:
        return False
    return (
        primary_theme.rank is not None
        or primary_theme.change_pct is not None
        or primary_theme.phase != "unknown"
    )


def has_stock_role_evidence(
    stock_code: str,
    primary_theme: Optional[PrimaryTheme],
    hotspot_constituents: Sequence[Mapping[str, Any]],
    leader_stocks: Sequence[Mapping[str, Any]],
) -> bool:
    if primary_theme is None:
        return False
    if not stock_code:
        return False
    return is_stock_in_constituents_for_theme(
        stock_code,
        primary_theme.name,
        hotspot_constituents,
    ) or is_stock_leader_for_theme(
        stock_code,
        primary_theme.name,
        leader_stocks,
    )


def infer_stock_role(
    stock_code: str,
    primary_theme: Optional[PrimaryTheme],
    related_boards: Sequence[StockBoardPosition],
    *,
    has_market_match: bool,
    hotspot_constituents: Sequence[Mapping[str, Any]],
    leader_stocks: Sequence[Mapping[str, Any]],
) -> StockRole:
    """个股在题材中的角色：leader / follower / edge / unknown。

    移植 DSA _infer_stock_role 的判定顺序：无主题材或市场不匹配或证据
    不足 → 有关联板块记 edge（证据薄）否则 unknown；主题材板块内先查
    领涨股名单（leader），再查热点成分（follower）。
    """
    if primary_theme is None:
        return "edge" if related_boards else "unknown"
    if not has_market_match:
        return "edge" if related_boards else "unknown"
    if not has_primary_market_evidence(primary_theme) or not has_stock_role_evidence(
        stock_code,
        primary_theme,
        hotspot_constituents,
        leader_stocks,
    ):
        return "edge" if related_boards else "unknown"
    for board in related_boards:
        if board.name == primary_theme.name:
            if is_stock_leader_for_theme(
                stock_code,
                primary_theme.name,
                leader_stocks,
            ):
                return "leader"
            if is_stock_in_constituents_for_theme(
                stock_code,
                primary_theme.name,
                hotspot_constituents,
            ):
                return "follower"
            break
    return "edge" if related_boards else "unknown"
