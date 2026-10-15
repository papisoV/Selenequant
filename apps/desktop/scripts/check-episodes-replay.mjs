// Live E2E: episodes replay page against real backend + real episode.
// DOM assertions only (no screenshots, per repo discipline).
import { chromium } from "playwright";

const BASE = "http://127.0.0.1:3001";
const EPISODE = "epi-7ba2d7372b83";

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const results = [];
  const check = (name, ok, extra = "") => {
    results.push(`${ok ? "PASS" : "FAIL"} ${name}${extra ? ` — ${extra}` : ""}`);
  };

  // 1. list view
  await page.goto(`${BASE}/research/episodes`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="episode-list"]', { timeout: 60_000 });
  const listText = await page.locator('[data-testid="episode-list"]').innerText();
  check("list renders episode row", listText.includes("ARBUSDT_autogen_factor"), listText.slice(0, 80));
  const replayHref = await page.locator('a:has-text("Replay")').first().getAttribute("href");
  check("replay link href", replayHref === `/research/episodes?id=${EPISODE}`, replayHref ?? "null");

  // 2. replay view — withheld state
  await page.goto(`${BASE}/research/episodes?id=${EPISODE}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector('[data-testid="scrubber"]', { timeout: 60_000 });
  const badges = await page.locator('[data-testid="prov-badge"]').allInnerTexts();
  check("provenance badges include RECORDED", badges.includes("RECORDED"), badges.join("|"));
  check("provenance badges include NOT AVAILABLE", badges.includes("NOT AVAILABLE"));
  check("WITHHELD badge present", badges.includes("WITHHELD"));
  const headerText = await page.locator("body").innerText();
  check("withheld count 0 at LAST_ACTION", headerText.includes("0 笔 T 后成交已扣住"));

  // 3. scrubber drag → ?at refetch
  const scrubber = page.locator('[data-testid="scrubber"]');
  const min = Number(await scrubber.getAttribute("min"));
  const max = Number(await scrubber.getAttribute("max"));
  const mid = Math.floor((min + max) / 2);
  await scrubber.fill(String(mid));
  await page.waitForSelector('[data-testid="reveal-btn"]', { timeout: 60_000 });
  const scrubVal = await page.locator('[data-testid="scrubber-value"]').innerText();
  check("scrubber value updates", Number(scrubVal.slice(0, 4)) > 0, scrubVal);
  check("reveal button appears after scrub", (await page.locator('[data-testid="reveal-btn"]').count()) === 1);

  // 4. click reveal → outcome table + post-mortem
  await page.locator('[data-testid="reveal-btn"]').click();
  await page.waitForSelector('[data-testid="trades-after"]', { timeout: 30_000 });
  const afterText = await page.locator('[data-testid="trades-after"]').innerText();
  check("outcome trades table renders", afterText.length > 20, `${afterText.split("\n").length} lines`);
  check("post-mortem section renders", (await page.locator("body").innerText()).includes("direction_correct"));
  check("metrics section renders", (await page.locator("body").innerText()).includes("total_return"));
  check("reveal button gone after reveal", (await page.locator('[data-testid="reveal-btn"]').count()) === 0);

  console.log(results.join("\n"));
  const failed = results.filter((r) => r.startsWith("FAIL")).length;
  await browser.close();
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((e) => {
  console.error("E2E ERROR:", e.message);
  process.exit(2);
});
