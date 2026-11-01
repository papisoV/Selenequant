"""渲染证据探针(精确版):alpha 挖矿后台可见性。

与 _probe_alpha_mine_ui.py 的差别:每步都**显式等**目标元素出现(而非固定 sleep),
否则 dev 首编译时序会让"chain not found"这类假阴性冒出来。

测:
1. 侧边栏徽标存在且计数 == 后端活跃会话数
2. 离开→返回后徽标仍在(证明全局 store + poller 跨路由存活)
3. 面板渲染「会话列表」标题(而非旧的「可恢复会话」)

用法(需 backend:8090 + 前端:3005):
    python scripts/_probe_alpha_mine_ui.py
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


def active_sessions() -> list[dict]:
    resp = httpx.get(f"{API}/api/v1/alpha/mining", timeout=15)
    resp.raise_for_status()
    sessions = resp.json()["data"]["sessions"]
    return [
        s for s in sessions if s.get("status") in ("starting", "running", "stopping")
    ]


def read_badge(page: Any) -> dict[str, Any]:
    # page.evaluate is typed Any; the JS literal below always yields an object.
    return cast(
        dict[str, Any],
        page.evaluate(
            """() => {
        const titles = [...document.querySelectorAll('.nav-chain-title')];
        const alpha = titles.find(t => t.textContent.includes('因子挖掘'));
        if (!alpha) return {present: false, reason: 'chain-not-rendered'};
        const badge = alpha.querySelector('.alpha-run-badge');
        if (!badge) return {present: false, reason: 'no-badge'};
        return {present: true, text: badge.textContent.trim(),
                aria: badge.getAttribute('aria-label')};
    }"""
        ),
    )


def main() -> int:
    actives = active_sessions()
    expected = len(actives)
    report: dict = {
        "base": BASE,
        "api": API,
        "api_active_sessions": [s.get("session_id") for s in actives],
        "expected_badge": expected,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        # ---- 1. 打开页面,等侧边栏链渲染 ----
        # 启动门(BackendReadyProvider)在后端冷启/健康门慢时会等满 60s 才降级放行,
        # 之后应用壳才挂载 —— 90s 在健康门慢时不够,给 180s。
        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".nav-chain-title", timeout=180_000)
        # 等 store refresh 落地(轮询周期内)
        page.wait_for_timeout(8_000)

        badge1 = read_badge(page)
        report["badge_on_load"] = badge1
        report["badge_on_load_correct"] = badge1.get("text") == str(expected) or (
            expected == 0 and not badge1.get("present")
        )

        body = page.inner_text("body")
        report["has_会话列表"] = "会话列表" in body
        report["has_可恢复会话_旧标题"] = "可恢复会话" in body

        # 裁剪侧边栏那条链
        chain = page.query_selector(".nav-chain-title:has-text('因子挖掘')")
        if chain:
            chain.screenshot(path=str(OUT / "wayinzi_badge_on_load.png"))
        page.screenshot(path=str(OUT / "wayinzi_load.png"))

        # ---- 2. 离开 → 返回 ----
        page.goto(
            f"{BASE}/research/strat", wait_until="domcontentloaded", timeout=120_000
        )
        page.wait_for_timeout(4_000)
        page.screenshot(path=str(OUT / "wayinzi_away_strat.png"))

        page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        page.wait_for_selector(".nav-chain-title", timeout=180_000)
        page.wait_for_timeout(8_000)
        badge2 = read_badge(page)
        report["badge_after_return"] = badge2
        report["badge_survives_navigation"] = badge2.get("present") == (expected > 0)

        back_body = page.inner_text("body")
        report["after_return_has_训练日志"] = "训练日志" in back_body
        report["after_return_has_会话列表"] = "会话列表" in back_body
        page.screenshot(path=str(OUT / "wayinzi_after_return.png"))
        chain2 = page.query_selector(".nav-chain-title:has-text('因子挖掘')")
        if chain2:
            chain2.screenshot(path=str(OUT / "wayinzi_badge_after_return.png"))

        browser.close()

    (OUT / "wayinzi_alpha_mine_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
