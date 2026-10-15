import { defineConfig, devices } from "@playwright/test";

// 多副本环境:同一台机器可能有其它副本占 3000 端口,用 E2E_PORT 指定本副本前端端口。
const PORT = process.env.E2E_PORT ?? "3000";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: "html",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    // 用 prod 模式起服务:E2E 走 next dev 时 Turbopack worker 会泄漏,跑完不回收,
    // 单次可涨到 10GB+。build 一次换来稳定内存,注意下面的 timeout 要覆盖 build 耗时。
    command: `npm run build && npm run start -- -p ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
  },
});
