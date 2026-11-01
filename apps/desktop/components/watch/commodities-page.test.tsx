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
import { CommoditiesPage } from "./commodities-page";

const URLS = {
  kline: "/api/v1/commodities-fx/AU0/kline?period=1",
  fx: "/api/v1/commodities-fx/fx/quotes",
};

function populatedApi() {
  mockApiData[URLS.kline] = [
    { timestamp: "2026-08-15 00:40:00", open: 953.1, high: 953.2, low: 953.1, close: 953.2, volume: 10, hold: 198542, symbol: "AU0" },
    { timestamp: "2026-08-17 00:00:00", open: 953.6, high: 953.7, low: 953.4, close: 953.7, volume: 41, hold: 199050, symbol: "AU0" },
  ];
  mockApiData[URLS.fx] = [
    { pair: "AUD/USD", bid: 0.7, ask: 0.71 },
    { pair: "USD/CNY", bid: 6.74, ask: 6.75 },
  ];
}

describe("CommoditiesPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders kline chart + fx quotes when populated", () => {
    populatedApi();
    render(<CommoditiesPage />);

    expect(screen.getByTestId("card-大宗/外汇研究 (AKShare)")).toBeTruthy();
    expect(screen.getByTestId("card-K 线 AU0")).toBeTruthy();
    expect(screen.getByTestId("card-外汇即期报价")).toBeTruthy();
    // 最新 K 线收盘价经 KlineChart OHLC 渲染(H 与 C 同为 953.7,用 getAllByText)
    expect(screen.getAllByText("953.7").length).toBeGreaterThan(0);
    // 最低价唯一,精确断言
    expect(screen.getByText("953.4")).toBeTruthy();
    // FX 报价行
    expect(screen.getByText("AUD/USD")).toBeTruthy();
    expect(screen.getByText("USD/CNY")).toBeTruthy();
    // 边界标注
    expect(screen.getByText(/研究数据,非交易信号/)).toBeTruthy();
  });

  it("shows 暂无 placeholders when API returns empty", () => {
    render(<CommoditiesPage />);
    expect(screen.getByText(/暂无 K 线数据/)).toBeTruthy();
    expect(screen.getByText(/暂无外汇报价数据/)).toBeTruthy();
  });
});
