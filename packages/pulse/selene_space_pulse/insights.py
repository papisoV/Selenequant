from __future__ import annotations

from .models import AssetAnalysis, AttentionItem, ChecklistItem, ContributionItem, InsightSummary, RiskFinding


def build_insights(analyses: list[AssetAnalysis]) -> InsightSummary:
    findings: list[RiskFinding] = []
    attention: list[AttentionItem] = []

    for analysis in analyses:
        asset_findings = _risk_findings(analysis)
        findings.extend(asset_findings)
        item = _attention_item(analysis, asset_findings)
        if item:
            attention.append(item)

    day_contributors = _contributors(analyses, key="day")
    unrealized_contributors = _contributors(analyses, key="unrealized")
    checklist = _checklist(attention, findings, analyses)
    return InsightSummary(
        attention=sorted(attention, key=lambda item: item.priority, reverse=True)[:12],
        risk_findings=sorted(findings, key=lambda item: _severity_rank(item.severity), reverse=True),
        day_contributors=day_contributors,
        unrealized_contributors=unrealized_contributors,
        checklist=checklist,
    )


def _risk_findings(analysis: AssetAnalysis) -> list[RiskFinding]:
    symbol = analysis.asset.symbol
    metrics = analysis.metrics
    findings: list[RiskFinding] = []
    close = _num(metrics.get("last_close")) or analysis.snapshot.last_close
    sma20 = _num(metrics.get("sma20"))
    sma50 = _num(metrics.get("sma50"))
    sma200 = _num(metrics.get("sma200"))
    rsi = _num(metrics.get("rsi14"))
    drawdown = _num(metrics.get("drawdown_60d"))
    change_pct = analysis.snapshot.change_pct
    unrealized_pct = analysis.position.unrealized_pnl_pct if analysis.position else None
    allocation = analysis.position.allocation_pct if analysis.position else None

    if close and sma20 and close < sma20:
        findings.append(_finding(symbol, "medium", "below_sma20", "Price is below the 20-day moving average.", close / sma20 - 1))
    if close and sma50 and close < sma50:
        findings.append(_finding(symbol, "medium", "below_sma50", "Price is below the 50-day moving average.", close / sma50 - 1))
    if close and sma200 and close < sma200:
        findings.append(_finding(symbol, "high", "below_sma200", "Price is below the 200-day moving average.", close / sma200 - 1))
    if rsi is not None and rsi >= 75:
        findings.append(_finding(symbol, "medium", "rsi_overheated", "RSI is overheated.", rsi))
    if rsi is not None and rsi <= 30:
        findings.append(_finding(symbol, "medium", "rsi_oversold", "RSI is oversold; verify whether this is weakness or capitulation.", rsi))
    if drawdown is not None and drawdown <= -0.18:
        findings.append(_finding(symbol, "high", "large_drawdown", "Recent drawdown is large.", drawdown))
    elif drawdown is not None and drawdown <= -0.10:
        findings.append(_finding(symbol, "medium", "moderate_drawdown", "Recent drawdown deserves risk review.", drawdown))
    if change_pct is not None and abs(change_pct) >= 0.06:
        findings.append(_finding(symbol, "medium", "single_day_move", "Single-day move is unusually large.", change_pct))
    if unrealized_pct is not None and unrealized_pct <= -0.12:
        findings.append(_finding(symbol, "high", "position_loss", "Position unrealized loss exceeds review threshold.", unrealized_pct))
    elif unrealized_pct is not None and unrealized_pct <= -0.06:
        findings.append(_finding(symbol, "medium", "position_loss_watch", "Position unrealized loss is building.", unrealized_pct))
    if allocation is not None and allocation >= 0.65:
        findings.append(_finding(symbol, "medium", "concentration", "Position allocation is concentrated.", allocation))
    return findings


def _attention_item(analysis: AssetAnalysis, findings: list[RiskFinding]) -> AttentionItem | None:
    has_position = analysis.position is not None
    high_count = sum(1 for item in findings if item.severity == "high")
    medium_count = sum(1 for item in findings if item.severity == "medium")
    day_pnl = analysis.position.day_pnl if analysis.position else None
    unrealized_pnl = analysis.position.unrealized_pnl if analysis.position else None
    priority = high_count * 30 + medium_count * 10
    if has_position:
        priority += 20
    if analysis.signal.risk_level == "high":
        priority += 18
    if day_pnl is not None and day_pnl < 0:
        priority += 8
    if unrealized_pnl is not None and unrealized_pnl < 0:
        priority += 12
    if priority <= 0:
        return None
    reason = _reason(analysis, findings)
    return AttentionItem(
        symbol=analysis.asset.symbol,
        priority=priority,
        reason=reason,
        has_position=has_position,
        risk_level=analysis.signal.risk_level,
        day_pnl=day_pnl,
        unrealized_pnl=unrealized_pnl,
    )


def _contributors(analyses: list[AssetAnalysis], key: str) -> list[ContributionItem]:
    items: list[ContributionItem] = []
    for analysis in analyses:
        if not analysis.position:
            continue
        items.append(
            ContributionItem(
                symbol=analysis.asset.symbol,
                currency=analysis.position.currency,
                day_pnl=analysis.position.day_pnl,
                unrealized_pnl=analysis.position.unrealized_pnl,
                market_value=analysis.position.market_value,
                allocation_pct=analysis.position.allocation_pct,
            )
        )
    if key == "day":
        return sorted(items, key=lambda item: abs(item.day_pnl or 0), reverse=True)[:10]
    return sorted(items, key=lambda item: abs(item.unrealized_pnl or 0), reverse=True)[:10]


def _checklist(
    attention: list[AttentionItem],
    findings: list[RiskFinding],
    analyses: list[AssetAnalysis],
) -> list[ChecklistItem]:
    checklist: list[ChecklistItem] = []
    for item in sorted(attention, key=lambda entry: entry.priority, reverse=True)[:5]:
        checklist.append(
            ChecklistItem(
                text=f"Review {item.symbol}: {item.reason}",
                priority="high" if item.priority >= 60 else "normal",
                symbol=item.symbol,
            )
        )
    if any(item.rule == "concentration" for item in findings):
        checklist.append(ChecklistItem(text="Check position concentration and whether allocation still matches the plan.", priority="normal"))
    if any(item.rule == "single_day_move" for item in findings):
        checklist.append(ChecklistItem(text="Verify large single-day moves against news, filings, and corporate actions.", priority="normal"))
    if any(analysis.snapshot.source is None for analysis in analyses):
        checklist.append(ChecklistItem(text="Review symbols without a confirmed data source.", priority="normal"))
    if not checklist:
        checklist.append(ChecklistItem(text="No urgent rule-based issues. Review news and data freshness before making decisions."))
    return checklist[:10]


def _finding(symbol: str, severity: str, rule: str, message: str, value: float | str | None) -> RiskFinding:
    return RiskFinding(symbol=symbol, severity=severity, rule=rule, message=message, value=_round(value))


def _reason(analysis: AssetAnalysis, findings: list[RiskFinding]) -> str:
    if findings:
        top = sorted(findings, key=lambda item: _severity_rank(item.severity), reverse=True)[0]
        return top.message
    if analysis.signal.risk_level == "high":
        return "Signal score indicates high risk."
    return "Position or signal deserves review."


def _severity_rank(severity: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(severity, 0)


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | str | None) -> float | str | None:
    if isinstance(value, str) or value is None:
        return value
    return round(float(value), 6)
