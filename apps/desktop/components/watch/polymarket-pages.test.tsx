import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// === shared mock state (hoisted) =====================
const {
  mockApiData,
  recordedUrls,
  mockFetch,
} = vi.hoisted(() => ({
  mockApiData: {} as Record<string, unknown>,
  recordedUrls: [] as string[],
  mockFetch: vi.fn(),
}));

// === mock useApi: 按 path 返回数据,并记录请求 path ===
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string | null) => {
    if (url) recordedUrls.push(url);
    return {
      data: url ? (mockApiData[url] ?? null) : null,
      error: undefined,
      isLoading: false,
      mutate: mockFetch,
    };
  },
}));

// === mock card primitives (avoid heavy deps) =========
vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>
      {right}
      {children}
    </div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

// === global.fetch mock (alert delete / watchlist add-remove) ===
vi.stubGlobal("fetch", mockFetch);

// === import AFTER mocks ==============================
import {
  PolymarketEventsPage,
  PolymarketMarketsPage,
  PolymarketCryptoPredictionsPage,
  PolymarketOrderbookPage,
  PolymarketPricePage,
  PolymarketAlertsPage,
  PolymarketWatchlistPage,
  PolymarketScreenPage,
  PolymarketEventDetailPage,
  PolymarketMarketDetailPage,
  PolymarketAddressDetailsPage,
} from "./polymarket-pages";

const EVENTS_URL = "/api/v1/polymarket/events?limit=20";
const MARKETS_URL = "/api/v1/polymarket/markets?limit=20";
const PRED_URL = "/api/v1/polymarket/crypto-predictions?limit=30";
const BOOK_URL = "/api/v1/polymarket/orderbook/tok123";
const PRICE_URL = "/api/v1/polymarket/price/tok123?side=BUY";
const ALERTS_URL = "/api/v1/polymarket/addresses/alerts";
const WATCHLIST_URL = "/api/v1/polymarket/addresses/watchlist";
const SCREEN_URL = "/api/v1/polymarket/addresses/screen?limit=30";
const EVENT_DETAIL_URL = "/api/v1/polymarket/events/btc-updown-5m";
const ADDR_DETAIL_URL = "/api/v1/polymarket/addresses/0x4b6e5d2c0a1f3e9d8c7b6a5f4e3d2c1b0a9f8e7d/details";

const TOKEN_ID = "tok123";

function populatedEvents() {
  return {
    events: [
      {
        id: "ev1",
        title: "Bitcoin up or down?",
        title_zh: "比特币涨或跌?",
        category: "crypto",
        category_zh: "加密货币",
        volume_24h: 2500000,
        market_count: 2,
      },
    ],
    count: 1,
    categories: ["crypto"],
    categories_zh: { crypto: "加密货币" },
  };
}

function populatedMarkets() {
  return {
    markets: [
      {
        condition_id: "cond1",
        question: "Will BTC be above 100k?",
        question_zh: "BTC 会突破 10 万吗?",
        volume_24h: 1234567,
        tokens: [{ outcome: "YES", price: 0.63 }, { outcome: "NO", price: 0.37 }],
      },
    ],
    count: 1,
  };
}

function populatedPredictions() {
  return {
    predictions: [
      {
        id: "p1",
        title: "BTC Up or Down 5m",
        title_zh: "比特币 5分钟涨跌",
        asset: "BTC",
        timeframe: "5m",
        timeframe_label: { en: "5 Minutes", zh: "5分钟" },
        outcomes: [{ name: "Yes", price: 0.55 }],
        polymarket_url: "https://polymarket.com/event/btc-updown-5m-123",
      },
    ],
    count: 1,
    available_assets: ["BTC"],
  };
}

describe("Polymarket 专属组件", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
    recordedUrls.length = 0;
    mockFetch.mockReset();
    mockFetch.mockResolvedValue({ ok: true, json: async () => ({}) });
  });

  describe("PolymarketEventsPage", () => {
    it("渲染事件列表 + 类别按钮,类别切换触发真实 category 参数", () => {
      mockApiData[EVENTS_URL] = populatedEvents();
      render(<PolymarketEventsPage />);

      expect(screen.getByText("比特币涨或跌?")).toBeTruthy();
      expect(screen.getByText("加密货币")).toBeTruthy();
      // 类别按钮真接线:点击"加密"→ URL 带 category=crypto
      fireEvent.click(screen.getByText("加密"));
      expect(recordedUrls.some((u) => u.includes("category=crypto"))).toBe(true);
    });
  });

  describe("PolymarketMarketsPage", () => {
    it("渲染市场列表 + limit 切换真实参数", () => {
      mockApiData[MARKETS_URL] = populatedMarkets();
      render(<PolymarketMarketsPage />);

      expect(screen.getByText("BTC 会突破 10 万吗?")).toBeTruthy();
      expect(screen.getByText("63¢")).toBeTruthy();
      // limit 按钮
      fireEvent.click(screen.getByText("50"));
      expect(recordedUrls.some((u) => u.includes("limit=50"))).toBe(true);
    });
  });

  describe("PolymarketCryptoPredictionsPage", () => {
    it("渲染预测列表 + 资产/时间筛选", () => {
      mockApiData[PRED_URL] = populatedPredictions();
      render(<PolymarketCryptoPredictionsPage />);

      expect(screen.getByText("比特币 5分钟涨跌")).toBeTruthy();
      const btcEls = screen.getAllByText("BTC");
      expect(btcEls.length).toBeGreaterThan(0);
      // asset 筛选
      fireEvent.click(btcEls[0]);
      expect(recordedUrls.some((u) => u.includes("asset=BTC"))).toBe(true);
    });
  });

  describe("PolymarketOrderbookPage", () => {
    it("输入 token_id 后请求真实 orderbook path", () => {
      mockApiData[BOOK_URL] = {
        asset_id: "tok123",
        bids: [{ price: "0.55", size: "120" }],
        asks: [{ price: "0.57", size: "80" }],
      };
      render(<PolymarketOrderbookPage />);

      // 默认预填 token → 发请求
      fireEvent.change(screen.getByPlaceholderText("clob token id"), { target: { value: TOKEN_ID } });
      expect(recordedUrls.some((u) => u.includes(`/orderbook/${TOKEN_ID}`))).toBe(true);
      expect(screen.getByText("0.55")).toBeTruthy();
      expect(screen.getByText("0.57")).toBeTruthy();
    });
  });

  describe("PolymarketPricePage", () => {
    it("渲染价格 + BUY/SELL 切换", () => {
      mockApiData[PRICE_URL] = { token_id: TOKEN_ID, price: 0.56, midpoint: 0.55, side: "BUY" };
      render(<PolymarketPricePage />);

      fireEvent.change(screen.getByPlaceholderText("clob token id"), { target: { value: TOKEN_ID } });
      expect(screen.getByText("0.560")).toBeTruthy();
      // SELL 切换 → side=SELL 请求
      fireEvent.click(screen.getByText("SELL"));
      expect(recordedUrls.some((u) => u.includes("side=SELL"))).toBe(true);
    });
  });

  describe("PolymarketAlertsPage", () => {
    it("渲染规则 + 删除按钮真调用 DELETE fetch", async () => {
      mockApiData[ALERTS_URL] = {
        rules: [{ rule_id: "alert-1", kind: "price", market_keyword: "BTC", enabled: true }],
        total: 1,
      };
      render(<PolymarketAlertsPage />);

      expect(screen.getByText("价格")).toBeTruthy();
      expect(screen.getByText("开")).toBeTruthy();

      fireEvent.click(screen.getByTitle("删除规则"));
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/polymarket/addresses/alerts/alert-1",
        expect.objectContaining({ method: "DELETE" })
      );
    });
  });

  describe("PolymarketWatchlistPage", () => {
    it("添加地址真调用 POST,移除真调用 DELETE", async () => {
      mockApiData[WATCHLIST_URL] = { entries: [], total: 0 };
      render(<PolymarketWatchlistPage />);

      // 添加
      fireEvent.change(screen.getByPlaceholderText("0x… 添加到关注"), {
        target: { value: "0xabc123" },
      });
      fireEvent.click(screen.getByText("添加"));
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/polymarket/addresses/watchlist",
        expect.objectContaining({
          method: "POST",
          body: expect.stringContaining("0xabc123"),
        })
      );

      // 列表有数据后移除
      mockApiData[WATCHLIST_URL] = {
        entries: [{ address: "0xabc123", label: "test", source: "manual" }],
        total: 1,
      };
      render(<PolymarketWatchlistPage />);
      expect(screen.getByText("test")).toBeTruthy();
      fireEvent.click(screen.getByTitle("移除"));
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/v1/polymarket/addresses/watchlist/0xabc123",
        expect.objectContaining({ method: "DELETE" })
      );
    });
  });

  describe("PolymarketScreenPage", () => {
    it("渲染筛选地址 + 分类切换", () => {
      mockApiData[SCREEN_URL] = {
        addresses: [{ address: "0xabc", classification: "elite", win_rate: 0.72, roi: 0.3, trade_count: 42 }],
        total: 1,
      };
      render(<PolymarketScreenPage />);

      const eliteEls = screen.getAllByText("精英");
      expect(eliteEls.length).toBeGreaterThan(0);
      expect(screen.getByText("72%")).toBeTruthy();
      // 分类切换
      fireEvent.click(screen.getByText("高手"));
      expect(recordedUrls.some((u) => u.includes("classification=proficient"))).toBe(true);
    });
  });

  describe("PolymarketEventDetailPage", () => {
    it("输入 event_id 后请求真实 path", () => {
      mockApiData[EVENT_DETAIL_URL] = {
        id: "ev1",
        title: "Bitcoin up or down?",
        title_zh: "比特币涨或跌?",
        category_zh: "加密货币",
        volume_24h: 2500000,
        markets: [{ question: "Will BTC go up?", outcomes: [{ name: "Yes", price: 0.6 }] }],
      };
      render(<PolymarketEventDetailPage />);

      expect(screen.getByText("比特币涨或跌?")).toBeTruthy();
      expect(screen.getByText(/60¢/)).toBeTruthy();
    });
  });

  describe("PolymarketMarketDetailPage", () => {
    it("输入 condition_id 后请求真实 path", () => {
      const cond = "0xabc123456789";
      const url = `/api/v1/polymarket/markets/${cond}`;
      mockApiData[url] = {
        question: "Will BTC be above 100k?",
        question_zh: "BTC 会突破 10 万吗?",
        tokens: [{ outcome: "YES", price: 0.63 }],
      };
      render(<PolymarketMarketDetailPage />);

      fireEvent.change(screen.getByPlaceholderText("输入 condition id"), { target: { value: cond } });
      expect(recordedUrls.some((u) => u.includes(`/markets/${cond}`))).toBe(true);
      expect(screen.getByText("BTC 会突破 10 万吗?")).toBeTruthy();
    });
  });

  describe("PolymarketAddressDetailsPage", () => {
    it("渲染地址分析 + 分类", () => {
      mockApiData[ADDR_DETAIL_URL] = {
        address: "0x4b6e5d2c0a1f3e9d8c7b6a5f4e3d2c1b0a9f8e7d",
        classification: "elite",
        win_rate: 0.72,
        roi: 0.3,
        trade_count: 42,
        total_volume: 120000,
        trading_style: "激进型",
        llm_enhanced: true,
      };
      render(<PolymarketAddressDetailsPage />);

      expect(screen.getByText("精英")).toBeTruthy();
      expect(screen.getByText("72%")).toBeTruthy();
      expect(screen.getByText("激进型")).toBeTruthy();
    });
  });
});
