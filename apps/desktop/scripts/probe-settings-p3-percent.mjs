// P3 渲染证据:四档止盈面板是否真的在百分比空间,以及 risk 只读组是否
// 只剩四个没有编辑入口的字符串键(八个 tiered_* 不该再出现第二遍)。
//
// 默认直连 `PROBE_BASE`。若该 dev server 的 rewrite 目标后端没起(端口空着),
// 设 `PROBE_API=http://127.0.0.1:<port>` 把 /api/v1/* 转发到真在跑的后端 ——
// 数据仍来自真后端,只是换了传输路径,面板渲染的是真 settings 响应。
import pw from "../node_modules/playwright-core/index.js";
const { chromium } = pw;

const BASE = process.env.PROBE_BASE || "http://localhost:3001";
const API = process.env.PROBE_API || "";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 2400 } });

if (API) {
  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    try {
      const res = await fetch(API + url.pathname + url.search, {
        method: req.method(),
        headers: { "content-type": req.headers()["content-type"] ?? "application/json" },
        body: ["GET", "HEAD"].includes(req.method()) ? undefined : req.postData() ?? undefined,
      });
      await route.fulfill({
        status: res.status,
        headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
        body: Buffer.from(await res.arrayBuffer()),
      });
    } catch (err) {
      await route.fulfill({ status: 502, body: JSON.stringify({ error: String(err) }) });
    }
  });
}

// 面板起步渲染的是本地默认值(0.02),拿到 `/api/v1/settings` 后才换成后端值。
// 固定 sleep 会在两者之间读到默认值 —— 先等响应,再等状态落定。
const settingsResponse = page
  .waitForResponse((r) => r.url().includes("/api/v1/settings") && r.request().method() === "GET", {
    timeout: 40000,
  })
  .catch(() => null);

await page.goto(BASE + "/system/set", { waitUntil: "domcontentloaded", timeout: 40000 });
await page.waitForSelector("[data-settings-section]", { timeout: 40000 }).catch(() => {});
await settingsResponse;
await page.waitForTimeout(1500);

const tiered = await page.evaluate(() => {
  const heading = [...document.querySelectorAll("h2")].find((h) =>
    (h.textContent ?? "").includes("四档止盈"),
  );
  const section = heading?.closest("[data-settings-section]");
  if (!section) return null;
  const rows = [];
  for (const row of section.querySelectorAll("div.space-y-0\\.5")) {
    const slider = row.querySelector('input[type="range"]');
    const number = row.querySelector('input[type="number"]');
    rows.push({
      label: row.querySelector("span.text-ink-3")?.textContent?.trim() ?? "?",
      readout: row.querySelector("span.font-mono")?.textContent?.trim() ?? null,
      suffix: row.querySelector('span[aria-hidden="true"]')?.textContent?.trim() ?? "",
      number: number?.value ?? null,
      slider: slider
        ? { value: slider.value, min: slider.min, max: slider.max, step: slider.step }
        : null,
    });
  }
  return { heading: heading.textContent.trim(), rows };
});

await page.evaluate(() => document.querySelectorAll("details").forEach((d) => (d.open = true)));
await page.waitForTimeout(800);

const riskGroup = await page.evaluate(() => {
  const sec = document.getElementById("set-group-risk");
  if (!sec) return null;
  return {
    title: sec.querySelector("h2")?.textContent?.trim() ?? null,
    keys: [...sec.querySelectorAll(".set-key")].map((el) => el.textContent.trim()),
    values: [...sec.querySelectorAll(".set-val")].map((el) => el.textContent.trim()),
  };
});

// 只读区里出现的 tiered_ 行(应为空:八个键只在可编辑表单里出现一次)
const tieredRowsInReadonly = await page.evaluate(() =>
  [...document.querySelectorAll(".set-key")]
    .map((el) => el.textContent.trim())
    .filter((k) => k.startsWith("tiered_")),
);

// 页面上不该再有未换算的小数比例(默认值 0.015 / 0.02 / 0.03 / 0.06)
const pageText = await page.evaluate(() => document.body.textContent ?? "");
const rawFractionLeaks = ["0.015", "0.02", "0.03", "0.06"].filter((v) => pageText.includes(v));

console.log(JSON.stringify({ tiered, riskGroup, tieredRowsInReadonly, rawFractionLeaks }, null, 1));
await browser.close();
