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

import { MarketsPage } from "./markets-page";

const URL = "/api/v1/markets?exchange=binance&market_type=futures";

describe("MarketsPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders symbol chips when populated", () => {
    mockApiData[URL] = {
      symbols: ["BTC/USDT", "ETH/USDT", "BNB/USDT"],
      count: 3,
      exchange: "binance",
      market_type: "futures",
    };
    render(<MarketsPage />);
    expect(screen.getByTestId("card-市场符号")).toBeTruthy();
    expect(screen.getByText("BTC/USDT")).toBeTruthy();
    expect(screen.getByText("ETH/USDT")).toBeTruthy();
    expect(screen.getByText("BNB/USDT")).toBeTruthy();
    expect(screen.getByTestId("stat-总符号数")).toBeTruthy();
    expect(screen.getByTestId("stat-基础币数")).toBeTruthy();
  });

  it("shows 暂无 placeholder when API returns empty", () => {
    mockApiData[URL] = { symbols: [], count: 0, exchange: "binance", market_type: "futures" };
    render(<MarketsPage />);
    expect(screen.getByText(/暂无市场符号数据/)).toBeTruthy();
  });
});
