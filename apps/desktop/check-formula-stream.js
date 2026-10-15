/* 刀①+刀③ live DOM 断言:http://127.0.0.1:3003 Research>Alpha 挖矿面板
 * 断言:公式流大屏挂载(hero score + 空态文案 + 进度条),无 console 错误。
 * 不截图(feedback_no-screenshot-files);Playwright DOM 断言纪律。 */
const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));

  let pass = 0, fail = 0;
  const assert = (name, cond) => {
    if (cond) { pass++; console.log(`PASS ${name}`); }
    else { fail++; console.log(`FAIL ${name}`); }
  };

  await page.goto("http://127.0.0.1:3003/research/alpha_mine", { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(4000);

  // 公式流大屏挂载
  const stream = page.locator('[data-testid="formula-stream"]');
  assert("公式流大屏挂载", (await stream.count()) >= 1);
  if (await stream.count()) {
    const text = await stream.textContent();
    assert("含 Best Score 标签", /best score/i.test(text));
    assert("空态文案(矿机预热中/暂无最优因子)", /矿机预热中|暂无最优因子/.test(text));
    const score = await page.locator('[data-testid="formula-stream-score"]').textContent();
    assert("hero score 数值渲染", score !== null && score.length > 0);
  }

  // 内联回测容器初始不可见(未触发)
  assert("内联回测块未触发时不渲染", (await page.locator('[data-testid="inline-backtest"]').count()) === 0);

  // console 干净(404 探测请求属预期噪声的除外)
  const realErrors = errors.filter((e) => !/404|Failed to load resource/.test(e));
  assert("0 console 错误(除资源 404)", realErrors.length === 0);
  if (realErrors.length) console.log("ERRORS:", realErrors.slice(0, 5));

  console.log(`\nRESULT: ${pass}/${pass + fail} PASS`);
  await browser.close();
  process.exit(fail === 0 ? 0 : 1);
})();
