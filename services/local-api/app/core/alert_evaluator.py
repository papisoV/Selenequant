"""Alert-rule evaluation — pure comparison, no I/O.

Split from the loop on purpose: the loop owns polling, dedupe and notification,
this module only answers "does this rule fire on this data". Tests can then pin
all three states (hit / miss / no data) without a clock, a socket or a database.

**No data is not a miss.** ``None`` means the comparison could not be made —
the ticker is cold, the symbol has too few bars for RSI, the news query failed.
``PriceAlertService.check_alerts`` already draws the same distinction for price
alerts (a ``None`` price skips the rule: no fire, no delete), and it matters
here for the same reason: a rule that fires because its feed is down would tell
the user something false about the market.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

#: Minimum completed bars before an indicator is trusted. RSI's EWM needs
#: ``min_periods=window``; below that the series is NaN and the rule must stay
#: silent rather than compare against NaN.
_INDICATOR_MIN_BARS = 30


@dataclass(frozen=True)
class AlertContext:
    """Everything an evaluation needs, injected so tests stay offline.

    ``price`` returns the last traded price or ``None`` when there is none.
    ``indicator`` returns the latest value of a named metric or ``None``.
    ``news`` returns ``(match_count, latest_title)`` for a ``voice`` rule id, or
    ``None`` when the query could not be made. It is keyed on the rule rather
    than the keyword because two rules may share a keyword and a single news
    query per rule keeps the fetch bounded.
    """

    price: Callable[[str], float | None]
    indicator: Callable[[str, str], float | None]
    news: Callable[[str], tuple[int, str | None] | None] | None = None


@dataclass(frozen=True)
class Hit:
    """One rule firing, with the numbers that made it fire."""

    rule_id: str
    text: str
    observed: float
    threshold: float


def evaluate(rule: dict[str, Any], ctx: AlertContext) -> Hit | None:
    """Evaluate one rule; ``None`` means "no hit OR no data".

    The two are indistinguishable to the caller by design — neither produces a
    notification. Callers that need to tell them apart (the operator reading
    logs) get it from :func:`evaluate_detail`.
    """
    hit, _reason = evaluate_detail(rule, ctx)
    return hit


def evaluate_detail(rule: dict[str, Any], ctx: AlertContext) -> tuple[Hit | None, str]:
    """Like :func:`evaluate`, plus why it did not fire.

    The reason string is for logs, not users: ``no_data`` and ``not_met`` lead
    to the same silence but mean very different things when debugging a rule
    that "never fires".
    """
    rule_type = str(rule.get("type") or "")
    threshold = rule.get("threshold")
    if not isinstance(threshold, dict):
        return (None, "bad_threshold")

    rule_id = str(rule.get("id") or "")
    name = str(rule.get("name") or rule_id)
    symbol = str(rule.get("symbol") or "")

    if rule_type == "price":
        return _evaluate_price(rule_id, name, symbol, threshold, ctx)
    if rule_type == "indicator":
        return _evaluate_indicator(rule_id, name, symbol, threshold, ctx)
    if rule_type == "voice":
        return _evaluate_voice(rule, ctx)
    return (None, "not_pollable")


def _evaluate_price(
    rule_id: str,
    name: str,
    symbol: str,
    threshold: dict[str, Any],
    ctx: AlertContext,
) -> tuple[Hit | None, str]:
    target = _as_float(threshold.get("value"))
    if target is None or not symbol:
        return (None, "bad_threshold")
    observed = ctx.price(symbol)
    if observed is None:
        return (None, "no_data")

    operator = str(threshold.get("operator") or "gt")
    if operator not in ("gt", "lt"):
        # An unrecognised operator is NOT defaulted to either side: "gt" and
        # "lt" are exact opposites, so guessing would silently invert a rule the
        # user wrote (a typo'd "lt " becoming a breakout alert). No fire is the
        # only reading that cannot be wrong in the dangerous direction.
        return (None, "bad_threshold")
    crossed = observed >= target if operator == "gt" else observed <= target
    if not crossed:
        return (None, "not_met")
    direction = "突破" if operator == "gt" else "跌破"
    return (
        Hit(
            rule_id=rule_id,
            text=f"[{name}] {symbol} {direction} {_fmt(target)},现价 {_fmt(observed)}",
            observed=observed,
            threshold=target,
        ),
        "hit",
    )


def _evaluate_indicator(
    rule_id: str,
    name: str,
    symbol: str,
    threshold: dict[str, Any],
    ctx: AlertContext,
) -> tuple[Hit | None, str]:
    target = _as_float(threshold.get("value"))
    metric = str(threshold.get("indicator") or "RSI")
    if target is None or not symbol:
        return (None, "bad_threshold")
    observed = ctx.indicator(symbol, metric)
    if observed is None:
        return (None, "no_data")

    if observed >= target:
        return (
            Hit(
                rule_id=rule_id,
                text=f"[{name}] {symbol} {metric} {_fmt(observed)} ≥ {_fmt(target)}",
                observed=observed,
                threshold=target,
            ),
            "hit",
        )
    return (None, "not_met")


def _evaluate_voice(rule: dict[str, Any], ctx: AlertContext) -> tuple[Hit | None, str]:
    """Match a ``voice`` rule against the news store's keyword search.

    "voice" is a historical misnomer: the rule's field is ``keyword`` and what
    it matches is headline/body text, not audio.

    ``no_data`` covers three cases the caller must treat identically — no news
    reader wired in this deployment, the query threw, and an empty keyword.
    None of them is a fact about the market, so none may fire.
    """
    if ctx.news is None:
        return (None, "not_pollable")
    threshold = rule.get("threshold")
    keyword = str((threshold or {}).get("keyword") or "").strip()
    if not keyword:
        return (None, "bad_threshold")
    rule_id = str(rule.get("id") or "")
    result = ctx.news(rule_id)
    if result is None:
        return (None, "no_data")
    count, latest_title = result
    hit = evaluate_news(rule, count, latest_title)
    return (hit, "hit") if hit is not None else (None, "not_met")


def evaluate_large_trade(
    rule: dict[str, Any], notional_usd: float, symbol: str
) -> Hit | None:
    """Match one ``LargeTradeEvent`` against one rule.

    Event-driven rather than polled: the detector sees each trade once and the
    event is gone, so there is no state to sample later.
    """
    threshold = rule.get("threshold")
    if not isinstance(threshold, dict):
        return None
    minimum = _as_float(threshold.get("min_notional_usd"))
    if minimum is None or notional_usd < minimum:
        return None
    rule_id = str(rule.get("id") or "")
    name = str(rule.get("name") or rule_id)
    return Hit(
        rule_id=rule_id,
        text=(f"[{name}] {symbol} 大单 {_fmt(notional_usd)} USD,阈值 {_fmt(minimum)}"),
        observed=notional_usd,
        threshold=minimum,
    )


def evaluate_news(
    rule: dict[str, Any], match_count: int, latest_title: str | None
) -> Hit | None:
    """Match the news-store result for a ``voice`` rule (keyword news search).

    The type name is a historical misnomer: its threshold field is ``keyword``
    and what it matches is news headlines, not audio.
    """
    if match_count <= 0:
        return None
    rule_id = str(rule.get("id") or "")
    name = str(rule.get("name") or rule_id)
    keyword = str((rule.get("threshold") or {}).get("keyword") or "")
    tail = f":{latest_title}" if latest_title else ""
    return Hit(
        rule_id=rule_id,
        text=f"[{name}] 新闻命中「{keyword}」{match_count} 条{tail}",
        observed=float(match_count),
        threshold=1.0,
    )


def indicator_min_bars() -> int:
    """Completed bars the indicator path requires before trusting a value."""
    return _INDICATOR_MIN_BARS


def _as_float(value: Any) -> float | None:
    """Numeric threshold or ``None``.

    ``bool`` is rejected explicitly: ``True`` is an ``int`` in Python and a
    stray boolean would silently become a 1.0 threshold.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fmt(value: float) -> str:
    """Compact number for the notification body (no trailing ``.0``)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.4g}"
