import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { CHAINS } from "@/lib/navigation-chains";

// === shared mock state (hoisted) ===========================================
const h = vi.hoisted(() => ({
  pathname: "/research/strat",
  favorites: [] as string[],
  wsOptions: [] as Array<Record<string, unknown>>,
  setSymBase: vi.fn(),
  symBase: "BTC",
  // 因子挖掘运行中徽标:sidebar 订阅 store.sessions(真实 store 会拉网络,测试里换成纯数组)
  alphaSessions: [] as Array<{ session_id: string; status: string; started_at?: number }>,
  workflow: {
    template: { id: "bb_v3", name: "布林带升级版", category: "oscillator" } as { id: string; name: string; category: string } | null,
    factors: null as unknown,
    optimization: null as unknown,
    backtest: null as unknown,
  },
}));

// === mocks (must come before import of Sidebar) =============================
vi.mock("next/navigation", () => ({
  usePathname: () => h.pathname,
}));

vi.mock("@/lib/hooks/use-symbol-favorites", () => ({
  useSymbolFavorites: () => ({
    favorites: h.favorites,
    toggleFavorite: vi.fn(),
    isFavorite: (s: string) => h.favorites.includes(s),
    isLoading: false,
  }),
}));

// 自选 ticker 订阅走 singleton WS — 测试里捕获 options 以便手动推消息
vi.mock("@/lib/ws/use-markets-ws", () => ({
  useMarketsWs: (opts: Record<string, unknown>) => {
    h.wsOptions.push(opts);
    return { state: { connected: false, streamState: "recovering", lastMessageAt: null } };
  },
}));

// sidebar 调用 useWorkflowStore() 无 selector;部分页面用 selector。两态都支持。
vi.mock("@/lib/store/workflow", () => ({
  useWorkflowStore: (selector?: (s: unknown) => unknown) => {
    const full = h.workflow;
    return selector ? selector(full) : full;
  },
}));

vi.mock("@/lib/store/context", () => ({
  useContextStore: () => ({
    pageFilter: { symbol: h.symBase },
    setSymBase: (s: string) => h.setSymBase(s),
  }),
}));

vi.mock("@/components/shared/page-renderer", () => ({
  preloadPage: () => {},
}));

// 只替换 hook,保留真实的 isActive(徽标的判定逻辑是要被测的生产代码)
vi.mock("@/lib/store/alpha-mining", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/store/alpha-mining")>();
  return {
    ...actual,
    useAlphaMiningStore: (selector: (s: { sessions: unknown[] }) => unknown) =>
      selector({ sessions: h.alphaSessions }),
  };
});

// === import AFTER mocks ====================================================
import { Sidebar } from "./sidebar";

describe("Sidebar 场景链式导航", () => {
  beforeEach(() => {
    h.pathname = "/research/strat";
    h.favorites = [];
    h.wsOptions.length = 0;
    h.alphaSessions.length = 0;
    window.localStorage.clear(); // kf 刀5 持久化——防残留 state 污染（裁决 0929：此行不可丢）
    h.workflow.template = { id: "bb_v3", name: "布林带升级版", category: "oscillator" };
    h.workflow.factors = null;
    h.workflow.optimization = null;
    h.workflow.backtest = null;
  });

  it("渲染 research 场景的链头(研究入口/策略开发/因子挖掘/回测调优/风险分析)", () => {
    render(<Sidebar scene="research" />);
    for (const chain of CHAINS.research) {
      // 链头 label 可能与链内 step 页标题同名(如"因子挖掘"),用 getAllByText 断言至少出现一次
      expect(screen.getAllByText(chain.label).length).toBeGreaterThan(0);
    }
  });

  it("链内步骤渲染为 page-item", () => {
    render(<Sidebar scene="research" />);
    // 策略开发链首步 strat → "策略工厂"
    expect(screen.getByText("策略工厂")).toBeTruthy();
  });

  it("更多组默认折叠,点击展开显示未入链页", () => {
    render(<Sidebar scene="research" />);
    // 默认折叠:更多组 body 不可见
    const moreTitle = screen.getByText("更多");
    const moreGroup = moreTitle.closest(".more-group");
    expect(moreGroup).toBeTruthy();
    const body = moreGroup!.querySelector(".chain-body");
    expect(body!.classList.contains("collapsed")).toBe(true);
    // 点击展开
    fireEvent.click(moreTitle);
    expect(body!.classList.contains("collapsed")).toBe(false);
  });

  it("链可折叠:点击链头切换 collapsed 状态", () => {
    render(<Sidebar scene="research" />);
    const stratChainTitle = screen.getByText("策略开发");
    const chainGroup = stratChainTitle.closest(".nav-chain-title")!.parentElement!;
    const body = chainGroup.querySelector(".chain-body")!;
    expect(body.classList.contains("collapsed")).toBe(false);
    fireEvent.click(stratChainTitle);
    expect(body.classList.contains("collapsed")).toBe(true);
  });

  it("workflow 链步骤显示 gold ✓ 当 store 有进度", () => {
    // 优化/回测有进度
    h.workflow.optimization = { id: "opt-1" };
    h.workflow.backtest = { id: "bt-1" };
    render(<Sidebar scene="research" />);
    const checks = screen.getAllByText("✓");
    // optimizer/scanner/tuning 三项 optimization 有值 → 3 ✓;backtests 项不在 research 场景(在 trade)
    expect(checks.length).toBe(3);
  });

  it("workflow 无进度时无 ✓", () => {
    render(<Sidebar scene="research" />);
    expect(screen.queryAllByText("✓").length).toBe(0);
  });

  it("workflow 链渲染带序号的步骤(ai-org 链 8 步,序号 1-8;episodes 为 q 合并追加)", () => {
    render(<Sidebar scene="research" />);
    const aiOrgTitle = screen.getByText("AI 组织");
    const chainGroup = aiOrgTitle.closest(".nav-chain-title")!.parentElement!;
    const numbers = Array.from(chainGroup.querySelectorAll(".wf-step-n")).map(
      (el) => el.textContent,
    );
    expect(numbers).toEqual(["1", "2", "3", "4", "5", "6", "7", "8"]);
  });

  it("AI 组织链头显示「流程」徽章", () => {
    render(<Sidebar scene="research" />);
    const aiOrgTitle = screen.getByText("AI 组织").closest(".nav-chain-title")!;
    expect(aiOrgTitle.querySelector(".wf-badge")).toBeTruthy();
  });

  it("非 workflow 链(研究入口/因子挖掘)无序号无徽章", () => {
    render(<Sidebar scene="research" />);
    const rschTitle = screen.getByText("研究入口").closest(".nav-chain-title")!;
    expect(rschTitle.querySelector(".wf-badge")).toBeNull();
    const rschGroup = rschTitle.parentElement!;
    expect(rschGroup.querySelectorAll(".wf-step-n").length).toBe(0);
    const alphaTitle = screen.getByText("因子挖掘").closest(".nav-chain-title")!;
    expect(alphaTitle.querySelector(".wf-badge")).toBeNull();
  });

  it("分组折叠态持久化:点击折叠后写 localStorage,刷新(重渲染)后恢复", () => {
    // 首次挂载:展开「更多」组（默认折叠），再折叠「策略开发」链
    const { unmount } = render(<Sidebar scene="research" />);
    fireEvent.click(screen.getByText("策略开发"));
    expect(JSON.parse(window.localStorage.getItem("selenespace-sidebar-collapsed")!)).toEqual(
      expect.objectContaining({ "chain:strategy-dev": true }),
    );
    unmount();
    // 重新挂载 = 模拟刷新:折叠态应恢复
    render(<Sidebar scene="research" />);
    const body = screen.getByText("策略开发").closest(".nav-chain-title")!.parentElement!.querySelector(".chain-body")!;
    expect(body.classList.contains("collapsed")).toBe(true);
  });

  it("自选区块保持在链分组之后", () => {
    render(<Sidebar scene="research" />);
    const wlTitle = screen.getByText("自选 · 按市场");
    expect(wlTitle).toBeTruthy();
    // 无自选时显示空态
    expect(screen.getByText(/暂无自选品种/)).toBeTruthy();
  });

  it("自选按 quote 币种分组（USDT/USDC/其他），组内字母序，白名单分组退役", () => {
    h.favorites = ["PEPE/USDT", "BTC/USDT", "ETH/USDC", "DOGE/USDT"];
    render(<Sidebar scene="research" />);
    // quote 分组头（组头在 .wl-group-header 内，行内 .wl-exch 同文本不干扰）
    expect(screen.getByText("USDT", { selector: ".wl-group-header span" })).toBeTruthy();
    expect(screen.getByText("USDC", { selector: ".wl-group-header span" })).toBeTruthy();
    // 白名单分组已删除
    expect(screen.queryByText("CEX 主力")).toBeNull();
    expect(screen.queryByText("ALT 热点")).toBeNull();
    // USDT 组内字母序:BTC → DOGE → PEPE(组内三行,按 DOM 顺序)
    const usdtHeader = screen.getByText("USDT", { selector: ".wl-group-header span" }).closest(".wl-group")!;
    const syms = Array.from(usdtHeader.querySelectorAll(".wl-sym")).map((el) => el.textContent);
    expect(syms).toEqual(["BTC", "DOGE", "PEPE"]);
  });

  it("裸符号 favorites 兜底视为 /USDT 分组", () => {
    h.favorites = ["BTC"];
    render(<Sidebar scene="research" />);
    expect(screen.getByText("USDT", { selector: ".wl-group-header span" })).toBeTruthy();
    expect(screen.getByText("BTC")).toBeTruthy();
  });

  it("自选分组头可折叠:点击切换 collapsed", () => {
    h.favorites = ["BTC/USDT"];
    render(<Sidebar scene="research" />);
    const usdtHeader = screen.getByText("USDT", { selector: ".wl-group-header span" }).closest(".wl-group-header")!;
    const body = usdtHeader.parentElement!.querySelector(".wl-group-body")!;
    expect(body.classList.contains("collapsed")).toBe(false);
    fireEvent.click(usdtHeader);
    expect(body.classList.contains("collapsed")).toBe(true);
  });

  it("整区头可折叠:点击自选区标题后分组体隐藏", () => {
    h.favorites = ["BTC/USDT"];
    render(<Sidebar scene="research" />);
    const head = screen.getByText("自选 · 按市场").closest(".wl-section-head")!;
    expect(head.classList.contains("collapsed")).toBe(false);
    fireEvent.click(head);
    expect(head.classList.contains("collapsed")).toBe(true);
    // 分组与行都随整区折叠消失
    expect(screen.queryByText("USDT")).toBeNull();
  });

  it("watch 场景自选区渲染在导航链之前,research 场景保持在后", () => {
    h.favorites = ["BTC/USDT"];
    const { container } = render(<Sidebar scene="watch" />);
    const nav = container.querySelector(".sb-body")!;
    const wlIdx = Array.from(nav.children).findIndex((el) => el.matches(".wl-section-head") || el.querySelector(".wl-section-head"));
    const chainIdx = Array.from(nav.children).findIndex((el) => el.querySelector(".nav-chain-title"));
    expect(wlIdx).toBeGreaterThanOrEqual(0);
    expect(chainIdx).toBeGreaterThanOrEqual(0);
    expect(wlIdx).toBeLessThan(chainIdx);

    // research 场景:链在前,自选在后
    const { container: c2 } = render(<Sidebar scene="research" />);
    const nav2 = c2.querySelector(".sb-body")!;
    const wlIdx2 = Array.from(nav2.children).findIndex((el) => el.matches(".wl-section-head") || el.querySelector(".wl-section-head"));
    const chainIdx2 = Array.from(nav2.children).findIndex((el) => el.querySelector(".nav-chain-title"));
    expect(wlIdx2).toBeGreaterThan(chainIdx2);
  });

  it("自选行 price/chg 接 WS ticker:缓冲 1s 后填充,无数据显示 —", () => {
    vi.useFakeTimers();
    try {
      h.favorites = ["BTC/USDT"];
      render(<Sidebar scene="research" />);
      // 无数据:price 与 chg 均显示 —
      expect(screen.getByText("—", { selector: ".wl-price" })).toBeTruthy();
      expect(screen.getByText("—", { selector: ".wl-chg" })).toBeTruthy();
      // 捕获的 WS options 携带完整对订阅
      expect(h.wsOptions.length).toBeGreaterThan(0);
      const opts = h.wsOptions[0];
      expect(opts.symbols).toEqual(["BTC/USDT"]);
      expect(opts.onMessage).toBeTypeOf("function");
      // 推一条 ticker 消息
      (opts.onMessage as (m: unknown) => void)({
        type: "ticker",
        data: { symbol: "BTC/USDT", last_price: 63000.5, change_24h_pct: 2.35 },
      });
      // 节流 1s 内不刷
      act(() => { vi.advanceTimersByTime(500); });
      expect(screen.getByText("—", { selector: ".wl-price" })).toBeTruthy();
      // 1s 后填充
      act(() => { vi.advanceTimersByTime(600); });
      expect(screen.getByText("63,001")).toBeTruthy();
      expect(screen.getByText("+2.35%")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("点击自选行 → setSymBase(裸符号)", () => {
    h.favorites = ["BTC/USDT"];
    render(<Sidebar scene="research" />);
    fireEvent.click(screen.getByText("BTC"));
    expect(h.setSymBase).toHaveBeenCalledWith("BTC");
  });

  it("因子挖掘链无活跃任务时不显示运行徽标", () => {
    render(<Sidebar scene="research" />);
    const alphaTitle = screen.getByText("因子挖掘").closest(".nav-chain-title")!;
    expect(alphaTitle.querySelector(".alpha-run-badge")).toBeNull();
  });

  it("因子挖掘链在运行任务时显示计数徽标(仅计活跃态)", () => {
    h.alphaSessions.push(
      { session_id: "a1", status: "running", started_at: 1 },
      { session_id: "a2", status: "starting", started_at: 2 },
      { session_id: "a3", status: "completed", started_at: 3 },
      { session_id: "a4", status: "failed", started_at: 4 },
    );
    render(<Sidebar scene="research" />);
    const alphaTitle = screen.getByText("因子挖掘").closest(".nav-chain-title")!;
    const badge = alphaTitle.querySelector(".alpha-run-badge")!;
    expect(badge).toBeTruthy();
    // 4 个会话里只有 running/starting 是活跃 → 2
    expect(badge.textContent).toBe("2");
    expect(badge.getAttribute("aria-label")).toBe("2 个挖矿任务运行中");
  });

  it("运行徽标只出现在因子挖掘链,不污染其他链", () => {
    h.alphaSessions.push({ session_id: "a1", status: "running", started_at: 1 });
    render(<Sidebar scene="research" />);
    expect(screen.getByText("因子挖掘").closest(".nav-chain-title")!.querySelector(".alpha-run-badge")).toBeTruthy();
    const aiOrg = screen.getByText("AI 组织").closest(".nav-chain-title")!;
    expect(aiOrg.querySelector(".alpha-run-badge")).toBeNull();
    expect(aiOrg.querySelector(".wf-badge")).toBeTruthy();
  });
});
