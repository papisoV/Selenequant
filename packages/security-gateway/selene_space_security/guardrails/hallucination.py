"""Hallucination guards: price checks, claim scanning, secret redaction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from selene_space_types import RiskLevel

_DEFAULT_PRICE_THRESHOLD = 0.10
_DEFAULT_MAX_CONFIDENCE = 1.0

_KNOWN_INDICATORS = frozenset(
    {"sma", "ema", "rsi", "macd", "atr", "adx", "bollinger", "stoch", "obv", "vwap"}
)

_UNVERIFIED_WARNING = "数值声明未经工具验证，请勿直接采信。"
_PROMISE_DISCLAIMER = "投资有风险，历史表现不代表未来收益，本内容不构成投资建议。"
_GENERIC_SECRET = "[已脱敏]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
)

_MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"\d+(?:\.\d+)?%")
_RATIO_RE = re.compile(
    r"(?:sharpe|sortino|calmar|ratio|drawdown|return)\D{0,20}?(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_TOOL_OUTPUT_KEYS = ("output", "result", "text", "data")

_PROMISE_PHRASES: tuple[str, ...] = (
    "保证盈利",
    "保证收益",
    "零风险",
    "必涨",
    "稳赚不赔",
    "只赚不亏",
    "包赚",
    "guarantee",
    "risk-free",
    "can't lose",
    "cannot lose",
    "sure profit",
    "no risk at all",
)


@dataclass
class ScanResult:
    triggered: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PriceDeviationAlert:
    severity: RiskLevel
    deviation_pct: float
    message: str


def _tool_output_text(tool_results: list[dict]) -> str:
    chunks: list[str] = []
    for entry in tool_results:
        if not isinstance(entry, dict):
            continue
        for key in _TOOL_OUTPUT_KEYS:
            value = entry.get(key)
            if isinstance(value, str):
                chunks.append(value.lower())
                break
    return "\n".join(chunks)


def _normalize_number(raw: str) -> str:
    cleaned = raw.replace("$", "").replace("%", "").replace(",", "").strip()
    if cleaned.endswith("."):
        cleaned = cleaned[:-1]
    try:
        as_float = float(cleaned)
    except ValueError:
        return cleaned
    if as_float == int(as_float):
        return str(int(as_float))
    return cleaned


class HallucinationDetector:
    def __init__(self, *, price_deviation_threshold: float = 0.10) -> None:
        self.price_deviation_threshold = price_deviation_threshold

    def check_price_reference(
        self,
        symbol: str,
        claimed_price: float,
        actual_price: float,
    ) -> PriceDeviationAlert | None:
        if actual_price <= 0:
            return PriceDeviationAlert(
                severity=RiskLevel.DANGEROUS,
                deviation_pct=float("inf"),
                message=(
                    f"{symbol} 的实际价格非法（{actual_price}），无法核对声称价格 "
                    f"{claimed_price}，该报价不可信。"
                ),
            )
        deviation = abs(claimed_price - actual_price) / abs(actual_price)
        if deviation < self.price_deviation_threshold:
            return None
        if deviation >= 0.50:
            severity = RiskLevel.BLOCKED
        elif deviation >= 0.25:
            severity = RiskLevel.DANGEROUS
        else:
            severity = RiskLevel.RESTRICTED
        claimed_text = f"{claimed_price:g}"
        message = (
            f"{symbol} 价格引用异常：声称 {claimed_text}，实际 "
            f"{actual_price:g}，偏离 {deviation:.1%}。请以行情数据为准。"
        )
        return PriceDeviationAlert(
            severity=severity,
            deviation_pct=deviation,
            message=message,
        )

    def check_indicator_exists(
        self,
        name: str,
        *,
        known: frozenset[str] | set[str] | None = None,
    ) -> bool:
        catalog = _KNOWN_INDICATORS if known is None else known
        return name.strip().lower() in catalog

    def check_confidence_reasonable(
        self,
        confidence: float,
        *,
        max_confidence: float | None = None,
    ) -> bool:
        ceiling = _DEFAULT_MAX_CONFIDENCE if max_confidence is None else max_confidence
        return confidence < ceiling

    def scan_text_for_unverified_claims(
        self,
        text: str,
        tool_results: list[dict],
    ) -> ScanResult:
        evidence = _tool_output_text(tool_results)
        candidates: list[str] = []
        candidates.extend(_MONEY_RE.findall(text))
        candidates.extend(_PERCENT_RE.findall(text))
        for match in _RATIO_RE.finditer(text):
            candidates.append(match.group(1))
        warnings: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = _normalize_number(candidate)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            if normalized not in evidence:
                warnings.append(f"“{candidate}” {_UNVERIFIED_WARNING}")
        return ScanResult(triggered=bool(warnings), warnings=warnings)

    def scan_for_investment_promises(self, text: str) -> ScanResult:
        lowered = text.lower()
        hits = [phrase for phrase in _PROMISE_PHRASES if phrase in lowered]
        if not hits:
            return ScanResult(triggered=False, warnings=[])
        warning = f"检测到收益承诺话术（{hits[0]}）。{_PROMISE_DISCLAIMER}"
        return ScanResult(triggered=True, warnings=[warning])

    def redact_secrets_in_output(self, text: str) -> str:
        redacted = text
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(_GENERIC_SECRET, redacted)
        return redacted

    def scan_output(
        self,
        text: str,
        tool_results: list[dict],
    ) -> tuple[str, ScanResult]:
        safe_text = self.redact_secrets_in_output(text)
        claims_scan = self.scan_text_for_unverified_claims(safe_text, tool_results)
        promise_scan = self.scan_for_investment_promises(safe_text)
        combined = ScanResult(
            triggered=claims_scan.triggered or promise_scan.triggered,
            warnings=[*claims_scan.warnings, *promise_scan.warnings],
        )
        if promise_scan.triggered:
            safe_text = f"{safe_text}\n\n{_PROMISE_DISCLAIMER}"
        return safe_text, combined
