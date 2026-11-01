import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// === mock useApiPage =================================
const mockApiData: Record<string, unknown> = {};
const mockMutate = vi.fn();
vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: (url: string) => ({
      data: mockApiData[url] ?? null,
      error: null,
      isLoading: false,
      mutate: mockMutate,
    }),
  }),
}));

// === mock card primitives (avoid heavy deps) =====
vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>
      <span>{title}</span>
      {right}
      {children}
    </div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
  V17Head: ({ t }: { t: string }) => <div>{t}</div>,
  V17Grid: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

// lucide icons are lightweight, keep real

// === import AFTER mocks =================================
import { UsRadarPage } from "./us-radar-page";
import { EventSignalsPage } from "./us-radar-event-signals-page";
import { MarketOverviewPage } from "./us-radar-market-overview-page";
import { ProvidersReadinessPage } from "./us-radar-providers-readiness-page";
import { ProvidersCapabilitiesPage } from "./us-radar-providers-capabilities-page";
import { ResearchAgentsReadinessPage } from "./us-radar-research-agents-readiness-page";
import { ScoresSymbolBreakdownPage } from "./us-radar-scores-symbol-breakdown-page";
import { SnapshotPage } from "./us-radar-snapshot-page";
import { SymbolsSymbolPage } from "./us-radar-symbols-symbol-page";
import { SymbolsSymbolAiConsolePage } from "./us-radar-symbols-symbol-ai-console-page";
import { UniversePage } from "./us-radar-universe-page";
import { useMarketStore } from "@/lib/store/market";

const SNAPSHOT_URL = "/api/v1/us-radar/snapshot";

function stubMarketSymbol(sym: string) {
  useMarketStore.setState({ symBase: sym, pageFilter: { tf: "1h", symbol: sym, exchange: "all" } });
}

beforeEach(() => {
  Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  mockMutate.mockReset();
  stubMarketSymbol("AAPL");
});

describe("UsRadarPage 入口页", () => {
  it("渲染真实 snapshot shape: 指数/事件信号/评分标的", () => {
    mockApiData[SNAPSHOT_URL] = {
      indices: [
        { symbol: "^GSPC", shortName: "S&P 500", regularMarketPrice: 5400, regularMarketChangePercent: 0.5 },
      ],
      event_signals: [{ symbol: "NVDA", signal: "bullish", score: 82 }],
      radar_scores: { AAPL: { total_score: 71, signal: "bullish" } },
      timestamp: 1700000000000,
    };
    render(<UsRadarPage />);
    expect(screen.getByTestId("stat-指数")).toBeTruthy();
    expect(screen.getByText("^GSPC")).toBeTruthy();
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("71")).toBeTruthy();
  });

  it("空态显示暂无数据", () => {
    mockApiData[SNAPSHOT_URL] = null;
    render(<UsRadarPage />);
    expect(screen.getAllByText("暂无数据").length).toBeGreaterThan(0);
  });

  it("点击刷新按钮调用 mutate", () => {
    mockApiData[SNAPSHOT_URL] = { indices: [], event_signals: [], radar_scores: {} };
    render(<UsRadarPage />);
    const refreshBtn = screen.getByLabelText("刷新");
    fireEvent.click(refreshBtn);
    expect(mockMutate).toHaveBeenCalled();
  });
});

describe("EventSignalsPage 事件信号", () => {
  it("渲染信号 headline + 事件类型 + 置信度", () => {
    mockApiData["/api/v1/us-radar/event-signals"] = {
      signals: [
        {
          event_id: "us-evt-abc",
          symbol: "AAPL",
          event_type: "earnings",
          headline: "Apple beats earnings expectations",
          source: "Yahoo",
          published_at: 1700000000,
          impact_score: 0.7,
          confidence: 0.8,
          signal_lane: "high_priority",
          suggested_actions: ["watch", "run_replay"],
          risk_flags: ["high_impact_monitor"],
        },
      ],
      total: 1,
      data_mode: "delayed",
    };
    render(<EventSignalsPage />);
    expect(screen.getByText("Apple beats earnings expectations")).toBeTruthy();
    expect(screen.getByText("财报")).toBeTruthy();
    expect(screen.getByText("high_priority")).toBeTruthy();
    expect(screen.getByText(/80%/)).toBeTruthy();
  });

  it("空态显示暂无数据", () => {
    mockApiData["/api/v1/us-radar/event-signals"] = { signals: [], total: 0 };
    render(<EventSignalsPage />);
    expect(screen.getByText("暂无数据")).toBeTruthy();
  });
});

describe("MarketOverviewPage 市场概览", () => {
  it("渲染指数 + 板块 + 领涨领跌", () => {
    mockApiData["/api/v1/us-radar/market/overview"] = {
      indices: [{ symbol: "^GSPC", shortName: "S&P 500", regularMarketPrice: 5400, regularMarketChangePercent: 0.5 }],
      sector_scores: { "^GSPC": { price: 5400, change_pct: 0.5, signal: "bullish" } },
      gainers: [{ symbol: "NVDA", name: "NVIDIA", change_pct: 2.5 }],
      losers: [{ symbol: "TSLA", name: "Tesla", change_pct: -1.5 }],
    };
    render(<MarketOverviewPage />);
    expect(screen.getAllByText("^GSPC").length).toBeGreaterThan(0);
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("TSLA")).toBeTruthy();
    expect(screen.getByText("领涨")).toBeTruthy();
    expect(screen.getByText("领跌")).toBeTruthy();
  });
});

describe("ProvidersReadinessPage 供应商就绪", () => {
  it("渲染供应商状态矩阵", () => {
    mockApiData["/api/v1/us-radar/providers/readiness"] = {
      providers: {
        public: { mode: "live", configured: true, available: true, reason: null, capabilities: ["quotes", "news"] },
        futu: { mode: "offline", configured: false, available: false, reason: "SELENE_FUTU_API_KEY not set", capabilities: [] },
      },
      configured_count: 1,
      available_count: 1,
    };
    render(<ProvidersReadinessPage />);
    expect(screen.getByText("public")).toBeTruthy();
    expect(screen.getByText("futu")).toBeTruthy();
    expect(screen.getAllByText("可用").length).toBeGreaterThan(0);
    expect(screen.getByText("SELENE_FUTU_API_KEY not set")).toBeTruthy();
  });
});

describe("ProvidersCapabilitiesPage 供应商能力", () => {
  it("渲染能力矩阵 + 维度覆盖", () => {
    mockApiData["/api/v1/us-radar/providers/capabilities"] = {
      providers: {
        public: {
          status: "primary",
          mode: "live",
          capabilities: { quotes: true, news: true },
          entitlement_status_by_capability: { quotes: "enabled", news: "enabled" },
          missing_credentials: [],
          limitations: ["No L2 orderbook data"],
          auth: "crumb_token",
          cost: "free",
        },
      },
      dimensions_coverage: { news: { public: "partial" } },
    };
    render(<ProvidersCapabilitiesPage />);
    expect(screen.getByText("报价")).toBeTruthy();
    expect(screen.getByText("新闻")).toBeTruthy();
    expect(screen.getByText(/No L2 orderbook data/)).toBeTruthy();
    expect(screen.getByText("news")).toBeTruthy();
  });
});

describe("ResearchAgentsReadinessPage 研究代理就绪", () => {
  it("渲染代理状态 + 缺失数据源", () => {
    mockApiData["/api/v1/us-radar/research-agents/readiness"] = {
      agents: [
        { agent_id: "sec_filing_agent", display_name: "SEC Filing Agent", agent_type: "sec_filing", status: "available", enabled_capabilities: ["filing_evidence"], missing_requirements: [] },
        { agent_id: "macro_calendar_agent", display_name: "Macro Calendar Agent", agent_type: "macro_calendar", status: "disabled", enabled_capabilities: [], missing_requirements: ["macro_data_provider"] },
      ],
      overall_readiness: "partial",
      available_count: 1,
      missing_providers: ["macro_data"],
    };
    render(<ResearchAgentsReadinessPage />);
    expect(screen.getByText("SEC Filing Agent")).toBeTruthy();
    expect(screen.getByText("Macro Calendar Agent")).toBeTruthy();
    expect(screen.getByText("macro_data")).toBeTruthy();
    expect(screen.getByText("partial")).toBeTruthy();
  });
});

describe("ScoresSymbolBreakdownPage 评分分解", () => {
  it("渲染 8 维评分 + 加权贡献", () => {
    stubMarketSymbol("AAPL");
    mockApiData["/api/v1/us-radar/scores/AAPL/breakdown"] = {
      symbol: "AAPL",
      total_score: 71,
      signal: "bullish",
      breakdown: [
        { dimension: "technical", score: 75, weight: 0.3, weighted_contribution: 22.5, signal: "positive" },
        { dimension: "sentiment", score: 40, weight: 0.2, weighted_contribution: -8, signal: "negative" },
      ],
    };
    render(<ScoresSymbolBreakdownPage />);
    expect(screen.getByText("71")).toBeTruthy();
    expect(screen.getByText("bullish")).toBeTruthy();
    expect(screen.getByText("技术")).toBeTruthy();
    expect(screen.getByText("情绪")).toBeTruthy();
    expect(screen.getByText(/22\.5/)).toBeTruthy();
  });
});

describe("SnapshotPage 快照", () => {
  it("渲染快照: 指数 + 事件信号 + 全量评分", () => {
    mockApiData[SNAPSHOT_URL] = {
      indices: [{ symbol: "^GSPC", shortName: "S&P 500", regularMarketPrice: 5400, regularMarketChangePercent: 0.5 }],
      event_signals: [{ symbol: "NVDA", signal: "bullish", score: 82 }],
      radar_scores: { AAPL: { total_score: 71, signal: "bullish" } },
    };
    render(<SnapshotPage />);
    expect(screen.getByText("^GSPC")).toBeTruthy();
    expect(screen.getByText("NVDA")).toBeTruthy();
    expect(screen.getByText("71")).toBeTruthy();
  });
});

describe("SymbolsSymbolPage 符号详情", () => {
  it("渲染雷达分 + 维度 + 风险标记", () => {
    stubMarketSymbol("AAPL");
    mockApiData["/api/v1/us-radar/symbols/AAPL"] = {
      symbol: "AAPL",
      radar_score: { total_score: 71, dimensions: { technical: 75, sentiment: 40 } },
      signal: "bullish",
      evidence: { news_count: 5, chart_points: 120 },
      risk_flags: ["high_volume_alert"],
    };
    render(<SymbolsSymbolPage />);
    expect(screen.getByText("71")).toBeTruthy();
    expect(screen.getByText("bullish")).toBeTruthy();
    expect(screen.getByText("技术")).toBeTruthy();
    expect(screen.getByText("high_volume_alert")).toBeTruthy();
  });

  it("导航按钮存在: 评分分解 / AI 控制台", () => {
    stubMarketSymbol("AAPL");
    mockApiData["/api/v1/us-radar/symbols/AAPL"] = { symbol: "AAPL", radar_score: { dimensions: {} } };
    render(<SymbolsSymbolPage />);
    expect(screen.getByText("评分分解")).toBeTruthy();
    expect(screen.getByText("AI 控制台")).toBeTruthy();
  });
});

describe("SymbolsSymbolAiConsolePage AI 控制台", () => {
  it("渲染 Thoughts + Alpha 分解 + 执行建议", () => {
    stubMarketSymbol("AAPL");
    mockApiData["/api/v1/us-radar/symbols/AAPL/ai-console"] = {
      symbol: "AAPL",
      thoughts: {
        thesis: "Apple is trading at all-time highs with strong momentum.",
        key_observations: ["Strong positive technical signal"],
        llm_enhanced: true,
      },
      alpha: {
        direction: "bullish",
        confidence: 0.8,
        fusion_score: 0.71,
        fusion_breakdown: { total_score: 0.71, contributions: [{ contributor_type: "technical", score: 75, contribution: 0.2 }] },
        expected_holding_period: "swing",
      },
      execution: {
        suggested_actions: [{ action_type: "consider_long", entry_price: 210, stop_loss: 199.5, take_profit: 231, position_size_pct: 5 }],
      },
      system: { data_mode: "delayed", provider: "public", evidence_count: 125, llm_status: "configured" },
    };
    render(<SymbolsSymbolAiConsolePage />);
    expect(screen.getByText(/Apple is trading at all-time highs/)).toBeTruthy();
    expect(screen.getByText("swing")).toBeTruthy();
    expect(screen.getByText("consider_long")).toBeTruthy();
    expect(screen.getByText("configured")).toBeTruthy();
  });
});

describe("UniversePage US 宇宙", () => {
  it("渲染标的行 + 按类型分组", () => {
    mockApiData["/api/v1/us-radar/universe"] = {
      universe: [
        { symbol: "AAPL", name: "Apple Inc", kind: "single_stock", sector: "technology", price: 210, change_pct: 1.2, volume: 50000000 },
        { symbol: "SPY", name: "SPDR S&P 500 ETF", kind: "index_etf", sector: "broad_market", price: 540, change_pct: 0.5, volume: 70000000 },
      ],
      by_kind: {
        single_stock: [{ symbol: "AAPL", name: "Apple Inc", kind: "single_stock", sector: "technology", price: 210, change_pct: 1.2, volume: 50000000 }],
        index_etf: [{ symbol: "SPY", name: "SPDR S&P 500 ETF", kind: "index_etf", sector: "broad_market", price: 540, change_pct: 0.5, volume: 70000000 }],
      },
      total: 2,
    };
    render(<UniversePage />);
    expect(screen.getByText("AAPL")).toBeTruthy();
    expect(screen.getByText("SPY")).toBeTruthy();
    expect(screen.getByText(/指数 ETF/)).toBeTruthy();
    expect(screen.getByText(/个股/)).toBeTruthy();
  });
});
