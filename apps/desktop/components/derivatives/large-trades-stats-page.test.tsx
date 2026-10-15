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

import { LargeTradesStatsPage } from "./large-trades-stats-page";

const URL = "/api/v1/derivatives/large-trades/stats?window_ms=3600000";

describe("LargeTradesStatsPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders stats blocks when populated", () => {
    mockApiData[URL] = {
      window_ms: 3600000,
      buy_count: 12,
      sell_count: 8,
      buy_volume_usd: 2_000_000,
      sell_volume_usd: 1_200_000,
      total_count: 20,
    };
    render(<LargeTradesStatsPage />);
    expect(screen.getByTestId("card-大宗成交统计")).toBeTruthy();
    expect(screen.getByTestId("stat-总笔数")).toBeTruthy();
    expect(screen.getByTestId("stat-买单数")).toBeTruthy();
    expect(screen.getByTestId("stat-卖单数")).toBeTruthy();
    expect(screen.getByText("$2.00M")).toBeTruthy();
    expect(screen.getByText("$1.20M")).toBeTruthy();
  });

  it("shows 暂无 when API returns null", () => {
    render(<LargeTradesStatsPage />);
    expect(screen.getByTestId("stat-买金额").textContent).toBe("—");
    expect(screen.getByTestId("stat-卖金额").textContent).toBe("—");
  });
});
