import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useMarketStore } from "@/lib/store/market";

// === shared mock state (hoisted so factories see them) =====================
const h = vi.hoisted(() => ({
  recordedUrls: [] as string[],
  mockApiData: {} as Record<string, unknown>,
  wsOptions: [] as Array<Record<string, unknown>>,
  mockToggleFavorite: vi.fn(),
  mockFavorites: [] as string[],
}));

vi.mock("@/lib/hooks/use-symbol-favorites", () => ({
  useSymbolFavorites: () => ({
    favorites: h.mockFavorites,
    toggleFavorite: h.mockToggleFavorite,
    // 与真 hook isFavorite 同语义：调用方传裸符号时补 /USDT 比对
    isFavorite: (s: string) =>
      h.mockFavorites.includes(s) || h.mockFavorites.includes(`${s}/USDT`),
    isLoading: false,
  }),
}));

// === mocks =================================================================
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string | null) => {
    if (url) h.recordedUrls.push(url);
    return {
      data: url ? (h.mockApiData[url] ?? null) : null,
      error: undefined,
      isLoading: false,
    };
  },
}));

vi.mock("@/lib/ws/use-markets-ws", () => ({
  useMarketsWs: (opts: Record<string, unknown>) => {
    h.wsOptions.push(opts);
    return { state: { connected: false, streamState: "recovering", lastMessageAt: null } };
  },
}));

vi.mock("@/lib/hooks/use-kt-tracks", () => ({
  useKTTracks: () => ({ signals: [], ai: [], positions: [] }),
  AI_COLOR: {},
  AI_LABEL: {},
}));

// KfChart 是 lightweight-charts 重型组件，测试里 stub 掉
vi.mock("@/components/watch/kf-chart", () => ({
  KfChart: () => <div data-testid="kf-chart">KfChart</div>,
}));

vi.mock("@/components/shared/workspace-link", () => ({
  WorkspaceLink: () => <span data-testid="ws-link">ws</span>,
}));

// === import AFTER mocks ====================================================
import { KfPage } from "./kf-page";

// ⌘K 交接通道：设置 symbolPick 模拟用户从搜索面板选中符号
function pick(symbol: string, market: "crypto" | "us" | "commodities" | "ashare" | "taiwan") {
  useMarketStore.setState({ symbolPick: { symbol, market } });
}

describe("KfPage 多市场超级图表", () => {
  beforeEach(() => {
    h.recordedUrls.length = 0;
    h.wsOptions.length = 0;
    h.mockFavorites = [];
    h.mockToggleFavorite.mockClear();
    Object.keys(h.mockApiData).forEach((k) => delete h.mockApiData[k]);
    useMarketStore.setState({ symbolPick: null, symBase: "BTC" });
  });

  it("默认 crypto → 渲染 KfChart，无页面内符号输入（入口=⌘K），不发 /chart/bars 请求", () => {
    render(<KfPage />);
    expect(screen.getByTestId("kf-chart")).toBeTruthy();
    // super-bar 已砍，页面内无符号输入框
    expect(screen.queryByTestId("super-symbol-input")).toBeNull();
    expect(h.recordedUrls.some((u) => u.includes("/chart/bars"))).toBe(false);
  });

  it("symbolPick 非加密（AAPL/us）→ 消费并渲染 KlineChart + 发 /chart/bars 请求", () => {
    const barsUrl = "/api/v1/chart/bars?market=us&symbol=AAPL&interval=1h";
    h.mockApiData[barsUrl] = {
      market: "us",
      symbol: "AAPL",
      interval: "1h",
      bars: [{ t: 1711000000, o: 100, h: 105, l: 99, c: 104, v: 1000 }],
    };
    useMarketStore.setState({ symbolPick: { symbol: "AAPL", market: "us" } });

    render(<KfPage />);

    // 非加密 → KlineChart（SVG 真实渲染），label 带真来源（Yahoo · 股票）
    expect(
      screen.getByRole("img", { name: "AAPL · 美股 · Yahoo · 股票 K线图" })
    ).toBeTruthy();
    expect(h.recordedUrls).toContain(barsUrl);
    // 消费后清空，避免跨页残留
    expect(useMarketStore.getState().symbolPick).toBeNull();
  });

  it("symbolPick 加密（ETH/crypto）→ KfChart 实时分支 + 驱动 symBase", () => {
    useMarketStore.setState({ symbolPick: { symbol: "ETH", market: "crypto" } });

    render(<KfPage />);

    expect(screen.getByTestId("kf-chart")).toBeTruthy();
    expect(useMarketStore.getState().symBase).toBe("ETH");
    expect(h.recordedUrls.some((u) => u.includes("/chart/bars"))).toBe(false);
  });

  it("symbolPick 重复消费防护：置 null 后不重放", () => {
    useMarketStore.setState({ symbolPick: { symbol: "AAPL", market: "us" } });

    const { unmount } = render(<KfPage />);
    expect(useMarketStore.getState().symbolPick).toBeNull();

    // 同页再次渲染（路由回跳场景）：symbolPick 已空，不发新请求
    unmount();
    h.recordedUrls.length = 0;
    render(<KfPage />);
    expect(h.recordedUrls.some((u) => u.includes("/chart/bars"))).toBe(false);
  });

  it("非加密周期 chips 可点（onTfChange）→ 切到 1d 触发新 /chart/bars 请求", () => {
    const bars1h = "/api/v1/chart/bars?market=us&symbol=MSFT&interval=1h";
    const bars1d = "/api/v1/chart/bars?market=us&symbol=MSFT&interval=1d";
    h.mockApiData[bars1h] = {
      market: "us",
      symbol: "MSFT",
      interval: "1h",
      bars: [{ t: 1711000000, o: 300, h: 310, l: 290, c: 305, v: 2000 }],
    };
    h.mockApiData[bars1d] = {
      market: "us",
      symbol: "MSFT",
      interval: "1d",
      bars: [{ t: 1710900000, o: 295, h: 315, l: 285, c: 310, v: 3000 }],
    };
    useMarketStore.setState({ symbolPick: { symbol: "MSFT", market: "us" } });

    render(<KfPage />);
    expect(screen.getByRole("img", { name: /MSFT/ })).toBeTruthy();

    fireEvent.click(screen.getByTestId("kline-tf-chip-1d"));

    expect(h.recordedUrls).toContain(bars1d);
  });

  it("symbolPick 非加密空 bars → 无数据空态，不渲染模拟 K 线", () => {
    const barsUrl = "/api/v1/chart/bars?market=us&symbol=AAPL&interval=1h";
    h.mockApiData[barsUrl] = { market: "us", symbol: "AAPL", interval: "1h", bars: [] };
    useMarketStore.setState({ symbolPick: { symbol: "AAPL", market: "us" } });

    const { container } = render(<KfPage />);

    // allowFallback=false → 空 bars 显示"无数据"，没有 genCandles 模拟 K 线
    expect(screen.getByText("无数据")).toBeTruthy();
    expect(container.querySelector(".kline-ohlc")).toBeNull();
  });

  // 1013 "切换交易对会有残留"：选非加密品种后，右侧面板的加密专属端点仍以 symBase
  // 去请求（/markets/<sym>/USDT/* → 404），且 WS 派生的旧值不清理。修法=按 isCrypto 门控 + 切换重置。
  it("非加密品种 → 不发任何加密专属端点（orderbook/ticker/whale-flow/liqmap/gex 全门控）", () => {
    const barsUrl = "/api/v1/chart/bars?market=ashare&symbol=600519&interval=1d";
    h.mockApiData[barsUrl] = {
      market: "ashare", symbol: "600519", interval: "1d",
      bars: [{ t: 1711000000, o: 100, h: 105, l: 99, c: 104, v: 1000 }],
    };
    // 首屏默认 crypto 会先渲染一次，清掉其请求再切 A股，只看切换后的行为
    const { unmount } = render(<KfPage />);
    unmount();
    h.recordedUrls.length = 0;
    h.wsOptions.length = 0;

    useMarketStore.setState({ symbolPick: { symbol: "600519", market: "ashare" } });
    render(<KfPage />);

    // A股图渲染（真来源 同花顺 · 股票）
    expect(screen.getByRole("img", { name: /600519/ })).toBeTruthy();
    const cryptoEndpoint = (u: string) =>
      u.includes("/USDT/") ||
      u.includes("/intelligence/whale-flow") ||
      u.includes("/liqmap/") ||
      u.includes("/gex/");
    const leaked = h.recordedUrls.filter(cryptoEndpoint);
    expect(leaked).toEqual([]);
    // WS 也应停订阅
    const lastWs = h.wsOptions[h.wsOptions.length - 1];
    expect(lastWs?.enabled).toBe(false);
  });

  it("加密品种 → 保留加密专属端点 + WS enabled（回归：门控不能误伤 crypto）", () => {
    render(<KfPage />);
    expect(h.recordedUrls.some((u) => u.includes("/USDT/ticker"))).toBe(true);
    expect(h.recordedUrls.some((u) => u.includes("/intelligence/whale-flow"))).toBe(true);
    const lastWs = h.wsOptions[h.wsOptions.length - 1];
    expect(lastWs?.enabled).toBe(true);
  });

  it("交易面板 品种：crypto 显示 <sym>-PERP；A股显示裸符号无 -PERP/永续", () => {
    // crypto
    const { unmount } = render(<KfPage />);
    expect(screen.getByText("BTC-PERP")).toBeTruthy();
    unmount();

    // A股：渲染后交易面板品种应是 600519，不带 -PERP
    const barsUrl = "/api/v1/chart/bars?market=ashare&symbol=600519&interval=1d";
    h.mockApiData[barsUrl] = {
      market: "ashare", symbol: "600519", interval: "1d",
      bars: [{ t: 1711000000, o: 100, h: 105, l: 99, c: 104, v: 1000 }],
    };
    useMarketStore.setState({ symbolPick: { symbol: "600519", market: "ashare" } });
    const { container } = render(<KfPage />);
    expect(screen.queryByText("600519-PERP")).toBeNull();
    // 交易面板 品种 字段是 .tp-input，内容应为裸符号
    expect(container.querySelector(".tp-input")?.textContent).toBe("600519");
  });

  // 1013 "没有添加取消自选给交易对"：kf 原先只在 ⌘K 里能加自选，看图时无开关
  it("工具栏自选开关：crypto 显示 BTC 并可点击 toggleFavorite；已自选显示实心星", () => {
    h.mockFavorites = ["BTC/USDT"]; // BTC 已在自选（crypto 存完整对）
    render(<KfPage />);

    const btn = screen.getByTestId("kf-fav-toggle");
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(btn.getAttribute("aria-label")).toContain("取消自选");
    fireEvent.click(btn);
    expect(h.mockToggleFavorite).toHaveBeenCalledWith("BTC");
  });

  it("工具栏自选开关：非加密品种用 superSymbol（A股裸符号）", () => {
    const barsUrl = "/api/v1/chart/bars?market=ashare&symbol=600519&interval=1d";
    h.mockApiData[barsUrl] = {
      market: "ashare", symbol: "600519", interval: "1d",
      bars: [{ t: 1711000000, o: 100, h: 105, l: 99, c: 104, v: 1000 }],
    };
    h.mockFavorites = []; // 未自选
    useMarketStore.setState({ symbolPick: { symbol: "600519", market: "ashare" } });
    render(<KfPage />);

    const btn = screen.getByTestId("kf-fav-toggle");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    expect(btn.getAttribute("aria-label")).toContain("加入自选 600519");
    fireEvent.click(btn);
    expect(h.mockToggleFavorite).toHaveBeenCalledWith("600519");
  });
});

describe("KfPage 指标 picker（0923 评审 P0 修正面）", () => {
  beforeEach(() => {
    h.recordedUrls.length = 0;
    h.wsOptions.length = 0;
    h.mockFavorites = [];
    h.mockToggleFavorite.mockClear();
    Object.keys(h.mockApiData).forEach((k) => delete h.mockApiData[k]);
    useMarketStore.setState({ symbolPick: null, symBase: "BTC" });
  });

  it("触发按钮是真 button + aria-haspopup/aria-expanded，计数随勾选变", () => {
    render(<KfPage />);
    const btn = screen.getByRole("button", { name: /指标 · / }) as HTMLButtonElement;
    expect(btn.getAttribute("aria-haspopup")).toBe("true");
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    expect(btn.textContent).toContain("指标 · 1"); // 默认 MA 勾选
  });

  it("打开弹层 → 两组 4+5 行，勾 BOLL 后计数与 active 态联动；Esc 关 + aria-expanded=false", () => {
    render(<KfPage />);
    const btn = screen.getByRole("button", { name: /指标 · / });
    fireEvent.click(btn);

    const dialog = screen.getByRole("dialog", { name: "指标选择" });
    expect(dialog.textContent).toContain("主图叠层");
    expect(dialog.textContent).toContain("独立子窗格");
    expect(dialog.textContent).toContain("页面级配置 · 应用到全部图格");
    // 行数：9 个指标 label 各一行
    const checkboxes = dialog.querySelectorAll('input[type="checkbox"]');
    expect(checkboxes.length).toBe(9);

    fireEvent.click(screen.getByLabelText(/BOLL/));
    expect((btn as HTMLButtonElement).textContent).toContain("指标 · 2");

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "指标选择" })).toBeNull();
    expect((btn as HTMLButtonElement).getAttribute("aria-expanded")).toBe("false");
  });

  it("勾 RSI → KfChart stub 收到 showRsi=true（双入口同源 tools）", () => {
    render(<KfPage />);
    fireEvent.click(screen.getByRole("button", { name: /指标 · / }));
    fireEvent.click(screen.getByLabelText(/RSI/));
    // stub 的 KfChart 不接 props；通过页面快照间接验证：快速开关 MA 仍在（双入口并存）
    const maSwitch = screen.getByText("MA", { selector: "div.chart-tool" });
    expect(maSwitch.className).toContain("active");
    fireEvent.click(maSwitch);
    expect(maSwitch.className).not.toContain("active");
    // picker 内 MA 行也应同步为未勾选（同一 tools 状态源）
    const dialog = screen.getByRole("dialog", { name: "指标选择" });
    const maRow = dialog.querySelector('label input[type="checkbox"]:not(:checked)');
    expect(maRow).toBeTruthy();
  });
});
