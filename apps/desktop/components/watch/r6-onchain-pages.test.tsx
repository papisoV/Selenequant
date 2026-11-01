import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DexPairsPage } from "./dex-pairs-page";
import { OnchainMetricsPage } from "./onchain-metrics-page";
import { OnchainActiveAddressesPage } from "./onchain-metric-page";
import { HyperliquidAddressesScreenPage } from "./hyperliquid-addresses-screen-page";
import { HyperliquidAddressesPipelinePage } from "./hyperliquid-addresses-pipeline-page";

// === mock store ===
vi.mock("@/lib/store/context", () => ({
  useContextStore: () => ({
    pageFilter: { symbol: "BTC", tf: "1h", exchange: "all" },
    setPageFilter: () => {},
  }),
}));
vi.mock("@/lib/store/market", () => ({
  useMarketStore: () => ({ pageFilter: { symbol: "BTC", tf: "1h", exchange: "all" } }),
}));

// === mock swr — 按 key 返回预设数据,消除真 SWR 缓存/竞态 ===
const { swrData, swrMutate } = vi.hoisted(() => ({
  swrData: {} as Record<string, unknown>,
  swrMutate: vi.fn(() => Promise.resolve()),
}));

vi.mock("swr", () => ({
  default: (key: string | null) => ({
    data: key != null ? (swrData[key] ?? null) : null,
    error: undefined,
    isLoading: false,
    mutate: swrMutate,
  }),
  preload: vi.fn(),
}));

const MOCK_PAIR = {
  chain_id: "ethereum",
  dex_id: "uniswap_v3",
  pair_address: "0xabc",
  base_token_symbol: "WETH",
  base_token_address: "0xW",
  quote_token_symbol: "USDC",
  quote_token_address: "0xU",
  price_usd: 2500.5,
  price_native: 2500.5,
  liquidity_usd: 10_000_000,
  volume_24h: 2_500_000,
  tx_count_24h: 320,
  price_change_24h: 1.5,
  url: null,
  timestamp: Date.now(),
};

beforeEach(() => {
  Object.keys(swrData).forEach((k) => delete swrData[k]);
  swrMutate.mockClear();
});

describe("DexPairsPage(dex-pairs 孤儿页)", () => {
  it("渲染 pair 卡 + 聚合统计", () => {
    swrData["dex-pairs-orphan-ethereum"] = { pairs: [MOCK_PAIR], count: 1, chain: "ethereum" };
    render(<DexPairsPage />);
    expect(screen.getAllByText("WETH").length).toBeGreaterThan(0);
    expect(screen.getAllByText("USDC").length).toBeGreaterThan(0);
  });

  it("刷新按钮 → mutate 调用", () => {
    swrData["dex-pairs-orphan-ethereum"] = { pairs: [], count: 0, chain: "ethereum" };
    const { container } = render(<DexPairsPage />);
    fireEvent.click(container.querySelector('[aria-label="refresh"]')!);
    expect(swrMutate).toHaveBeenCalled();
  });
});

describe("OnchainMetricsPage(onchain-metrics 孤儿页)", () => {
  it("渲染 3 指标", () => {
    swrData["onchain-metrics-BTC-0"] = {
      active_addresses: { value: 1_200_000 },
      nvt_ratio: { value: 42.5 },
      sopr: { value: 1.02 },
    };
    render(<OnchainMetricsPage />);
    expect(screen.getByText("1M")).toBeTruthy();
    expect(screen.getByText("42.5")).toBeTruthy();
    expect(screen.getByText("1.020")).toBeTruthy();
  });
});

describe("OnchainActiveAddressesPage(onchain-metrics-active-addresses 孤儿页)", () => {
  it("渲染单指标 value", () => {
    swrData["onchain-active-addresses-BTC-0"] = { symbol: "BTC", metric: "active_addresses", value: 2_300_000 };
    render(<OnchainActiveAddressesPage />);
    expect(screen.getByText("2.3M")).toBeTruthy();
  });
});

describe("HyperliquidAddressesScreenPage(hyperliquid-addresses-screen 孤儿页)", () => {
  it("渲染筛选行 + 分类 + 胜率", () => {
    swrData["hl-screen-all-30"] = {
      results: [
        {
          address: "0xAAAA",
          classification: "elite",
          score: 88,
          metrics: { win_rate: 0.6, profit_factor: 1.8, realized_pnl: 50_000 },
          risk_flags: [],
        },
      ],
      count: 1,
      classification: "all",
      window_days: 30,
    };
    render(<HyperliquidAddressesScreenPage />);
    expect(screen.getAllByText("精英").length).toBeGreaterThan(0);
    expect(screen.getByText("60%")).toBeTruthy();
  });
});

describe("HyperliquidAddressesPipelinePage(hyperliquid-addresses-pipeline 孤儿页)", () => {
  it("渲染缓存统计", () => {
    swrData["hl-pipeline"] = {
      candidate_source: "stats_leaderboard",
      screen_cache_entries: 5,
      detail_cache_entries: 3,
      screen_cache_age_s: 120,
    };
    render(<HyperliquidAddressesPipelinePage />);
    expect(screen.getByText("5")).toBeTruthy();
    expect(screen.getByText("3")).toBeTruthy();
    expect(screen.getByText("2m 0s")).toBeTruthy();
  });
});
