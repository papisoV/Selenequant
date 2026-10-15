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

import { LiquidationStatsPage } from "./liquidation-stats-page";

const URL = "/api/v1/derivatives/liquidation-stats?window=1h";

describe("LiquidationStatsPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders stats when populated", () => {
    mockApiData[URL] = {
      symbol: "ALL",
      window: "1h",
      long_liquidated_usd: 5_000_000,
      short_liquidated_usd: 3_000_000,
      total_liquidated_usd: 8_000_000,
      long_count: 42,
      short_count: 31,
      largest_single_usd: 1_500_000,
      timestamp: 1711000000000,
    };
    render(<LiquidationStatsPage />);
    expect(screen.getByTestId("card-爆仓统计")).toBeTruthy();
    expect(screen.getByTestId("stat-总爆仓")).toBeTruthy();
    expect(screen.getByText("$8.00M")).toBeTruthy();
    expect(screen.getByText("$5.00M")).toBeTruthy();
    expect(screen.getByText("$3.00M")).toBeTruthy();
    expect(screen.getByTestId("stat-多头笔数")).toBeTruthy();
  });

  it("shows 暂无 when API returns null", () => {
    render(<LiquidationStatsPage />);
    expect(screen.getByText(/暂无数据/)).toBeTruthy();
  });
});
