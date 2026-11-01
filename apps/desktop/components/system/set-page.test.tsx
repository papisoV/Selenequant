import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, within, cleanup } from "@testing-library/react";
import { I18nProvider } from "@/components/i18n/i18n-provider";
import { SetPage } from "./set-page";
import { GROUP_LABELS, READONLY_GROUP_ORDER } from "@/lib/settings-groups";
import { translateUiText } from "@/lib/i18n/ui-text";

/**
 * P1 验收门(`internal-plan:237-244`)。
 *
 * 这里刻意渲染**真面板**(只 mock 数据层),因为要证的是"页面上不再出现
 * 裸 JSON key 标题"——mock 掉面板等于把待证对象换成了假想的标题。
 *
 * jsdom 没有布局,`getBoundingClientRect()` 恒返回全 0、`scrollIntoView`
 * 不存在。所以浏览器几何(门 4)在这里只能证到"调用了 scrollIntoView 且
 * 目标是该 chip 对应节点";真实视口落位由活体渲染证据负责,不在此伪造。
 */

const mockApiData: Record<string, unknown> = {};

// set-page 与 TieredTrailingForm 走的是 `lib/hooks/use-api`(不是 use-api-page),
// 两个 hook 都要 mock,否则 SWR 会去 fetch 相对路径。
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: <T,>(url: string | null) => ({
    data: (url ? (mockApiData[url] ?? null) : null) as T | null,
    error: undefined,
    isLoading: false,
    mutate: vi.fn(),
  }),
}));

vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: <T,>(url: string | null) => ({
      data: (url ? (mockApiData[url] ?? null) : null) as T | null,
      error: undefined,
      isLoading: false,
      mutate: vi.fn(),
    }),
  }),
}));

vi.mock("swr", () => ({
  default: () => ({ data: null, error: undefined, isLoading: false, mutate: vi.fn() }),
}));

vi.mock("@/lib/toast", () => ({
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

vi.mock("@/lib/api/account", () => ({
  exportUserData: vi.fn(),
  getDeletionStatus: vi.fn(),
  requestDeletion: vi.fn(),
  cancelDeletion: vi.fn(),
}));

vi.mock("@/lib/api/settings", () => ({
  TIERED_TRAILING_DEFAULTS: {
    tiered_hard_stop_pct: 0.02,
    tiered_low_tier_threshold: 0.015,
    tiered_first_tier_threshold: 0.03,
    tiered_second_tier_threshold: 0.06,
    tiered_low_tier_drop_pct: 0.5,
    tiered_first_tier_drop_pct: 0.3,
    tiered_second_tier_drop_pct: 0.2,
    tiered_min_emit_interval_seconds: 30,
  },
  putTieredConfig: vi.fn(async () => undefined),
  putLlmSettings: vi.fn(async () => undefined),
  putExchangeSettings: vi.fn(async () => undefined),
  testExchangeCredentials: vi.fn(async () => ({})),
  putNetworkSettings: vi.fn(async () => undefined),
  fetchProxyStatus: vi.fn(async () => null),
  detectLocalProxies: vi.fn(async () => ({ candidates: [] })),
  testProxyConnectivity: vi.fn(async () => ({})),
  putPaHeartbeatSettings: vi.fn(async () => undefined),
  putLLMBudgetSettings: vi.fn(async () => undefined),
  getLLMUsage: vi.fn(async () => null),
}));

const SETTINGS_URL = "/api/v1/settings";

/** 后端 `_defaults()` 的 16 段 + `get_settings()` 注入的 security。 */
function backendSettings() {
  return {
    general: { language: "zh-CN", theme: "dark" },
    exchange: { provider: "binance", market_type: "spot", testnet: true },
    llm: { provider: "openai", base_url: "", model: "" },
    risk: { max_position_pct: 0.2 },
    risk_alerts: { enabled: true, rules: { drawdown: { threshold_pct: 10 } } },
    grid: { enabled: false, spacing_pct: 1.5 },
    dex: { uniswap: { enabled: false, trade_mode: "paper" } },
    sentinel: { enabled: true, interval_minutes: 5 },
    pa_heartbeat: { enabled: false, daily_max: 20 },
    llm_budget: { daily_tokens: 0 },
    mission_worker: { enabled: true, interval_minutes: 15, max_rounds_per_mission: 3 },
    network: { proxy_url: "" },
    skills: { enabled: {} },
    ai: { react_enabled: true, max_tool_rounds: 8 },
    favorites: { symbols: ["BTCUSDT"] },
    workspace_layouts: { presets: {} },
    security: { encrypted: true, security_note: "ok" },
  };
}

function renderPage({ locale }: { locale?: "en" } = {}) {
  if (locale) localStorage.setItem("selenespace-locale", locale);
  return render(
    <I18nProvider>
      <SetPage />
    </I18nProvider>,
  );
}

beforeEach(() => {
  localStorage.clear();
  for (const key of Object.keys(mockApiData)) delete mockApiData[key];
  mockApiData[SETTINGS_URL] = backendSettings();
});

afterEach(() => {
  cleanup();
  localStorage.clear();
  document.documentElement.lang = "";
  delete document.documentElement.dataset.locale;
});

describe("设置页 P1:标题语义", () => {
  it("门 1 —— 15 个后端段里没有任何一段以裸 JSON key 作标题", () => {
    renderPage();
    const backendKeys = Object.keys(backendSettings());

    // 所有"标题位"文字:分组标题 class + 真 heading + 折叠摘要
    const titleNodes = [
      ...document.querySelectorAll('[class*="set-group-title"]'),
      ...document.querySelectorAll("h1, h2, h3, summary"),
    ];
    const titles = titleNodes.map((el) => (el.textContent ?? "").trim()).filter(Boolean);
    expect(titles.length).toBeGreaterThan(0);

    const rawKeys = titles.filter((text) =>
      backendKeys.some((key) => text === key || text === key.replace(/_/g, " ")),
    );
    expect(rawKeys, `标题位出现裸后端 key: ${rawKeys.join(", ")}`).toEqual([]);

    // 页面正文里也不该出现任何 snake_case 后端标识
    const bodyText = document.body.textContent ?? "";
    const leaked = backendKeys.filter((key) => key.includes("_") && bodyText.includes(key));
    expect(leaked, `页面正文出现裸 key: ${leaked.join(", ")}`).toEqual([]);
  });

  it("门 2 —— 恰好 1 个 h1,且 h2 数 ≥ 5", () => {
    renderPage();
    const h1 = screen.getAllByRole("heading", { level: 1 });
    const h2 = screen.getAllByRole("heading", { level: 2 });
    expect(h1).toHaveLength(1);
    expect(h1[0]).toHaveTextContent("设置");
    expect(h2.length, `h2 只有 ${h2.length} 个`).toBeGreaterThanOrEqual(5);
    // 组合门:1 个 h1 + 至少 5 个 h2
    expect(h1.length + h2.length).toBeGreaterThanOrEqual(6);
  });

  it("h2 覆盖每个专门面板(代理/交易所/LLM/四档止盈/PA 心跳/用量/调试)", () => {
    renderPage();
    const h2Text = screen
      .getAllByRole("heading", { level: 2 })
      .map((el) => (el.textContent ?? "").trim());
    for (const expected of [
      "网络代理",
      "交易所凭证",
      "LLM / Agent 配置",
      "四档止盈 (Tiered Trailing)",
      "PA 心跳(无人值守诊断)",
      "每日 LLM 用量上限",
      "调试",
    ]) {
      expect(h2Text, `缺 h2:${expected}`).toContain(expected);
    }
  });
});

describe("设置页 P1:搜索", () => {
  it("门 3 —— 搜索「代理」后可见卡片数下降,且网络代理面板仍在", () => {
    renderPage();
    const sections = () => document.querySelectorAll("[data-settings-section]");
    const before = sections().length;
    expect(before).toBeGreaterThan(5);

    const input = screen.getByRole("searchbox", { name: "搜索设置" });
    fireEvent.change(input, { target: { value: "代理" } });

    const after = sections().length;
    expect(after, "搜索没减少任何分区").toBeLessThan(before);

    const network = document.getElementById("set-network");
    expect(network, "搜「代理」把网络代理面板也过滤掉了").not.toBeNull();
    expect(network?.textContent).toContain("网络代理");
  });

  it("搜索匹配行标签与值(不只标题),并把只读组强制展开", () => {
    renderPage();
    const input = screen.getByRole("searchbox", { name: "搜索设置" });
    fireEvent.change(input, { target: { value: "drawdown" } });

    // risk_alerts.rules.drawdown 是嵌套值,formatScalar 摊成 drawdown=…
    const riskAlerts = document.getElementById("set-group-risk_alerts");
    expect(riskAlerts, "搜索值没命中只读组").not.toBeNull();
    expect(riskAlerts?.textContent).toContain("drawdown");
    // 搜索态不渲染 <details>,组直接可见
    expect(document.querySelector("details")).toBeNull();
  });

  it("无结果给空态而不是一片空白", () => {
    renderPage();
    const input = screen.getByRole("searchbox", { name: "搜索设置" });
    fireEvent.change(input, { target: { value: "zzz不可能命中zzz" } });
    expect(screen.getByText("无匹配设置项")).toBeInTheDocument();
  });
});

describe("设置页 P1:导航", () => {
  it("门 4 —— 点第 13 个 chip 滚到它指向的分区", () => {
    renderPage();
    const nav = screen.getByRole("navigation", { name: "跳转到分组" });
    const chips = within(nav).getAllByRole("button");
    expect(chips.length).toBeGreaterThanOrEqual(13);

    const target = chips[12];
    const targetId = READONLY_GROUP_ORDER[12 - 8]; // 8 个专门面板在前(含研究循环),其后是只读组
    expect(target).toHaveTextContent(GROUP_LABELS[targetId]);

    const scrolled: { el: Element; options?: ScrollIntoViewOptions }[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (options?: ScrollIntoViewOptions) {
      scrolled.push({ el: this, options });
    };
    try {
      fireEvent.click(target);
    } finally {
      Element.prototype.scrollIntoView = original;
    }

    expect(scrolled.length, "点 chip 没有触发任何滚动").toBeGreaterThan(0);
    const hit = scrolled.find((s) => s.el.id === `set-group-${targetId}`);
    expect(hit, `没滚到 set-group-${targetId}(实际滚到 ${scrolled.map((s) => s.el.id).join(", ")})`)
      .toBeDefined();
    expect(hit?.options).toEqual({ block: "start" });
    expect(document.getElementById(`set-group-${targetId}`)).not.toBeNull();
  });

  it("连点同一个 chip 每次都滚(跳转状态不许被吞成一次性)", () => {
    renderPage();
    const nav = screen.getByRole("navigation", { name: "跳转到分组" });
    const target = within(nav).getAllByRole("button")[12];
    const targetId = `set-group-${READONLY_GROUP_ORDER[12 - 8]}`;

    const scrolled: string[] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function () {
      scrolled.push(this.id);
    };
    try {
      fireEvent.click(target);
      fireEvent.click(target);
      fireEvent.click(target);
    } finally {
      Element.prototype.scrollIntoView = original;
    }
    expect(scrolled.filter((id) => id === targetId).length).toBe(3);
  });

  /**
   * 活体探针回归:点「哨兵」后高亮停在「风控告警」。
   *
   * 真因是观察器的两条路径 —— 陈旧占比(节点已卸载/已滚出)压过当前分区,
   * 以及"占比最大"在末段被夹住时永远选到更靠上的一个。这两个在 jsdom 里
   * 都复现不了(没有布局、IntersectionObserver 是空壳),所以这里守的是
   * 修法的可见产物:用户点了哪个 chip,高亮就必须立刻跟到哪个,不等观察器投票。
   */
  it("点击后 chip 高亮立刻跟到被点的那个", () => {
    renderPage();
    const nav = screen.getByRole("navigation", { name: "跳转到分组" });
    const chips = within(nav).getAllByRole("button");
    const target = chips[12];

    const activeBefore = chips.filter((c) => c.getAttribute("aria-current") === "true");
    expect(activeBefore.length).toBe(1);
    expect(target).not.toHaveAttribute("aria-current");

    fireEvent.click(target);

    const activeAfter = within(nav)
      .getAllByRole("button")
      .filter((c) => c.getAttribute("aria-current") === "true");
    expect(activeAfter, "点击后不是唯一高亮").toHaveLength(1);
    expect(activeAfter[0]).toHaveTextContent((target.textContent ?? "").trim());
  });

  it("chip 指向的每个 id 在页面里都真实存在", () => {
    renderPage();
    const nav = screen.getByRole("navigation", { name: "跳转到分组" });
    const missing = within(nav)
      .getAllByRole("button")
      .map((_, i) => i)
      .filter(() => false); // 占位,下面按名称解析
    expect(missing).toEqual([]);

    // 只读组在折叠区里,展开后才在 DOM 中
    fireEvent.click(screen.getByText("高级配置（只读）"));
    const ids = Array.from(
      document.querySelectorAll("[data-settings-section]"),
    ).map((el) => el.id);
    for (const id of ids) {
      expect(id, "分区缺 id,chip 无处可跳").not.toBe("");
    }
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("设置页 P1:高级配置折叠", () => {
  it("门 5 —— 默认折叠;展开后每个只读组各有一张卡", () => {
    renderPage();
    const details = document.querySelector("details");
    expect(details, "高级配置不是 <details>").not.toBeNull();
    expect((details as HTMLDetailsElement).open, "高级配置默认就是展开的").toBe(false);

    // 只有"有没有展开"这一件事能控住可见性 —— 组节点始终保持挂载(<details>
    // 收起时浏览器不绘制其内容,但 DOM 里在)。搜索态才走条件渲染。
    const collapsedGroups = document.querySelectorAll('[id^="set-group-"]');
    expect(collapsedGroups.length).toBe(READONLY_GROUP_ORDER.length);

    const reopened = document.querySelector("details") as HTMLDetailsElement;
    reopened.open = true;
    fireEvent(reopened, new Event("toggle"));

    // 展开后仍在 DOM 中且数量不变 —— 折叠不改结构,只改绘制
    const groups = Array.from(document.querySelectorAll('[id^="set-group-"]'));
    // 计划书写的是 10 个;后端实际有 11 个可只读段(计划漏了 mission_worker)
    expect(groups.length, `只读组只有 ${groups.length} 个`).toBeGreaterThanOrEqual(10);
    expect(groups.length).toBe(READONLY_GROUP_ORDER.length);
    expect(groups.map((g) => g.id)).toEqual(
      READONLY_GROUP_ORDER.map((key) => `set-group-${key}`),
    );
  });
});

describe("设置页 P3:四档止盈的百分比空间", () => {
  const pctField = (label: string) => screen.getByLabelText(label) as HTMLInputElement;

  it("读数/滑块/数字框三处都在百分比空间,存储仍是小数", () => {
    renderPage();
    const panel = screen
      .getByRole("heading", { level: 2, name: /四档止盈/ })
      .closest("[data-settings-section]") ?? document.body;

    // 存储默认 0.02 → 界面 2%
    expect(pctField("硬止损比例").value).toBe("2");
    expect(pctField("T1 触发阈值").value).toBe("1.5");
    expect(pctField("T1 回撤比例").value).toBe("50");

    // 滑块边界也换算了:硬止损 min 0.005 → 0.5,max 0.1 → 10
    const slider = screen.getByLabelText("硬止损比例（滑块）") as HTMLInputElement;
    expect(slider.min).toBe("0.5");
    expect(slider.max).toBe("10");
    expect(slider.value).toBe("2");

    // 同一行里不该再有小数比例原值
    const text = (panel as HTMLElement).textContent ?? "";
    expect(text).not.toContain("0.02");
    expect(text).toContain("2%");
  });

  it("秒字段不跟着换算", () => {
    renderPage();
    expect(pctField("最小emit间隔(秒)").value).toBe("30");
  });

  it("输入 5(百分比)→ 保存时回存 0.05(比例)", async () => {
    const { putTieredConfig } = await import("@/lib/api/settings");
    renderPage();

    fireEvent.change(pctField("硬止损比例"), { target: { value: "5" } });
    expect(pctField("硬止损比例").value).toBe("5");

    // 页面上不止一个「保存」按钮(每个设置面板各有一个),限定在四档止盈分区内。
    const section = screen
      .getByRole("heading", { level: 2, name: /四档止盈/ })
      .closest("[data-settings-section]") as HTMLElement;
    fireEvent.click(within(section).getByRole("button", { name: "保存" }));
    await vi.waitFor(() => expect(putTieredConfig).toHaveBeenCalled());

    const saved = (putTieredConfig as unknown as ReturnType<typeof vi.fn>).mock
      .calls.at(-1)?.[0] as Record<string, number>;
    expect(saved.tiered_hard_stop_pct).toBeCloseTo(0.05, 10);
    // 未被触碰的字段仍是原比例,没被顺手 ×100
    expect(saved.tiered_low_tier_threshold).toBeCloseTo(0.015, 10);
    expect(saved.tiered_min_emit_interval_seconds).toBe(30);
  });
});

describe("设置页 P1:en 模式", () => {
  it("门 6 —— 分组名与导航 chip 全部有译文,不留中文", () => {
    renderPage({ locale: "en" });

    const cjk = /[一-鿿]/u;
    const nav = screen.getByRole("navigation", { name: "Jump to section" });
    const chips = within(nav).getAllByRole("button");
    const untranslatedChips = chips
      .map((el) => (el.textContent ?? "").trim())
      .filter((text) => cjk.test(text));
    expect(untranslatedChips, `chip 没译文: ${untranslatedChips.join(", ")}`).toEqual([]);

    // 展开后逐个只读组标题也必须是英文
    const details = document.querySelector("details") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle"));

    const groupHeadings = Array.from(
      document.querySelectorAll('[id^="set-group-"] h2'),
    ).map((el) => (el.textContent ?? "").trim());
    expect(groupHeadings.length).toBeGreaterThanOrEqual(10);
    const untranslated = groupHeadings.filter((text) => cjk.test(text));
    expect(untranslated, `只读组名没译文: ${untranslated.join(", ")}`).toEqual([]);
  });

  it("en 译文表确实覆盖了每个分组名(不是靠兜底)", () => {
    const cjk = /[一-鿿]/u;
    for (const [key, label] of Object.entries(GROUP_LABELS)) {
      // DEX / AI 这类中英同形缩写,en 下本来就该原样显示 —— 不算漏译。
      if (!cjk.test(label)) continue;
      const en = translateUiText("en", label);
      expect(en, `${key} 的组名没有译文`).not.toBe(label);
      expect(en, `${key} 的译文里还有中文`).not.toMatch(cjk);
    }
  });

  it("en 模式下搜索仍按英文文案命中(搜索设置 → network)", () => {
    renderPage({ locale: "en" });
    const input = screen.getByRole("searchbox", { name: "Search settings" });
    fireEvent.change(input, { target: { value: "proxy" } });
    expect(document.getElementById("set-network")?.textContent).toContain("Network proxy");
  });
});
