# Absorbed from ai-berkshire (https://github.com/xbtlin/ai-berkshire), MIT License (c) 2026 xbtlin.
# Cleanroom reimplementation of the rigor engine contract; see REWRITE_SPEC.md.
"""Exact-decimal financial rigor checks: market-cap validation, multi-source
cross-validation, Benford analysis, human formatting, and a safe calculator."""

from __future__ import annotations

import math
import re
from decimal import Context, Decimal, ROUND_HALF_EVEN

_CTX = Context(prec=50, rounding=ROUND_HALF_EVEN)

_BENFORD_MIN_SAMPLE = 30
_MAD_CONFORM_THRESHOLD = 0.015


def exact(value) -> Decimal:
    """Convert to Decimal via its string form (avoids float artifacts);
    Decimal input passes through unchanged."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def verify_market_cap(price, shares, reported_cap, currency: str = "") -> dict:
    """Recalculate price x shares and compare against the reported cap.

    is_valid is True when the deviation stays within 5% (<=1% is a clean pass)."""
    calc = _CTX.multiply(exact(price), exact(shares))
    reported = exact(reported_cap)
    if reported == 0:
        deviation_pct = 0.0 if calc == 0 else 100.0
    else:
        deviation_pct = abs(float(_CTX.divide(calc, reported)) - 1.0) * 100.0
    return {
        "price": exact(price),
        "shares": exact(shares),
        "calculated_cap": +calc,
        "reported_cap": +reported,
        "currency": currency,
        "deviation_pct": deviation_pct,
        "is_valid": deviation_pct <= 5.0,
    }


def cross_validate(
    field_name: str,
    source_values: dict,
    unit: str = "",
    tolerance_pct: float = 2.0,
) -> dict:
    """Pick the modal value as consensus; every source's deviation from it is
    measured against tolerance_pct."""
    values = {k: exact(v) for k, v in source_values.items()}
    if not values:
        return {
            "field_name": field_name,
            "consensus": None,
            "unit": unit,
            "all_consistent": False,
            "details": [],
        }

    counts: dict[Decimal, int] = {}
    for v in values.values():
        counts[v] = counts.get(v, 0) + 1
    # Modal value; tie-break by insertion order of the first occurrence.
    consensus = max(counts.items(), key=lambda kv: kv[1])[0]
    for v in values.values():
        if counts[v] == counts[consensus]:
            consensus = v
            break

    all_ok = True
    details = []
    for src, val in values.items():
        if consensus != 0:
            dev = abs(float(val) - float(consensus)) / abs(float(consensus)) * 100
        elif val == consensus:
            dev = 0.0  # all-zero agreement: genuinely consistent
        else:
            dev = math.inf  # zero consensus vs nonzero source: maximal disagreement
        ok = dev <= tolerance_pct
        all_ok = all_ok and ok
        details.append({"source": src, "value": val, "deviation_pct": dev})

    return {
        "field_name": field_name,
        "consensus": consensus,
        "unit": unit,
        "all_consistent": all_ok,
        "details": details,
    }


def _leading_digit(value: Decimal) -> int | None:
    if value == 0:
        return None
    magnitude = value.copy_abs().adjusted()
    return int(value.copy_abs().scaleb(-magnitude))


def benford(values: list) -> dict | None:
    """First-digit distribution vs Benford's law; None below minimum sample."""
    digits: list[int] = []
    for v in values:
        d = _leading_digit(abs(exact(v)))
        if d is not None:
            digits.append(d)

    n = len(digits)
    if n < _BENFORD_MIN_SAMPLE:
        return None

    counts: dict[int, int] = {}
    for d in digits:
        counts[d] = counts.get(d, 0) + 1
    observed = {digit: counts.get(digit, 0) / n for digit in range(1, 10)}
    expected = {digit: math.log10(1 + 1 / digit) for digit in range(1, 10)}
    mad = sum(abs(observed[k] - expected[k]) for k in range(1, 10)) / 9

    return {
        "n": n,
        "observed": observed,
        "expected": expected,
        "mad": mad,
        "is_conforming": mad <= _MAD_CONFORM_THRESHOLD,
    }


def fmt_number(d: Decimal, unit: str = "") -> str:
    """Human formatting with CN 亿/万亿 or EN T/B/M units, two decimals.

    With a 亿-suffix the input is already expressed in 亿 (12345亿 = 1.23万亿)."""
    v = float(d)
    if unit.startswith("亿"):
        tail = unit[1:]
        if v >= 10000:
            return f"{v / 10000:.2f}万亿{tail}"
        return f"{v:.2f}{unit}"
    if abs(v) >= 1e12:
        return f"{v / 1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    return f"{v:,.2f}"


_TOKEN = re.compile(r"\s*([0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?|[()+\-*/])\s*")


def exact_calc(expression: str) -> Decimal | None:
    """Evaluate a pure +-*/ arithmetic expression in exact Decimal.

    None on anything unsafe: names, **, division by zero, malformed input."""

    class Reject(Exception):
        pass

    tokens: list[str] = []
    pos = 0
    while pos < len(expression):
        match = _TOKEN.match(expression, pos)
        if match is None:
            return None
        tokens.append(match.group(1))
        pos = match.end()
    if not tokens:
        return None

    cursor = [0]

    def peek() -> str | None:
        return tokens[cursor[0]] if cursor[0] < len(tokens) else None

    def take() -> str | None:
        tok = peek()
        cursor[0] += 1
        return tok

    def parse_primary() -> Decimal:
        tok = take()
        if tok is None:
            raise Reject()
        if tok == "(":
            value = parse_sum()
            if take() != ")":
                raise Reject()
            return value
        try:
            return Decimal(tok)
        except Exception:
            raise Reject()

    def parse_unary() -> Decimal:
        nxt = peek()
        if nxt == "-":
            take()
            return -parse_unary()
        if nxt == "+":
            take()
            return parse_unary()
        return parse_primary()

    def parse_product() -> Decimal:
        value = parse_unary()
        while peek() in ("*", "/"):
            op = take()
            rhs = parse_unary()
            if op == "*":
                value = value * rhs
            else:
                if rhs == 0:
                    raise Reject()
                value = _CTX.divide(value, rhs)
        return value

    def parse_sum() -> Decimal:
        value = parse_product()
        while peek() in ("+", "-"):
            op = take()
            rhs = parse_product()
            value = value + rhs if op == "+" else value - rhs
        return value

    try:
        result = parse_sum()
        if cursor[0] != len(tokens):
            raise Reject()
        return +result
    except Reject:
        return None
    except Exception:
        return None


def three_scenario_valuation(
    current_price,
    current_eps,
    shares_billion,
    growth_optimistic,
    growth_neutral,
    growth_pessimistic,
    pe_optimistic,
    pe_neutral,
    pe_pessimistic,
    years: int = 5,
) -> list[dict]:
    """Project EPS per scenario, apply the scenario PE, report upside vs spot."""
    spot = exact(current_price)
    scenarios = [
        ("Bull", exact(growth_optimistic), exact(pe_optimistic)),
        ("Neutral", exact(growth_neutral), exact(pe_neutral)),
        ("Bear", exact(growth_pessimistic), exact(pe_pessimistic)),
    ]

    results = []
    for name, growth, pe in scenarios:
        future_eps = _CTX.multiply(exact(current_eps), ((Decimal(1) + growth) ** years))
        target_price = _CTX.multiply(future_eps, pe)
        change_pct = float(_CTX.divide(target_price - spot, spot) * Decimal(100))
        results.append(
            {
                "scenario": name,
                "future_eps": +future_eps,
                "target_price": +target_price,
                "change_pct": change_pct,
            }
        )
    return results


def verify_valuation(price, eps=None, bvps=None) -> dict:
    """Spot-check PE / PB / ROE consistency from raw inputs."""
    p = exact(price)
    out: dict = {}
    e = exact(eps) if eps is not None else None
    b = exact(bvps) if bvps is not None else None
    if e is not None and e != 0:
        out["PE"] = float(_CTX.divide(p, e))
    if b is not None and b != 0:
        out["PB"] = float(_CTX.divide(p, b))
        if e is not None and e != 0:
            out["ROE"] = float(_CTX.multiply(_CTX.divide(e, b), Decimal(100)))
    return out
