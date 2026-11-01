"""Alert-rule contract: which types exist and what each one requires.

Kept apart from the store (persistence) and the evaluator (comparison) because
the API validates on write, the loop reads back, and the frontend mirrors the
same four types — the set has to live in exactly one place on this side.

``onchain`` is deliberately absent. Without a Glassnode key the fetcher serves
stale cached values (``onchain_fetcher.py`` logs "GLASSNODE_API_KEY unset —
serving stale"), so a rule of that type could only ever fire on hours-old data.
That is worse than no rule: it looks like monitoring and is not.
"""

from __future__ import annotations

from typing import Any

ALERT_TYPES: tuple[str, ...] = ("price", "indicator", "large_trade", "voice")

#: The one threshold field each type is meaningless without.
PRIMARY_FIELD: dict[str, str] = {
    "price": "value",
    "indicator": "value",
    "large_trade": "min_notional_usd",
    "voice": "keyword",
}

#: Types whose evaluation needs a concrete instrument. ``large_trade`` matches
#: every instrument the detector reports, and ``voice`` is news-wide, so neither
#: takes a symbol.
TYPES_REQUIRING_SYMBOL: frozenset[str] = frozenset({"price", "indicator"})

PRICE_OPERATORS: tuple[str, ...] = ("gt", "lt")
INDICATOR_METRICS: tuple[str, ...] = ("RSI", "MACD")

#: Every threshold key each type actually reads. Anything else is dropped on
#: write: the evaluator runs this data every 30s unattended, so its input should
#: be exactly the fields it understands rather than whatever a client sent.
THRESHOLD_FIELDS: dict[str, tuple[str, ...]] = {
    "price": ("operator", "value"),
    "indicator": ("indicator", "value"),
    "large_trade": ("min_notional_usd",),
    "voice": ("keyword",),
}


def sanitize_threshold(rule_type: str, threshold: Any) -> dict[str, Any]:
    """Keep only the keys *rule_type* reads. Unknown types keep nothing."""
    allowed = THRESHOLD_FIELDS.get(rule_type, ())
    if not isinstance(threshold, dict):
        return {}
    return {k: v for k, v in threshold.items() if k in allowed}


def validate_rule(draft: dict[str, Any]) -> list[str]:
    """Return the reasons *draft* is rejected; empty means valid.

    Shape checks only — whether the rule can be *evaluated* (does the symbol
    resolve to a live source, are there enough bars for RSI) is the evaluator's
    business and deliberately not a write-time error. A rule whose data source
    is temporarily cold stays stored and honest rather than being rejected.
    """
    errors: list[str] = []
    rule_type = str(draft.get("type") or "")
    if rule_type not in ALERT_TYPES:
        return [f"type must be one of {list(ALERT_TYPES)}, got {rule_type!r}"]

    if not str(draft.get("name") or "").strip():
        errors.append("name is required")

    if rule_type in TYPES_REQUIRING_SYMBOL:
        if not str(draft.get("symbol") or "").strip():
            errors.append(f"symbol is required for type {rule_type!r}")

    threshold = draft.get("threshold")
    if not isinstance(threshold, dict):
        return [*errors, "threshold must be an object"]

    key = PRIMARY_FIELD[rule_type]
    value = threshold.get(key)
    if rule_type == "voice":
        if not isinstance(value, str) or not value.strip():
            errors.append("threshold.keyword must be a non-empty string")
    else:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"threshold.{key} must be a number")

    if rule_type == "price":
        operator = threshold.get("operator", "gt")
        if operator not in PRICE_OPERATORS:
            errors.append(f"threshold.operator must be one of {list(PRICE_OPERATORS)}")
    if rule_type == "indicator":
        metric = threshold.get("indicator", "RSI")
        if metric not in INDICATOR_METRICS:
            errors.append(
                f"threshold.indicator must be one of {list(INDICATOR_METRICS)}"
            )
    # A rule evaluated every 30s needs a cadence, not a symbol: ``large_trade``
    # is matched against every instrument the detector reports and ``voice`` is
    # news-wide, so requiring a symbol for them would reject valid rules.
    return errors
