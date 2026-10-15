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

import { LiquidationStatsAllPage } from "./liquidation-stats-all-page";

const URL = "/api/v1/derivatives/liquidation-stats/all";

describe("LiquidationStatsAllPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders window cards when populated", () => {
    mockApiData[URL] = {
      windows: {
        "1h": {
          symbol: "ALL",
          window: "1h",
          long_liquidated_usd: 5_000_000,
          short_liquidated_usd: 3_000_000,
          total_liquidated_usd: 8_000_000,
          long_count: 42,
          short_count: 31,
          largest_single_usd: 1_500_000,
          timestamp: 1711000000000,
        },
        "24h": {
          symbol: "ALL",
          window: "24h",
          long_liquidated_usd: 50_000_000,
          short_liquidated_usd: 30_000_000,
          total_liquidated_usd: 80_000_000,
          long_count: 420,
          short_count: 310,
          largest_single_usd: 15_000_000,
          timestamp: 1711000000000,
        },
      },
    };
    render(<LiquidationStatsAllPage />);
    expect(screen.getByTestId("card-全市场爆仓统计")).toBeTruthy();
    expect(screen.getByText("1h")).toBeTruthy();
    expect(screen.getByText("24h")).toBeTruthy();
    expect(screen.getByText("$8.00M")).toBeTruthy();
    expect(screen.getByText("$80.00M")).toBeTruthy();
  });

  it("shows 暂无 when API returns empty windows", () => {
    mockApiData[URL] = { windows: {} };
    render(<LiquidationStatsAllPage />);
    expect(screen.getByText(/暂无爆仓统计数据/)).toBeTruthy();
  });
});
