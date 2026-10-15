import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// === mock useApi =================================
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

import { LargeTradesPage } from "./large-trades-page";

const URL = "/api/v1/derivatives/large-trades/recent?limit=50";

describe("LargeTradesPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders trade rows when populated", () => {
    mockApiData[URL] = {
      events: [
        {
          exchange: "binance",
          symbol: "BTC/USDT",
          market_type: "futures",
          side: "buy",
          price: 68000,
          quantity: 2.5,
          notional_usd: 170000,
          trade_id: "123",
          timestamp: 1711000000000,
        },
        {
          exchange: "okx",
          symbol: "ETH/USDT",
          market_type: "futures",
          side: "sell",
          price: 3500,
          quantity: 10,
          notional_usd: 35000,
          trade_id: "456",
          timestamp: 1711000000000,
        },
      ],
      count: 2,
      threshold_usd: 100000,
    };
    render(<LargeTradesPage />);
    expect(screen.getByTestId("card-大宗成交")).toBeTruthy();
    expect(screen.getByText("BTC/USDT")).toBeTruthy();
    expect(screen.getByText("ETH/USDT")).toBeTruthy();
    expect(screen.getByText("BUY")).toBeTruthy();
    expect(screen.getByText("SELL")).toBeTruthy();
    // $170.0K 出现在表格行 + header 买金额 + StatBlock 买金额
    expect(screen.getAllByText("$170.0K").length).toBeGreaterThan(0);
    expect(screen.getByTestId("stat-总笔数")).toBeTruthy();
  });

  it("shows 暂无 placeholder when API returns empty", () => {
    mockApiData[URL] = { events: [], count: 0, threshold_usd: 100000 };
    render(<LargeTradesPage />);
    expect(screen.getByText(/暂无大宗成交数据/)).toBeTruthy();
  });
});
