from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from selene_space_stock.config import NotificationTarget, _config_from_mapping
from . import notify
from .dashboard import write_dashboard
from .history import append_history, attach_history, load_history
from .reporting import write_reports
from .site import build_site
from .ui import language_boot_script, language_runtime_script, language_toggle, ui_styles


@dataclass(frozen=True)
class ConsoleOptions:
    symbols: list[str]
    title: str
    timezone: str
    language: str
    providers: list[str]
    reports_dir: Path
    history_path: Path
    site_dir: Path
    include_news: bool
    use_ai: bool
    build_dashboard: bool
    build_site: bool
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    feishu_webhook: str | None = None


def run_console(host: str = "127.0.0.1", port: int = 8766, root: str | Path = ".") -> None:
    root_path = Path(root).resolve()
    server = _bind_server(host, port, root_path)
    actual_host, actual_port = server.server_address
    print(f"Selene Space Pulse Console: http://{actual_host}:{actual_port}/")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped Selene Space Pulse Console.")
    finally:
        server.server_close()


def render_console_html() -> str:
    return f"""<!doctype html>
<html lang="en" data-lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Selene Space Pulse Console</title>
  {language_boot_script("en")}
  <style>
    {ui_styles()}
    .console-grid {{ display: grid; grid-template-columns: minmax(280px, 0.72fr) minmax(0, 1fr); gap: 16px; align-items: start; }}
    .form-grid {{ display: grid; gap: 12px; }}
    .field label {{ display: block; margin-bottom: 6px; color: var(--muted); font-size: 12px; font-weight: 760; }}
    .field input, .field textarea, .field select {{
      width: 100%; border: 1px solid var(--line-strong); border-radius: 8px; padding: 10px 11px;
      background: var(--canvas); color: var(--ink); font: inherit;
    }}
    .field textarea {{ min-height: 118px; resize: vertical; }}
    .switch-row {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .checkline {{ display: flex; gap: 8px; align-items: center; min-height: 36px; color: var(--ink); font-weight: 680; }}
    .checkline input {{ width: 16px; height: 16px; }}
    .primary-button {{
      min-height: 42px; border: 1px solid var(--brand); border-radius: 8px; padding: 10px 13px;
      background: var(--brand); color: var(--top); font: inherit; font-weight: 820; cursor: pointer;
    }}
    .primary-button[disabled] {{ opacity: 0.62; cursor: wait; }}
    .result-list {{ display: grid; gap: 10px; margin-top: 12px; }}
    .result-link {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: var(--canvas-soft); }}
    .result-link strong {{ color: var(--ink); }}
    .status-box {{ min-height: 190px; }}
    .status-line {{ margin: 0; white-space: pre-wrap; word-break: break-word; }}
    .error-box {{ color: var(--red); }}
    .notify-details {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px 11px; }}
    .notify-details summary {{ cursor: pointer; color: var(--muted); font-size: 12px; font-weight: 760; }}
    .notify-details .form-grid {{ margin-top: 12px; }}
    @media (max-width: 900px) {{ .console-grid, .switch-row {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <nav class="topbar">
    <div class="brand-mark">Selene Space Pulse</div>
    {language_toggle()}
  </nav>
  <header class="hero">
    <div>
      <div class="eyebrow"><span data-i18n-en>Local Console</span><span data-i18n-zh>本地控制台</span></div>
      <h1><span data-i18n-en>Analyze Any Watchlist</span><span data-i18n-zh>分析任意股票池</span></h1>
      <p><span data-i18n-en>Run AI-assisted quant research: screen symbols, review signals, refresh risk dashboards, and open the static site from one local page.</span><span data-i18n-zh>运行 AI 辅助量化研究：筛选股票池、复盘信号、刷新风险 Dashboard，并从同一个本地页面打开静态站点。</span></p>
    </div>
    <aside class="hero-panel">
      <strong><span data-i18n-en>Visual flow</span><span data-i18n-zh>可视化流程</span></strong>
      <p class="fineprint"><span data-i18n-en>Localhost only. Quant research automation only. Not financial advice. No order placement.</span><span data-i18n-zh>仅本机访问。仅用于量化研究自动化，不构成投资建议，不会下单交易。</span></p>
    </aside>
  </header>
  <section class="console-grid">
    <form class="panel form-grid" data-run-form>
      <div class="field">
        <label for="symbols"><span data-i18n-en>Symbols</span><span data-i18n-zh>股票代码</span></label>
        <textarea id="symbols" name="symbols" placeholder="AAPL, MSFT, NVDA, 600519, 0700.HK, BTC-USD" required>AAPL, MSFT, NVDA, 600519</textarea>
      </div>
      <div class="field">
        <label for="title"><span data-i18n-en>Report title</span><span data-i18n-zh>报告标题</span></label>
        <input id="title" name="title" value="我的自选股每日分析报告" data-default-en="My Daily Stock Analysis Report" data-default-zh="我的自选股每日分析报告">
      </div>
      <div class="switch-row">
        <div class="field">
          <label for="providers"><span data-i18n-en>Data providers</span><span data-i18n-zh>数据源</span></label>
          <input id="providers" name="providers" value="yfinance">
        </div>
        <div class="field">
          <label for="timezone"><span data-i18n-en>Timezone</span><span data-i18n-zh>时区</span></label>
          <input id="timezone" name="timezone" value="America/Los_Angeles">
        </div>
      </div>
      <div class="switch-row">
        <label class="checkline"><input type="checkbox" name="includeNews" checked> <span data-i18n-en>Include news</span><span data-i18n-zh>包含新闻</span></label>
        <label class="checkline"><input type="checkbox" name="buildDashboard" checked> <span data-i18n-en>Refresh dashboard</span><span data-i18n-zh>刷新 Dashboard</span></label>
        <label class="checkline"><input type="checkbox" name="buildSite" checked> <span data-i18n-en>Build static site</span><span data-i18n-zh>生成静态站点</span></label>
        <label class="checkline"><input type="checkbox" name="useAi"> <span data-i18n-en>Use AI summaries</span><span data-i18n-zh>启用 AI 总结</span></label>
      </div>
      <details class="notify-details">
        <summary><span data-i18n-en>Push Notifications (optional)</span><span data-i18n-zh>推送通知（可选）</span></summary>
        <div class="form-grid">
          <div class="field">
            <label for="telegramToken"><span data-i18n-en>Telegram Bot Token</span><span data-i18n-zh>Telegram 机器人 Token</span></label>
            <input id="telegramToken" name="telegramToken" placeholder="123456:ABC-DEF...">
          </div>
          <div class="field">
            <label for="telegramChatId"><span data-i18n-en>Telegram Chat ID</span><span data-i18n-zh>Telegram 会话 ID</span></label>
            <input id="telegramChatId" name="telegramChatId" placeholder="-100123456789">
          </div>
          <div class="field">
            <label for="feishuWebhook"><span data-i18n-en>Feishu Webhook URL</span><span data-i18n-zh>飞书 Webhook 地址</span></label>
            <input id="feishuWebhook" name="feishuWebhook" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/...">
          </div>
        </div>
      </details>
      <button class="primary-button" type="submit"><span data-i18n-en>Run Analysis</span><span data-i18n-zh>开始分析</span></button>
    </form>
    <section class="panel status-box" aria-live="polite">
      <h2><span data-i18n-en>Results</span><span data-i18n-zh>结果</span></h2>
      <p class="muted status-line" data-status><span data-i18n-en>Ready.</span><span data-i18n-zh>就绪。</span></p>
      <div class="result-list" data-results></div>
    </section>
  </section>
</main>
{language_runtime_script()}
<script>
  (function () {{
    var form = document.querySelector("[data-run-form]");
    var status = document.querySelector("[data-status]");
    var results = document.querySelector("[data-results]");
    var button = form.querySelector("button");
    var titleInput = form.querySelector('input[name="title"]');
    var titleEdited = false;
    function currentLang() {{ return document.documentElement.dataset.lang || "en"; }}
    function text(en, zh) {{ return currentLang() === "zh" ? zh : en; }}
    function defaultTitle(lang) {{
      return lang === "zh" ? titleInput.dataset.defaultZh : titleInput.dataset.defaultEn;
    }}
    function syncDefaultTitle() {{
      var lang = currentLang();
      var other = lang === "zh" ? titleInput.dataset.defaultEn : titleInput.dataset.defaultZh;
      if (!titleEdited || titleInput.value === other) {{
        titleInput.value = defaultTitle(lang);
        titleEdited = false;
      }}
    }}
    function esc(value) {{
      return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {{
        return {{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[char];
      }});
    }}
    function setStatus(message, isError) {{
      status.textContent = message;
      status.classList.toggle("error-box", Boolean(isError));
    }}
    function linkRow(label, href) {{
      if (!href) return "";
      return '<a class="result-link" href="' + esc(href) + '" target="_blank" rel="noopener"><strong>' + esc(label) + '</strong><span>' + esc(text("Open", "打开")) + '</span></a>';
    }}
    function notificationRows(notifications) {{
      if (!notifications || !notifications.length) return "";
      return notifications.map(function (line) {{
        return '<div class="result-link"><strong>' + esc(text("Notification", "推送通知")) + '</strong><span>' + esc(line) + '</span></div>';
      }}).join("");
    }}
    form.addEventListener("submit", async function (event) {{
      event.preventDefault();
      button.disabled = true;
      results.innerHTML = "";
      setStatus(text("Running analysis. This can take a minute when market/news data is fetched.", "正在分析。拉取行情和新闻时可能需要一点时间。"), false);
      var payload = {{
        symbols: form.symbols.value,
        title: form.title.value,
        timezone: form.timezone.value,
        providers: form.providers.value,
        includeNews: form.includeNews.checked,
        useAi: form.useAi.checked,
        buildDashboard: form.buildDashboard.checked,
        buildSite: form.buildSite.checked,
        telegramToken: form.telegramToken.value,
        telegramChatId: form.telegramChatId.value,
        feishuWebhook: form.feishuWebhook.value
      }};
      try {{
        var response = await fetch("/api/analyze", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify(payload)
        }});
        var body = await response.json();
        if (!response.ok) throw new Error(body.error || "Request failed");
        setStatus(text("Done. Generated " + body.symbols.join(", "), "完成。已生成 " + body.symbols.join(", ")), false);
        results.innerHTML = [
          linkRow(text("HTML report", "HTML 报告"), body.links.html),
          linkRow(text("Dashboard", "Dashboard"), body.links.dashboard),
          linkRow(text("Static site", "静态站点"), body.links.site),
          linkRow("JSON", body.links.json),
          linkRow("Markdown", body.links.markdown),
          notificationRows(body.notifications)
        ].join("");
      }} catch (error) {{
        setStatus(error.message || String(error), true);
      }} finally {{
        button.disabled = false;
      }}
    }});
    titleInput.addEventListener("input", function () {{
      titleEdited = titleInput.value !== defaultTitle(currentLang());
    }});
    document.querySelectorAll("[data-lang-choice]").forEach(function (langButton) {{
      langButton.addEventListener("click", function () {{
        window.setTimeout(syncDefaultTitle, 0);
      }});
    }});
    syncDefaultTitle();
  }}());
</script>
</body>
</html>
"""


def options_from_payload(payload: dict[str, Any]) -> ConsoleOptions:
    symbols = parse_symbols(str(payload.get("symbols", "")))
    if not symbols:
        raise ValueError("Enter at least one symbol.")
    providers = parse_symbols(str(payload.get("providers") or "yfinance"))
    return ConsoleOptions(
        symbols=symbols,
        title=str(payload.get("title") or "我的自选股每日分析报告"),
        timezone=str(payload.get("timezone") or "America/Los_Angeles"),
        language=str(payload.get("language") or "zh-CN"),
        providers=providers or ["yfinance"],
        reports_dir=_relative_path(payload.get("reportsDir"), "reports"),
        history_path=_relative_path(payload.get("historyPath"), "data/history.jsonl"),
        site_dir=_relative_path(payload.get("siteDir"), "site"),
        include_news=bool(payload.get("includeNews", True)),
        use_ai=bool(payload.get("useAi", False)),
        build_dashboard=bool(payload.get("buildDashboard", True)),
        build_site=bool(payload.get("buildSite", True)),
        telegram_token=_optional_str(payload.get("telegramToken")),
        telegram_chat_id=_optional_str(payload.get("telegramChatId")),
        feishu_webhook=_optional_str(payload.get("feishuWebhook")),
    )


def run_console_analysis(options: ConsoleOptions, root: str | Path = ".") -> dict[str, Any]:
    from selene_space_pulse.engine import run_analysis
    from selene_space_pulse.sample import custom_watchlist_config

    root_path = Path(root).resolve()
    reports_dir = _resolve_under_root(root_path, options.reports_dir)
    history_path = _resolve_under_root(root_path, options.history_path)
    site_dir = _resolve_under_root(root_path, options.site_dir)
    config_text = custom_watchlist_config(
        options.symbols,
        title=options.title,
        timezone=options.timezone,
        language=options.language,
        providers=options.providers,
    )
    config = _config_from_mapping(yaml.safe_load(config_text) or {})
    config = config.model_copy(update={"news": config.news.model_copy(update={"enabled": options.include_news})})
    if not options.use_ai:
        config = config.model_copy(update={"llm": config.llm.model_copy(update={"enabled": False})})

    report = run_analysis(config, "web console")
    existing_history = load_history(history_path)
    report = attach_history(report, existing_history)
    paths = write_reports(report, reports_dir)
    append_history(history_path, report)

    dashboard_path = None
    if options.build_dashboard:
        dashboard_path = write_dashboard(load_history(history_path), reports_dir / "dashboard.html")

    site_result = None
    if options.build_site:
        site_result = build_site(reports_dir, site_dir, title=options.title)

    targets = _notification_targets(options)
    notification_results = (
        notify.send_notifications(report, targets, html_path=paths["html"]) if targets else []
    )

    return {
        "symbols": [analysis.asset.symbol for analysis in report.analyses],
        "links": {
            "html": _href(root_path, paths["html"]),
            "markdown": _href(root_path, paths["markdown"]),
            "json": _href(root_path, paths["json"]),
            "dashboard": _href(root_path, dashboard_path) if dashboard_path else None,
            "site": _href(root_path, site_result.index_path) if site_result else None,
        },
        "notifications": notification_results,
    }


def _notification_targets(options: ConsoleOptions) -> list[NotificationTarget]:
    targets: list[NotificationTarget] = []
    if options.telegram_token and options.telegram_chat_id:
        targets.append(
            NotificationTarget(
                type="telegram",
                name="telegram",
                enabled=True,
                settings={"token": options.telegram_token, "chat_id": options.telegram_chat_id},
            )
        )
    if options.feishu_webhook:
        targets.append(
            NotificationTarget(
                type="feishu",
                name="feishu",
                enabled=True,
                settings={"url": options.feishu_webhook},
            )
        )
    return targets


class ConsoleHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, root: Path, **kwargs):
        self.root = root
        super().__init__(*args, directory=str(root), **kwargs)

    def do_GET(self) -> None:
        if self.path in {"/", "/console"}:
            self._send_html(render_console_html())
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/analyze":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            result = run_console_analysis(options_from_payload(payload), self.root)
            self._send_json(result)
        except Exception:
            traceback.print_exc()
            self._send_json({"error": "Analysis failed. Check the server console for details."}, status=400)

    def _send_html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, body: dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _bind_server(host: str, port: int, root: Path) -> ThreadingHTTPServer:
    handler = partial(ConsoleHandler, root=root)
    for candidate in range(port, port + 20):
        try:
            return ThreadingHTTPServer((host, candidate), handler)
        except OSError:
            continue
    raise OSError(f"No available port found from {port} to {port + 19}.")


def _optional_str(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _relative_path(value: object, default: str) -> Path:
    path = Path(str(value or default))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Output paths must stay inside the project directory.")
    return path


def _resolve_under_root(root: Path, path: Path) -> Path:
    resolved = (root / path).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("Output paths must stay inside the project directory.")
    return resolved


def _href(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    return "/" + quote(path.resolve().relative_to(root).as_posix())


def parse_symbols(value: str) -> list[str]:
    import re
    return [symbol.strip() for symbol in re.split(r"[,，\s]+", value) if symbol.strip()]
