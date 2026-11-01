import type { Page } from "@playwright/test";

// Boot 门拦截:BackendReadyProvider 轮询 /api/v1/health(Next 代理 IPv6 断→500),不 mock 则卡 60×1s
// 纯测试手法:让 boot 门放行,场景照常渲染(不改任何源码)
export async function mockHealth(page: Page): Promise<void> {
  await page.route("**/api/v1/health", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) }),
  );
}
