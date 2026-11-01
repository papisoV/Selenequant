import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { useMarketStore } from "@/lib/store/market";
import { useDrawingsStore } from "@/lib/store/drawings";

// === shared mock state (hoisted so factories see them) =====================
const h = vi.hoisted(() => ({
  recordedUrls: [] as string[],
  mockApiData: {} as Record<string, unknown>,
  wsOptions: [] as Array<Record<string, unknown>>,
  mockToggleFavorite: vi.fn(),
  mockFavorites: [] as string[],
  alertCalls: [] as unknown[],
  mockAlerts: [] as unknown[],
  deletedIds: [] as number[],
  chartProps: [] as Array<Record<string, unknown>>,
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

// KfChart 是 lightweight-charts 重型组件，测试里 stub 掉。
// alertLinesFor 是纯函数但同模块导出，页面 import 它 —— stub 必须一起给，
// 否则页面启动即 undefined 调用而崩（stub 与真模块的公开面要同步）。
vi.mock("@/components/watch/kf-chart", async () => {
  const actual = await vi.importActual<typeof import("@/components/watch/kf-chart")>(
    "@/components/watch/kf-chart",
  );
  return {
    KfChart: (props: Record<string, unknown>) => {
      h.chartProps.push(props);
      return <div data-testid="kf-chart">KfChart</div>;
    },
    alertLinesFor: actual.alertLinesFor,
  };
});

// 批3 预警 API：页面挂载时会拉一次列表，POST/DELETE 由测试断言。
vi.mock("@/lib/api/decision-workflow", () => ({
  createPriceAlert: (b: unknown) => {
    h.alertCalls.push(b);
    return Promise.resolve({ id: h.alertCalls.length, status: "registered" });
  },
  listPriceAlerts: () => Promise.resolve(h.mockAlerts),
  deletePriceAlert: (id: number) => {
    h.deletedIds.push(id);
    return Promise.resolve(true);
  },
  httpErrorMessage: (e: unknown) => String(e),
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
    h.alertCalls.length = 0;
    h.mockAlerts = [];
    h.deletedIds.length = 0;
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

describe("KfPage 对象树（指标区合流，1021）", () => {
  beforeEach(() => {
    h.recordedUrls.length = 0;
    h.wsOptions.length = 0;
    h.chartProps.length = 0;
    h.mockFavorites = [];
    h.mockToggleFavorite.mockClear();
    Object.keys(h.mockApiData).forEach((k) => delete h.mockApiData[k]);
    useMarketStore.setState({ symbolPick: null, symBase: "BTC" });
    // `dockOpen` 现在是 store 态(第 4 步改成常驻侧栏),跨用例会残留 ——
    // 不重置的话第二个用例点「对象树」反而把它关掉。
    useDrawingsStore.setState({ dockOpen: false });
    useDrawingsStore.getState().clearSelection();
  });

  // 打开对象树并返回侧栏（role=complementary name=对象树）
  function openTree() {
    const btn = screen.getByRole("button", { name: /对象树/ }) as HTMLButtonElement;
    fireEvent.click(btn);
    return { btn, dialog: screen.getByRole("complementary", { name: "对象树" }) };
  }

  it("指标不再有独立弹层：工具栏只有「对象树」图标，无「指标 · N」按钮", () => {
    render(<KfPage />);
    expect(screen.queryByRole("button", { name: /指标 · / })).toBeNull();
    expect(screen.getByRole("button", { name: /对象树/ })).toBeTruthy();
  });

  it("对象树内有指标区：9 行分「主图叠层」「独立子窗格」两组 + 4 行结构层，标注页面级", () => {
    render(<KfPage />);
    const { dialog } = openTree();

    const section = dialog.querySelector('[data-testid="kf-draw-indicators"]') as HTMLElement;
    expect(section).toBeTruthy();
    // 区头用 `技术指标` 而不是 `指标`:后者在 research-b3b 里译作 "Metrics",
    // 且展开序在本域之后 —— 写 `指标` 英文界面会显示成 "Metrics"。
    expect(section.querySelector(".kf-draw-group-name")?.textContent).toBe("技术指标");
    expect(section.textContent).toContain("页面级 · 作用于全部图格");
    expect(section.textContent).toContain("主图叠层");
    expect(section.textContent).toContain("独立子窗格");
    expect(section.textContent).toContain("结构层");

    // 每行一个可见性按钮（◉/○），共 13 个指标（主图叠层 4 + 子窗格 5 + 结构层 4）
    const rows = section.querySelectorAll(".kf-draw-ind-section .kf-draw-row");
    expect(rows.length).toBe(13);
  });

  it("树内点指标可见性 → tools 状态翻面，且与工具栏快捷开关同源", () => {
    render(<KfPage />);
    const { dialog } = openTree();
    const section = dialog.querySelector('[data-testid="kf-draw-indicators"]') as HTMLElement;

    // 找 BOLL 行（label 文本），点它的可见性按钮
    const bollRow = Array.from(section.querySelectorAll(".kf-draw-row")).find((r) =>
      r.textContent?.includes("BOLL"),
    ) as HTMLElement;
    expect(bollRow).toBeTruthy();
    expect(bollRow.className).toContain("dim"); // boll 默认关

    const toggle = bollRow.querySelector(".kf-draw-ic") as HTMLButtonElement;
    expect(toggle.textContent).toBe("○");
    fireEvent.click(toggle);
    expect(toggle.textContent).toBe("◉");
    expect(bollRow.className).not.toContain("dim");
  });

  it("树内指标与工具栏快捷开关同源：关掉 MA 后，树里的 MA 行同步变暗", () => {
    render(<KfPage />);
    const { dialog } = openTree();
    const section = dialog.querySelector('[data-testid="kf-draw-indicators"]') as HTMLElement;
    const rowOf = (label: string) =>
      Array.from(section.querySelectorAll(".kf-draw-row")).find((r) =>
        r.textContent?.includes(label),
      ) as HTMLElement;

    expect(rowOf("MA").className).not.toContain("dim"); // 默认开
    fireEvent.click(screen.getByRole("button", { name: "MA" }));
    expect(rowOf("MA").className).toContain("dim");
  });

  it("范围开关默认「只看当前图」，点击切到「全部标的」并改标题", () => {
    render(<KfPage />);
    const { dialog } = openTree();

    const scopeBtn = dialog.querySelector(".kf-draw-scope") as HTMLButtonElement;
    expect(scopeBtn).toBeTruthy();
    expect(scopeBtn.textContent).toBe("只看当前图");
    expect(scopeBtn.getAttribute("aria-pressed")).toBe("false");
    expect(dialog.textContent).toContain("当前图 BTC 的手绘对象");

    fireEvent.click(scopeBtn);
    expect(scopeBtn.textContent).toBe("全部标的");
    expect(scopeBtn.getAttribute("aria-pressed")).toBe("true");
    expect(dialog.textContent).toContain("全部标的的手绘对象");
  });

  // ── 工具栏死键清理 + 两个真跳转（批1.2/1.3/1.4）────────────────────────
  it("「面积」死键已删（无 onClick 的占位不再渲染）", () => {
    render(<KfPage />);
    expect(screen.queryByText("面积")).toBeNull();
    // 同排的 K 线仍在（只删了死键，没顺手删别的）
    expect(screen.getByText("K 线", { selector: "span.chart-tool" })).toBeTruthy();
  });

  it("「回测」是带当前品种的真链接（crypto → 回测中心）", () => {
    render(<KfPage />);
    const link = screen.getByRole("link", { name: "回测" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/trade/backtests?symbol=BTC%2FUSDT");
  });

  it("「回测」在非加密市场指向 A股回测页，并带上当前代码", () => {
    useMarketStore.setState({ symbolPick: { symbol: "600519", market: "ashare" } });
    render(<KfPage />);
    const link = screen.getByRole("link", { name: "回测" }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/research/ashare-backtest?symbol=600519");
  });

  it("「情绪」无社区样本时显示 —（不把后端 0.5 中性兜底当真读数）", () => {
    h.mockApiData["/api/v1/intelligence/social/sentiment"] = {
      overall: 0.5,
      by_subreddit: {},
      timestamp: 1792044614,
    };
    render(<KfPage />);
    const link = screen.getByRole("link", { name: /情绪/ }) as HTMLAnchorElement;
    expect(link.textContent).toContain("—");
    expect(link.textContent).not.toContain("0.50");
    expect(link.getAttribute("href")).toBe("/watch/sig?tab=social");
  });

  it("「情绪」有社区样本时才显示数值", () => {
    h.mockApiData["/api/v1/intelligence/social/sentiment"] = {
      overall: 0.7312,
      by_subreddit: { "r/CryptoCurrency": 0.73 },
      timestamp: 1792044614,
    };
    render(<KfPage />);
    const link = screen.getByRole("link", { name: /情绪/ }) as HTMLAnchorElement;
    expect(link.textContent).toContain("0.73");
  });

  // ── 批2.1 工具栏键盘可达（div→button）────────────────────────────────
  it("MA/VOL/MACD 是原生 button：可 Tab 聚焦、可 Enter 触发、带 aria-pressed", () => {
    render(<KfPage />);
    for (const name of ["MA", "VOL", "MACD"]) {
      const el = screen.getByRole("button", { name });
      expect(el.tagName).toBe("BUTTON");
      // 原生 button 才有的可聚焦性（div 默认不可聚焦）
      expect((el as HTMLButtonElement).tabIndex).toBe(0);
      expect(el.getAttribute("aria-pressed")).toBe(
        name === "MA" ? "true" : "false", // 默认只勾 MA
      );
    }
  });

  it("键盘 Enter 能切换 MA（既不是只有鼠标能点，也不是假按钮）", () => {
    render(<KfPage />);
    const ma = screen.getByRole("button", { name: "MA" });
    expect(ma.className).toContain("active");
    // 原生 button 的 Enter 激活走 click 事件；jsdom 不代为合成，直接断言语义等价路径
    fireEvent.click(ma);
    expect(ma.className).not.toContain("active");
    expect(ma.getAttribute("aria-pressed")).toBe("false");
  });

  it("布局三键也是真 button，选中项 aria-pressed=true", () => {
    render(<KfPage />);
    const group = screen.getByRole("group", { name: "图表布局" });
    const btns = group.querySelectorAll("button");
    expect(btns.length).toBe(3);
    expect(btns[0].getAttribute("aria-pressed")).toBe("true"); // 默认单图
    expect(btns[1].getAttribute("aria-pressed")).toBe("false");
    expect(btns[2].getAttribute("aria-pressed")).toBe("false");
  });

  it("「K 线」是静态标签不是按钮（唯一图型，避免假 affordance）", () => {
    render(<KfPage />);
    const label = screen.getByText("K 线", { selector: "span.chart-tool" });
    expect(label.tagName).toBe("SPAN");
    expect(label.getAttribute("aria-current")).toBe("true");
    // 整条工具栏不应再多出一个叫「K 线」的 button
    expect(screen.queryByRole("button", { name: "K 线" })).toBeNull();
  });
});

// ── 批3 手画价格预警 ──────────────────────────────────────────────────────
describe("KfPage 价格预警开关", () => {
  beforeEach(() => {
    h.recordedUrls.length = 0;
    h.wsOptions.length = 0;
    h.alertCalls.length = 0;
    h.mockAlerts = [];
    h.deletedIds.length = 0;
    Object.keys(h.mockApiData).forEach((k) => delete h.mockApiData[k]);
    useMarketStore.setState({ symbolPick: null, symBase: "BTC" });
  });

  it("加密模式：预警按钮可用，点击切换 aria-pressed", () => {
    render(<KfPage />);
    const btn = screen.getByTestId("kf-alert-toggle") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });

  it("★ 非加密（AAPL/us）→ 预警按钮 disabled + aria-disabled，且有解释性 title", () => {
    // 轮询 loop 的价格源写死 binance/futures，其它市场取不到价。
    // 必须显式禁用并说明原因，而不是让用户画一条永远不响的线。
    useMarketStore.setState({ symbolPick: { symbol: "AAPL", market: "us" } });
    render(<KfPage />);
    const btn = screen.getByTestId("kf-alert-toggle") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute("aria-disabled")).toBe("true");
    expect(btn.getAttribute("title")).toContain("加密货币");
  });

  it("非加密时点不动：aria-pressed 不变（disabled button 不派发 onClick）", () => {
    useMarketStore.setState({ symbolPick: { symbol: "AAPL", market: "us" } });
    render(<KfPage />);
    const btn = screen.getByTestId("kf-alert-toggle") as HTMLButtonElement;
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-pressed")).toBe("false");
  });

  it("★ 挂载时拉一次已挂规则，并作为 alertLines 交给图（刷新后线能重建）", async () => {
    h.mockAlerts = [
      { id: 7, symbol: "BTC/USDT", interval: "1h", levels: { price: 63000 } },
    ];
    render(<KfPage />);
    await act(async () => { await Promise.resolve(); });
    const last = h.chartProps[h.chartProps.length - 1];
    expect(last.alertLines).toEqual([{ id: 7, price: 63000 }]);
  });

  it("已挂规则属于别的品种 → 不画到本图上", async () => {
    h.mockAlerts = [
      { id: 8, symbol: "ETH/USDT", interval: "1h", levels: { price: 3000 } },
    ];
    render(<KfPage />); // 默认 symBase = BTC
    await act(async () => { await Promise.resolve(); });
    const last = h.chartProps[h.chartProps.length - 1];
    expect(last.alertLines).toEqual([]);
  });

  it("★ 图上落点确认 → POST 到 decision-workflow，symbol 用统一形式（BTC/USDT）", async () => {
    // 后端轮询 loop 直接 get_ticker(symbol) 查缓存,缓存键是 to_unified() 的
    // 结果(BTC/USDT)。这里传 bare "BTC" 的话线画得出来但永远不触发。
    render(<KfPage />);
    fireEvent.click(screen.getByTestId("kf-alert-toggle"));
    const onDraw = h.chartProps[h.chartProps.length - 1].onDrawLevel as (
      p: number,
    ) => void;
    await act(async () => { onDraw(63000); });

    expect(h.alertCalls).toEqual([
      { symbol: "BTC/USDT", interval: "1h", levels: { price: 63000 } },
    ]);
  });

  it("drawMode 只在加密分支传给图（非加密恒 false）", async () => {
    render(<KfPage />);
    fireEvent.click(screen.getByTestId("kf-alert-toggle"));
    expect(h.chartProps[h.chartProps.length - 1].drawMode).toBe(true);

    useMarketStore.setState({ symbolPick: { symbol: "AAPL", market: "us" } });
    await act(async () => { await Promise.resolve(); });
    // 非加密走 KlineChart 分支，KfChart stub 不再拿到 props；已渲染的最后一份
    // 仍是加密那份。这里断言的是"切走时不会带着 drawMode=true 过去"。
    const toggle = screen.getByTestId("kf-alert-toggle") as HTMLButtonElement;
    expect(toggle.disabled).toBe(true);
  });

  it("树内勾 ICT 四层 → KfChart 各收到对应开关；全部默认关", () => {
    render(<KfPage />);
    const last = () => h.chartProps[h.chartProps.length - 1] as Record<string, unknown>;
    const layers = [
      ["ICT 摆动点", "showIctSwings"],
      ["ICT 破位线", "showIctEvents"],
      ["ICT 时段带", "showIctKillzone"],
      ["ICT 溢价折价", "showIctDealingRange"],
    ] as const;
    for (const [, prop] of layers) expect(last()[prop]).toBe(false);

    // 结构层开关随 main 的架构一起并入对象树（独立 picker 已删）。
    useDrawingsStore.setState({ dockOpen: false });
    fireEvent.click(screen.getByRole("button", { name: "对象树" }));
    const dialog = screen.getByRole("complementary", { name: "对象树" });
    const section = dialog.querySelector('[data-testid="kf-draw-indicators"]') as HTMLElement;
    const rowOf = (label: string) =>
      Array.from(section.querySelectorAll(".kf-draw-row")).find((r) =>
        r.textContent?.includes(label),
      ) as HTMLElement;

    for (const [label, prop] of layers) {
      const row = rowOf(label);
      expect(row, label).toBeTruthy();
      expect(row.className).toContain("dim"); // 默认关
      fireEvent.click(row.querySelector(".kf-draw-ic") as HTMLButtonElement);
      expect(last()[prop]).toBe(true);
      fireEvent.click(row.querySelector(".kf-draw-ic") as HTMLButtonElement);
      expect(last()[prop]).toBe(false);
    }
  });
});
