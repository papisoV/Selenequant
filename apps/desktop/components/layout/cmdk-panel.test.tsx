import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

// === shared mock state (hoisted so factories see them) =====================
const h = vi.hoisted(() => ({
  recordedUrls: [] as string[],
  mockApiData: {} as Record<string, unknown>,
  mockRouter: { push: vi.fn() },
  mockFavorites: [] as string[],
  mockToggleFavorite: vi.fn(),
  mockRecents: [] as Array<{ symbol: string; market: string; name?: string }>,
  mockRecordRecent: vi.fn(),
  mockSetSymBase: vi.fn(),
  mockSetSymbolPick: vi.fn(),
  mockMutate: vi.fn(),
}));

// === mocks =================================================================
// cmdk 直接用 useSWR（keepPreviousData），mock swr 走同一套 h.mockApiData
vi.mock("swr", () => ({
  default: (key: string | null, fn: unknown) => {
    if (key) h.recordedUrls.push(key);
    return {
      data: key ? (h.mockApiData[key] ?? null) : null,
      error: undefined,
      isLoading: false,
      mutate: h.mockMutate,
    };
  },
}));

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string | null) => {
    if (url) h.recordedUrls.push(url);
    return {
      data: url ? (h.mockApiData[url] ?? null) : null,
      error: undefined,
      isLoading: false,
      mutate: h.mockMutate,
    };
  },
  fetcher: (...args: unknown[]) => hFetcher.fn(...args),
}));

vi.mock("@/lib/hooks/use-symbol-favorites", () => ({
  useSymbolFavorites: () => ({
    favorites: h.mockFavorites,
    toggleFavorite: h.mockToggleFavorite,
    isFavorite: (s: string) => h.mockFavorites.includes(s),
    isLoading: false,
  }),
  // cmdk 合成行用 toPair 判精确命中；与真实现同语义（crypto 补 /USDT，非加密原样大写）
  toPair: (s: string) => {
    const t = s.trim().toUpperCase();
    if (!t) return s.trim();
    if (/^(BTC|ETH|SOL|BNB|XRP|DOGE|ADA|AVAX|LINK|MATIC|DOT|UNI|FIL|APT|ARB|OP|NEAR|SUI|TRX|TON|SHIB|PEPE|ATOM|LTC)$/.test(t)) return `${t}/USDT`;
    return t;
  },
}));

vi.mock("@/lib/hooks/use-recent-symbols", () => ({
  useRecentSymbols: () => ({
    recents: h.mockRecents,
    recordRecent: h.mockRecordRecent,
    clearRecents: vi.fn(),
  }),
}));

vi.mock("@/lib/store/market", () => ({
  useMarketStore: (sel: (s: Record<string, unknown>) => unknown) =>
    sel?.({ setSymBase: h.mockSetSymBase, setSymbolPick: h.mockSetSymbolPick }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: h.mockRouter.push }),
}));

// 透明性批3-11: 研究动作拍板走 apiPost;needs_input 拉取走 fetcher(可注入)
const hApiPost = vi.hoisted(() => ({ post: vi.fn() }));
const hFetcher = vi.hoisted(() => ({ fn: vi.fn() }));
vi.mock("@/lib/api-client", () => ({
  apiPost: (...args: unknown[]) => hApiPost.post(...args),
  api: vi.fn(),
}));

// === import AFTER mocks ====================================================
import { CmdkPanel } from "./cmdk-panel";
import { I18nProvider } from "@/components/i18n/i18n-provider";

const HOT_URL = "/api/v1/chart/symbols?q=";

function openPanel() {
  act(() => {
    window.dispatchEvent(new CustomEvent("cmdk-open"));
  });
}

function typeQuery(value: string) {
  fireEvent.change(screen.getByRole("combobox"), { target: { value } });
  act(() => vi.advanceTimersByTime(300));
}

describe("CmdkPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    h.recordedUrls.length = 0;
    h.mockFavorites.length = 0;
    h.mockRecents.length = 0;
    h.mockRouter.push.mockClear();
    h.mockToggleFavorite.mockClear();
    h.mockRecordRecent.mockClear();
    h.mockSetSymBase.mockClear();
    h.mockSetSymbolPick.mockClear();
    h.mockMutate.mockClear();
    hApiPost.post.mockReset();
    hFetcher.fn.mockReset();
    hFetcher.fn.mockResolvedValue(null);
    Object.keys(h.mockApiData).forEach((k) => delete h.mockApiData[k]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("打开默认交易对模式：空态置顶 自选/最近 + 热门", () => {
    h.mockFavorites.push("BTC");
    h.mockRecents.push({ symbol: "AAPL", market: "us", name: "Apple Inc" });
    h.mockApiData[HOT_URL] = {
      query: "",
      items: [{ symbol: "SPY", market: "us", name: "SPDR S&P 500 ETF" }],
    };

    render(<CmdkPanel />);
    openPanel();

    expect(screen.getByText("自选")).toBeTruthy();
    expect(screen.getByText("最近")).toBeTruthy();
    expect(screen.getByText("热门")).toBeTruthy();
    expect(screen.getByText("BTC")).toBeTruthy();
    expect(screen.getByText("Apple Inc")).toBeTruthy();
    expect(screen.getByText("SPY")).toBeTruthy();
  });

  it("最近列表旧脏数据 market 重算：BTC/USDT 存了 us 仍显示 Binance·永续", () => {
    // 刀4 前斜杠对被误存 "us"；routeSymbol 可确定性路由 crypto，应以重算为准
    h.mockRecents.push({ symbol: "BTC/USDT", market: "us", name: "BTC/USDT" });

    render(<CmdkPanel />);
    openPanel();

    const badge = screen.getByTestId("sym-src-badge");
    expect(badge.textContent).toContain("Binance");
  });

  it("输入 → debounce 后调 /chart/symbols?q= 并按市场分组显示", () => {
    h.mockApiData["/api/v1/chart/symbols?q=btc"] = {
      query: "btc",
      items: [{ symbol: "BTC", market: "crypto", name: "比特币" }],
    };

    render(<CmdkPanel />);
    openPanel();
    typeQuery("btc");

    expect(h.recordedUrls).toContain("/api/v1/chart/symbols?q=btc");
    expect(screen.getByText("搜索")).toBeTruthy();
    expect(screen.getByText("比特币")).toBeTruthy();
    // 行内市场标签（市场分类 chips 也叫"加密"，用 testid 区分取行内那个）
    expect(screen.getByTestId("sym-src-badge").textContent).toContain("Binance");
  });

  it("回车选中符号 → recordRecent + setSymBase + setSymbolPick + 跳超级图表", () => {
    h.mockApiData[HOT_URL] = {
      query: "",
      items: [{ symbol: "SPY", market: "us", name: "SPDR" }],
    };

    render(<CmdkPanel />);
    openPanel();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "ArrowDown" });
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });

    expect(h.mockRecordRecent).toHaveBeenCalledWith({ symbol: "SPY", market: "us", name: "SPDR" });
    expect(h.mockSetSymBase).toHaveBeenCalledWith("SPY");
    expect(h.mockSetSymbolPick).toHaveBeenCalledWith({ symbol: "SPY", market: "us" });
    expect(h.mockRouter.push).toHaveBeenCalledWith("/watch/kf");
  });

  it("自选星标点击 → toggleFavorite 且不跳转", () => {
    h.mockFavorites.push("BTC");

    render(<CmdkPanel />);
    openPanel();

    fireEvent.click(screen.getAllByTestId("star-toggle")[0]);
    expect(h.mockToggleFavorite).toHaveBeenCalledWith("BTC");
    expect(h.mockRouter.push).not.toHaveBeenCalled();
  });

  it("切到页面模式 → 回车跳页面路由", () => {
    render(<CmdkPanel />);
    openPanel();
    fireEvent.click(screen.getByTestId("mode-chip-page"));

    expect(screen.getByPlaceholderText(/搜索页面/)).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(h.mockRouter.push).toHaveBeenCalledWith(expect.stringMatching(/^\/.+\/.+$/));
  });

  it("Tab 键切模式：symbol → page → action → symbol(透明性批3-11 三模式)", () => {
    render(<CmdkPanel />);
    openPanel();

    expect(screen.getByPlaceholderText(/搜索交易对/)).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Tab" });
    expect(screen.getByPlaceholderText(/搜索页面/)).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Tab" });
    expect(screen.getByPlaceholderText(/研究决策/)).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Tab" });
    expect(screen.getByPlaceholderText(/搜索交易对/)).toBeTruthy();
  });

  it("搜索失败（后端不可达）→ 显示错误 + 重试触发 mutate", () => {
    render(<CmdkPanel />);
    openPanel();
    typeQuery("btc");

    expect(screen.getByText("搜索服务暂不可用")).toBeTruthy();
    fireEvent.click(screen.getByText("重试"));
    expect(h.mockMutate).toHaveBeenCalled();
  });

  it("研究动作模式:needs_input mission 拍板 approve(透明性批3-11)", async () => {
    vi.useRealTimers();
    hFetcher.fn.mockResolvedValue({
      missions: [
        { id: "mis-1", title: "验证 RSI 背离", question: "有边缘吗?", phase: "needs_input" },
      ],
    });
    hApiPost.post.mockResolvedValue({ mission: { id: "mis-1" } });

    render(<CmdkPanel />);
    openPanel();
    fireEvent.click(screen.getByTestId("mode-chip-action"));

    const row = await screen.findByTestId("action-mission-row");
    fireEvent.click(row);
    fireEvent.click(screen.getByTestId("decision-approve"));

    await screen.findByTestId("decision-done");
    expect(hApiPost.post).toHaveBeenCalledWith(
      "/api/v1/research/missions/mis-1/decision",
      { action: "approve", note: null }
    );
  });

  it("研究动作模式:空态诚实提示", async () => {
    vi.useRealTimers();
    hFetcher.fn.mockResolvedValue({ missions: [] });

    render(<CmdkPanel />);
    openPanel();
    fireEvent.click(screen.getByTestId("mode-chip-action"));

    await screen.findByText(/当前没有需要拍板的调查/);
  });

  it("类符号无命中 → 合成行兜底可跳图；非类符号（中文）无命中回车 → 不静默跳页面路由", () => {
    h.mockApiData["/api/v1/chart/symbols?q=zzz"] = { query: "zzz", items: [] };
    render(<CmdkPanel />);
    openPanel();
    // 类符号 zzz：合成行存在，回车跳图
    typeQuery("zzz");
    expect(screen.getByText("添加自选")).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(h.mockRouter.push).toHaveBeenCalledWith("/watch/kf");

    // 非类符号（中文）：无合成行，回车不跳
    openPanel();
    typeQuery("你好世界");
    expect(screen.queryByText("添加自选")).toBeNull();
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(h.mockRouter.push).toHaveBeenCalledTimes(1);
  });

  it("类符号输入无后端命中 → 合成添加自选行：星标可 toggle，回车可跳图", () => {
    h.mockApiData["/api/v1/chart/symbols?q=pepe"] = { query: "pepe", items: [] };
    render(<CmdkPanel />);
    openPanel();
    typeQuery("pepe");

    expect(screen.getByText("添加自选")).toBeTruthy();
    // 行存裸符号 PEPE，toggle 后 hook 内部归一化 PEPE/USDT（mock 直收实参）
    fireEvent.click(screen.getByTestId("star-toggle"));
    expect(h.mockToggleFavorite).toHaveBeenCalledWith("PEPE");
    // 回车 → 跳图（不走"不静默回落"路径）
    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    expect(h.mockRouter.push).toHaveBeenCalledWith("/watch/kf");
  });

  it("crypto 符号已在自选 → 不合成添加行（精确命中）", () => {
    h.mockFavorites.push("BTC/USDT");
    h.mockApiData["/api/v1/chart/symbols?q=btc"] = { query: "btc", items: [] };
    render(<CmdkPanel />);
    openPanel();
    typeQuery("btc");

    expect(screen.queryByText("添加自选")).toBeNull();
  });

  // 1013 事故："a股还是不能显示" —— 自选里第一条是脏数据 "300750.SZ"，旧实现
  // 输入查询词时自选/最近不过滤，回车选中的是"列表第一行"=第一条自选，而不是
  // 用户搜的品种。修法：自选/最近也按 query 过滤。
  it("输入查询词 → 自选/最近也参与过滤，回车选中搜到的品种而非第一条自选", () => {
    h.mockFavorites.push("300750.SZ", "AAPL"); // 脏数据带后缀
    h.mockApiData["/api/v1/chart/symbols?q=600519"] = {
      query: "600519",
      items: [{ symbol: "600519", market: "ashare", name: "贵州茅台" }],
    };

    render(<CmdkPanel />);
    openPanel();
    typeQuery("600519");

    // 自选里没有匹配 600519 的项 → 只应剩"搜索"组的 600519
    expect(screen.queryByText("300750.SZ")).toBeNull();
    expect(screen.queryByText("AAPL")).toBeNull();
    expect(screen.getByText("600519")).toBeTruthy();

    fireEvent.keyDown(screen.getByRole("combobox"), { key: "Enter" });
    // 选中的是搜到的 A股品种，且交接的是归一化裸符号
    expect(h.mockSetSymbolPick).toHaveBeenCalledWith({ symbol: "600519", market: "ashare" });
    expect(h.mockSetSymBase).toHaveBeenCalledWith("600519");
    expect(h.mockRouter.push).toHaveBeenCalledWith("/watch/kf");
  });

  it("自选脏数据 300750.SZ → 行内展示与交接都用裸符号 300750，市场按 A股", () => {
    h.mockFavorites.push("300750.SZ");

    render(<CmdkPanel />);
    openPanel();

    // 展示归一化后的裸符号（后端只认 300750），且不再误标为美股
    expect(screen.getByText("300750")).toBeTruthy();
    expect(screen.queryByText("300750.SZ")).toBeNull();
    // 点击该行 → 交接裸符号
    fireEvent.click(screen.getByText("300750"));
    expect(h.mockSetSymbolPick).toHaveBeenCalledWith({ symbol: "300750", market: "ashare" });
  });

  // 2026-10-21 用户截图:"这个正在英文界面很别扭,间距没做好" ——
  // en 下 A-Shares/US Stocks/Commodities 在 28px 定宽列里折成两行。
  // 注意:路由级的 144 页泄漏/溢出扫描看不见本面板(它只在 ⌘K 后存在),
  // 故这里锁的是**渲染结果**,不是源码写法。
  it("英文界面:市场标签不换行，分组标题与页脚计数不留中文", async () => {
    window.localStorage.setItem("selenespace-locale", "en");
    h.mockApiData[HOT_URL] = {
      query: "",
      items: [
        { symbol: "600519", market: "ashare", name: "贵州茅台" },
        { symbol: "AAPL", market: "us", name: "Apple Inc" },
        { symbol: "AU0", market: "commodities", name: "黄金" },
      ],
    };

    try {
      // 裸渲染时 useUiText 无 provider → 恒等;要验英文必须包 provider。
      // locale 在 mount 后由 effect 从 localStorage 恢复,故用 findBy 等一帧。
      render(
        <I18nProvider>
          <CmdkPanel />
        </I18nProvider>,
      );
      openPanel();
      // 本文件用假定时器,findBy* 的轮询不会自己走;推进一拍让 effect 完成 locale 恢复
      act(() => vi.advanceTimersByTime(50));

      // 词表补全:此前 热门/搜索/添加自选/个符号 全缺,同一面板里 自选/最近 却已翻译
      expect(screen.getByText("Popular")).toBeTruthy();
      expect(screen.getByText("3 symbols")).toBeTruthy();

      // 标签列按 locale 定宽:英文自然宽最长 68.2px(Commodities),列宽取 72;
      // 关键是**各行同宽** —— 自适应宽度会让每行符号起点 x 参差(实测 477/488/493)
      const rows = screen.getAllByTestId("symbol-row");
      const labels = rows.map((r) => r.querySelector<HTMLElement>("[data-testid='market-label']")!);
      expect(labels[0].textContent).toBe("A-Shares");
      expect(labels[0].style.whiteSpace).toBe("nowrap");
      expect(labels[0].style.width).toBe("72px");
      // 同一面板里所有行的标签列宽一致(行对齐),且不与中文列宽混用
      expect(new Set(labels.map((l) => l.style.width)).size).toBe(1);
      expect(screen.getAllByTestId("sym-src-badge")[0].textContent).toBe("Tonghuashun · Stock");
    } finally {
      window.localStorage.removeItem("selenespace-locale");
    }
  });
});
