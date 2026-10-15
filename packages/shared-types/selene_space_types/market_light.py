# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: dimension availability
# is market-capability aware — limit-up/down statistics apply only to markets
# with daily price limits (CN/TW), not HK/US/JP/KR/CRYPTO — so a dimension
# that does not apply to a market is marked ``applicable=False`` instead of
# merely missing, and does not degrade ``data_quality``.
"""Market Light — market-level traffic-light snapshot.

Composes three orthogonal breadth dimensions into one 0-100 market score and
a three-band status (green ≥60 / yellow ≥40 / red):

===========  =====  =====================================================
dimension    weight meaning
===========  =====  =====================================================
breadth      0.45   advancers / (advancers + decliners)
index        0.35   50 + average major-index change (%) × 12, clamped 0-100
limit        0.20   limit-ups / (limit-ups + limit-downs)
===========  =====  =====================================================

A dimension whose data is absent, or that does not apply to the market,
contributes a neutral 50, so missing data is never read as bearish — the same
fail-neutral stance DSA uses. ``index`` is the anchor: without index data the
snapshot is ``unavailable``.

Pure functions, stdlib only — safe to import from any package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Iterable, Literal, Sequence

from selene_space_types.market_context import resolve_market


MarketLightStatus = Literal["green", "yellow", "red"]
MarketLightDataQuality = Literal["ok", "partial", "unavailable"]

NEUTRAL_SCORE: Final[int] = 50

# Daily price limits exist for CN A-shares (±10/20/30%) and TW (±10%); the
# other markets Selene spans have no daily limit band.
LIMIT_APPLICABLE_MARKETS: Final[frozenset[str]] = frozenset({"CN", "TW"})

_STATUS_GREEN_MIN: Final[int] = 60
_STATUS_YELLOW_MIN: Final[int] = 40
_TEMPERATURE_STRONG_MIN: Final[int] = 70
_TEMPERATURE_WARM_MIN: Final[int] = 55
_TEMPERATURE_MIXED_MIN: Final[int] = 40
_INDEX_CHANGE_GAIN: Final[float] = 12.0

_LABEL: Final[dict[str, dict[str, str]]] = {
    "zh": {"green": "可进攻", "yellow": "需观察", "red": "偏防守"},
    "en": {"green": "risk-on", "yellow": "balanced", "red": "risk-off"},
}

_GUIDANCE: Final[dict[str, dict[str, str]]] = {
    "zh": {
        "green": "风险偏好尚可，关注主线延续与仓位纪律。",
        "yellow": "信号分化，控制仓位并等待量价确认。",
        "red": "风险偏高，优先控制回撤，避免追高弱反弹。",
    },
    "en": {
        "green": "Risk appetite is acceptable; focus on leading themes and position discipline.",
        "yellow": "Signals are mixed; keep position sizing moderate and wait for confirmation.",
        "red": "Risk is elevated; prioritize drawdown control and avoid chasing weak rebounds.",
    },
}


@dataclass(frozen=True)
class MarketLightDimension:
    """One Market Light scoring dimension.

    ``available`` means data was present; ``applicable`` means the dimension
    is meaningful for the market at all. An inapplicable dimension reports a
    neutral score and is excluded from the ``data_quality`` judgement.
    """

    score: int
    available: bool
    applicable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.score, int) or not 0 <= self.score <= 100:
            raise ValueError(
                f"dimension score must be an int within [0, 100]: {self.score!r}"
            )


@dataclass(frozen=True)
class MarketLightWeights:
    """Weighting across the three dimensions; must sum to 1."""

    breadth: float = 0.45
    index: float = 0.35
    limit: float = 0.20

    def __post_init__(self) -> None:
        total = self.breadth + self.index + self.limit
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0, got {total!r}")


@dataclass(frozen=True)
class MarketLight:
    """Market-level traffic-light snapshot."""

    region: str
    status: MarketLightStatus
    score: int
    label: str
    temperature_label: str
    guidance: str
    reasons: tuple[str, ...]
    breadth: MarketLightDimension
    index: MarketLightDimension
    limit: MarketLightDimension
    data_quality: MarketLightDataQuality


def _lang_key(lang: str | None) -> str:
    return "zh" if (lang or "").strip().lower().startswith("zh") else "en"


def _clamp_int(value: float) -> int:
    """Match DSA's ``int(...)`` truncation of a clamped dimension score."""
    return int(max(0, min(100, value)))


def _clamp_round(value: float) -> int:
    return int(max(0, min(100, round(value))))


def breadth_dimension(up_count: int, down_count: int) -> MarketLightDimension:
    """Advancers share of all movers, as a 0-100 score."""
    participants = up_count + down_count
    if participants <= 0:
        return MarketLightDimension(score=NEUTRAL_SCORE, available=False)
    return MarketLightDimension(
        score=_clamp_int(up_count / participants * 100), available=True
    )


def index_dimension(changes: Iterable[float | None]) -> MarketLightDimension:
    """Average major-index change mapped to 0-100 (50 = flat)."""
    values = [float(c) for c in changes if c is not None]
    if not values:
        return MarketLightDimension(score=NEUTRAL_SCORE, available=False)
    avg_change = sum(values) / len(values)
    return MarketLightDimension(
        score=_clamp_int(NEUTRAL_SCORE + avg_change * _INDEX_CHANGE_GAIN),
        available=True,
    )


def limit_dimension(
    limit_up_count: int,
    limit_down_count: int,
    *,
    applicable: bool,
) -> MarketLightDimension:
    """Limit-ups share of all limit moves, as a 0-100 score."""
    if not applicable:
        return MarketLightDimension(
            score=NEUTRAL_SCORE, available=False, applicable=False
        )
    total = limit_up_count + limit_down_count
    if total <= 0:
        return MarketLightDimension(score=NEUTRAL_SCORE, available=False)
    return MarketLightDimension(
        score=_clamp_int(limit_up_count / total * 100), available=True
    )


def market_light_status(score: int) -> MarketLightStatus:
    """Three-band status: green ≥60, yellow ≥40, red below."""
    if score >= _STATUS_GREEN_MIN:
        return "green"
    if score >= _STATUS_YELLOW_MIN:
        return "yellow"
    return "red"


def market_light_temperature(score: int, lang: str = "zh") -> str:
    """Four-band qualitative temperature label."""
    zh = _lang_key(lang) == "zh"
    if score >= _TEMPERATURE_STRONG_MIN:
        return "强势" if zh else "risk-on"
    if score >= _TEMPERATURE_WARM_MIN:
        return "偏暖" if zh else "constructive"
    if score >= _TEMPERATURE_MIXED_MIN:
        return "震荡" if zh else "mixed"
    return "偏弱" if zh else "defensive"


def compose_market_light(
    *,
    region: str,
    up_count: int = 0,
    down_count: int = 0,
    index_changes: Sequence[float | None] = (),
    limit_up_count: int = 0,
    limit_down_count: int = 0,
    limit_applicable: bool | None = None,
    total_amount: float | None = None,
    lang: str = "zh",
    weights: MarketLightWeights | None = None,
) -> MarketLight:
    """Compose a Market Light snapshot from raw market inputs.

    Args:
        region: Market label (``"cn"`` / ``"US"`` / ``"crypto"`` and aliases);
            normalized via ``resolve_market``. Unknown markets raise.
        up_count / down_count: Advancing vs declining symbols.
        index_changes: Major-index percentage changes (``None`` entries ignored).
        limit_up_count / limit_down_count: Limit-move counts, where applicable.
        limit_applicable: Override the market-capability default
            (``LIMIT_APPLICABLE_MARKETS``).
        total_amount: Turnover, used only for a last-resort reason string.
        lang: ``"zh"`` (default) or ``"en"`` for label/guidance/reasons.
        weights: Dimension weights; defaults to ``MarketLightWeights()``.

    Raises:
        ValueError: If ``region`` does not resolve to a known market.
    """
    key = resolve_market(region)
    if key is None:
        raise ValueError(f"unknown market region: {region!r}")
    if limit_applicable is None:
        limit_applicable = key in LIMIT_APPLICABLE_MARKETS
    weights = weights or MarketLightWeights()

    breadth = breadth_dimension(up_count, down_count)
    index = index_dimension(index_changes)
    limit = limit_dimension(
        limit_up_count, limit_down_count, applicable=limit_applicable
    )

    score = _clamp_round(
        breadth.score * weights.breadth
        + index.score * weights.index
        + limit.score * weights.limit
    )
    status = market_light_status(score)
    lang_key = _lang_key(lang)

    if not index.available:
        data_quality: MarketLightDataQuality = "unavailable"
    elif all(d.available for d in (breadth, index, limit) if d.applicable):
        data_quality = "ok"
    else:
        data_quality = "partial"

    return MarketLight(
        region=key,
        status=status,
        score=score,
        label=_LABEL[lang_key][status],
        temperature_label=market_light_temperature(score, lang_key),
        guidance=_GUIDANCE[lang_key][status],
        reasons=_build_reasons(
            up_count=up_count,
            down_count=down_count,
            index_changes=index_changes,
            limit_up_count=limit_up_count,
            limit_down_count=limit_down_count,
            total_amount=total_amount,
            lang_key=lang_key,
        ),
        breadth=breadth,
        index=index,
        limit=limit,
        data_quality=data_quality,
    )


def _build_reasons(
    *,
    up_count: int,
    down_count: int,
    index_changes: Sequence[float | None],
    limit_up_count: int,
    limit_down_count: int,
    total_amount: float | None,
    lang_key: str,
) -> tuple[str, ...]:
    zh = lang_key == "zh"
    reasons: list[str] = []

    participants = up_count + down_count
    if participants > 0:
        ratio = up_count / participants
        if zh:
            if ratio >= 0.6:
                reasons.append(f"上涨家数占比 {ratio:.0%}，赚钱效应扩散")
            elif ratio <= 0.4:
                reasons.append(f"上涨家数占比 {ratio:.0%}，亏钱效应较强")
            else:
                reasons.append(f"上涨家数占比 {ratio:.0%}，市场分化")
        else:
            if ratio >= 0.6:
                reasons.append(f"advancers ratio {ratio:.0%}, breadth is expanding")
            elif ratio <= 0.4:
                reasons.append(
                    f"advancers ratio {ratio:.0%}, downside pressure dominates"
                )
            else:
                reasons.append(f"advancers ratio {ratio:.0%}, breadth is mixed")

    values = [float(c) for c in index_changes if c is not None]
    if values:
        avg_change = sum(values) / len(values)
        reasons.append(
            f"主要指数平均涨跌幅 {avg_change:+.2f}%"
            if zh
            else f"average major-index change {avg_change:+.2f}%"
        )

    if limit_up_count or limit_down_count:
        spread = limit_up_count - limit_down_count
        reasons.append(
            f"涨跌停差 {spread:+d}" if zh else f"limit-up/down spread {spread:+d}"
        )

    if not reasons and total_amount:
        reasons.append(
            f"成交额 {total_amount:.0f}" if zh else f"turnover {total_amount:.0f}"
        )

    if not reasons:
        reasons.append(
            "结构化涨跌数据有限，按可用行情综合判断"
            if zh
            else "limited structured breadth data; using available market inputs"
        )
    return tuple(reasons[:4])


__all__ = [
    "LIMIT_APPLICABLE_MARKETS",
    "NEUTRAL_SCORE",
    "MarketLight",
    "MarketLightDataQuality",
    "MarketLightDimension",
    "MarketLightStatus",
    "MarketLightWeights",
    "breadth_dimension",
    "compose_market_light",
    "index_dimension",
    "limit_dimension",
    "market_light_status",
    "market_light_temperature",
]
