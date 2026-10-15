import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string) => ({ data: mockApiData[url] ?? null, loading: false, error: null }),
}));

vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

import { LiquidationsRecentPage } from "./liquidations-recent-page";

const URL = "/api/v1/derivatives/liquidations/recent?limit=50";

describe("LiquidationsRecentPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders liquidation rows when populated", () => {
    mockApiData[URL] = {
      events: [
        {
          exchange: "binance",
          symbol: "BTC/USDT",
          market_type: "futures",
          side: "long",
          quantity: 0.5,
          price: 68000,
          avg_price: 67950,
          notional_usd: 33975,
          status: "FILLED",
          timestamp: 1711000000000,
        },
        {
          exchange: "okx",
          symbol: "ETH/USDT",
          market_type: "futures",
          side: "short",
          quantity: 20,
          price: 3500,
          avg_price: 3495,
          notional_usd: 70000,
          status: "FILLED",
          timestamp: 1711000000000,
        },
      ],
      count: 2,
    };
    render(<LiquidationsRecentPage />);
    expect(screen.getByTestId("card-近期爆仓")).toBeTruthy();
    expect(screen.getByText("BTC/USDT")).toBeTruthy();
    expect(screen.getByText("ETH/USDT")).toBeTruthy();
    expect(screen.getByText("LONG")).toBeTruthy();
    expect(screen.getByText("SHORT")).toBeTruthy();
    // 33975 → $34.0K（formatUsd toFixed(1)）；出现在表格行 + header + StatBlock
    expect(screen.getAllByText("$34.0K").length).toBeGreaterThan(0);
    expect(screen.getByTestId("stat-总笔数")).toBeTruthy();
  });

  it("shows 暂无 placeholder when API returns empty", () => {
    mockApiData[URL] = { events: [], count: 0 };
    render(<LiquidationsRecentPage />);
    expect(screen.getByText(/暂无爆仓事件数据/)).toBeTruthy();
  });
});
