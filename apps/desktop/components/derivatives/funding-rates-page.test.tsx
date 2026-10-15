import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

// === mock useApi =================================
const mockApiData: Record<string, unknown> = {};
vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string) => ({ data: mockApiData[url] ?? null, loading: false, error: null }),
}));

// === mock card primitives (avoid heavy deps) =====
vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

// === import AFTER mocks =================================
import { FundingRatesPage } from "./funding-rates-page";

const URL = "/api/v1/derivatives/funding-rates";

describe("FundingRatesPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders funding rate rows when populated", () => {
    mockApiData[URL] = {
      rates: {
        "BTC/USDT": {
          exchange: "binance",
          symbol: "BTC/USDT",
          funding_rate: 0.0001,
          mark_price: 68000,
          index_price: 67990,
          next_funding_time: 1711036800000,
          timestamp: 1711000000000,
        },
        "ETH/USDT": {
          exchange: "okx",
          symbol: "ETH/USDT",
          funding_rate: -0.00005,
          mark_price: 3500,
          index_price: 3499,
          next_funding_time: 1711036800000,
          timestamp: 1711000000000,
        },
      },
    };
    render(<FundingRatesPage />);
    expect(screen.getByTestId("card-资金费率 (WS)")).toBeTruthy();
    // 正费率显示 +0.0100%
    expect(screen.getByText("+0.0100%")).toBeTruthy();
    // 负费率显示 -0.0050%
    expect(screen.getByText("-0.0050%")).toBeTruthy();
    // symbol 行
    expect(screen.getByText("BTC/USDT")).toBeTruthy();
    expect(screen.getByText("ETH/USDT")).toBeTruthy();
    // 统计
    expect(screen.getByTestId("stat-跟踪数")).toBeTruthy();
  });

  it("shows 暂无 placeholder when API returns empty", () => {
    mockApiData[URL] = { rates: {} };
    render(<FundingRatesPage />);
    expect(screen.getByText(/暂无资金费率数据/)).toBeTruthy();
  });
});
