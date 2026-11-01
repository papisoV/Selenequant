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

vi.mock("@/components/trading/strategy-status", () => ({
  StrategyStatus: () => <div data-testid="strategy-status" />,
}));

import { TofPage } from "./tof-page";

describe("TofPage — ToxicOrderflowScore 字段对齐", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("把 toxicity_score/direction/cvd_delta/liq_imbalance/book_imbalance 映射到 StatBlock", () => {
    h.payload = {
      exchange: "binance",
      symbol: "BTC/USDT",
      toxicity_score: 72.5,
      direction: "bearish",
      cvd_delta: -1_500_000,
      liq_imbalance: 2_000_000,
      book_imbalance: -0.25,
      mark_price: 60000,
    };
    render(<TofPage />);

    // 0-100 分制（旧代码假设 0-1，会把 72.5 显示成 72.50）
    expect(screen.getByTestId("stat-毒性分数").textContent).toBe("72.5");
    // direction 是真实数据（旧代码从不读，回退成假 label）
    expect(screen.getByTestId("stat-方向").textContent).toBe("看空");
    // cvd_delta / liq_imbalance 是 USD 名义额（旧代码把 liq_imbalance 当比值 ×100 显示成 %）
    expect(screen.getByTestId("stat-CVD").textContent).toBe("-$1.50M");
    expect(screen.getByTestId("stat-爆仓失衡").textContent).toBe("+$2.00M");
    // book_imbalance 是 [-1,1] 比值（旧代码算出来却从不渲染）
    expect(screen.getByTestId("stat-盘口失衡").textContent).toBe("-25.0%");
  });

  it("无数据时全部显示占位，不显示 NaN", () => {
    h.payload = null;
    render(<TofPage />);
    expect(screen.getByTestId("stat-毒性分数").textContent).toBe("—");
    expect(screen.getByTestId("stat-方向").textContent).toBe("中性");
  });

  it("请求打到 /api/v1/tof/current", () => {
    render(<TofPage />);
    expect(h.urls.some((u) => u.startsWith("/api/v1/tof/current"))).toBe(true);
  });
});
