// Playwright config for TGWatcher E2E tests.
// Assumes Flask dev server is already running on :5000 (manual start).
// To auto-start, uncomment `webServer` block below.
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  expect: { timeout: 5000 },
  fullyParallel: false,  // SSE tests are stateful, run sequentially
  retries: 0,
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],  use: {
    baseURL: 'http://localhost:5800',
    trace: 'on-first-retry',
    headless: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],
  // Uncomment to auto-start Flask:
  // webServer: {
  //   command: 'python -m tgwatcher.web.app',
  //   url: 'http://localhost:5000',
  //   timeout: 30_000,
  //   reuseExistingServer: true,
  // },
});
