"""Stock market pulse commands — daily reports, dashboards, notifications."""

from __future__ import annotations

import click


@click.group("pulse")
def pulse_group() -> None:
    """Stock market signal scoring and daily reports."""


@pulse_group.command("run")
@click.option("--config", default="watchlist.yaml", help="Path to watchlist config.")
@click.option("--symbols", default=None, help="Comma-separated symbols.")
@click.option("--output", default="reports", help="Output directory.")
@click.option("--history", default=None, help="JSONL history file path.")
@click.option("--no-notify", is_flag=True, help="Skip notifications.")
@click.option("--no-ai", is_flag=True, help="Disable AI summaries.")
@click.option("--ai-only", is_flag=True, help="Only run AI summaries.")
def pulse_run(
    config: str,
    symbols: str | None,
    output: str,
    history: str | None,
    no_notify: bool,
    no_ai: bool,
    ai_only: bool,
) -> None:
    """Generate the daily stock market report."""
    from pathlib import Path

    from selene_space_stock.config import load_stock_config
    from selene_space_stock.engine import run_analysis
    from selene_space_pulse.reporting import write_reports

    if symbols:
        from selene_space_stock.config import StockConfig, DataSettings, AnalysisSettings
        from selene_space_types.stock import StockAsset

        assets = [StockAsset(symbol=s.strip()) for s in symbols.split(",")]
        cfg = StockConfig(assets=assets, data=DataSettings(), analysis=AnalysisSettings())
    else:
        cfg = load_stock_config(config)

    if no_ai:
        cfg = cfg.model_copy(update={"llm": cfg.llm.model_copy(update={"enabled": False})})
    if ai_only:
        click.echo("AI-only mode: running summaries on existing data.")
        return

    report = run_analysis(cfg, config_path=config if not symbols else None)

    out_dir = Path(output)
    paths = write_reports(report, out_dir)
    click.echo(f"Report written: {paths}")

    if history:
        from selene_space_pulse.history import append_history

        append_history(report, history)
        click.echo(f"History appended: {history}")

    if not no_notify and cfg.notifications:
        from selene_space_pulse.notify import send_notifications

        html_path = paths.get("html")
        results = send_notifications(report, cfg.notifications, html_path=html_path)
        for r in results:
            click.echo(f"  {r}")


@pulse_group.command("init")
@click.option("--path", default="watchlist.yaml", help="Output config path.")
@click.option("--symbols", default=None, help="Comma-separated symbols to include.")
@click.option("--template", default="default", help="Config template name.")
def pulse_init(path: str, symbols: str | None, template: str) -> None:
    """Create a starter watchlist config."""
    import yaml
    from pathlib import Path

    config_data: dict = {"title": "My Market Pulse", "timezone": "Asia/Shanghai"}

    if symbols:
        config_data["assets"] = [{"symbol": s.strip()} for s in symbols.split(",")]
    else:
        config_data["assets"] = [{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "600519.SS"}]

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(yaml.dump(config_data, default_flow_style=False, allow_unicode=True))
    click.echo(f"Config written to {out_path}")


@pulse_group.command("dashboard")
@click.option("--history", required=True, help="JSONL history file path.")
@click.option("--output", default="reports/dashboard.html", help="Output path.")
def pulse_dashboard(history: str, output: str) -> None:
    """Generate interactive HTML dashboard from history."""
    from selene_space_pulse.history import load_history
    from selene_space_pulse.dashboard import write_dashboard

    records = load_history(history)
    if not records:
        click.echo("No history records found.")
        return

    write_dashboard(records, output)
    click.echo(f"Dashboard written to {output}")
