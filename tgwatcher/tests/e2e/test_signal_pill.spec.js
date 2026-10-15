// AC: signal tab renders daemon status pill within 1s of click.
// Pill is rendered by checkDaemonStatus() after /api/signal/daemon resolves.
const { test, expect } = require('@playwright/test');

test('signal tab renders daemon status pill within 1s', async ({ page }) => {
  await page.goto('/');
  await page.waitForSelector('#msgBody');
  await page.waitForTimeout(500);

  await page.click('button[data-tab="signal"]');

  await expect(page.locator('#signalDaemonPill')).toBeVisible({ timeout: 1000 });
  await expect(page.locator('#signalDaemonPill')).toContainText(/daemon:/);
});
