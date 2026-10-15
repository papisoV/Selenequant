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
import { AshareDeepPage } from "./ashare-deep-page";

const TODAY = new Date().toISOString().slice(0, 10).replace(/-/g, "");

const URLS = {
  fund: "/api/v1/ashare/fund-flow/600519",
  margin: "/api/v1/ashare/margin/600519",
  lhb: `/api/v1/ashare/lhb?date=${TODAY}`,
  northbound: "/api/v1/ashare/northbound",
};

function populatedApi() {
  mockApiData[URLS.fund] = [
    { date: "20240101", "主力净流入-净额": 100.5, "超大单净流入-净额": 50.25 },
  ];
  mockApiData[URLS.margin] = [
    { date: "20240105", "融资余额": 1234567.0, "融券余额": 890.0 },
  ];
  mockApiData[URLS.lhb] = [
    { "代码": "600519", "名称": "贵州茅台", "涨跌幅": 2.1, "上榜原因": "日涨幅偏离值达7%" },
  ];
  mockApiData[URLS.northbound] = [
    { date: "20240108", value: 88.88 },
  ];
}

describe("AshareDeepPage", () => {
  beforeEach(() => {
    Object.keys(mockApiData).forEach((k) => delete mockApiData[k]);
  });

  it("renders 4 data cards with populated rows", () => {
    populatedApi();
    render(<AshareDeepPage />);

    expect(screen.getByTestId("card-资金流 (东财个股)")).toBeTruthy();
    expect(screen.getByTestId("card-融资融券")).toBeTruthy();
    expect(screen.getByTestId("card-龙虎榜")).toBeTruthy();
    expect(screen.getByTestId("card-北向资金 (每日)")).toBeTruthy();
    // 资金流数值渲染
    expect(screen.getByText("100.5")).toBeTruthy();
    // 融资融券日期行
    expect(screen.getByText("20240105")).toBeTruthy();
    // 龙虎榜股票名称
    expect(screen.getByText("贵州茅台")).toBeTruthy();
    // 北向数值
    expect(screen.getByText("88.88")).toBeTruthy();
  });

  it("shows 暂无 placeholders when API returns empty", () => {
    render(<AshareDeepPage />);
    expect(screen.getByText(/暂无资金流数据/)).toBeTruthy();
    expect(screen.getByText(/暂无融资融券数据/)).toBeTruthy();
    expect(screen.getByText(/暂无该日龙虎榜/)).toBeTruthy();
    expect(screen.getByText(/暂无北向资金数据/)).toBeTruthy();
  });
});
