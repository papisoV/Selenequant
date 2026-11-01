"""渲染证据探针:alpha 挖矿页面「资源上限(保护硬件)」面板。

测的是**真实渲染的 DOM**(不是源码存在性):
1. 资源面板挂载(data-testid=resource-policy)
2. 预算文本显示 thread_budget / cpu_cores,且与后端 GET /alpha/resources 一致
3. 两个下拉(并发会话数 / 每会话 torch 线程)存在,值 == 后端策略
4. 训练步数 / 批量大小 两个输入框已带 min/max 属性(= 之前裸输入框的修复)
5. 并发上限输入框的 max 已对齐后端真实上限(不再谎报 32)

用法(需 backend:8090 + 前端:3005):
    python scripts/_probe_alpha_resources_ui.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import httpx
from playwright.sync_api import sync_playwright

BASE = os.environ.get("PROBE_BASE", "http://127.0.0.1:3005")
API = os.environ.get("PROBE_API", "http://127.0.0.1:8090")
URL = f"{BASE}/research/alpha_mine"
OUT = Path(__file__).resolve().parent / "_bench_out"
OUT.mkdir(exist_ok=True)


def fetch_policy() -> dict[str, Any]:
    resp = httpx.get(f"{API}/api/v1/alpha/resources", timeout=15)
    resp.raise_for_status()
    return cast(dict[str, Any], resp.json()["data"])


READ_PANEL = """() => {
    const panel = document.querySelector('[data-testid="resource-policy"]');
    if (!panel) return {present: false};
    const budget = panel.querySelector('[data-testid="resource-budget"]');
    const conc = panel.querySelector('[data-testid="resource-concurrency"]');
    const thr = panel.querySelector('[data-testid="resource-threads"]');
    return {
      present: true,
      text: panel.textContent.replace(/\\s+/g, ' ').trim(),
      budget: budget ? budget.textContent.trim() : null,
      concurrency: conc ? conc.value : null,
      concurrency_disabled: conc ? conc.disabled : null,
      threads: thr ? thr.value : null,
    };
}"""

READ_BOUNDS = """() => {
    const inputs = [...document.querySelectorAll('input[type="number"]')];
    const byLabel = (label) => {
      const el = [...document.querySelectorAll('label')]
        .find(l => l.textContent.includes(label));
      if (!el) return null;
      const container = el.parentElement;
      const inp = container ? container.querySelector('input[type="number"]') : null;
      if (!inp) return null;
      return {min: inp.getAttribute('min'), max: inp.getAttribute('max')};
    };
    return {
      train_steps: byLabel('训练步数'),
      batch_size: byLabel('批量大小'),
      concurrency: byLabel('并发上限'),
      number_input_count: inputs.length,
    };
}"""


def main() -> int:
    policy = fetch_policy()
    report: dict = {"base": BASE, "api": API, "backend_policy": policy}

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".nav-chain-title", timeout=180_000)
        page.wait_for_selector('[data-testid="resource-policy"]', timeout=120_000)
        page.wait_for_timeout(3_000)

        panel = cast(dict[str, Any], page.evaluate(READ_PANEL))
        report["panel"] = panel
        report["panel_present"] = bool(panel.get("present"))
        report["concurrency_matches_backend"] = panel.get("concurrency") == str(
            policy["max_concurrency"]
        )
        report["threads_matches_backend"] = panel.get("threads") == str(
            policy["torch_num_threads"]
        )
        report["budget_shows_thread_count"] = panel.get("budget") is not None and str(
            policy["thread_budget"]
        ) in (panel.get("budget") or "")
        report["budget_shows_cpu_cores"] = panel.get("budget") is not None and str(
            policy["cpu_cores"]
        ) in (panel.get("budget") or "")

        bounds = cast(dict[str, Any], page.evaluate(READ_BOUNDS))
        report["input_bounds"] = bounds
        report["train_steps_bounded"] = (bounds.get("train_steps") or {}).get(
            "min"
        ) == "1" and (bounds.get("train_steps") or {}).get("max") == "10000"
        report["batch_size_bounded"] = (bounds.get("batch_size") or {}).get(
            "min"
        ) == "1" and (bounds.get("batch_size") or {}).get("max") == "8192"

        # 并发上限 只在批量模式下渲染 → 先勾上批量模式再读它的 max。
        page.get_by_label("批量模式").check()
        page.wait_for_timeout(1_500)
        bounds_batch = cast(dict[str, Any], page.evaluate(READ_BOUNDS))
        report["input_bounds_batch_mode"] = bounds_batch
        report["concurrency_max_matches_ceiling"] = (
            bounds_batch.get("concurrency") or {}
        ).get("max") == str(policy["max_concurrency"])

        # 裁面板本体(视觉证据)
        panel_el = page.query_selector('[data-testid="resource-policy"]')
        if panel_el:
            panel_el.screenshot(path=str(OUT / "wayinzi_resource_panel.png"))
        page.screenshot(path=str(OUT / "wayinzi_resources_page.png"))

        browser.close()

    (OUT / "wayinzi_alpha_resources_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
