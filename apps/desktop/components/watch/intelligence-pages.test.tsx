import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// === mock useApiPage (返回的 useApi 按 path 返回 mock 数据) ===
const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api-page", () => ({
  useApiPage: () => ({
    useApi: (url: string | null) => ({
      data: url ? (mockApiData[url] ?? null) : null,
      isLoading: false,
      error: null,
      mutate: vi.fn(),
    }),
  }),
}));

// === mock next/navigation (agent-analysis-status 动态页) ===
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("analysis_id=abc123"),
}));

// === mock sendAgentChat (agent-chat 页) ===
const mockSendAgentChat = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api/chat", () => ({
  sendAgentChat: mockSendAgentChat,
}));

// === mock useMarketStore (agent-chat 页,取 pageFilter) ===
vi.mock("@/lib/store/market", () => ({
  useMarketStore: () => ({
    pageFilter: { symbol: "BTC", tf: "1h", exchange: "all" },
  }),
}));

// === mock card primitives ===
vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

import { IntelligenceMarketPulseHistoryPage } from "./intelligence-market-pulse-history-page";
import { IntelligenceNewsFactorPipelinePage } from "./intelligence-news-factor-pipeline-page";
import { IntelligenceNewsSummaryPage } from "./intelligence-news-summary-page";
import { IntelligenceNewsTokenSignalsPage } from "./intelligence-news-token-signals-page";
import { IntelligenceSocialConfirmationPage } from "./intelligence-social-confirmation-page";
import { IntelligenceSocialSentimentPage } from "./intelligence-social-sentiment-page";
import { IntelligenceSocialTrendingPage } from "./intelligence-social-trending-page";
import { McpToolsPage } from "@/components/system/mcp-tools-page";
import { AgentAnalysisStatusPage } from "@/components/agent/agent-analysis-status-page";
import { AgentTradingWorkspacePage } from "@/components/agent/agent-trading-workspace-page";
import { AgentChatPage } from "@/components/agent/agent-chat-page";
import { AgentAnalysisStreamPage } from "@/components/agent/agent-analysis-stream-page";

const PULSE = "/api/v1/intelligence/market-pulse/history";
const PIPELINE = "/api/v1/intelligence/news/factor-pipeline";
const NEWS_SUMMARY = "/api/v1/intelligence/news/summary";
const TOKEN_SIGNALS = "/api/v1/intelligence/news/token-signals";
const CONFIRMATION = "/api/v1/intelligence/social/confirmation";
const SENTIMENT = "/api/v1/intelligence/social/sentiment";
const TRENDING = "/api/v1/intelligence/social/trending";
const MCP_TOOLS = "/api/v1/mcp/tools";
const STATUS_URL = "/api/v1/agent/analysis/status/abc123";
const WORKSPACE = "/api/v1/agent/trading/workspace";
const LESSONS = "/api/v1/agent/decision-lessons";

beforeEach(() => {
  Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
});

describe("IntelligenceMarketPulseHistoryPage", () => {
  it("renders current pulse stats + history table when data present", () => {
    mockApiData[PULSE] = {
      current: { timestamp: 1750000000, btc_dominance: 52.3, fear_greed_index: 45, market_cap: 2450000000000 },
      pulses: [
        { timestamp: 1750000000, btc_dominance: 52.3, fear_greed_index: 45, market_cap: 2450000000000 },
        { timestamp: 1749996400, btc_dominance: 52.0, fear_greed_index: 40, market_cap: 2440000000000 },
      ],
    };
    render(<IntelligenceMarketPulseHistoryPage />);
    expect(screen.getByTestId("stat-BTC 占比").textContent).toContain("52.3");
    expect(screen.getByTestId("stat-恐慌贪婪").textContent).toContain("45");
    expect(screen.getByTestId("card-历史脉冲 (最近 24h)")).toBeTruthy();
  });

  it("shows 暂无数据 when empty", () => {
    mockApiData[PULSE] = { current: {}, pulses: [] };
    render(<IntelligenceMarketPulseHistoryPage />);
    expect(screen.getByText(/暂无数据/)).toBeTruthy();
  });
});

describe("IntelligenceNewsFactorPipelinePage", () => {
  it("renders pipeline stages with status", () => {
    mockApiData[PIPELINE] = {
      stages: [
        { name: "raw_feed", label: "Raw Fast Feed", status: "active", throughput_per_min: 12, lag_ms: 200, queue_depth: 3 },
        { name: "ai_parse", label: "AI Event Parse", status: "idle", throughput_per_min: 0, lag_ms: null, queue_depth: 0 },
      ],
      llm_available: false,
    };
    render(<IntelligenceNewsFactorPipelinePage />);
    expect(screen.getByTestId("stat-LLM").textContent).toContain("未配置");
    expect(screen.getByText("Raw Fast Feed")).toBeTruthy();
    expect(screen.getByText("active")).toBeTruthy();
  });

  it("shows 暂无数据 when empty", () => {
    mockApiData[PIPELINE] = { stages: [], llm_available: false };
    render(<IntelligenceNewsFactorPipelinePage />);
    expect(screen.getByText(/暂无数据/)).toBeTruthy();
  });
});

describe("IntelligenceNewsSummaryPage", () => {
  it("renders LLM summary + pulse stats + headlines", () => {
    mockApiData[NEWS_SUMMARY] = {
      summary: "Crypto sentiment is positive.",
      headlines: [
        { title: "BTC rallies", source: "r/crypto", url: "https://reddit.com/x", published_at: 1750000000000, sentiment: "positive" },
      ],
      pulse: { positive: 8, negative: 2, neutral: 5 },
      llm_available: true,
    };
    render(<IntelligenceNewsSummaryPage />);
    expect(screen.getByText(/Crypto sentiment is positive/)).toBeTruthy();
    expect(screen.getByTestId("stat-积极").textContent).toContain("%");
    expect(screen.getByText("BTC rallies")).toBeTruthy();
  });
});

describe("IntelligenceNewsTokenSignalsPage", () => {
  it("renders token cards with direction + score bar", () => {
    mockApiData[TOKEN_SIGNALS] = {
      tokens: {
        BTC: { direction: "neutral", score: 0.52, confidence: 0.6, key_events: ["ETF flows"], price_context: { price: 62500, change_24h: 0.8 } },
        ETH: { direction: "bullish", score: 0.65, confidence: 0.55, key_events: [], price_context: { price: 3420, change_24h: 2.1 } },
      },
      data_mode: "rule_based",
      llm_enhanced: false,
    };
    render(<IntelligenceNewsTokenSignalsPage />);
    expect(screen.getByText("BTC")).toBeTruthy();
    expect(screen.getByText("ETH")).toBeTruthy();
    expect(screen.getByText("neutral")).toBeTruthy();
    expect(screen.getByText("bullish")).toBeTruthy();
  });
});

describe("IntelligenceSocialConfirmationPage", () => {
  it("renders confirmations with alignment badges", () => {
    mockApiData[CONFIRMATION] = {
      confirmations: [
        { topic: "BTC ETF", subreddit: "r/crypto", news_correlation: true, community_heat: 75, sentiment_alignment: "aligned", posts_count: 1, comments_count: 50 },
        { topic: "SOL upgrade", subreddit: "r/solana", news_correlation: false, community_heat: 20, sentiment_alignment: "independent", posts_count: 1, comments_count: 5 },
      ],
      total: 2,
      llm_enhanced: true,
    };
    render(<IntelligenceSocialConfirmationPage />);
    expect(screen.getByText("BTC ETF")).toBeTruthy();
    expect(screen.getByText("SOL upgrade")).toBeTruthy();
    expect(screen.getByText("aligned")).toBeTruthy();
  });
});

describe("IntelligenceSocialSentimentPage", () => {
  it("renders overall sentiment + per-subreddit bars", () => {
    mockApiData[SENTIMENT] = {
      overall: 0.62,
      by_subreddit: { "r/crypto": 0.62, "r/ethereum": 0.55 },
    };
    render(<IntelligenceSocialSentimentPage />);
    expect(screen.getByTestId("stat-综合情绪").textContent).toContain("0.62");
    expect(screen.getByTestId("stat-追踪社区").textContent).toContain("2");
    expect(screen.getByText("r/crypto")).toBeTruthy();
  });
});

describe("IntelligenceSocialTrendingPage", () => {
  it("renders trending topics ranked", () => {
    mockApiData[TRENDING] = {
      topics: [
        { title: "Bitcoin all time high", subreddit: "r/crypto", score: 5000, upvote_ratio: 0.9, url: "https://reddit.com/x", num_comments: 200 },
      ],
      updated_at: 1750000000,
    };
    render(<IntelligenceSocialTrendingPage />);
    expect(screen.getByText("Bitcoin all time high")).toBeTruthy();
    expect(screen.getByText("r/crypto")).toBeTruthy();
  });
});

describe("McpToolsPage", () => {
  it("renders tools table from backend shape", () => {
    mockApiData[MCP_TOOLS] = {
      tools: [
        { server_name: "dev", tool_name: "search", registered_name: "mcp_dev_search", description: "Search codebase" },
      ],
    };
    render(<McpToolsPage />);
    expect(screen.getByTestId("stat-工具总数").textContent).toContain("1");
    expect(screen.getByText("search")).toBeTruthy();
    expect(screen.getByText("mcp_dev_search")).toBeTruthy();
  });

  it("shows 暂无 MCP 工具 when empty", () => {
    mockApiData[MCP_TOOLS] = { tools: [] };
    render(<McpToolsPage />);
    expect(screen.getByText(/暂无 MCP 工具/)).toBeTruthy();
  });
});

describe("AgentAnalysisStatusPage", () => {
  it("uses analysis_id from URL and renders status + result", () => {
    mockApiData[STATUS_URL] = {
      analysis_id: "abc123",
      status: "completed",
      created_at: "2026-08-19T08:00:00Z",
      result: { alpha: 0.42 },
    };
    render(<AgentAnalysisStatusPage />);
    expect(screen.getByTestId("stat-分析 ID").textContent).toContain("abc123");
    expect(screen.getByTestId("stat-状态").textContent).toContain("completed");
    expect(screen.getByText(/alpha/)).toBeTruthy();
  });
});

describe("AgentTradingWorkspacePage", () => {
  it("renders account stats + positions + orders + strategies", () => {
    mockApiData[WORKSPACE] = {
      account_summary: { balance: 10000, equity: 10500, available: 5000, unrealized_pnl: 500 },
      positions: [{ symbol: "BTC-USDT-SWAP", side: "long", size: 1, entry_price: 62000, mark_price: 62500, unrealized_pnl: 500 }],
      pending_orders: [{ id: "o1", symbol: "BTC-USDT-SWAP", side: "buy", size: 0.5, price: 61000, type: "limit" }],
      active_strategies: [{ id: "s1", name: "bb_v3", symbol: "BTC", status: "active" }],
    };
    render(<AgentTradingWorkspacePage />);
    expect(screen.getByTestId("stat-账户余额").textContent).toContain("10000");
    expect(screen.getByTestId("stat-未实现盈亏").textContent).toContain("500");
    expect(screen.getAllByText("BTC-USDT-SWAP").length).toBeGreaterThan(0);
    expect(screen.getByText("bb_v3")).toBeTruthy();
  });

  it("shows 无持仓 when empty", () => {
    mockApiData[WORKSPACE] = { positions: [], pending_orders: [], active_strategies: [], account_summary: {} };
    render(<AgentTradingWorkspacePage />);
    expect(screen.getByText(/无持仓/)).toBeTruthy();
  });

  it("flags REVIEW lessons with a distinct warning badge", () => {
    mockApiData[WORKSPACE] = { positions: [], pending_orders: [], active_strategies: [], account_summary: {} };
    mockApiData[LESSONS] = [
      {
        deployment_id: "d1",
        strategy_id: "s1",
        entry_date: "2026-10-01",
        rating: "REVIEW",
        is_review: true,
        status: "filled",
      },
      {
        deployment_id: "d2",
        strategy_id: "s2",
        entry_date: "2026-10-02",
        rating: "Buy",
        is_review: false,
        status: "filled",
      },
    ];
    render(<AgentTradingWorkspacePage />);
    // REVIEW 决策必须显式标注"需人看",不能与普通评级一样裸显。
    expect(screen.getByText(/需人看|需复核|评级读不出/)).toBeTruthy();
    // 可交易评级(Buy)不出现该警告。
    expect(screen.getByText("Buy")).toBeTruthy();
  });
});

describe("AgentChatPage", () => {
  it("renders title with symbol/tf + example prompts", () => {
    render(<AgentChatPage />);
    expect(screen.getByTestId("card-Agent 对话 · BTC 1h")).toBeTruthy();
    expect(screen.getByText(/分析 BTC 当前行情/)).toBeTruthy();
    expect(screen.getByText(/交易建议与风险/)).toBeTruthy();
  });

  it("sends message via sendAgentChat when 发送 clicked", async () => {
    mockSendAgentChat.mockResolvedValue(undefined);
    render(<AgentChatPage />);
    fireEvent.change(screen.getByPlaceholderText(/向 AI 提问/), { target: { value: "分析 BTC" } });
    fireEvent.click(screen.getByText("发送"));
    expect(mockSendAgentChat).toHaveBeenCalled();
    expect(mockSendAgentChat.mock.calls[0][0]).toBe("分析 BTC");
    expect(screen.getByText("分析 BTC")).toBeTruthy();
  });
});

describe("AgentAnalysisStreamPage", () => {
  it("renders input + connect button", () => {
    // jsdom 无 Element.scrollTo
    if (typeof window !== "undefined" && !(window.HTMLElement.prototype as unknown as Record<string, unknown>).scrollTo) {
      (window.HTMLElement.prototype as unknown as Record<string, unknown>).scrollTo = () => {};
    }
    render(<AgentAnalysisStreamPage />);
    expect(screen.getByPlaceholderText("输入 analysis_id")).toBeTruthy();
    expect(screen.getByText("连接")).toBeTruthy();
  });
});
