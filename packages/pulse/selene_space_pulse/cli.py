from __future__ import annotations

import argparse
import os
import shlex
from dataclasses import replace
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

import yaml

from .config import load_config, load_config_from_mapping
from .dashboard import write_dashboard
from .demo import build_demo
from .doctor import format_doctor, run_doctor
from .engine import run_analysis
from .history import append_history, attach_history, load_history
from selene_space_types.stock import DailyReport
from .notify import send_notifications
from .portfolio_import import import_portfolio_config
from .reporting import write_reports
from .sample import SAMPLE_CONFIGS, custom_watchlist_config, parse_symbols
from .site import build_site
from .web import run_console


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="market-pulse",
        description="Generate AI-enhanced daily market analysis reports and notifications.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a starter watchlist config.")
    init_parser.add_argument("--path", default="watchlist.yaml", help="Where to write the config file.")
    init_parser.add_argument("--template", default="default", help="Template name.")
    init_parser.add_argument("--symbols", default=None, help="Comma/space separated custom symbols, e.g. AAPL,MSFT,NVDA,600519.")
    init_parser.add_argument("--title", default="My AI Market Pulse", help="Title for a custom --symbols watchlist.")
    init_parser.add_argument("--timezone", default="America/Los_Angeles", help="Timezone for a custom --symbols watchlist.")
    init_parser.add_argument("--language", default="zh-CN", help="Report language for a custom --symbols watchlist.")
    init_parser.add_argument("--providers", default=None, help="Comma/space separated providers for a custom watchlist, default: yfinance.")
    init_parser.add_argument(
        "--telegram-token",
        default=None,
        help="Telegram bot token to configure notifications (only applies with --symbols).",
    )
    init_parser.add_argument(
        "--telegram-chat-id",
        default=None,
        help="Telegram chat id to configure notifications (only applies with --symbols).",
    )
    init_parser.add_argument(
        "--feishu-webhook",
        default=None,
        help="Feishu custom bot webhook URL to configure notifications (only applies with --symbols).",
    )
    init_parser.add_argument("--list-templates", action="store_true", help="List available templates.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing file.")

    import_parser = subparsers.add_parser("import-portfolio", help="Import CSV/TSV/XLSX holdings into watchlist YAML.")
    import_parser.add_argument("--input", required=True, help="Portfolio file path.")
    import_parser.add_argument("--output", default="watchlist.yaml", help="Output watchlist YAML path.")
    import_parser.add_argument("--template", default="default", help="Base config template.")
    import_parser.add_argument("--title", default=None, help="Override generated config title.")
    import_parser.add_argument("--force", action="store_true", help="Overwrite an existing output file.")

    run_parser = subparsers.add_parser("run", help="Generate the daily report.")
    run_parser.add_argument("--config", default="watchlist.yaml", help="Path to watchlist config.")
    run_parser.add_argument("--symbols", default=None, help="Comma/space separated symbols to analyze without a config file.")
    run_parser.add_argument("--title", default="My AI Market Pulse", help="Title when using --symbols.")
    run_parser.add_argument("--timezone", default="America/Los_Angeles", help="Timezone when using --symbols.")
    run_parser.add_argument("--language", default="zh-CN", help="Report language when using --symbols.")
    run_parser.add_argument("--providers", default=None, help="Comma/space separated providers when using --symbols, default: yfinance.")
    run_parser.add_argument("--output", default="reports", help="Directory for generated reports.")
    run_parser.add_argument("--history", default="data/history.jsonl", help="Path to persistent history JSONL.")
    run_parser.add_argument("--no-history", action="store_true", help="Do not read or append history.")
    run_parser.add_argument("--no-notify", action="store_true", help="Skip configured notifications.")
    run_parser.add_argument("--no-ai", action="store_true", help="Disable AI summaries for this run.")
    run_parser.add_argument("--ai-only", action="store_true", help="Generate AI summaries without history append or notifications.")

    dashboard_parser = subparsers.add_parser("dashboard", help="Render a local static dashboard from history.")
    dashboard_parser.add_argument("--history", default="data/history.jsonl", help="Path to persistent history JSONL.")
    dashboard_parser.add_argument("--output", default="reports/dashboard.html", help="Dashboard HTML output path.")

    site_parser = subparsers.add_parser("site", help="Build a local static research site from generated reports.")
    site_parser.add_argument("--reports", default="reports", help="Directory containing reports and dashboard.html.")
    site_parser.add_argument("--output", default="site", help="Directory where the site should be written.")
    site_parser.add_argument("--title", default="AI Market Pulse", help="Site title.")
    site_parser.add_argument("--keep-reports", type=int, default=30, help="Maximum number of reports to include.")

    serve_parser = subparsers.add_parser("serve", help="Start the local visual console.")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host for the local console.")
    serve_parser.add_argument("--port", type=int, default=8766, help="Port for the local console.")
    serve_parser.add_argument("--root", default=".", help="Project directory to serve and write outputs in.")

    demo_parser = subparsers.add_parser("demo", help="Generate a complete offline demo site with sample data.")
    demo_parser.add_argument("--output", default="demo", help="Directory where demo files should be written.")
    demo_parser.add_argument("--title", default="AI Market Pulse Demo", help="Demo site/report title.")

    doctor_parser = subparsers.add_parser("doctor", help="Check config, providers, and optional integrations.")
    doctor_parser.add_argument("--config", default="watchlist.yaml", help="Path to watchlist config.")

    test_notify_parser = subparsers.add_parser(
        "test-notify", help="Send a test message to configured notification targets."
    )
    test_notify_parser.add_argument("--config", default="watchlist.yaml", help="Path to watchlist config.")

    args = parser.parse_args(argv)
    if args.command == "init":
        _init_config(
            Path(args.path),
            args.template,
            args.list_templates,
            args.force,
            args.symbols,
            args.title,
            args.timezone,
            args.language,
            args.providers,
            args.telegram_token,
            args.telegram_chat_id,
            args.feishu_webhook,
        )
    elif args.command == "import-portfolio":
        _import_portfolio(Path(args.input), Path(args.output), args.template, args.title, args.force)
    elif args.command == "run":
        _run(
            Path(args.config),
            Path(args.output),
            Path(args.history),
            args.no_history,
            args.no_notify,
            args.no_ai,
            args.ai_only,
            args.symbols,
            args.title,
            args.timezone,
            args.language,
            args.providers,
        )
    elif args.command == "dashboard":
        _dashboard(Path(args.history), Path(args.output))
    elif args.command == "site":
        _site(Path(args.reports), Path(args.output), args.title, args.keep_reports)
    elif args.command == "serve":
        run_console(args.host, args.port, args.root)
    elif args.command == "demo":
        _demo(Path(args.output), args.title)
    elif args.command == "doctor":
        _doctor(Path(args.config))
    elif args.command == "test-notify":
        _test_notify(Path(args.config))


def _init_config(
    path: Path,
    template: str,
    list_templates: bool,
    force: bool,
    symbols: str | None,
    title: str,
    timezone: str,
    language: str,
    providers: str | None,
    telegram_token: str | None = None,
    telegram_chat_id: str | None = None,
    feishu_webhook: str | None = None,
) -> None:
    if list_templates:
        print("\n".join(sorted(SAMPLE_CONFIGS)))
        return
    if path.exists() and not force:
        raise SystemExit(f"{path} already exists. Use --force to overwrite it.")
    if bool(telegram_token) != bool(telegram_chat_id):
        raise SystemExit("--telegram-token and --telegram-chat-id must be provided together.")
    if symbols:
        config = custom_watchlist_config(
            parse_symbols(symbols),
            title=title,
            timezone=timezone,
            language=language,
            providers=parse_symbols(providers) if providers else None,
            telegram_token=telegram_token,
            telegram_chat_id=telegram_chat_id,
            feishu_webhook=feishu_webhook,
        )
        path.write_text(config, encoding="utf-8")
        print(f"Wrote custom watchlist config to {path}")
        _print_notification_setup_hint(path, telegram_token, telegram_chat_id, feishu_webhook)
        return
    if template not in SAMPLE_CONFIGS:
        raise SystemExit(f"Unknown template '{template}'. Available: {', '.join(sorted(SAMPLE_CONFIGS))}")
    path.write_text(SAMPLE_CONFIGS[template], encoding="utf-8")
    print(f"Wrote {template} starter config to {path}")


def _print_notification_setup_hint(
    path: Path,
    telegram_token: str | None,
    telegram_chat_id: str | None,
    feishu_webhook: str | None,
) -> None:
    if not (telegram_token or telegram_chat_id or feishu_webhook):
        return
    print("\nSet these environment variables before running market-pulse:")
    if telegram_token:
        print(f"  export TELEGRAM_BOT_TOKEN={shlex.quote(telegram_token)}")
    if telegram_chat_id:
        print(f"  export TELEGRAM_CHAT_ID={shlex.quote(telegram_chat_id)}")
    if feishu_webhook:
        print(f"  export FEISHU_WEBHOOK_URL={shlex.quote(feishu_webhook)}")
    print(f"\nThen verify it works: market-pulse test-notify --config {path}")


def _import_portfolio(
    input_path: Path,
    output_path: Path,
    template: str,
    title: str | None,
    force: bool,
) -> None:
    if output_path.exists() and not force:
        raise SystemExit(f"{output_path} already exists. Use --force to overwrite it.")
    path = import_portfolio_config(input_path, output_path, template=template, title=title)
    print(f"Watchlist: {path}")


def _run(
    config_path: Path,
    output_dir: Path,
    history_path: Path,
    no_history: bool,
    no_notify: bool,
    no_ai: bool,
    ai_only: bool,
    symbols: str | None = None,
    title: str = "My AI Market Pulse",
    timezone: str = "America/Los_Angeles",
    language: str = "zh-CN",
    providers: str | None = None,
) -> None:
    config, config_label = _load_run_config(config_path, symbols, title, timezone, language, providers)
    if no_ai and ai_only:
        raise SystemExit("--no-ai and --ai-only cannot be used together.")
    if no_ai:
        config = replace(config, llm=replace(config.llm, enabled=False))
    if ai_only:
        if not config.llm.enabled:
            raise SystemExit("--ai-only requires llm.enabled: true in config.")
        no_history = True
        no_notify = True
    report = run_analysis(config, config_label)
    valid_count = sum(1 for analysis in report.analyses if analysis.snapshot.rows > 0)
    if not no_history:
        report = attach_history(report, load_history(history_path))
    paths = write_reports(report, output_dir)
    print(f"Markdown: {paths['markdown']}")
    print(f"HTML:     {paths['html']}")
    print(f"JSON:     {paths['json']}")
    if valid_count == 0:
        raise SystemExit(
            "No valid market data was returned for any symbol. "
            "Skipping history append and notifications so a broken report is not published. "
            "Check data providers, symbols, and network access."
        )
    if not no_history:
        append_history(history_path, report)
        print(f"History:  {history_path}")
    if not no_notify and config.notifications:
        report_url = os.getenv("MARKET_PULSE_REPORT_URL")
        for result in send_notifications(report, config.notifications, paths["html"], report_url):
            print(result)


def _load_run_config(
    config_path: Path,
    symbols: str | None,
    title: str,
    timezone: str,
    language: str,
    providers: str | None,
):
    if not symbols:
        return load_config(config_path), str(config_path)
    parsed_symbols = parse_symbols(symbols)
    config_text = custom_watchlist_config(
        parsed_symbols,
        title=title,
        timezone=timezone,
        language=language,
        providers=parse_symbols(providers) if providers else None,
    )
    config = load_config_from_mapping(yaml.safe_load(config_text) or {})
    print(f"Symbols:  {', '.join(asset.symbol for asset in config.assets)}")
    return config, "inline --symbols"


def _dashboard(history_path: Path, output_path: Path) -> None:
    records = load_history(history_path)
    if not records:
        raise SystemExit(f"No history records found at {history_path}. Run `market-pulse run` first.")
    path = write_dashboard(records, output_path)
    print(f"Dashboard: {path}")


def _site(reports_dir: Path, output_dir: Path, title: str, keep_reports: int) -> None:
    result = build_site(reports_dir, output_dir, title=title, keep_reports=keep_reports)
    print(f"Site:      {result.index_path}")
    if result.dashboard_path:
        print(f"Dashboard: {result.dashboard_path}")
    print(f"Reports:   {len(result.reports)}")


def _doctor(config_path: Path) -> None:
    config = load_config(config_path)
    print(format_doctor(run_doctor(config)))


def _test_notify(config_path: Path) -> None:
    config = load_config(config_path)
    if not config.notifications:
        print(
            "No notifications are configured in this config. "
            "Add one with, e.g.: market-pulse init --telegram-token ... --telegram-chat-id ..."
        )
        return
    report = _test_notify_report()
    for result in send_notifications(report, config.notifications):
        print(result)


def _test_notify_report() -> DailyReport:
    return DailyReport(
        title="AI Market Pulse Test",
        generated_at=datetime.now(dt_timezone.utc),
        timezone="UTC",
        language="en-US",
        analyses=[],
        market_brief=(
            "AI Market Pulse notification test - if you see this, your setup works!\n"
            "AI Market Pulse 通知测试成功，配置没问题！"
        ),
    )


def _demo(output_dir: Path, title: str) -> None:
    result = build_demo(output_dir, title=title)
    print(f"Demo root:  {result.root}")
    print(f"History:    {result.history_path}")
    print(f"Dashboard:  {result.dashboard_path}")
    print(f"Site:       {result.site.index_path}")
    print(f"Report:     {result.report_paths['html']}")


if __name__ == "__main__":
    main()
