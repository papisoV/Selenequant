import { test, expect, type Page, type Route } from "@playwright/test";

// B1→B2→B3 的真实渲染验收。
//
// 真实 next build + 真实 React 组件 + 真实布局与 CSS;只有网络层被 route 桩住
// (本副本没有自己的后端,机器上 8090 那个是别的树)。
// 每张截图钉一条用户可验收的行为,不是"跑通了"的自我报告。

const SHOTS = "e2e/__shots__";
const AGENT_ROUTE = "/system/agent"; // page-renderer slug "agent" 挂在这

/** 组一条 SSE 响应体。 */
function sse(events: Array<{ type: string; data: unknown }>): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("") + "data: [DONE]\n\n";
}

/** 桩住后端:chat 用给定 SSE,approve 可观察,其余只读端点给空信封。 */
async function stubBackend(
  page: Page,
  opts: {
    chatBody: string;
    onApprove?: (route: Route) => Promise<void>;
    /** 历史会话列表(GET /chat/sessions)。裸数组 —— 真后端就是裸响应。 */
    sessions?: Array<Record<string, unknown>>;
    /** 某会话的消息(GET /chat/sessions/{id}/messages)。 */
    sessionMessages?: Array<Record<string, unknown>>;
  },
): Promise<void> {
  await page.route("**/api/v1/agent/chat", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "X-Session-ID": "sess-live-1", "Cache-Control": "no-cache" },
      body: opts.chatBody,
    });
  });

  await page.route("**/api/v1/agent/approve", async (route) => {
    if (opts.onApprove) return opts.onApprove(route);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ error: null, data: { ok: true } }),
    });
  });

  // chat_sessions.py 是**裸响应**路由(不是 APIResponse 外壳)——桩也照裸 shapes 给。
  // 若这里错给成 {error,data} 信封,测的就不是真契约了。
  await page.route("**/api/v1/chat/sessions**", async (route) => {
    const url = route.request().url();
    const method = route.request().method();
    if (/\/sessions\/[^/?]+\/messages/.test(url)) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(opts.sessionMessages ?? []),
      });
      return;
    }
    if (method === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: "cs-e2e-new",
          strategy_id: null,
          title: "",
          created_at: "2026-10-11T00:00:00Z",
          updated_at: "2026-10-11T00:00:00Z",
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(opts.sessions ?? []),
    });
  });

  // 兜底:其余 /api/v1/** 一律空信封(避免 preload 的未处理 rejection 糊住界面)。
  await page.route("**/api/v1/**", async (route) => {
    if (/\/agent\/(chat|approve)|\/chat\//.test(route.request().url())) return route.fallback();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ error: null, data: [] }),
    });
  });
}

/** 点开 drawer 的某个 tab(点击本身会展开折叠态)。 */
async function openDrawerTab(page: Page, label: string): Promise<void> {
  const tab = page.locator(".dtab", { hasText: label });
  await tab.click();
  await expect(tab).toHaveClass(/active/);
  // 等抽屉体渲染完
  await page.waitForTimeout(150);
}

test.describe("B1/B2/B3 渲染验收", () => {
  test("B1 · drawer 无「Agent 推理」tab,FAB 无幽灵角标", async ({ page }) => {
    await stubBackend(page, { chatBody: sse([{ type: "done", data: { session_id: "s1" } }]) });
    await page.goto(AGENT_ROUTE);
    await expect(page.locator(".drawer").first()).toBeVisible();

    // 该 tab 已从 TABS 常量里摘掉
    await expect(page.locator(".dtab", { hasText: "Agent 推理" })).toHaveCount(0);

    // 逐个点开每个 tab,任何一个都不该冒出 agent 聊天输入框
    const drawer = page.locator(".drawer").first();
    const tabs = drawer.locator(".dtab");
    const n = await tabs.count();
    expect(n).toBeGreaterThan(0);
    for (let i = 0; i < n; i++) {
      await tabs.nth(i).click();
      await page.waitForTimeout(120);
      await expect(drawer.locator(".ac-input")).toHaveCount(0);
    }
    await page.screenshot({ path: `${SHOTS}/b1-drawer-tabs.png` });

    // FAB 幽灵角标已摘
    await expect(page.locator(".fab-badge")).toHaveCount(0);
  });

  test("B3 · 终端 tab 是活的 agent 面(事件 → 带符号行)", async ({ page }) => {
    // 事件字段名照抄后端契约(react.py/runtime.py):
    //   TOOL_CALL  {name, arguments} · TOOL_RESULT {name, output} · THINKING {}
    await stubBackend(page, {
      chatBody: sse([
        { type: "thinking", data: {} },
        { type: "tool_call", data: { name: "get_klines", arguments: { symbol: "BTC", interval: "1h" } } },
        { type: "tool_result", data: { name: "get_klines", output: "返回 500 根 K 线" } },
        { type: "text_delta", data: { text: "建议观望,等放量。" } },
        { type: "done", data: { session_id: "sess-live-1" } },
      ]),
    });
    await page.goto(AGENT_ROUTE);
    await openDrawerTab(page, "终端");

    const term = page.locator(".terminal-panel");
    await expect(term).toBeVisible();

    // 死占位那行字必须消失
    await expect(page.locator("text=终端未连接")).toHaveCount(0);

    const input = page.getByPlaceholder(/向 Agent 输入指令/);
    await input.click();
    await input.fill("现在能买吗");
    await input.press("Enter");

    // 输入回显 + 工具行 + 结果行 + 最终文本
    await expect(term.locator(".term-line", { hasText: "现在能买吗" })).toBeVisible();
    await expect(term.locator(".term-line", { hasText: "→ tool: get_klines" })).toBeVisible();
    await expect(term.locator(".term-line", { hasText: "← 返回 500 根 K 线" })).toBeVisible();
    await expect(term.locator("text=建议观望")).toBeVisible();

    await page.screenshot({ path: `${SHOTS}/b3-terminal-live.png` });
  });

  test("B3 · 终端审批块可点,批准打 /agent/approve", async ({ page }) => {
    const approvals: Array<Record<string, unknown>> = [];
    await stubBackend(page, {
      // 审批挂起:真后端在人类决定前不发 done。
      chatBody: sse([
        { type: "thinking", data: { text: "要下单了" } },
        {
          type: "tool_approval_request",
          data: {
            tool_call_id: "call-1",
            tool_name: "place_order",
            arguments: { symbol: "BTC", side: "buy", qty: 0.01 },
          },
        },
      ]),
      onApprove: async (route) => {
        approvals.push(route.request().postDataJSON() as Record<string, unknown>);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ error: null, data: { ok: true } }),
        });
      },
    });
    await page.goto(AGENT_ROUTE);
    await openDrawerTab(page, "终端");

    const term = page.locator(".terminal-panel");
    const input = page.getByPlaceholder(/向 Agent 输入指令/);
    await input.click();
    await input.fill("帮我买 0.01 BTC");
    await input.press("Enter");

    await expect(term.locator("text=place_order")).toBeVisible();
    // 参数字面量(带引号,避开回显行里的裸 BTC)
    await expect(term.locator("text=/\\{\"symbol\":\"BTC\"/")).toBeVisible();
    const approveBtn = term.locator("button", { hasText: "批准" });
    await expect(approveBtn).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/b3-terminal-approval.png` });

    await approveBtn.click();
    await expect.poll(() => approvals.length, { timeout: 5000 }).toBe(1);
    expect(approvals[0]).toMatchObject({
      session_id: "sess-live-1",
      tool_call_id: "call-1",
      approve: true,
    });
  });

  test("B2 · chat 页渲染审批卡并可批准(补齐的 B0 面)", async ({ page }) => {
    const approvals: Array<Record<string, unknown>> = [];
    await stubBackend(page, {
      chatBody: sse([
        { type: "thinking", data: { text: "要下单了" } },
        {
          type: "tool_approval_request",
          data: {
            tool_call_id: "call-page-1",
            tool_name: "place_order",
            arguments: { symbol: "BTC", side: "buy", qty: 0.01 },
          },
        },
      ]),
      onApprove: async (route) => {
        approvals.push(route.request().postDataJSON() as Record<string, unknown>);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ error: null, data: { ok: true } }),
        });
      },
    });
    await page.goto(AGENT_ROUTE);

    const grid = page.locator(".v17-page-grid").first();
    await expect(grid).toBeVisible();
    await expect(grid.locator("text=Agent 对话")).toBeVisible();

    const input = page.getByPlaceholder(/向 AI 提问/);
    await input.click();
    await input.fill("帮我买 0.01 BTC");
    await input.press("Enter");

    await expect(grid.locator("text=place_order")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/b2-page-approval.png` });

    await grid.locator("button", { hasText: "批准" }).first().click();
    await expect.poll(() => approvals.length, { timeout: 5000 }).toBe(1);
    expect(approvals[0]).toMatchObject({ tool_call_id: "call-page-1", approve: true });
  });

  test("缺口1 · 对话页历史侧栏列出会话并可切回历史(裸数组契约)", async ({ page }) => {
    const SESSION = {
      id: "cs-6a89fd8e8729",
      strategy_id: null,
      title: "历史会话甲",
      created_at: "2026-05-08T05:08:59Z",
      updated_at: "2026-05-20T13:48:02Z",
    };
    await stubBackend(page, {
      chatBody: sse([{ type: "done", data: { session_id: "sess-live-1" } }]),
      sessions: [SESSION, { ...SESSION, id: "cs-b47af52f3923", title: "历史会话乙" }],
      sessionMessages: [
        {
          id: "m-1",
          session_id: "cs-6a89fd8e8729",
          role: "user",
          content: "历史里的第一句提问",
          extra: {},
          created_at: "2026-05-20T13:48:02Z",
        },
      ],
    });
    await page.goto(AGENT_ROUTE);

    const grid = page.locator(".v17-page-grid").first();
    await expect(grid).toBeVisible();

    // B2 验收项:对话页**能看到历史会话侧栏**。此前 page 面根本没消费 loadMessages/
    // initialSessionId —— 侧栏只活在 FAB 弹层里。
    await grid.locator("button", { hasText: "历史" }).first().click();

    // 两条会话都渲染(裸数组若被 api() 当信封解 → 这里是 0 条,即历史 bug)
    await expect(grid.locator("text=历史会话甲")).toBeVisible();
    await expect(grid.locator("text=历史会话乙")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/gap1-history-sidebar.png` });

    // 能切历史会话:点一条 → 回到对话面且它的消息已装载
    await grid.locator("text=历史会话甲").click();
    await expect(grid.locator("text=历史里的第一句提问")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/gap1-history-loaded.png` });
  });

  test("缺口2 · 策略工作台 AiSidebar 审批卡可批准", async ({ page }) => {
    const approvals: Array<Record<string, unknown>> = [];
    await stubBackend(page, {
      chatBody: sse([
        { type: "thinking", data: { text: "要部署了" } },
        {
          type: "tool_approval_request",
          data: {
            tool_call_id: "call-side-1",
            tool_name: "deploy_strategy",
            arguments: { strategy_id: "strat-e2e-1", mode: "paper" },
          },
        },
      ]),
      onApprove: async (route) => {
        approvals.push(route.request().postDataJSON() as Record<string, unknown>);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ error: null, data: { resolved: true } }),
        });
      },
    });
    // 必须在 stubBackend **之后**注册:Playwright 后注册的路由优先命中,
    // 否则会被上面的兜底 `**/api/v1/**` 吃掉,策略详情恒空 → 工作台停在骨架屏。
    await page.route("**/api/v1/strategies/**", async (route) => {
      const url = route.request().url();
      // 版本次列表走数组,详情走单对象。
      if (/\/versions/.test(url)) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ error: null, data: [] }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          error: null,
          data: {
            id: "strat-e2e-1",
            name: "E2E 策略",
            type: "custom",
            symbols: ["BTC/USDT"],
            interval: "1h",
            parameters: {},
            entry_rules: {},
            exit_rules: {},
            risk_params: {},
            tags: [],
            version: 1,
          },
        }),
      });
    });

    // 工作台直入(不等 State,State 经代理偶发慢)。
    await page.goto("/research/strategy-cockpit?id=strat-e2e-1&view=workbench");

    const input = page.getByPlaceholder("描述你想做的策略...");
    await expect(input).toBeVisible();
    await input.click();
    await input.fill("把这个策略部署到纸面");
    await input.press("Enter");

    // 此前本面 onEvent 不认 tool_approval_request → 卡不出现 → 写工具静默挂到超时。
    await expect(page.locator("text=deploy_strategy")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/gap2-ai-sidebar-approval.png` });

    await page.locator("button", { hasText: "批准" }).first().click();
    await expect.poll(() => approvals.length, { timeout: 5000 }).toBe(1);
    expect(approvals[0]).toMatchObject({ tool_call_id: "call-side-1", approve: true });
  });

  test("缺口2 · PA 面板审批卡可批准", async ({ page }) => {
    const approvals: Array<Record<string, unknown>> = [];
    await stubBackend(page, {
      chatBody: sse([
        { type: "thinking", data: { text: "要下单了" } },
        {
          type: "tool_approval_request",
          data: {
            tool_call_id: "call-pa-1",
            tool_name: "place_order",
            arguments: { symbol: "BTC", side: "buy", qty: 0.01 },
          },
        },
      ]),
      onApprove: async (route) => {
        approvals.push(route.request().postDataJSON() as Record<string, unknown>);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ error: null, data: { resolved: true } }),
        });
      },
    });

    await page.goto("/research/pa");
    await expect(page.locator("text=价格行为分析")).toBeVisible();

    // PA 面的追问输入(无 recordId 时走通用 agent 链路 —— 正是要覆盖的路径)。
    const input = page.getByPlaceholder(/追问分析结果|对着这条诊断追问/);
    await expect(input).toBeVisible();
    await input.click();
    await input.fill("帮我买 0.01 BTC");
    await input.press("Enter");

    await expect(page.locator("text=place_order")).toBeVisible();
    await page.screenshot({ path: `${SHOTS}/gap2-pa-panel-approval.png` });

    await page.locator("button", { hasText: "批准" }).first().click();
    await expect.poll(() => approvals.length, { timeout: 5000 }).toBe(1);
    expect(approvals[0]).toMatchObject({ tool_call_id: "call-pa-1", approve: true });
  });
});
