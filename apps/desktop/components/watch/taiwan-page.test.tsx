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
import { TaiwanPage } from "./taiwan-page";

const URLS = {
  quote: "/api/v1/taiwan/2330/quote",
  valuation: "/api/v1/taiwan/2330/valuation",
  revenue: "/api/v1/taiwan/2330/revenue",
  dividend: "/api/v1/taiwan/2330/dividend",
  financials: "/api/v1/taiwan/2330/financials",
};

function populatedApi() {
  mockApiData[URLS.quote] = {
    stock_id: "2330", name: "台积电", board: "上市", currency: "TWD",
    date: "2025-03-22", open: 980, max: 1010, min: 975, close: 1000,
    change: 20, change_pct: 2.04, trading_volume: 15000, trading_money: 1.5e10,
    per: 22.5, pbr: 5.2, dividend_yield: 1.8, shares_issued: 2.6e10, market_cap: 2.6e13,
  };
  mockApiData[URLS.valuation] = {
    close: 1000, per: 22.5, per_year_range: [15.2, 28.4], pbr: 5.2,
    dividend_yield: 1.8, high_52w: 1100, low_52w: 850, market_cap: 2.6e13,
  };
  mockApiData[URLS.revenue] = {
    months: [
      { year: 2025, month: 2, revenue: 210000000000, yoy_pct: 18.5 },
      { year: 2025, month: 3, revenue: 220000000000, yoy_pct: 20.1 },
    ],
  };
  mockApiData[URLS.dividend] = {
    dividends: [
      { year: "2024", cash: 15.0, stock: 0, cash_ex_date: "2025-03-20" },
    ],
  };
  mockApiData[URLS.financials] = {
    years: [
      { year: "2024", full_year: true, revenue: 2.6e12, gross_profit: 1.3e12, gross_margin_pct: 50.0,
        operating_income: 1.2e12, operating_margin_pct: 46.0, net_income: 1.1e12,
        net_margin_pct: 42.0, eps: 45.0, roe_pct: 32.0 },
    ],
  };
}

describe("TaiwanPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders quote + financials + revenue + dividend cards with data", () => {
    populatedApi();
    render(<TaiwanPage />);

    expect(screen.getByTestId("card-行情快照")).toBeTruthy();
    expect(screen.getByTestId("card-财务摘要 (年)")).toBeTruthy();
    expect(screen.getByTestId("card-月营收 (近13个月)")).toBeTruthy();
    expect(screen.getByTestId("card-股利政策 (近5年)")).toBeTruthy();
    // 收盘价统计块有值
    expect(screen.getByTestId("stat-收盘价").textContent).toContain("1,000");
    // 财务表格渲染年份 (ROE 值定位行,避免与股利年份撞文本)
    expect(screen.getByText("32.00%")).toBeTruthy();
    // 营收表格渲染月份
    expect(screen.getByText("2025-03")).toBeTruthy();
    // 股利渲染 (现金股利值)
    expect(screen.getByText("15")).toBeTruthy();
  });

  it("shows 暂无 placeholders when API returns empty", () => {
    render(<TaiwanPage />);
    expect(screen.getByText(/暂无财务数据/)).toBeTruthy();
    expect(screen.getByText(/暂无营收数据/)).toBeTruthy();
    expect(screen.getByText(/暂无股利数据/)).toBeTruthy();
    expect(screen.getByTestId("stat-收盘价").textContent).toContain("—");
  });
});
