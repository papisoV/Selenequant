"""Grammar for human-readable rule strings.

The AI strategy generator and hand-written YAML both produce conditions as
text like ``"EMA21 > EMA55"`` or ``"MACD Histogram > 0"``. This module turns
those strings (or already-structured dicts) into :class:`ConditionRule`
records, dropping anything it cannot understand rather than raising —
partially parseable rule lists should degrade, not explode.
"""

from __future__ import annotations

import re
from typing import Any

from selene_space_strategy.models import ConditionRule, EntryRules, ExitRules


# Accepted comparison verbs. Symbolic spellings fold onto their canonical
# word form; ">=" / "<=" intentionally share semantics with strict forms
# (cross detection makes the distinction moot in practice).
_VERBS: dict[str, str] = {
    ">": "greater_than",
    "<": "less_than",
    "=": "equal",
    ">=": "greater_than",
    "<=": "less_than",
    "greater_than": "greater_than",
    "less_than": "less_than",
    "equal": "equal",
    "crosses_above": "crosses_above",
    "crosses_below": "crosses_below",
}


# Two-word indicator names must be matched before single-word splitting can
# chew them apart. Longest alternation first is guaranteed by ordering here.
_MULTIWORD = re.compile(
    r"^(macd\s+histogram|macd\s+signal"
    r"|bbands\s+upper|bbands\s+lower|bbands\s+middle"
    r"|bollinger\s+upper|bollinger\s+lower|bollinger\s+middle)",
    re.IGNORECASE,
)

_NAME_WITH_PERIOD = re.compile(r"^([A-Za-z_]+)(\d+)?$")

# Canonical names + friendly aliases -> registry keys.
_ALIASES: dict[str, str] = {
    "ema": "ema",
    "sma": "sma",
    "rsi": "rsi",
    "macd": "macd",
    "macd_signal": "macd_signal",
    "macd_histogram": "macd_histogram",
    "macd signal": "macd_signal",
    "macd histogram": "macd_histogram",
    "bbands": "bbands",
    "bbands_upper": "bbands_upper",
    "bbands_lower": "bbands_lower",
    "bbands_middle": "bbands_middle",
    "bbands upper": "bbands_upper",
    "bbands lower": "bbands_lower",
    "bbands middle": "bbands_middle",
    "bollinger upper": "bbands_upper",
    "bollinger lower": "bbands_lower",
    "bollinger middle": "bbands_middle",
}


def _as_number(text: str) -> float | None:
    """Numeric literal or None."""
    try:
        return float(text)
    except (ValueError, TypeError):
        return None


def _indicator_ref(token: str) -> tuple[str, dict[str, Any]]:
    """Split an indicator token into its registry key and constructor params.

    A trailing integer becomes the period: ``EMA21`` → ``("ema", {"period":
    21})``. Unknown bare words pass through lowercased so custom indicators
    survive parsing.
    """
    cleaned = token.strip()
    folded = cleaned.lower()

    if folded in _ALIASES:
        return _ALIASES[folded], {}

    hit = _NAME_WITH_PERIOD.match(cleaned)
    if hit:
        key = _ALIASES.get(hit.group(1).lower(), hit.group(1).lower())
        params: dict[str, Any] = {}
        if hit.group(2):
            params["period"] = int(hit.group(2))
        return key, params

    return folded, {}


def _split_verb(remainder: str) -> tuple[str | None, str]:
    """Find the first comparison verb in ``remainder``.

    Returns ``(canonical_verb, right_hand_side)``; verb is None when no known
    spelling appears. Word verbs are matched case-insensitively on word
    boundaries so 'equal' never bites inside a longer identifier.
    """
    lowered = remainder.lower()

    # Word-boundary pass over symbolic + long word verbs, longest first.
    for spelling in sorted(_VERBS, key=len, reverse=True):
        canonical = _VERBS[spelling]
        if len(spelling) > 2:  # multi-letter word verbs need boundaries
            match = re.search(rf"\b{re.escape(spelling)}\b", lowered)
            if match:
                return canonical, remainder[match.end() :].strip()
        elif spelling in (">=", "<="):
            idx = lowered.find(spelling)
            if idx != -1:
                return canonical, remainder[idx + 2 :].strip()

    # Single-character symbols last ('>' would corrupt '>=' otherwise).
    for symbol in (">", "<", "="):
        idx = lowered.find(symbol)
        if idx != -1:
            return _VERBS[symbol], remainder[idx + 1 :].strip()

    return None, ""


def parse_rule_string(rule: str) -> ConditionRule:
    """Parse one condition string into a :class:`ConditionRule`.

    Supported shapes::

        "EMA21 > EMA55"
        "EMA9 crosses_above EMA21"
        "RSI < 30"

    Raises:
        ValueError: no verb found, or the string has no left-hand side.
    """
    text = rule.strip()
    if not text:
        raise ValueError(f"Cannot parse rule: '{rule}'")

    # Multiword indicators at position zero must be lifted before whitespace
    # splitting would tear them into pieces.
    head_hit = _MULTIWORD.match(text)
    if head_hit:
        lhs_token = head_hit.group(1)
        tail = text[head_hit.end() :].strip()
    else:
        pieces = text.split(None, 1)
        if len(pieces) < 2:
            raise ValueError(f"Cannot parse rule: '{rule}'")
        lhs_token, tail = pieces

    verb, rhs_text = _split_verb(tail)
    if verb is None or not lhs_token:
        raise ValueError(f"No operator found in rule: '{rule}'")

    # Anything sitting between the LHS token and the verb belongs to the
    # left-hand indicator name (e.g. "MACD Histogram > 0" after lifting).
    lhs_name, lhs_params = _indicator_ref(lhs_token)

    number = _as_number(rhs_text)
    if number is not None:
        return ConditionRule(indicator=lhs_name, params=lhs_params, operator=verb, comparator=number)

    rhs_name, rhs_params = _indicator_ref(rhs_text)
    return ConditionRule(
        indicator=lhs_name,
        params=lhs_params,
        operator=verb,
        comparator={"indicator": rhs_name, "params": rhs_params},
    )


def parse_rules_list(rules: list[Any]) -> list[ConditionRule]:
    """Convert a mixed list of strings/dicts; unparseable entries are skipped."""
    parsed: list[ConditionRule] = []
    for item in rules:
        if isinstance(item, str):
            try:
                parsed.append(parse_rule_string(item))
            except ValueError:
                continue
        elif isinstance(item, dict) and "indicator" in item and "operator" in item:
            try:
                parsed.append(ConditionRule.model_validate(item))
            except Exception:
                continue
    return parsed


def _list_or_wrap(value: Any) -> list[Any]:
    """Accept either a bare string or a list where a list is expected."""
    if isinstance(value, str):
        return [value]
    return value if isinstance(value, list) else []


def parse_entry_rules(raw: dict[str, Any]) -> EntryRules:
    """Structure the raw ``entry_rules`` mapping."""
    return EntryRules(
        long=parse_rules_list(_list_or_wrap(raw.get("long"))),
        short=parse_rules_list(_list_or_wrap(raw.get("short"))),
    )


def parse_exit_rules(raw: dict[str, Any]) -> ExitRules:
    """Structure the raw ``exit_rules`` mapping (bare strings tolerated)."""
    return ExitRules(
        close_long=parse_rules_list(_list_or_wrap(raw.get("close_long"))),
        close_short=parse_rules_list(_list_or_wrap(raw.get("close_short"))),
    )
