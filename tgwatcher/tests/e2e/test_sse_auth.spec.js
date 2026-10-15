// AC-1: SSE auth token must NOT appear in the /api/events request URL.
// Verifies the fetch+ReadableStream SSE client uses Authorization header, not query string.
const { test, expect } = require('@playwright/test');

test('SSE request URL does not contain token', async ({ page }) => {
  // Capture all requests to /api/events
  const sseRequests = [];
  page.on('request', req => {
    if (req.url().includes('/api/events')) {
      sseRequests.push({ url: req.url(), headers: req.headers() });
    }
  });

  // Navigate to app — auto-login bootstrap should fire on localhost
  await page.goto('/');
  // Wait for SSE client to connect (give it a moment)
  await page.waitForTimeout(2000);

  // Must have at least one SSE request
  expect(sseRequests.length).toBeGreaterThan(0);
  // None of the request URLs should contain 'token='
  for (const r of sseRequests) {
    expect(r.url).not.toMatch(/token=/);
  }
  // The Authorization header should be present
  const last = sseRequests[sseRequests.length - 1];
  expect(last.headers['authorization']).toMatch(/^Bearer .+/);
});
