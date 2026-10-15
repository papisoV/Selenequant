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

import { OpenInterestPage } from "./open-interest-page";

const URL = "/api/v1/derivatives/open-interest";

describe("OpenInterestPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders OI rows when populated", () => {
    mockApiData[URL] = {
      snapshots: {
        "BTC/USDT": {
          exchange: "binance",
          symbol: "BTC/USDT",
          open_interest: 12345.67,
          open_interest_usd: 839_506_560,
          timestamp: 1711000000000,
        },
        "ETH/USDT": {
          exchange: "okx",
          symbol: "ETH/USDT",
          open_interest: 23456,
          open_interest_usd: 82_096_000,
          timestamp: 1711000000000,
        },
      },
    };
    render(<OpenInterestPage />);
    expect(screen.getByTestId("card-未平仓量")).toBeTruthy();
    expect(screen.getByText("BTC/USDT")).toBeTruthy();
    expect(screen.getByText("ETH/USDT")).toBeTruthy();
    // 12345.67 → 12.3K（formatNum toFixed(1)）
    expect(screen.getByText("12.3K")).toBeTruthy();
    // $839.51M 出现在表格行 + StatBlock 最大 OI
    expect(screen.getAllByText("$839.51M").length).toBeGreaterThan(0);
    expect(screen.getByTestId("stat-跟踪数")).toBeTruthy();
  });

  it("shows 暂无 placeholder when API returns empty snapshots", () => {
    mockApiData[URL] = { snapshots: {} };
    render(<OpenInterestPage />);
    expect(screen.getByText(/暂无未平仓量数据/)).toBeTruthy();
  });
});
