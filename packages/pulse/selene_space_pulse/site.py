from __future__ import annotations

import html
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ui import lang, language_boot_script, language_runtime_script, language_toggle, ui_styles


REPORT_PATTERN = re.compile(r"market-pulse-(\d{8})-(\d{4})\.html$")


@dataclass(frozen=True)
class SiteReport:
    source: Path
    href: str
    title: str
    generated_at: datetime | None


@dataclass(frozen=True)
class SiteBuildResult:
    index_path: Path
    dashboard_path: Path | None
    reports: list[SiteReport]


def build_site(
    reports_dir: str | Path,
    output_dir: str | Path,
    title: str = "AI Market Pulse",
    keep_reports: int = 30,
) -> SiteBuildResult:
    reports_path = Path(reports_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    site_reports_dir = output_path / "reports"
    site_reports_dir.mkdir(parents=True, exist_ok=True)

    reports = _discover_reports(reports_path)[:keep_reports]
    copied_reports = _copy_reports(reports, site_reports_dir)
    dashboard_path = _copy_dashboard(reports_path, output_path)
    index_path = output_path / "index.html"
    index_path.write_text(
        render_site_index(title=title, reports=copied_reports, dashboard_path=dashboard_path),
        encoding="utf-8",
    )
    return SiteBuildResult(index_path=index_path, dashboard_path=dashboard_path, reports=copied_reports)


def render_site_index(
    title: str,
    reports: list[SiteReport],
    dashboard_path: Path | None,
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now()
    latest = reports[0] if reports else None
    dashboard_href = dashboard_path.name if dashboard_path else None
    latest_card = _link_card(
        "Latest Report",
        "最新报告",
        latest.title if latest else "No report yet",
        latest.title if latest else "暂无报告",
        latest.href if latest else None,
        "Open the newest generated daily research note.",
        "打开最新生成的每日研究简报。",
    )
    dashboard_card = _link_card(
        "Dashboard",
        "仪表盘",
        "Portfolio, risk, and trend view",
        "组合、风险与趋势视图",
        dashboard_href,
        "Review net value curves, score changes, risk board, and contribution board.",
        "查看净值曲线、评分变化、风险榜和收益贡献榜。",
    )
    archive_rows = "\n".join(_report_row(report) for report in reports)
    generated = generated_at.strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="en" data-lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  {language_boot_script("en")}
  <style>
    {ui_styles()}
    .site-hero {{ padding-bottom: 36px; }}
    .data-visual {{ display: grid; gap: 10px; }}
    .visual-row {{ display: grid; grid-template-columns: 70px 1fr 42px; gap: 10px; align-items: center; color: #d9e5df; font-size: 12px; }}
    .bar {{ height: 8px; border-radius: 999px; background: rgba(255,255,255,0.12); overflow: hidden; }}
    .bar span {{ display: block; height: 100%; border-radius: inherit; background: var(--brand); }}
    .bar.amber span {{ background: var(--amber); }}
    .bar.indigo span {{ background: var(--indigo); }}
    .grid.primary-links {{ margin-top: 6px; }}
    .card {{ min-height: 156px; display: flex; flex-direction: column; justify-content: space-between; }}
    .card h3 {{ color: var(--muted); }}
    .card strong {{ display: block; margin: 10px 0; font-size: clamp(23px, 3vw, 30px); line-height: 1.12; }}
    .actions {{ margin-top: 14px; }}
    .empty {{ padding: 18px; background: var(--canvas); border: 1px solid var(--line); border-radius: 8px; }}
    footer {{ margin-top: 30px; color: var(--muted); font-size: 13px; }}
  </style>
</head>
<body>
<main>
  <nav class="topbar">
    <div class="brand-mark">AI Market Pulse</div>
    {language_toggle()}
  </nav>
  <header class="hero site-hero">
    <div>
      <div class="eyebrow">{lang("Quant Research Site", "量化研究站点")}</div>
      <h1>{html.escape(title)}</h1>
      <p>{lang("Daily AI-assisted quant research, signal review, portfolio risk monitoring, and static publishing. No server required.", "每日 AI 辅助量化研究、信号复盘、组合风控与静态发布。无需服务器。")}</p>
      <p class="fineprint">{lang("Updated", "更新时间")}: {html.escape(generated)}</p>
    </div>
    <aside class="hero-panel data-visual" aria-label="Signal visual">
      <div class="visual-row"><span>{lang("Risk", "风险")}</span><div class="bar"><span style="width: 68%"></span></div><strong>{len(reports)}</strong></div>
      <div class="visual-row"><span>{lang("Pulse", "脉搏")}</span><div class="bar amber"><span style="width: 46%"></span></div><strong>AI</strong></div>
      <div class="visual-row"><span>{lang("Review", "复盘")}</span><div class="bar indigo"><span style="width: 82%"></span></div><strong>JSONL</strong></div>
    </aside>
  </header>
  <section class="grid primary-links" aria-label="Primary links">
    {dashboard_card}
    {latest_card}
  </section>
  <h2>{lang("Report Archive", "报告归档")}</h2>
  {_archive_table(archive_rows)}
  <footer>
    {lang("Quant research automation only. This site is not financial advice, does not connect to brokers, and does not place trades. Verify data, model output, corporate actions, and news before making decisions.", "仅用于量化研究自动化。本网站不构成投资建议，不连接券商，也不会下单交易。做决策前请核验行情、模型输出、公司行动与新闻。")}
  </footer>
</main>
{language_runtime_script()}
</body>
</html>
"""


def _discover_reports(reports_dir: Path) -> list[SiteReport]:
    reports: list[SiteReport] = []
    if not reports_dir.exists():
        return reports
    for path in reports_dir.glob("market-pulse-*.html"):
        generated = _parse_report_time(path.name)
        title = generated.strftime("%Y-%m-%d %H:%M") if generated else path.stem
        reports.append(SiteReport(source=path, href=f"reports/{path.name}", title=title, generated_at=generated))
    return sorted(reports, key=lambda report: report.generated_at or datetime.min, reverse=True)


def _copy_reports(reports: list[SiteReport], destination: Path) -> list[SiteReport]:
    copied: list[SiteReport] = []
    for report in reports:
        target = destination / report.source.name
        shutil.copy2(report.source, target)
        copied.append(SiteReport(source=target, href=f"reports/{target.name}", title=report.title, generated_at=report.generated_at))
    return copied


def _copy_dashboard(reports_dir: Path, output_dir: Path) -> Path | None:
    source = reports_dir / "dashboard.html"
    if not source.exists():
        return None
    target = output_dir / "dashboard.html"
    shutil.copy2(source, target)
    return target


def _parse_report_time(name: str) -> datetime | None:
    match = REPORT_PATTERN.match(name)
    if not match:
        return None
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M")


def _link_card(label: str, label_zh: str, heading: str, heading_zh: str, href: str | None, description: str, description_zh: str) -> str:
    action = (
        f'<a class="button" href="{html.escape(href)}">{lang("Open", "打开")}</a>'
        if href
        else f'<span class="muted">{lang("Not generated yet", "尚未生成")}</span>'
    )
    return f"""
<article class="card">
  <div>
    <h3>{lang(label, label_zh)}</h3>
    <strong>{lang(heading, heading_zh)}</strong>
    <p class="muted">{lang(description, description_zh)}</p>
  </div>
  <div class="actions">{action}</div>
</article>
"""


def _archive_table(rows: str) -> str:
    if not rows:
        return f'<div class="empty muted">{lang("No reports found. Run market-pulse run first.", "暂无报告。请先运行 market-pulse run。")}</div>'
    return f"""
<table>
  <thead><tr><th>{lang("Report", "报告")}</th><th>{lang("Generated", "生成时间")}</th><th>{lang("Link", "链接")}</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
"""


def _report_row(report: SiteReport) -> str:
    generated = report.generated_at.strftime("%Y-%m-%d %H:%M") if report.generated_at else "Unknown"
    return (
        "<tr>"
        f"<td>{html.escape(report.title)}</td>"
        f"<td>{html.escape(generated)}</td>"
        f'<td><a href="{html.escape(report.href)}">{lang("Open report", "打开报告")}</a></td>'
        "</tr>"
    )
