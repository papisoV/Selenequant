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
  fetcher: () => Promise.resolve(null),
}));

vi.mock("@/components/shared/page-filter-bar", () => ({
  PageFilterBar: () => <div data-testid="page-filter-bar" />,
  V17Card: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid={`card-${title}`}>{children}</div>
  ),
  StatBlock: ({ label, value }: { label: string; value: string | number }) => (
    <div data-testid={`stat-${label}`}>{value}</div>
  ),
}));

vi.mock("@/components/shared/workspace-link", () => ({
  WorkspaceLink: () => <span data-testid="ws-link" />,
}));

import { GexPage } from "./gex-page";

// 每档净 GEX = call_gamma*call_oi - put_gamma*put_oi（与后端 gamma_flip 公式一致）
// 50000 → 0.001*100 - 0.002*50 = 0 ; 70000 → 0.003*200 - 0.001*80 = 0.52
const GEX_PAYLOAD = {
  symbol: "BTC/USDT",
  underlying_price: 60000,
  net_gex: -1234.5,
  dealer_positioning: "short_gamma",
  total_call_gex: 5000,
  total_put_gex: 6000,
  key_levels: [{ price: 65000, net_gex: 0, label: "gamma_flip" }],
  strikes: [
    { strike: 50000, call_oi: 100, put_oi: 50, call_gamma: 0.001, put_gamma: 0.002 },
    { strike: 70000, call_oi: 200, put_oi: 80, call_gamma: 0.003, put_gamma: 0.001 },
  ],
};

describe("GexPage — GEXResult 字段对齐", () => {
  beforeEach(() => {
    h.urls.length = 0;
    h.payload = null;
  });

  it("从 call_gamma/put_gamma 重建每档柱、并渲染现货/翻转点/正负总量", () => {
    h.payload = GEX_PAYLOAD;
    const { container } = render(<GexPage />);

    // 旧代码读 strikes[].gex（不存在）→ 图恒空；现在两档都画出来
    expect(container.querySelectorAll(".gex-bar").length).toBe(2);

    expect(screen.getByTestId("stat-现货").textContent).toBe("$60,000");
    // 翻转点取自 key_levels[label=gamma_flip]，非顶层 flip_point
    expect(screen.getByTestId("stat-翻转点").textContent).toBe("$65,000");
    expect(screen.getByTestId("stat-净 GEX").textContent).toBe("-1234.50");
    expect(screen.getByTestId("stat-正 GEX 总量").textContent).toBe("+5000");
    expect(screen.getByTestId("stat-负 GEX 总量").textContent).toBe("-6000");
    // dealer_positioning 也是真实数据
    expect(container.textContent).toContain("做市商空 Gamma");
  });

  it("strikes 为空 → 显示空态，不渲染柱", () => {
    h.payload = { underlying_price: 60000, strikes: [], key_levels: [] };
    const { container } = render(<GexPage />);
    expect(screen.getByText("暂无 GEX 数据")).toBeTruthy();
    expect(container.querySelectorAll(".gex-bar").length).toBe(0);
  });

  it("请求打到 /api/v1/gex/current", () => {
    render(<GexPage />);
    expect(h.urls.some((u) => u.startsWith("/api/v1/gex/current"))).toBe(true);
  });
});
