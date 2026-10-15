import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";

const h = vi.hoisted(() => ({
  urls: [] as string[],
  payload: null as unknown,
}));

vi.mock("@/lib/hooks/use-api", () => ({
  useApi: (url: string | null) => {
    if (url) h.urls.push(url);
    return { data: h.payload, isLoading: false, error: null };
  },
}));

vi.mock("@/components/shared/page-filter-bar", () => ({
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string | number }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

import { RegimePage } from "./regime-page";

describe("RegimePage — regime 端点字段对齐", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("读 regime/annual_return/volatility（旧代码读 current_regime 恒 —）", () => {
    h.payload = {
      regime: "bull",
      annual_return: 0.23,
      volatility: 0.18,
      symbol: "BTC/USDT",
      timestamp: 1711000000000,
    };
    render(<RegimePage />);

    expect(screen.getByTestId("stat-体制").textContent).toBe("牛市");
    expect(screen.getByTestId("stat-年化收益").textContent).toBe("23.0%");
    expect(screen.getByTestId("stat-波动率").textContent).toBe("18.0%");
  });

  it("transition 分类有中文标签", () => {
    h.payload = { regime: "transition", annual_return: -0.05, volatility: 0.4 };
    render(<RegimePage />);
    expect(screen.getByTestId("stat-体制").textContent).toBe("转换期");
    expect(screen.getByTestId("stat-年化收益").textContent).toBe("-5.0%");
  });

  it("无数据时显示占位", () => {
    h.payload = null;
    render(<RegimePage />);
    expect(screen.getByTestId("stat-体制").textContent).toBe("—");
    expect(screen.getByTestId("stat-年化收益").textContent).toBe("—");
  });
});
