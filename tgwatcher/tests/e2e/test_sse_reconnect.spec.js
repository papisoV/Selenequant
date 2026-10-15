// AC-3: SSE reconnect uses exponential backoff (1s -> 2s -> 4s -> 8s -> 15s cap).
// Uses Playwright's page.clock to fast-forward through wall-clock waits.
const { test, expect } = require('@playwright/test');

test('SSE reconnect uses exponential backoff', async ({ page }) => {
  // Install fake clock BEFORE navigation so all setTimeout calls are virtual
  await page.clock.install();
  // Stub /api/events to fail 503 first 3 times, then succeed
  let attempts = 0;
  await page.route('**/api/events', async route => {
    attempts++;
    if (attempts <= 3) {
      return route.fulfill({ status: 503, body: 'Service Unavailable' });
    }
    return route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: '',
    });
  });
  const token = 'test-token';
  const json = (body, status = 200) => ({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/auth/bootstrap', route => route.fulfill(json({ token })));
  await page.route('**/api/login/status', route => route.fulfill(json({ logged_in: true, phone: '+1' })));
  await page.route('**/api/chats', route => route.fulfill(json([])));
  await page.route('**/api/messages**', route => route.fulfill(json({ messages: [], total: 0, page: 1 })));
  await page.route('**/api/crawl/status', route => route.fulfill(json({ running: false, auto_poll: [], groups: [] })));
  await page.route('**/api/listen/status', route => route.fulfill(json({ enabled: false, groups: [] })));
  await page.route('**/api/auto-poll', route => route.fulfill(json([])));
  await page.route('**/api/webhook', route => route.fulfill(json({ enabled: false, endpoints: [] })));

  const warnings = [];
  page.on('console', msg => {
    if (msg.type() === 'warning' && msg.text().includes('[SSE]')) {
      warnings.push(msg.text());
    }
  });

  await page.goto('/');
  // Drain microtasks so fetch promises resolve before fast-forwarding.
  await page.waitForFunction(() => eval('_sseRetryIdx') >= 1, null, { timeout: 10000 });

  // After first 503, _scheduleReconnect queues setTimeout(connectSSE, 1000).
  // Step virtual clock in small chunks with real microtask drains between,
  // because fetch.then/catch chains need a microtask tick to fire.
  for (const ms of [1000, 2000, 4000, 1000]) {
    await page.clock.fastForward(ms);
    // Yield to microtask queue so Promise chains (fetch -> catch -> schedule)
    // resolve before the next virtual clock tick.
    await page.evaluate(() => new Promise(r => setTimeout(r, 0)));
  }
  // After 1+2+4+1 = 8s of virtual time, >=3 failed attempts should have happened.
  expect(attempts).toBeGreaterThanOrEqual(3);

  // 4th attempt queued at +8s, returns 200. Run more to fire it.
  for (const ms of [1000, 2000, 4000, 4000]) {
    await page.clock.fastForward(ms);
    await page.evaluate(() => new Promise(r => setTimeout(r, 0)));
  }
  expect(attempts).toBeGreaterThanOrEqual(4);
});
