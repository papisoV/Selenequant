from __future__ import annotations

import html
import json
import re
from pathlib import Path

from selene_space_types.stock import (
    AssetAnalysis,
    BenchmarkSnapshot,
    DailyReport,
    DataFreshness,
    HistoryPoint,
    PortfolioSummary,
)
from .ui import lang, language_boot_script, language_runtime_script, language_toggle, ui_styles


def write_reports(report: DailyReport, output_dir: str | Path) -> dict[str, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y%m%d-%H%M")
    base = f"market-pulse-{stamp}"
    markdown_path = directory / f"{base}.md"
    html_path = directory / f"{base}.html"
    json_path = directory / f"{base}.json"

    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    html_path.write_text(render_html(report), encoding="utf-8")
    json_path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"markdown": markdown_path, "html": html_path, "json": json_path}


def render_markdown(report: DailyReport) -> str:
    lines = [
        f"# {report.title}",
        "",
        f"Generated: {report.generated_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        "",
        "> Research automation only. This report is not financial advice.",
        "",
        "## Market Brief",
        "",
        report.market_brief,
        "",
    ]
    if report.portfolio_ai_summary:
        lines.extend(["## AI Portfolio Brief", "", report.portfolio_ai_summary, ""])
    if report.portfolio:
        lines.extend(_portfolio_markdown(report.portfolio))
    lines.extend(_benchmarks_markdown(report.benchmarks))
    lines.extend(_insights_markdown(report))
    lines.extend(
        [
            "",
            "## Watchlist",
            "",
            "| Symbol | Name | Close | Change | Source | Benchmark | Rel 20D | Freshness | Score | Stance | Risk | Position | Day P/L | Unrealized |",
            "|---|---:|---:|---:|---|---|---:|---|---:|---|---|---:|---:|---:|",
        ]
    )
    for item in report.analyses:
        position = item.position
        lines.append(
            "| {symbol} | {name} | {close} | {change} | {source} | {benchmark} | {rel20} | {freshness} | {score} | {stance} | {risk} | {position} | {day_pnl} | {unrealized} |".format(
                symbol=item.asset.symbol,
                name=item.snapshot.name,
                close=_money(item.snapshot.last_close, item.snapshot.currency),
                change=_pct(item.snapshot.change_pct),
                source=item.snapshot.source or "n/a",
                benchmark=item.benchmark.symbol if item.benchmark else "n/a",
                rel20=_pct(item.benchmark.relative_return_20d) if item.benchmark else "n/a",
                freshness=item.freshness.status if item.freshness else "n/a",
                score=item.signal.score,
                stance=item.signal.stance,
                risk=item.signal.risk_level,
                position=_money(position.market_value, position.currency) if position else "n/a",
                day_pnl=_signed_money(position.day_pnl, position.currency) if position else "n/a",
                unrealized=_signed_money(position.unrealized_pnl, position.currency) if position else "n/a",
            )
        )

    for item in report.analyses:
        lines.extend(_asset_markdown(item, report.history.get(item.asset.symbol, [])))
    return "\n".join(lines).strip() + "\n"


def render_html(report: DailyReport) -> str:
    rows = "\n".join(_html_card(item, report.history.get(item.asset.symbol, [])) for item in report.analyses)
    generated = html.escape(report.generated_at.strftime("%Y-%m-%d %H:%M:%S %Z"))
    title = html.escape(report.title)
    brief = html.escape(report.market_brief)
    portfolio = _portfolio_html(report.portfolio)
    benchmarks = _benchmarks_html(report.benchmarks)
    ai_brief = _ai_brief_html(report.portfolio_ai_summary)
    summary_strip = _summary_strip_html(report)
    insights = _insights_html(report)
    default_lang = "zh" if report.language.lower().startswith("zh") else "en"
    high_risk = sum(1 for item in report.analyses if item.signal.risk_level == "high")
    positioned = sum(1 for item in report.analyses if item.position)
    return f"""<!doctype html>
<html lang="en" data-lang="{default_lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  {language_boot_script(default_lang)}
  <style>
    {ui_styles()}
    .report-brief {{ margin: 18px 0; }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 18px 0; }}
    .report-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(295px, 1fr)); gap: 16px; }}
    .strip-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px; margin: 18px 0; }}
    .strip-row {{ background: var(--canvas); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }}
    .strip-row .strip-top {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
    .strip-row .strip-symbol {{ font-weight: 760; }}
    .strip-row .strip-meta {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }}
    .strip-score {{ font-size: 18px; font-weight: 800; line-height: 1; }}
    .top {{ display: flex; align-items: start; justify-content: space-between; gap: 12px; }}
    .symbol {{ font-size: 22px; font-weight: 780; }}
    .score {{ min-width: 54px; text-align: right; font-size: 30px; line-height: 1; font-weight: 840; color: var(--brand-strong); }}
    dl {{ display: grid; grid-template-columns: 1fr 1fr; gap: 9px 14px; margin: 14px 0; }}
    dt {{ color: var(--muted); font-size: 12px; }}
    dd {{ margin: 0; font-weight: 680; }}
    .asset-card h3 {{ margin-top: 16px; }}
    .asset-card .spark {{ height: 48px; }}
    .hero-panel dl {{ margin: 0; display: grid; gap: 10px; }}
    .hero-panel dt {{ color: #a9b8b1; }}
    .hero-panel dd {{ color: #ffffff; }}
  </style>
</head>
<body>
<main>
  <nav class="topbar">
    <div class="brand-mark">AI Market Pulse</div>
    {language_toggle()}
  </nav>
  <header class="hero">
    <div>
      <div class="eyebrow">{lang("Daily Quant Research Note", "每日量化研究简报")}</div>
      <h1>{title}</h1>
      <p>{lang("Rules-first quant signal review with optional AI context, benchmark comparison, portfolio attribution, and risk controls.", "以规则量化信号为底座，结合可选 AI 上下文、基准对比、组合归因和风险控制。")}</p>
    </div>
    <aside class="hero-panel">
      <dl>
        <div><dt>{lang("Generated", "生成时间")}</dt><dd>{generated}</dd></div>
        <div><dt>{lang("Symbols", "标的数量")}</dt><dd>{len(report.analyses)}</dd></div>
        <div><dt>{lang("High risk", "高风险")}</dt><dd>{high_risk}</dd></div>
        <div><dt>{lang("Positioned", "持仓标的")}</dt><dd>{positioned}</dd></div>
      </dl>
    </aside>
  </header>
  <section class="brief report-brief">{brief}<br><span class="muted">{lang("Quant research automation only. This report is not financial advice and does not place trades.", "仅用于量化研究自动化。本报告不构成投资建议，也不会下单交易。")}</span></section>
  {summary_strip}
  {ai_brief}
  {portfolio}
  {benchmarks}
  {insights}
  <h2>{lang("Watchlist", "观察列表")}</h2>
  <p class="section-note">{lang("Each card blends price action, signal score, position context, reasons, warnings, and news links.", "每张卡片整合价格表现、信号评分、持仓上下文、原因、警告与新闻链接。")}</p>
  <section class="report-grid">
    {rows}
  </section>
</main>
{language_runtime_script()}
</body>
</html>
"""


def _portfolio_markdown(portfolio: list[PortfolioSummary]) -> list[str]:
    lines = [
        "",
        "## Portfolio",
        "",
        "| Currency | Positions | Market Value | Day P/L | Unrealized P/L | Unrealized % |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for summary in portfolio:
        lines.append(
            "| {currency} | {positions} | {market} | {day} | {unrealized} | {unrealized_pct} |".format(
                currency=summary.currency,
                positions=summary.positions,
                market=_money(summary.market_value, summary.currency),
                day=_signed_money(summary.day_pnl, summary.currency),
                unrealized=_signed_money(summary.unrealized_pnl, summary.currency),
                unrealized_pct=_pct(summary.unrealized_pnl_pct),
            )
        )
    return lines


def _benchmarks_markdown(benchmarks: list[BenchmarkSnapshot]) -> list[str]:
    if not benchmarks:
        return []
    lines = [
        "",
        "## Benchmark Context",
        "",
        "| Benchmark | Name | Close | Change | 20D | 60D | Source | Freshness |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for benchmark in benchmarks:
        lines.append(
            "| {symbol} | {name} | {close} | {change} | {return_20d} | {return_60d} | {source} | {freshness} |".format(
                symbol=benchmark.symbol,
                name=benchmark.name,
                close=_money(benchmark.last_close, benchmark.currency),
                change=_pct(benchmark.change_pct),
                return_20d=_pct(benchmark.return_20d),
                return_60d=_pct(benchmark.return_60d),
                source=benchmark.source or "n/a",
                freshness=benchmark.freshness.status if benchmark.freshness else "n/a",
            )
        )
    return lines


def _insights_markdown(report: DailyReport) -> list[str]:
    insights = report.insights
    lines: list[str] = ["", "## Focus Board", ""]
    if insights.attention:
        lines.extend(["### Needs Attention", "", "| Symbol | Priority | Risk | Reason | Day P/L | Unrealized |", "|---|---:|---|---|---:|---:|"])
        for item in insights.attention:
            lines.append(
                f"| {item.symbol} | {item.priority} | {item.risk_level} | {_md_escape(item.reason)} | {_signed_money(item.day_pnl)} | {_signed_money(item.unrealized_pnl)} |"
            )
    else:
        lines.extend(["No rule-based attention items.", ""])
    if insights.day_contributors:
        lines.extend(["", "### Day Contribution", "", "| Symbol | Day P/L | Unrealized | Allocation |", "|---|---:|---:|---:|"])
        for item in insights.day_contributors:
            lines.append(
                f"| {item.symbol} | {_signed_money(item.day_pnl, item.currency)} | {_signed_money(item.unrealized_pnl, item.currency)} | {_pct(item.allocation_pct)} |"
            )
    if insights.risk_findings:
        lines.extend(["", "### Risk Findings", ""])
        for finding in insights.risk_findings[:12]:
            value = f" ({_num(finding.value)})" if finding.value is not None else ""
            lines.append(f"- [{finding.severity}] {finding.symbol}: {_md_escape(finding.message)}{value}")
    if insights.checklist:
        lines.extend(["", "### Checklist", ""])
        for item in insights.checklist:
            prefix = f"{item.symbol}: " if item.symbol else ""
            lines.append(f"- [{item.priority}] {prefix}{_md_escape(item.text)}")
    return lines


def _asset_markdown(item: AssetAnalysis, history: list[HistoryPoint]) -> list[str]:
    lines = [
        "",
        f"## {item.asset.symbol} - {item.snapshot.name}",
        "",
        f"- Close: {_money(item.snapshot.last_close, item.snapshot.currency)} ({_pct(item.snapshot.change_pct)})",
        f"- Data source: {item.snapshot.source or 'n/a'}",
        f"- Signal: {item.signal.score}/100, {item.signal.stance}, risk {item.signal.risk_level}",
        f"- Data freshness: {_freshness_plain(item.freshness)}",
        f"- 20D / 60D return: {_pct(item.metrics.get('return_20d'))} / {_pct(item.metrics.get('return_60d'))}",
        f"- RSI14: {_num(item.metrics.get('rsi14'))}, ATR%: {_pct(item.metrics.get('atr_pct'))}",
    ]
    if item.benchmark:
        lines.append(
            f"- Benchmark: {item.benchmark.symbol}, relative 20D {_pct(item.benchmark.relative_return_20d)}, "
            f"relative 60D {_pct(item.benchmark.relative_return_60d)}, {item.benchmark.verdict}"
        )
    if item.position:
        lines.extend(
            [
                f"- Position: {item.position.quantity:g} shares, value {_money(item.position.market_value, item.position.currency)}, allocation {_pct(item.position.allocation_pct)}",
                f"- P/L: day {_signed_money(item.position.day_pnl, item.position.currency)}, unrealized {_signed_money(item.position.unrealized_pnl, item.position.currency)} ({_pct(item.position.unrealized_pnl_pct)})",
            ]
        )
    if len(history) >= 2:
        lines.append(f"- Score trend: {_trend_text([point.score for point in history])}")
    if item.ai_summary:
        lines.extend(["", "### AI Summary", "", item.ai_summary])
    lines.extend(["", "### Reasons", ""])
    lines.extend(f"- {reason}" for reason in item.signal.reasons)
    if item.warnings:
        lines.extend(["", "### Warnings", ""])
        lines.extend(f"- {warning}" for warning in item.warnings)
    if item.news:
        lines.extend(["", "### News", ""])
        for news in item.news:
            label = f"{news.title} - {news.source}" if news.source else news.title
            lines.append(f"- [{label}]({_safe_href(news.link)})")
    return lines


def _portfolio_html(portfolio: list[PortfolioSummary]) -> str:
    if not portfolio:
        return ""
    cards = []
    for summary in portfolio:
        day_class = _value_class(summary.day_pnl)
        unrealized_class = _value_class(summary.unrealized_pnl)
        cards.append(
            f"""
<div class="summary">
  <span class="muted">{html.escape(summary.currency)} {lang("portfolio", "组合")}</span>
  <strong>{html.escape(_money(summary.market_value, summary.currency))}</strong>
  <div>{summary.positions} {lang("positions", "个持仓")}</div>
  <div class="{day_class}">{lang("Day P/L", "当日盈亏")} {html.escape(_signed_money(summary.day_pnl, summary.currency))}</div>
  <div class="{unrealized_class}">{lang("Unrealized", "浮动盈亏")} {html.escape(_signed_money(summary.unrealized_pnl, summary.currency))} ({html.escape(_pct(summary.unrealized_pnl_pct))})</div>
</div>
"""
        )
    return '<section class="summary-grid">' + "\n".join(cards) + "</section>"


def _benchmarks_html(benchmarks: list[BenchmarkSnapshot]) -> str:
    if not benchmarks:
        return ""
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.symbol)}</td>"
        f"<td>{html.escape(item.name)}</td>"
        f"<td>{html.escape(_money(item.last_close, item.currency))}</td>"
        f"<td class=\"{_value_class(item.change_pct)}\">{html.escape(_pct(item.change_pct))}</td>"
        f"<td class=\"{_value_class(item.return_20d)}\">{html.escape(_pct(item.return_20d))}</td>"
        f"<td class=\"{_value_class(item.return_60d)}\">{html.escape(_pct(item.return_60d))}</td>"
        f"<td>{html.escape(item.source or 'n/a')}</td>"
        f"<td>{_freshness_badge(item.freshness)}</td>"
        "</tr>"
        for item in benchmarks
    )
    return f"""
<section class="brief">
  <strong>{lang("Benchmark Context", "基准对比")}</strong>
  <p class="muted">{lang("SPY, QQQ, CSI 300, Hang Seng Index, and configured market benchmarks provide relative context for each symbol.", "SPY、QQQ、沪深300、恒生指数及配置中的市场基准，为单股强弱提供参照。")}</p>
  <table>
    <thead><tr><th>{lang("Benchmark", "基准")}</th><th>{lang("Name", "名称")}</th><th>{lang("Close", "收盘价")}</th><th>{lang("Change", "涨跌幅")}</th><th>20D</th><th>60D</th><th>{lang("Source", "数据源")}</th><th>{lang("Freshness", "新鲜度")}</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>
"""


def _ai_brief_html(summary: str | None) -> str:
    if not summary:
        return ""
    return f"""
<section class="brief">
  <strong>{lang("AI Portfolio Brief", "AI 组合简报")}</strong>
  <p>{html.escape(summary)}</p>
</section>
"""


def _insights_html(report: DailyReport) -> str:
    insights = report.insights
    attention = "".join(
        "<tr>"
        f"<td>{html.escape(item.symbol)}</td>"
        f"<td>{item.priority}</td>"
        f"<td>{html.escape(item.risk_level)}</td>"
        f"<td>{html.escape(item.reason)}</td>"
        f"<td class=\"{_value_class(item.day_pnl)}\">{html.escape(_signed_money(item.day_pnl))}</td>"
        f"<td class=\"{_value_class(item.unrealized_pnl)}\">{html.escape(_signed_money(item.unrealized_pnl))}</td>"
        "</tr>"
        for item in insights.attention[:8]
    )
    checklist = "".join(
        f"<li><strong>{html.escape(item.priority)}</strong> {html.escape((item.symbol + ': ') if item.symbol else '')}{html.escape(item.text)}</li>"
        for item in insights.checklist[:8]
    )
    day = "".join(
        "<tr>"
        f"<td>{html.escape(item.symbol)}</td>"
        f"<td class=\"{_value_class(item.day_pnl)}\">{html.escape(_signed_money(item.day_pnl, item.currency))}</td>"
        f"<td class=\"{_value_class(item.unrealized_pnl)}\">{html.escape(_signed_money(item.unrealized_pnl, item.currency))}</td>"
        f"<td>{html.escape(_pct(item.allocation_pct))}</td>"
        "</tr>"
        for item in insights.day_contributors[:6]
    )
    risks = "".join(
        f"<li><strong>{html.escape(finding.severity)}</strong> {html.escape(finding.symbol)}: {html.escape(finding.message)}</li>"
        for finding in insights.risk_findings[:8]
    )
    attention_body = attention or f'<tr><td colspan="6" class="muted">{lang("No rule-based attention items.", "暂无规则触发的重点关注项。")}</td></tr>'
    day_body = day or f'<tr><td colspan="4" class="muted">{lang("No positioned assets yet.", "暂无持仓资产。")}</td></tr>'
    risks_body = risks or f'<li class="muted">{lang("No rule-based risk findings.", "暂无规则触发的风险发现。")}</li>'
    checklist_body = checklist or f'<li class="muted">{lang("No checklist items.", "暂无检查项。")}</li>'
    return f"""
<section class="brief">
  <strong>{lang("Focus Board", "焦点看板")}</strong>
  <h3>{lang("Needs Attention", "重点关注")}</h3>
  <table>
    <thead><tr><th>{lang("Symbol", "代码")}</th><th>{lang("Priority", "优先级")}</th><th>{lang("Risk", "风险")}</th><th>{lang("Reason", "原因")}</th><th>{lang("Day P/L", "当日盈亏")}</th><th>{lang("Unrealized", "浮动盈亏")}</th></tr></thead>
    <tbody>{attention_body}</tbody>
  </table>
  <h3>{lang("Day Contribution", "当日贡献")}</h3>
  <table>
    <thead><tr><th>{lang("Symbol", "代码")}</th><th>{lang("Day P/L", "当日盈亏")}</th><th>{lang("Unrealized", "浮动盈亏")}</th><th>{lang("Allocation", "仓位占比")}</th></tr></thead>
    <tbody>{day_body}</tbody>
  </table>
  <h3>{lang("Risk Findings", "风险发现")}</h3>
  <ul>{risks_body}</ul>
  <h3>{lang("Checklist", "检查清单")}</h3>
  <ul>{checklist_body}</ul>
</section>
"""


def _summary_strip_html(report: DailyReport) -> str:
    if not report.analyses:
        return ""
    rows = "".join(_summary_strip_row(item) for item in report.analyses)
    heading = lang("At a Glance", "一览")
    note = lang(
        "Score, change, benchmark verdict, and risk for every tracked symbol in one scan.",
        "一次扫描即可看到每个标的的评分、涨跌幅、基准对比结论与风险等级。",
    )
    return f'<h2>{heading}</h2><p class="section-note">{note}</p><section class="strip-grid">{rows}</section>'


def _summary_strip_row(item: AssetAnalysis) -> str:
    symbol = html.escape(item.asset.symbol)
    change_class = _value_class(item.snapshot.change_pct)
    score_class = _score_class(item.signal.score)
    benchmark_badge = _verdict_badge(item.benchmark.verdict) if item.benchmark else ""
    return f"""
<div class="strip-row" data-symbol="{symbol}" data-risk="{html.escape(item.signal.risk_level)}">
  <div class="strip-top">
    <span class="strip-symbol">{symbol}</span>
    <span class="strip-score {score_class}">{item.signal.score}</span>
  </div>
  <div class="{change_class}">{html.escape(_pct(item.snapshot.change_pct))}</div>
  <div class="strip-meta">
    {benchmark_badge}
    <span class="pill risk-{html.escape(item.signal.risk_level)}">{lang("risk", "风险")} {html.escape(item.signal.risk_level)}</span>
  </div>
</div>
"""


def _html_card(item: AssetAnalysis, history: list[HistoryPoint]) -> str:
    reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in item.signal.reasons)
    warnings = "".join(f"<li>{html.escape(warning)}</li>" for warning in item.warnings)
    news = "".join(
        f'<li><a href="{html.escape(_safe_href(news_item.link))}">{html.escape(news_item.title)}</a></li>'
        for news_item in item.news[:4]
    )
    ai = f"<p>{html.escape(item.ai_summary)}</p>" if item.ai_summary else ""
    warning_block = f"<h3>{lang('Warnings', '警告')}</h3><ul>{warnings}</ul>" if warnings else ""
    news_block = f"<h3>{lang('News', '新闻')}</h3><ul>{news}</ul>" if news else ""
    position = _position_html(item)
    benchmark = _benchmark_html(item)
    freshness = _freshness_html(item.freshness)
    sparkline = _sparkline(history)
    return f"""
<article class="card asset-card">
  <div class="top">
    <div>
      <div class="symbol">{html.escape(item.asset.symbol)}</div>
      <div class="muted">{html.escape(item.snapshot.name)}</div>
    </div>
    <div class="score">{item.signal.score}</div>
  </div>
  <p><span class="pill">{html.escape(item.signal.stance)}</span> <span class="pill">{lang("risk", "风险")} {html.escape(item.signal.risk_level)}</span></p>
  <p class="muted">{lang("Data source", "数据源")}: {html.escape(item.snapshot.source or "n/a")}</p>
  <dl>
    <div><dt>{lang("Close", "收盘价")}</dt><dd>{html.escape(_money(item.snapshot.last_close, item.snapshot.currency))}</dd></div>
    <div><dt>{lang("Change", "涨跌幅")}</dt><dd>{html.escape(_pct(item.snapshot.change_pct))}</dd></div>
    <div><dt>{lang("20D return", "20日收益")}</dt><dd>{html.escape(_pct(item.metrics.get("return_20d")))}</dd></div>
    <div><dt>RSI14</dt><dd>{html.escape(_num(item.metrics.get("rsi14")))}</dd></div>
  </dl>
  {freshness}
  {benchmark}
  {position}
  {sparkline}
  {ai}
  <h3>{lang("Reasons", "信号原因")}</h3>
  <ul>{reasons}</ul>
  {warning_block}
  {news_block}
</article>
"""


def _position_html(item: AssetAnalysis) -> str:
    if not item.position:
        return ""
    position = item.position
    day_class = _value_class(position.day_pnl)
    unrealized_class = _value_class(position.unrealized_pnl)
    return f"""
<dl>
  <div><dt>{lang("Position", "持仓")}</dt><dd>{position.quantity:g}</dd></div>
  <div><dt>{lang("Allocation", "仓位占比")}</dt><dd>{html.escape(_pct(position.allocation_pct))}</dd></div>
  <div><dt>{lang("Market value", "市值")}</dt><dd>{html.escape(_money(position.market_value, position.currency))}</dd></div>
  <div><dt>{lang("Day P/L", "当日盈亏")}</dt><dd class="{day_class}">{html.escape(_signed_money(position.day_pnl, position.currency))}</dd></div>
  <div><dt>{lang("Cost basis", "成本价")}</dt><dd>{html.escape(_money(position.cost_basis, position.currency))}</dd></div>
  <div><dt>{lang("Unrealized", "浮动盈亏")}</dt><dd class="{unrealized_class}">{html.escape(_signed_money(position.unrealized_pnl, position.currency))}</dd></div>
</dl>
"""


def _benchmark_html(item: AssetAnalysis) -> str:
    if not item.benchmark:
        return f'<p class="muted">{lang("Benchmark", "基准")}: n/a</p>'
    benchmark = item.benchmark
    return f"""
<dl>
  <div><dt>{lang("Benchmark", "基准")}</dt><dd>{html.escape(benchmark.symbol)}</dd></div>
  <div><dt>{lang("Relative strength", "相对强弱")}</dt><dd>{_verdict_badge(benchmark.verdict)}</dd></div>
  <div><dt>{lang("Rel 20D", "相对20日")}</dt><dd class="{_value_class(benchmark.relative_return_20d)}">{html.escape(_pct(benchmark.relative_return_20d))}</dd></div>
  <div><dt>{lang("Rel 60D", "相对60日")}</dt><dd class="{_value_class(benchmark.relative_return_60d)}">{html.escape(_pct(benchmark.relative_return_60d))}</dd></div>
</dl>
"""


def _freshness_html(freshness: DataFreshness | None) -> str:
    if not freshness:
        return ""
    age = "n/a" if freshness.age_days is None else str(freshness.age_days)
    return f"""
<dl>
  <div><dt>{lang("Latest trading day", "最新交易日")}</dt><dd>{html.escape(freshness.latest_date or "n/a")}</dd></div>
  <div><dt>{lang("Data freshness", "数据新鲜度")}</dt><dd>{_freshness_badge(freshness)} <span class="muted">({html.escape(age)}d)</span></dd></div>
</dl>
"""


def _sparkline(history: list[HistoryPoint]) -> str:
    if len(history) < 2:
        return ""
    values = [float(point.score) for point in history]
    min_value = min(values)
    max_value = max(values)
    spread = max(max_value - min_value, 1)
    width = 240
    height = 42
    step = width / max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = index * step
        y = height - ((value - min_value) / spread * (height - 8)) - 4
        points.append(f"{x:.1f},{y:.1f}")
    label = f"Score trend {values[0]:.0f} to {values[-1]:.0f}"
    visible_label = lang(f"Score trend {values[0]:.0f} to {values[-1]:.0f}", f"评分趋势 {values[0]:.0f} 到 {values[-1]:.0f}")
    return f"""
<div class="muted">{visible_label}</div>
<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="{html.escape(label)}">
  <polyline fill="none" stroke="var(--brand)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="{' '.join(points)}" />
</svg>
"""


def _freshness_plain(freshness: DataFreshness | None) -> str:
    if not freshness:
        return "n/a"
    age = "n/a" if freshness.age_days is None else f"{freshness.age_days}d"
    return f"{freshness.status}, latest {freshness.latest_date or 'n/a'}, source {freshness.source or 'n/a'}, age {age}"


def _freshness_badge(freshness: DataFreshness | None) -> str:
    if not freshness:
        return ""
    css = {
        "fresh": "gain",
        "stale": "loss",
        "missing": "loss",
        "unknown": "risk-medium",
    }.get(freshness.status, "")
    label = {
        "fresh": lang("fresh", "新鲜"),
        "stale": lang("stale", "滞后"),
        "missing": lang("missing", "缺失"),
        "unknown": lang("unknown", "未知"),
    }.get(freshness.status, html.escape(freshness.status))
    return f'<span class="pill {css}" title="{html.escape(freshness.message)}">{label}</span>'


def _verdict_badge(verdict: str) -> str:
    css = {
        "outperforming": "gain",
        "underperforming": "loss",
        "tracking": "",
        "mixed": "risk-medium",
        "unknown": "",
    }.get(verdict, "")
    label = {
        "outperforming": lang("outperforming", "跑赢"),
        "underperforming": lang("underperforming", "跑输"),
        "tracking": lang("tracking", "贴近基准"),
        "mixed": lang("mixed", "分化"),
        "unknown": lang("unknown", "未知"),
    }.get(verdict, html.escape(verdict))
    return f'<span class="pill {css}">{label}</span>'


def _money(value: float | int | str | None, currency: str | None = None) -> str:
    if value in (None, ""):
        return "n/a"
    prefix = f"{currency} " if currency else ""
    try:
        return f"{prefix}{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _signed_money(value: float | int | str | None, currency: str | None = None) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    prefix = f"{currency} " if currency else ""
    sign = "+" if number > 0 else ""
    return f"{prefix}{sign}{number:,.2f}"


def _pct(value: float | int | str | None) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _num(value: float | int | str | None) -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _value_class(value: float | int | str | None) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number > 0:
        return "gain"
    if number < 0:
        return "loss"
    return ""


def _score_class(score: int) -> str:
    if score >= 70:
        return "gain"
    if score >= 40:
        return "risk-medium"
    return "loss"


def _trend_text(values: list[int]) -> str:
    if not values:
        return "n/a"
    return " -> ".join(str(value) for value in values[-6:])


def _safe_href(url: str | None) -> str:
    if url is None:
        return "#"
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return "#"
    if re.search(r"[)\s\x00-\x1f]", url):
        return "#"
    return url


def _md_escape(text: object) -> str:
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
