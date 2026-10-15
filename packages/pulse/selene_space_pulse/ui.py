from __future__ import annotations

import html


def lang(en: str, zh: str) -> str:
    return (
        f'<span data-i18n-en>{html.escape(en)}</span>'
        f'<span data-i18n-zh>{html.escape(zh)}</span>'
    )


def language_boot_script(default_lang: str = "en") -> str:
    safe_default = "zh" if default_lang.startswith("zh") else "en"
    return f"""<script>
    (function () {{
      var stored = localStorage.getItem("amp-lang");
      var browserLang = (navigator.language || "").toLowerCase().indexOf("zh") === 0 ? "zh" : "{safe_default}";
      var lang = stored || browserLang;
      document.documentElement.dataset.lang = lang;
      document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    }})();
  </script>"""


def language_toggle() -> str:
    return """
<div class="language-switch" aria-label="Language">
  <button type="button" data-lang-choice="en">EN</button>
  <button type="button" data-lang-choice="zh">中文</button>
</div>
"""


def language_runtime_script() -> str:
    return """<script>
    (function () {
      function setLang(lang) {
        document.documentElement.dataset.lang = lang;
        document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
        localStorage.setItem("amp-lang", lang);
        document.querySelectorAll("[data-lang-choice]").forEach(function (button) {
          button.setAttribute("aria-pressed", button.dataset.langChoice === lang ? "true" : "false");
        });
      }
      document.querySelectorAll("[data-lang-choice]").forEach(function (button) {
        button.addEventListener("click", function () { setLang(button.dataset.langChoice); });
      });
      setLang(document.documentElement.dataset.lang || "en");
    }());
  </script>"""


def ui_styles() -> str:
    return """
    :root {
      color-scheme: light;
      --bg: #f5f7f4;
      --canvas: #ffffff;
      --canvas-soft: #f9fbf8;
      --ink: #151a18;
      --muted: #66736d;
      --line: #d8e1da;
      --line-strong: #b9c8c0;
      --brand: #0f766e;
      --brand-strong: #115e59;
      --indigo: #4f46e5;
      --amber: #b45309;
      --rose: #be123c;
      --green: #067647;
      --red: #b42318;
      --top: #111a17;
      --shadow: 0 18px 50px rgba(17, 26, 23, 0.08);
    }
    html[data-lang="en"] [data-i18n-zh],
    html[data-lang="zh"] [data-i18n-en] {
      display: none !important;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
      text-rendering: geometricPrecision;
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0 0 auto;
      height: clamp(360px, 46vh, 520px);
      background: var(--top);
      z-index: -1;
    }
    a { color: var(--brand-strong); text-decoration: none; }
    a:hover { text-decoration: underline; text-underline-offset: 3px; }
    main { width: min(1220px, calc(100% - 36px)); margin: 0 auto; padding: 24px 0 56px; }
    h1, h2, h3 { letter-spacing: 0; }
    h1 { margin: 0; font-size: clamp(34px, 6vw, 64px); line-height: 0.98; }
    h2 { margin: 32px 0 14px; font-size: clamp(22px, 2.4vw, 30px); line-height: 1.15; }
    h3 { margin: 0 0 10px; font-size: 15px; line-height: 1.25; }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 28px;
      color: rgba(255, 255, 255, 0.78);
    }
    .brand-mark {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-weight: 760;
      color: #f8faf9;
    }
    .brand-mark::before {
      content: "";
      width: 10px;
      height: 10px;
      border-radius: 2px;
      background: var(--brand);
      box-shadow: 12px 0 0 var(--amber), 24px 0 0 var(--indigo);
    }
    .language-switch {
      display: inline-flex;
      gap: 3px;
      padding: 3px;
      border: 1px solid rgba(255, 255, 255, 0.16);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.08);
    }
    .language-switch button {
      min-width: 46px;
      min-height: 32px;
      border: 0;
      border-radius: 6px;
      background: transparent;
      color: rgba(255, 255, 255, 0.72);
      font: inherit;
      font-size: 13px;
      font-weight: 720;
      cursor: pointer;
    }
    html[data-lang="en"] .language-switch button[data-lang-choice="en"],
    html[data-lang="zh"] .language-switch button[data-lang-choice="zh"] {
      color: var(--top);
      background: #ffffff;
    }
    .hero {
      color: #f8faf9;
      padding: 18px 0 30px;
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.7fr);
      gap: 28px;
      align-items: end;
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 14px;
      color: #b9c8c0;
      font-size: 13px;
      font-weight: 760;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    .eyebrow::before {
      content: "";
      width: 22px;
      height: 2px;
      border-radius: 2px;
      background: var(--brand);
    }
    .hero p {
      max-width: 760px;
      margin: 14px 0 0;
      color: #ccd7d1;
      font-size: clamp(16px, 2vw, 19px);
    }
    .hero-panel {
      border: 1px solid rgba(255, 255, 255, 0.14);
      border-radius: 8px;
      padding: 16px;
      background: rgba(255, 255, 255, 0.06);
    }
    .hero-panel strong {
      display: block;
      font-size: 28px;
      line-height: 1;
      margin-top: 6px;
      color: #ffffff;
    }
    .muted { color: var(--muted); }
    .fineprint { font-size: 13px; color: var(--muted); }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
    .wide-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 14px; }
    .panel,
    .card,
    .summary,
    .brief,
    table {
      background: var(--canvas);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .panel,
    .card,
    .summary,
    .brief {
      padding: 16px;
      min-width: 0;
    }
    .section-note {
      margin: -8px 0 14px;
      color: var(--muted);
      max-width: 760px;
    }
    .kpi span,
    .summary span {
      display: block;
      font-size: 13px;
      font-weight: 700;
      color: var(--muted);
    }
    .kpi strong,
    .summary strong {
      display: block;
      font-size: clamp(24px, 3vw, 34px);
      line-height: 1.05;
      margin-top: 8px;
    }
    .metric {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 8px 0;
      border-bottom: 1px solid var(--line);
    }
    .metric:last-child { border-bottom: 0; }
    .metric span { color: var(--muted); }
    .spark { width: 100%; height: 96px; margin-top: 10px; }
    .score-spark { width: 100%; height: 48px; margin-top: 8px; }
    table {
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    th, td {
      text-align: left;
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
      background: var(--canvas-soft);
    }
    tr:last-child td { border-bottom: 0; }
    .gain { color: var(--green); }
    .loss { color: var(--red); }
    .risk-high { color: var(--rose); font-weight: 760; }
    .risk-medium { color: var(--amber); font-weight: 760; }
    .risk-low { color: var(--green); font-weight: 760; }
    .pill {
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      padding: 4px 9px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      border: 1px solid var(--brand);
      border-radius: 8px;
      padding: 8px 12px;
      color: var(--brand-strong);
      font-weight: 760;
    }
    .button:hover { background: #e9f6f2; text-decoration: none; }
    ul { padding-left: 18px; }
    li + li { margin-top: 4px; }
    :focus-visible { outline: 3px solid rgba(15, 118, 110, 0.28); outline-offset: 2px; }
    @media (max-width: 760px) {
      main { width: min(100% - 28px, 1220px); padding-top: 18px; }
      .hero { grid-template-columns: 1fr; padding-bottom: 24px; }
      .topbar { align-items: flex-start; }
      .wide-grid { grid-template-columns: 1fr; }
      table { display: block; overflow-x: auto; }
      th, td { white-space: nowrap; }
    }
    @media (prefers-color-scheme: dark) {
      :root {
        color-scheme: dark;
        --bg: #111614;
        --canvas: #18211e;
        --canvas-soft: #121a17;
        --ink: #f6faf7;
        --muted: #a9b8b1;
        --line: #2b3a35;
        --line-strong: #3f514b;
        --brand: #5eead4;
        --brand-strong: #99f6e4;
        --amber: #fbbf24;
        --rose: #fb7185;
        --green: #86efac;
        --red: #fca5a5;
        --top: #0b100e;
        --shadow: 0 18px 50px rgba(0, 0, 0, 0.18);
      }
      .button:hover { background: rgba(94, 234, 212, 0.12); }
    }
  """
