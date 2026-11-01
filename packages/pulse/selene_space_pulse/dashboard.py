from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from selene_space_types.stock import HistoryPoint
from .ui import lang, language_boot_script, language_runtime_script, language_toggle, ui_styles


RISK_RANK = {"high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class PortfolioSeries:
    currency: str
    points: list[tuple[str, float]]


@dataclass(frozen=True)
class SymbolTrend:
    symbol: str
    latest: HistoryPoint
    previous: HistoryPoint | None
    score_delta: int | None
    points: list[HistoryPoint]


@dataclass(frozen=True)
class DashboardData:
    generated_at: datetime
    records: list[HistoryPoint]
    portfolio_series: list[PortfolioSeries]
    symbol_trends: list[SymbolTrend]
    risk_leaders: list[SymbolTrend]
    contribution_leaders: list[SymbolTrend]


def build_dashboard_data(records: list[HistoryPoint], max_points: int = 90) -> DashboardData:
    cleaned = _dedupe(records)
    by_symbol = _group_by_symbol(cleaned)
    trends = [_symbol_trend(symbol, points[-max_points:]) for symbol, points in sorted(by_symbol.items())]
    risk_leaders = sorted(
        trends,
        key=lambda item: (RISK_RANK.get(item.latest.risk_level, 0), -item.latest.score),
        reverse=True,
    )[:10]
    contribution_leaders = sorted(
        [item for item in trends if item.latest.day_pnl is not None or item.latest.unrealized_pnl is not None],
        key=lambda item: (
            abs(item.latest.day_pnl or 0),
            abs(item.latest.unrealized_pnl or 0),
            item.latest.symbol,
        ),
        reverse=True,
    )[:10]
    return DashboardData(
        generated_at=datetime.now(),
        records=cleaned,
        portfolio_series=_portfolio_series(cleaned, max_points=max_points),
        symbol_trends=trends,
        risk_leaders=risk_leaders,
        contribution_leaders=contribution_leaders,
    )


def write_dashboard(records: list[HistoryPoint], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = build_dashboard_data(records)
    path.write_text(render_dashboard(data), encoding="utf-8")
    return path


def render_dashboard(data: DashboardData) -> str:
    title = "AI Market Pulse Dashboard"
    generated = data.generated_at.strftime("%Y-%m-%d %H:%M:%S")
    kpis = _kpis(data)
    portfolio_cards = "\n".join(_portfolio_card(series) for series in data.portfolio_series)
    score_cards = "\n".join(_score_card(trend) for trend in data.symbol_trends)
    risk_rows = "\n".join(_risk_row(trend) for trend in data.risk_leaders)
    contribution_rows = "\n".join(_contribution_row(trend) for trend in data.contribution_leaders)
    latest_rows = "\n".join(_latest_row(trend) for trend in data.symbol_trends)
    dashboard_json = _dashboard_json(data)
    return f"""<!doctype html>
<html lang="en" data-lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  {language_boot_script("en")}
  <style>
    {ui_styles()}
    .dashboard-table {{ margin-bottom: 22px; }}
    .hero-panel dl {{ margin: 0; display: grid; gap: 10px; }}
    .hero-panel dt {{ color: #a9b8b1; font-size: 12px; }}
    .hero-panel dd {{ margin: 0; font-weight: 760; color: #ffffff; }}
    .controls {{ margin: 18px 0 18px; display: grid; grid-template-columns: minmax(220px, 1.2fr) repeat(3, minmax(160px, 0.8fr)); gap: 12px; align-items: end; }}
    .field label {{ display: block; margin-bottom: 6px; color: var(--muted); font-size: 12px; font-weight: 760; }}
    .field input, .field select {{ width: 100%; min-height: 40px; border: 1px solid var(--line-strong); border-radius: 8px; padding: 8px 10px; background: var(--canvas); color: var(--ink); font: inherit; }}
    .tool-row {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin: 10px 0 18px; }}
    .ghost-button {{ min-height: 36px; border: 1px solid var(--line-strong); border-radius: 8px; padding: 7px 10px; background: var(--canvas); color: var(--ink); font: inherit; font-weight: 720; cursor: pointer; }}
    .detail-panel {{ margin: 18px 0 22px; display: grid; grid-template-columns: minmax(220px, 0.75fr) minmax(0, 1.25fr); gap: 14px; }}
    .detail-panel h3 {{ font-size: 18px; margin-bottom: 12px; }}
    .detail-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .detail-stat {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: var(--canvas-soft); }}
    .detail-stat span {{ display: block; color: var(--muted); font-size: 12px; font-weight: 720; }}
    .detail-stat strong {{ display: block; margin-top: 4px; font-size: 18px; }}
    .detail-chart {{ width: 100%; height: 130px; margin-top: 8px; }}
    .detail-list {{ margin: 10px 0 0; padding-left: 18px; }}
    .symbol-card {{ cursor: pointer; transition: transform 140ms ease, border-color 140ms ease; }}
    .symbol-card:hover {{ transform: translateY(-1px); border-color: var(--brand); }}
    .symbol-card[aria-selected="true"] {{ border-color: var(--brand); box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.14), var(--shadow); }}
    .hidden-by-filter {{ display: none !important; }}
    .row-action {{ border: 0; padding: 0; background: transparent; color: var(--brand-strong); font: inherit; font-weight: 760; cursor: pointer; }}
    .empty-state {{ margin: 10px 0 18px; }}
    @media (max-width: 900px) {{ .controls, .detail-panel {{ grid-template-columns: 1fr; }} }}
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
      <div class="eyebrow">{lang("Dashboard", "仪表盘")}</div>
      <h1>{lang("Portfolio Research Cockpit", "组合研究驾驶舱")}</h1>
      <p>{lang("Net value curves, signal drift, risk pressure, and contribution attribution from local JSONL history.", "从本地 JSONL 历史中生成组合净值曲线、信号漂移、风险压力和收益贡献归因。")}</p>
    </div>
    <aside class="hero-panel">
      <dl>
        <div><dt>{lang("Generated", "生成时间")}</dt><dd>{html.escape(generated)}</dd></div>
        <div><dt>{lang("History records", "历史记录")}</dt><dd>{len(data.records)}</dd></div>
        <div><dt>{lang("Use case", "用途")}</dt><dd>{lang("Research only", "仅供研究")}</dd></div>
      </dl>
    </aside>
  </header>
  <section class="grid" aria-label="Dashboard summary">
    {kpis}
  </section>
  <h2>{lang("Explore Symbols", "探索标的")}</h2>
  <p class="section-note">{lang("Filter by symbol, risk, relative strength, and history window. Click any card or table row for a focused drilldown.", "按代码、风险、相对强弱和历史窗口过滤。点击任意卡片或表格行查看单股详情。")}</p>
  {_controls_html()}
  {_detail_html()}
  <h2>{lang("Score Changes", "评分变化")}</h2>
  <p class="section-note">{lang("A compact view of signal momentum for each tracked symbol.", "快速观察每个标的的信号动量变化。")}</p>
  <div class="tool-row"><span class="muted" data-filter-count></span><button class="ghost-button" type="button" data-reset-filters>{lang("Reset filters", "重置筛选")}</button></div>
  <section class="grid" data-symbol-cards>
    {score_cards or f'<div class="panel muted">{lang("No score history yet.", "暂无评分历史。")}</div>'}
  </section>
  <h2>{lang("Portfolio Net Value", "组合净值")}</h2>
  <p class="section-note">{lang("Grouped by currency so mixed portfolios remain readable without hiding currency risk.", "按币种分组展示，让多币种组合在可读的同时保留汇率风险提示。")}</p>
  <section class="wide-grid">
    {portfolio_cards or f'<div class="panel muted">{lang("No portfolio market value history yet. Add quantity and cost_basis to assets, then run the report.", "暂无组合市值历史。给资产。给资产加入 quantity 和 cost_basis 后重新运行报告即可。")}</div>'}
  </section>
  <h2>{lang("Risk Board", "风险榜")}</h2>
  <table class="dashboard-table" data-latest-table>
    <thead><tr><th>{lang("Symbol", "代码")}</th><th>{lang("Risk", "风险")}</th><th>{lang("Score", "评分")}</th><th>{lang("Stance", "状态")}</th><th>{lang("Close", "收盘价")}</th><th>{lang("Change", "涨跌幅")}</th></tr></thead>
    <tbody>{risk_rows or f'<tr><td colspan="6" class="muted">{lang("No risk data.", "暂无风险数据。")}</td></tr>'}</tbody>
  </table>
  <h2>{lang("Contribution Board", "收益贡献榜")}</h2>
  <table class="dashboard-table">
    <thead><tr><th>{lang("Symbol", "代码")}</th><th>{lang("Currency", "币种")}</th><th>{lang("Day P/L", "当日盈亏")}</th><th>{lang("Unrealized P/L", "浮动盈亏")}</th><th>{lang("Unrealized %", "浮盈比例")}</th><th>{lang("Market Value", "市值")}</th></tr></thead>
    <tbody>{contribution_rows or f'<tr><td colspan="6" class="muted">{lang("No position data.", "暂无持仓数据。")}</td></tr>'}</tbody>
  </table>
  <h2>{lang("Latest Positions And Signals", "最新持仓与信号")}</h2>
  <table class="dashboard-table">
    <thead><tr><th>{lang("Symbol", "代码")}</th><th>{lang("Date", "日期")}</th><th>{lang("Close", "收盘价")}</th><th>{lang("Score", "评分")}</th><th>{lang("Delta", "变化")}</th><th>{lang("Risk", "风险")}</th><th>{lang("Benchmark", "基准")}</th><th>{lang("Rel 20D", "相对20日")}</th><th>{lang("Freshness", "新鲜度")}</th><th>{lang("Market Value", "市值")}</th></tr></thead>
    <tbody>{latest_rows or f'<tr><td colspan="10" class="muted">{lang("No history data.", "暂无历史数据。")}</td></tr>'}</tbody>
  </table>
</main>
{language_runtime_script()}
{_dashboard_runtime_script(dashboard_json)}
</body>
</html>
"""


def _dedupe(records: list[HistoryPoint]) -> list[HistoryPoint]:
    by_key: dict[tuple[str, str], HistoryPoint] = {}
    for record in records:
        if not record.symbol or not record.date:
            continue
        by_key[(record.symbol, record.date)] = record
    return sorted(by_key.values(), key=lambda item: (item.date, item.symbol))


def _group_by_symbol(records: list[HistoryPoint]) -> dict[str, list[HistoryPoint]]:
    grouped: dict[str, list[HistoryPoint]] = {}
    for record in records:
        grouped.setdefault(record.symbol, []).append(record)
    return {symbol: sorted(points, key=lambda item: item.date) for symbol, points in grouped.items()}


def _symbol_trend(symbol: str, points: list[HistoryPoint]) -> SymbolTrend:
    latest = points[-1]
    previous = points[-2] if len(points) >= 2 else None
    delta = latest.score - previous.score if previous else None
    return SymbolTrend(symbol=symbol, latest=latest, previous=previous, score_delta=delta, points=points)


def _portfolio_series(records: list[HistoryPoint], max_points: int) -> list[PortfolioSeries]:
    grouped: dict[str, dict[str, float]] = {}
    for record in records:
        if record.market_value is None:
            continue
        currency = record.currency or "UNKNOWN"
        grouped.setdefault(currency, {})
        grouped[currency][record.date] = grouped[currency].get(record.date, 0) + float(record.market_value)

    series: list[PortfolioSeries] = []
    for currency, by_date in sorted(grouped.items()):
        points = sorted(by_date.items())[-max_points:]
        series.append(PortfolioSeries(currency=currency, points=points))
    return series


def _kpis(data: DashboardData) -> str:
    latest = [trend.latest for trend in data.symbol_trends]
    portfolio_value = sum(point.market_value or 0 for point in latest)
    day_pnl = sum(point.day_pnl or 0 for point in latest)
    high_risk = sum(1 for point in latest if point.risk_level == "high")
    average_score = sum(point.score for point in latest) / len(latest) if latest else 0
    cards = [
        (lang("Tracked Symbols", "跟踪标的"), str(len(latest)), lang("Latest unique symbols in history", "历史中的最新唯一标的数量")),
        (lang("Portfolio Value", "组合市值"), _plain_money(portfolio_value), lang("Mixed currencies are summed for quick scanning", "多币种仅作快速扫描合计")),
        (lang("Day P/L", "当日盈亏"), _signed_plain(day_pnl), lang("Latest recorded contribution across positioned assets", "持仓资产的最新当日贡献")),
        (lang("Average Score", "平均评分"), f"{average_score:.1f}", lang(f"{high_risk} high-risk symbols", f"{high_risk} 个高风险标的")),
    ]
    return "\n".join(
        f'<div class="panel kpi"><span class="muted">{label}</span><strong>{html.escape(value)}</strong><div class="muted">{note}</div></div>'
        for label, value, note in cards
    )


def _controls_html() -> str:
    return f"""
<section class="panel controls" aria-label="Dashboard filters">
  <div class="field">
    <label for="symbol-filter">{lang("Symbol search", "代码搜索")}</label>
    <input id="symbol-filter" type="search" data-symbol-filter placeholder="AAPL / NVDA / 600519">
  </div>
  <div class="field">
    <label for="risk-filter">{lang("Risk level", "风险级别")}</label>
    <select id="risk-filter" data-risk-filter>
      <option value="all">All risks / 全部风险</option>
      <option value="high">high</option>
      <option value="medium">medium</option>
      <option value="low">low</option>
    </select>
  </div>
  <div class="field">
    <label for="relative-filter">{lang("Relative strength", "相对强弱")}</label>
    <select id="relative-filter" data-relative-filter>
      <option value="all">All / 全部</option>
      <option value="outperforming">outperforming / 跑赢</option>
      <option value="underperforming">underperforming / 跑输</option>
      <option value="tracking">tracking / 贴近基准</option>
      <option value="mixed">mixed / 分化</option>
      <option value="unknown">unknown / 未知</option>
    </select>
  </div>
  <div class="field">
    <label for="range-filter">{lang("History window", "历史窗口")}</label>
    <select id="range-filter" data-range-filter>
      <option value="all">All history / 全部历史</option>
      <option value="7">7D</option>
      <option value="30">30D</option>
      <option value="90">90D</option>
    </select>
  </div>
</section>
"""


def _detail_html() -> str:
    return f"""
<section class="detail-panel" aria-label="Symbol detail">
  <article class="panel" data-detail-summary>
    <h3>{lang("Symbol Detail", "单股详情")}</h3>
    <p class="muted">{lang("Select a symbol card or row to inspect score drift, benchmark strength, freshness, and position impact.", "选择标的卡片或表格行，查看评分漂移、基准强弱、新鲜度和持仓影响。")}</p>
  </article>
  <article class="panel" data-detail-chart>
    <h3>{lang("History Lens", "历史透镜")}</h3>
    <p class="muted">{lang("The chart updates with the selected history window.", "图表会随选择的历史窗口更新。")}</p>
  </article>
</section>
"""


def _portfolio_card(series: PortfolioSeries) -> str:
    latest_value = series.points[-1][1] if series.points else 0
    first_value = series.points[0][1] if series.points else 0
    delta = latest_value - first_value
    delta_pct = delta / first_value if first_value else None
    return f"""
<article class="panel">
  <h3>{html.escape(series.currency)} {lang("Net Value", "净值")}</h3>
  <div class="metric"><span>{lang("Latest", "最新")}</span><strong>{html.escape(_money(latest_value, series.currency))}</strong></div>
  <div class="metric"><span>{lang("Period change", "区间变化")}</span><strong class="{_value_class(delta)}">{html.escape(_signed_money(delta, series.currency))} ({html.escape(_pct(delta_pct))})</strong></div>
  {_line_chart(series.points, height=90)}
</article>
"""


def _score_card(trend: SymbolTrend) -> str:
    delta = trend.score_delta
    delta_text = "n/a" if delta is None else f"{delta:+d}"
    relative = _relative_bucket(trend.latest.relative_return_20d, trend.latest.relative_return_60d)
    return f"""
<article class="panel symbol-card" tabindex="0" role="button" aria-selected="false" data-symbol-card data-symbol="{html.escape(trend.symbol)}" data-risk="{html.escape(trend.latest.risk_level)}" data-relative="{html.escape(relative)}" data-date="{html.escape(trend.latest.date)}">
  <h3>{html.escape(trend.symbol)}</h3>
  <div class="metric"><span>{lang("Latest score", "最新评分")}</span><strong>{trend.latest.score}</strong></div>
  <div class="metric"><span>{lang("Score delta", "评分变化")}</span><strong class="{_value_class(delta)}">{html.escape(delta_text)}</strong></div>
  <div class="metric"><span>{lang("Risk", "风险")}</span><strong class="risk-{html.escape(trend.latest.risk_level)}">{html.escape(trend.latest.risk_level)}</strong></div>
  <div class="metric"><span>{lang("Benchmark", "基准")}</span><strong>{html.escape(trend.latest.benchmark_symbol or "n/a")}</strong></div>
  <div class="metric"><span>{lang("Rel 20D", "相对20日")}</span><strong class="{_value_class(trend.latest.relative_return_20d)}">{html.escape(_pct(trend.latest.relative_return_20d))}</strong></div>
  {_score_chart(trend.points)}
</article>
"""


def _risk_row(trend: SymbolTrend) -> str:
    point = trend.latest
    return (
        f"<tr {_row_data_attrs(trend)}>"
        f"<td>{html.escape(trend.symbol)}</td>"
        f'<td class="risk-{html.escape(point.risk_level)}">{html.escape(point.risk_level)}</td>'
        f"<td>{point.score}</td>"
        f"<td>{html.escape(point.stance)}</td>"
        f"<td>{html.escape(_money(point.close, point.currency))}</td>"
        f"<td class=\"{_value_class(point.change_pct)}\">{html.escape(_pct(point.change_pct))}</td>"
        "</tr>"
    )


def _contribution_row(trend: SymbolTrend) -> str:
    point = trend.latest
    return (
        f"<tr {_row_data_attrs(trend)}>"
        f"<td>{html.escape(trend.symbol)}</td>"
        f"<td>{html.escape(point.currency or 'n/a')}</td>"
        f"<td class=\"{_value_class(point.day_pnl)}\">{html.escape(_signed_money(point.day_pnl, point.currency))}</td>"
        f"<td class=\"{_value_class(point.unrealized_pnl)}\">{html.escape(_signed_money(point.unrealized_pnl, point.currency))}</td>"
        f"<td class=\"{_value_class(point.unrealized_pnl_pct)}\">{html.escape(_pct(point.unrealized_pnl_pct))}</td>"
        f"<td>{html.escape(_money(point.market_value, point.currency))}</td>"
        "</tr>"
    )


def _latest_row(trend: SymbolTrend) -> str:
    point = trend.latest
    delta = "n/a" if trend.score_delta is None else f"{trend.score_delta:+d}"
    return (
        f"<tr {_row_data_attrs(trend)}>"
        f'<td><button class="row-action" type="button" data-symbol-button="{html.escape(trend.symbol)}">{html.escape(trend.symbol)}</button></td>'
        f"<td>{html.escape(point.date)}</td>"
        f"<td>{html.escape(_money(point.close, point.currency))}</td>"
        f"<td>{point.score}</td>"
        f"<td class=\"{_value_class(trend.score_delta)}\">{html.escape(delta)}</td>"
        f'<td class="risk-{html.escape(point.risk_level)}">{html.escape(point.risk_level)}</td>'
        f"<td>{html.escape(point.benchmark_symbol or 'n/a')}</td>"
        f"<td class=\"{_value_class(point.relative_return_20d)}\">{html.escape(_pct(point.relative_return_20d))}</td>"
        f"<td>{_freshness_label(point)}</td>"
        f"<td>{html.escape(_money(point.market_value, point.currency))}</td>"
        "</tr>"
    )


def _row_data_attrs(trend: SymbolTrend) -> str:
    point = trend.latest
    relative = _relative_bucket(point.relative_return_20d, point.relative_return_60d)
    return (
        f'data-symbol-row data-symbol="{html.escape(trend.symbol)}" '
        f'data-risk="{html.escape(point.risk_level)}" '
        f'data-relative="{html.escape(relative)}" '
        f'data-date="{html.escape(point.date)}"'
    )


def _line_chart(points: list[tuple[str, float]], height: int) -> str:
    if len(points) < 2:
        return f'<div class="muted">{lang("Need at least two history points for a curve.", "至少需要两个历史点才能生成曲线。")}</div>'
    values = [value for _, value in points]
    polyline = _polyline(values, width=520, height=height)
    labels = f"{points[0][0]} to {points[-1][0]}"
    return f"""
<svg class="spark" viewBox="0 0 520 {height}" role="img" aria-label="{html.escape(labels)}">
  <line x1="0" y1="{height - 4}" x2="520" y2="{height - 4}" stroke="var(--border)" />
  <polyline fill="none" stroke="var(--brand)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="{polyline}" />
</svg>
<div class="muted">{html.escape(labels)}</div>
"""


def _score_chart(points: list[HistoryPoint]) -> str:
    if len(points) < 2:
        return f'<div class="muted">{lang("Need at least two points.", "至少需要两个历史点。")}</div>'
    values = [point.score for point in points]
    polyline = _polyline(values, width=240, height=44, fixed_min=0, fixed_max=100)
    return f"""
<svg class="score-spark" viewBox="0 0 240 44" role="img" aria-label="Score trend">
  <polyline fill="none" stroke="var(--brand)" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" points="{polyline}" />
</svg>
"""


def _polyline(
    values: list[float | int],
    width: int,
    height: int,
    fixed_min: float | None = None,
    fixed_max: float | None = None,
) -> str:
    min_value = float(fixed_min if fixed_min is not None else min(values))
    max_value = float(fixed_max if fixed_max is not None else max(values))
    spread = max(max_value - min_value, 1)
    step = width / max(len(values) - 1, 1)
    points = []
    for index, value in enumerate(values):
        x = index * step
        y = height - ((float(value) - min_value) / spread * (height - 8)) - 4
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def _dashboard_json(data: DashboardData) -> str:
    payload = {
        "symbols": {
            trend.symbol: {
                "symbol": trend.symbol,
                "latest": trend.latest.model_dump(),
                "previous": trend.previous.model_dump() if trend.previous else None,
                "scoreDelta": trend.score_delta,
                "relativeBucket": _relative_bucket(trend.latest.relative_return_20d, trend.latest.relative_return_60d),
                "points": [point.model_dump() for point in trend.points],
            }
            for trend in data.symbol_trends
        }
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")


def _dashboard_runtime_script(payload: str) -> str:
    return f"""
<script type="application/json" id="dashboard-data">{payload}</script>
<script>
  (function () {{
    var data = JSON.parse(document.getElementById("dashboard-data").textContent || "{{}}");
    var symbols = data.symbols || {{}};
    var selectedSymbol = Object.keys(symbols)[0] || null;
    var searchInput = document.querySelector("[data-symbol-filter]");
    var riskSelect = document.querySelector("[data-risk-filter]");
    var relativeSelect = document.querySelector("[data-relative-filter]");
    var rangeSelect = document.querySelector("[data-range-filter]");
    var resetButton = document.querySelector("[data-reset-filters]");
    var countTarget = document.querySelector("[data-filter-count]");
    var summaryTarget = document.querySelector("[data-detail-summary]");
    var chartTarget = document.querySelector("[data-detail-chart]");
    var cards = Array.prototype.slice.call(document.querySelectorAll("[data-symbol-card]"));
    var rows = Array.prototype.slice.call(document.querySelectorAll("[data-symbol-row]"));

    function L(en, zh) {{
      return '<span data-i18n-en>' + esc(en) + '</span><span data-i18n-zh>' + esc(zh) + '</span>';
    }}
    function esc(value) {{
      return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {{
        return {{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[char];
      }});
    }}
    function pct(value) {{
      return value == null || value === "" ? "n/a" : (Number(value) * 100).toFixed(2) + "%";
    }}
    function money(value, currency) {{
      if (value == null || value === "") return "n/a";
      return (currency ? currency + " " : "") + Number(value).toLocaleString(undefined, {{ maximumFractionDigits: 2, minimumFractionDigits: 2 }});
    }}
    function valueClass(value) {{
      if (value == null || value === "") return "";
      return Number(value) > 0 ? "gain" : Number(value) < 0 ? "loss" : "";
    }}
    function dateMs(date) {{
      var parsed = Date.parse(String(date || "") + "T00:00:00");
      return Number.isFinite(parsed) ? parsed : 0;
    }}
    function latestTime() {{
      var values = [];
      Object.keys(symbols).forEach(function (symbol) {{
        (symbols[symbol].points || []).forEach(function (point) {{ values.push(dateMs(point.date)); }});
      }});
      return values.length ? Math.max.apply(null, values) : 0;
    }}
    function minTime() {{
      var range = rangeSelect ? rangeSelect.value : "all";
      if (range === "all") return -Infinity;
      return latestTime() - Number(range) * 86400000;
    }}
    function scopedPoints(symbol) {{
      var floor = minTime();
      return ((symbols[symbol] || {{}}).points || []).filter(function (point) {{ return dateMs(point.date) >= floor; }});
    }}
    function visibleByFilters(meta) {{
      var query = (searchInput ? searchInput.value : "").trim().toUpperCase();
      var risk = riskSelect ? riskSelect.value : "all";
      var relative = relativeSelect ? relativeSelect.value : "all";
      if (query && meta.symbol.toUpperCase().indexOf(query) === -1) return false;
      if (risk !== "all" && meta.latest.risk_level !== risk) return false;
      if (relative !== "all" && meta.relativeBucket !== relative) return false;
      return scopedPoints(meta.symbol).length > 0;
    }}
    function applyFilters() {{
      var visible = [];
      Object.keys(symbols).forEach(function (symbol) {{
        var meta = symbols[symbol];
        var show = visibleByFilters(meta);
        if (show) visible.push(symbol);
      }});
      cards.forEach(function (card) {{
        card.classList.toggle("hidden-by-filter", visible.indexOf(card.dataset.symbol) === -1);
        card.setAttribute("aria-selected", card.dataset.symbol === selectedSymbol ? "true" : "false");
      }});
      rows.forEach(function (row) {{
        row.classList.toggle("hidden-by-filter", visible.indexOf(row.dataset.symbol) === -1);
      }});
      if (visible.indexOf(selectedSymbol) === -1) selectedSymbol = visible[0] || null;
      if (countTarget) countTarget.innerHTML = L(visible.length + " visible symbols", visible.length + " 个可见标的");
      renderDetail();
    }}
    function selectSymbol(symbol) {{
      selectedSymbol = symbol;
      applyFilters();
    }}
    function renderDetail() {{
      cards.forEach(function (card) {{
        card.setAttribute("aria-selected", card.dataset.symbol === selectedSymbol ? "true" : "false");
      }});
      if (!selectedSymbol || !symbols[selectedSymbol]) {{
        summaryTarget.innerHTML = '<h3>' + L("No symbol selected", "未选择标的") + '</h3><p class="muted">' + L("Adjust filters or reset them to restore symbols.", "调整筛选或重置筛选以恢复标的。") + '</p>';
        chartTarget.innerHTML = '<h3>' + L("History Lens", "历史透镜") + '</h3><p class="muted">n/a</p>';
        return;
      }}
      var meta = symbols[selectedSymbol];
      var latest = meta.latest || {{}};
      var points = scopedPoints(selectedSymbol);
      var first = points[0] || latest;
      var last = points[points.length - 1] || latest;
      var closeChange = first.close ? (Number(last.close) / Number(first.close) - 1) : null;
      summaryTarget.innerHTML =
        '<h3>' + esc(selectedSymbol) + '</h3>' +
        '<div class="detail-grid">' +
          stat(L("Close", "收盘价"), money(latest.close, latest.currency), "") +
          stat(L("Score", "评分"), esc(latest.score), "") +
          stat(L("Risk", "风险"), esc(latest.risk_level || "n/a"), "risk-" + esc(latest.risk_level || "")) +
          stat(L("Benchmark", "基准"), esc(latest.benchmark_symbol || "n/a"), "") +
          stat(L("Rel 20D", "相对20日"), pct(latest.relative_return_20d), valueClass(latest.relative_return_20d)) +
          stat(L("Window close change", "窗口价格变化"), pct(closeChange), valueClass(closeChange)) +
          stat(L("Freshness", "新鲜度"), freshness(latest), "") +
          stat(L("Market value", "市值"), money(latest.market_value, latest.currency), "") +
        '</div>';
      chartTarget.innerHTML =
        '<h3>' + L("History Lens", "历史透镜") + '</h3>' +
        chart(points) +
        '<ul class="detail-list">' +
          '<li>' + L("Window", "窗口") + ': ' + esc((first.date || "n/a") + " to " + (last.date || "n/a")) + '</li>' +
          '<li>' + L("Score drift", "评分漂移") + ': ' + esc(first.score == null || last.score == null ? "n/a" : String(Number(last.score) - Number(first.score))) + '</li>' +
          '<li>' + L("Relative bucket", "相对强弱分组") + ': ' + esc(meta.relativeBucket || "unknown") + '</li>' +
        '</ul>';
    }}
    function stat(label, value, css) {{
      return '<div class="detail-stat"><span>' + label + '</span><strong class="' + esc(css || "") + '">' + value + '</strong></div>';
    }}
    function freshness(point) {{
      var status = point.freshness_status || "n/a";
      var age = point.data_age_days == null ? "n/a" : point.data_age_days + "d";
      return esc(status + " / " + (point.latest_data_date || "n/a") + " / " + age);
    }}
    function chart(points) {{
      if (!points || points.length < 2) return '<p class="muted">' + L("Need at least two points.", "至少需要两个历史点。") + '</p>';
      var values = points.map(function (point) {{ return Number(point.score || 0); }});
      var min = Math.min.apply(null, values.concat([0]));
      var max = Math.max.apply(null, values.concat([100]));
      var spread = Math.max(max - min, 1);
      var width = 520;
      var height = 130;
      var coords = values.map(function (value, index) {{
        var x = values.length === 1 ? 0 : index * (width / (values.length - 1));
        var y = height - ((value - min) / spread * (height - 12)) - 6;
        return x.toFixed(1) + "," + y.toFixed(1);
      }}).join(" ");
      return '<svg class="detail-chart" viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Score detail">' +
        '<line x1="0" y1="' + (height - 6) + '" x2="' + width + '" y2="' + (height - 6) + '" stroke="var(--line)" />' +
        '<polyline fill="none" stroke="var(--brand)" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" points="' + coords + '" />' +
        '</svg>';
    }}
    cards.forEach(function (card) {{
      card.addEventListener("click", function () {{ selectSymbol(card.dataset.symbol); }});
      card.addEventListener("keydown", function (event) {{
        if (event.key === "Enter" || event.key === " ") {{
          event.preventDefault();
          selectSymbol(card.dataset.symbol);
        }}
      }});
    }});
    rows.forEach(function (row) {{
      row.addEventListener("click", function () {{ selectSymbol(row.dataset.symbol); }});
    }});
    [searchInput, riskSelect, relativeSelect, rangeSelect].forEach(function (control) {{
      if (control) control.addEventListener("input", applyFilters);
      if (control) control.addEventListener("change", applyFilters);
    }});
    if (resetButton) resetButton.addEventListener("click", function () {{
      if (searchInput) searchInput.value = "";
      if (riskSelect) riskSelect.value = "all";
      if (relativeSelect) relativeSelect.value = "all";
      if (rangeSelect) rangeSelect.value = "all";
      selectedSymbol = Object.keys(symbols)[0] || null;
      applyFilters();
    }});
    applyFilters();
  }}());
</script>
"""


def _relative_bucket(relative_20d: float | int | None, relative_60d: float | int | None) -> str:
    values = [float(value) for value in [relative_20d, relative_60d] if value is not None]
    if not values:
        return "unknown"
    positive = sum(1 for value in values if value >= 0.02)
    negative = sum(1 for value in values if value <= -0.02)
    if positive and not negative:
        return "outperforming"
    if negative and not positive:
        return "underperforming"
    if all(abs(value) < 0.02 for value in values):
        return "tracking"
    return "mixed"


def _plain_money(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):,.2f}"


def _money(value: float | int | None, currency: str | None = None) -> str:
    if value is None:
        return "n/a"
    prefix = f"{currency} " if currency else ""
    return f"{prefix}{float(value):,.2f}"


def _signed_plain(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    number = float(value)
    sign = "+" if number > 0 else ""
    return f"{sign}{number:,.2f}"


def _signed_money(value: float | int | None, currency: str | None = None) -> str:
    if value is None:
        return "n/a"
    prefix = f"{currency} " if currency else ""
    return prefix + _signed_plain(value)


def _pct(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.2f}%"


def _value_class(value: float | int | None) -> str:
    if value is None:
        return ""
    number = float(value)
    if number > 0:
        return "gain"
    if number < 0:
        return "loss"
    return ""


def _freshness_label(point: HistoryPoint) -> str:
    status = point.freshness_status or "n/a"
    css = {
        "fresh": "gain",
        "stale": "loss",
        "missing": "loss",
        "unknown": "risk-medium",
    }.get(status, "")
    label = {
        "fresh": lang("fresh", "新鲜"),
        "stale": lang("stale", "滞后"),
        "missing": lang("missing", "缺失"),
        "unknown": lang("unknown", "未知"),
    }.get(status, html.escape(status))
    date = point.latest_data_date or "n/a"
    age = "n/a" if point.data_age_days is None else f"{point.data_age_days}d"
    return f'<span class="pill {css}">{label}</span> <span class="muted">{html.escape(date)} / {html.escape(age)}</span>'
