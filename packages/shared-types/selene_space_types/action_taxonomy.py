# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: consolidated from three
# upstream schema modules (decision_action / decision_scale / decision_profile)
# into one declarative vocabulary layer. Upstream's 270-line Chinese/English
# report-prose parser (normalize_decision_action with its negation phrase
# tables) is intentionally NOT carried over — Selene consumes structured LLM
# signals (LLMSignal), not free-form report advice, so there is no prose entry
# point. A one-way ``action_direction`` bridge to Selene's existing
# long/short/neutral direction language is added; nothing existing is replaced.
"""Eight-state decision action vocabulary, score scale, and profile helpers.

daily_stock_analysis separates two decision languages: an eight-state
``action`` **display taxonomy** (buy / add / hold / reduce / sell / watch /
avoid / alert) and a three-state ``decision_type`` **statistics contract**
(buy / hold / sell). This module carries the eight-state vocabulary, the
canonical 0-100 → band → action scale, and the decision-profile normalizer.

It is purely declarative: every function maps an *explicit* value (an action
token, alias, score, or profile) to a canonical result. It never scans prose
for intent and never mutates an input contract. Selene's execution direction
language (``FinalDirection`` / ``LLMSignalAction``) is left untouched — the
``action_direction`` helper bridges *from* this vocabulary *to* it, one way.

Pure data plus pure functions, stdlib only — safe to import from any package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal, get_args


DecisionAction = Literal[
    "buy", "add", "hold", "reduce", "sell", "watch", "avoid", "alert"
]

DECISION_SCALE_VERSION: Final[str] = "decision-scale-v1"

_ACTION_VALUES: Final[frozenset[str]] = frozenset(get_args(DecisionAction))

ACTION_LABELS: Final[dict[str, dict[str, str]]] = {
    "buy": {"zh": "买入", "en": "Buy", "ko": "매수"},
    "add": {"zh": "加仓", "en": "Add", "ko": "추가 매수"},
    "hold": {"zh": "持有", "en": "Hold", "ko": "보유"},
    "reduce": {"zh": "减仓", "en": "Reduce", "ko": "비중축소"},
    "sell": {"zh": "卖出", "en": "Sell", "ko": "매도"},
    "watch": {"zh": "观望", "en": "Watch", "ko": "관망"},
    "avoid": {"zh": "回避", "en": "Avoid", "ko": "회피"},
    "alert": {"zh": "预警", "en": "Alert", "ko": "경고"},
}

# Eight-state action → three-state statistics bucket. buy/add fold to "buy",
# reduce/sell to "sell", and the neutral/guard states (hold/watch/avoid/alert)
# to "hold"; this mirrors upstream's ``display_decision_type_for_result``.
ACTION_DECISION_TYPE: Final[dict[str, str]] = {
    "buy": "buy",
    "add": "buy",
    "hold": "hold",
    "reduce": "sell",
    "sell": "sell",
    "watch": "hold",
    "avoid": "hold",
    "alert": "hold",
}

# Selene bridge (not from upstream): the direction an action implies in
# Selene's existing long/short execution vocabulary. The four non-directional
# states (hold/watch/avoid/alert) are all "neutral" — none of them is a trade.
ACTION_DIRECTION: Final[dict[str, str]] = {
    "buy": "long",
    "add": "long",
    "hold": "neutral",
    "reduce": "short",
    "sell": "short",
    "watch": "neutral",
    "avoid": "neutral",
    "alert": "neutral",
}

# Explicit aliases upstream accepts for a *token* (never for prose). The
# underscore form ("strong_buy") reaches these via key normalization.
_EXPLICIT_ALIASES: Final[dict[str, str]] = {
    "strong buy": "buy",
    "accumulate": "add",
    "trim": "reduce",
    "strong sell": "sell",
    "wait": "watch",
}


@dataclass(frozen=True)
class DecisionScaleBand:
    """One band of the canonical score-to-decision scale."""

    min_score: int
    max_score: int
    signal_key: str
    action: str
    decision_type: str
    label_zh: str
    description_zh: str


DECISION_SCALE: Final[tuple[DecisionScaleBand, ...]] = (
    DecisionScaleBand(
        80,
        100,
        "strong_buy",
        "buy",
        "buy",
        "强烈买入",
        "高胜率机会，可执行买入/加仓计划",
    ),
    DecisionScaleBand(
        60, 79, "buy", "buy", "buy", "买入", "偏积极机会，允许少量待确认项"
    ),
    DecisionScaleBand(
        40, 59, "watch", "watch", "hold", "观望", "信号分歧或确认不足，等待触发条件"
    ),
    DecisionScaleBand(
        20, 39, "reduce", "reduce", "sell", "减仓", "风险明显抬升，优先降低暴露"
    ),
    DecisionScaleBand(
        0, 19, "sell", "sell", "sell", "卖出", "趋势或风险显著恶化，优先退出"
    ),
)

DECISION_SCALE_PROMPT_ZH: Final[str] = """## Canonical 评分与动作口径

- `sentiment_score`、三态 `decision_type` 与八态 `action` 必须按同一口径表达。
- 80-100：强烈买入，`action=buy`，`decision_type=buy`。
- 60-79：买入，`action=buy`，`decision_type=buy`。
- 40-59：观望，`action=watch`，`decision_type=hold`。
- 20-39：减仓，`action=reduce`，`decision_type=sell`。
- 0-19：卖出，`action=sell`，`decision_type=sell`。
- `decision_type` 只保留 `buy|hold|sell` 兼容统计；更细建议必须写入 `action`。
- 若 score >= 60 但最终 `action` 是 `hold/watch`，或 score < 40 但最终 `action` 是 `hold/watch`，必须在 `guardrail_reason` 中说明降级原因。"""

DECISION_SCALE_PROMPT_EN: Final[str] = """## Canonical Score and Action Scale

- `sentiment_score`, the three-state `decision_type` and the eight-state `action` must follow the same scale.
- 80-100: Strong Buy, `action=buy`, `decision_type=buy`.
- 60-79: Buy, `action=buy`, `decision_type=buy`.
- 40-59: Watch, `action=watch`, `decision_type=hold`.
- 20-39: Reduce, `action=reduce`, `decision_type=sell`.
- 0-19: Sell, `action=sell`, `decision_type=sell`.
- `decision_type` only keeps `buy|hold|sell` for statistics compatibility; finer-grained advice must go into `action`.
- If score >= 60 but the final `action` is `hold/watch`, or score < 40 but the final `action` is `hold/watch`, explain the downgrade in `guardrail_reason`."""


DecisionProfile = Literal["conservative", "balanced", "aggressive"]

VALID_DECISION_PROFILES: Final[tuple[str, ...]] = (
    "conservative",
    "balanced",
    "aggressive",
)
DECISION_PROFILE_UNKNOWN: Final[str] = "unknown"

_LANG_KEYS: Final[frozenset[str]] = frozenset({"zh", "en", "ko"})


def _lang_key(lang: str | None) -> str:
    """Map a language label to a supported key, defaulting to ``en``."""
    text = (lang or "").strip().lower()
    if text[:2] in _LANG_KEYS:
        return text[:2]
    return "en"


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def normalize_score(value: Any) -> int | None:
    """Return a bounded integer score in ``0..100``, or None when not numeric."""
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        return None
    if 0 <= score <= 100:
        return score
    return None


def decision_band_for_score(value: Any) -> DecisionScaleBand | None:
    """Return the canonical decision band for a 0-100 score."""
    score = normalize_score(value)
    if score is None:
        return None
    for band in DECISION_SCALE:
        if band.min_score <= score <= band.max_score:
            return band
    return None


def signal_key_for_score(value: Any) -> str | None:
    """Return the canonical signal key for a 0-100 score."""
    band = decision_band_for_score(value)
    return band.signal_key if band else None


def action_for_score(value: Any) -> str | None:
    """Return the canonical eight-state action for a 0-100 score."""
    band = decision_band_for_score(value)
    return band.action if band else None


def decision_type_for_score(value: Any) -> str | None:
    """Return the canonical three-state decision type for a 0-100 score."""
    band = decision_band_for_score(value)
    return band.decision_type if band else None


def score_band_metadata(value: Any) -> dict[str, Any]:
    """Return stable scale metadata for persistence and diagnostics."""
    score = normalize_score(value)
    band = decision_band_for_score(score)
    if score is None or band is None:
        return {}
    return {
        "scale_version": DECISION_SCALE_VERSION,
        "score": score,
        "score_band": f"{band.min_score}-{band.max_score}",
        "signal_key": band.signal_key,
        "canonical_action": band.action,
        "canonical_decision_type": band.decision_type,
    }


def normalize_action(value: Any) -> DecisionAction | None:
    """Return a canonical eight-state action for an explicit token or alias.

    Only *explicit* values are recognized — an action name ("buy", "BUY",
    "strong_buy") or a known alias ("accumulate", "trim", "wait"). Arbitrary
    human-readable text is NOT parsed: an unrecognized value returns ``None``
    rather than defaulting to a neutral action, so a caller never mistakes
    "no signal" for "hold".
    """
    key = _normalize_key(value)
    if not key:
        return None
    if key in _ACTION_VALUES:
        return key  # type: ignore[return-value]
    alias = _EXPLICIT_ALIASES.get(key)
    return alias  # type: ignore[return-value]


def action_label(action: Any, lang: str | None = "en") -> str | None:
    """Return a localized display label for a decision action."""
    normalized = normalize_action(action)
    if not normalized:
        return None
    return ACTION_LABELS[normalized][_lang_key(lang)]


def action_decision_type(action: Any) -> str | None:
    """Return the three-state statistics bucket for an eight-state action."""
    normalized = normalize_action(action)
    if not normalized:
        return None
    return ACTION_DECISION_TYPE[normalized]


def action_direction(action: Any) -> str | None:
    """Return the Selene execution direction for an eight-state action.

    A one-way bridge to Selene's existing long/short/neutral vocabulary: the
    four non-directional states (hold/watch/avoid/alert) map to ``neutral``.
    Returns ``None`` for an unrecognized action.
    """
    normalized = normalize_action(action)
    if not normalized:
        return None
    return ACTION_DIRECTION[normalized]


def normalize_decision_profile(
    value: Any,
    *,
    field_name: str = "decision_profile",
) -> DecisionProfile | None:
    """Return a normalized profile, or raise for a non-empty invalid value.

    ``None`` / ``""`` mean "legacy / unknown profile" and pass through as
    ``None``; a non-empty but invalid value raises ``ValueError`` so a typo is
    never silently read as "no preference".
    """
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in VALID_DECISION_PROFILES:
        return text  # type: ignore[return-value]
    allowed = ", ".join(VALID_DECISION_PROFILES)
    raise ValueError(f"{field_name} must be one of: {allowed}")


def score_action_conflicts_without_guardrail(
    *,
    score: Any,
    action: Any,
    guardrail_reason: Any = None,
) -> bool:
    """Return True when a neutral action conflicts with a directional score.

    Score 0-100 and eight-state action must tell the same story: a ``hold`` /
    ``watch`` action is inconsistent with a score that maps to buy / reduce /
    sell, *unless* an explicit guardrail reason explains the downgrade. Used to
    flag reports whose displayed action silently contradicts their score.
    """
    if str(guardrail_reason or "").strip():
        return False
    normalized_action = _normalize_key(action)
    if normalized_action not in {"hold", "watch"}:
        return False
    score_action = action_for_score(score)
    return score_action in {"buy", "reduce", "sell"}
