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

import { FundingRatesAllExchangesPage } from "./funding-rates-all-exchanges-page";

const URL = "/api/v1/derivatives/funding-rates/all-exchanges?exchange=all";

describe("FundingRatesAllExchangesPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders exchange rows when populated", () => {
    mockApiData[URL] = {
      rates: [
        {
          exchange: "binance",
          symbol: "BTC/USDT",
          funding_rate: 0.0001,
          mark_price: 68000,
          index_price: 67990,
          next_funding_time: 1711036800000,
          timestamp: 1711000000000,
          open_interest: 12345,
          long_short_ratio: 1.2,
        },
        {
          exchange: "okx",
          symbol: "ETH/USDT",
          funding_rate: -0.00005,
          mark_price: 3500,
          index_price: 3499,
          next_funding_time: 1711036800000,
          timestamp: 1711000000000,
        },
      ],
      count: 2,
      exchanges: ["binance", "okx"],
    };
    render(<FundingRatesAllExchangesPage />);
    expect(screen.getByTestId("card-全交易所资金费率 (REST)")).toBeTruthy();
    expect(screen.getByText("BTC/USDT")).toBeTruthy();
    expect(screen.getByText("ETH/USDT")).toBeTruthy();
    expect(screen.getByText("+0.0100%")).toBeTruthy();
    expect(screen.getByText("-0.0050%")).toBeTruthy();
    expect(screen.getByTestId("stat-总费率数")).toBeTruthy();
  });

  it("shows 暂无 placeholder when API returns empty", () => {
    mockApiData[URL] = { rates: [], count: 0, exchanges: [] };
    render(<FundingRatesAllExchangesPage />);
    expect(screen.getByText(/暂无资金费率数据/)).toBeTruthy();
  });
});
